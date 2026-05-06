#!/usr/bin/env python3
"""Product-level E2E demo (ingress -> full graph -> platform egress).

This demo runs the real full graph (retrieval + reflection + answer) and
simulates platform ingress/egress without needing Telegram/Discord bot tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

# Allow running from repo root without package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.graph_engine.full_graph import (  # noqa: E402
    FullGraphRuntime,
    build_full_graph,
    invoke_full_graph,
)
from nervos_brain.graph_engine.llm import get_model_name  # noqa: E402
from nervos_brain.logging_system import setup_logging  # noqa: E402
from nervos_brain.memory import (  # noqa: E402
    MemoryService,
    build_session_factory,
    init_memory_schema,
)
from nervos_brain.retrieval import (  # noqa: E402
    build_configured_retriever,
    load_retrieval_config,
)
from nervos_brain.tool_runtime.discord_bot_runtime import DiscordGateway  # noqa: E402
from nervos_brain.tool_runtime.telegram_bot_runtime import TelegramPollingGateway  # noqa: E402

logger = logging.getLogger("nervos_brain.product_e2e_demo")


class _FixedModelRegistry:
    """Pin all graph tasks to one model name."""

    def __init__(self, model: str) -> None:
        self._model = model

    def get_model_for(
        self,
        task_type: str,
        *,
        require_json: bool = False,
        max_cost: str = "high",
    ) -> str:
        _ = task_type, require_json, max_cost
        return self._model


class _CaptureTelegramAPI:
    """No-network fake Telegram API for gateway e2e simulation."""

    def __init__(self) -> None:
        self.sent_requests: list[dict[str, Any]] = []

    def send_requests(self, requests_payloads: list[dict[str, Any]]) -> int:
        self.sent_requests.extend(requests_payloads)
        return len(requests_payloads)


@dataclass
class _RunnerCapture:
    state: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


def _trim_graph_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    user_message = state.get("user_message", {})
    if not isinstance(user_message, dict):
        user_message = {}
    return {
        "request_id": str(state.get("request_id", "")),
        "route": str(state.get("route", "")),
        "locale": str(state.get("locale", "")),
        "budget": dict(state.get("budget", {})) if isinstance(state.get("budget"), dict) else {},
        "user_message": {
            "kind": str(user_message.get("kind", "")),
            "message_id": str(user_message.get("message_id", "")),
            "content": str(user_message.get("content", "")),
            "context": dict(user_message.get("context", {}))
            if isinstance(user_message.get("context"), dict)
            else {},
        },
    }


def _trim_evidence(evidence: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in evidence[:limit]:
        if not isinstance(ev, dict):
            continue
        out.append(
            {
                "id": str(ev.get("id", "")),
                "source": str(ev.get("source", "")),
                "title": str(ev.get("title", "")),
                "url": str(ev.get("url", "")),
                "anchor": str(ev.get("anchor", "")),
                "score": float(ev.get("score", 0.0))
                if isinstance(ev.get("score"), (int, float))
                else 0.0,
                "snippet": str(ev.get("snippet", ""))[:200],
            }
        )
    return out


def _trim_graph_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    final_response = result.get("_final_response", {})
    if not isinstance(final_response, dict):
        final_response = {}
    return {
        "request_id": str(result.get("request_id", "")),
        "retry_count": int(result.get("retry_count", 0) or 0),
        "hop_count": int(result.get("hop_count", 0) or 0),
        "reflection_stage": str(result.get("reflection_stage", "")),
        "reflection_decision": str(result.get("reflection_decision", "")),
        "reflection_reasoning": str(result.get("reflection_reasoning", "")),
        "uncertainty_score": result.get("uncertainty_score"),
        "reflection_round": int(result.get("reflection_round", 0) or 0),
        "evidence": _trim_evidence(
            result.get("evidence", []) if isinstance(result.get("evidence"), list) else []
        ),
        "conflicts": (
            result.get("conflicts", [])[:10]
            if isinstance(result.get("conflicts"), list)
            else []
        ),
        "_final_response": final_response,
    }


def _build_runtime(
    *,
    model: str,
    memory_db: Path,
    retrieval_section: str,
) -> FullGraphRuntime:
    cfg = load_retrieval_config(section=retrieval_section)
    if not Path(cfg.archive_db).exists():
        raise SystemExit(
            f"archive DB not found: {cfg.archive_db}. Run ingestion first."
        )

    retriever = build_configured_retriever(sections=[retrieval_section])

    memory_db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{memory_db}", future=True)
    init_memory_schema(engine)
    memory_service = MemoryService(build_session_factory(engine))

    return FullGraphRuntime(
        multi_retriever=retriever,
        memory_service=memory_service,
        provider_registry=_FixedModelRegistry(model),
        provider_max_cost="high",
    )


def _build_graph_runner(
    *,
    runtime: FullGraphRuntime,
    graph: Any,
    capture: _RunnerCapture,
):
    def _runner(state: dict[str, Any]) -> dict[str, Any]:
        result = invoke_full_graph(state, runtime=runtime, compiled_graph=graph)
        capture.state = state
        capture.result = result
        return result

    return _runner


def _run_telegram_demo(
    *,
    question: str,
    render_mode: str,
    append_csat: bool,
    graph_runner: Any,
    capture: _RunnerCapture,
) -> dict[str, Any]:
    api = _CaptureTelegramAPI()
    gateway = TelegramPollingGateway(
        api=api,  # type: ignore[arg-type]
        graph_runner=graph_runner,
        render_mode=render_mode,
        append_csat=append_csat,
    )
    update = {
        "update_id": int(time.time() * 1000) % 2_000_000_000,
        "message": {
            "message_id": 1,
            "date": int(time.time()),
            "text": question,
            "chat": {"id": -10099887766, "type": "supergroup"},
            "from": {"id": 6887614924, "is_bot": False, "language_code": "zh-CN"},
            "message_thread_id": 42,
        },
    }
    row = gateway.process_update(update, dry_run=False)
    return {
        "platform": "telegram",
        "ingress_payload": update,
        "gateway_row": row,
        "send_requests": api.sent_requests,
        "graph_state": _trim_graph_state(capture.state),
        "graph_result": _trim_graph_result(capture.result),
    }


def _run_discord_demo(
    *,
    question: str,
    render_mode: str,
    append_csat: bool,
    mention_only_in_guild: bool,
    graph_runner: Any,
    capture: _RunnerCapture,
) -> dict[str, Any]:
    bot_user_id = "999000111"
    content = f"<@!{bot_user_id}> {question}" if mention_only_in_guild else question
    payload = {
        "id": f"msg-{int(time.time() * 1000)}",
        "content": content,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "author": {"id": "6887614924", "bot": False, "locale": "zh-CN"},
        "guild_id": "demo-guild",
        "channel_id": "demo-channel",
        "thread_id": "demo-thread",
        "mention_user_ids": [bot_user_id] if mention_only_in_guild else [],
    }

    gateway = DiscordGateway(
        graph_runner=graph_runner,
        render_mode=render_mode,
        append_csat=append_csat,
        mention_only_in_guild=mention_only_in_guild,
    )
    row = gateway.process_message_payload(payload, bot_user_id=bot_user_id)
    return {
        "platform": "discord",
        "ingress_payload": payload,
        "gateway_row": row,
        "send_requests": row.get("send_requests", []),
        "graph_state": _trim_graph_state(capture.state),
        "graph_result": _trim_graph_result(capture.result),
    }


def _join_answer_text(send_requests: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for req in send_requests:
        payload = req.get("payload")
        if not isinstance(payload, dict):
            continue
        if "text" in payload:
            chunks.append(str(payload.get("text", "")))
            continue
        if "content" in payload:
            chunks.append(str(payload.get("content", "")))
    return "\n".join([c for c in chunks if c.strip()]).strip()


def _print_summary(row: dict[str, Any]) -> None:
    platform = row.get("platform", "unknown")
    gateway_row = row.get("gateway_row", {})
    graph_result = row.get("graph_result", {})
    send_requests = row.get("send_requests", [])
    final_response = (
        graph_result.get("_final_response", {})
        if isinstance(graph_result, dict)
        else {}
    )

    print(f"\n{'=' * 70}")
    print(f"Platform: {platform}")
    print(f"{'-' * 70}")
    print(json.dumps(gateway_row, ensure_ascii=False, indent=2))

    answer_text = _join_answer_text(send_requests if isinstance(send_requests, list) else [])
    if answer_text:
        print(f"\n[Outbound Answer]\n{answer_text}")

    evidence_count = len(graph_result.get("evidence", [])) if isinstance(graph_result, dict) else 0
    print(
        "\n[Graph Stats] "
        f"evidence={evidence_count}, "
        f"hop_count={graph_result.get('hop_count', 0)}, "
        f"retry_count={graph_result.get('retry_count', 0)}, "
        f"reflection={graph_result.get('reflection_stage', '-')}/"
        f"{graph_result.get('reflection_decision', '-')}, "
        f"uncertainty={graph_result.get('uncertainty_score', '-')}"
    )

    citations = final_response.get("citations", [])
    if isinstance(citations, list) and citations:
        print("\n[Citations]")
        for c in citations[:5]:
            if not isinstance(c, dict):
                continue
            print(f"{c.get('label', '?')} {c.get('title', '')} | {c.get('url', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a product-level E2E demo.")
    parser.add_argument(
        "--platform",
        choices=["telegram", "discord", "both"],
        default="both",
        help="Which platform ingress/egress path to demo.",
    )
    parser.add_argument(
        "--question",
        default="什么是 CKB 的 Cell Model？请简短说明并给引用。",
        help="Question to ask through platform payload.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model override. Defaults to llm.model in config/env.",
    )
    parser.add_argument(
        "--retrieval-section",
        default="retrieval",
        help="Config section for retrieval settings.",
    )
    parser.add_argument(
        "--memory-db",
        default="data/product_demo/memory.db",
        help="SQLite memory DB path for demo runs.",
    )
    parser.add_argument(
        "--render-mode",
        choices=["markdown", "plain"],
        default="markdown",
        help="Outbound render mode.",
    )
    parser.add_argument(
        "--append-csat",
        action="store_true",
        help="Append CSAT marker in outbound payload.",
    )
    parser.add_argument(
        "--discord-mention-only-in-guild",
        action="store_true",
        help="Require @bot mention for Discord guild messages.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full demo result as JSON.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug log level.",
    )
    args = parser.parse_args()

    setup_info = setup_logging(
        service_name="product_e2e_demo",
        debug=bool(args.debug),
    )

    model = args.model.strip() or get_model_name()
    runtime = _build_runtime(
        model=model,
        memory_db=Path(args.memory_db).expanduser().resolve(),
        retrieval_section=args.retrieval_section,
    )
    graph = build_full_graph()

    all_rows: list[dict[str, Any]] = []
    print(
        f"Product E2E demo starting: model={model} "
        f"platform={args.platform} retrieval_section={args.retrieval_section}"
    )
    logger.info(
        "product_e2e_demo start model=%s platform=%s retrieval_section=%s log_file=%s",
        model,
        args.platform,
        args.retrieval_section,
        setup_info.get("log_file", ""),
    )

    if args.platform in {"telegram", "both"}:
        cap = _RunnerCapture()
        runner = _build_graph_runner(runtime=runtime, graph=graph, capture=cap)
        all_rows.append(
            _run_telegram_demo(
                question=args.question,
                render_mode=args.render_mode,
                append_csat=bool(args.append_csat),
                graph_runner=runner,
                capture=cap,
            )
        )

    if args.platform in {"discord", "both"}:
        cap = _RunnerCapture()
        runner = _build_graph_runner(runtime=runtime, graph=graph, capture=cap)
        all_rows.append(
            _run_discord_demo(
                question=args.question,
                render_mode=args.render_mode,
                append_csat=bool(args.append_csat),
                mention_only_in_guild=bool(args.discord_mention_only_in_guild),
                graph_runner=runner,
                capture=cap,
            )
        )

    if args.json:
        print(json.dumps({"model": model, "runs": all_rows}, ensure_ascii=False, indent=2))
        return 0

    for row in all_rows:
        _print_summary(row)
    print(f"\n{'=' * 70}\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
