"""Project-root based path and config helpers.

Config values should remain portable (usually relative paths). Runtime code can
call these helpers when it needs an actual filesystem path to read or write.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROJECT_MARKERS = ("pyproject.toml", ".git")


def find_project_root(start: str | Path) -> Path:
    """Find the repository/project root by walking upward from ``start``.

    ``pyproject.toml`` is preferred because packaged or copied deployments may
    not include a ``.git`` directory. ``.git`` remains a useful fallback for
    development worktrees.
    """
    current = Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent

    search = (current, *current.parents)
    for marker in _PROJECT_MARKERS:
        for candidate in search:
            if (candidate / marker).exists():
                return candidate
    raise RuntimeError(f"Could not find project root from {start!s}")


@lru_cache(maxsize=1)
def project_root() -> Path:
    """Return this installation's project root without depending on cwd."""
    return find_project_root(Path(__file__))


@lru_cache(maxsize=1)
def config_path() -> Path | None:
    """Return the effective config.yaml path, if one exists.

    Priority:
    1. ``NERVOS_BRAIN_CONFIG`` environment variable. If set, it must exist.
    2. ``project_root() / "config.yaml"`` if present.
    3. ``None``. ``config.yaml.example`` is intentionally not a runtime fallback.
    """
    override = os.environ.get("NERVOS_BRAIN_CONFIG")
    if override:
        path = resolve_project_path(override)
        if not path.is_file():
            raise FileNotFoundError(f"NERVOS_BRAIN_CONFIG does not exist: {path}")
        return path

    default = project_root() / "config.yaml"
    return default if default.is_file() else None


@lru_cache(maxsize=1)
def load_project_config() -> dict[str, Any]:
    """Load project config as a plain dict.

    This function has no side effects beyond reading YAML. It does not create
    directories, change cwd, or mutate environment variables.
    """
    path = config_path()
    if path is None:
        return {}

    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw if isinstance(raw, dict) else {}


def resolve_project_path(path: str | Path, *, base: str | Path | None = None) -> Path:
    """Resolve a user/config path for runtime filesystem access.

    - Relative paths are interpreted relative to ``base`` or project root.
    - Absolute paths are accepted as explicit user intent.
    - ``~`` paths are expanded.
    - The target does not need to already exist.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    root = Path(base).expanduser() if base is not None else project_root()
    if not root.is_absolute():
        root = project_root() / root
    return root / candidate


def ensure_parent_dir(path: str | Path) -> Path:
    """Create ``path``'s parent directory and return ``Path(path)``."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved
