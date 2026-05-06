# ============================================================
# response_protocols.py —— 回复相关协议
# ============================================================
# 这个文件定义的是"最终回复"相关的数据结构。
#
# 在整个流程中，这些结构出现在最后一步：
#   用户提问 → 检索证据 → 打分 → 组装回答 → 【这里】→ 发给平台
#
# 这个文件只定义两个东西：
#   1. Citation   = 引用（回答里的 [1]、[2] 对应的来源信息）
#   2. AssistantResponse = 助手的最终回答结构
#
# 注意：
#   AssistantResponse 是"平台无关"的回答。
#   它不关心最终发到 Discord 还是 Telegram。
#   真正发给平台的结构是 message_protocols.py 里的 OutboundMessage。
#   中间由 ResponseNormalizer 和 PlatformFormatter 负责转换。
# ============================================================

from typing import List, NotRequired, TypedDict


# ============================================================
# Citation（引用）
# ============================================================
# 什么是引用？
#   Nervos Brain 回答问题时，不能"空口说白话"。
#   每个关键断言都必须有出处。
#   回答正文里会出现 [1]、[2] 这样的编号，
#   每个编号对应一条 Citation，记录了来源链接和标题。
#
# 强制规则：
#   - citations 列表里的编号必须和 text 里的 [1]、[2] 一一对应
#   - text 里没有被引用的 citation 会被删掉
#   - citation 里没有对应编号的也会被删掉
#   - 如果实在修不好，就降级成"纯文本 + 引用列表"

class Citation(TypedDict):
    # 引用标签，例如 "[1]"、"[2]"
    label: str

    # 来源链接（点击可打开原文）
    url: str

    # 锚点（精确定位到原文的哪个位置）
    anchor: str

    # 来源标题
    title: str


# ============================================================
# AssistantResponse（助手最终回答）
# ============================================================
# 什么是 AssistantResponse？
#   这是整个系统最终产出的"回答语义结构"。
#   它包含：
#     - 给用户看的 Markdown 文本
#     - 引用列表
#     - 可选的调试摘要
#     - 如果需要用户补充信息，还会带上问题
#
# 它和 OutboundMessage 的区别：
#   - AssistantResponse = "回答的内容是什么"（平台无关）
#   - OutboundMessage   = "怎么发到平台上去"（要拆段、要适配格式）

class AssistantResponse(TypedDict):
    # 请求 ID（和整个流程中的 request_id 一致）
    request_id: str

    # 最终给用户看的 Markdown 文本
    text: str

    # 引用列表
    citations: List[Citation]

    # 可选：执行过程的简短摘要（用于调试和追溯）
    trace_summary: NotRequired[str]

    # 可选：是否需要用户补充信息
    # 当某个 InfoNeed 是 required=True 但系统查不到时，
    # 就会设为 True，并在 ask_user_question 里写上要问什么
    need_user_input: NotRequired[bool]

    # 可选：要问用户的问题
    # 例如 "请问您使用的是哪个版本的 Fiber SDK？"
    ask_user_question: NotRequired[str]
