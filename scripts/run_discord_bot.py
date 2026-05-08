#!/usr/bin/env python3
"""Run Discord bot gateway."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.graph_engine.full_graph import (  # noqa: E402
    FullGraphRuntime,
    build_full_graph,
    invoke_full_graph,
)
from nervos_brain.graph_engine.provider_registry import ProviderCapabilityRegistry  # noqa: E402
from nervos_brain.memory import (  # noqa: E402
    MemoryService,
    build_session_factory,
    init_memory_schema,
)
from nervos_brain.logging_system import setup_logging  # noqa: E402
from nervos_brain.pathing import load_project_config, resolve_project_path  # noqa: E402
from nervos_brain.retrieval import (  # noqa: E402
    build_configured_retriever,
)
from nervos_brain.tool_runtime.discord_bot_runtime import (  # noqa: E402
    DiscordBotConfig,
    DiscordBotRuntime,
    DiscordBotRuntimeError,
    DiscordGateway,
)

logger = logging.getLogger("nervos_brain.discord_bot")


class _FixedModelRegistry:
    """Pin all graph tasks to one model name (from config or CLI)."""

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

    def get_profile_for(
        self,
        task_type: str,
        *,
        tier: str | None = None,
        require_json: bool = False,
        max_cost: str = "high",
    ) -> dict[str, Any]:
        _ = task_type, tier, require_json, max_cost
        return {
            "tier": "override",
            "model": self._model,
            "reasoning_effort": "",
            "verbosity": "",
            "max_tokens": 0,
        }


def _load_project_cfg() -> dict[str, Any]:
    try:
        return load_project_config()
    except Exception:
        logger.exception("Failed to load project config")
        return {}


def _load_discord_bot_cfg(project_cfg: dict[str, Any]) -> dict[str, Any]:
    section = project_cfg.get("discord_bot", {})
    if isinstance(section, dict):
        return section
    return {}


def _build_runtime(*, model: str, memory_db: Path) -> FullGraphRuntime:
    retriever = build_configured_retriever()
    backend_names = getattr(retriever, "backend_names", None)
    if backend_names:
        logger.info("Retrieval backends loaded: %s", backend_names)
    else:
        cfg = getattr(retriever, "_cfg", None)
        logger.info(
            "Retrieval backend loaded: %s",
            getattr(cfg, "collection_name", "retrieval"),
        )

    memory_db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{memory_db}", future=True)
    init_memory_schema(engine)
    memory_service = MemoryService(build_session_factory(engine))

    provider_registry = _FixedModelRegistry(model) if model else ProviderCapabilityRegistry()
    if model:
        logger.info("LLM model override enabled for all graph nodes: %s", model)
    else:
        profiles = getattr(provider_registry, "list_profiles", lambda: {})()
        logger.info("Dynamic LLM profiles loaded: %s", profiles)

    return FullGraphRuntime(
        multi_retriever=retriever,
        memory_service=memory_service,
        provider_registry=provider_registry,
        provider_max_cost="high",
    )


def _parse_id_set(values: list[str]) -> set[str]:
    out: set[str] = set()
    for raw in values:
        for part in raw.split(","):
            value = part.strip()
            if value:
                out.add(value)
    return out


def _cfg_bool(cfg: dict[str, Any], key: str, default: bool) -> bool:
    raw = str(cfg.get(key, default)).strip().lower()
    return raw not in {"0", "false", "no"}


def _cfg_int(cfg: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def main() -> int:
    project_cfg = _load_project_cfg()
    bot_cfg = _load_discord_bot_cfg(project_cfg)
    default_model = ""

    parser = argparse.ArgumentParser(description="Run Discord Bot gateway.")
    parser.add_argument(
        "--bot-token",
        default="",
        help="Discord bot token. Defaults to DISCORD_BOT_TOKEN env.",
    )
    parser.add_argument(
        "--memory-db",
        default=str(bot_cfg.get("memory_db", "data/discord_bot/memory.db")),
        help="SQLite path for memory service.",
    )
    parser.add_argument(
        "--model",
        default=default_model,
        help="Debug override for all full-graph tasks. Omit to use dynamic node-level router.",
    )
    parser.add_argument(
        "--allowed-channel-id",
        action="append",
        default=[],
        help="Allow only these channel IDs (repeatable, comma supported).",
    )
    parser.add_argument(
        "--allowed-guild-id",
        action="append",
        default=[],
        help="Allow only these guild IDs (repeatable, comma supported).",
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
        help="Whether to append CSAT marker in outbound messages.",
    )
    parser.add_argument(
        "--max-worker-threads",
        type=int,
        default=_cfg_int(bot_cfg, "max_worker_threads", 4),
        help="Concurrent Discord graph worker threads; same channel messages stay ordered.",
    )
    parser.add_argument(
        "--mention-only-in-guild",
        action="store_true",
        default=_cfg_bool(bot_cfg, "mention_only_in_guild", True),
        help="In guilds, only respond when bot is mentioned.",
    )
    parser.add_argument(
        "--no-mention-only-in-guild",
        action="store_false",
        dest="mention_only_in_guild",
        help="In guilds, respond even if not mentioned.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug log level.",
    )
    args = parser.parse_args()

    setup_info = setup_logging(
        service_name="discord_bot",
        debug=bool(args.debug),
    )

    token = args.bot_token.strip() or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        raise DiscordBotRuntimeError(
            "Missing Discord bot token. Set DISCORD_BOT_TOKEN or pass --bot-token."
        )

    model = str(args.model).strip()
    logger.info(
        "Discord bot starting: model_mode=%s mention_only_in_guild=%s render_mode=%s log_file=%s",
        model or "dynamic-router",
        bool(args.mention_only_in_guild),
        args.render_mode,
        setup_info.get("log_file", ""),
    )
    runtime = _build_runtime(
        model=model,
        memory_db=resolve_project_path(args.memory_db),
    )
    graph = build_full_graph()

    gateway = DiscordGateway(
        graph_runner=lambda state: invoke_full_graph(
            state,
            runtime=runtime,
            compiled_graph=graph,
        ),
        allowed_channel_ids=_parse_id_set(args.allowed_channel_id),
        allowed_guild_ids=_parse_id_set(args.allowed_guild_id),
        render_mode=args.render_mode,
        append_csat=args.append_csat,
        mention_only_in_guild=bool(args.mention_only_in_guild),
    )
    bot_runtime = DiscordBotRuntime(
        config=DiscordBotConfig(
            bot_token=token,
            mention_only_in_guild=bool(args.mention_only_in_guild),
        ),
        gateway=gateway,
        max_worker_threads=max(1, int(args.max_worker_threads)),
    )
    bot_runtime.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
