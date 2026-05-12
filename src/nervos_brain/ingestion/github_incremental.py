"""Incremental GitHub ingest helpers.

Runtime state is local/private. Manifest output is public metadata describing
which repo commits are represented by the published corpus.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .base import RawDocument, SourceCrawler
from .github_docs import GitHubDocsCrawler, GitHubRepo
from .pipeline import IngestionPipeline

_STATE_VERSION = 1


def now_ms() -> int:
    return int(time.time() * 1000)


def targets_hash(targets: Iterable[str]) -> str:
    normalized = sorted(str(t).strip() for t in targets if str(t).strip())
    payload = "\n".join(normalized)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class GitHubRepoState:
    repo: str
    branch: str = ""
    commit: str = ""
    status: str = "unknown"
    doc_count: int = 0
    updated_ts_ms: int = 0
    error: str = ""


@dataclass
class GitHubIncrementalResult:
    repos: int = 0
    changed_repos: int = 0
    skipped_repos: int = 0
    seen: int = 0
    written: int = 0
    cleaned: int = 0
    failed_repos: int = 0
    bm25_index_size: int = 0


@dataclass
class GitHubIngestState:
    version: int
    corpus: str
    targets_hash: str
    repos: dict[str, GitHubRepoState] = field(default_factory=dict)
    updated_ts_ms: int = 0

    @classmethod
    def create(cls, *, corpus: str, targets_hash: str) -> "GitHubIngestState":
        return cls(version=_STATE_VERSION, corpus=corpus, targets_hash=targets_hash, updated_ts_ms=now_ms())

    @classmethod
    def from_dict(cls, raw: dict) -> "GitHubIngestState":
        repos: dict[str, GitHubRepoState] = {}
        raw_repos = raw.get("repos", {})
        if isinstance(raw_repos, dict):
            for repo, value in raw_repos.items():
                if isinstance(value, dict):
                    repos[str(repo)] = GitHubRepoState(
                        repo=str(value.get("repo") or repo),
                        branch=str(value.get("branch") or ""),
                        commit=str(value.get("commit") or ""),
                        status=str(value.get("status") or "unknown"),
                        doc_count=int(value.get("doc_count") or 0),
                        updated_ts_ms=int(value.get("updated_ts_ms") or 0),
                        error=str(value.get("error") or ""),
                    )
        return cls(
            version=int(raw.get("version") or _STATE_VERSION),
            corpus=str(raw.get("corpus") or ""),
            targets_hash=str(raw.get("targets_hash") or ""),
            repos=repos,
            updated_ts_ms=int(raw.get("updated_ts_ms") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "corpus": self.corpus,
            "targets_hash": self.targets_hash,
            "updated_ts_ms": self.updated_ts_ms,
            "repos": {repo: asdict(state) for repo, state in sorted(self.repos.items())},
        }

    def is_compatible(self, *, corpus: str, targets_hash: str) -> bool:
        return self.version == _STATE_VERSION and self.corpus == corpus and self.targets_hash == targets_hash


class GitHubIngestStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> GitHubIngestState | None:
        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return None
        return GitHubIngestState.from_dict(raw)

    def save(self, state: GitHubIngestState) -> None:
        state.updated_ts_ms = now_ms()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)


class GitHubManifestWriter:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *, corpus: str, targets: list[str], state: GitHubIngestState) -> None:
        repos = []
        for repo, item in sorted(state.repos.items()):
            if item.status != "indexed":
                continue
            repos.append(
                {
                    "repo": repo,
                    "branch": item.branch,
                    "commit": item.commit,
                    "doc_count": item.doc_count,
                    "updated_ts_ms": item.updated_ts_ms,
                }
            )
        payload = {
            "version": 1,
            "corpus": corpus,
            "targets": sorted(str(t).strip() for t in targets if str(t).strip()),
            "targets_hash": targets_hash(targets),
            "generated_ts_ms": now_ms(),
            "repos": repos,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)


class GitHubRepoCrawler(SourceCrawler):
    """Wrap a GitHub crawler class so it crawls one already-resolved repo."""

    def __init__(self, crawler: GitHubDocsCrawler, repo: GitHubRepo) -> None:
        self._crawler = crawler
        self._repo = repo

    def crawl(self):
        yield from self._crawler._crawl_repo(self._repo)


class JsonlExportCrawler(SourceCrawler):
    """Append crawler output to JSONL while yielding docs."""

    def __init__(self, base: SourceCrawler, jsonl_path: str, *, row_factory) -> None:
        self._base = base
        self._path = Path(jsonl_path)
        self._row_factory = row_factory

    def crawl(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            for doc in self._base.crawl():
                f.write(json.dumps(self._row_factory(doc), ensure_ascii=False) + "\n")
                yield doc


def reset_jsonl(path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def resolve_repositories(crawler: GitHubDocsCrawler) -> list[GitHubRepo]:
    return list(crawler._iter_repositories())


def latest_commit_for_repo(crawler: GitHubDocsCrawler, repo: GitHubRepo) -> str:
    return str(crawler.fetch_repo_head_commit(repo)).strip()


def run_incremental_github_ingest(
    *,
    corpus: str,
    targets: list[str],
    crawler: GitHubDocsCrawler,
    writer,
    cfg,
    state_file: str,
    manifest_out: str,
    jsonl_out: str,
    row_factory: Callable[[RawDocument], dict],
    reset_state: bool = False,
    dry_run: bool = False,
) -> GitHubIncrementalResult:
    """Run repo-commit incremental ingest for GitHub docs/code corpora."""
    from nervos_brain.retrieval import MultiRetriever

    if not dry_run and writer is None:
        raise ValueError("writer is required when dry_run is false")

    state_store = GitHubIngestStateStore(state_file)
    thash = targets_hash(targets)
    loaded = None if reset_state else state_store.load()
    state = (
        loaded
        if loaded and loaded.is_compatible(corpus=corpus, targets_hash=thash)
        else GitHubIngestState.create(corpus=corpus, targets_hash=thash)
    )
    pipeline = IngestionPipeline(writer)
    reset_jsonl(jsonl_out)

    repos = resolve_repositories(crawler)
    result = GitHubIncrementalResult(repos=len(repos))

    for repo in repos:
        repo_key = repo.full_name
        try:
            commit = latest_commit_for_repo(crawler, repo)
            previous = state.repos.get(repo_key)
            if previous and previous.commit == commit and previous.status == "indexed":
                result.skipped_repos += 1
                continue

            result.changed_repos += 1
            export_crawler = JsonlExportCrawler(
                GitHubRepoCrawler(crawler, repo),
                jsonl_out,
                row_factory=row_factory,
            )
            stats = pipeline.run(export_crawler, dry_run=dry_run)
            result.seen += stats.seen
            result.written += stats.written

            if stats.failed:
                result.failed_repos += 1
                if not dry_run:
                    if previous is None:
                        state.repos[repo_key] = GitHubRepoState(
                            repo=repo_key,
                            branch=repo.default_branch,
                            commit="",
                            status="failed",
                            doc_count=0,
                            updated_ts_ms=now_ms(),
                            error="; ".join(stats.errors[:3])[:500],
                        )
                        state_store.save(state)
                print(f"[failed] repo={repo_key} error={'; '.join(stats.errors[:3])[:500]}")
                continue

            removed = 0
            if not dry_run:
                keep_hashes = set(stats.content_hashes)
                removed = cleanup_stale_repo_records(
                    writer,
                    source=corpus,
                    topic=repo.full_name,
                    keep_hashes=keep_hashes,
                )
                result.cleaned += removed

            if not dry_run:
                state.repos[repo_key] = GitHubRepoState(
                    repo=repo_key,
                    branch=repo.default_branch,
                    commit=commit,
                    status="indexed",
                    doc_count=stats.written,
                    updated_ts_ms=now_ms(),
                    error="",
                )
                state_store.save(state)
            print(f"[indexed] repo={repo_key} commit={commit[:12]} docs={stats.written} cleaned={removed}")
        except Exception as exc:
            result.failed_repos += 1
            previous = state.repos.get(repo_key)
            if not dry_run:
                if previous is None:
                    state.repos[repo_key] = GitHubRepoState(
                        repo=repo_key,
                        branch=repo.default_branch,
                        commit="",
                        status="failed",
                        doc_count=0,
                        updated_ts_ms=now_ms(),
                        error=str(exc)[:500],
                    )
                    state_store.save(state)
            print(f"[failed] repo={repo_key} error={exc}")

    if not dry_run:
        GitHubManifestWriter(manifest_out).write(corpus=corpus, targets=targets, state=state)
    if not dry_run:
        retriever = MultiRetriever(qdrant_store=writer._qdrant, archive_store=writer._archive, config=cfg)
        result.bm25_index_size = retriever.rebuild_bm25()
    return result


def cleanup_stale_repo_records(writer, *, source: str, topic: str, keep_hashes: set[str]) -> int:
    existing = writer._archive.list_by_source_topic(source=source, topic=topic)
    stale_hashes = [record.content_hash for record in existing if record.content_hash not in keep_hashes]
    if not stale_hashes:
        return 0
    writer._qdrant.delete_by_hashes(stale_hashes)
    return writer._archive.delete_by_hashes(stale_hashes)
