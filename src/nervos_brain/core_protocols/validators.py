# ============================================================
# validators.py —— 最小校验函数
# ============================================================
# 这个文件放的是"校验函数"——检查一个字典是否符合协议格式。
#
# 为什么需要校验？
#   TypedDict 只在编辑器里做类型提示，Python 运行时并不会真的检查。
#   也就是说，你写了 class MessageEnvelope(TypedDict): ...
#   但运行时如果有人传了一个少字段的字典，Python 不会报错。
#   所以我们需要手动写一些校验函数，在关键入口处检查一下。
#
# 这个文件在 M2 阶段只放最小集合的校验：
#   1. validate_required_keys()          —— 通用：检查必填字段是否齐全
#   2. validate_message_envelope()       —— 检查 MessageEnvelope
#   3. validate_evidence()               —— 检查 Evidence
#   4. validate_assistant_response()     —— 检查 AssistantResponse
#   5. validate_tool_call_request()      —— 检查 ToolCallRequest
#
# 后续随着项目推进，可以在这里逐步添加更多校验。
# ============================================================

from typing import Any, Dict, List, Sequence


# ============================================================
# 通用校验：检查必填字段是否齐全
# ============================================================
# 用法：
#   errors = validate_required_keys(some_dict, ["request_id", "text"])
#   如果 errors 为空列表，说明全部通过
#   如果 errors 不为空，说明缺了哪些字段

def validate_required_keys(
    data: Dict[str, Any],
    required_keys: Sequence[str],
) -> List[str]:
    """
    检查字典 data 中是否包含所有 required_keys。

    参数：
        data: 要检查的字典
        required_keys: 必须存在的键名列表

    返回：
        错误消息列表。如果全部通过，返回空列表 []。
    """
    errors: List[str] = []
    for key in required_keys:
        if key not in data:
            errors.append(f"缺少必填字段: '{key}'")
    return errors


# ============================================================
# MessageEnvelope 校验
# ============================================================
# 检查一个字典是否是合法的 MessageEnvelope。
# 这里只做最基本的"字段有没有"检查，不做深层类型检查。

# MessageEnvelope 的必填字段
_MESSAGE_ENVELOPE_REQUIRED = [
    "kind",
    "ts_ms",
    "message_id",
    "context",
    "content",
]

# ConversationContext 的必填字段
_CONVERSATION_CONTEXT_REQUIRED = [
    "platform",
    "user_id",
]


def validate_message_envelope(data: Dict[str, Any]) -> List[str]:
    """
    检查字典是否符合 MessageEnvelope 的最低要求。

    检查内容：
        1. 顶层必填字段是否齐全
        2. context 是否是字典
        3. context 里的必填字段是否齐全
        4. kind 的值是否合法
        5. ts_ms 是否是整数

    返回：
        错误消息列表。空列表 = 全部通过。
    """
    errors = validate_required_keys(data, _MESSAGE_ENVELOPE_REQUIRED)

    # 如果连必填字段都缺，就先返回，后面的检查没意义
    if errors:
        return errors

    # 检查 context 是否是字典
    context = data.get("context")
    if not isinstance(context, dict):
        errors.append("'context' 必须是一个字典")
        return errors

    # 检查 context 里的必填字段
    context_errors = validate_required_keys(context, _CONVERSATION_CONTEXT_REQUIRED)
    for err in context_errors:
        errors.append(f"context 内部 → {err}")

    # 检查 kind 的值是否合法
    kind = data.get("kind")
    if kind not in ("message", "command"):
        errors.append(f"'kind' 的值必须是 'message' 或 'command'，但收到了: '{kind}'")

    # 检查 ts_ms 是否是整数
    ts_ms = data.get("ts_ms")
    if not isinstance(ts_ms, int):
        errors.append(f"'ts_ms' 必须是整数，但收到了: {type(ts_ms).__name__}")

    return errors


# ============================================================
# Evidence 校验
# ============================================================
# 检查一个字典是否是合法的 Evidence。
# 重点：Evidence 是整个系统的"证据通货"，必须严格校验。

_EVIDENCE_REQUIRED = [
    "id",
    "source",
    "title",
    "url",
    "anchor",
    "snippet",
    "score",
    "payload",
    "hash",
    "retrieved_ts_ms",
]

_VALID_EVIDENCE_SOURCES = {"qdrant", "discourse", "github", "memory"}


def validate_evidence(data: Dict[str, Any]) -> List[str]:
    """
    检查字典是否符合 Evidence 的最低要求。

    检查内容：
        1. 必填字段是否齐全
        2. source 是否在白名单内
        3. score 是否在 0~1 之间
        4. snippet 是否超过 1200 字符上限
        5. payload 是否是字典

    返回：
        错误消息列表。空列表 = 全部通过。
    """
    errors = validate_required_keys(data, _EVIDENCE_REQUIRED)

    if errors:
        return errors

    # 检查 source 是否合法
    source = data.get("source")
    if source not in _VALID_EVIDENCE_SOURCES:
        errors.append(
            f"'source' 必须是 {_VALID_EVIDENCE_SOURCES} 之一，"
            f"但收到了: '{source}'"
        )

    # 检查 score 是否在合理范围
    score = data.get("score")
    if isinstance(score, (int, float)):
        if not (0.0 <= score <= 1.0):
            errors.append(f"'score' 必须在 0~1 之间，但收到了: {score}")
    else:
        errors.append(f"'score' 必须是数字，但收到了: {type(score).__name__}")

    # 检查 snippet 长度
    snippet = data.get("snippet")
    if isinstance(snippet, str) and len(snippet) > 1200:
        errors.append(
            f"'snippet' 不能超过 1200 字符，当前长度: {len(snippet)}"
        )

    # 检查 payload 是否是字典
    payload = data.get("payload")
    if not isinstance(payload, dict):
        errors.append("'payload' 必须是一个字典")

    return errors


# ============================================================
# AssistantResponse 校验
# ============================================================
# 检查最终回答是否合法。
# 重点检查：引用编号是否和正文对应。

_ASSISTANT_RESPONSE_REQUIRED = [
    "request_id",
    "text",
    "citations",
]


def validate_assistant_response(data: Dict[str, Any]) -> List[str]:
    """
    检查字典是否符合 AssistantResponse 的最低要求。

    检查内容：
        1. 必填字段是否齐全
        2. citations 是否是列表
        3. 每条 citation 是否有 label、url、anchor、title
        4. text 中的引用编号是否都能在 citations 里找到对应

    返回：
        错误消息列表。空列表 = 全部通过。
    """
    import re

    errors = validate_required_keys(data, _ASSISTANT_RESPONSE_REQUIRED)

    if errors:
        return errors

    # 检查 citations 是否是列表
    citations = data.get("citations")
    if not isinstance(citations, list):
        errors.append("'citations' 必须是一个列表")
        return errors

    # 检查每条 citation 的必填字段
    citation_required = ["label", "url", "anchor", "title"]
    citation_labels_in_list = set()

    for i, cit in enumerate(citations):
        if not isinstance(cit, dict):
            errors.append(f"citations[{i}] 必须是一个字典")
            continue
        cit_errors = validate_required_keys(cit, citation_required)
        for err in cit_errors:
            errors.append(f"citations[{i}] → {err}")
        label = cit.get("label")
        if isinstance(label, str):
            citation_labels_in_list.add(label)

    # 检查 text 中的引用编号是否都有对应的 citation
    text = data.get("text", "")
    if isinstance(text, str):
        # 从正文中提取所有 [1]、[2]、[3] 这样的编号
        text_labels = set(re.findall(r"\[\d+\]", text))
        orphan_labels = text_labels - citation_labels_in_list
        if orphan_labels:
            errors.append(
                f"正文中出现了引用编号 {orphan_labels}，"
                f"但在 citations 列表中找不到对应条目"
            )

    return errors


# ============================================================
# ToolCallRequest 校验
# ============================================================
# 检查工具调用请求是否合法。
# 重点：tool 必须在白名单内。

_TOOL_CALL_REQUEST_REQUIRED = [
    "request_id",
    "step_id",
    "tool",
    "args",
    "timeout_ms",
    "issued_ts_ms",
    "deadline_ts_ms",
    "idempotency_key",
    "allow_parallel",
]

_VALID_TOOL_NAMES = {"qdrant_search", "discourse_query", "github_search", "memory_fetch"}


def validate_tool_call_request(data: Dict[str, Any]) -> List[str]:
    """
    检查字典是否符合 ToolCallRequest 的最低要求。

    检查内容：
        1. 必填字段是否齐全
        2. tool 是否在白名单内
        3. args 是否是字典
        4. deadline_ts_ms 是否大于 issued_ts_ms

    返回：
        错误消息列表。空列表 = 全部通过。
    """
    errors = validate_required_keys(data, _TOOL_CALL_REQUEST_REQUIRED)

    if errors:
        return errors

    # 检查 tool 是否在白名单内
    tool = data.get("tool")
    if tool not in _VALID_TOOL_NAMES:
        errors.append(
            f"'tool' 必须是 {_VALID_TOOL_NAMES} 之一，"
            f"但收到了: '{tool}'"
        )

    # 检查 args 是否是字典
    args = data.get("args")
    if not isinstance(args, dict):
        errors.append("'args' 必须是一个字典")

    # 检查截止时间必须晚于发出时间
    issued = data.get("issued_ts_ms")
    deadline = data.get("deadline_ts_ms")
    if isinstance(issued, int) and isinstance(deadline, int):
        if deadline <= issued:
            errors.append(
                f"'deadline_ts_ms'({deadline}) 必须大于 "
                f"'issued_ts_ms'({issued})"
            )

    return errors
