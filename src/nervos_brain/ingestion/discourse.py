"""Discourse forum crawler and ingestion pipeline.

Supports:
  - Full topic crawl (all posts, pagination-aware).
  - Incremental update (skips posts already in the archive).
  - Category / latest-topics sweep for periodic refresh.
  - Polite rate-limiting between requests.
  - Optional API key authentication for private forums.

Discourse REST API reference:
  GET /t/{id}.json                       — topic metadata + first 20 posts
  GET /t/{id}/posts.json?post_ids[]=...  — arbitrary post IDs in bulk
  GET /latest.json?page=N               — latest topics (paginated)
  GET /c/{slug}/{id}.json               — category topics

All posts are ingested into the dual-layer store via DualLayerWriter.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import requests

from nervos_brain.retrieval.dual_layer import DualLayerWriter

from .html_cleaner import html_to_text, make_summary

logger = logging.getLogger(__name__)

# Discourse paginates bulk post fetches in chunks of this size
_POST_BATCH_SIZE = 20

# Default wait between API calls (seconds) — keeps us within rate limits
_DEFAULT_DELAY = 1.2


# ── Data models ─────────────────────────────────────────────────────────────


@dataclass
class DiscoursePost:
    """Normalised representation of a single Discourse post."""

    id: int
    topic_id: int
    topic_title: str
    topic_slug: str
    post_number: int
    username: str
    created_at: str      # ISO-8601
    updated_at: str
    cooked: str          # raw HTML from API
    like_count: int = 0
    reply_count: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://talk.nervos.org/t/{self.topic_slug}/{self.topic_id}/{self.post_number}"

    @property
    def anchor(self) -> str:
        return f"doc:nervos-talk-{self.topic_id}#post:{self.post_number}"

    @property
    def plain_text(self) -> str:
        return html_to_text(self.cooked)

    @property
    def summary(self) -> str:
        return make_summary(self.plain_text)

    @property
    def keywords(self) -> str:
        parts = [
            f"user:{self.username}",
            f"topic:{self.topic_id}",
            f"post:{self.post_number}",
        ]
        if self.tags:
            parts.extend(self.tags)
        return ",".join(parts)

    @property
    def title(self) -> str:
        if self.post_number == 1:
            return self.topic_title
        return f"{self.topic_title} — reply #{self.post_number}"


@dataclass
class TopicMeta:
    id: int
    title: str
    slug: str
    posts_count: int
    tags: list[str]
    category_id: int
    all_post_ids: list[int]


# ── Crawler ─────────────────────────────────────────────────────────────────


class DiscourseCrawler:
    """Discourse REST API client with rate-limiting and incremental support.

    Args:
        base_url:       Forum root, e.g. ``"https://talk.nervos.org"``.
        request_delay:  Seconds to sleep between HTTP requests.
        api_key:        Discourse API key (optional; needed for private forums).
        api_username:   Username matching the API key.
        timeout:        Requests timeout in seconds.
    """

    def __init__(
        self,
        base_url: str = "https://talk.nervos.org",
        request_delay: float = _DEFAULT_DELAY,
        api_key: Optional[str] = None,
        api_username: Optional[str] = None,
        timeout: int = 15,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._delay = request_delay
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        if api_key and api_username:
            self._session.headers.update({
                "Api-Key": api_key,
                "Api-Username": api_username,
            })
        self._last_request_ts: float = 0.0

    # ── low-level HTTP ──────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """GET ``{base}{path}`` with rate-limiting and error handling."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

        url = f"{self._base}{path}"
        logger.debug("GET %s %s", url, params or "")
        resp = self._session.get(url, params=params, timeout=self._timeout)
        self._last_request_ts = time.monotonic()
        resp.raise_for_status()
        return resp.json()

    # ── topic-level API ─────────────────────────────────────────────────────

    def fetch_topic_meta(self, topic_id: int) -> TopicMeta:
        """Fetch topic metadata and the list of all post IDs."""
        data = self._get(f"/t/{topic_id}.json")
        stream: list[int] = data.get("post_stream", {}).get("stream", [])
        # If stream is empty, the first page of posts gives us the IDs
        if not stream:
            stream = [p["id"] for p in data.get("post_stream", {}).get("posts", [])]
        return TopicMeta(
            id=data["id"],
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            posts_count=data.get("posts_count", 0),
            tags=data.get("tags", []),
            category_id=data.get("category_id", 0),
            all_post_ids=stream,
        )

    def fetch_posts_by_ids(
        self, topic_id: int, post_ids: list[int], topic_meta: TopicMeta
    ) -> list[DiscoursePost]:
        """Fetch specific posts by ID from a topic."""
        if not post_ids:
            return []
        params: dict[str, Any] = {}
        for i, pid in enumerate(post_ids):
            params[f"post_ids[]"] = pid  # requests handles repeated keys as list
        # requests encodes repeated keys correctly when passed as a list of tuples
        param_list = [("post_ids[]", pid) for pid in post_ids]
        path = f"/t/{topic_id}/posts.json"
        url = f"{self._base}{path}"
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        resp = self._session.get(url, params=param_list, timeout=self._timeout)
        self._last_request_ts = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("post_stream", {}).get("posts", [])
        return [self._parse_post(p, topic_meta) for p in posts]

    def iter_all_posts(
        self, topic_id: int, skip_existing_anchors: set[str] | None = None
    ) -> Iterator[DiscoursePost]:
        """Yield every post in the topic, fetching in batches.

        Args:
            topic_id:              Discourse topic ID.
            skip_existing_anchors: Set of anchor strings already in the archive;
                                   posts whose anchor is in this set are skipped
                                   (incremental mode).
        """
        meta = self.fetch_topic_meta(topic_id)
        skip = skip_existing_anchors or set()

        # The first page of posts is already embedded in the topic JSON.
        # Re-fetch them via the posts endpoint for consistency.
        all_ids = meta.all_post_ids
        logger.info("Topic %d: %d total posts", topic_id, len(all_ids))

        for batch_start in range(0, len(all_ids), _POST_BATCH_SIZE):
            batch_ids = all_ids[batch_start: batch_start + _POST_BATCH_SIZE]
            # Quick check: build provisional anchors from post numbers.
            # We don't know post_number → id mapping yet, so we fetch and
            # then skip if the parsed anchor is already stored.
            posts = self.fetch_posts_by_ids(topic_id, batch_ids, meta)
            for post in posts:
                if post.anchor in skip:
                    logger.debug("Skipping existing post %s", post.anchor)
                    continue
                yield post

    # ── latest / category sweep ────────────────────────────────────────────

    def fetch_latest_topic_ids(
        self,
        category_slug: Optional[str] = None,
        pages: int = 1,
    ) -> list[int]:
        """Return topic IDs from latest or a specific category feed.

        Args:
            category_slug:  e.g. ``"english"`` or ``None`` for site-wide latest.
            pages:          How many pages to fetch (each page ≈ 30 topics).
        """
        if pages <= 0:
            return []
        return list(
            self.iter_latest_topic_ids(
                category_slug=category_slug,
                max_pages=pages,
            )
        )

    def iter_latest_topic_ids(
        self,
        category_slug: Optional[str] = None,
        max_pages: Optional[int] = None,
        start_page: int = 0,
    ) -> Iterator[int]:
        """Yield topic IDs page-by-page from latest/category feed.

        Args:
            category_slug:  e.g. ``"english"`` or ``None`` for site-wide latest.
            max_pages:      Maximum pages to fetch. ``None`` means until exhausted.
            start_page:     Starting page index.
        """
        if max_pages is not None and max_pages <= 0:
            return

        page = start_page
        fetched_pages = 0
        while True:
            if max_pages is not None and fetched_pages >= max_pages:
                break
            path = f"/c/{category_slug}.json" if category_slug else "/latest.json"
            data = self._get(path, params={"page": page})
            topic_list = data.get("topic_list", {}).get("topics", [])
            if not topic_list:
                break
            for row in topic_list:
                topic_id = row.get("id")
                if isinstance(topic_id, int):
                    yield topic_id
            fetched_pages += 1
            page += 1

    def fetch_all_topic_ids(
        self,
        category_slug: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> list[int]:
        """Return deduplicated topic IDs from all available feed pages."""
        ids: list[int] = []
        seen: set[int] = set()
        for topic_id in self.iter_latest_topic_ids(
            category_slug=category_slug,
            max_pages=max_pages,
        ):
            if topic_id in seen:
                continue
            seen.add(topic_id)
            ids.append(topic_id)
        return ids

    # ── ingest ──────────────────────────────────────────────────────────────

    def crawl_topic(
        self,
        topic_id: int,
        writer: DualLayerWriter,
        incremental: bool = True,
    ) -> int:
        """Crawl a topic and write all (new) posts to the dual-layer store.

        Args:
            topic_id:    Discourse topic ID.
            writer:      DualLayerWriter connected to Qdrant + SQLite.
            incremental: If True, skip posts whose anchor already exists in
                         the archive (avoids re-processing unchanged posts).

        Returns:
            Number of posts actually written.
        """
        skip: set[str] = set()
        if incremental:
            # Build set of already-stored anchors for this topic
            existing = writer._archive.list_all()
            prefix = f"doc:nervos-talk-{topic_id}#"
            skip = {r.anchor for r in existing if r.anchor.startswith(prefix)}
            if skip:
                logger.info("Topic %d: %d posts already in archive, skipping", topic_id, len(skip))

        written = 0
        for post in self.iter_all_posts(topic_id, skip_existing_anchors=skip):
            writer.write(
                source="nervos_talk",
                doc_type="forum_post",
                url=post.url,
                anchor=post.anchor,
                title=post.title,
                summary=post.summary,
                keywords=post.keywords,
                raw_text=post.plain_text,
                raw_format="html",
                lang="en",
                version="latest",
                topic=f"nervos_talk:{post.topic_id}",
            )
            written += 1
            logger.info("Wrote %s", post.anchor)

        return written

    def crawl_latest(
        self,
        writer: DualLayerWriter,
        category_slug: Optional[str] = None,
        pages: int = 1,
        incremental: bool = True,
    ) -> dict[int, int]:
        """Crawl latest (or category) topics and ingest all posts.

        Returns:
            Dict mapping topic_id → number of posts written.
        """
        topic_ids = self.fetch_latest_topic_ids(category_slug=category_slug, pages=pages)
        return self.crawl_topic_ids(writer, topic_ids, incremental=incremental)

    def crawl_all(
        self,
        writer: DualLayerWriter,
        category_slug: Optional[str] = None,
        max_pages: Optional[int] = None,
        incremental: bool = True,
    ) -> dict[int, int]:
        """Crawl all available latest/category pages and ingest all posts."""
        topic_ids = self.fetch_all_topic_ids(
            category_slug=category_slug,
            max_pages=max_pages,
        )
        return self.crawl_topic_ids(writer, topic_ids, incremental=incremental)

    def crawl_topic_ids(
        self,
        writer: DualLayerWriter,
        topic_ids: list[int],
        incremental: bool = True,
    ) -> dict[int, int]:
        """Crawl specific topic IDs and ingest all posts."""
        results: dict[int, int] = {}
        for tid in topic_ids:
            try:
                n = self.crawl_topic(tid, writer, incremental=incremental)
                results[tid] = n
            except Exception as exc:
                logger.warning("Failed to crawl topic %d: %s", tid, exc)
                results[tid] = 0
        return results

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_post(raw: dict, meta: TopicMeta) -> DiscoursePost:
        return DiscoursePost(
            id=raw["id"],
            topic_id=meta.id,
            topic_title=meta.title,
            topic_slug=meta.slug,
            post_number=raw.get("post_number", 0),
            username=raw.get("username", "unknown"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", raw.get("created_at", "")),
            cooked=raw.get("cooked", ""),
            like_count=raw.get("like_count", 0),
            reply_count=raw.get("reply_count", 0),
            tags=meta.tags,
        )
