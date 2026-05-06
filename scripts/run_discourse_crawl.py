#!/usr/bin/env python3
"""CLI entry-point for the Nervos Talk Discourse crawler.

Usage examples
--------------
# Crawl a single topic (full):
python scripts/run_discourse_crawl.py --topic 9995

# Crawl a single topic (incremental — skip posts already in archive):
python scripts/run_discourse_crawl.py --topic 9995 --incremental

# Crawl latest N pages of site-wide topics:
python scripts/run_discourse_crawl.py --latest --pages 2

# Crawl a specific category:
python scripts/run_discourse_crawl.py --category english --pages 1

# Custom archive path:
python scripts/run_discourse_crawl.py --topic 9995 --archive data/my_archive.db
"""

import argparse
import logging
import sys
from pathlib import Path

# allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.ingestion import DiscourseCrawler
from nervos_brain.retrieval import (
    ArchiveStore,
    MultiRetriever,
    QdrantStore,
    RetrievalConfig,
    load_retrieval_config,
)
from nervos_brain.retrieval.dual_layer import DualLayerWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("crawl")


def build_writer(cfg: RetrievalConfig) -> DualLayerWriter:
    qs = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    ar = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qs, archive_store=ar, config=cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Nervos Talk crawler")
    parser.add_argument("--base-url", default="https://talk.nervos.org",
                        help="Forum base URL")
    parser.add_argument("--topic", type=int, metavar="ID",
                        help="Single topic ID to crawl")
    parser.add_argument("--latest", action="store_true",
                        help="Crawl latest topics")
    parser.add_argument("--category", metavar="SLUG",
                        help="Category slug to crawl (implies --latest)")
    parser.add_argument("--pages", type=int, default=1,
                        help="Number of pages for --latest/--category (default: 1)")
    parser.add_argument("--incremental", action="store_true", default=True,
                        help="Skip posts already in archive (default: on)")
    parser.add_argument("--no-incremental", dest="incremental", action="store_false",
                        help="Re-crawl all posts even if already stored")
    parser.add_argument("--delay", type=float, default=1.2,
                        help="Seconds between API requests (default: 1.2)")
    parser.add_argument("--api-key", default=None, help="Discourse API key")
    parser.add_argument("--api-user", default=None, help="Discourse API username")
    parser.add_argument("--archive", default=None,
                        help="Override archive DB path from config")
    parser.add_argument("--qdrant", default=None,
                        help="Override Qdrant path from config")
    args = parser.parse_args()

    cfg = load_retrieval_config()
    if args.archive:
        cfg = RetrievalConfig(**{**cfg.__dict__, "archive_db": args.archive})
    if args.qdrant:
        cfg = RetrievalConfig(**{**cfg.__dict__, "qdrant_path": args.qdrant})

    writer = build_writer(cfg)
    crawler = DiscourseCrawler(
        base_url=args.base_url,
        request_delay=args.delay,
        api_key=args.api_key,
        api_username=args.api_user,
    )

    if args.topic:
        logger.info("Crawling topic %d (incremental=%s)", args.topic, args.incremental)
        n = crawler.crawl_topic(args.topic, writer, incremental=args.incremental)
        logger.info("Done — wrote %d posts", n)

    elif args.latest or args.category:
        label = f"category={args.category}" if args.category else "latest"
        logger.info("Crawling %s (pages=%d, incremental=%s)",
                    label, args.pages, args.incremental)
        results = crawler.crawl_latest(
            writer,
            category_slug=args.category,
            pages=args.pages,
            incremental=args.incremental,
        )
        total = sum(results.values())
        logger.info("Done — %d topics, %d total posts written", len(results), total)

    else:
        parser.print_help()
        sys.exit(1)

    # Rebuild BM25 index after ingestion
    retriever = MultiRetriever(
        qdrant_store=writer._qdrant,
        archive_store=writer._archive,
        config=cfg,
    )
    n_indexed = retriever.rebuild_bm25()
    logger.info("BM25 index rebuilt — %d records", n_indexed)


if __name__ == "__main__":
    main()
