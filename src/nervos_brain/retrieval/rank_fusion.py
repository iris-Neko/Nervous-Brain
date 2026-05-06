"""Reciprocal Rank Fusion (RRF) for multi-path retrieval.

RRF score formula (Cormack et al., 2009):
    RRF(d) = Σ_r  1 / (k + rank_r(d))

where ``rank_r(d)`` is the 1-based position of document d in ranked list r,
and k is a smoothing constant (default 60, from the original paper).

Documents are deduplicated by ``anchor``.  Ties are broken by descending
insertion order (i.e. whichever path returned the document first wins).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence


@dataclass
class FusedResult:
    anchor: str
    title: str
    source: str
    rrf_score: float
    # provenance: which paths contributed and their raw scores
    contributions: Dict[str, float] = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[List[dict]],
    *,
    k: int = 60,
    path_names: Sequence[str] | None = None,
) -> List[FusedResult]:
    """Fuse multiple ranked lists of candidates using RRF.

    Args:
        ranked_lists: Each element is a list of dicts with at minimum
                      ``anchor``, ``title``, and ``source`` keys.
                      Lists are assumed already sorted best-first.
        k:            RRF smoothing constant (default 60).
        path_names:   Optional human-readable name for each list
                      (used to populate ``contributions``).

    Returns:
        A new list sorted by descending RRF score with duplicates merged.
    """
    names = list(path_names) if path_names else [f"path_{i}" for i in range(len(ranked_lists))]

    # accumulate RRF scores keyed by anchor
    scores: Dict[str, float] = {}
    meta: Dict[str, dict] = {}   # anchor → first-seen title/source
    contribs: Dict[str, Dict[str, float]] = {}

    for path_idx, ranked in enumerate(ranked_lists):
        path_name = names[path_idx]
        for rank, candidate in enumerate(ranked, start=1):
            anchor = candidate.get("anchor", "")
            if not anchor:
                continue
            rrf = 1.0 / (k + rank)
            scores[anchor] = scores.get(anchor, 0.0) + rrf
            contribs.setdefault(anchor, {})[path_name] = candidate.get("score", 0.0)
            if anchor not in meta:
                meta[anchor] = {
                    "title": candidate.get("title", ""),
                    "source": candidate.get("source", ""),
                }

    fused = [
        FusedResult(
            anchor=anchor,
            title=meta[anchor]["title"],
            source=meta[anchor]["source"],
            rrf_score=scores[anchor],
            contributions=contribs[anchor],
        )
        for anchor in scores
    ]
    fused.sort(key=lambda r: r.rrf_score, reverse=True)
    return fused
