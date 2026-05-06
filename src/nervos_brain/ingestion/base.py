"""Generic ingestion abstractions for future real data sources.

This module is intentionally source-agnostic:
you can implement a crawler for GitHub, docs sites, RSS, forums, etc.
as long as it yields ``RawDocument`` items.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RawDocument:
    """Normalized unit produced by a crawler before DB ingestion."""

    source: str
    external_id: str
    title: str
    raw_text: str
    url: str = "unknown"
    anchor: str = ""
    doc_type: str = "doc"
    summary: str = ""
    keywords: str = ""
    raw_format: str = "text"
    lang: str = "unknown"
    version: str = "unknown"
    topic: str = "unknown"
    metadata: dict[str, str] = field(default_factory=dict)


class SourceCrawler(abc.ABC):
    """Abstract crawler interface for all future data sources."""

    @abc.abstractmethod
    def crawl(self) -> Iterable[RawDocument]:
        """Yield normalized documents from a data source."""
