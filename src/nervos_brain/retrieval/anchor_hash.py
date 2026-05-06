import hashlib
import json
from typing import Dict


def build_anchor(doc_id: str, chunk_index: int) -> str:
    """稳定锚点规则：doc:{doc_id}#chunk:{index}"""
    return f"doc:{doc_id}#chunk:{chunk_index}"


def canonical_json(data: Dict[str, str]) -> str:
    """稳定 JSON 序列化。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_stable_hash(url: str, anchor: str, snippet: str, payload: Dict[str, str]) -> str:
    """基于 canonical json 生成稳定哈希。"""
    basis = {
        "url": url,
        "anchor": anchor,
        "snippet": snippet.strip(),
        "payload": payload,
    }
    raw = canonical_json(basis).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
