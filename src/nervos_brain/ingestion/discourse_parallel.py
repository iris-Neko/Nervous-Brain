"""Parallel Discourse ingestion with resumable crawl state."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from nervos_brain.retrieval.dual_layer import ArchiveStore, DualLayerWriter

from .discourse import DiscourseCrawler, TopicMeta

logger = logging.getLogger(__name__)

_STATE_VERSION = 1
_POST_BATCH_SIZE = 20


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_topic_ids(topic_ids: list[int]) -> str:
    if not topic_ids:
        return "none"
    payload = ",".join(str(i) for i in sorted(set(topic_ids)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_crawl_key(
    *,
    mode: str,
    base_url: str,
    category_slug: Optional[str],
    latest_pages: Optional[int],
    max_pages: Optional[int],
    explicit_topic_ids: Optional[list[int]],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "base_url": base_url.rstrip("/"),
        "category_slug": category_slug or "",
        "latest_pages": int(latest_pages) if latest_pages is not None else None,
        "max_pages": int(max_pages) if max_pages is not None else None,
        "topic_ids_hash": _hash_topic_ids(explicit_topic_ids or []),
    }


@dataclass
class TopicCrawlResult:
    topic_id: int
    ok: bool
    attempts: int
    total_posts: int = 0
    skipped_existing: int = 0
    written_posts: int = 0
    error: str = ""


@dataclass
class ForumCrawlState:
    version: int
    crawl_key: dict[str, Any]
    topic_ids: list[int]
    completed_topic_ids: list[int] = field(default_factory=list)
    failed_topics: dict[str, dict[str, Any]] = field(default_factory=dict)
    created_ts_ms: int = field(default_factory=_now_ms)
    updated_ts_ms: int = field(default_factory=_now_ms)

    @classmethod
    def create(cls, crawl_key: dict[str, Any], topic_ids: list[int]) -> "ForumCrawlState":
        dedup = sorted(set(topic_ids))
        now = _now_ms()
        return cls(
            version=_STATE_VERSION,
            crawl_key=dict(crawl_key),
            topic_ids=dedup,
            completed_topic_ids=[],
            failed_topics={},
            created_ts_ms=now,
            updated_ts_ms=now,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ForumCrawlState":
        return cls(
            version=int(raw.get("version", _STATE_VERSION)),
            crawl_key=dict(raw.get("crawl_key", {})),
            topic_ids=[int(i) for i in raw.get("topic_ids", [])],
            completed_topic_ids=[int(i) for i in raw.get("completed_topic_ids", [])],
            failed_topics=dict(raw.get("failed_topics", {})),
            created_ts_ms=int(raw.get("created_ts_ms", _now_ms())),
            updated_ts_ms=int(raw.get("updated_ts_ms", _now_ms())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "crawl_key": self.crawl_key,
            "topic_ids": sorted(set(self.topic_ids)),
            "completed_topic_ids": sorted(set(self.completed_topic_ids)),
            "failed_topics": self.failed_topics,
            "created_ts_ms": self.created_ts_ms,
            "updated_ts_ms": self.updated_ts_ms,
        }

    def is_compatible(self, crawl_key: dict[str, Any]) -> bool:
        return self.crawl_key == crawl_key

    def pending_topic_ids(self) -> list[int]:
        done = set(self.completed_topic_ids)
        return [tid for tid in self.topic_ids if tid not in done]

    def mark_success(self, topic_id: int) -> None:
        if topic_id not in self.completed_topic_ids:
            self.completed_topic_ids.append(topic_id)
        self.failed_topics.pop(str(topic_id), None)
        self.updated_ts_ms = _now_ms()

    def mark_failure(self, topic_id: int, *, attempts: int, error: str) -> None:
        self.failed_topics[str(topic_id)] = {
            "attempts": attempts,
            "error": error[:500],
            "updated_ts_ms": _now_ms(),
        }
        self.updated_ts_ms = _now_ms()


class ForumCrawlStateStore:
    """JSON state file persistence for resumable forum crawl."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Optional[ForumCrawlState]:
        if not self._path.exists():
            return None
        with self._path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return ForumCrawlState.from_dict(raw)

    def save(self, state: ForumCrawlState) -> None:
        state.updated_ts_ms = _now_ms()
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        tmp_path.replace(self._path)


class ParallelDiscourseIngestor:
    """Concurrent topic crawler with retry and incremental skip support."""

    def __init__(
        self,
        *,
        writer: DualLayerWriter,
        archive_store: ArchiveStore,
        base_url: str,
        request_delay: float,
        api_key: Optional[str],
        api_username: Optional[str],
        incremental: bool,
        workers: int = 6,
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
        crawler_factory: Optional[Callable[[], DiscourseCrawler]] = None,
    ) -> None:
        self._writer = writer
        self._archive = archive_store
        self._incremental = incremental
        self._workers = max(1, workers)
        self._max_retries = max(0, max_retries)
        self._retry_backoff_s = max(0.0, retry_backoff_s)
        self._write_lock = threading.Lock()

        if crawler_factory is None:
            self._crawler_factory = lambda: DiscourseCrawler(
                base_url=base_url,
                request_delay=request_delay,
                api_key=api_key,
                api_username=api_username,
            )
        else:
            self._crawler_factory = crawler_factory

    def resolve_topic_ids(
        self,
        *,
        mode: str,
        category_slug: Optional[str],
        latest_pages: Optional[int],
        max_pages: Optional[int],
        explicit_topic_ids: Optional[list[int]] = None,
    ) -> list[int]:
        crawler = self._crawler_factory()
        if mode == "topic":
            return sorted(set(explicit_topic_ids or []))
        if mode == "latest":
            pages = latest_pages or 1
            return sorted(
                set(
                    crawler.fetch_latest_topic_ids(
                        category_slug=category_slug,
                        pages=pages,
                    )
                )
            )
        page_limit = max_pages if max_pages and max_pages > 0 else None
        return crawler.fetch_all_topic_ids(category_slug=category_slug, max_pages=page_limit)

    def run_topics(
        self,
        topic_ids: list[int],
        on_topic_done: Optional[Callable[[TopicCrawlResult, int, int], None]] = None,
    ) -> list[TopicCrawlResult]:
        if not topic_ids:
            return []
        total = len(topic_ids)
        done = 0
        results: list[TopicCrawlResult] = []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._crawl_topic_with_retries, tid): tid for tid in topic_ids}
            for future in as_completed(futures):
                done += 1
                result = future.result()
                results.append(result)
                if on_topic_done is not None:
                    on_topic_done(result, done, total)
        return results

    def _crawl_topic_with_retries(self, topic_id: int) -> TopicCrawlResult:
        last_error = ""
        for attempt in range(1, self._max_retries + 2):
            try:
                result = self._crawl_topic_once(topic_id)
                result.attempts = attempt
                return result
            except Exception as exc:  # pragma: no cover - covered by caller behavior
                last_error = str(exc)
                if attempt <= self._max_retries:
                    sleep_s = self._retry_backoff_s * (2 ** (attempt - 1))
                    if sleep_s > 0:
                        time.sleep(sleep_s)
        return TopicCrawlResult(
            topic_id=topic_id,
            ok=False,
            attempts=self._max_retries + 1,
            error=last_error or "unknown error",
        )

    def _crawl_topic_once(self, topic_id: int) -> TopicCrawlResult:
        crawler = self._crawler_factory()
        meta = crawler.fetch_topic_meta(topic_id)

        skip: set[str] = set()
        if self._incremental:
            prefix = f"doc:nervos-talk-{topic_id}#"
            skip = self._archive.list_anchors_with_prefix(prefix)

        total_posts = 0
        skipped_existing = 0
        written_posts = 0

        all_ids = meta.all_post_ids
        logger.info("Topic %d: %d total posts", topic_id, len(all_ids))
        for batch_start in range(0, len(all_ids), _POST_BATCH_SIZE):
            batch_ids = all_ids[batch_start: batch_start + _POST_BATCH_SIZE]
            posts = crawler.fetch_posts_by_ids(topic_id, batch_ids, meta)
            for post in posts:
                total_posts += 1
                if post.anchor in skip:
                    skipped_existing += 1
                    continue
                self._write_post(post, meta)
                skip.add(post.anchor)
                written_posts += 1

        return TopicCrawlResult(
            topic_id=topic_id,
            ok=True,
            attempts=1,
            total_posts=total_posts,
            skipped_existing=skipped_existing,
            written_posts=written_posts,
        )

    def _write_post(self, post: Any, meta: TopicMeta) -> None:
        with self._write_lock:
            self._writer.write(
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
                topic=f"nervos_talk:{meta.id}",
            )
