"""LiteLLM 薄封装：统一 LLM 调用入口。

配置优先级（高 → 低）：
  1. 函数参数直接传入
  2. 环境变量 (CHAT_MODEL_FAST / LLM_API_KEY / LLM_API_BASE)
  3. 项目根目录 config.yaml 中的 llm 段
  4. 内置默认值
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_config_cache: dict[str, Any] | None = None
_logger = logging.getLogger(__name__)
_last_call_meta: dict[str, Any] | None = None
_TRANSIENT_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "bad gateway",
    "gateway timeout",
    "too many requests",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "service unavailable",
    " 429",
    " 500",
    " 502",
    " 503",
    " 504",
    "empty responses output",
)


def _load_config() -> dict[str, Any]:
    """从 config.yaml 加载配置，缓存结果。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    _config_cache = {}
    candidates = [
        Path.cwd() / "config.yaml",
        Path(__file__).resolve().parents[3] / "config.yaml",  # src/nervos_brain/graph_engine -> 项目根
    ]
    for path in candidates:
        if path.is_file():
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                _config_cache = data.get("llm", {})
            except Exception:
                pass
            break
    return _config_cache


def _get(key: str, env_var: str, default: str) -> str:
    """按优先级取值：环境变量 > config.yaml > 默认值。"""
    env_val = os.environ.get(env_var)
    if env_val:
        return env_val
    cfg = _load_config()
    cfg_val = cfg.get(key)
    if cfg_val:
        return str(cfg_val)
    return default


def get_model_name() -> str:
    return _get("model", "CHAT_MODEL_FAST", "openai/glm-4-flash")


def get_api_key() -> str | None:
    val = _get("api_key", "LLM_API_KEY", "")
    if not val:
        val = os.environ.get("OPENAI_API_KEY", "")
    return val or None


def get_api_base() -> str | None:
    val = _get("api_base", "LLM_API_BASE", "")
    return val or None


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool = False,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    disable_response_storage: bool | None = None,
) -> str:
    """统一调用 LLM，返回 assistant 内容字符串。"""
    global _last_call_meta
    import litellm
    # 避免在捕获到异常并走业务 fallback 时输出冗余的帮助提示噪音。
    litellm.suppress_debug_info = True
    litellm.set_verbose = False

    cfg = _load_config()
    model_name = model or get_model_name()
    model_lower = model_name.lower()
    temp = temperature if temperature is not None else float(cfg.get("temperature", 0.3))
    tokens = max_tokens if max_tokens is not None else int(cfg.get("max_tokens", 2048))
    effort = (
        str(reasoning_effort).strip()
        if reasoning_effort is not None
        else str(cfg.get("reasoning_effort", "")).strip()
    )
    text_verbosity = (
        str(verbosity).strip()
        if verbosity is not None
        else str(cfg.get("verbosity", "")).strip()
    )
    if disable_response_storage is None:
        disable_store = bool(cfg.get("disable_response_storage", False))
    else:
        disable_store = bool(disable_response_storage)

    max_retries = int(cfg.get("max_retries", 3) or 3)

    started = time.perf_counter()
    usage: dict[str, Any] = {}
    try:
        if "gpt-5" in model_lower:
            text, usage = _retry_llm_call(
                lambda: _call_llm_via_responses(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model_name=model_name,
                    max_tokens=tokens,
                    json_mode=json_mode,
                    reasoning_effort=effort,
                    verbosity=text_verbosity,
                    disable_store=disable_store,
                ),
                max_attempts=max_retries,
                model_name=model_name,
            )
            return text

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": tokens,
        }
        kwargs["temperature"] = temp

        api_key = get_api_key()
        if api_key:
            kwargs["api_key"] = api_key

        api_base = get_api_base()
        if api_base:
            kwargs["api_base"] = api_base

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if effort:
            kwargs["reasoning_effort"] = effort
        if text_verbosity:
            kwargs["verbosity"] = text_verbosity
        if disable_store:
            kwargs["store"] = False

        response = _retry_llm_call(
            lambda: litellm.completion(**kwargs),
            max_attempts=max_retries,
            model_name=model_name,
        )
        usage = _extract_usage(response)
        return _extract_completion_text(response)
    finally:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _last_call_meta = {
            "model": model_name,
            "json_mode": bool(json_mode),
            "reasoning_effort": effort,
            "verbosity": text_verbosity,
            "max_tokens": tokens,
            "elapsed_ms": elapsed_ms,
            "usage": usage,
        }


def get_last_call_meta() -> dict[str, Any] | None:
    if _last_call_meta is None:
        return None
    return dict(_last_call_meta)


def _legacy_call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    json_mode: bool,
    model_name: str,
    temp: float,
    tokens: int,
    effort: str,
    text_verbosity: str,
    disable_store: bool,
    max_retries: int,
) -> str:
    import litellm

    model_lower = model_name.lower()
    if "gpt-5" in model_lower:
        text, _usage = _retry_llm_call(
            lambda: _call_llm_via_responses(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model_name=model_name,
                max_tokens=tokens,
                json_mode=json_mode,
                reasoning_effort=effort,
                verbosity=text_verbosity,
                disable_store=disable_store,
            ),
            max_attempts=max_retries,
            model_name=model_name,
        )
        return text

    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": tokens,
    }
    kwargs["temperature"] = temp

    api_key = get_api_key()
    if api_key:
        kwargs["api_key"] = api_key

    api_base = get_api_base()
    if api_base:
        kwargs["api_base"] = api_base

    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if effort:
        kwargs["reasoning_effort"] = effort
    if text_verbosity:
        kwargs["verbosity"] = text_verbosity
    if disable_store:
        kwargs["store"] = False

    response = _retry_llm_call(
        lambda: litellm.completion(**kwargs),
        max_attempts=max_retries,
        model_name=model_name,
    )
    return _extract_completion_text(response)


def _call_llm_via_responses(
    *,
    system_prompt: str,
    user_prompt: str,
    model_name: str,
    max_tokens: int,
    json_mode: bool,
    reasoning_effort: str,
    verbosity: str,
    disable_store: bool,
) -> tuple[str, dict[str, Any]]:
    import litellm

    kwargs: dict[str, Any] = {
        "model": model_name,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_output_tokens": max_tokens,
    }

    api_key = get_api_key()
    if api_key:
        kwargs["api_key"] = api_key

    api_base = get_api_base()
    if api_base:
        kwargs["api_base"] = api_base

    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}

    text_cfg: dict[str, Any] = {}
    if verbosity:
        text_cfg["verbosity"] = verbosity
    if text_cfg:
        kwargs["text"] = text_cfg

    if disable_store:
        kwargs["store"] = False

    response = litellm.responses(**kwargs)
    output_text = getattr(response, "output_text", None)
    usage = _extract_usage(response)
    if isinstance(output_text, str) and output_text:
        return output_text, usage
    output = getattr(response, "output", [])
    extracted = _extract_responses_output_text(output)
    if extracted:
        return extracted, usage
    data = _obj_to_dict(response)
    extracted = _extract_responses_output_text(data.get("output", []))
    if extracted:
        return extracted, usage
    raise RuntimeError("empty responses output")


def _extract_responses_output_text(output: Any) -> str:
    if not isinstance(output, list):
        return ""
    chunks: list[str] = []
    for item in output:
        item_dict = _obj_to_dict(item)
        content = item_dict.get("content")
        if content is None:
            content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            part_dict = _obj_to_dict(part)
            text = part_dict.get("text")
            if text is None:
                text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
                continue
            if isinstance(text, dict):
                maybe_value = text.get("value") or text.get("text")
                if isinstance(maybe_value, str) and maybe_value.strip():
                    chunks.append(maybe_value.strip())
    return "\n".join(chunks).strip()


def _obj_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            dumped = obj.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return {}
    if hasattr(obj, "dict"):
        try:
            dumped = obj.dict()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return {}
    return {}


def _extract_usage(response: Any) -> dict[str, Any]:
    data = _obj_to_dict(response)
    usage = data.get("usage")
    if usage is None:
        usage = getattr(response, "usage", None)
    usage_dict = _obj_to_dict(usage)
    if not usage_dict and isinstance(usage, dict):
        usage_dict = usage
    if not usage_dict:
        return {}

    def _pick(*keys: str) -> int:
        for key in keys:
            value = usage_dict.get(key)
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    result = {
        "input_tokens": _pick("input_tokens", "prompt_tokens"),
        "output_tokens": _pick("output_tokens", "completion_tokens"),
        "total_tokens": _pick("total_tokens"),
    }
    if result["total_tokens"] <= 0:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]

    details: dict[str, Any] = {}
    for key in ("input_tokens_details", "output_tokens_details", "prompt_tokens_details", "completion_tokens_details"):
        value = usage_dict.get(key)
        if value is not None:
            details[key] = _obj_to_dict(value) or value
    if details:
        result["details"] = details
    return result


def _extract_completion_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except Exception:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in {"text", "output_text"} and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
            else:
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks).strip()
    return ""


def _is_transient_llm_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def _retry_llm_call(fn: Any, *, max_attempts: int, model_name: str) -> Any:
    attempts = max(1, int(max_attempts))
    delay = 1.0
    for idx in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if idx >= attempts or not _is_transient_llm_error(exc):
                raise
            _logger.warning(
                "llm transient error model=%s attempt=%d/%d err=%s",
                model_name,
                idx,
                attempts,
                type(exc).__name__,
            )
            time.sleep(delay)
            delay = min(delay * 2.0, 4.0)


def _extract_first_json_object(text: str) -> str | None:
    if not text:
        return None
    length = len(text)
    for start in range(length):
        if text[start] != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, length):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == "\"":
                    in_string = False
                continue
            if ch == "\"":
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except Exception:
                        break
    return None


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """调用 LLM 并解析 JSON 响应。"""
    json_hint_system = (
        f"{system_prompt}\n\n"
        "你必须只输出一个 JSON 对象，不要输出 markdown 代码块、解释性文本或额外前后缀。"
    )
    json_hint_user = (
        f"{user_prompt}\n\n"
        "只返回 JSON 对象。"
    )
    raw = call_llm(
        json_hint_system,
        json_hint_user,
        json_mode=True,
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_tokens=max_tokens,
    )
    try:
        return json.loads(raw)
    except Exception as exc:
        extracted = _extract_first_json_object(raw)
        if extracted:
            return json.loads(extracted)
        # 二次兜底：改用非 json_mode 再试一次，兼容部分网关对结构化输出参数支持不稳定。
        raw_retry = call_llm(
            json_hint_system,
            json_hint_user,
            json_mode=False,
            model=model,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            max_tokens=max_tokens,
        )
        try:
            return json.loads(raw_retry)
        except Exception:
            extracted_retry = _extract_first_json_object(raw_retry)
            if extracted_retry:
                return json.loads(extracted_retry)
        raise ValueError(
            f"LLM JSON parse failed: {exc}; raw={raw[:300]!r}; raw_retry={raw_retry[:300]!r}"
        ) from exc
