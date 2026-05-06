import hashlib
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from .config import RetrievalConfig


def deterministic_embedding(text: str, dim: int = 64) -> List[float]:
    """确定性 embedding（M4 本地最小实现）。

    用哈希生成固定长度向量，便于本地可重复测试。
    """
    vec: List[float] = []
    for i in range(dim):
        h = hashlib.sha256(f"{i}:{text}".encode("utf-8")).hexdigest()
        # 映射到 [-1, 1]
        vec.append(((int(h[:8], 16) / 0xFFFFFFFF) * 2.0) - 1.0)
    return vec


class QdrantStore:
    """Qdrant 最小存储封装。"""

    def __init__(
        self,
        config: Optional[RetrievalConfig] = None,
        qdrant_location: str = "data/qdrant_local",
    ) -> None:
        self.config = config or RetrievalConfig()
        Path(qdrant_location).mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=qdrant_location)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        collections = self.client.get_collections().collections
        names = {c.name for c in collections}
        if self.config.collection_name not in names:
            self.client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config=VectorParams(size=self.config.vector_size, distance=Distance.COSINE),
            )

    def upsert_chunks(self, chunks: List[Dict[str, str]]) -> int:
        """批量写入切片。"""
        points: List[PointStruct] = []
        for item in chunks:
            text = item["text"]
            payload = dict(item["payload"])
            payload["snippet"] = text[: self.config.snippet_max_chars]
            points.append(
                PointStruct(
                    id=str(uuid4()),
                    vector=deterministic_embedding(text, self.config.vector_size),
                    payload=payload,
                )
            )

        self.client.upsert(collection_name=self.config.collection_name, points=points)
        return len(points)
