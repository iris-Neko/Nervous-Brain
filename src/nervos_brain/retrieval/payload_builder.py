from typing import Dict


def build_payload(
    source: str,
    doc_type: str,
    version: str,
    lang: str,
    url: str,
    anchor: str,
    topic: str = "unknown",
) -> Dict[str, str]:
    """构建标准 payload，缺省值统一补 unknown。"""
    payload = {
        "source": source or "unknown",
        "type": doc_type or "unknown",
        "version": version or "unknown",
        "lang": lang or "unknown",
        "url": url or "unknown",
        "anchor": anchor or "unknown",
        "topic": topic or "unknown",
    }
    return payload
