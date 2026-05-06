# ============================================================
# tool_protocols.py —— 工具调用相关协议
# ============================================================
# 这个文件定义的是"工具调用"相关的数据结构。
#
# 什么是工具调用？
#   Nervos Brain 在回答问题时，不只是靠 LLM 自己"编"答案，
#   而是会调用外部工具去获取真实数据。
#   比如：
#     - 调用 Qdrant 搜索向量数据库
#     - 调用 Discourse API 搜索 Nervos Talk 论坛
#     - 调用 GitHub API 搜索代码仓库
#     - 从记忆系统里取历史记录
#
# 为什么需要协议？
#   工具调用不能随便调：
#     1. 必须先填一张"请求表"（ToolCallRequest）
#     2. 调用完必须交一张"结果表"（ToolCallResult）
#     3. 出错了必须用统一的错误码（ErrorCode + ToolError）
#   这样才能审计、复盘、重试。
#
# 安全规则：
#   - 只有白名单里的工具才允许调用
#   - 参数必须通过 schema 校验
#   - 禁止从自然语言里解析工具调用（防 prompt injection）
# ============================================================

from __future__ import annotations

from typing import Dict, List, Literal, NotRequired, TypedDict

from .retrieval_protocols import Evidence, ToolName


# ============================================================
# ToolCallRequest（工具调用请求）
# ============================================================
# 什么时候产生？
#   当 LangGraph 的 Execute 节点决定"我要调用某个工具"时，
#   会先填好这张请求表，交给 ToolRuntime 去执行。
#
# 为什么要这么正式？
#   因为每次工具调用都有成本（网络延迟、API 配额、token 消耗），
#   必须留下记录，方便后续追溯和去重。

class ToolCallRequest(TypedDict):
    # 请求 ID（和 GraphState 里的 request_id 一致）
    request_id: str

    # 对应的检索步骤 ID（来自 RetrievalStep.step_id）
    step_id: str

    # 调用哪个工具（必须是 ToolName 白名单里的）
    tool: ToolName

    # 工具参数（每个工具的参数不同，所以用通用字典）
    # 例如 qdrant_search 的参数可能是 {"query": "...", "top_k": 5}
    args: Dict[str, object]

    # 超时时间（毫秒）：超过这个时间就放弃
    timeout_ms: int

    # 发出请求的时间（毫秒时间戳）
    issued_ts_ms: int

    # 截止时间（毫秒时间戳）：过了这个时间，即使返回了结果也要丢弃
    # 目的是防止"超时雪崩"——很久以后才回来的结果已经没有意义了
    deadline_ts_ms: int

    # 去重键：用于防止同一个请求被执行两次
    # 建议用 hash(canonical_json({tool, args, step_id})) 生成
    idempotency_key: str

    # 是否允许和其他工具调用并行执行
    allow_parallel: bool


# ============================================================
# ToolCallWarning（工具调用警告）
# ============================================================
# 什么是警告？
#   工具调用成功了，但过程中发生了一些"不影响结果但值得记录"的事情。
#   比如：
#     - 返回内容太长被截断了（WARN_TRUNCATED）
#     - 去掉了一些噪音内容（WARN_DENOISED）
#     - 参数格式不完全对但被自动修正了（WARN_SCHEMA_COERCED）

class ToolCallWarning(TypedDict):
    # 警告码
    code: Literal[
        "WARN_TRUNCATED",       # 内容被截断
        "WARN_DENOISED",        # 内容被去噪
        "WARN_SCHEMA_COERCED",  # 参数被自动修正
    ]

    # 警告详情
    message: str


# ============================================================
# ToolCallStatus（工具调用状态）
# ============================================================
# 工具调用的最终状态只有三种可能

ToolCallStatus = Literal[
    "ok",         # 成功
    "error",      # 出错
    "cancelled",  # 被取消（比如超时、用户取消）
]


# ============================================================
# ErrorCode（统一错误码）
# ============================================================
# 为什么要统一错误码？
#   如果每个工具自己随便报错，后面的重试逻辑就没法写。
#   统一错误码后，系统可以根据错误码自动决定：
#     - 要不要重试？
#     - 重试几次？
#     - 还是直接放弃？

ErrorCode = Literal[
    # ---------- 检索相关 ----------
    "ERR_RETRIEVAL_EMPTY",              # 检索结果为空
    "ERR_RETRIEVAL_TIMEOUT",            # 检索超时

    # ---------- MCP（Model Context Protocol）相关 ----------
    "ERR_MCP_TIMEOUT",                  # MCP 服务超时
    "ERR_MCP_TRANSPORT_UNAVAILABLE",    # MCP 传输通道不可用
    "ERR_MCP_BAD_FRAME",                # MCP 数据帧格式错误（可能是 stdout 被污染）

    # ---------- SSE（Server-Sent Events）相关 ----------
    "ERR_SSE_DISCONNECTED",             # SSE 连接断开
    "ERR_SSE_BACKPRESSURE",             # SSE 背压（消息堆积太多，处理不过来）

    # ---------- 限流与预算 ----------
    "ERR_RATE_LIMIT",                   # 触发速率限制
    "ERR_BUDGET_EXCEEDED",              # 超出 token 预算

    # ---------- 自检与参数 ----------
    "ERR_SELF_CHECK_FAILED",            # 自检失败（答案质量不达标）
    "ERR_USER_PARAM_MISSING",           # 用户缺少必要参数

    # ---------- 模型能力 ----------
    "ERR_PROVIDER_CAPABILITY_MISMATCH", # 当前模型不具备所需能力

    # ---------- 工具调用 ----------
    "ERR_TOOL_SCHEMA_INVALID",          # 工具参数不符合 schema
    "ERR_TOOL_PARSE_FAILED",            # 工具调用解析失败
    "ERR_TOOL_EXECUTION_FAILED",        # 工具执行过程抛错
    "ERR_TOOL_CANCELLED",               # 工具调用被取消

    # ---------- 输出格式 ----------
    "ERR_OUTPUT_SCHEMA_INVALID",        # 输出不符合 AssistantResponse 格式
    "ERR_OUTPUT_MARKDOWN_BROKEN",       # 输出的 Markdown 格式损坏
]


# ============================================================
# ToolError（工具错误详情）
# ============================================================
# 当工具调用失败时，用这个结构来记录错误详情。

class ToolError(TypedDict):
    # 错误码（来自上面的 ErrorCode）
    code: ErrorCode

    # 错误描述（人类可读的说明）
    message: str

    # 是否可以重试
    # True = 系统可以自动重试（比如超时）
    # False = 不可恢复的错误（比如参数格式错误）
    retryable: bool


# ============================================================
# ToolCallResult（工具调用结果）
# ============================================================
# 工具执行完毕后，必须返回这张结果表。
# 不管成功还是失败，都必须填。

class ToolCallResult(TypedDict):
    # 请求 ID（和请求里的一致）
    request_id: str

    # 对应的检索步骤 ID
    step_id: str

    # 调用的工具名
    tool: ToolName

    # 最终状态
    status: ToolCallStatus

    # 是否成功（status == "ok" 时为 True）
    ok: bool

    # 可选：工具返回的原始数据（每个工具格式不同）
    data: NotRequired[Dict[str, object]]

    # 可选：已经转换成标准 Evidence 格式的证据列表
    evidence: NotRequired[List[Evidence]]

    # 可选：如果出错了，错误详情
    error: NotRequired[ToolError]

    # 可选：过程中的警告列表
    warnings: NotRequired[List[ToolCallWarning]]

    # 原始返回数据的大小（字节）
    raw_size_bytes: int

    # 脱敏记录：哪些敏感信息被遮掩了
    # 例如 ["api_key_masked"] 表示 API 密钥已被遮掩
    redactions_applied: List[str]

    # 工具开始执行的时间（毫秒时间戳）
    started_ts_ms: int

    # 工具执行完成的时间（毫秒时间戳）
    finished_ts_ms: int
