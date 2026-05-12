"""Platform-specific output formatting for outbound message delivery.

Design:
  AssistantResponse (platform-agnostic, markdown-first)
      -> PlatformFormatter (Discord / Telegram adaptation)
      -> OutboundMessage (platform-ready segments)
"""

from __future__ import annotations

import abc
import re
from typing import Any

from markdown_it import MarkdownIt
from telegramify_markdown import convert as telegramify_convert
from telegramify_markdown import split_entities as telegramify_split_entities
from telegramify_markdown import utf16_len as telegramify_utf16_len

from nervos_brain.core_protocols.common import Platform, RenderMode
from nervos_brain.core_protocols.message_protocols import (
    ConversationContext,
    OutboundMessage,
    OutboundMessageSegment,
    PlatformCapabilities,
)

from .normalizer import chunk_for_platform

_CITATION_LABEL = re.compile(r"\[(\d+)\]")
_ESCAPED_CITATION_LABEL = re.compile(r"\\\[(\d+)\\\]")
_TELEGRAM_MD_RESERVED = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")


def get_platform_capabilities(
    platform: Platform,
    *,
    render_mode: RenderMode = "markdown",
) -> PlatformCapabilities:
    """Return platform capability profile for outbound formatting."""
    if platform == "telegram":
        return {
            "platform": "telegram",
            "render_mode": render_mode,
            "max_chars_per_segment": 4096,
            "max_segments": 8,
            "supports_streaming": False,
            "supports_inline_csat": False,
        }
    return {
        "platform": "discord",
        "render_mode": render_mode,
        "max_chars_per_segment": 2000,
        "max_segments": 8,
        "supports_streaming": True,
        "supports_inline_csat": True,
    }


class PlatformFormatter(abc.ABC):
    """Base formatter for converting markdown response into outbound payload."""

    @property
    @abc.abstractmethod
    def platform(self) -> Platform:
        """Target platform name."""

    def capabilities(self, *, render_mode: RenderMode) -> PlatformCapabilities:
        return get_platform_capabilities(self.platform, render_mode=render_mode)

    def format(
        self,
        *,
        request_id: str,
        context: ConversationContext,
        text: str,
        render_mode: RenderMode,
        append_csat: bool,
        reply_to_message_id: str | None = None,
    ) -> OutboundMessage:
        body = (
            self._to_plain_text(text)
            if render_mode == "plain"
            else self._transform_markdown(text)
        )

        caps = self.capabilities(render_mode=render_mode)
        segments = self._build_segments(
            request_id=request_id,
            body=body,
            caps=caps,
        )

        outbound: OutboundMessage = {
            "request_id": request_id,
            "context": context,
            "segments": segments,
            "render_mode": render_mode,
            "append_csat": append_csat,
        }
        if reply_to_message_id:
            outbound["reply_to_message_id"] = reply_to_message_id
        return outbound

    @abc.abstractmethod
    def _transform_markdown(self, text: str) -> str:
        """Convert standard markdown into platform-safe markdown."""

    def _build_segments(
        self,
        *,
        request_id: str,
        body: Any,
        caps: PlatformCapabilities,
    ) -> list[OutboundMessageSegment]:
        raw_chunks = chunk_for_platform(str(body), max_chars=caps["max_chars_per_segment"])
        chunks = _enforce_segment_limit(
            raw_chunks,
            max_segments=caps["max_segments"],
            max_chars=caps["max_chars_per_segment"],
        )
        return [
            _build_segment(
                request_id=request_id,
                idx=idx,
                text=chunk,
            )
            for idx, chunk in enumerate(chunks)
        ]

    @staticmethod
    def _to_plain_text(text: str) -> str:
        cleaned = text
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.replace("`", "")
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
        cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1 (\2)", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()


class DiscordFormatter(PlatformFormatter):
    @property
    def platform(self) -> Platform:
        return "discord"

    def _transform_markdown(self, text: str) -> str:
        return text.strip()

    def _build_segments(
        self,
        *,
        request_id: str,
        body: Any,
        caps: PlatformCapabilities,
    ) -> list[OutboundMessageSegment]:
        chunks = _split_discord_markdown(
            str(body),
            max_chars=caps["max_chars_per_segment"],
        )
        chunks = _enforce_segment_limit(
            chunks,
            max_segments=caps["max_segments"],
            max_chars=caps["max_chars_per_segment"],
        )
        return [
            _build_segment(request_id=request_id, idx=idx, text=chunk)
            for idx, chunk in enumerate(chunks)
        ]


class TelegramFormatter(PlatformFormatter):
    @property
    def platform(self) -> Platform:
        return "telegram"

    def _transform_markdown(self, text: str) -> tuple[str, list[Any]]:
        # Telegram entity payloads avoid MarkdownV2 escaping and split safely across code blocks.
        return telegramify_convert(text.strip())

    def _build_segments(
        self,
        *,
        request_id: str,
        body: Any,
        caps: PlatformCapabilities,
    ) -> list[OutboundMessageSegment]:
        if not isinstance(body, tuple) or len(body) != 2:
            return super()._build_segments(request_id=request_id, body=body, caps=caps)

        text, entities = body
        split_chunks = telegramify_split_entities(
            str(text),
            list(entities or []),
            max_utf16_len=caps["max_chars_per_segment"],
        )
        split_chunks = _enforce_telegram_segment_limit(
            split_chunks,
            max_segments=caps["max_segments"],
            max_utf16_len=caps["max_chars_per_segment"],
        )
        if not split_chunks:
            split_chunks = [("", [])]

        segments: list[OutboundMessageSegment] = []
        for idx, (chunk_text, chunk_entities) in enumerate(split_chunks):
            segment = _build_segment(
                request_id=request_id,
                idx=idx,
                text=chunk_text,
                char_count=telegramify_utf16_len(chunk_text),
            )
            segment["parse_mode_enabled"] = False
            entity_dicts = [
                entity.to_dict() if hasattr(entity, "to_dict") else dict(entity)
                for entity in (chunk_entities or [])
            ]
            if entity_dicts:
                segment["entities"] = entity_dicts
            segments.append(segment)
        return segments


def format_response_to_outbound(
    *,
    response: dict[str, Any],
    context: ConversationContext,
    render_mode: RenderMode = "markdown",
    append_csat: bool = False,
    reply_to_message_id: str | None = None,
) -> OutboundMessage:
    """Build OutboundMessage from AssistantResponse with platform conversion."""
    request_id = str(response.get("request_id", "unknown"))
    text = str(response.get("text", ""))
    platform = context.get("platform", "discord")

    formatter: PlatformFormatter
    if platform == "telegram":
        formatter = TelegramFormatter()
    else:
        formatter = DiscordFormatter()

    return formatter.format(
        request_id=request_id,
        context=context,
        text=text,
        render_mode=render_mode,
        append_csat=append_csat,
        reply_to_message_id=reply_to_message_id,
    )


def _split_discord_markdown(text: str, *, max_chars: int) -> list[str]:
    """Split Discord Markdown without leaving fenced code blocks unbalanced."""
    source = str(text or "").strip()
    if not source:
        return [""]

    blocks = _discord_markdown_blocks(source)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        for piece in _split_discord_block(block, max_chars=max_chars):
            piece = piece.strip("\n")
            if not piece:
                continue
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks or [""]


def _discord_markdown_blocks(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]

    fence_ranges: dict[int, int] = {}
    try:
        parser = MarkdownIt()
        for token in parser.parse(text):
            if token.type == "fence" and token.map:
                start, end = token.map
                fence_ranges[int(start)] = int(end)
    except Exception:
        fence_ranges = {}

    blocks: list[str] = []
    idx = 0
    pending: list[str] = []
    while idx < len(lines):
        if idx in fence_ranges:
            if pending:
                blocks.append("".join(pending).strip("\n"))
                pending = []
            end = max(idx + 1, fence_ranges[idx])
            blocks.append("".join(lines[idx:end]).strip("\n"))
            idx = end
            continue

        line = lines[idx]
        if not line.strip():
            if pending:
                blocks.append("".join(pending).strip("\n"))
                pending = []
        else:
            pending.append(line)
        idx += 1

    if pending:
        blocks.append("".join(pending).strip("\n"))
    return [block for block in blocks if block]


def _split_discord_block(block: str, *, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]
    fence = _parse_fenced_code_block(block)
    if fence is not None:
        lang, code = fence
        return _split_discord_code_block(lang=lang, code=code, max_chars=max_chars)
    return chunk_for_platform(block, max_chars=max_chars)


def _parse_fenced_code_block(block: str) -> tuple[str, str] | None:
    text = block.strip("\n")
    if not text.startswith("```"):
        return None
    lines = text.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return None
    first = lines[0].strip()
    lang = first[3:].strip().split()[0] if len(first) > 3 and first[3:].strip() else ""
    code = "\n".join(lines[1:-1])
    return lang, code


def _split_discord_code_block(*, lang: str, code: str, max_chars: int) -> list[str]:
    prefix = f"```{lang}\n" if lang else "```\n"
    suffix = "\n```"
    budget = max_chars - len(prefix) - len(suffix)
    if budget < 20:
        return chunk_for_platform(code, max_chars=max_chars)

    pieces: list[str] = []
    current = ""
    for line in code.splitlines(keepends=True):
        if len(line) > budget:
            if current:
                pieces.append(current.rstrip("\n"))
                current = ""
            for part in chunk_for_platform(line.rstrip("\n"), max_chars=budget):
                pieces.append(part)
            continue
        if len(current) + len(line) > budget and current:
            pieces.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current:
        pieces.append(current.rstrip("\n"))
    return [f"{prefix}{piece}{suffix}" for piece in pieces if piece]


def _extract_citation_labels(text: str) -> list[str]:
    labels: set[str] = set()
    for match in _CITATION_LABEL.findall(text):
        labels.add(f"[{match}]")
    for match in _ESCAPED_CITATION_LABEL.findall(text):
        labels.add(f"[{match}]")
    return sorted(labels, key=lambda s: int(s.strip("[]")))


def _enforce_segment_limit(
    chunks: list[str],
    *,
    max_segments: int,
    max_chars: int,
) -> list[str]:
    if len(chunks) <= max_segments:
        return chunks

    suffix = "\n\n[truncated]"
    result = chunks[:max_segments]
    last = result[-1]
    remaining = max_chars - len(suffix)
    if remaining < 1:
        result[-1] = suffix[:max_chars]
        return result
    result[-1] = last[:remaining] + suffix
    return result


def _build_segment(
    *,
    request_id: str,
    idx: int,
    text: str,
    char_count: int | None = None,
) -> OutboundMessageSegment:
    return {
        "segment_id": f"{request_id}:{idx}",
        "index": idx,
        "text": text,
        "char_count": len(text) if char_count is None else int(char_count),
        "citation_labels": _extract_citation_labels(text),
    }


def _enforce_telegram_segment_limit(
    chunks: list[tuple[str, list[Any]]],
    *,
    max_segments: int,
    max_utf16_len: int,
) -> list[tuple[str, list[Any]]]:
    if len(chunks) <= max_segments:
        return chunks

    suffix = "\n\n[truncated]"
    suffix_len = telegramify_utf16_len(suffix)
    result = chunks[:max_segments]
    last_text, last_entities = result[-1]
    remaining = max_utf16_len - suffix_len
    if remaining < 1:
        result[-1] = (_truncate_to_utf16_len(suffix, max_utf16_len), [])
        return result

    clipped_text = _truncate_to_utf16_len(last_text, remaining)
    clipped_len = telegramify_utf16_len(clipped_text)
    result[-1] = (
        clipped_text + suffix,
        _clip_telegram_entities(last_entities, max_utf16_len=clipped_len),
    )
    return result


def _truncate_to_utf16_len(text: str, max_utf16_len: int) -> str:
    if telegramify_utf16_len(text) <= max_utf16_len:
        return text

    out: list[str] = []
    total = 0
    for char in text:
        char_len = telegramify_utf16_len(char)
        if total + char_len > max_utf16_len:
            break
        out.append(char)
        total += char_len
    return "".join(out)


def _clip_telegram_entities(
    entities: list[Any],
    *,
    max_utf16_len: int,
) -> list[dict[str, Any]]:
    clipped: list[dict[str, Any]] = []
    for entity in entities or []:
        entity_dict = entity.to_dict() if hasattr(entity, "to_dict") else dict(entity)
        offset = int(entity_dict.get("offset", 0))
        length = int(entity_dict.get("length", 0))
        if offset >= max_utf16_len or length <= 0:
            continue
        if offset + length > max_utf16_len:
            entity_dict["length"] = max_utf16_len - offset
        clipped.append(entity_dict)
    return clipped


def _escape_non_code_md_v2(text: str) -> str:
    return _TELEGRAM_MD_RESERVED.sub(r"\\\1", text)


def _escape_code_block_content(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_inline_code_content(text: str) -> str:
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _escape_telegram_markdown_v2_code(text: str) -> str:
    # Preserve fenced and inline code blocks while escaping the rest.
    pieces = re.split(r"(```[\s\S]*?```)", text)
    out: list[str] = []
    for block in pieces:
        if not block:
            continue
        if block.startswith("```") and block.endswith("```"):
            code = block[3:-3]
            out.append("```" + _escape_code_block_content(code) + "```")
            continue

        inner = re.split(r"(`[^`]*`)", block)
        for frag in inner:
            if not frag:
                continue
            if frag.startswith("`") and frag.endswith("`"):
                out.append("`" + _escape_inline_code_content(frag[1:-1]) + "`")
            else:
                out.append(_escape_non_code_md_v2(frag))
    return "".join(out)


def _standard_markdown_to_telegram_markdown_v2(text: str) -> str:
    """Render common Markdown into Telegram MarkdownV2.

    Telegram MarkdownV2 does not support GitHub-style headings (`###`) and uses
    single asterisks for bold, so standard Markdown must be translated before
    escaping.
    """
    pieces = re.split(r"(```[\s\S]*?```)", text)
    out: list[str] = []
    for block in pieces:
        if not block:
            continue
        if block.startswith("```") and block.endswith("```"):
            code = block[3:-3]
            out.append("```" + _escape_code_block_content(code) + "```")
            continue

        inner = re.split(r"(`[^`]*`)", block)
        for frag in inner:
            if not frag:
                continue
            if frag.startswith("`") and frag.endswith("`"):
                out.append("`" + _escape_inline_code_content(frag[1:-1]) + "`")
                continue
            out.append(_convert_non_code_markdown_to_tg(frag))
    return "".join(out)


def _convert_non_code_markdown_to_tg(text: str) -> str:
    placeholders: list[str] = []

    def protect(value: str) -> str:
        token = f"\u0000TGMD{len(placeholders)}\u0000"
        placeholders.append(value)
        return token

    def bold_repl(match: re.Match[str]) -> str:
        inner = _escape_non_code_md_v2(match.group(1).strip())
        return protect(f"*{inner}*") if inner else ""

    def italic_repl(match: re.Match[str]) -> str:
        inner = _escape_non_code_md_v2(match.group(1).strip())
        return protect(f"_{inner}_") if inner else ""

    def link_repl(match: re.Match[str]) -> str:
        label = _escape_non_code_md_v2(match.group(1).strip())
        url = match.group(2).strip().replace("\\", "\\\\").replace(")", "\\)")
        return protect(f"[{label}]({url})")

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", link_repl, text)
    text = re.sub(r"\*\*([^*\n](?:[\s\S]*?[^*\n])?)\*\*", bold_repl, text)
    text = re.sub(r"__([^_\n](?:[\s\S]*?[^_\n])?)__", bold_repl, text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", italic_repl, text)

    converted_lines: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^(\s*)#{1,6}\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(2).strip()
            converted_lines.append(protect(f"*{_escape_non_code_md_v2(title)}*"))
            continue
        converted_lines.append(_escape_non_code_md_v2(line))

    escaped = "\n".join(converted_lines)
    for _ in range(len(placeholders) + 1):
        changed = False
        for idx, value in enumerate(placeholders):
            token = f"\u0000TGMD{idx}\u0000"
            if token in escaped:
                escaped = escaped.replace(token, value)
                changed = True
        if not changed:
            break
    return escaped
