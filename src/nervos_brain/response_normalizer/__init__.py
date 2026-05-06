# response_normalizer 包
# 这个包负责把模型输出修整成不会发崩平台的格式

from .normalizer import (
    chunk_for_platform,
    normalize_citations,
    sanitize_markdown,
    validate_response_shape,
)
from .platform_formatter import (
    DiscordFormatter,
    PlatformFormatter,
    TelegramFormatter,
    format_response_to_outbound,
    get_platform_capabilities,
)

__all__ = [
    "validate_response_shape",
    "normalize_citations",
    "sanitize_markdown",
    "chunk_for_platform",
    "PlatformFormatter",
    "DiscordFormatter",
    "TelegramFormatter",
    "format_response_to_outbound",
    "get_platform_capabilities",
]
