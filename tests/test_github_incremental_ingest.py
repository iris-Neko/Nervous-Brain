from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from nervos_brain.ingestion.base import RawDocument
from nervos_brain.ingestion.github_docs import GitHubDocsCrawler, GitHubRepo
from nervos_brain.ingestion.github_incremental import (
    GitHubIngestState,
    GitHubIngestStateStore,
    GitHubManifestWriter,
    cleanup_stale_repo_records,
    run_incremental_github_ingest,
    targets_hash,
)
from nervos_brain.retrieval import ArchiveStore, QdrantStore, RetrievalConfig
from nervos_brain.retrieval.dual_layer import DualLayerWriter


class _StubCrawler(GitHubDocsCrawler):
    def __init__(self, tmp_path: Path, *, commit: str = "newcommit", fail: bool = False) -> None:
        super().__init__(targets=["nervosnetwork/fiber"], clone_workspace=str(tmp_path / "clones"))
        self.commit = commit
        self.fail = fail
        self.crawled = 0

    def _iter_repositories(self):
        yield GitHubRepo(owner="nervosnetwork", name="fiber", default_branch="main")

    def fetch_repo_head_commit(self, repo: GitHubRepo) -> str:
        return self.commit

    def _crawl_repo(self, repo: GitHubRepo) -> Iterable[RawDocument]:
        self.crawled += 1
        if self.fail:
            raise RuntimeError("clone failed")
        yield RawDocument(
            source="github_docs",
            external_id=f"{repo.full_name}@{self.commit}:README.md",
            title=f"{repo.full_name}/README.md",
            raw_text=f"# Fiber {self.commit}",
            url=f"https://github.com/{repo.full_name}/blob/main/README.md",
            anchor=f"doc:github-nervosnetwork-fiber#blob:{self.commit[:8]}",
            doc_type="github_doc",
            summary=f"Fiber {self.commit}",
            keywords="github,nervosnetwork,fiber",
            raw_format="markdown",
            lang="unknown",
            version=self.commit[:12],
            topic=repo.full_name,
            metadata={"repo": repo.full_name, "commit": self.commit, "path": "README.md"},
        )


def _row(doc: RawDocument) -> dict:
    return {"title": doc.title, "raw_text": doc.raw_text, "anchor": doc.anchor, "topic": doc.topic}


def _writer(tmp_path: Path) -> tuple[DualLayerWriter, RetrievalConfig]:
    cfg = RetrievalConfig(
        qdrant_path=str(tmp_path / "qdrant"),
        archive_db=str(tmp_path / "archive.db"),
        collection_name="github_docs_test",
    )
    writer = DualLayerWriter(
        qdrant_store=QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path),
        archive_store=ArchiveStore(db_path=cfg.archive_db, config=cfg),
        config=cfg,
    )
    return writer, cfg


def test_state_store_and_manifest_roundtrip(tmp_path):
    state = GitHubIngestState.create(corpus="github_docs", targets_hash=targets_hash(["nervosnetwork/fiber"]))
    store = GitHubIngestStateStore(str(tmp_path / "state.json"))
    store.save(state)

    loaded = store.load()

    assert loaded is not None
    assert loaded.is_compatible(corpus="github_docs", targets_hash=targets_hash(["nervosnetwork/fiber"]))

    manifest = tmp_path / "manifest.json"
    GitHubManifestWriter(str(manifest)).write(corpus="github_docs", targets=["nervosnetwork/fiber"], state=loaded)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["corpus"] == "github_docs"
    assert payload["targets"] == ["nervosnetwork/fiber"]


def test_incremental_skips_unchanged_repo_without_crawling(tmp_path):
    writer, cfg = _writer(tmp_path)
    state_file = tmp_path / "state.json"
    manifest = tmp_path / "manifest.json"
    jsonl = tmp_path / "delta.jsonl"

    first = _StubCrawler(tmp_path, commit="samecommit")
    result1 = run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=first,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )
    second = _StubCrawler(tmp_path, commit="samecommit")
    result2 = run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=second,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )

    assert result1.changed_repos == 1
    assert result2.skipped_repos == 1
    assert second.crawled == 0


def test_incremental_changed_repo_cleans_old_records(tmp_path):
    writer, cfg = _writer(tmp_path)
    state_file = tmp_path / "state.json"
    manifest = tmp_path / "manifest.json"
    jsonl = tmp_path / "delta.jsonl"

    old = _StubCrawler(tmp_path, commit="oldcommit")
    run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=old,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )
    new = _StubCrawler(tmp_path, commit="newcommit")
    result = run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=new,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )

    records = writer._archive.list_by_source_topic(source="github_docs", topic="nervosnetwork/fiber")
    assert result.cleaned == 1
    assert len(records) == 1
    assert records[0].version == "newcommit"[:12]
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["repos"][0]["commit"] == "newcommit"


def test_incremental_failure_does_not_advance_state_or_cleanup(tmp_path):
    writer, cfg = _writer(tmp_path)
    state_file = tmp_path / "state.json"
    manifest = tmp_path / "manifest.json"
    jsonl = tmp_path / "delta.jsonl"

    ok = _StubCrawler(tmp_path, commit="okcommit")
    run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=ok,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )
    failing = _StubCrawler(tmp_path, commit="badcommit", fail=True)
    result = run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=failing,
        writer=writer,
        cfg=cfg,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
    )

    loaded = GitHubIngestStateStore(str(state_file)).load()
    records = writer._archive.list_by_source_topic(source="github_docs", topic="nervosnetwork/fiber")
    assert result.failed_repos == 1
    assert loaded is not None
    assert loaded.repos["nervosnetwork/fiber"].commit == "okcommit"
    assert loaded.repos["nervosnetwork/fiber"].status == "indexed"
    assert len(records) == 1
    assert records[0].version == "okcommit"[:12]


def test_incremental_dry_run_does_not_require_writer_or_write_state(tmp_path):
    state_file = tmp_path / "state.json"
    manifest = tmp_path / "manifest.json"
    jsonl = tmp_path / "delta.jsonl"
    crawler = _StubCrawler(tmp_path, commit="drycommit")

    result = run_incremental_github_ingest(
        corpus="github_docs",
        targets=["nervosnetwork/fiber"],
        crawler=crawler,
        writer=None,
        cfg=None,
        state_file=str(state_file),
        manifest_out=str(manifest),
        jsonl_out=str(jsonl),
        row_factory=_row,
        dry_run=True,
    )

    assert result.changed_repos == 1
    assert result.seen == 1
    assert result.written == 1
    assert jsonl.exists()
    assert not state_file.exists()
    assert not manifest.exists()


def test_cleanup_stale_repo_records_deletes_only_removed_hashes(tmp_path):
    writer, _cfg = _writer(tmp_path)
    keep = writer.write(
        source="github_docs",
        doc_type="github_doc",
        url="u1",
        anchor="a1",
        title="keep",
        summary="keep",
        keywords="k",
        raw_text="keep",
        topic="repo",
    )
    writer.write(
        source="github_docs",
        doc_type="github_doc",
        url="u2",
        anchor="a2",
        title="stale",
        summary="stale",
        keywords="k",
        raw_text="stale",
        topic="repo",
    )

    removed = cleanup_stale_repo_records(writer, source="github_docs", topic="repo", keep_hashes={keep})

    assert removed == 1
    records = writer._archive.list_by_source_topic(source="github_docs", topic="repo")
    assert len(records) == 1
    assert records[0].title == "keep"
