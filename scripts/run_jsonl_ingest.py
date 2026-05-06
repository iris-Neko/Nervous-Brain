#!/usr/bin/env python3
"""Generic JSONL source ingestion CLI.

Usage:
  python scripts/run_jsonl_ingest.py \
    --file data/my_source.jsonl \
    --source my_docs \
    --topic onboarding \
    --lang en
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from repo root without package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.ingestion import IngestionPipeline, JsonlCrawler
from nervos_brain.retrieval import (
    ArchiveStore,
    MultiRetriever,
    QdrantStore,
    RetrievalConfig,
    load_retrieval_config,
)
from nervos_brain.retrieval.dual_layer import DualLayerWriter


def _build_writer(cfg: RetrievalConfig) -> DualLayerWriter:
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qdrant, archive_store=archive, config=cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a JSONL source into dual-layer DB.")
    parser.add_argument("--file", required=True, help="Path to JSONL file.")
    parser.add_argument("--source", required=True, help="Source name written to payload.source.")
    parser.add_argument("--doc-type", default="doc", help="Default doc_type.")
    parser.add_argument("--lang", default="unknown", help="Default language.")
    parser.add_argument("--version", default="unknown", help="Default version.")
    parser.add_argument("--topic", default="unknown", help="Default topic.")
    parser.add_argument("--strict", action="store_true", help="Fail on malformed rows.")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write DB.")
    parser.add_argument("--archive", default=None, help="Override archive db path.")
    parser.add_argument("--qdrant", default=None, help="Override qdrant path.")
    args = parser.parse_args()

    cfg = load_retrieval_config()
    if args.archive:
        cfg = RetrievalConfig(**{**cfg.__dict__, "archive_db": args.archive})
    if args.qdrant:
        cfg = RetrievalConfig(**{**cfg.__dict__, "qdrant_path": args.qdrant})

    crawler = JsonlCrawler(
        file_path=args.file,
        source=args.source,
        doc_type=args.doc_type,
        lang=args.lang,
        version=args.version,
        topic=args.topic,
        strict=args.strict,
    )
    writer = _build_writer(cfg)
    pipeline = IngestionPipeline(writer)
    stats = pipeline.run(crawler, dry_run=args.dry_run)

    print("Ingest finished:")
    print(f"  seen={stats.seen}")
    print(f"  written={stats.written}")
    print(f"  skipped={stats.skipped}")
    print(f"  failed={stats.failed}")
    if stats.errors:
        print("  errors:")
        for err in stats.errors[:5]:
            print(f"    - {err}")

    if not args.dry_run:
        retriever = MultiRetriever(
            qdrant_store=writer._qdrant,
            archive_store=writer._archive,
            config=cfg,
        )
        indexed = retriever.rebuild_bm25()
        print(f"  bm25_index_size={indexed}")


if __name__ == "__main__":
    main()
