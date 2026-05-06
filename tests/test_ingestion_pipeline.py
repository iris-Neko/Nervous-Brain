from __future__ import annotations

import json

from nervos_brain.ingestion import IngestionPipeline, JsonlCrawler
from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
from nervos_brain.retrieval.dual_layer import DualLayerWriter


def _write_jsonl(path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_writer(tmp_path) -> DualLayerWriter:
    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        vector_size=64,
    )
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qdrant, archive_store=archive, config=cfg)


def test_jsonl_crawler_parses_rows(tmp_path):
    jsonl = tmp_path / "source.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "id": "doc-1",
                "title": "Fiber Overview",
                "raw_text": "Fiber is a payment channel on Nervos.",
                "keywords": ["fiber", "payment", "channel"],
            }
        ],
    )

    crawler = JsonlCrawler(file_path=str(jsonl), source="test_source")
    docs = list(crawler.crawl())

    assert len(docs) == 1
    assert docs[0].source == "test_source"
    assert docs[0].title == "Fiber Overview"
    assert docs[0].anchor.startswith("doc:test_source-")
    assert "fiber" in docs[0].keywords


def test_pipeline_writes_to_dual_layer(tmp_path):
    jsonl = tmp_path / "source.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "id": "doc-1",
                "title": "Cell Model",
                "raw_text": "Cell is the basic data unit in CKB.",
                "url": "https://example.com/cell",
                "topic": "ckb",
            },
            {
                "id": "doc-2",
                "title": "HTLC Timeout",
                "raw_text": "HTLC timeout is enforced on-chain.",
                "url": "https://example.com/htlc",
                "topic": "fiber",
            },
            {
                # invalid row: missing raw_text -> skipped by crawler in non-strict mode
                "id": "doc-3",
                "title": "Invalid Row",
            },
        ],
    )

    writer = _build_writer(tmp_path)
    crawler = JsonlCrawler(file_path=str(jsonl), source="jsonl_docs", strict=False)
    stats = IngestionPipeline(writer).run(crawler)

    assert stats.seen == 2
    assert stats.written == 2
    assert stats.failed == 0
    assert writer._archive.count() == 2


def test_pipeline_counts_batch_duplicates(tmp_path):
    jsonl = tmp_path / "source.jsonl"
    row = {
        "id": "doc-dup",
        "title": "Same Doc",
        "raw_text": "same body",
        "url": "https://example.com/same",
    }
    _write_jsonl(jsonl, [row, row])

    writer = _build_writer(tmp_path)
    crawler = JsonlCrawler(file_path=str(jsonl), source="dup_source")
    stats = IngestionPipeline(writer).run(crawler)

    assert stats.seen == 2
    assert stats.written == 1
    assert stats.skipped == 1
    assert writer._archive.count() == 1
