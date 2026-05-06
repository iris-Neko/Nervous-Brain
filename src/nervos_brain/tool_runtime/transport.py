"""M6-T9/T10/T11: MCP Transport 抽象 + Mock + 选择逻辑。"""

from __future__ import annotations

import abc
from typing import Any


class MCPTransportAdapter(abc.ABC):
    """MCP 传输层抽象基类。"""

    @abc.abstractmethod
    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        """发送请求并返回原始结果。"""

    @abc.abstractmethod
    def close(self) -> None:
        """关闭连接。"""


class MockTransportAdapter(MCPTransportAdapter):
    """返回固定假数据的 Mock Transport。"""

    def __init__(self, fixed_response: dict[str, Any] | None = None) -> None:
        self._response = fixed_response or {
            "data": {"mock": True},
            "evidence": [],
            "raw_size_bytes": 0,
            "redactions_applied": [],
        }

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return dict(self._response)

    def close(self) -> None:
        pass


TRANSPORT_PREFERENCE = ["streamable_http", "sse"]


def select_transport(
    available: set[str] | None = None,
    preference: list[str] | None = None,
) -> str | None:
    """按优先级选择可用的传输协议。"""
    prefs = preference or TRANSPORT_PREFERENCE
    avail = available or set()
    for proto in prefs:
        if proto in avail:
            return proto
    return None
