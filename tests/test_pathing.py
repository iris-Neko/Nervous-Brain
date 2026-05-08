from __future__ import annotations

from pathlib import Path

import pytest

import nervos_brain.pathing as pathing


def _clear_pathing_caches() -> None:
    pathing.project_root.cache_clear()
    pathing.config_path.cache_clear()
    pathing.load_project_config.cache_clear()


def test_project_root_does_not_depend_on_cwd(monkeypatch, tmp_path: Path):
    _clear_pathing_caches()
    monkeypatch.chdir(tmp_path)

    root = pathing.project_root()

    assert (root / "pyproject.toml").is_file()
    assert root.name == "Nervos-Brain"


def test_find_project_root_from_nested_file():
    nested = Path(__file__).resolve()

    root = pathing.find_project_root(nested)

    assert (root / "pyproject.toml").is_file()


def test_resolve_project_path_uses_project_root_for_relative_paths():
    _clear_pathing_caches()

    resolved = pathing.resolve_project_path("data/logs")

    assert resolved == pathing.project_root() / "data" / "logs"


def test_resolve_project_path_expands_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = pathing.resolve_project_path("~/nervos-test")

    assert resolved == tmp_path / "nervos-test"


def test_resolve_project_path_accepts_absolute_path(tmp_path: Path):
    absolute = tmp_path / "x.db"

    assert pathing.resolve_project_path(absolute) == absolute


def test_config_path_env_override_and_load(monkeypatch, tmp_path: Path):
    _clear_pathing_caches()
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("llm:\n  model: test-model\n", encoding="utf-8")
    monkeypatch.setenv("NERVOS_BRAIN_CONFIG", str(cfg))

    assert pathing.config_path() == cfg
    assert pathing.load_project_config()["llm"]["model"] == "test-model"


def test_config_path_env_override_missing_fails_fast(monkeypatch, tmp_path: Path):
    _clear_pathing_caches()
    monkeypatch.setenv("NERVOS_BRAIN_CONFIG", str(tmp_path / "missing.yaml"))

    with pytest.raises(FileNotFoundError):
        pathing.config_path()


def test_load_project_config_returns_empty_without_config(monkeypatch, tmp_path: Path):
    _clear_pathing_caches()
    monkeypatch.delenv("NERVOS_BRAIN_CONFIG", raising=False)
    monkeypatch.setattr(pathing, "project_root", lambda: tmp_path)
    pathing.config_path.cache_clear()
    pathing.load_project_config.cache_clear()

    assert pathing.config_path() is None
    assert pathing.load_project_config() == {}
