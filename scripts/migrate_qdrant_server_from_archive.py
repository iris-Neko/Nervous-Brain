#!/usr/bin/env python3
"""Rebuild Qdrant server collections from SQLite archive stores.

This is the migration path from embedded Qdrant local directories to a shared
Qdrant server. The SQLite archive remains the source of truth.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.retrieval import ArchiveStore, RetrievalConfig, deterministic_embedding
from nervos_brain.retrieval.config import load_retrieval_backend_configs


def _point_id(record) -> str:
    return str(uuid5(NAMESPACE_URL, str(record.content_hash or record.anchor)))


def _payload(record, cfg: RetrievalConfig) -> dict[str, str]:
    return {
        "source": record.source,
        "type": record.doc_type,
        "version": record.version,
        "lang": record.lang,
        "url": record.url,
        "anchor": record.anchor,
        "topic": record.topic,
        "title": record.title,
        "snippet": record.summary[: cfg.snippet_max_chars],
        "keywords": record.keywords,
        "hash": record.content_hash,
    }


def _batched(rows: list, size: int):
    for idx in range(0, len(rows), size):
        yield rows[idx : idx + size]


def migrate_backend(
    *,
    client: QdrantClient,
    name: str,
    cfg: RetrievalConfig,
    recreate: bool,
    batch_size: int,
) -> int:
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    records = archive.list_all()
    existing = {c.name for c in client.get_collections().collections}

    if recreate and cfg.collection_name in existing:
        client.delete_collection(cfg.collection_name)
        existing.remove(cfg.collection_name)

    if cfg.collection_name not in existing:
        if not recreate:
            print(
                f"[check] {name}: collection={cfg.collection_name} missing; "
                f"archive_rows={len(records)}"
            )
            return 0
        client.create_collection(
            collection_name=cfg.collection_name,
            vectors_config=VectorParams(size=cfg.vector_size, distance=Distance.COSINE),
        )

    if not recreate:
        info = client.get_collection(cfg.collection_name)
        print(
            f"[check] {name}: collection={cfg.collection_name} "
            f"points={info.points_count} archive_rows={len(records)}"
        )
        return int(info.points_count or 0)

    written = 0
    for batch in _batched(records, batch_size):
        points = []
        for record in batch:
            index_text = f"{record.title}\n{record.summary}"
            points.append(
                PointStruct(
                    id=_point_id(record),
                    vector=deterministic_embedding(index_text, cfg.vector_size),
                    payload=_payload(record, cfg),
                )
            )
        if points:
            client.upsert(collection_name=cfg.collection_name, points=points)
            written += len(points)

    info = client.get_collection(cfg.collection_name)
    print(
        f"[migrated] {name}: collection={cfg.collection_name} "
        f"written={written} points={info.points_count} archive_rows={len(records)}"
    )
    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Qdrant server collections from SQLite archive stores.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:6333",
        help="Qdrant server URL.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="Optional Qdrant API key. Empty for localhost/no-auth deployments.",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete/recreate target collections and upsert all archive rows.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Qdrant upsert batch size.",
    )
    args = parser.parse_args()

    client = QdrantClient(url=args.url, api_key=args.api_key or None, timeout=30.0)
    try:
        client.get_collections()
    except Exception as exc:
        print(
            f"Failed to connect to Qdrant server at {args.url}. "
            "Start it with: docker compose -f docker-compose.qdrant.yml up -d",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    total = 0
    for name, cfg in load_retrieval_backend_configs():
        cfg = RetrievalConfig(**{**cfg.__dict__, "qdrant_url": args.url})
        total += migrate_backend(
            client=client,
            name=name,
            cfg=cfg,
            recreate=bool(args.recreate),
            batch_size=max(1, int(args.batch_size)),
        )

    mode = "migrated" if args.recreate else "checked"
    print(f"Qdrant server archive migration {mode}: total={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
