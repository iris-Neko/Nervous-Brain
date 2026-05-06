#!/usr/bin/env python3
"""Interactive multi-turn product demo for Nervos Brain.

Features:
  - Real full-graph run (retrieval + reflection + answer)
  - Interactive multi-turn chat in terminal
  - Domain memory context switching (user/guild/channel/thread)
  - Optional manual fact upsert/list for memory demonstration

Run:
  python scripts/chat_cli_demo.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
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

logger = logging.getLogger("nervos_brain.chat_cli_demo")


def _ensure_utf8_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


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
    """No-network fake Telegram API for CLI demo."""

    def __init__(self) -> None:
        self.sent_requests: list[dict[str, Any]] = []

    def send_requests(self, requests_payloads: list[dict[str, Any]]) -> int:
        self.sent_requests.extend(requests_payloads)
        return len(requests_payloads)


@dataclass
class SessionContext:
    platform: str
    user_id: str
    guild_id: str
    channel_id: str
    thread_id: str


@dataclass
class GraphConfig:
    ask_user_uncertainty_threshold: float
    max_hops: int
    max_reflection_rounds_pre: int
    max_reflection_rounds_post: int
    force_retrieval: bool
    render_mode: str
    append_csat: bool


@dataclass
class TurnResult:
    request_id: str
    answer_text: str
    send_requests: list[dict[str, Any]]
    graph_result: dict[str, Any]
    gateway_row: dict[str, Any]


def _build_runtime(
    *,
    model: str,
    memory_db: Path,
    retrieval_section: str,
) -> tuple[FullGraphRuntime, MemoryService]:
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

    runtime = FullGraphRuntime(
        multi_retriever=retriever,
        memory_service=memory_service,
        provider_registry=_FixedModelRegistry(model),
        provider_max_cost="high",
    )
    return runtime, memory_service


def _join_payload_text(send_requests: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for req in send_requests:
        payload = req.get("payload")
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str):
            chunks.append(text)
            continue
        content = payload.get("content")
        if isinstance(content, str):
            chunks.append(content)
    return "\n".join([c for c in chunks if c.strip()]).strip()


class InteractiveChatDemo:
    def __init__(
        self,
        *,
        runtime: FullGraphRuntime,
        memory_service: MemoryService,
        graph_cfg: GraphConfig,
        context: SessionContext,
        discord_mention_only_in_guild: bool,
    ) -> None:
        self._runtime = runtime
        self._memory = memory_service
        self._graph_cfg = graph_cfg
        self._context = context
        self._discord_mention_only_in_guild = discord_mention_only_in_guild
        self._graph = build_full_graph()

        self._telegram_api: _CaptureTelegramAPI | None = None
        self._telegram_gateway: TelegramPollingGateway | None = None
        self._discord_gateway: DiscordGateway | None = None
        self._bot_user_id = "999000111"
        self._last_user_event_id: str | None = None

        if self._context.platform == "telegram":
            self._telegram_api = _CaptureTelegramAPI()
            self._telegram_gateway = TelegramPollingGateway(
                api=self._telegram_api,  # type: ignore[arg-type]
                graph_runner=self._run_graph,
                render_mode=self._graph_cfg.render_mode,
                append_csat=self._graph_cfg.append_csat,
            )
        else:
            self._discord_gateway = DiscordGateway(
                graph_runner=self._run_graph,
                render_mode=self._graph_cfg.render_mode,
                append_csat=self._graph_cfg.append_csat,
                mention_only_in_guild=self._discord_mention_only_in_guild,
            )

        self._capture_state: dict[str, Any] | None = None
        self._capture_result: dict[str, Any] | None = None
        self._counter = 0

    @property
    def context(self) -> SessionContext:
        return self._context

    def _run_graph(self, state: dict[str, Any]) -> dict[str, Any]:
        budget = state.setdefault("budget", {})
        if not isinstance(budget, dict):
            budget = {}
            state["budget"] = budget
        budget["ask_user_uncertainty_threshold"] = self._graph_cfg.ask_user_uncertainty_threshold
        budget["max_hops"] = self._graph_cfg.max_hops
        budget["max_reflection_rounds_pre"] = self._graph_cfg.max_reflection_rounds_pre
        budget["max_reflection_rounds_post"] = self._graph_cfg.max_reflection_rounds_post
        state["force_retrieval"] = self._graph_cfg.force_retrieval
        state["render_mode"] = self._graph_cfg.render_mode
        state["append_csat"] = self._graph_cfg.append_csat

        result = invoke_full_graph(state, runtime=self._runtime, compiled_graph=self._graph)
        self._capture_state = state
        self._capture_result = result
        return result

    def _write_message_event(self, *, role: str, content: str) -> str | None:
        try:
            return self._memory.write_message_event(
                platform=self._context.platform,
                user_id=self._context.user_id,
                guild_id=self._context.guild_id,
                channel_id=self._context.channel_id,
                thread_id=self._context.thread_id,
                role=role,
                content=content,
            )
        except Exception:
            return None

    def chat_turn(self, text: str) -> TurnResult:
        self._counter += 1
        self._capture_state = None
        self._capture_result = None
        self._last_user_event_id = self._write_message_event(role="user", content=text)

        if self._context.platform == "telegram":
            if self._telegram_api is None or self._telegram_gateway is None:
                raise RuntimeError("telegram gateway not initialized")
            before = len(self._telegram_api.sent_requests)
            update = {
                "update_id": int(time.time() * 1000) % 2_000_000_000,
                "message": {
                    "message_id": self._counter,
                    "date": int(time.time()),
                    "text": text,
                    "chat": {"id": int(self._context.channel_id), "type": "supergroup"},
                    "from": {
                        "id": int(self._context.user_id),
                        "is_bot": False,
                        "language_code": "zh-CN",
                    },
                    "message_thread_id": int(self._context.thread_id),
                },
            }
            row = self._telegram_gateway.process_update(update, dry_run=False)
            send_requests = self._telegram_api.sent_requests[before:]
        else:
            if self._discord_gateway is None:
                raise RuntimeError("discord gateway not initialized")
            content = text
            mention_ids: list[str] = []
            if self._discord_mention_only_in_guild:
                content = f"<@!{self._bot_user_id}> {text}"
                mention_ids = [self._bot_user_id]
            payload = {
                "id": f"cli-msg-{int(time.time() * 1000)}",
                "content": content,
                "timestamp": str(int(time.time())),
                "author": {"id": self._context.user_id, "bot": False, "locale": "zh-CN"},
                "guild_id": self._context.guild_id,
                "channel_id": self._context.channel_id,
                "thread_id": self._context.thread_id,
                "mention_user_ids": mention_ids,
            }
            row = self._discord_gateway.process_message_payload(
                payload, bot_user_id=self._bot_user_id
            )
            send_requests = row.get("send_requests", [])
            if not isinstance(send_requests, list):
                send_requests = []

        graph_result = self._capture_result if isinstance(self._capture_result, dict) else {}
        final_response = graph_result.get("_final_response", {})
        if not isinstance(final_response, dict):
            final_response = {}

        # Prefer raw graph answer text for CLI readability.
        # (Telegram MarkdownV2 payload text is escaped and looks noisy in terminal.)
        answer = str(final_response.get("text", "") or "").strip()
        if not answer:
            answer = _join_payload_text(send_requests)
        if answer:
            self._write_message_event(role="assistant", content=answer)

        request_id = str(row.get("request_id", ""))
        return TurnResult(
            request_id=request_id,
            answer_text=answer,
            send_requests=send_requests,
            graph_result=graph_result,
            gateway_row=row if isinstance(row, dict) else {},
        )

    def set_context(self, *, key: str, value: str) -> None:
        if key == "user":
            self._context.user_id = value
            return
        if key == "guild":
            self._context.guild_id = value
            return
        if key == "channel":
            self._context.channel_id = value
            return
        if key == "thread":
            self._context.thread_id = value
            return
        raise ValueError(f"unsupported context key: {key}")

    def upsert_fact(self, *, namespace: str, fact_key: str, fact_value: str) -> str:
        source_events = [self._last_user_event_id] if self._last_user_event_id else []
        if namespace == "user":
            return self._memory.upsert_user_fact(
                key={
                    "platform": self._context.platform,
                    "user_id": self._context.user_id,
                },
                fact_key=fact_key,
                fact_value=fact_value,
                confidence=0.95,
                source_event_ids=source_events,
            )
        if namespace == "channel":
            return self._memory.upsert_channel_fact(
                key={
                    "platform": self._context.platform,
                    "guild_id": self._context.guild_id,
                    "channel_id": self._context.channel_id,
                },
                fact_key=fact_key,
                fact_value=fact_value,
                confidence=0.95,
                source_event_ids=source_events,
            )
        raise ValueError("namespace must be `user` or `channel`")

    def list_facts(self) -> dict[str, list[dict[str, Any]]]:
        user_facts = self._memory.list_user_facts(
            key={"platform": self._context.platform, "user_id": self._context.user_id}
        )
        channel_facts = self._memory.list_channel_facts(
            key={
                "platform": self._context.platform,
                "guild_id": self._context.guild_id,
                "channel_id": self._context.channel_id,
            }
        )
        return {"user": user_facts, "channel": channel_facts}


def _parse_switch_cmd(line: str) -> tuple[str, str]:
    parts = line.strip().split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError("usage: /switch <user|guild|channel|thread> <value>")
    key = parts[1].strip().lower()
    value = parts[2].strip()
    if key not in {"user", "guild", "channel", "thread"}:
        raise ValueError("key must be one of user/guild/channel/thread")
    if not value:
        raise ValueError("value cannot be empty")
    return key, value


def _parse_fact_cmd(line: str) -> tuple[str, str, str]:
    parts = line.strip().split(maxsplit=3)
    if len(parts) != 4:
        raise ValueError("usage: /fact <user|channel> <key> <value>")
    namespace = parts[1].strip().lower()
    fact_key = parts[2].strip()
    fact_value = parts[3].strip()
    if namespace not in {"user", "channel"}:
        raise ValueError("namespace must be user or channel")
    if not fact_key:
        raise ValueError("fact key cannot be empty")
    if not fact_value:
        raise ValueError("fact value cannot be empty")
    return namespace, fact_key, fact_value


def _print_help() -> None:
    print("Commands:")
    print("  /help                               show this help")
    print("  /exit | /quit                       exit")
    print("  /ctx                                print current context")
    print("  /switch <k> <v>                     switch context key (user/guild/channel/thread)")
    print("  /fact <user|channel> <k> <v>        upsert one memory fact")
    print("  /facts                              list user/channel facts")
    print("  /raw                                print last gateway + graph result json")


def _print_context(ctx: SessionContext) -> None:
    print(
        f"context: platform={ctx.platform} user={ctx.user_id} guild={ctx.guild_id} "
        f"channel={ctx.channel_id} thread={ctx.thread_id}"
    )


def _safe_int_str(value: str, fallback: str) -> str:
    text = value.strip()
    if text and text.lstrip("-").isdigit():
        return text
    return fallback


def main() -> int:
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Interactive multi-turn Nervos Brain CLI demo.")
    parser.add_argument(
        "--platform",
        choices=["telegram", "discord"],
        default="telegram",
        help="Ingress/egress adapter to simulate.",
    )
    parser.add_argument("--model", default="", help="Model override.")
    parser.add_argument("--retrieval-section", default="retrieval", help="Retrieval config section.")
    parser.add_argument("--memory-db", default="data/product_demo/chat_memory.db", help="Memory DB path.")
    parser.add_argument("--user-id", default="6887614924", help="Demo user id.")
    parser.add_argument("--guild-id", default="-10099887766", help="Demo guild id.")
    parser.add_argument("--channel-id", default="-10099887766", help="Demo channel id.")
    parser.add_argument("--thread-id", default="42", help="Demo thread id.")
    parser.add_argument(
        "--discord-mention-only-in-guild",
        action="store_true",
        help="Require @bot mention in Discord guild mode.",
    )
    parser.add_argument(
        "--ask-user-threshold",
        type=float,
        default=0.95,
        help="uncertainty threshold to trigger ask_user (higher = less ask_user)",
    )
    parser.add_argument("--max-hops", type=int, default=4, help="max retrieval hops per turn.")
    parser.add_argument("--max-reflection-pre", type=int, default=4, help="max pre-answer reflection rounds.")
    parser.add_argument("--max-reflection-post", type=int, default=2, help="max post-answer reflection rounds.")
    parser.add_argument(
        "--render-mode",
        choices=["markdown", "plain"],
        default="markdown",
        help="Outbound render mode.",
    )
    parser.add_argument("--append-csat", action="store_true", help="Append CSAT marker.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug log level.",
    )
    args = parser.parse_args()

    setup_info = setup_logging(
        service_name="chat_cli_demo",
        debug=bool(args.debug),
    )

    model = args.model.strip() or get_model_name()
    runtime, memory = _build_runtime(
        model=model,
        memory_db=Path(args.memory_db).expanduser().resolve(),
        retrieval_section=args.retrieval_section,
    )
    graph_cfg = GraphConfig(
        ask_user_uncertainty_threshold=max(0.0, min(1.0, float(args.ask_user_threshold))),
        max_hops=max(1, int(args.max_hops)),
        max_reflection_rounds_pre=max(1, int(args.max_reflection_pre)),
        max_reflection_rounds_post=max(1, int(args.max_reflection_post)),
        force_retrieval=True,
        render_mode=args.render_mode,
        append_csat=bool(args.append_csat),
    )
    ctx = SessionContext(
        platform=args.platform,
        user_id=_safe_int_str(args.user_id, "6887614924") if args.platform == "telegram" else args.user_id,
        guild_id=_safe_int_str(args.guild_id, "-10099887766") if args.platform == "telegram" else args.guild_id,
        channel_id=_safe_int_str(args.channel_id, "-10099887766")
        if args.platform == "telegram"
        else args.channel_id,
        thread_id=_safe_int_str(args.thread_id, "42") if args.platform == "telegram" else args.thread_id,
    )
    demo = InteractiveChatDemo(
        runtime=runtime,
        memory_service=memory,
        graph_cfg=graph_cfg,
        context=ctx,
        discord_mention_only_in_guild=bool(args.discord_mention_only_in_guild),
    )

    print("Nervos Brain Interactive Demo")
    print(
        f"model={model} platform={args.platform} retrieval_section={args.retrieval_section} "
        f"memory_db={args.memory_db}"
    )
    logger.info(
        "chat_cli_demo start model=%s platform=%s memory_db=%s log_file=%s",
        model,
        args.platform,
        args.memory_db,
        setup_info.get("log_file", ""),
    )
    _print_context(demo.context)
    _print_help()

    last_gateway_row: dict[str, Any] = {}
    last_graph_result: dict[str, Any] = {}
    while True:
        try:
            line = input("\nYou> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExit.")
            logger.info("chat_cli_demo exit by keyboard interrupt/eof")
            break

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            print("Exit.")
            logger.info("chat_cli_demo exit by command")
            break
        if line == "/help":
            _print_help()
            continue
        if line == "/ctx":
            _print_context(demo.context)
            continue
        if line.startswith("/switch "):
            try:
                key, value = _parse_switch_cmd(line)
                demo.set_context(key=key, value=value)
                _print_context(demo.context)
            except ValueError as exc:
                print(f"[error] {exc}")
            continue
        if line.startswith("/fact "):
            try:
                namespace, fact_key, fact_value = _parse_fact_cmd(line)
                fact_id = demo.upsert_fact(
                    namespace=namespace, fact_key=fact_key, fact_value=fact_value
                )
                print(f"[ok] upsert fact: namespace={namespace} id={fact_id}")
            except Exception as exc:
                print(f"[error] {exc}")
            continue
        if line == "/facts":
            facts = demo.list_facts()
            print("[user facts]")
            for fact in facts.get("user", [])[:20]:
                print(f"  - {fact.get('key')}={fact.get('value')} (conf={fact.get('confidence')})")
            print("[channel facts]")
            for fact in facts.get("channel", [])[:20]:
                print(f"  - {fact.get('key')}={fact.get('value')} (conf={fact.get('confidence')})")
            continue
        if line == "/raw":
            print(
                json.dumps(
                    {"gateway_row": last_gateway_row, "graph_result": last_graph_result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            continue

        try:
            turn = demo.chat_turn(line)
        except Exception as exc:
            print(f"[error] turn failed: {exc}")
            logger.exception("chat turn failed: %s", exc)
            continue

        last_gateway_row = turn.gateway_row
        last_graph_result = turn.graph_result
        print(f"NB> {turn.answer_text or '(empty response)'}")

        evidence_count = len(turn.graph_result.get("evidence", []))
        reflection_stage = str(turn.graph_result.get("reflection_stage", "-"))
        reflection_decision = str(turn.graph_result.get("reflection_decision", "-"))
        uncertainty = turn.graph_result.get("uncertainty_score", "-")
        print(
            "[meta] "
            f"request_id={turn.request_id} evidence={evidence_count} "
            f"reflection={reflection_stage}/{reflection_decision} uncertainty={uncertainty}"
        )
        logger.debug(
            "turn done request_id=%s evidence=%d reflection=%s/%s uncertainty=%s",
            turn.request_id,
            evidence_count,
            reflection_stage,
            reflection_decision,
            uncertainty,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
