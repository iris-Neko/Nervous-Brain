from nervos_brain.retrieval import (
    RetrievalConfig,
    build_anchor,
    build_payload,
    build_stable_hash,
    chunk_text,
    get_retrieval_backend_sections,
    get_retrieval_section,
    load_retrieval_backend_configs,
    load_retrieval_config,
)
import nervos_brain.retrieval.config as retrieval_config_module


def test_anchor_is_stable():
    a1 = build_anchor("rfcs_cell", 3)
    a2 = build_anchor("rfcs_cell", 3)
    assert a1 == a2
    assert a1 == "doc:rfcs_cell#chunk:3"


def test_hash_is_stable_for_same_input():
    payload = build_payload(
        source="rfcs",
        doc_type="doc",
        version="latest",
        lang="en",
        url="https://docs.nervos.org/rfcs/cell",
        anchor="doc:rfcs_cell#chunk:0",
        topic="cell",
    )
    h1 = build_stable_hash(payload["url"], payload["anchor"], "hello world", payload)
    h2 = build_stable_hash(payload["url"], payload["anchor"], "hello world", payload)
    assert h1 == h2


def test_payload_fill_unknown():
    payload = build_payload(
        source="",
        doc_type="",
        version="",
        lang="",
        url="",
        anchor="",
    )
    assert payload["source"] == "unknown"
    assert payload["type"] == "unknown"
    assert payload["version"] == "unknown"
    assert payload["lang"] == "unknown"
    assert payload["url"] == "unknown"
    assert payload["anchor"] == "unknown"
    assert payload["topic"] == "unknown"


def test_chunk_text_basic():
    text = "a" * 1200
    chunks = chunk_text(text, chunk_size=600, overlap=120)
    assert len(chunks) >= 2
    assert all(len(c) <= 600 for c in chunks)


def test_config_defaults():
    cfg = RetrievalConfig()
    assert cfg.vector_size == 64
    assert cfg.collection_name == "nervos_docs"


def test_load_retrieval_config_supports_section_inheritance(monkeypatch):
    monkeypatch.setattr(
        retrieval_config_module,
        "_config_cache",
        {
            "retrieval": {
                "qdrant_path": "data/qdrant_main",
                "collection_name": "main_docs",
                "vector_size": 64,
            },
            "retrieval_forum_talk": {
                "archive_db": "data/forum_talk/archive.db",
                "collection_name": "forum_docs",
            },
        },
        raising=False,
    )

    cfg = load_retrieval_config(section="retrieval_forum_talk", inherit_from="retrieval")
    assert cfg.qdrant_path == "data/qdrant_main"
    assert cfg.archive_db == "data/forum_talk/archive.db"
    assert cfg.collection_name == "forum_docs"


def test_get_retrieval_section_returns_raw_section(monkeypatch):
    monkeypatch.setattr(
        retrieval_config_module,
        "_config_cache",
        {"retrieval_forum_talk": {"archive_db": "data/forum_talk/archive.db"}},
        raising=False,
    )
    assert get_retrieval_section("retrieval_forum_talk") == {
        "archive_db": "data/forum_talk/archive.db"
    }


def test_get_retrieval_backend_sections_defaults_to_primary(monkeypatch):
    monkeypatch.setattr(
        retrieval_config_module,
        "_config_cache",
        {"retrieval": {"archive_db": "data/archive.db"}},
        raising=False,
    )
    assert get_retrieval_backend_sections() == ["retrieval"]


def test_load_retrieval_backend_configs_uses_configured_sections(monkeypatch):
    monkeypatch.setattr(
        retrieval_config_module,
        "_config_cache",
        {
            "retrieval_backends": ["retrieval", "retrieval_forum_talk"],
            "retrieval": {
                "qdrant_path": "data/qdrant_local",
                "collection_name": "nervos_docs",
                "archive_db": "data/archive.db",
                "vector_size": 64,
                "final_top_k": 7,
            },
            "retrieval_forum_talk": {
                "qdrant_path": "data/qdrant_talk_forum",
                "collection_name": "nervos_talk_user_discussions",
                "archive_db": "data/forum_talk/archive.db",
            },
        },
        raising=False,
    )

    configs = load_retrieval_backend_configs()
    assert [name for name, _ in configs] == ["retrieval", "retrieval_forum_talk"]
    assert configs[0][1].archive_db == "data/archive.db"
    assert configs[1][1].archive_db == "data/forum_talk/archive.db"
    assert configs[1][1].qdrant_path == "data/qdrant_talk_forum"
    assert configs[1][1].collection_name == "nervos_talk_user_discussions"
    assert configs[1][1].final_top_k == 7
