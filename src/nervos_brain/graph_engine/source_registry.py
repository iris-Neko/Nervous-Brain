"""Retrieval source registry shared by planning and runtime validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalSource:
    id: str
    label: str
    tool_hint: str
    description: str
    topic_examples: tuple[str, ...] = ()


SOURCES: dict[str, RetrievalSource] = {
    "github_docs": RetrievalSource(
        id="github_docs",
        label="official docs and GitHub repository documents",
        tool_hint="qdrant_search",
        description=(
            "官方文档、docs.nervos.org、RFC、CKB/CCC/Fiber 仓库 README、SDK 文档、"
            "教程、协议规范和仓库内说明。用户问官方教程、入门路径、概念解释、"
            "规范或文档链接时优先使用。"
        ),
        topic_examples=(
            "nervosnetwork/docs.nervos.org",
            "nervosnetwork/ckb",
            "nervosnetwork/rfcs",
            "ckb-devrel/ccc",
            "nervosnetwork/fiber",
            "nervosnetwork/fiber-docs",
        ),
    ),
    "github_code": RetrievalSource(
        id="github_code",
        label="GitHub source code, config files, and executable examples",
        tool_hint="github_search",
        description=(
            "Nervos 相关 GitHub 仓库源码、配置、脚本、函数、类型、模块、测试、"
            "SDK 示例代码和可执行调用方式。用户问源码位置、函数/类/命令实现、"
            "配置项、调用链、报错对应代码或具体代码片段时优先使用。"
        ),
        topic_examples=(
            "nervosnetwork/ckb",
            "nervosnetwork/fiber",
            "ckb-devrel/ccc",
            "RGBPlusPlus/rgbpp",
            "appfi5/appfi5",
        ),
    ),
    "nervos_talk": RetrievalSource(
        id="nervos_talk",
        label="Nervos Talk forum posts and replies",
        tool_hint="discourse_query",
        description=(
            "Nervos Talk 论坛帖子、回复、社区讨论、Spark/grant/proposal、生态项目介绍、"
            "真实案例、项目列表、社区评价和路线争议。用户问社区有没有、项目案例、"
            "讨论链接、谁在做、可以看看什么时优先使用 discourse_query。"
        ),
        topic_examples=("nervos_talk:<topic_id>",),
    ),
}

SOURCE_ALIASES: dict[str, str] = {
    "official": "github_docs",
    "official_docs": "github_docs",
    "official-docs": "github_docs",
    "docs": "github_docs",
    "documentation": "github_docs",
    "github": "github_docs",
    "github_doc": "github_docs",
    "github_docs": "github_docs",
    "code": "github_code",
    "source_code": "github_code",
    "github_code": "github_code",
    "github-source": "github_code",
    "github_source": "github_code",
    "repo_code": "github_code",
    "rfcs": "github_docs",
    "rfc": "github_docs",
    "talk": "nervos_talk",
    "forum": "nervos_talk",
    "community": "nervos_talk",
    "discourse": "nervos_talk",
    "nervos_talk": "nervos_talk",
}

QDRANT_FILTER_KEYS = {
    "source",
    "type",
    "doc_type",
    "version",
    "lang",
    "url",
    "anchor",
    "topic",
    "title",
    "keywords",
}


def format_source_registry_for_prompt() -> str:
    lines = [
        "合法 source 只能使用下面这些精确值；不要自造 official_docs/docs/documentation 等 source："
    ]
    for source in SOURCES.values():
        topics = ", ".join(source.topic_examples) if source.topic_examples else "(无)"
        lines.append(
            f"- source={source.id}: {source.description} "
            f"推荐工具: {source.tool_hint}. 常见 topic: {topics}"
        )
    lines.append(
        "qdrant_search.filters 支持字段：source、topic、type/doc_type、version、lang、url、anchor、title、keywords。"
    )
    lines.append(
        "官方教程/官方文档/入门资料应使用 source=github_docs；源码/函数/配置/代码片段应使用 source=github_code 或 github_search；社区讨论/项目案例优先使用 discourse_query。"
    )
    return "\n".join(lines)


def normalize_tool_filters(tool: str, filters: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize LLM-produced filters before tool execution."""
    if not isinstance(filters, dict):
        return {}, ["filters_not_dict"]

    normalized: dict[str, Any] = {}
    notes: list[str] = []
    for raw_key, raw_value in filters.items():
        key = str(raw_key).strip()
        if not key or raw_value in (None, ""):
            continue
        if isinstance(raw_value, (list, dict, tuple, set)):
            notes.append(f"dropped_complex_filter:{key}")
            continue

        value = str(raw_value).strip()
        if not value:
            continue

        if tool == "qdrant_search" and key not in QDRANT_FILTER_KEYS:
            notes.append(f"dropped_unknown_filter:{key}")
            continue

        if key == "source":
            canonical = SOURCE_ALIASES.get(value.lower())
            if canonical:
                if canonical != value:
                    notes.append(f"mapped_source:{value}->{canonical}")
                normalized[key] = canonical
            else:
                notes.append(f"dropped_unknown_source:{value}")
            continue

        if key == "type":
            normalized[key] = "github_doc" if value == "official_docs" else value
            continue

        normalized[key] = value

    return normalized, notes


def should_retry_qdrant_without_filters(filters: dict[str, Any], evidence_count: int) -> bool:
    return evidence_count == 0 and bool(filters)
