"""Generic crawler-to-database ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from nervos_brain.retrieval.dual_layer import DualLayerWriter

from .base import SourceCrawler


@dataclass
class IngestStats:
    seen: int = 0
    written: int = 0
    skipped: int = 0
    failed: int = 0
    content_hashes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class IngestionPipeline:
    """Run a crawler and persist normalized documents to the dual-layer DB."""

    def __init__(self, writer: DualLayerWriter) -> None:
        self._writer = writer

    def run(self, crawler: SourceCrawler, *, dry_run: bool = False) -> IngestStats:
        stats = IngestStats()
        batch_hashes: set[str] = set()

        for doc in crawler.crawl():
            stats.seen += 1
            try:
                if dry_run:
                    stats.written += 1
                    continue

                content_hash = self._writer.write(
                    source=doc.source,
                    doc_type=doc.doc_type,
                    url=doc.url,
                    anchor=doc.anchor,
                    title=doc.title,
                    summary=doc.summary,
                    keywords=doc.keywords,
                    raw_text=doc.raw_text,
                    raw_format=doc.raw_format,
                    lang=doc.lang,
                    version=doc.version,
                    topic=doc.topic,
                )
                if content_hash in batch_hashes:
                    stats.skipped += 1
                else:
                    batch_hashes.add(content_hash)
                    stats.content_hashes.append(content_hash)
                    stats.written += 1
            except Exception as exc:
                stats.failed += 1
                stats.errors.append(str(exc))

        return stats
