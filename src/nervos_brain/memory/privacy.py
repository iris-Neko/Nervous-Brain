"""M5 最小隐私过滤器。"""

from __future__ import annotations

import re

# 直接拒绝入库的敏感模式（MVP 从严）。
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"api[_-]?key\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"token\s*[:=]\s*\S+", re.IGNORECASE),
]

# MVP 默认也不入库的 PII。
_PII_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3,4}[-.\s]?){2,4}\d{2,4}\b"),
]


def check_memory_text(text: str) -> tuple[bool, list[str]]:
    """检查文本是否允许入库。

    返回:
      - allow: 是否允许入库
      - reasons: 命中的拒绝原因列表
    """
    reasons: list[str] = []

    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            reasons.append("secret_detected")

    for pattern in _PII_PATTERNS:
        if pattern.search(text):
            reasons.append("pii_detected")

    return len(reasons) == 0, sorted(set(reasons))
