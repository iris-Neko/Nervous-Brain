"""M6-T6/T7/T8: 工具调用超时、取消、结果标准化。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from nervos_brain.core_protocols import ToolCallRequest, ToolCallResult


async def execute_tool(
    request: ToolCallRequest,
    handler: Callable[..., Any],
) -> ToolCallResult:
    """带超时和 deadline 的工具调用执行器。"""
    started = int(time.time() * 1000)

    now_ms = int(time.time() * 1000)
    if now_ms >= request["deadline_ts_ms"]:
        return _cancelled_result(request, started, "deadline already passed")

    timeout_s = request["timeout_ms"] / 1000.0

    try:
        if asyncio.iscoroutinefunction(handler):
            raw = await asyncio.wait_for(handler(request), timeout=timeout_s)
        else:
            raw = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, handler, request),
                timeout=timeout_s,
            )
    except asyncio.TimeoutError:
        return _cancelled_result(request, started, "timeout")
    except Exception as exc:
        return _error_result(
            request,
            started,
            "ERR_TOOL_EXECUTION_FAILED",
            str(exc) or exc.__class__.__name__,
            retryable=False,
        )

    finished = int(time.time() * 1000)
    if finished >= request["deadline_ts_ms"]:
        return _cancelled_result(request, started, "result arrived after deadline")

    if not isinstance(raw, dict):
        return _error_result(
            request,
            started,
            "ERR_TOOL_PARSE_FAILED",
            "tool handler returned non-object payload",
            retryable=False,
        )

    return normalize_tool_result(raw, request, started, finished)


def normalize_tool_result(
    raw: dict[str, Any],
    request: ToolCallRequest,
    started_ts_ms: int,
    finished_ts_ms: int,
) -> ToolCallResult:
    """把原始工具返回包装成标准 ToolCallResult。"""
    evidence = raw.get("evidence", [])
    return ToolCallResult(
        request_id=request["request_id"],
        step_id=request["step_id"],
        tool=request["tool"],
        status="ok",
        ok=True,
        data=raw.get("data", {}),
        evidence=evidence,
        raw_size_bytes=raw.get("raw_size_bytes", 0),
        redactions_applied=raw.get("redactions_applied", []),
        started_ts_ms=started_ts_ms,
        finished_ts_ms=finished_ts_ms,
    )


def _cancelled_result(
    request: ToolCallRequest,
    started_ts_ms: int,
    reason: str,
) -> ToolCallResult:
    now = int(time.time() * 1000)
    return ToolCallResult(
        request_id=request["request_id"],
        step_id=request["step_id"],
        tool=request["tool"],
        status="cancelled",
        ok=False,
        error={"code": "ERR_TOOL_CANCELLED", "message": reason, "retryable": True},
        raw_size_bytes=0,
        redactions_applied=[],
        started_ts_ms=started_ts_ms,
        finished_ts_ms=now,
    )


def _error_result(
    request: ToolCallRequest,
    started_ts_ms: int,
    code: str,
    message: str,
    *,
    retryable: bool,
) -> ToolCallResult:
    now = int(time.time() * 1000)
    return ToolCallResult(
        request_id=request["request_id"],
        step_id=request["step_id"],
        tool=request["tool"],
        status="error",
        ok=False,
        error={"code": code, "message": message[:280], "retryable": retryable},
        raw_size_bytes=0,
        redactions_applied=[],
        started_ts_ms=started_ts_ms,
        finished_ts_ms=now,
    )


_SEEN_KEYS: dict[str, float] = {}
_DEDUP_WINDOW_S = 60.0


def check_idempotency(key: str) -> bool:
    """如果 key 在去重窗口内已见过，返回 True（重复）。"""
    now = time.time()
    _cleanup_expired(now)
    if key in _SEEN_KEYS:
        return True
    _SEEN_KEYS[key] = now
    return False


def _cleanup_expired(now: float) -> None:
    expired = [k for k, ts in _SEEN_KEYS.items() if now - ts > _DEDUP_WINDOW_S]
    for k in expired:
        del _SEEN_KEYS[k]


def reset_idempotency_cache() -> None:
    """测试用：清空幂等缓存。"""
    _SEEN_KEYS.clear()
