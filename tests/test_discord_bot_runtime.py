from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from nervos_brain.pathing import project_root

from nervos_brain.tool_runtime.discord_bot_runtime import (
    DiscordBotConfig,
    DiscordBotRuntime,
    DiscordBotRuntimeError,
    DiscordGateway,
)


def _load_script_module(name: str, filename: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sample_payload(
    *,
    message_id: str = "m-1",
    guild_id: str | None = "g-1",
    channel_id: str = "c-1",
    user_id: str = "u-1",
    text: str = "hello",
    is_bot: bool = False,
    mention_user_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "content": text,
        "timestamp": "2024-03-22T10:11:12Z",
        "author": {"id": user_id, "bot": is_bot},
        "guild_id": guild_id,
        "channel_id": channel_id,
        "mention_user_ids": mention_user_ids or [],
    }


def test_discord_bot_config_from_env_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with pytest.raises(DiscordBotRuntimeError, match="DISCORD_BOT_TOKEN"):
        DiscordBotConfig.from_env()


def test_discord_bot_config_from_env_parses_mention_flag(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "abc")
    monkeypatch.setenv("DISCORD_MENTION_ONLY_IN_GUILD", "false")
    cfg = DiscordBotConfig.from_env()
    assert cfg.bot_token == "abc"
    assert cfg.mention_only_in_guild is False


def test_discord_bot_runtime_clamps_worker_count():
    gateway = DiscordGateway(graph_runner=lambda state: {})
    runtime = DiscordBotRuntime(
        config=DiscordBotConfig(bot_token="token"),
        gateway=gateway,
        max_worker_threads=999,
    )

    assert runtime._max_worker_threads == 32


def test_gateway_ignores_bot_sender():
    gateway = DiscordGateway(graph_runner=lambda state: {})
    row = gateway.process_message_payload(_sample_payload(is_bot=True), bot_user_id="b-1")
    assert row["ignored"] is True
    assert row["reason"] == "bot_sender"


def test_gateway_guild_filter_blocks_not_allowed():
    gateway = DiscordGateway(
        graph_runner=lambda state: {},
        allowed_guild_ids={"g-allowed"},
    )
    row = gateway.process_message_payload(_sample_payload(guild_id="g-other"), bot_user_id="b-1")
    assert row["ignored"] is True
    assert row["reason"] == "guild_not_allowed"


def test_gateway_channel_filter_blocks_not_allowed():
    gateway = DiscordGateway(
        graph_runner=lambda state: {},
        allowed_channel_ids={"c-allowed"},
    )
    row = gateway.process_message_payload(_sample_payload(channel_id="c-other"), bot_user_id="b-1")
    assert row["ignored"] is True
    assert row["reason"] == "channel_not_allowed"


def test_gateway_guild_requires_bot_user_id_when_mention_only_enabled():
    gateway = DiscordGateway(graph_runner=lambda state: {}, mention_only_in_guild=True)
    row = gateway.process_message_payload(_sample_payload(guild_id="g-1", text="hello"), bot_user_id=None)
    assert row["ignored"] is True
    assert row["reason"] == "missing_bot_user_id"


def test_gateway_guild_mention_only_ignores_if_not_mentioned():
    gateway = DiscordGateway(graph_runner=lambda state: {}, mention_only_in_guild=True)
    row = gateway.process_message_payload(_sample_payload(guild_id="g-1", text="hello"), bot_user_id="999")
    assert row["ignored"] is True
    assert row["reason"] == "not_mentioned"


def test_gateway_processes_when_mentioned_and_strips_mention():
    captured: dict[str, Any] = {}

    def runner(state: dict[str, Any]) -> dict[str, Any]:
        captured.update(state)
        return {
            "_final_response": {
                "request_id": state["request_id"],
                "text": "ok",
                "citations": [],
            }
        }

    gateway = DiscordGateway(graph_runner=runner, mention_only_in_guild=True)
    row = gateway.process_message_payload(
        _sample_payload(
            guild_id="g-1",
            text="<@!999> 什么是 ckb",
            mention_user_ids=["999"],
        ),
        bot_user_id="999",
    )
    assert row["ignored"] is False
    assert row["request_id"].startswith("dc-")
    assert len(row["send_requests"]) == 1
    assert "force_retrieval" not in captured
    assert captured["user_message"]["content"] == "什么是 ckb"


def test_gateway_processes_dm_without_mention():
    gateway = DiscordGateway(
        graph_runner=lambda state: {"_final_response": {"request_id": state["request_id"], "text": "ok"}},
        mention_only_in_guild=True,
    )
    row = gateway.process_message_payload(_sample_payload(guild_id=None, text="hello"), bot_user_id=None)
    assert row["ignored"] is False
    assert len(row["send_requests"]) == 1


def test_gateway_runner_error_returns_fallback_message():
    def boom(_state: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    gateway = DiscordGateway(graph_runner=boom, mention_only_in_guild=False)
    row = gateway.process_message_payload(_sample_payload(guild_id=None, text="hello"), bot_user_id=None)
    assert row["ignored"] is False
    assert len(row["send_requests"]) == 1
    assert "处理请求时发生错误" in row["send_requests"][0]["payload"]["content"]


def test_gateway_splits_long_response():
    gateway = DiscordGateway(
        graph_runner=lambda state: {
            "_final_response": {
                "request_id": state["request_id"],
                "text": "A" * 4500,
                "citations": [],
            }
        },
        mention_only_in_guild=False,
    )
    row = gateway.process_message_payload(_sample_payload(guild_id=None, text="hello"), bot_user_id=None)
    assert row["ignored"] is False
    assert len(row["send_requests"]) >= 3


def test_build_runtime_defaults_to_dynamic_provider_registry(monkeypatch, tmp_path):
    from nervos_brain.graph_engine.provider_registry import ProviderCapabilityRegistry

    runner = _load_script_module("run_discord_bot_under_test", "run_discord_bot.py")

    class FakeRetriever:
        backend_names = ["fake"]

    monkeypatch.setattr(runner, "build_configured_retriever", lambda: FakeRetriever())

    runtime = runner._build_runtime(model="", memory_db=tmp_path / "memory.db")

    assert isinstance(runtime.provider_registry, ProviderCapabilityRegistry)
    assert runtime.provider_registry.get_profile_for("general", tier="router")["tier"] == "router"


def test_build_runtime_model_argument_forces_fixed_registry(monkeypatch, tmp_path):
    runner = _load_script_module("run_discord_bot_under_test_override", "run_discord_bot.py")

    class FakeRetriever:
        backend_names = ["fake"]

    monkeypatch.setattr(runner, "build_configured_retriever", lambda: FakeRetriever())

    runtime = runner._build_runtime(model="openai/gpt-5.5", memory_db=tmp_path / "memory.db")

    profile = runtime.provider_registry.get_profile_for("reflection", tier="low")
    assert profile["model"] == "openai/gpt-5.5"
    assert profile["reasoning_effort"] == ""


def test_discord_runner_config_loads_when_cwd_differs(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("discord_bot:\n  memory_db: data/discord_bot/memory.db\n", encoding="utf-8")
    monkeypatch.setenv("NERVOS_BRAIN_CONFIG", str(cfg_path))
    from nervos_brain import pathing
    pathing.config_path.cache_clear()
    pathing.load_project_config.cache_clear()
    runner = _load_script_module("run_discord_bot_pathing_config", "run_discord_bot.py")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    cfg = runner._load_project_cfg()

    assert cfg["discord_bot"]["memory_db"] == "data/discord_bot/memory.db"


def test_discord_runner_resolves_runtime_paths_to_project_root(monkeypatch, tmp_path):
    runner = _load_script_module("run_discord_bot_pathing_runtime", "run_discord_bot.py")
    monkeypatch.chdir(tmp_path)

    resolved = runner.resolve_project_path("data/discord_bot/memory.db")

    assert resolved == project_root() / "data" / "discord_bot" / "memory.db"
