import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nervos_brain.retrieval import QdrantStore, RetrievalConfig, build_ingest_records


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    samples = root / "data" / "samples"
    config = RetrievalConfig()
    store = QdrantStore(config=config, qdrant_location=str(root / "data" / "qdrant_local"))

    sources = [
        ("rfcs_cell.md", "rfcs", "doc", "latest", "en", "https://docs.nervos.org/rfcs/cell", "cell"),
        ("fiber_channel.md", "fiber", "doc", "v0.3", "en", "https://docs.nervos.org/fiber/open-channel", "fiber"),
        ("ccc_capacity.md", "ccc", "code", "latest", "en", "https://github.com/ckb-ecofund/ccc", "capacity"),
        ("talk_routing.md", "talk", "forum", "unknown", "en", "https://talk.nervos.org/t/fiber-routing", "routing"),
    ]

    total = 0
    for filename, source, doc_type, version, lang, url, topic in sources:
        records = build_ingest_records(
            file_path=str(samples / filename),
            source=source,
            doc_type=doc_type,
            version=version,
            lang=lang,
            url=url,
            topic=topic,
            config=config,
        )
        total += store.upsert_chunks(records)
        print(f"[ingest] {filename} -> {len(records)} chunks")

    print(f"[done] total chunks upserted: {total}")


if __name__ == "__main__":
    main()
