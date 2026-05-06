"""M6-T4/T5: ToolCallRequest 构造 + 幂等 key 生成。"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from nervos_brain.core_protocols import ToolCallRequest

from .registry import validate_tool_args


def build_idempotency_key(tool: str, args: dict[str, Any], step_id: str) -> str:
    """canonical json + sha256 生成稳定的幂等去重键。"""
    stable_args = {k: v for k, v in args.items() if not k.startswith("_")}
    payload = {"tool": tool, "args": stable_args, "step_id": step_id}
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_tool_call_request(
    *,
    request_id: str,
    step_id: str,
    tool: str,
    args: dict[str, Any],
    timeout_ms: int = 60_000,
    allow_parallel: bool = True,
) -> ToolCallRequest:
    """构造合法的 ToolCallRequest，含自动校验。"""
    errors = validate_tool_args(tool, args)
    if errors:
        raise ValueError(f"tool args validation failed: {errors}")

    now_ms = int(time.time() * 1000)
    return ToolCallRequest(
        request_id=request_id,
        step_id=step_id,
        tool=tool,  # type: ignore[arg-type]
        args=args,
        timeout_ms=timeout_ms,
        issued_ts_ms=now_ms,
        deadline_ts_ms=now_ms + timeout_ms,
        idempotency_key=build_idempotency_key(tool, args, step_id),
        allow_parallel=allow_parallel,
    )
