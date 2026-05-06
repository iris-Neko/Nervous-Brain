"""Tests for the Nervos Talk Discourse crawler.

Structure
---------
html_cleaner        — pure-function unit tests (no I/O).
DiscoursePost       — dataclass derived-property tests.
DiscourseCrawler    — HTTP is mocked with unittest.mock.patch;
                      all tests run fully offline.
Integration         — marked ``@pytest.mark.integration``; hits the live
                      https://talk.nervos.org API.  Run explicitly with:
                        pytest -m integration tests/test_discourse_crawler.py
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nervos_brain.ingestion import DiscourseCrawler, DiscoursePost, TopicMeta
from nervos_brain.ingestion.html_cleaner import html_to_text, make_summary

# ── fixtures / helpers ──────────────────────────────────────────────────────

TOPIC_ID = 9995
TOPIC_SLUG = "spark-program-nervos-brain"
TOPIC_TITLE = "Nervos Brain — Agentic RAG"

# Minimal Discourse topic JSON (matches real API shape)
_TOPIC_JSON: dict[str, Any] = {
    "id": TOPIC_ID,
    "title": TOPIC_TITLE,
    "slug": TOPIC_SLUG,
    "posts_count": 3,
    "category_id": 5,
    "tags": ["spark-program", "in-progress"],
    "post_stream": {
        "posts": [
            {
                "id": 101,
                "post_number": 1,
                "username": "alice",
                "created_at": "2026-01-01T10:00:00.000Z",
                "updated_at": "2026-01-01T10:00:00.000Z",
                "cooked": "<p>Welcome to the Nervos Brain project.</p>",
                "like_count": 3,
                "reply_count": 0,
            },
        ],
        "stream": [101, 102, 103],
    },
}

_POSTS_PAGE2_JSON: dict[str, Any] = {
    "post_stream": {
        "posts": [
            {
                "id": 102,
                "post_number": 2,
                "username": "bob",
                "created_at": "2026-01-02T09:00:00.000Z",
                "updated_at": "2026-01-02T09:00:00.000Z",
                "cooked": "<p>This week we built the <code>LangGraph</code> backbone.</p>",
                "like_count": 1,
                "reply_count": 0,
            },
            {
                "id": 103,
                "post_number": 3,
                "username": "charlie",
                "created_at": "2026-01-03T08:00:00.000Z",
                "updated_at": "2026-01-03T08:00:00.000Z",
                "cooked": "<h2>Update</h2><p>Added dual-layer storage.</p>",
                "like_count": 5,
                "reply_count": 2,
            },
        ]
    }
}

_LATEST_JSON: dict[str, Any] = {
    "topic_list": {
        "topics": [
            {"id": 9995, "title": TOPIC_TITLE},
            {"id": 8001, "title": "Another Topic"},
        ]
    }
}

_LATEST_JSON_PAGE2: dict[str, Any] = {
    "topic_list": {
        "topics": [
            {"id": 8001, "title": "Another Topic (dup)"},
            {"id": 7007, "title": "Third Topic"},
        ]
    }
}


def _make_meta() -> TopicMeta:
    return TopicMeta(
        id=TOPIC_ID,
        title=TOPIC_TITLE,
        slug=TOPIC_SLUG,
        posts_count=3,
        tags=["spark-program"],
        category_id=5,
        all_post_ids=[101, 102, 103],
    )


def _mock_response(json_data: dict) -> MagicMock:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = json_data
    return mock


# ═══════════════════════════════════════════════════════════════════════════
# html_cleaner
# ═══════════════════════════════════════════════════════════════════════════


def test_html_to_text_strips_tags():
    result = html_to_text("<p>Hello <b>world</b>!</p>")
    assert "<" not in result
    assert "Hello" in result
    assert "world" in result


def test_html_to_text_preserves_code_backticks():
    result = html_to_text("<p>Use <code>openChannel</code> here.</p>")
    assert "`openChannel`" in result


def test_html_to_text_block_tags_produce_newlines():
    result = html_to_text("<p>First</p><p>Second</p>")
    assert "First" in result
    assert "Second" in result
    assert "\n" in result


def test_html_to_text_skips_script():
    result = html_to_text("<p>OK</p><script>alert('x')</script>")
    assert "alert" not in result
    assert "OK" in result


def test_html_to_text_empty_string():
    assert html_to_text("") == ""


def test_html_to_text_heading():
    result = html_to_text("<h2>Week 2 Report</h2><p>Content here.</p>")
    assert "Week 2 Report" in result
    assert "Content here." in result


def test_html_to_text_collapses_blank_lines():
    result = html_to_text("<p>A</p><p></p><p></p><p>B</p>")
    # Must not have 3+ consecutive newlines
    assert "\n\n\n" not in result


def test_make_summary_short_text():
    text = "Hello world."
    assert make_summary(text, max_chars=300) == text


def test_make_summary_truncates_at_word_boundary():
    text = "a " * 200  # 400 chars
    result = make_summary(text, max_chars=50)
    assert result.endswith("…")
    # should not cut a word in half — ends with space + ellipsis
    assert len(result) <= 51


def test_make_summary_does_not_exceed_max():
    text = "x" * 1000
    result = make_summary(text, max_chars=100)
    assert len(result) <= 101  # +1 for ellipsis char


# ═══════════════════════════════════════════════════════════════════════════
# DiscoursePost properties
# ═══════════════════════════════════════════════════════════════════════════


def _make_post(post_number: int = 1) -> DiscoursePost:
    return DiscoursePost(
        id=101 + post_number,
        topic_id=TOPIC_ID,
        topic_title=TOPIC_TITLE,
        topic_slug=TOPIC_SLUG,
        post_number=post_number,
        username="alice",
        created_at="2026-01-01T10:00:00.000Z",
        updated_at="2026-01-01T10:00:00.000Z",
        cooked="<p>Hello Nervos.</p>",
        like_count=2,
        reply_count=0,
        tags=["spark-program"],
    )


def test_post_url_format():
    post = _make_post(post_number=3)
    assert post.url == f"https://talk.nervos.org/t/{TOPIC_SLUG}/{TOPIC_ID}/3"


def test_post_anchor_format():
    post = _make_post(post_number=5)
    assert post.anchor == f"doc:nervos-talk-{TOPIC_ID}#post:5"


def test_post_title_first_post():
    post = _make_post(post_number=1)
    assert post.title == TOPIC_TITLE


def test_post_title_reply():
    post = _make_post(post_number=7)
    assert "reply" in post.title.lower()
    assert "7" in post.title


def test_post_plain_text():
    post = _make_post()
    assert "Hello Nervos." in post.plain_text
    assert "<p>" not in post.plain_text


def test_post_summary_is_short():
    long_html = "<p>" + ("word " * 200) + "</p>"
    post = DiscoursePost(
        id=1, topic_id=1, topic_title="T", topic_slug="s",
        post_number=1, username="u", created_at="", updated_at="",
        cooked=long_html,
    )
    assert len(post.summary) <= 301  # 300 + ellipsis


def test_post_keywords_includes_username_and_topic():
    post = _make_post()
    kw = post.keywords
    assert "user:alice" in kw
    assert f"topic:{TOPIC_ID}" in kw


def test_post_keywords_includes_tags():
    post = _make_post()
    assert "spark-program" in post.keywords


# ═══════════════════════════════════════════════════════════════════════════
# DiscourseCrawler — mocked HTTP
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def crawler():
    return DiscourseCrawler(base_url="https://talk.nervos.org", request_delay=0)


def _patch_get(side_effect):
    """Patch requests.Session.get with a callable side_effect."""
    return patch("requests.Session.get", side_effect=side_effect)


def _topic_then_posts(topic_json, posts_json):
    """Return a side_effect that yields topic_json first, then posts_json."""
    calls = [_mock_response(topic_json), _mock_response(posts_json)]
    idx = [0]
    def _side_effect(*args, **kwargs):
        resp = calls[min(idx[0], len(calls) - 1)]
        idx[0] += 1
        return resp
    return _side_effect


def test_fetch_topic_meta_parses_title(crawler):
    with _patch_get(lambda *a, **kw: _mock_response(_TOPIC_JSON)):
        meta = crawler.fetch_topic_meta(TOPIC_ID)
    assert meta.title == TOPIC_TITLE
    assert meta.id == TOPIC_ID
    assert meta.slug == TOPIC_SLUG


def test_fetch_topic_meta_returns_all_post_ids(crawler):
    with _patch_get(lambda *a, **kw: _mock_response(_TOPIC_JSON)):
        meta = crawler.fetch_topic_meta(TOPIC_ID)
    assert meta.all_post_ids == [101, 102, 103]


def test_fetch_topic_meta_parses_tags(crawler):
    with _patch_get(lambda *a, **kw: _mock_response(_TOPIC_JSON)):
        meta = crawler.fetch_topic_meta(TOPIC_ID)
    assert "spark-program" in meta.tags


def test_fetch_posts_by_ids_returns_posts(crawler):
    meta = _make_meta()
    with _patch_get(lambda *a, **kw: _mock_response(_POSTS_PAGE2_JSON)):
        posts = crawler.fetch_posts_by_ids(TOPIC_ID, [102, 103], meta)
    assert len(posts) == 2
    assert posts[0].post_number == 2
    assert posts[1].post_number == 3


def test_fetch_posts_by_ids_empty_list(crawler):
    meta = _make_meta()
    posts = crawler.fetch_posts_by_ids(TOPIC_ID, [], meta)
    assert posts == []


def test_iter_all_posts_yields_all(crawler):
    call_count = [0]
    def _side_effect(*args, **kwargs):
        n = call_count[0]
        call_count[0] += 1
        if n == 0:
            return _mock_response(_TOPIC_JSON)
        return _mock_response(_POSTS_PAGE2_JSON)

    with patch("requests.Session.get", side_effect=_side_effect):
        posts = list(crawler.iter_all_posts(TOPIC_ID))

    # stream=[101,102,103] → batch 1 fetches all 3; mock returns 2 for page2
    assert len(posts) >= 1


def test_iter_all_posts_skips_existing_anchors(crawler):
    skip = {f"doc:nervos-talk-{TOPIC_ID}#post:2"}

    call_count = [0]
    def _side_effect(*args, **kwargs):
        n = call_count[0]
        call_count[0] += 1
        if n == 0:
            return _mock_response(_TOPIC_JSON)
        return _mock_response(_POSTS_PAGE2_JSON)

    with patch("requests.Session.get", side_effect=_side_effect):
        posts = list(crawler.iter_all_posts(TOPIC_ID, skip_existing_anchors=skip))

    anchors = [p.anchor for p in posts]
    assert f"doc:nervos-talk-{TOPIC_ID}#post:2" not in anchors


def test_fetch_latest_topic_ids(crawler):
    with _patch_get(lambda *a, **kw: _mock_response(_LATEST_JSON)):
        ids = crawler.fetch_latest_topic_ids()
    assert TOPIC_ID in ids
    assert 8001 in ids


def test_fetch_latest_empty_stops_early(crawler):
    empty_page = {"topic_list": {"topics": []}}
    with _patch_get(lambda *a, **kw: _mock_response(empty_page)):
        ids = crawler.fetch_latest_topic_ids(pages=3)
    assert ids == []


def test_iter_latest_topic_ids_paginates_until_empty(crawler):
    queue = [_LATEST_JSON, _LATEST_JSON_PAGE2, {"topic_list": {"topics": []}}]
    idx = [0]

    def _side_effect(*args, **kwargs):
        resp = queue[min(idx[0], len(queue) - 1)]
        idx[0] += 1
        return _mock_response(resp)

    with _patch_get(_side_effect):
        ids = list(crawler.iter_latest_topic_ids(max_pages=None))

    assert ids == [9995, 8001, 8001, 7007]


def test_fetch_all_topic_ids_deduplicates(crawler):
    queue = [_LATEST_JSON, _LATEST_JSON_PAGE2, {"topic_list": {"topics": []}}]
    idx = [0]

    def _side_effect(*args, **kwargs):
        resp = queue[min(idx[0], len(queue) - 1)]
        idx[0] += 1
        return _mock_response(resp)

    with _patch_get(_side_effect):
        ids = crawler.fetch_all_topic_ids(max_pages=None)

    assert ids == [9995, 8001, 7007]


def test_crawl_all_uses_topic_list(crawler):
    writer = MagicMock()
    with patch.object(crawler, "fetch_all_topic_ids", return_value=[111, 222]):
        with patch.object(crawler, "crawl_topic", side_effect=[3, 0]):
            results = crawler.crawl_all(writer, max_pages=10, incremental=True)

    assert results == {111: 3, 222: 0}


def test_crawl_topic_writes_to_store(crawler, tmp_path):
    from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
    from nervos_brain.retrieval.dual_layer import DualLayerWriter

    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
    )
    qs = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    ar = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    writer = DualLayerWriter(qdrant_store=qs, archive_store=ar, config=cfg)

    # topic fetch + one batch of posts (all in stream=[101,102,103])
    responses_queue = [_TOPIC_JSON, _POSTS_PAGE2_JSON]
    idx = [0]
    def _side_effect(*args, **kwargs):
        resp = responses_queue[min(idx[0], len(responses_queue) - 1)]
        idx[0] += 1
        return _mock_response(resp)

    with patch("requests.Session.get", side_effect=_side_effect):
        n = crawler.crawl_topic(TOPIC_ID, writer, incremental=False)

    assert n > 0
    assert ar.count() == n


def test_crawl_topic_incremental_skips_existing(crawler, tmp_path):
    from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
    from nervos_brain.retrieval.dual_layer import DualLayerWriter

    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
    )
    qs = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    ar = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    writer = DualLayerWriter(qdrant_store=qs, archive_store=ar, config=cfg)

    responses_queue = [_TOPIC_JSON, _POSTS_PAGE2_JSON]
    idx = [0]
    def _side_effect(*args, **kwargs):
        resp = responses_queue[min(idx[0], len(responses_queue) - 1)]
        idx[0] += 1
        return _mock_response(resp)

    with patch("requests.Session.get", side_effect=_side_effect):
        n1 = crawler.crawl_topic(TOPIC_ID, writer, incremental=False)

    # Second crawl in incremental mode — all already in archive
    responses_queue2 = [_TOPIC_JSON, _POSTS_PAGE2_JSON]
    idx2 = [0]
    def _side_effect2(*args, **kwargs):
        resp = responses_queue2[min(idx2[0], len(responses_queue2) - 1)]
        idx2[0] += 1
        return _mock_response(resp)

    with patch("requests.Session.get", side_effect=_side_effect2):
        n2 = crawler.crawl_topic(TOPIC_ID, writer, incremental=True)

    assert n1 > 0
    assert n2 == 0  # nothing new to write


def test_crawl_latest_handles_per_topic_errors(crawler, tmp_path):
    from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
    from nervos_brain.retrieval.dual_layer import DualLayerWriter

    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
    )
    qs = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    ar = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    writer = DualLayerWriter(qdrant_store=qs, archive_store=ar, config=cfg)

    call_count = [0]
    def _side_effect(*args, **kwargs):
        n = call_count[0]
        call_count[0] += 1
        if n == 0:
            return _mock_response(_LATEST_JSON)  # latest topics
        if n == 1:
            return _mock_response(_TOPIC_JSON)   # first topic: OK
        if n == 2:
            return _mock_response(_POSTS_PAGE2_JSON)
        # second topic: simulate server error
        mock = MagicMock()
        mock.raise_for_status.side_effect = Exception("500 server error")
        return mock

    with patch("requests.Session.get", side_effect=_side_effect):
        results = crawler.crawl_latest(writer, pages=1, incremental=False)

    assert TOPIC_ID in results
    assert 8001 in results
    assert results[8001] == 0  # failed gracefully


def test_parse_post_static_method():
    meta = _make_meta()
    raw = {
        "id": 999,
        "post_number": 4,
        "username": "dave",
        "created_at": "2026-02-01T00:00:00.000Z",
        "updated_at": "2026-02-01T00:00:00.000Z",
        "cooked": "<p>Test post.</p>",
        "like_count": 7,
        "reply_count": 1,
    }
    post = DiscourseCrawler._parse_post(raw, meta)
    assert post.id == 999
    assert post.username == "dave"
    assert post.like_count == 7
    assert post.topic_title == TOPIC_TITLE
    assert "spark-program" in post.tags


def test_api_key_set_in_headers():
    c = DiscourseCrawler(api_key="k123", api_username="admin", request_delay=0)
    assert c._session.headers.get("Api-Key") == "k123"
    assert c._session.headers.get("Api-Username") == "admin"


def test_no_api_key_no_auth_headers():
    c = DiscourseCrawler(request_delay=0)
    assert "Api-Key" not in c._session.headers


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests — real network (run with: pytest -m integration)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
def test_integration_fetch_topic_meta():
    """Fetch topic 9995 metadata from the live forum."""
    crawler = DiscourseCrawler(request_delay=1.0)
    meta = crawler.fetch_topic_meta(9995)
    assert meta.id == 9995
    assert "Nervos" in meta.title or "nervos" in meta.title.lower()
    assert meta.posts_count > 0
    assert len(meta.all_post_ids) > 0


@pytest.mark.integration
def test_integration_fetch_first_batch(tmp_path):
    """Fetch the first batch of posts from topic 9995 and ingest into a temp store."""
    from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
    from nervos_brain.retrieval.dual_layer import DualLayerWriter

    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
    )
    qs = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    ar = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    writer = DualLayerWriter(qdrant_store=qs, archive_store=ar, config=cfg)

    crawler = DiscourseCrawler(request_delay=1.2)
    # Crawl only the first 20 posts to keep the test fast
    meta = crawler.fetch_topic_meta(9995)
    first_batch = meta.all_post_ids[:20]
    posts = crawler.fetch_posts_by_ids(9995, first_batch, meta)

    assert len(posts) > 0
    for post in posts:
        assert post.topic_id == 9995
        assert len(post.plain_text) > 0

    # Ingest and verify storage
    for post in posts:
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

    assert ar.count() == len(posts)
    # Verify one record is retrievable
    first_post = posts[0]
    stored = ar.get_by_anchor(first_post.anchor)
    assert stored is not None
    assert stored.source == "nervos_talk"
