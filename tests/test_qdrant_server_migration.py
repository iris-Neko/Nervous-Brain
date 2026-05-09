from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from nervos_brain.retrieval import ArchiveRecord, ArchiveStore, RetrievalConfig


def _load_script_module(name: str, filename: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_migration_payload_and_point_id_are_stable(tmp_path):
    module = _load_script_module(
        "migrate_qdrant_server_from_archive_under_test",
        "migrate_qdrant_server_from_archive.py",
    )
    cfg = RetrievalConfig(archive_db=str(tmp_path / "archive.db"))
    store = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    record = ArchiveRecord(
        id="r1",
        source="github_code",
        doc_type="github_code",
        url="https://github.com/nervosnetwork/fiber/blob/main/src/lib.rs",
        anchor="code:fiber#lib",
        title="nervosnetwork/fiber/src/lib.rs",
        summary="pub fn open_channel()",
        keywords="github,code,fiber,open_channel",
        raw_text="pub fn open_channel() {}",
        raw_format="code",
        lang="rust",
        version="abc123",
        topic="nervosnetwork/fiber",
        content_hash="hash-r1",
    )
    store.upsert(record)
    loaded = store.list_all()[0]

    payload = module._payload(loaded, cfg)

    assert payload["source"] == "github_code"
    assert payload["type"] == "github_code"
    assert payload["topic"] == "nervosnetwork/fiber"
    assert payload["hash"] == "hash-r1"
    assert module._point_id(loaded) == module._point_id(loaded)


def test_public_backend_configs_cover_publishable_three_corpora():
    module = _load_script_module(
        "migrate_qdrant_server_from_archive_public_configs_under_test",
        "migrate_qdrant_server_from_archive.py",
    )

    configs = module.load_public_backend_configs()

    assert [name for name, _ in configs] == [
        "retrieval",
        "retrieval_forum_talk",
        "retrieval_github_code",
    ]
    assert [cfg.collection_name for _, cfg in configs] == [
        "nervos_docs",
        "nervos_talk_user_discussions",
        "nervos_github_code",
    ]
    assert [cfg.archive_db for _, cfg in configs] == [
        "data/archive.db",
        "data/forum_talk/archive.db",
        "data/github_code/archive.db",
    ]


def test_archive_ready_rejects_git_lfs_pointer(tmp_path):
    module = _load_script_module(
        "migrate_qdrant_server_from_archive_lfs_under_test",
        "migrate_qdrant_server_from_archive.py",
    )
    pointer = tmp_path / "archive.db"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\n"
        "size 123\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Git LFS pointer"):
        module._assert_archive_ready(str(pointer))
