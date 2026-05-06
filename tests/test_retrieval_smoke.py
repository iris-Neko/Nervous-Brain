from pathlib import Path

import pytest

from nervos_brain.retrieval import (
    QdrantStore,
    RetrievalConfig,
    build_ingest_records,
    search_with_filters,
)


@pytest.fixture()
def built_store(tmp_path):
    cfg = RetrievalConfig()
    qdrant_dir = tmp_path / "qdrant_local"
    store = QdrantStore(config=cfg, qdrant_location=str(qdrant_dir))

    root = Path(__file__).resolve().parent.parent
    samples = root / "data" / "samples"
    sources = [
        ("rfcs_cell.md", "rfcs", "doc", "latest", "en", "https://docs.nervos.org/rfcs/cell", "cell"),
        ("fiber_channel.md", "fiber", "doc", "v0.3", "en", "https://docs.nervos.org/fiber/open-channel", "fiber"),
        ("ccc_capacity.md", "ccc", "code", "latest", "en", "https://github.com/ckb-ecofund/ccc", "capacity"),
    ]

    for filename, source, doc_type, version, lang, url, topic in sources:
        records = build_ingest_records(
            file_path=str(samples / filename),
            source=source,
            doc_type=doc_type,
            version=version,
            lang=lang,
            url=url,
            topic=topic,
            config=cfg,
        )
        store.upsert_chunks(records)
    return store


def test_requires_filters(built_store):
    with pytest.raises(ValueError):
        search_with_filters(store=built_store, query="cell", filters={}, top_k=3)


def test_query_rfcs_source(built_store):
    results = search_with_filters(
        store=built_store,
        query="cell model",
        filters={"source": "rfcs", "type": "doc"},
        top_k=3,
    )
    assert len(results) > 0
    assert all(ev["payload"]["source"] == "rfcs" for ev in results)


def test_query_fiber_source(built_store):
    results = search_with_filters(
        store=built_store,
        query="open channel",
        filters={"source": "fiber", "type": "doc"},
        top_k=3,
    )
    assert len(results) > 0
    assert all(ev["payload"]["source"] == "fiber" for ev in results)


def test_query_ccc_source(built_store):
    results = search_with_filters(
        store=built_store,
        query="invalid capacity",
        filters={"source": "ccc", "type": "code"},
        top_k=3,
    )
    assert len(results) > 0
    assert all(ev["payload"]["source"] == "ccc" for ev in results)
