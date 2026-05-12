"""Read-only Nervos Talk MCP adapter.

This module exposes public Discourse/Nervos Talk query helpers as FastMCP tools.
It is intentionally read-only: no posting, login, moderation, or database writes.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote_plus

import requests

from nervos_brain.ingestion.html_cleaner import html_to_text, make_summary

_DEFAULT_BASE_URL = "https://talk.nervos.org"
_DEFAULT_TIMEOUT_S = 15
_DEFAULT_REQUEST_DELAY_S = 0.5
_MAX_LIMIT = 50


class TalkMCPError(RuntimeError):
    """Domain error for the Nervos Talk MCP adapter."""


@dataclass(frozen=True)
class TalkMCPConfig:
    """Runtime config for public read-only Nervos Talk access."""

    base_url: str = _DEFAULT_BASE_URL
    request_delay: float = _DEFAULT_REQUEST_DELAY_S
    timeout_s: int = _DEFAULT_TIMEOUT_S
    api_key: str = ""
    api_username: str = ""

    @classmethod
    def from_env(cls) -> "TalkMCPConfig":
        return cls(
            base_url=(os.getenv("NERVOS_TALK_BASE_URL") or _DEFAULT_BASE_URL).strip() or _DEFAULT_BASE_URL,
            request_delay=_float_env("NERVOS_TALK_REQUEST_DELAY", _DEFAULT_REQUEST_DELAY_S),
            timeout_s=_int_env("NERVOS_TALK_TIMEOUT_S", _DEFAULT_TIMEOUT_S),
            api_key=(os.getenv("NERVOS_TALK_API_KEY") or "").strip(),
            api_username=(os.getenv("NERVOS_TALK_API_USER") or "").strip(),
        )


@dataclass(frozen=True)
class TalkPost:
    title: str
    url: str
    topic_id: int
    post_number: int
    username: str
    created_at: str
    snippet: str
    content: str
    tags: list[str]
    score: float = 1.0
    source: str = "discourse"


@dataclass(frozen=True)
class TalkTopic:
    title: str
    url: str
    topic_id: int
    slug: str
    posts_count: int
    tags: list[str]
    posts: list[dict[str, Any]]


class TalkMCPClient:
    """Small Discourse API client for public Nervos Talk reads."""

    def __init__(self, config: TalkMCPConfig | None = None, *, session: requests.Session | None = None) -> None:
        self._cfg = config or TalkMCPConfig.from_env()
        self._base = self._cfg.base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.update({"Accept": "application/json", "User-Agent": "nervos-brain-talk-mcp"})
        if self._cfg.api_key and self._cfg.api_username:
            self._session.headers.update({"Api-Key": self._cfg.api_key, "Api-Username": self._cfg.api_username})
        self._last_request_ts = 0.0

    @property
    def base_url(self) -> str:
        return self._base

    def search(self, query: str, *, limit: int = 5, category: str = "", time_range: str = "") -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            raise TalkMCPError("query cannot be empty")
        safe_limit = _clamp_limit(limit)
        response = self._get("/search.json", params={"q": _build_search_query(query, category=category, time_range=time_range)})
        posts = response.get("posts") if isinstance(response.get("posts"), list) else []
        topics = _topics_by_id(response.get("topics"))
        rows: list[dict[str, Any]] = []
        for idx, raw_post in enumerate(posts[:safe_limit], start=1):
            if not isinstance(raw_post, dict):
                continue
            topic_id = _int_like(raw_post.get("topic_id"))
            topic = topics.get(topic_id, {})
            row = self._post_from_raw(raw_post, topic=topic, fallback_score=1.0 / idx)
            rows.append(self._evidence_from_post(row))
        return rows

    def latest(self, *, category: str = "", limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = _clamp_limit(limit)
        path = f"/c/{category.strip().strip('/')}.json" if str(category or "").strip() else "/latest.json"
        response = self._get(path, params={"page": 0})
        topics = response.get("topic_list", {}).get("topics", [])
        rows: list[dict[str, Any]] = []
        for idx, topic in enumerate(topics[:safe_limit], start=1):
            if not isinstance(topic, dict):
                continue
            topic_id = _int_like(topic.get("id"))
            slug = str(topic.get("slug") or "")
            title = str(topic.get("title") or "")
            content = str(topic.get("excerpt") or topic.get("fancy_title") or title)
            rows.append(
                {
                    "id": f"talk-topic-{topic_id}",
                    "source": "discourse",
                    "title": title,
                    "url": self._topic_url(slug=slug, topic_id=topic_id),
                    "anchor": f"doc:nervos-talk-{topic_id}",
                    "snippet": make_summary(html_to_text(content) or title, 500),
                    "content": html_to_text(content) or title,
                    "score": 1.0 / idx,
                    "payload": {
                        "source": "nervos_talk",
                        "type": "forum_topic",
                        "topic_id": topic_id,
                        "post_number": 1,
                        "username": str(topic.get("last_poster_username") or ""),
                        "created_at": str(topic.get("created_at") or ""),
                        "tags": list(topic.get("tags") or []),
                    },
                    "hash": f"talk-topic-{topic_id}",
                    "retrieved_ts_ms": _now_ms(),
                }
            )
        return rows

    def get_topic(self, topic_id: int, *, max_posts: int = 50) -> dict[str, Any]:
        topic_id = _positive_int(topic_id, "topic_id")
        safe_limit = _clamp_limit(max_posts)
        response = self._get(f"/t/{topic_id}.json")
        stream = response.get("post_stream", {}) if isinstance(response.get("post_stream"), dict) else {}
        raw_posts = stream.get("posts") if isinstance(stream.get("posts"), list) else []
        topic = {
            "id": _int_like(response.get("id")),
            "title": str(response.get("title") or ""),
            "slug": str(response.get("slug") or ""),
            "tags": list(response.get("tags") or []),
        }
        posts = [asdict(self._post_from_raw(raw, topic=topic)) for raw in raw_posts[:safe_limit] if isinstance(raw, dict)]
        row = TalkTopic(
            title=topic["title"],
            url=self._topic_url(slug=topic["slug"], topic_id=topic_id),
            topic_id=topic_id,
            slug=topic["slug"],
            posts_count=_int_like(response.get("posts_count")),
            tags=topic["tags"],
            posts=posts,
        )
        return asdict(row)

    def get_post(self, topic_id: int, post_number: int) -> dict[str, Any]:
        topic = self.get_topic(topic_id, max_posts=_MAX_LIMIT)
        for post in topic.get("posts", []):
            if _int_like(post.get("post_number")) == int(post_number):
                return post
        raise TalkMCPError(f"post not found: topic_id={topic_id} post_number={post_number}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._cfg.request_delay:
            time.sleep(self._cfg.request_delay - elapsed)
        response = self._session.get(f"{self._base}{path}", params=params or {}, timeout=self._cfg.timeout_s)
        self._last_request_ts = time.monotonic()
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise TalkMCPError(f"Nervos Talk request failed: {response.status_code} {path}") from exc
        data = response.json()
        if not isinstance(data, dict):
            raise TalkMCPError("Nervos Talk returned non-object JSON")
        return data

    def _post_from_raw(self, raw: dict[str, Any], *, topic: dict[str, Any], fallback_score: float = 1.0) -> TalkPost:
        topic_id = _int_like(raw.get("topic_id") or topic.get("id"))
        post_number = _int_like(raw.get("post_number"), default=1)
        title = str(topic.get("title") or raw.get("topic_title") or raw.get("title") or f"Nervos Talk topic {topic_id}")
        slug = str(topic.get("slug") or raw.get("topic_slug") or _slugify(title))
        content = html_to_text(str(raw.get("cooked") or raw.get("blurb") or raw.get("excerpt") or ""))
        if not content:
            content = str(raw.get("raw") or "").strip()
        return TalkPost(
            title=title if post_number == 1 else f"{title} — reply #{post_number}",
            url=self._post_url(slug=slug, topic_id=topic_id, post_number=post_number),
            topic_id=topic_id,
            post_number=post_number,
            username=str(raw.get("username") or "unknown"),
            created_at=str(raw.get("created_at") or ""),
            snippet=make_summary(content, 500),
            content=content,
            tags=list(topic.get("tags") or raw.get("tags") or []),
            score=float(raw.get("score") or fallback_score),
        )

    def _evidence_from_post(self, post: TalkPost) -> dict[str, Any]:
        return {
            "id": f"talk-{post.topic_id}-{post.post_number}",
            "source": "discourse",
            "title": post.title,
            "url": post.url,
            "anchor": f"doc:nervos-talk-{post.topic_id}#post:{post.post_number}",
            "snippet": post.snippet,
            "content": post.content,
            "score": post.score,
            "payload": {
                "source": "nervos_talk",
                "type": "forum_post",
                "topic_id": post.topic_id,
                "post_number": post.post_number,
                "username": post.username,
                "created_at": post.created_at,
                "tags": post.tags,
            },
            "hash": f"talk-{post.topic_id}-{post.post_number}",
            "retrieved_ts_ms": _now_ms(),
        }

    def _topic_url(self, *, slug: str, topic_id: int) -> str:
        return f"{self._base}/t/{slug or 'topic'}/{topic_id}"

    def _post_url(self, *, slug: str, topic_id: int, post_number: int) -> str:
        return f"{self._topic_url(slug=slug, topic_id=topic_id)}/{post_number}"


def build_fastmcp_server(client: TalkMCPClient):
    """Build a FastMCP server bound to a read-only Talk client."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise TalkMCPError("Missing dependency: mcp. Install with `pip install \"mcp[cli]\"`.") from exc

    mcp = FastMCP("nervos-talk-mcp")

    @mcp.tool()
    async def talk_search(query: str, limit: int = 5, category: str = "", time_range: str = "") -> list[dict[str, Any]]:
        return client.search(query=query, limit=limit, category=category, time_range=time_range)

    @mcp.tool()
    async def talk_get_topic(topic_id: int, max_posts: int = 50) -> dict[str, Any]:
        return client.get_topic(topic_id=topic_id, max_posts=max_posts)

    @mcp.tool()
    async def talk_get_post(topic_id: int, post_number: int) -> dict[str, Any]:
        return client.get_post(topic_id=topic_id, post_number=post_number)

    @mcp.tool()
    async def talk_latest(category: str = "", limit: int = 10) -> list[dict[str, Any]]:
        return client.latest(category=category, limit=limit)

    return mcp


def _build_search_query(query: str, *, category: str, time_range: str) -> str:
    parts = [query.strip()]
    category = str(category or "").strip().strip("/")
    if category:
        parts.append(f"category:{category}")
    time_range = str(time_range or "").strip()
    if time_range:
        parts.append(time_range)
    return " ".join(parts)


def _topics_by_id(raw_topics: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(raw_topics, list):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for topic in raw_topics:
        if isinstance(topic, dict):
            result[_int_like(topic.get("id"))] = topic
    return result


def _clamp_limit(value: int, *, default: int = 5) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, _MAX_LIMIT))


def _positive_int(value: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TalkMCPError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise TalkMCPError(f"{name} must be > 0")
    return parsed


def _int_like(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except ValueError:
        return default


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "topic"


def _now_ms() -> int:
    return int(time.time() * 1000)
