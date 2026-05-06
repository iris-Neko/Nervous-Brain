"""Embedding abstraction for the retrieval layer.

Production mode  → calls the Zhipu embedding-2 API via litellm.
Offline / test   → falls back to the deterministic hash embedding from M4,
                   which is stable, reproducible, and needs no API key.
"""
from __future__ import annotations

import os
from typing import List

from .config import RetrievalConfig, _load_yaml_config, load_retrieval_config
from .qdrant_writer import deterministic_embedding


def get_embedding(
    text: str,
    config: RetrievalConfig | None = None,
) -> List[float]:
    """Return a float vector for *text*.

    Uses real embedding API when ``config.use_real_embedding`` is True;
    otherwise falls back to the deterministic hash-based vector.
    """
    cfg = config or load_retrieval_config()
    if cfg.use_real_embedding:
        return _api_embedding(text, cfg)
    return deterministic_embedding(text, cfg.vector_size)


def _api_embedding(text: str, cfg: RetrievalConfig) -> List[float]:
    """Call the configured embedding model via litellm."""
    import litellm  # deferred to avoid hard import at module load

    api_key = os.environ.get("LLM_API_KEY") or _read_api_key_from_yaml()
    api_base = _read_api_base_from_yaml()

    kwargs: dict = {
        "model": cfg.embedding_model,
        "input": [text],
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    resp = litellm.embedding(**kwargs)
    return list(resp.data[0]["embedding"])


# ── helpers to read llm section from config.yaml ───────────────────────────

def _read_api_key_from_yaml() -> str:
    try:
        raw = _load_yaml_config()
        llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
        return str(llm.get("api_key", "") if isinstance(llm, dict) else "")
    except Exception:
        pass
    return ""


def _read_api_base_from_yaml() -> str:
    try:
        raw = _load_yaml_config()
        llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
        return str(llm.get("api_base", "") if isinstance(llm, dict) else "")
    except Exception:
        pass
    return ""
