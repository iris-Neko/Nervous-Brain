"""GitHub documentation crawler.

Targets can be either owner URLs or repo URLs:
  - https://github.com/nervosnetwork
  - https://github.com/nervosnetwork/fiber
  - nervosnetwork
  - nervosnetwork/fiber

The crawler resolves public repositories via GitHub REST API, then uses local
git commands to read doc-like files from each repository.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator
from urllib.parse import urlparse

import requests

from .base import RawDocument, SourceCrawler
from .html_cleaner import make_summary

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_WEB_HOSTS = {"github.com", "www.github.com"}

_DOC_EXTENSIONS = {
    ".md",
    ".markdown",
    ".mdx",
    ".rst",
    ".txt",
    ".adoc",
    ".asciidoc",
}
_ROOT_DOC_NAMES = {
    "changelog",
    "contributing",
    "security",
    "license",
    "code_of_conduct",
}
_DOC_DIR_HINTS = (
    "/docs/",
    "/doc/",
    "/guide/",
    "/guides/",
    "/tutorial/",
    "/tutorials/",
    "/spec/",
    "/specs/",
    "/book/",
    "/rfc/",
    "/rfcs/",
    "/whitepaper/",
    "/wiki/",
)
_EXCLUDED_DIR_HINTS = (
    "/node_modules/",
    "/vendor/",
    "/dist/",
    "/build/",
    "/target/",
    "/.venv/",
    "/venv/",
    "/.git/",
)


@dataclass(frozen=True)
class GitHubRepo:
    """Resolved repository reference."""

    owner: str
    name: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class _TargetRef:
    owner: str
    repo: str | None = None


def _parse_target_ref(target: str) -> _TargetRef:
    value = target.strip().rstrip("/")
    if not value:
        raise ValueError("empty GitHub target")

    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in _GITHUB_WEB_HOSTS:
            raise ValueError(f"unsupported GitHub host: {parsed.netloc}")
        parts = [p for p in parsed.path.split("/") if p]
    else:
        raw = value
        if raw.startswith("github.com/"):
            raw = raw[len("github.com/"):]
        parts = [p for p in raw.split("/") if p]

    if not parts:
        raise ValueError(f"invalid GitHub target: {target}")

    owner = parts[0]
    repo = None
    if len(parts) >= 2:
        repo = parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
    return _TargetRef(owner=owner, repo=repo)


class GitHubDocsCrawler(SourceCrawler):
    """Crawl public GitHub docs files and emit normalized RawDocument rows."""

    def __init__(
        self,
        targets: Iterable[str],
        *,
        clone_workspace: str = "data/tmp/github_repos",
        github_token: str | None = None,
        request_delay: float = 0.2,
        include_forks: bool = False,
        include_archived: bool = False,
        max_repos_per_owner: int | None = None,
        max_files_per_repo: int | None = None,
        max_file_bytes: int = 300_000,
        source: str = "github_docs",
    ) -> None:
        self._targets = [t.strip() for t in targets if t.strip()]
        if not self._targets:
            raise ValueError("at least one GitHub target is required")

        self._workspace = Path(clone_workspace)
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._request_delay = max(0.0, request_delay)
        self._include_forks = include_forks
        self._include_archived = include_archived
        self._max_repos_per_owner = max_repos_per_owner
        self._max_files_per_repo = max_files_per_repo
        self._max_file_bytes = max(1, max_file_bytes)
        self._source = source

        self._last_request_ts: float = 0.0
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "nervos-brain-github-crawler",
            }
        )
        if github_token:
            self._session.headers.update({"Authorization": f"Bearer {github_token}"})

    def crawl(self) -> Iterable[RawDocument]:
        for repo in self._iter_repositories():
            try:
                yield from self._crawl_repo(repo)
            except Exception as exc:
                logger.warning("Failed to crawl %s: %s", repo.full_name, exc)

    # ── target resolution ──────────────────────────────────────────────────

    def _iter_repositories(self) -> Iterator[GitHubRepo]:
        seen: set[str] = set()
        for target in self._targets:
            ref = _parse_target_ref(target)
            repos = (
                [self._fetch_repo(ref.owner, ref.repo)]
                if ref.repo
                else self._list_owner_repos(ref.owner)
            )
            for repo in repos:
                key = repo.full_name.lower()
                if key in seen:
                    continue
                seen.add(key)
                yield repo

    def _fetch_repo(self, owner: str, repo: str | None) -> GitHubRepo:
        if not repo:
            raise ValueError("repo is required")
        data = self._api_get_json(f"/repos/{owner}/{repo}")
        return GitHubRepo(
            owner=data["owner"]["login"],
            name=data["name"],
            default_branch=data.get("default_branch", "main"),
        )

    def _list_owner_repos(self, owner: str) -> list[GitHubRepo]:
        user = self._api_get_json(f"/users/{owner}")
        is_org = str(user.get("type", "")).lower() == "organization"
        endpoint = f"/orgs/{owner}/repos" if is_org else f"/users/{owner}/repos"

        repos: list[GitHubRepo] = []
        page = 1
        while True:
            params = {"per_page": 100, "page": page}
            rows = self._api_get_json(endpoint, params=params)
            if not rows:
                break
            for item in rows:
                if item.get("private", False):
                    continue
                if (not self._include_archived) and item.get("archived", False):
                    continue
                if (not self._include_forks) and item.get("fork", False):
                    continue
                repos.append(
                    GitHubRepo(
                        owner=item["owner"]["login"],
                        name=item["name"],
                        default_branch=item.get("default_branch", "main"),
                    )
                )
                if self._max_repos_per_owner and len(repos) >= self._max_repos_per_owner:
                    return repos
            page += 1
        return repos

    def fetch_repo_head_commit(self, repo: GitHubRepo) -> str:
        """Return current default-branch commit SHA without cloning."""
        data = self._api_get_json(f"/repos/{repo.owner}/{repo.name}/commits/{repo.default_branch}")
        sha = str(data.get("sha") or "").strip()
        if not sha:
            raise RuntimeError(f"GitHub API returned no head sha for {repo.full_name}")
        return sha

    def _api_get_json(self, path: str, params: dict | None = None):
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._request_delay:
            time.sleep(self._request_delay - elapsed)

        url = f"{_GITHUB_API}{path}"
        resp = self._session.get(url, params=params, timeout=20)
        self._last_request_ts = time.monotonic()
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset", "unknown")
            raise RuntimeError(
                "GitHub API rate limit exceeded. "
                f"Reset timestamp: {reset}. Set GITHUB_TOKEN and retry."
            )
        resp.raise_for_status()
        return resp.json()

    # ── repository crawling ────────────────────────────────────────────────

    def _crawl_repo(self, repo: GitHubRepo) -> Iterable[RawDocument]:
        with tempfile.TemporaryDirectory(
            prefix=f"gh_{repo.owner}_{repo.name}_",
            dir=str(self._workspace),
        ) as temp_dir:
            repo_path = Path(temp_dir)
            self._clone_repo(repo, repo_path)
            commit = self._git_output(repo_path, "rev-parse", "HEAD").strip()
            file_paths = self._git_output(repo_path, "ls-tree", "-r", "--name-only", "HEAD")
            selected_paths = self._select_doc_paths(file_paths.splitlines())

            for rel_path in selected_paths:
                size = int(self._git_output(repo_path, "cat-file", "-s", f"HEAD:{rel_path}").strip())
                if size > self._max_file_bytes:
                    continue

                blob = self._git_output(repo_path, "show", f"HEAD:{rel_path}", binary=True)
                text = self._decode_blob(blob)
                if not text.strip():
                    continue

                url = f"https://github.com/{repo.owner}/{repo.name}/blob/{repo.default_branch}/{rel_path}"
                external_id = f"{repo.full_name}@{commit}:{rel_path}"
                yield RawDocument(
                    source=self._source,
                    external_id=external_id,
                    title=f"{repo.full_name}/{rel_path}",
                    raw_text=text,
                    url=url,
                    anchor=self._build_anchor(repo, commit, rel_path),
                    doc_type="github_doc",
                    summary=make_summary(text, max_chars=300),
                    keywords=self._build_keywords(repo, rel_path),
                    raw_format=self._infer_raw_format(rel_path),
                    lang="unknown",
                    version=commit[:12],
                    topic=repo.full_name,
                    metadata={
                        "owner": repo.owner,
                        "repo": repo.name,
                        "branch": repo.default_branch,
                        "commit": commit,
                        "path": rel_path,
                    },
                )

    def _clone_repo(self, repo: GitHubRepo, dst: Path) -> None:
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{repo.owner}/{repo.name}.git",
            str(dst),
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git clone failed for {repo.full_name}: {stderr}")

    def _git_output(self, repo_path: Path, *args: str, binary: bool = False) -> str | bytes:
        cmd = ["git", "-C", str(repo_path), *args]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git command failed ({' '.join(args)}): {stderr}")
        if binary:
            return proc.stdout
        return proc.stdout.decode("utf-8", errors="replace")

    # ── helpers ────────────────────────────────────────────────────────────

    def _select_doc_paths(self, paths: Iterable[str]) -> list[str]:
        selected = sorted({p for p in paths if p and self._is_doc_path(p)})
        if self._max_files_per_repo is not None:
            return selected[: self._max_files_per_repo]
        return selected

    @staticmethod
    def _is_doc_path(path: str) -> bool:
        normalized = "/" + path.strip("/").lower()
        if normalized == "/":
            return False
        if any(hint in normalized for hint in _EXCLUDED_DIR_HINTS):
            return False

        p = PurePosixPath(normalized)
        name = p.name
        stem = p.stem
        ext = p.suffix.lower()

        if name.startswith("readme"):
            return True
        if stem in _ROOT_DOC_NAMES:
            return True
        if ext not in _DOC_EXTENSIONS:
            return False
        if len(p.parts) <= 2:  # root text docs
            return True
        if any(hint in normalized for hint in _DOC_DIR_HINTS):
            return True
        if any(token in name for token in ("guide", "tutorial", "spec", "whitepaper", "faq")):
            return True
        return False

    @staticmethod
    def _decode_blob(blob: bytes) -> str:
        if not blob:
            return ""
        if b"\x00" in blob:
            return ""
        return blob.decode("utf-8", errors="replace")

    @staticmethod
    def _infer_raw_format(path: str) -> str:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in {".md", ".markdown", ".mdx"}:
            return "markdown"
        if suffix == ".rst":
            return "rst"
        if suffix in {".adoc", ".asciidoc"}:
            return "adoc"
        return "text"

    @staticmethod
    def _build_anchor(repo: GitHubRepo, commit: str, rel_path: str) -> str:
        digest = hashlib.sha256(f"{repo.full_name}@{commit}:{rel_path}".encode("utf-8")).hexdigest()[:20]
        safe_owner = repo.owner.replace("/", "-")
        safe_repo = repo.name.replace("/", "-")
        return f"doc:github-{safe_owner}-{safe_repo}#blob:{digest}"

    @staticmethod
    def _build_keywords(repo: GitHubRepo, rel_path: str) -> str:
        pieces = ["github", repo.owner, repo.name]
        path_obj = PurePosixPath(rel_path)
        if path_obj.suffix:
            pieces.append(f"ext:{path_obj.suffix.lstrip('.')}")
        for part in path_obj.parts[:3]:
            pieces.append(part.lower())
        # preserve order while removing duplicates
        seen: set[str] = set()
        unique: list[str] = []
        for token in pieces:
            if token in seen:
                continue
            seen.add(token)
            unique.append(token)
        return ",".join(unique)
