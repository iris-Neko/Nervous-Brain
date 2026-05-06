from __future__ import annotations

from pathlib import Path

import pytest

from nervos_brain.ingestion.github_docs import GitHubDocsCrawler, GitHubRepo, _parse_target_ref


def test_parse_target_ref_supports_owner_and_repo():
    owner_only = _parse_target_ref("https://github.com/nervosnetwork")
    assert owner_only.owner == "nervosnetwork"
    assert owner_only.repo is None

    repo_ref = _parse_target_ref("nervosnetwork/fiber")
    assert repo_ref.owner == "nervosnetwork"
    assert repo_ref.repo == "fiber"

    with pytest.raises(ValueError):
        _parse_target_ref("https://gitlab.com/nervosnetwork")


def test_select_doc_paths_filters_non_docs(tmp_path):
    crawler = GitHubDocsCrawler(
        targets=["nervosnetwork/fiber"],
        clone_workspace=str(tmp_path),
        max_files_per_repo=None,
    )
    selected = crawler._select_doc_paths(
        [
            "README.md",
            "docs/fiber.md",
            "src/main.rs",
            "guide/quickstart.rst",
            "node_modules/pkg/readme.md",
        ]
    )
    assert "README.md" in selected
    assert "docs/fiber.md" in selected
    assert "guide/quickstart.rst" in selected
    assert "src/main.rs" not in selected
    assert "node_modules/pkg/readme.md" not in selected


def test_crawl_repo_yields_normalized_documents(tmp_path):
    class StubCrawler(GitHubDocsCrawler):
        def _iter_repositories(self):
            yield GitHubRepo(owner="nervosnetwork", name="fiber", default_branch="main")

        def _clone_repo(self, repo: GitHubRepo, dst: Path) -> None:
            # no-op for test
            return None

        def _git_output(self, repo_path: Path, *args: str, binary: bool = False):
            cmd = list(args)
            if cmd == ["rev-parse", "HEAD"]:
                return "abc123def4567890\n"
            if cmd == ["ls-tree", "-r", "--name-only", "HEAD"]:
                return "README.md\ndocs/guide.md\nsrc/main.rs\nCONTRIBUTING.md\n"
            if cmd[0:2] == ["cat-file", "-s"]:
                if "README.md" in cmd[2]:
                    return "32\n"
                if "docs/guide.md" in cmd[2]:
                    return "28\n"
                if "CONTRIBUTING.md" in cmd[2]:
                    return "21\n"
                return "0\n"
            if cmd[0] == "show":
                target = cmd[1].split("HEAD:", 1)[1]
                content = {
                    "README.md": "# Fiber\nPayment channel docs",
                    "docs/guide.md": "## Guide\nHow to open channel",
                    "CONTRIBUTING.md": "Please open PR with tests.",
                }[target]
                if binary:
                    return content.encode("utf-8")
                return content
            raise AssertionError(f"unexpected git args: {cmd}")

    crawler = StubCrawler(
        targets=["nervosnetwork/fiber"],
        clone_workspace=str(tmp_path),
    )
    docs = list(crawler.crawl())

    assert len(docs) == 3
    assert all(d.source == "github_docs" for d in docs)
    assert all(d.doc_type == "github_doc" for d in docs)
    assert all(d.topic == "nervosnetwork/fiber" for d in docs)
    assert all(d.url.startswith("https://github.com/nervosnetwork/fiber/blob/main/") for d in docs)
    assert all(d.anchor.startswith("doc:github-nervosnetwork-fiber#blob:") for d in docs)

