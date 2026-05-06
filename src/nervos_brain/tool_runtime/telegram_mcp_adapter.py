"""Telegram MCP adapter (based on dryeab/mcp-telegram, MIT).

Upstream reference:
  - Repo: https://github.com/dryeab/mcp-telegram
  - Commit: 653a1ed43c4927b28b4060e6759d3c4009d5ea00

This module is a refactored subset for Nervos Brain:
  - Removes global singleton state.
  - Adds explicit config validation and clearer runtime errors.
  - Keeps dependencies optional via lazy imports.
  - Exposes a compact FastMCP server builder.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from telethon import TelegramClient

from .telegram_bot_protocol_adapter import (
    message_envelope_to_graph_state,
    outbound_message_to_telegram_requests,
    telegram_update_to_message_envelope,
)

_TELEGRAM_MESSAGE_URL = re.compile(
    r"^(?:https?://)?t(?:elegram)?\.me/"
    r"(?:(?P<username>[A-Za-z0-9_]+)/(?P<message_id>\d+)"
    r"|c/(?P<chat_id>\d+)/(?P<chat_message_id>\d+))/?$"
)


class TelegramMCPError(RuntimeError):
    """Domain error for Telegram MCP adapter."""


def parse_entity(entity: str) -> int | str:
    """Return int for numeric entity IDs, else raw string (username/phone/me)."""
    value = entity.strip()
    return int(value) if value.lstrip("-").isdigit() else value


def parse_telegram_message_url(url: str) -> tuple[int | str, int] | None:
    """Parse a Telegram message URL to (entity, message_id)."""
    match = _TELEGRAM_MESSAGE_URL.match(url.strip())
    if not match:
        return None

    groups = match.groupdict()
    entity = groups.get("username") or groups.get("chat_id")
    message_id = groups.get("message_id") or groups.get("chat_message_id")
    if not entity or not message_id:
        return None
    return parse_entity(entity), int(message_id)


@dataclass(frozen=True)
class TelegramMCPConfig:
    """Runtime config for Telethon-backed MCP server."""

    api_id: int
    api_hash: str
    state_dir: Path = Path.home() / ".local" / "state" / "nervos-brain-telegram-mcp"

    @classmethod
    def from_env(cls) -> "TelegramMCPConfig":
        api_id_raw = (
            os.getenv("TELEGRAM_API_ID")
            or os.getenv("API_ID")
            or ""
        ).strip()
        api_hash = (
            os.getenv("TELEGRAM_API_HASH")
            or os.getenv("API_HASH")
            or ""
        ).strip()

        if not api_id_raw or not api_hash:
            raise TelegramMCPError(
                "Missing TELEGRAM_API_ID/TELEGRAM_API_HASH environment variables."
            )

        try:
            api_id = int(api_id_raw)
        except ValueError as exc:
            raise TelegramMCPError("TELEGRAM_API_ID must be an integer.") from exc

        return cls(api_id=api_id, api_hash=api_hash)


@dataclass(frozen=True)
class DialogInfo:
    id: int
    title: str
    username: str | None
    phone_number: str | None
    kind: str


@dataclass(frozen=True)
class MessageInfo:
    message_id: int
    sender_id: int | None
    text: str | None
    outgoing: bool
    date_iso: str | None
    reply_to: int | None


class TelegramMCPClient:
    """Thin, explicit wrapper around Telethon for MCP tools."""

    def __init__(self, config: TelegramMCPConfig) -> None:
        self._cfg = config
        self._cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self._session_path = self._cfg.state_dir / "session"
        self._client: TelegramClient | None = None

    @property
    def session_path(self) -> Path:
        return self._session_path

    @property
    def client(self) -> "TelegramClient":
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @staticmethod
    def _import_telethon():
        try:
            from telethon import TelegramClient, hints, types  # type: ignore
            from telethon.tl import custom, functions, patched  # type: ignore
        except ImportError as exc:
            raise TelegramMCPError(
                "Missing dependency: telethon. "
                "Install with `pip install telethon`."
            ) from exc
        return TelegramClient, hints, types, custom, functions, patched

    def _build_client(self) -> "TelegramClient":
        TelegramClient, *_ = self._import_telethon()
        return TelegramClient(
            session=self._session_path,
            api_id=self._cfg.api_id,
            api_hash=self._cfg.api_hash,
        )

    async def connect(self) -> None:
        if not self.client.is_connected():
            await self.client.connect()

    async def disconnect(self) -> None:
        if self._client is not None and self._client.is_connected():
            await self._client.disconnect()

    async def login_interactive(self, phone: str) -> None:
        await self.connect()
        try:
            await self.client.start(phone=phone)  # type: ignore[arg-type]
        finally:
            await self.disconnect()

    async def send_message(
        self,
        entity: int | str,
        message: str,
        *,
        file_paths: list[str] | None = None,
        reply_to: int | None = None,
    ) -> None:
        paths = file_paths or []
        for file_path in paths:
            p = Path(file_path)
            if not p.exists() or not p.is_file():
                raise TelegramMCPError(f"Invalid file path: {file_path}")

        await self.client.send_message(
            entity,
            message,
            file=paths or None,  # type: ignore[arg-type]
            reply_to=reply_to,  # type: ignore[arg-type]
        )

    async def search_dialogs(self, query: str, limit: int = 10) -> list[DialogInfo]:
        if not query.strip():
            raise TelegramMCPError("query cannot be empty")
        if limit <= 0:
            raise TelegramMCPError("limit must be > 0")

        _, hints, types, _, functions, _ = self._import_telethon()
        response: Any = await self.client(
            functions.contacts.SearchRequest(q=query, limit=limit)
        )
        if not isinstance(response, types.contacts.Found):
            return []

        dialogs: list[DialogInfo] = []
        for item in list(response.users) + list(response.chats):
            if not isinstance(item, hints.Entity):
                continue

            if isinstance(item, types.User):
                kind = "bot" if bool(item.bot) else "user"
                username = item.username if isinstance(item.username, str) else None
                phone = item.phone if isinstance(item.phone, str) else None
            elif isinstance(item, types.Chat):
                kind = "group"
                username = None
                phone = None
            else:
                kind = "group" if bool(getattr(item, "megagroup", False)) else "channel"
                username = item.username if isinstance(item.username, str) else None
                phone = None

            dialogs.append(
                DialogInfo(
                    id=await self.client.get_peer_id(item),
                    title=self._display_name(item),
                    username=username,
                    phone_number=phone,
                    kind=kind,
                )
            )

        return dialogs[:limit]

    async def get_messages(
        self,
        entity: int | str,
        *,
        limit: int = 20,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[MessageInfo]:
        if limit <= 0:
            raise TelegramMCPError("limit must be > 0")

        _, _, types, _, _, patched = self._import_telethon()
        end = end_date or datetime.now(timezone.utc)
        start = start_date or datetime(1970, 1, 1, tzinfo=timezone.utc)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        collected: list[MessageInfo] = []
        async for msg in self.client.iter_messages(entity, offset_date=end):  # type: ignore[arg-type]
            if not isinstance(msg, patched.Message):
                continue
            if isinstance(msg, patched.MessageService | patched.MessageEmpty):
                continue
            if msg.date is None or msg.date < start:
                break
            if len(collected) >= limit:
                break

            reply_to: int | None = None
            if msg.reply_to and isinstance(msg.reply_to, types.MessageReplyHeader):
                reply_to = int(msg.reply_to.reply_to_msg_id or 0) or None

            sender_id: int | None = None
            if msg.from_id is not None:
                sender_id = int(await self.client.get_peer_id(msg.from_id))

            collected.append(
                MessageInfo(
                    message_id=msg.id,
                    sender_id=sender_id,
                    text=msg.text if isinstance(msg.text, str) else None,
                    outgoing=bool(msg.out),
                    date_iso=msg.date.isoformat() if msg.date else None,
                    reply_to=reply_to,
                )
            )

        return collected

    async def message_from_link(self, url: str) -> MessageInfo:
        parsed = parse_telegram_message_url(url)
        if parsed is None:
            raise TelegramMCPError(f"Invalid Telegram message URL: {url}")
        entity, message_id = parsed

        _, _, _, _, _, patched = self._import_telethon()
        msg = await self.client.get_messages(entity, ids=message_id)  # type: ignore[arg-type]
        if not isinstance(msg, patched.Message):
            raise TelegramMCPError(f"Message not found: {url}")

        sender_id: int | None = None
        if msg.from_id is not None:
            sender_id = int(await self.client.get_peer_id(msg.from_id))

        return MessageInfo(
            message_id=msg.id,
            sender_id=sender_id,
            text=msg.text if isinstance(msg.text, str) else None,
            outgoing=bool(msg.out),
            date_iso=msg.date.isoformat() if msg.date else None,
            reply_to=None,
        )

    @staticmethod
    def _display_name(entity: Any) -> str:
        first = str(getattr(entity, "first_name", "") or "").strip()
        last = str(getattr(entity, "last_name", "") or "").strip()
        title = str(getattr(entity, "title", "") or "").strip()
        if title:
            return title
        if first or last:
            return (first + " " + last).strip()
        username = str(getattr(entity, "username", "") or "").strip()
        return username or str(getattr(entity, "id", "unknown"))


def build_fastmcp_server(client: TelegramMCPClient):
    """Build a FastMCP server bound to TelegramMCPClient."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise TelegramMCPError(
            "Missing dependency: mcp. Install with `pip install \"mcp[cli]\"`."
        ) from exc

    @asynccontextmanager
    async def lifespan(_: Any) -> "AsyncIterator[None]":
        await client.connect()
        try:
            yield
        finally:
            await client.disconnect()

    mcp = FastMCP("nervos-telegram-mcp", lifespan=lifespan)

    @mcp.tool()
    async def tg_send_message(
        entity: str,
        message: str,
        file_paths: list[str] | None = None,
        reply_to: int | None = None,
    ) -> str:
        await client.send_message(
            parse_entity(entity),
            message,
            file_paths=file_paths,
            reply_to=reply_to,
        )
        return f"sent message to {entity}"

    @mcp.tool()
    async def tg_search_dialogs(query: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = await client.search_dialogs(query=query, limit=limit)
        return [asdict(row) for row in rows]

    @mcp.tool()
    async def tg_get_messages(entity: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await client.get_messages(parse_entity(entity), limit=limit)
        return [asdict(row) for row in rows]

    @mcp.tool()
    async def tg_message_from_link(url: str) -> dict[str, Any]:
        row = await client.message_from_link(url)
        return asdict(row)

    return mcp


def build_fastmcp_server_lite():
    """Build a lite FastMCP server with pure protocol tools only.

    This server does not require Telegram credentials or Telethon.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise TelegramMCPError(
            "Missing dependency: mcp. Install with `pip install \"mcp[cli]\"`."
        ) from exc

    mcp = FastMCP("nervos-telegram-mcp-lite")

    @mcp.tool()
    async def tg_parse_update_to_envelope(update: dict[str, Any]) -> dict[str, Any]:
        """Convert Telegram Bot API update payload into MessageEnvelope."""
        return telegram_update_to_message_envelope(update)

    @mcp.tool()
    async def tg_envelope_to_graph_state(
        envelope: dict[str, Any],
        request_id: str = "",
    ) -> dict[str, Any]:
        """Build full-graph input state from MessageEnvelope."""
        rid = request_id.strip() or None
        return message_envelope_to_graph_state(envelope, request_id=rid)  # type: ignore[arg-type]

    @mcp.tool()
    async def tg_build_send_requests(outbound: dict[str, Any]) -> list[dict[str, Any]]:
        """Build Telegram Bot API sendMessage request payloads from OutboundMessage."""
        return outbound_message_to_telegram_requests(outbound)  # type: ignore[arg-type]

    return mcp
