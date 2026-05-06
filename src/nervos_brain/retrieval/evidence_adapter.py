import time
from typing import Any, Dict

from nervos_brain.core_protocols import Evidence


def qdrant_result_to_evidence(result: Any) -> Evidence:
    """把 Qdrant 检索结果适配成统一 Evidence。"""
    payload: Dict[str, str] = dict(result.payload or {})
    return {
        "id": str(result.id),
        "source": "qdrant",
        "title": payload.get("title", "unknown"),
        "url": payload.get("url", "unknown"),
        "anchor": payload.get("anchor", "unknown"),
        "snippet": payload.get("snippet", "")[:1200],
        "score": float(result.score or 0.0),
        "payload": {k: str(v) for k, v in payload.items()},
        "hash": payload.get("hash", ""),
        "retrieved_ts_ms": int(time.time() * 1000),
    }
