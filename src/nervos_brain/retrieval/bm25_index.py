"""BM25 keyword index built in-memory from the archive layer.

Language handling:
  - ASCII terms:  split on whitespace + punctuation, lowercased.
  - CJK:          each character becomes its own token (unigram).
  - Mixed:        both rules applied, deduped.

The index is rebuilt from the ArchiveStore on demand (lazy) or explicitly
via ``BM25Index.rebuild()``.  For large corpora (>100 k records) a
persistent SQLite FTS5 table is more appropriate, but the in-memory approach
is sufficient for the current development phase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from rank_bm25 import BM25Okapi

from .dual_layer import ArchiveRecord, ArchiveStore


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokenize(text: str) -> List[str]:
    """Split text into BM25-ready tokens.

    Handles mixed Chinese + English / code identifiers.
    Splits camelCase and snake_case into sub-tokens so that
    e.g. ``openChannel`` matches both ``open`` and ``channel``.
    """
    # camelCase split must happen before lowercasing (regex relies on case)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"_+", " ", text)
    text = text.lower()

    tokens: List[str] = []
    # CJK unigrams
    tokens.extend(_CJK_RE.findall(text))
    # ASCII words (identifiers, numbers)
    tokens.extend(_WORD_RE.findall(text))
    return tokens or [""]


@dataclass
class BM25Result:
    anchor: str
    title: str
    source: str
    score: float
    raw_text_preview: str = ""  # first 200 chars of raw_text


class BM25Index:
    """In-memory BM25 index over ArchiveStore records.

    Indexes: title + keywords + raw_text (first 2000 chars)
    The raw_text slice keeps build time bounded while still covering
    the most information-dense part of most documents.
    """

    def __init__(self) -> None:
        self._model: Optional[BM25Okapi] = None
        self._records: List[ArchiveRecord] = []

    # ── build ──────────────────────────────────────────────────────────────

    def build(self, records: List[ArchiveRecord]) -> None:
        """Build the index from an explicit list of ArchiveRecords."""
        self._records = list(records)
        corpus = [self._doc_text(r) for r in self._records]
        tokenized = [tokenize(d) for d in corpus]
        self._model = BM25Okapi(tokenized)

    def build_from_store(self, store: ArchiveStore) -> None:
        """Convenience: load all records from a store then build."""
        self.build(store.list_all())

    # ── search ─────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10) -> List[BM25Result]:
        """Return up to *top_k* records ranked by BM25 score."""
        if self._model is None or not self._records:
            return []

        q_tokens = tokenize(query)
        scores = self._model.get_scores(q_tokens)

        # pair (index, score), filter zero-score results, sort descending
        ranked = sorted(
            [(i, float(s)) for i, s in enumerate(scores) if s > 0.0],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

        results: List[BM25Result] = []
        for idx, score in ranked:
            r = self._records[idx]
            results.append(
                BM25Result(
                    anchor=r.anchor,
                    title=r.title,
                    source=r.source,
                    score=score,
                    raw_text_preview=r.raw_text[:200],
                )
            )
        return results

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _doc_text(record: ArchiveRecord) -> str:
        """Concatenate the fields that carry retrieval signal."""
        parts = [
            record.title,
            record.keywords,
            record.summary,
            record.raw_text[:2000],
        ]
        return " ".join(p for p in parts if p)

    @property
    def size(self) -> int:
        return len(self._records)
