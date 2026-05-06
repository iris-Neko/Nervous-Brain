# ============================================================
# memory_protocols.py —— 记忆系统相关协议
# ============================================================
# 这个文件定义的是"记忆系统"相关的数据结构。
#
# 什么是记忆系统？
#   Nervos Brain 在和用户聊天时，需要记住一些东西：
#   - 这个用户之前问过什么（用户记忆）
#   - 这个频道里讨论过什么（频道记忆）
#   - 当前这个线程聊了什么（线程状态）
#
# 为什么要分域？
#   如果不分域，A 用户的私密问题可能泄漏到 B 用户的回答里。
#   所以记忆必须按"用户""频道""线程"三把钥匙来分开存取。
#
# 这个文件不写业务逻辑，只定义"表格格式"。
# ============================================================

from typing import List, Literal, TypedDict

from .common import Platform


# ============================================================
# 记忆分域钥匙（Memory Keys）
# ============================================================
# 这三把钥匙决定了"去哪个柜子里取记忆"。
# 就像图书馆的索书号：楼层 -> 书架 -> 格子。

# 用户记忆钥匙：
# 用 平台 + 用户ID 来定位某个用户的私人记忆
class UserMemoryKey(TypedDict):
    # 来自哪个平台
    platform: Platform

    # 用户的唯一 ID
    user_id: str


# 频道记忆钥匙：
# 用 平台 + 服务器ID + 频道ID 来定位某个频道的公共记忆
class ChannelMemoryKey(TypedDict):
    # 来自哪个平台
    platform: Platform

    # 服务器 / 群组 ID（Discord 叫 guild，Telegram 叫 supergroup）
    guild_id: str

    # 频道 / 聊天 ID
    channel_id: str


# 线程钥匙：
# 用 平台 + 服务器ID + 频道ID + 线程ID 来定位某个具体的对话线程
class ThreadKey(TypedDict):
    # 来自哪个平台
    platform: Platform

    # 服务器 / 群组 ID
    guild_id: str

    # 频道 / 聊天 ID
    channel_id: str

    # 线程 ID
    thread_id: str


# ============================================================
# 记忆指针（MemoryPointer）
# ============================================================
# 什么是记忆指针？
#   系统不会把整段历史记忆全塞进 prompt（那样太浪费 token）。
#   而是传一个"指针"，告诉后续节点：
#   "你需要的那段记忆，编号是 xxx，在 user 域里，相关度 0.85"
#   后续节点拿着这个指针去数据库里按需取。

# 指针种类：
# summary = 一段对话的摘要
# fact    = 一个短事实（比如"用户偏好 JS SDK"）
# event_span = 一段原始事件片段
MemoryPointerKind = Literal["summary", "fact", "event_span"]


class MemoryPointer(TypedDict):
    # 指针种类
    kind: MemoryPointerKind

    # 记忆条目的唯一 ID（summary_id / fact_id / event_span_id）
    id: str

    # 检索相关度评分（0~1 之间，越高越相关）
    score: float

    # 属于哪个记忆域：用户 / 频道 / 线程
    namespace: Literal["user", "channel", "thread"]


# ============================================================
# 事实卡片（Fact）
# ============================================================
# 什么是事实卡片？
#   高频、短小、稳定的信息片段。
#   比如：
#     - "这个用户的默认 SDK 是 JavaScript"
#     - "当前频道讨论的 repo 是 nervosnetwork/ckb"
#     - "Fiber 的最新版本是 0.3"
#
# 为什么要单独提出来？
#   因为这些信息每次回答几乎都要用，但又很短（几个词），
#   单独存成卡片后可以直接注入 prompt，不用走完整的检索流程，
#   既省 token 又快。

class Fact(TypedDict):
    # 事实卡片的唯一 ID
    id: str

    # 属于哪个域：用户私有 / 频道公共
    namespace: Literal["user", "channel"]

    # 事实的键名，例如 "default_sdk"、"repo_url"、"fiber_version"
    key: str

    # 事实的值，例如 "javascript"、"https://github.com/nervosnetwork/ckb"
    value: str

    # 置信度（0~1）：这条事实有多可靠
    # 刚从对话里推断出来的可能是 0.6，被多次确认的可能是 0.95
    confidence: float

    # 最后更新时间（毫秒时间戳）
    updated_ts_ms: int

    # 这条事实是从哪些对话事件中提取出来的（事件 ID 列表）
    source_event_ids: List[str]
