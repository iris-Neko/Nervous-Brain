#!/usr/bin/env python3
"""Fetch plain-text web documents and ingest them into the docs retrieval DB.

Examples:
  python scripts/run_web_text_ingest.py \
    --url https://context7.com/ckb-devrel/ccc/llms.txt \
    --url https://mintlify.wiki/ckb-devrel/ccc/llms-full.txt \
    --topic ckb-devrel/ccc
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

# Allow running from repo root without package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.ingestion import IngestionPipeline, RawDocument, SourceCrawler
from nervos_brain.ingestion.html_cleaner import make_summary
from nervos_brain.retrieval import (
    ArchiveStore,
    MultiRetriever,
    QdrantStore,
    RetrievalConfig,
    load_retrieval_config,
)
from nervos_brain.retrieval.dual_layer import DualLayerWriter


class WebTextCrawler(SourceCrawler):
    """Fetch text URLs and emit normalized docs."""

    def __init__(
        self,
        urls: list[str],
        *,
        topic: str,
        source: str = "github_docs",
        doc_type: str = "github_doc",
        timeout_s: float = 30.0,
    ) -> None:
        self._urls = urls
        self._topic = topic
        self._source = source
        self._doc_type = doc_type
        self._timeout_s = timeout_s

    def crawl(self):
        session = requests.Session()
        session.headers.update({"User-Agent": "nervos-brain-web-text-ingest"})
        for url in self._urls:
            resp = session.get(url, timeout=self._timeout_s)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                continue
            yield RawDocument(
                source=self._source,
                external_id=_external_id_for_url(url),
                title=_title_for_url(url),
                raw_text=text,
                url=url,
                anchor=_anchor_for_url(url),
                doc_type=self._doc_type,
                summary=make_summary(text, max_chars=300),
                keywords=_keywords_for_url(url, self._topic),
                raw_format="markdown" if url.endswith((".md", ".markdown")) else "text",
                lang="unknown",
                version="web",
                topic=self._topic,
                metadata={"url": url, "topic": self._topic},
            )


class JsonlExportCrawler(SourceCrawler):
    """Tee crawler output to JSONL while yielding docs for ingestion."""

    def __init__(self, base: SourceCrawler, jsonl_path: str) -> None:
        self._base = base
        self._path = Path(jsonl_path)

    def crawl(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            for doc in self._base.crawl():
                f.write(json.dumps(_doc_to_row(doc), ensure_ascii=False) + "\n")
                yield doc


def _build_writer(cfg: RetrievalConfig) -> DualLayerWriter:
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    return DualLayerWriter(qdrant_store=qdrant, archive_store=archive, config=cfg)


def _doc_to_row(doc: RawDocument) -> dict:
    return {
        "id": doc.external_id,
        "title": doc.title,
        "raw_text": doc.raw_text,
        "url": doc.url,
        "anchor": doc.anchor,
        "doc_type": doc.doc_type,
        "summary": doc.summary,
        "keywords": doc.keywords,
        "raw_format": doc.raw_format,
        "lang": doc.lang,
        "version": doc.version,
        "topic": doc.topic,
        "metadata": doc.metadata,
    }


def _external_id_for_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or "index"
    return f"web:{parsed.netloc}/{path}"


def _anchor_for_url(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    parsed = urlparse(url)
    safe_host = parsed.netloc.replace(".", "-")
    return f"doc:web-{safe_host}#url:{digest}"


def _title_for_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/") or parsed.netloc
    return f"{parsed.netloc}/{path}"


def _keywords_for_url(url: str, topic: str) -> str:
    parsed = urlparse(url)
    pieces = ["web", parsed.netloc, topic]
    pieces.extend(part for part in parsed.path.strip("/").split("/") if part)
    seen: set[str] = set()
    unique: list[str] = []
    for piece in pieces:
        key = piece.lower()
        if not piece or key in seen:
            continue
        seen.add(key)
        unique.append(piece)
    return ",".join(unique)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch plain-text web docs and ingest into Qdrant + SQLite.",
    )
    parser.add_argument("--url", action="append", required=True, help="Text URL to fetch.")
    parser.add_argument("--topic", default="web_text", help="Topic label for the documents.")
    parser.add_argument("--source", default="github_docs", help="Source name written to payload.source.")
    parser.add_argument("--doc-type", default="github_doc", help="Document type.")
    parser.add_argument(
        "--jsonl-out",
        default="data/tmp/web_text_delta.jsonl",
        help="Path to export fetched documents as JSONL.",
    )
    parser.add_argument("--timeout-s", type=float, default=30.0, help="HTTP timeout per URL.")
    parser.add_argument("--no-ingest", action="store_true", help="Only fetch + export JSONL; skip DB.")
    parser.add_argument("--dry-run", action="store_true", help="Parse only; skip DB writes.")
    parser.add_argument("--archive", default=None, help="Override archive db path.")
    parser.add_argument("--qdrant", default=None, help="Override qdrant path.")
    args = parser.parse_args()

    crawler = WebTextCrawler(
        args.url,
        topic=args.topic,
        source=args.source,
        doc_type=args.doc_type,
        timeout_s=args.timeout_s,
    )
    export_crawler = JsonlExportCrawler(crawler, jsonl_path=args.jsonl_out)

    try:
        if args.no_ingest:
            docs = 0
            for _ in export_crawler.crawl():
                docs += 1
            print("Fetch finished:")
            print(f"  urls={len(args.url)}")
            print(f"  docs={docs}")
            print(f"  jsonl={args.jsonl_out}")
            return

        cfg = load_retrieval_config()
        if args.archive:
            cfg = RetrievalConfig(**{**cfg.__dict__, "archive_db": args.archive})
        if args.qdrant:
            cfg = RetrievalConfig(**{**cfg.__dict__, "qdrant_path": args.qdrant})

        writer = _build_writer(cfg)
        pipeline = IngestionPipeline(writer)
        stats = pipeline.run(export_crawler, dry_run=args.dry_run)

        print("Web text ingest finished:")
        print(f"  urls={len(args.url)}")
        print(f"  seen={stats.seen}")
        print(f"  written={stats.written}")
        print(f"  skipped={stats.skipped}")
        print(f"  failed={stats.failed}")
        print(f"  jsonl={args.jsonl_out}")
        if stats.errors:
            print("  errors:")
            for err in stats.errors[:10]:
                print(f"    - {err}")

        if not args.dry_run:
            retriever = MultiRetriever(
                qdrant_store=writer._qdrant,
                archive_store=writer._archive,
                config=cfg,
            )
            indexed = retriever.rebuild_bm25()
            print(f"  bm25_index_size={indexed}")
    except requests.RequestException as exc:
        print(f"Network error while fetching web text: {exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
