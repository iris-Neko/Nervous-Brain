from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time
from pathlib import Path
from queue import Empty

from nervos_brain.retrieval import (
    ArchiveRecord,
    ArchiveStore,
    CompositeArchiveStore,
    CompositeRetriever,
    RetrievalBackend,
    RetrievalConfig,
)
from nervos_brain.tool_runtime import build_tool_call_request, handle_discourse_query, handle_github_search


_REPO_ROOT = Path(__file__).resolve().parents[1]
_VERSIONED_FORUM_ARCHIVE = _REPO_ROOT / "data" / "forum_talk" / "archive.db"


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


def _run_versioned_discourse_query(queue: mp.Queue, query: str) -> None:
    try:
        cfg = RetrievalConfig(archive_db=str(_VERSIONED_FORUM_ARCHIVE))
        forum = ArchiveStore(db_path=cfg.archive_db, config=cfg)
        req = build_tool_call_request(
            request_id="r-versioned-forum",
            step_id="s1",
            tool="discourse_query",
            args={
                "query": query,
                "category": "nervos talk",
                "top_k": 3,
                "_archive_store": CompositeArchiveStore([forum]),
            },
        )
        started = time.perf_counter()
        result = handle_discourse_query(req)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        queue.put(
            {
                "elapsed_ms": elapsed_ms,
                "anchors": [row["anchor"] for row in result.get("evidence", [])],
            }
        )
    except Exception as exc:  # pragma: no cover - surfaced through child payload
        queue.put({"error": f"{exc.__class__.__name__}: {exc}"})


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


def test_github_search_fallback_includes_github_code_archive(tmp_path):
    code_cfg = RetrievalConfig(archive_db=str(tmp_path / "code.db"))
    code = ArchiveStore(db_path=code_cfg.archive_db, config=code_cfg)
    code.upsert(
        ArchiveRecord(
            id="code-1",
            source="github_code",
            doc_type="github_code",
            url="https://github.com/nervosnetwork/fiber/blob/main/src/channel.rs",
            anchor="code:github-fiber-channel",
            title="nervosnetwork/fiber/src/channel.rs",
            summary="pub fn open_channel",
            keywords="github,code,nervosnetwork,fiber,open_channel,Channel",
            raw_text="pub struct Channel {}\npub fn open_channel() {}\n",
            raw_format="code",
            lang="rust",
            version="abc123",
            topic="nervosnetwork/fiber",
            content_hash="hash-code-1",
        )
    )

    req = build_tool_call_request(
        request_id="r-code",
        step_id="s1",
        tool="github_search",
        args={
            "query": "open_channel Channel",
            "repo": "nervosnetwork/fiber",
            "top_k": 3,
            "_archive_store": CompositeArchiveStore([code]),
        },
    )

    result = handle_github_search(req)

    assert [row["anchor"] for row in result["evidence"]] == ["code:github-fiber-channel"]
    assert result["evidence"][0]["payload"]["source"] == "github_code"
    assert result["evidence"][0]["payload"]["type"] == "github_code"


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


def test_discourse_fallback_retrieves_nervos_brain_progress_query(tmp_path):
    forum_cfg = RetrievalConfig(archive_db=str(tmp_path / "forum.db"))
    forum = ArchiveStore(db_path=forum_cfg.archive_db, config=forum_cfg)
    forum.upsert(
        _record(
            record_id="forum-nervos-brain-progress",
            source="nervos_talk",
            anchor="doc:nervos-talk-9995#post:26",
            title=(
                "Spark Program | Nervos Brain - A Global Developer Onboarding "
                "Engine and Cross-Language Hub Powered by Agentic RAG — reply #26"
            ),
            raw_text=(
                "第四周周报：Nervos Brain 当前开发进度围绕真实数据接入、"
                "真实模型回答、GitHub 多源文档抓取与入库、检索库规模化构建，"
                "已经形成数据抓取到入库再到检索和回答的端到端示例。"
            ),
            topic="nervos_talk:9995",
            url=(
                "https://talk.nervos.org/t/spark-program-nervos-brain-a-global-"
                "developer-onboarding-engine-and-cross-language-hub-powered-by-agentic-rag/9995/26"
            ),
        )
    )

    req = build_tool_call_request(
        request_id="r-progress",
        step_id="s1",
        tool="discourse_query",
        args={
            "query": "Nervos Brain 目前开发进度怎么样了",
            "category": "nervos talk",
            "top_k": 3,
            "_archive_store": CompositeArchiveStore([forum]),
        },
    )

    result = handle_discourse_query(req)

    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["anchor"] == "doc:nervos-talk-9995#post:26"
    assert "真实数据接入" in result["evidence"][0]["snippet"]


def test_versioned_forum_archive_contains_known_retrieval_fixtures():
    assert _VERSIONED_FORUM_ARCHIVE.is_file()

    with sqlite3.connect(_VERSIONED_FORUM_ARCHIVE) as con:
        count = con.execute("select count(*) from archive_records").fetchone()[0]
        nervos_brain_hits = con.execute(
            """
            select count(*)
            from archive_records
            where topic = 'nervos_talk:9995'
              and (title like '%Nervos Brain%' or raw_text like '%Nervos Brain%')
              and (raw_text like '%周报%' or raw_text like '%开发进度%')
            """
        ).fetchone()[0]
        game_hits = con.execute(
            """
            select count(*)
            from archive_records
            where source = 'nervos_talk'
              and (
                raw_text like '%GameFi%'
                or raw_text like '%Nervos.Land%'
                or raw_text like '%game%'
                or raw_text like '%游戏%'
              )
            """
        ).fetchone()[0]

    assert count > 0
    assert nervos_brain_hits > 0
    assert game_hits > 0


def test_versioned_forum_discourse_query_returns_before_tool_timeout():
    assert _VERSIONED_FORUM_ARCHIVE.is_file()

    ctx = mp.get_context("fork")
    queue = ctx.Queue()
    proc = ctx.Process(
        target=_run_versioned_discourse_query,
        args=(queue, "Nervos Brain 目前开发进度怎么样了"),
    )
    proc.start()
    proc.join(timeout=12)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        raise AssertionError(
            "versioned forum discourse_query exceeded 12s; online tool timeout is 10s"
        )

    try:
        payload = queue.get_nowait()
    except Empty as exc:
        raise AssertionError(f"versioned forum query process exited without result: {proc.exitcode}") from exc

    assert "error" not in payload
    assert payload["elapsed_ms"] < 10_000
    assert any("doc:nervos-talk-9995#post" in anchor for anchor in payload["anchors"])
