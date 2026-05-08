"""GitHub source-code crawler.

This crawler mirrors the GitHub docs crawler's repository discovery and clone
flow, but indexes code/config files into a separate corpus so source search can
be tuned independently from docs and forum retrieval.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Iterable

from .base import RawDocument
from .github_docs import GitHubDocsCrawler, GitHubRepo

logger = logging.getLogger(__name__)

_CODE_EXTENSIONS = {
    ".rs",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".sh",
    ".bash",
    ".sql",
    ".proto",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".php",
    ".sol",
    ".move",
    ".dockerfile",
    ".tla",
}

_CODE_FILE_NAMES = {
    "dockerfile",
    "makefile",
    "justfile",
    "cargo.toml",
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
    "next.config.js",
}

_EXCLUDED_DIR_HINTS = (
    "/node_modules/",
    "/vendor/",
    "/dist/",
    "/build/",
    "/target/",
    "/coverage/",
    "/.next/",
    "/.turbo/",
    "/.venv/",
    "/venv/",
    "/.git/",
)

_EXCLUDED_FILE_SUFFIXES = (
    ".lock",
    ".min.js",
    ".bundle.js",
    ".generated.rs",
    ".pb.go",
)

_SYMBOL_PATTERNS = (
    re.compile(r"\b(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:pub\s+)?(?:struct|enum|trait|mod)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    re.compile(r"\b(?:class|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    re.compile(r"\bfunc\s+(?:\([^)]+\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*[=:]"),
)


class GitHubCodeCrawler(GitHubDocsCrawler):
    """Crawl public GitHub source files and emit normalized code documents."""

    def __init__(
        self,
        targets: Iterable[str],
        *,
        clone_workspace: str = "data/tmp/github_code_repos",
        github_token: str | None = None,
        request_delay: float = 0.2,
        include_forks: bool = False,
        include_archived: bool = False,
        max_repos_per_owner: int | None = None,
        max_files_per_repo: int | None = None,
        max_file_bytes: int = 250_000,
        source: str = "github_code",
        git_timeout: int = 180,
        git_retries: int = 2,
    ) -> None:
        super().__init__(
            targets=targets,
            clone_workspace=clone_workspace,
            github_token=github_token,
            request_delay=request_delay,
            include_forks=include_forks,
            include_archived=include_archived,
            max_repos_per_owner=max_repos_per_owner,
            max_files_per_repo=max_files_per_repo,
            max_file_bytes=max_file_bytes,
            source=source,
        )
        self._git_timeout = max(1, int(git_timeout))
        self._git_retries = max(0, int(git_retries))

    def crawl(self) -> Iterable[RawDocument]:
        total_docs = 0
        for repo in self._iter_repositories():
            logger.info("Crawling code repo %s", repo.full_name)
            repo_docs = 0
            started = time.monotonic()
            try:
                for doc in self._crawl_repo(repo):
                    repo_docs += 1
                    total_docs += 1
                    yield doc
                logger.info(
                    "Finished code repo %s docs=%s elapsed=%.1fs total_docs=%s",
                    repo.full_name,
                    repo_docs,
                    time.monotonic() - started,
                    total_docs,
                )
            except Exception as exc:
                logger.warning("Failed to crawl code repo %s after docs=%s: %s", repo.full_name, repo_docs, exc)

    def _crawl_repo(self, repo: GitHubRepo) -> Iterable[RawDocument]:
        with tempfile.TemporaryDirectory(
            prefix=f"gh_code_{repo.owner}_{repo.name}_",
            dir=str(self._workspace),
        ) as temp_dir:
            repo_path = Path(temp_dir)
            self._clone_repo(repo, repo_path)
            commit = self._git_output(repo_path, "rev-parse", "HEAD").strip()
            file_paths = self._git_output(repo_path, "ls-tree", "-r", "--name-only", "HEAD")
            selected_paths = self._select_code_paths(file_paths.splitlines())

            for rel_path in selected_paths:
                size = int(self._git_output(repo_path, "cat-file", "-s", f"HEAD:{rel_path}").strip())
                if size > self._max_file_bytes:
                    continue

                blob = self._git_output(repo_path, "show", f"HEAD:{rel_path}", binary=True)
                text = self._decode_blob(blob)
                if not text.strip():
                    continue

                lang = self._infer_language(rel_path)
                url = f"https://github.com/{repo.owner}/{repo.name}/blob/{repo.default_branch}/{rel_path}"
                external_id = f"{repo.full_name}@{commit}:{rel_path}"
                yield RawDocument(
                    source=self._source,
                    external_id=external_id,
                    title=f"{repo.full_name}/{rel_path}",
                    raw_text=text,
                    url=url,
                    anchor=self._build_code_anchor(repo, commit, rel_path),
                    doc_type="github_code",
                    summary=self._make_code_summary(text, max_chars=500),
                    keywords=self._build_code_keywords(repo, rel_path, text),
                    raw_format="code",
                    lang=lang,
                    version=commit[:12],
                    topic=repo.full_name,
                    metadata={
                        "owner": repo.owner,
                        "repo": repo.name,
                        "branch": repo.default_branch,
                        "commit": commit,
                        "path": rel_path,
                        "language": lang,
                    },
                )

    def _clone_repo(self, repo: GitHubRepo, dst: Path) -> None:
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--no-tags",
            f"https://github.com/{repo.owner}/{repo.name}.git",
            str(dst),
        ]
        self._run_git_command(
            cmd,
            error_prefix=f"git clone failed for {repo.full_name}",
        )

    def _git_output(self, repo_path: Path, *args: str, binary: bool = False) -> str | bytes:
        cmd = ["git", "-C", str(repo_path), *args]
        stdout = self._run_git_command(
            cmd,
            error_prefix=f"git command failed ({' '.join(args)})",
        )
        if binary:
            return stdout
        return stdout.decode("utf-8", errors="replace")

    def _run_git_command(self, cmd: list[str], *, error_prefix: str) -> bytes:
        import subprocess

        last_error = ""
        for attempt in range(self._git_retries + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=self._git_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                last_error = f"timed out after {self._git_timeout}s"
                if attempt < self._git_retries:
                    logger.warning("%s: %s; retrying", error_prefix, last_error)
                    _cleanup_git_clone_destination(cmd)
                    continue
                raise RuntimeError(f"{error_prefix}: {last_error}") from exc

            if proc.returncode == 0:
                return proc.stdout

            last_error = proc.stderr.decode("utf-8", errors="replace").strip()
            if attempt < self._git_retries and _is_retryable_git_error(last_error):
                logger.warning("%s: %s; retrying", error_prefix, last_error[:300])
                _cleanup_git_clone_destination(cmd)
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            raise RuntimeError(f"{error_prefix}: {last_error}")

        raise RuntimeError(f"{error_prefix}: {last_error}")

    def _select_code_paths(self, paths: Iterable[str]) -> list[str]:
        selected = sorted({p for p in paths if p and self._is_code_path(p)})
        if self._max_files_per_repo is not None:
            return selected[: self._max_files_per_repo]
        return selected

    @staticmethod
    def _is_code_path(path: str) -> bool:
        normalized = "/" + path.strip("/").lower()
        if normalized == "/":
            return False
        if any(hint in normalized for hint in _EXCLUDED_DIR_HINTS):
            return False

        p = PurePosixPath(normalized)
        name = p.name
        if any(name.endswith(suffix) for suffix in _EXCLUDED_FILE_SUFFIXES):
            return False
        if name in _CODE_FILE_NAMES:
            return True
        return p.suffix.lower() in _CODE_EXTENSIONS

    @staticmethod
    def _infer_language(path: str) -> str:
        name = PurePosixPath(path).name.lower()
        suffix = PurePosixPath(path).suffix.lower()
        if name == "dockerfile":
            return "dockerfile"
        if name in {"makefile", "justfile"}:
            return "make"
        mapping = {
            ".rs": "rust",
            ".toml": "toml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".py": "python",
            ".go": "go",
            ".sh": "shell",
            ".bash": "shell",
            ".sql": "sql",
            ".proto": "protobuf",
            ".c": "c",
            ".h": "c",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".hpp": "cpp",
            ".java": "java",
            ".kt": "kotlin",
            ".swift": "swift",
            ".rb": "ruby",
            ".php": "php",
            ".sol": "solidity",
            ".move": "move",
            ".dockerfile": "dockerfile",
            ".tla": "tla",
        }
        return mapping.get(suffix, "unknown")

    @staticmethod
    def _make_code_summary(text: str, max_chars: int = 500) -> str:
        interesting: list[str] = []
        fallback: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if len(fallback) < 40:
                fallback.append(line)
            if re.search(
                r"\b(fn|func|def|class|struct|enum|trait|interface|type|function|const)\b",
                line,
            ):
                interesting.append(line)
            if len(interesting) >= 30:
                break

        lines = interesting or fallback
        summary = "\n".join(lines)
        if len(summary) <= max_chars:
            return summary
        return summary[: max_chars - 1].rstrip() + "…"

    @staticmethod
    def _build_code_keywords(repo: GitHubRepo, rel_path: str, text: str) -> str:
        pieces = [
            "github",
            "code",
            repo.owner,
            repo.name,
            repo.full_name,
        ]
        path_obj = PurePosixPath(rel_path)
        if path_obj.suffix:
            pieces.append(f"ext:{path_obj.suffix.lstrip('.')}")
        pieces.extend(part.lower() for part in path_obj.parts[:5])

        for pattern in _SYMBOL_PATTERNS:
            for match in pattern.finditer(text[:80_000]):
                pieces.append(match.group(1))

        seen: set[str] = set()
        unique: list[str] = []
        for token in pieces:
            cleaned = str(token).strip()
            key = cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            unique.append(cleaned)
            if len(unique) >= 120:
                break
        return ",".join(unique)

    @staticmethod
    def _build_code_anchor(repo: GitHubRepo, commit: str, rel_path: str) -> str:
        digest = hashlib.sha256(f"{repo.full_name}@{commit}:{rel_path}".encode("utf-8")).hexdigest()[:20]
        safe_owner = repo.owner.replace("/", "-")
        safe_repo = repo.name.replace("/", "-")
        return f"code:github-{safe_owner}-{safe_repo}#blob:{digest}"


def _is_retryable_git_error(stderr: str) -> bool:
    text = stderr.lower()
    return any(
        marker in text
        for marker in (
            "tls connect error",
            "unexpected eof",
            "early eof",
            "connection reset",
            "operation timed out",
            "failed to connect",
            "could not resolve host",
            "the remote end hung up unexpectedly",
            "promisor remote",
        )
    )


def _cleanup_git_clone_destination(cmd: list[str]) -> None:
    if len(cmd) >= 3 and cmd[0:2] == ["git", "clone"]:
        dst = Path(cmd[-1])
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
