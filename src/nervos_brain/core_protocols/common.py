from typing import Literal

# 平台类型：目前系统只支持 Discord 和 Telegram
Platform = Literal["discord", "telegram"]

# 消息种类：普通消息，或者命令消息
MessageKind = Literal["message", "command"]

# 渲染模式：
# markdown = 正常标准输出
# plain = 兜底降级输出
RenderMode = Literal["markdown", "plain"]