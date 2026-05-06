from __future__ import annotations

from nervos_brain.retrieval import (
    ArchiveRecord,
    ArchiveStore,
    CompositeArchiveStore,
    CompositeRetriever,
    RetrievalBackend,
    RetrievalConfig,
)
from nervos_brain.tool_runtime import build_tool_call_request, handle_discourse_query, handle_github_search


class _FakeRetriever:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict | None, int | None]] = []
        self.rebuilds = 0

    def search(self, query: str, filters=None, top_k=None):
        self.calls.append((query, filters, top_k))
        return self.rows[: top_k or len(self.rows)]

    def rebuild_bm25(self) -> int:
        self.rebuilds += 1
        return len(self.rows)


def _backend(name: str, rows: list[dict], tmp_path) -> RetrievalBackend:
    cfg = RetrievalConfig(
        collection_name=f"{name}_collection",
        archive_db=str(tmp_path / f"{name}.db"),
    )
    retriever = _FakeRetriever(rows)
    archive = ArchiveStore(config=cfg)
    return RetrievalBackend(
        name=name,
        config=cfg,
        qdrant_store=None,  # type: ignore[arg-type]
        archive_store=archive,
        retriever=retriever,  # type: ignore[arg-type]
    )


def _record(
    *,
    record_id: str,
    source: str,
    anchor: str,
    title: str,
    raw_text: str,
    topic: str,
    url: str = "https://example.com",
) -> ArchiveRecord:
    return ArchiveRecord(
        id=record_id,
        source=source,
        doc_type="forum_post" if source == "nervos_talk" else "github_doc",
        url=url,
        anchor=anchor,
        title=title,
        summary=raw_text[:80],
        keywords=title,
        raw_text=raw_text,
        raw_format="text",
        lang="en",
        version="latest",
        topic=topic,
        content_hash=f"hash-{record_id}",
    )


def test_composite_retriever_merges_backends_and_keeps_best_duplicate_score(tmp_path):
    docs = {
        "id": "doc:ccc",
        "source": "github_docs",
        "title": "CCC docs",
        "url": "https://github.com/nervosnetwork/docs",
        "anchor": "doc:ccc",
        "snippet": "CCC SDK docs",
        "score": 0.2,
        "payload": {"source": "github_docs"},
        "hash": "h1",
        "retrieved_ts_ms": 1,
    }
    forum = {
        "id": "forum:ccc",
        "source": "nervos_talk",
        "title": "CCC community thread",
        "url": "https://talk.nervos.org/t/ccc",
        "anchor": "forum:ccc",
        "snippet": "Community usage notes",
        "score": 0.9,
        "payload": {"source": "nervos_talk"},
        "hash": "h2",
        "retrieved_ts_ms": 2,
    }
    duplicate_better = {**docs, "score": 0.8, "snippet": "Better duplicate"}
    composite = CompositeRetriever(
        [
            _backend("docs", [docs], tmp_path),
            _backend("forum", [forum, duplicate_better], tmp_path),
        ],
    )

    results = composite.search("ccc", top_k=5)

    assert [row["anchor"] for row in results] == ["forum:ccc", "doc:ccc"]
    assert results[1]["snippet"] == "Better duplicate"
    assert results[0]["payload"]["backend"] == "forum"


def test_composite_retriever_rebuilds_all_backend_indexes(tmp_path):
    composite = CompositeRetriever(
        [
            _backend("docs", [{"anchor": "a", "score": 1.0}], tmp_path),
            _backend(
                "forum",
                [{"anchor": "b", "score": 1.0}, {"anchor": "c", "score": 1.0}],
                tmp_path,
            ),
        ],
    )
    assert composite.rebuild_bm25() == 3
    assert composite.backends[0].retriever.rebuilds == 1
    assert composite.backends[1].retriever.rebuilds == 1


def test_composite_archive_store_feeds_discourse_and_github_fallback(tmp_path):
    docs_cfg = RetrievalConfig(archive_db=str(tmp_path / "docs.db"))
    forum_cfg = RetrievalConfig(archive_db=str(tmp_path / "forum.db"))
    docs = ArchiveStore(db_path=docs_cfg.archive_db, config=docs_cfg)
    forum = ArchiveStore(db_path=forum_cfg.archive_db, config=forum_cfg)
    docs.upsert(
        _record(
            record_id="gh-1",
            source="github_docs",
            anchor="doc:github-ccc",
            title="CCC transfer docs",
            raw_text="CCC TypeScript transfer transaction example.",
            topic="ckb-devrel/ccc",
            url="https://github.com/ckb-devrel/ccc",
        )
    )
    forum.upsert(
        _record(
            record_id="forum-1",
            source="nervos_talk",
            anchor="doc:forum-game",
            title="Decentralized game on Nervos",
            raw_text="A forum discussion about building games with CKB assets.",
            topic="game",
            url="https://talk.nervos.org/t/game",
        )
    )
    composite_archive = CompositeArchiveStore([docs, forum])

    disc_req = build_tool_call_request(
        request_id="r1",
        step_id="s1",
        tool="discourse_query",
        args={"query": "building games", "category": "game", "_archive_store": composite_archive},
    )
    gh_req = build_tool_call_request(
        request_id="r1",
        step_id="s2",
        tool="github_search",
        args={"query": "transfer transaction", "repo": "ckb-devrel/ccc", "_archive_store": composite_archive},
    )

    disc_result = handle_discourse_query(disc_req)
    gh_result = handle_github_search(gh_req)

    assert [row["anchor"] for row in disc_result["evidence"]] == ["doc:forum-game"]
    assert [row["anchor"] for row in gh_result["evidence"]] == ["doc:github-ccc"]


def test_discourse_fallback_expands_chinese_game_query(tmp_path):
    forum_cfg = RetrievalConfig(archive_db=str(tmp_path / "forum.db"))
    forum = ArchiveStore(db_path=forum_cfg.archive_db, config=forum_cfg)
    forum.upsert(
        _record(
            record_id="forum-game-zh-query",
            source="nervos_talk",
            anchor="doc:forum-game-en",
            title="Nervos.Land - Online NFT Strategy Game Development",
            raw_text="Discussion about building an online NFT strategy game on Nervos and CKB.",
            topic="uncategorized",
            url="https://talk.nervos.org/t/dis-nervos-land-online-nft-strategy-game-development/7524",
        )
    )

    req = build_tool_call_request(
        request_id="r-game",
        step_id="s1",
        tool="discourse_query",
        args={
            "query": "找一下nervos talk上关于用ckb做游戏的讨论",
            "category": "nervos talk",
            "top_k": 3,
            "_archive_store": CompositeArchiveStore([forum]),
        },
    )

    result = handle_discourse_query(req)

    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["anchor"] == "doc:forum-game-en"
