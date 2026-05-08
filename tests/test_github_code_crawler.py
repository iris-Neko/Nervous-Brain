from __future__ import annotations

from pathlib import Path

from nervos_brain.ingestion.github_code import GitHubCodeCrawler
from nervos_brain.ingestion.github_docs import GitHubRepo


def test_select_code_paths_filters_source_files(tmp_path):
    crawler = GitHubCodeCrawler(
        targets=["nervosnetwork/fiber"],
        clone_workspace=str(tmp_path),
        max_files_per_repo=None,
    )

    selected = crawler._select_code_paths(
        [
            "README.md",
            "src/main.rs",
            "crates/fiber-lib/src/channel.rs",
            "examples/open-channel.ts",
            "Cargo.toml",
            "spec/model.tla",
            "docker/dev.dockerfile",
            "node_modules/pkg/index.js",
            "target/debug/build.rs",
            "dist/app.min.js",
            "docs/guide.md",
        ]
    )

    assert "src/main.rs" in selected
    assert "crates/fiber-lib/src/channel.rs" in selected
    assert "examples/open-channel.ts" in selected
    assert "Cargo.toml" in selected
    assert "spec/model.tla" in selected
    assert "docker/dev.dockerfile" in selected
    assert "README.md" not in selected
    assert "node_modules/pkg/index.js" not in selected
    assert "target/debug/build.rs" not in selected
    assert "dist/app.min.js" not in selected
    assert "docs/guide.md" not in selected


def test_crawl_repo_yields_normalized_code_documents(tmp_path):
    class StubCrawler(GitHubCodeCrawler):
        def _iter_repositories(self):
            yield GitHubRepo(owner="nervosnetwork", name="fiber", default_branch="main")

        def _clone_repo(self, repo: GitHubRepo, dst: Path) -> None:
            return None

        def _git_output(self, repo_path: Path, *args: str, binary: bool = False):
            cmd = list(args)
            if cmd == ["rev-parse", "HEAD"]:
                return "abc123def4567890\n"
            if cmd == ["ls-tree", "-r", "--name-only", "HEAD"]:
                return "README.md\nsrc/channel.rs\nexamples/open-channel.ts\npackage.json\n"
            if cmd[0:2] == ["cat-file", "-s"]:
                return "128\n"
            if cmd[0] == "show":
                target = cmd[1].split("HEAD:", 1)[1]
                content = {
                    "src/channel.rs": "pub struct Channel {}\npub fn open_channel() {}\n",
                    "examples/open-channel.ts": "export async function openChannel() {}\n",
                    "package.json": '{"scripts":{"test":"vitest"}}',
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
    assert all(doc.source == "github_code" for doc in docs)
    assert all(doc.doc_type == "github_code" for doc in docs)
    assert all(doc.raw_format == "code" for doc in docs)
    assert all(doc.topic == "nervosnetwork/fiber" for doc in docs)
    assert all(doc.anchor.startswith("code:github-nervosnetwork-fiber#blob:") for doc in docs)
    assert {doc.lang for doc in docs} == {"rust", "typescript", "json"}
    assert any("open_channel" in doc.keywords for doc in docs)
    assert any("openChannel" in doc.keywords for doc in docs)
