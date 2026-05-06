"""Lightweight HTML → plain-text converter for Discourse post content.

Discourse returns post bodies as ``cooked`` HTML.  This module converts
them to clean UTF-8 text suitable for storage, BM25 indexing, and display
in Evidence snippets.

No external dependencies — uses only stdlib ``html.parser``.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser


# Tags whose inner content should be skipped entirely
_SKIP_TAGS = frozenset({"script", "style", "aside", "nav", "head"})

# Block-level tags that should introduce a newline
_BLOCK_TAGS = frozenset({
    "p", "br", "div", "li", "tr", "th",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre",
})

# Inline emphasis worth preserving as backticks (code spans)
_CODE_TAGS = frozenset({"code", "tt"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0
        self._code_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag in _CODE_TAGS:
            self._code_depth += 1
            self._parts.append("`")
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        if tag in _CODE_TAGS:
            self._code_depth = max(0, self._code_depth - 1)
            self._parts.append("`")
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # collapse runs of blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        # strip trailing spaces from each line
        text = "\n".join(line.rstrip() for line in text.splitlines())
        return text.strip()


def html_to_text(html: str) -> str:
    """Convert Discourse ``cooked`` HTML to clean plain text.

    Preserves paragraph breaks and inline code spans.
    Discards all markup, scripts, and navigation elements.
    """
    if not html:
        return ""
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def make_summary(text: str, max_chars: int = 300) -> str:
    """Return the first ``max_chars`` characters of *text* as a summary.

    Truncates at the last word boundary before the limit to avoid cutting
    a word in half.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        truncated = truncated[:last_space]
    return truncated + "…"
