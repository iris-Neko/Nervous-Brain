"""Discord Bot runtime (token-based online receive/send layer)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from nervos_brain.logging_system import log_request_context
from nervos_brain.response_normalizer.platform_formatter import format_response_to_outbound

from .discord_bot_protocol_adapter import (
    discord_message_envelope_to_graph_state,
    discord_message_to_message_envelope,
    outbound_message_to_discord_requests,
)

logger = logging.getLogger(__name__)


class DiscordBotRuntimeError(RuntimeError):
    """Domain error for Discord Bot runtime."""


@dataclass(frozen=True)
class DiscordBotConfig:
    """Config for Discord Bot runtime."""

    bot_token: str
    mention_only_in_guild: bool = True

    @classmethod
    def from_env(cls) -> "DiscordBotConfig":
        token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
        if not token:
            raise DiscordBotRuntimeError("Missing DISCORD_BOT_TOKEN environment variable.")

        mention_raw = (os.getenv("DISCORD_MENTION_ONLY_IN_GUILD") or "true").strip().lower()
        mention_only = mention_raw not in {"0", "false", "no"}
        return cls(bot_token=token, mention_only_in_guild=mention_only)


GraphRunner = Callable[[dict[str, Any]], dict[str, Any]]


class DiscordGateway:
    """Gateway logic that converts Discord payload to graph call and send requests."""

    def __init__(
        self,
        *,
        graph_runner: GraphRunner,
        allowed_channel_ids: set[str] | None = None,
        allowed_guild_ids: set[str] | None = None,
        render_mode: str = "markdown",
        append_csat: bool = False,
        mention_only_in_guild: bool = True,
    ) -> None:
        self._graph_runner = graph_runner
        self._allowed_channel_ids = set(allowed_channel_ids or [])
        self._allowed_guild_ids = set(allowed_guild_ids or [])
        self._render_mode = "plain" if render_mode == "plain" else "markdown"
        self._append_csat = append_csat
        self._mention_only_in_guild = mention_only_in_guild

    def process_message_payload(
        self,
        payload: dict[str, Any],
        *,
        bot_user_id: str | None = None,
    ) -> dict[str, Any]:
        message_id = str(payload.get("id", "unknown"))
        channel_id = str(payload.get("channel_id", "")) if payload.get("channel_id") is not None else None
        guild_id = str(payload.get("guild_id", "")) if payload.get("guild_id") is not None else None

        author = payload.get("author")
        if not isinstance(author, dict):
            author = {}
        if bool(author.get("bot", False)):
            return {
                "message_id": message_id,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "ignored": True,
                "reason": "bot_sender",
                "request_id": None,
                "send_requests": [],
            }

        if self._allowed_guild_ids and guild_id and guild_id not in self._allowed_guild_ids:
            return {
                "message_id": message_id,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "ignored": True,
                "reason": "guild_not_allowed",
                "request_id": None,
                "send_requests": [],
            }
        if self._allowed_channel_ids and channel_id and channel_id not in self._allowed_channel_ids:
            return {
                "message_id": message_id,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "ignored": True,
                "reason": "channel_not_allowed",
                "request_id": None,
                "send_requests": [],
            }

        content = str(payload.get("content", "") or "")
        if guild_id and self._mention_only_in_guild:
            if not bot_user_id:
                return {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "guild_id": guild_id,
                    "ignored": True,
                    "reason": "missing_bot_user_id",
                    "request_id": None,
                    "send_requests": [],
                }
            if not _is_bot_mentioned(content, bot_user_id, payload):
                return {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "guild_id": guild_id,
                    "ignored": True,
                    "reason": "not_mentioned",
                    "request_id": None,
                    "send_requests": [],
                }
            content = _strip_bot_mention(content, bot_user_id)

        normalized_payload = dict(payload)
        normalized_payload["content"] = content

        try:
            envelope = discord_message_to_message_envelope(normalized_payload)
        except ValueError:
            return {
                "message_id": message_id,
                "channel_id": channel_id,
                "guild_id": guild_id,
                "ignored": True,
                "reason": "unsupported_message",
                "request_id": None,
                "send_requests": [],
            }

        state = discord_message_envelope_to_graph_state(envelope)
        state["render_mode"] = self._render_mode
        state["append_csat"] = self._append_csat
        request_id = str(state.get("request_id", "unknown"))

        with log_request_context(request_id):
            try:
                result = self._graph_runner(state)
            except Exception:
                logger.exception(
                    "discord graph_runner failed message_id=%s channel_id=%s guild_id=%s",
                    message_id,
                    channel_id,
                    guild_id,
                )
                result = {
                    "_final_response": {
                        "request_id": str(state.get("request_id", "unknown")),
                        "text": "处理请求时发生错误，请稍后重试。",
                        "citations": [],
                    }
                }

        outbound = _build_outbound_from_graph_result(
            result=result,
            state=state,
            envelope=envelope,
            render_mode=self._render_mode,
            append_csat=self._append_csat,
        )
        send_requests = outbound_message_to_discord_requests(outbound)
        logger.info(
            "discord processed message_id=%s channel_id=%s guild_id=%s request_id=%s segments=%d",
            message_id,
            channel_id,
            guild_id,
            request_id,
            len(send_requests),
        )
        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "ignored": False,
            "reason": "",
            "request_id": state.get("request_id"),
            "send_requests": send_requests,
        }


class DiscordBotRuntime:
    """Online Discord runtime backed by discord.py event loop."""

    def __init__(self, *, config: DiscordBotConfig, gateway: DiscordGateway) -> None:
        self._config = config
        self._gateway = gateway

    def run(self) -> None:
        try:
            import discord
        except ImportError as exc:
            raise DiscordBotRuntimeError(
                "Missing dependency: discord.py. Install with `pip install discord.py`."
            ) from exc

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guild_messages = True
        intents.dm_messages = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            logger.info(
                "Discord bot online: user=%s id=%s",
                client.user,
                client.user.id if client.user else "unknown",
            )

        @client.event
        async def on_message(message):
            if client.user is None:
                return

            payload = _discord_message_to_payload(message)
            row = self._gateway.process_message_payload(payload, bot_user_id=str(client.user.id))
            if row.get("ignored"):
                logger.info(
                    "[skip] message_id=%s channel_id=%s guild_id=%s reason=%s",
                    row.get("message_id"),
                    row.get("channel_id"),
                    row.get("guild_id"),
                    row.get("reason"),
                )
                return

            send_requests = row.get("send_requests", [])
            sent = await _send_discord_requests(message, send_requests)
            logger.info(
                "[ok] message_id=%s channel_id=%s guild_id=%s request_id=%s sent=%s",
                row.get("message_id"),
                row.get("channel_id"),
                row.get("guild_id"),
                row.get("request_id"),
                sent,
            )

        client.run(self._config.bot_token)


def _is_bot_mentioned(content: str, bot_user_id: str, payload: dict[str, Any]) -> bool:
    if re.search(rf"<@!?{re.escape(bot_user_id)}>", content):
        return True
    mention_ids = payload.get("mention_user_ids")
    if isinstance(mention_ids, list):
        return bot_user_id in {str(v) for v in mention_ids}
    return False


def _strip_bot_mention(content: str, bot_user_id: str) -> str:
    cleaned = re.sub(rf"<@!?{re.escape(bot_user_id)}>", "", content)
    return cleaned.strip()


def _discord_message_to_payload(message: Any) -> dict[str, Any]:
    channel_id = str(getattr(message.channel, "id", ""))
    guild_id = str(getattr(message.guild, "id", "")) if getattr(message, "guild", None) else None
    thread_id: str | None = None

    parent = getattr(message.channel, "parent", None)
    if parent is not None and getattr(parent, "id", None) is not None:
        # Thread channel: store parent as channel_id and thread id separately.
        thread_id = channel_id
        channel_id = str(parent.id)

    payload: dict[str, Any] = {
        "id": str(getattr(message, "id", "unknown")),
        "content": str(getattr(message, "content", "") or ""),
        "timestamp": getattr(message, "created_at", None).isoformat()
        if getattr(message, "created_at", None)
        else None,
        "author": {
            "id": str(getattr(message.author, "id", "unknown_user")),
            "bot": bool(getattr(message.author, "bot", False)),
            "locale": getattr(message.author, "locale", None),
        },
        "channel_id": channel_id,
        "guild_id": guild_id,
        "thread_id": thread_id,
        "mention_user_ids": [str(getattr(u, "id", "")) for u in getattr(message, "mentions", [])],
        "attachments": [
            {
                "id": str(getattr(a, "id", "")),
                "url": str(getattr(a, "url", "")),
                "filename": str(getattr(a, "filename", "")),
                "content_type": str(getattr(a, "content_type", "")),
            }
            for a in getattr(message, "attachments", [])
        ],
        "embeds": [
            {
                "url": str(getattr(e, "url", "")) if getattr(e, "url", None) else "",
                "title": str(getattr(e, "title", "")),
            }
            for e in getattr(message, "embeds", [])
        ],
    }

    reference = getattr(message, "reference", None)
    if reference is not None and getattr(reference, "message_id", None) is not None:
        payload["reference"] = {"message_id": str(reference.message_id)}

    return payload


def _build_outbound_from_graph_result(
    *,
    result: dict[str, Any],
    state: dict[str, Any],
    envelope: dict[str, Any],
    render_mode: str,
    append_csat: bool,
) -> dict[str, Any]:
    outbound = result.get("_outbound_message")
    if isinstance(outbound, dict) and isinstance(outbound.get("segments"), list):
        return outbound

    response = result.get("_final_response")
    if not isinstance(response, dict):
        response = {}
    response.setdefault("request_id", str(state.get("request_id", "unknown")))
    response.setdefault("text", "")
    response.setdefault("citations", [])

    return format_response_to_outbound(
        response=response,
        context=envelope["context"],  # type: ignore[arg-type]
        render_mode="plain" if render_mode == "plain" else "markdown",  # type: ignore[arg-type]
        append_csat=bool(append_csat),
        reply_to_message_id=str(envelope.get("reply_to_message_id") or "") or None,
    )


async def _send_discord_requests(message: Any, send_requests: list[dict[str, Any]]) -> int:
    sent = 0
    for req in send_requests:
        payload = req.get("payload")
        if not isinstance(payload, dict):
            continue
        content = str(payload.get("content", ""))
        reply_to = payload.get("reply_to_message_id")
        if reply_to:
            await message.reply(content)
        else:
            await message.channel.send(content)
        sent += 1
    return sent
