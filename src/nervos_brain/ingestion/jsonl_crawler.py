"""JSONL-based generic crawler.

This is a bridge crawler while waiting for real upstream source adapters.
Each JSON line is one document object.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .base import RawDocument, SourceCrawler
from .html_cleaner import make_summary


class JsonlCrawler(SourceCrawler):
    """Read normalized docs from a local JSONL file."""

    def __init__(
        self,
        file_path: str,
        *,
        source: str,
        doc_type: str = "doc",
        lang: str = "unknown",
        version: str = "unknown",
        topic: str = "unknown",
        strict: bool = False,
    ) -> None:
        self._path = Path(file_path)
        self._source = source
        self._doc_type = doc_type
        self._lang = lang
        self._version = version
        self._topic = topic
        self._strict = strict

    def crawl(self) -> Iterable[RawDocument]:
        if not self._path.exists():
            raise FileNotFoundError(f"jsonl file not found: {self._path}")
        if not self._path.is_file():
            raise ValueError(f"jsonl path is not a file: {self._path}")

        with self._path.open("r", encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue

                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    if self._strict:
                        raise
                    continue

                doc = self._row_to_doc(row, line_no=line_no)
                if doc is None:
                    if self._strict:
                        raise ValueError(f"invalid row at line {line_no}")
                    continue
                yield doc

    def _row_to_doc(self, row: dict, *, line_no: int) -> RawDocument | None:
        title = str(row.get("title", "")).strip()
        raw_text = str(row.get("raw_text", "")).strip()
        if not title or not raw_text:
            return None

        external_id = str(row.get("id", "")).strip() or f"line-{line_no}"
        url = str(row.get("url", "unknown")).strip() or "unknown"
        anchor = str(row.get("anchor", "")).strip()
        if not anchor:
            anchor = self._build_anchor(external_id)

        keywords = row.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ",".join(str(k).strip() for k in keywords if str(k).strip())
        else:
            keywords = str(keywords).strip()

        summary = str(row.get("summary", "")).strip() or make_summary(raw_text, max_chars=300)
        topic = str(row.get("topic", self._topic)).strip() or self._topic
        lang = str(row.get("lang", self._lang)).strip() or self._lang
        version = str(row.get("version", self._version)).strip() or self._version
        doc_type = str(row.get("doc_type", self._doc_type)).strip() or self._doc_type
        raw_format = str(row.get("raw_format", "text")).strip() or "text"

        metadata = row.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        return RawDocument(
            source=self._source,
            external_id=external_id,
            title=title,
            raw_text=raw_text,
            url=url,
            anchor=anchor,
            doc_type=doc_type,
            summary=summary,
            keywords=keywords,
            raw_format=raw_format,
            lang=lang,
            version=version,
            topic=topic,
            metadata={k: str(v) for k, v in metadata.items()},
        )

    def _build_anchor(self, external_id: str) -> str:
        digest = hashlib.sha256(f"{self._source}:{external_id}".encode("utf-8")).hexdigest()[:16]
        return f"doc:{self._source}-{digest}#unit:0"
