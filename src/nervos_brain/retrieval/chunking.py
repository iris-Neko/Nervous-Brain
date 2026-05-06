from typing import List


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 120) -> List[str]:
    """固定窗口切片。

    规则：
    - chunk_size 必须 > 0
    - overlap 必须 >= 0 且 < chunk_size
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")

    cleaned = text.strip()
    if not cleaned:
        return []

    step = chunk_size - overlap
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        end = start + chunk_size
        chunks.append(cleaned[start:end])
        start += step
    return chunks
