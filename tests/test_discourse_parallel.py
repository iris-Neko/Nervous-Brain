from __future__ import annotations

from typing import Any

from nervos_brain.ingestion.discourse import DiscoursePost, TopicMeta
from nervos_brain.ingestion.discourse_parallel import (
    ForumCrawlState,
    ForumCrawlStateStore,
    ParallelDiscourseIngestor,
    build_crawl_key,
)
from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
from nervos_brain.retrieval.dual_layer import DualLayerWriter


def _build_writer(tmp_path) -> DualLayerWriter:
    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
        collection_name="forum_test",
    )
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qdrant, archive_store=archive, config=cfg)


def _make_post(topic_id: int, post_number: int, topic_title: str) -> DiscoursePost:
    return DiscoursePost(
        id=post_number,
        topic_id=topic_id,
        topic_title=topic_title,
        topic_slug=f"topic-{topic_id}",
        post_number=post_number,
        username="alice",
        created_at="2026-01-01T00:00:00.000Z",
        updated_at="2026-01-01T00:00:00.000Z",
        cooked=f"<p>topic {topic_id} post {post_number}</p>",
        tags=["test"],
    )


def test_build_crawl_key_is_order_independent_for_explicit_topics():
    k1 = build_crawl_key(
        mode="topic",
        base_url="https://talk.nervos.org",
        category_slug=None,
        latest_pages=None,
        max_pages=None,
        explicit_topic_ids=[9, 1, 4],
    )
    k2 = build_crawl_key(
        mode="topic",
        base_url="https://talk.nervos.org/",
        category_slug=None,
        latest_pages=None,
        max_pages=None,
        explicit_topic_ids=[4, 9, 1],
    )
    assert k1 == k2


def test_state_store_roundtrip_and_pending(tmp_path):
    store = ForumCrawlStateStore(str(tmp_path / "crawl_state.json"))
    key = build_crawl_key(
        mode="full",
        base_url="https://talk.nervos.org",
        category_slug="",
        latest_pages=None,
        max_pages=None,
        explicit_topic_ids=[],
    )
    state = ForumCrawlState.create(crawl_key=key, topic_ids=[3, 1, 2])
    state.mark_success(1)
    state.mark_failure(2, attempts=2, error="timeout")
    store.save(state)

    loaded = store.load()
    assert loaded is not None
    assert loaded.is_compatible(key)
    assert loaded.pending_topic_ids() == [2, 3]
    assert loaded.failed_topics["2"]["attempts"] == 2


def test_parallel_ingestor_retry_and_incremental_skip(tmp_path):
    writer = _build_writer(tmp_path)

    # Pre-seed one post in topic 1, so incremental mode should skip it.
    existing = _make_post(topic_id=1, post_number=1, topic_title="topic-1")
    writer.write(
        source="nervos_talk",
        doc_type="forum_post",
        url=existing.url,
        anchor=existing.anchor,
        title=existing.title,
        summary=existing.summary,
        keywords=existing.keywords,
        raw_text=existing.plain_text,
        raw_format="html",
        lang="en",
        version="latest",
        topic="nervos_talk:1",
    )

    topic_meta: dict[int, TopicMeta] = {
        1: TopicMeta(
            id=1,
            title="topic-1",
            slug="topic-1",
            posts_count=2,
            tags=["test"],
            category_id=1,
            all_post_ids=[1, 2],
        ),
        2: TopicMeta(
            id=2,
            title="topic-2",
            slug="topic-2",
            posts_count=1,
            tags=["test"],
            category_id=1,
            all_post_ids=[1],
        ),
    }
    posts: dict[tuple[int, int], DiscoursePost] = {
        (1, 1): _make_post(1, 1, "topic-1"),
        (1, 2): _make_post(1, 2, "topic-1"),
        (2, 1): _make_post(2, 1, "topic-2"),
    }
    calls: dict[str, Any] = {"topic2_fail_once": True}

    class FakeCrawler:
        def fetch_topic_meta(self, topic_id: int) -> TopicMeta:
            if topic_id == 2 and calls["topic2_fail_once"]:
                calls["topic2_fail_once"] = False
                raise RuntimeError("temporary failure")
            return topic_meta[topic_id]

        def fetch_posts_by_ids(self, topic_id: int, post_ids: list[int], meta: TopicMeta):
            return [posts[(topic_id, pid)] for pid in post_ids if (topic_id, pid) in posts]

    ingestor = ParallelDiscourseIngestor(
        writer=writer,
        archive_store=writer._archive,
        base_url="https://talk.nervos.org",
        request_delay=0,
        api_key=None,
        api_username=None,
        incremental=True,
        workers=2,
        max_retries=1,
        retry_backoff_s=0,
        crawler_factory=FakeCrawler,
    )

    results = ingestor.run_topics([1, 2])
    by_topic = {r.topic_id: r for r in results}

    assert by_topic[1].ok
    assert by_topic[1].written_posts == 1
    assert by_topic[1].skipped_existing >= 1

    assert by_topic[2].ok
    assert by_topic[2].attempts == 2
    assert by_topic[2].written_posts == 1
