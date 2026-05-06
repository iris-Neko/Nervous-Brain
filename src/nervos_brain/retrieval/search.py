from typing import Dict, List

from nervos_brain.core_protocols import Evidence

from .evidence_adapter import qdrant_result_to_evidence
from .qdrant_writer import QdrantStore, deterministic_embedding


def _build_filter_must(filters: Dict[str, str]):
    # 延迟导入，避免模块初始化过重
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    must = []
    for key, value in filters.items():
        must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=must)


def search_with_filters(
    store: QdrantStore,
    query: str,
    filters: Dict[str, str],
    top_k: int = 5,
) -> List[Evidence]:
    """强制带 filter 的检索接口（防裸 Top-K）。"""
    if not filters:
        raise ValueError("检索必须提供 payload filter，禁止裸 Top-K")
    if top_k < 1 or top_k > 20:
        raise ValueError("top_k 必须在 1..20")

    query_vec = deterministic_embedding(query, store.config.vector_size)
    query_filter = _build_filter_must(filters)
    response = store.client.query_points(
        collection_name=store.config.collection_name,
        query=query_vec,
        query_filter=query_filter,
        limit=top_k,
    )
    return [qdrant_result_to_evidence(r) for r in response.points]
