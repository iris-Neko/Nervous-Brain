import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nervos_brain.retrieval import QdrantStore, RetrievalConfig, search_with_filters


def run_query(store: QdrantStore, query: str, filters: dict, top_k: int = 3) -> None:
    print(f"\n[query] {query}")
    print(f"[filter] {filters}")
    results = search_with_filters(store=store, query=query, filters=filters, top_k=top_k)
    for i, ev in enumerate(results, start=1):
        print(f"  {i}. {ev['title']} | source={ev['payload'].get('source')} | score={ev['score']:.4f}")
        print(f"     anchor={ev['anchor']}")
        print(f"     snippet={ev['snippet'][:100]}...")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    config = RetrievalConfig()
    store = QdrantStore(config=config, qdrant_location=str(root / "data" / "qdrant_local"))

    run_query(
        store,
        query="cell model",
        filters={"source": "rfcs", "type": "doc"},
    )
    run_query(
        store,
        query="open channel",
        filters={"source": "fiber", "type": "doc"},
    )
    run_query(
        store,
        query="invalid capacity",
        filters={"source": "ccc", "type": "code"},
    )


if __name__ == "__main__":
    main()
