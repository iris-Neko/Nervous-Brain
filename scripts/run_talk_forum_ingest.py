#!/usr/bin/env python3
"""Ingest talk.nervos.org user discussions into a dedicated database.

This script is intentionally isolated from the standard docs DB.
Default storage targets:
  - SQLite archive: data/forum_talk/archive.db
  - Qdrant path:    data/qdrant_talk_forum
  - Collection:     nervos_talk_user_discussions

Examples:
  # Crawl all available latest pages with resumable state (default mode):
  python scripts/run_talk_forum_ingest.py

  # Crawl all, but stop after 50 pages:
  python scripts/run_talk_forum_ingest.py --max-pages 50

  # Use 12 workers for faster crawl:
  python scripts/run_talk_forum_ingest.py --workers 12

  # Crawl one topic only:
  python scripts/run_talk_forum_ingest.py --topic 9995

  # Crawl category latest (2 pages):
  python scripts/run_talk_forum_ingest.py --category chinese --latest-pages 2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.ingestion import (
    ForumCrawlState,
    ForumCrawlStateStore,
    ParallelDiscourseIngestor,
    TopicCrawlResult,
    build_crawl_key,
)
from nervos_brain.retrieval import (
    ArchiveStore,
    MultiRetriever,
    QdrantStore,
    RetrievalConfig,
    get_retrieval_section,
    load_retrieval_config,
)
from nervos_brain.retrieval.dual_layer import DualLayerWriter

DEFAULT_FORUM_ARCHIVE_DB = "data/forum_talk/archive.db"
DEFAULT_FORUM_QDRANT_PATH = "data/qdrant_talk_forum"
DEFAULT_FORUM_COLLECTION = "nervos_talk_user_discussions"
DEFAULT_STATE_FILE = "data/forum_talk/crawl_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("talk-forum-ingest")


def build_writer(cfg: RetrievalConfig) -> DualLayerWriter:
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qdrant, archive_store=archive, config=cfg)


def build_forum_config(args: argparse.Namespace) -> RetrievalConfig:
    section = "retrieval_forum_talk"
    base = load_retrieval_config()
    merged = {
        **base.__dict__,
        "archive_db": DEFAULT_FORUM_ARCHIVE_DB,
        "qdrant_path": DEFAULT_FORUM_QDRANT_PATH,
        "collection_name": DEFAULT_FORUM_COLLECTION,
        **get_retrieval_section(section),
    }

    if args.archive:
        merged["archive_db"] = args.archive
    if args.qdrant:
        merged["qdrant_path"] = args.qdrant
    if args.collection:
        merged["collection_name"] = args.collection

    return RetrievalConfig(**merged)


def resolve_mode(args: argparse.Namespace) -> str:
    if args.topic:
        return "topic"
    if args.latest_pages is not None:
        return "latest"
    return "full"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest Nervos Talk forum posts into a dedicated user-discussion DB "
            "with parallel workers and resumable crawl state."
        ),
    )
    parser.add_argument("--base-url", default="https://talk.nervos.org", help="Discourse forum base URL.")
    parser.add_argument("--topic", action="append", type=int, help="Specific topic ID(s). Repeatable.")
    parser.add_argument(
        "--latest-pages",
        type=int,
        default=None,
        help="Crawl latest/category feed for N pages. If omitted, runs full crawl mode.",
    )
    parser.add_argument("--category", default=None, help="Category slug, e.g. english/chinese.")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Max pages in full crawl mode (<=0 means no limit).",
    )
    parser.add_argument("--incremental", action="store_true", default=True, help="Skip existing anchors.")
    parser.add_argument("--no-incremental", dest="incremental", action="store_false", help="Re-crawl all posts.")
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds between API requests.")
    parser.add_argument("--api-key", default=None, help="Discourse API key (optional).")
    parser.add_argument("--api-user", default=None, help="Discourse API username (optional).")
    parser.add_argument("--archive", default=None, help="Override SQLite archive path.")
    parser.add_argument("--qdrant", default=None, help="Override Qdrant path.")
    parser.add_argument("--collection", default=None, help="Override Qdrant collection name.")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel topic workers.")
    parser.add_argument("--max-retries", type=int, default=2, help="Retry count per topic on failure.")
    parser.add_argument("--retry-backoff", type=float, default=1.0, help="Retry backoff seconds.")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Resumable crawl state JSON path.")
    parser.add_argument("--save-every", type=int, default=10, help="Persist state every N completed topics.")
    parser.add_argument("--reset-state", action="store_true", help="Ignore existing state and rebuild topic list.")
    parser.add_argument(
        "--no-refresh-topic-list",
        action="store_true",
        help="Do not refresh/merge latest topic IDs when resuming an existing state.",
    )
    args = parser.parse_args()

    cfg = build_forum_config(args)
    writer = build_writer(cfg)
    mode = resolve_mode(args)
    explicit_topics = sorted(set(args.topic or []))
    max_pages = args.max_pages if args.max_pages > 0 else None
    latest_pages = args.latest_pages
    if mode == "latest":
        if latest_pages is None or latest_pages <= 0:
            raise SystemExit("--latest-pages must be > 0")
        max_pages = None
    if mode == "topic":
        latest_pages = None
        max_pages = None

    crawl_key = build_crawl_key(
        mode=mode,
        base_url=args.base_url,
        category_slug=args.category,
        latest_pages=latest_pages,
        max_pages=max_pages,
        explicit_topic_ids=explicit_topics,
    )
    state_store = ForumCrawlStateStore(args.state_file)

    ingestor = ParallelDiscourseIngestor(
        writer=writer,
        archive_store=writer._archive,
        base_url=args.base_url,
        request_delay=args.delay,
        api_key=args.api_key,
        api_username=args.api_user,
        incremental=args.incremental,
        workers=args.workers,
        max_retries=args.max_retries,
        retry_backoff_s=args.retry_backoff,
    )

    logger.info(
        "Forum DB targets: archive=%s qdrant=%s collection=%s",
        cfg.archive_db,
        cfg.qdrant_path,
        cfg.collection_name,
    )
    logger.info(
        "Crawl settings: mode=%s workers=%d incremental=%s state=%s",
        mode,
        max(1, args.workers),
        args.incremental,
        args.state_file,
    )

    existing_state = None if args.reset_state else state_store.load()
    if existing_state and existing_state.is_compatible(crawl_key):
        state = existing_state
        logger.info(
            "Resume crawl state: completed=%d/%d pending=%d",
            len(set(state.completed_topic_ids)),
            len(state.topic_ids),
            len(state.pending_topic_ids()),
        )
        if mode in {"full", "latest"} and not args.no_refresh_topic_list:
            latest_ids = ingestor.resolve_topic_ids(
                mode=mode,
                category_slug=args.category,
                latest_pages=latest_pages,
                max_pages=max_pages,
                explicit_topic_ids=explicit_topics,
            )
            before = set(state.topic_ids)
            merged = sorted(before | set(latest_ids))
            new_count = len(merged) - len(before)
            if new_count > 0:
                state.topic_ids = merged
                state_store.save(state)
                logger.info("Merged %d new topic(s) into existing state", new_count)
    else:
        if existing_state:
            logger.info("Existing state is incompatible with current args; rebuilding topic list.")
        topic_ids = ingestor.resolve_topic_ids(
            mode=mode,
            category_slug=args.category,
            latest_pages=latest_pages,
            max_pages=max_pages,
            explicit_topic_ids=explicit_topics,
        )
        state = ForumCrawlState.create(crawl_key=crawl_key, topic_ids=topic_ids)
        state_store.save(state)
        logger.info("Topic list prepared: %d topic(s)", len(topic_ids))

    pending = state.pending_topic_ids()
    if not pending:
        logger.info("No pending topics. Crawl already complete for current state key.")
    else:
        logger.info("Starting crawl for %d pending topic(s)", len(pending))

        save_every = max(1, int(args.save_every))

        def on_topic_done(result: TopicCrawlResult, done: int, total: int) -> None:
            if result.ok:
                state.mark_success(result.topic_id)
                logger.info(
                    "[ok] topic=%d written=%d skipped=%d total=%d attempts=%d progress=%d/%d",
                    result.topic_id,
                    result.written_posts,
                    result.skipped_existing,
                    result.total_posts,
                    result.attempts,
                    done,
                    total,
                )
            else:
                state.mark_failure(result.topic_id, attempts=result.attempts, error=result.error)
                logger.warning(
                    "[fail] topic=%d attempts=%d error=%s progress=%d/%d",
                    result.topic_id,
                    result.attempts,
                    result.error,
                    done,
                    total,
                )
            if done % save_every == 0 or done == total:
                state_store.save(state)

        results = ingestor.run_topics(pending, on_topic_done=on_topic_done)
        state_store.save(state)

        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        total_written = sum(r.written_posts for r in results)
        logger.info(
            "Crawl finished: processed_topics=%d success=%d failed=%d written_posts=%d",
            len(results),
            ok_count,
            fail_count,
            total_written,
        )

    retriever = MultiRetriever(
        qdrant_store=writer._qdrant,
        archive_store=writer._archive,
        config=cfg,
    )
    indexed = retriever.rebuild_bm25()
    logger.info("BM25 index rebuilt: %d records", indexed)


if __name__ == "__main__":
    main()
