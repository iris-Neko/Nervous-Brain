from pathlib import Path
from typing import Dict, List

from .anchor_hash import build_anchor, build_stable_hash
from .chunking import chunk_text
from .config import RetrievalConfig
from .payload_builder import build_payload
from .readers import read_text_file


def build_ingest_records(
    file_path: str,
    source: str,
    doc_type: str,
    version: str,
    lang: str,
    url: str,
    topic: str,
    config: RetrievalConfig,
) -> List[Dict[str, str]]:
    """从单文件构建可写入 Qdrant 的切片记录。"""
    text = read_text_file(file_path)
    chunks = chunk_text(text, chunk_size=config.chunk_size, overlap=config.chunk_overlap)
    doc_id = Path(file_path).stem

    records: List[Dict[str, str]] = []
    for idx, piece in enumerate(chunks):
        anchor = build_anchor(doc_id, idx)
        payload = build_payload(
            source=source,
            doc_type=doc_type,
            version=version,
            lang=lang,
            url=url,
            anchor=anchor,
            topic=topic,
        )
        payload["title"] = doc_id
        payload["hash"] = build_stable_hash(url=url, anchor=anchor, snippet=piece, payload=payload)
        records.append({"text": piece, "payload": payload})
    return records
