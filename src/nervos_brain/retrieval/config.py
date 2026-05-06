from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_config_cache: dict[str, Any] | None = None


def _load_yaml_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    module_path = Path(__file__).resolve()
    candidates = [Path.cwd() / "config.yaml"]
    candidates.extend(parent / "config.yaml" for parent in module_path.parents)
    yaml_path = next((path for path in candidates if path.is_file()), None)

    if yaml_path is not None:
        import yaml

        with open(yaml_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        _config_cache = raw if isinstance(raw, dict) else {}
    else:
        _config_cache = {}
    return _config_cache


def get_retrieval_section(section: str) -> dict[str, Any]:
    """Return a shallow copy of a config section if it exists and is a mapping."""
    root = _load_yaml_config()
    data = root.get(section, {})
    return dict(data) if isinstance(data, dict) else {}


def has_retrieval_section(section: str) -> bool:
    """Whether config.yaml contains a mapping section with this name."""
    return bool(get_retrieval_section(section))


def get_retrieval_backend_sections(default_section: str = "retrieval") -> list[str]:
    """Return configured retrieval backend section names.

    ``config.yaml`` may define a top-level ``retrieval_backends`` list:

    retrieval_backends:
      - retrieval
      - retrieval_forum_talk

    If it is absent or invalid, the runtime falls back to the primary
    ``retrieval`` section for backward compatibility.
    """
    root = _load_yaml_config()
    raw = root.get("retrieval_backends")
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item) for item in raw]
    else:
        candidates = [default_section]

    sections: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        section = str(item).strip()
        if not section or section in seen:
            continue
        seen.add(section)
        sections.append(section)
    return sections or [default_section]


@dataclass(frozen=True)
class RetrievalConfig:
    """检索层统一配置。

    从 config.yaml[retrieval] 读取，缺省值保持向后兼容 M4 行为。
    """

    # Qdrant 浅层索引
    vector_size: int = 64
    collection_name: str = "nervos_docs"
    qdrant_path: str = "data/qdrant_local"

    # 切片参数
    chunk_size: int = 600
    chunk_overlap: int = 120
    snippet_max_chars: int = 1200

    # SQLite 深层原件库
    archive_db: str = "data/archive.db"

    # Embedding
    use_real_embedding: bool = False
    embedding_model: str = "openai/embedding-2"

    # 多路检索参数
    top_k_per_path: int = 10
    final_top_k: int = 10
    rrf_k: int = 60
    fuzzy_threshold: float = 0.55
    enable_bm25: bool = True
    enable_fuzzy: bool = True
    enable_exact: bool = True


def load_retrieval_config(
    section: str = "retrieval",
    inherit_from: str | None = None,
) -> RetrievalConfig:
    """从 config.yaml 指定 section 构建 RetrievalConfig。

    Args:
        section:      读取的配置段名，默认 ``retrieval``。
        inherit_from: 可选的基础段名，若提供则先读取该段，再由 ``section`` 覆盖。
    """
    cfg: dict[str, Any] = {}

    if inherit_from:
        cfg.update(get_retrieval_section(inherit_from))

    cfg.update(get_retrieval_section(section))

    return RetrievalConfig(
        vector_size=int(cfg.get("vector_size", 64)),
        collection_name=cfg.get("collection_name", "nervos_docs"),
        qdrant_path=cfg.get("qdrant_path", "data/qdrant_local"),
        chunk_size=int(cfg.get("chunk_size", 600)),
        chunk_overlap=int(cfg.get("chunk_overlap", 120)),
        snippet_max_chars=int(cfg.get("snippet_max_chars", 1200)),
        archive_db=cfg.get("archive_db", "data/archive.db"),
        use_real_embedding=bool(cfg.get("use_real_embedding", False)),
        embedding_model=cfg.get("embedding_model", "openai/embedding-2"),
        top_k_per_path=int(cfg.get("top_k_per_path", 10)),
        final_top_k=int(cfg.get("final_top_k", 10)),
        rrf_k=int(cfg.get("rrf_k", 60)),
        fuzzy_threshold=float(cfg.get("fuzzy_threshold", 0.55)),
        enable_bm25=bool(cfg.get("enable_bm25", True)),
        enable_fuzzy=bool(cfg.get("enable_fuzzy", True)),
        enable_exact=bool(cfg.get("enable_exact", True)),
    )


def load_retrieval_backend_configs(
    sections: list[str] | None = None,
    *,
    inherit_from: str = "retrieval",
) -> list[tuple[str, RetrievalConfig]]:
    """Load all configured retrieval backend configs.

    Non-primary sections inherit shared knobs from ``retrieval`` by default
    (embedding mode, vector size, ranking parameters) while overriding their
    own Qdrant/archive paths and collection names.
    """
    selected = sections or get_retrieval_backend_sections(default_section=inherit_from)
    configs: list[tuple[str, RetrievalConfig]] = []
    for section in selected:
        name = str(section).strip()
        if not name:
            continue
        base = None if name == inherit_from else inherit_from
        configs.append((name, load_retrieval_config(section=name, inherit_from=base)))
    if configs:
        return configs
    return [(inherit_from, load_retrieval_config(section=inherit_from))]
