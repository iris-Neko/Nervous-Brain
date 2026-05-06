"""Centralized logging setup for Nervos Brain runtimes.

Features:
  - Unified root logger setup (console + rotating file)
  - Configurable via config.yaml[logging]
  - Optional debug mode
  - Request-id context propagation
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nervos_brain_request_id", default="-"
)
_setup_lock = Lock()
_configured = False
_DEFAULT_QUIET_LOGGERS = (
    "urllib3",
    "httpx",
    "httpcore",
    "qdrant_client",
    "litellm",
    "discord",
    "aiogram",
    "asyncio",
    "sqlalchemy.engine",
)


class RequestIdFilter(logging.Filter):
    """Inject request_id from context var into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Simple JSON formatter for production ingestion pipelines."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _load_project_logging_cfg() -> dict[str, Any]:
    candidates = [
        Path.cwd() / "config.yaml",
        Path(__file__).resolve().parents[2] / "config.yaml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            import yaml

            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, dict):
                return {}
            section = raw.get("logging", {})
            return dict(section) if isinstance(section, dict) else {}
        except Exception:
            return {}
    return {}


def _to_level(value: str | int | None, default: int = logging.INFO) -> int:
    if isinstance(value, int):
        return value
    if not value:
        return default
    text = str(value).strip().upper()
    mapped = logging.getLevelNamesMapping().get(text)
    if isinstance(mapped, int):
        return mapped
    return default


def setup_logging(
    *,
    service_name: str = "nervos_brain",
    debug: bool = False,
    level: str | int | None = None,
    log_dir: str | Path | None = None,
    json_logs: bool | None = None,
    file_logging: bool | None = None,
    force_reconfigure: bool = False,
) -> dict[str, Any]:
    """Configure root logging once and return effective settings."""

    global _configured
    with _setup_lock:
        if _configured and not force_reconfigure:
            return {"configured": True}

        cfg = _load_project_logging_cfg()
        effective_level = (
            logging.DEBUG
            if debug
            else _to_level(level if level is not None else cfg.get("level"), logging.INFO)
        )
        third_party_level = _to_level(cfg.get("third_party_level"), logging.WARNING)

        effective_json = (
            bool(json_logs)
            if json_logs is not None
            else bool(cfg.get("json", False))
        )
        effective_file = (
            bool(file_logging)
            if file_logging is not None
            else bool(cfg.get("file", True))
        )

        dir_value = log_dir if log_dir is not None else cfg.get("log_dir", "data/logs")
        log_dir_path = Path(dir_value).expanduser().resolve()
        max_bytes = int(cfg.get("max_bytes", 10 * 1024 * 1024))
        backup_count = int(cfg.get("backup_count", 5))

        root = logging.getLogger()
        if root.handlers:
            for h in list(root.handlers):
                root.removeHandler(h)
                with contextlib.suppress(Exception):
                    h.close()

        root.setLevel(effective_level)
        request_filter = RequestIdFilter()

        if effective_json:
            formatter: logging.Formatter = JsonLogFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] [req=%(request_id)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        console = logging.StreamHandler()
        console.setLevel(effective_level)
        console.setFormatter(formatter)
        console.addFilter(request_filter)
        root.addHandler(console)

        log_path = None
        if effective_file:
            log_dir_path.mkdir(parents=True, exist_ok=True)
            log_path = log_dir_path / f"{service_name}.log"
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=max(1024, max_bytes),
                backupCount=max(1, backup_count),
                encoding="utf-8",
            )
            file_handler.setLevel(effective_level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(request_filter)
            root.addHandler(file_handler)

        quiet_loggers = list(_DEFAULT_QUIET_LOGGERS)
        extra_quiet = cfg.get("quiet_loggers", [])
        if isinstance(extra_quiet, list):
            quiet_loggers.extend(str(name) for name in extra_quiet if name)

        for lib_name in dict.fromkeys(quiet_loggers):
            logging.getLogger(lib_name).setLevel(third_party_level)

        _configured = True
        return {
            "configured": True,
            "level": logging.getLevelName(effective_level),
            "file_logging": effective_file,
            "log_file": str(log_path) if log_path else "",
            "json_logs": effective_json,
            "third_party_level": logging.getLevelName(third_party_level),
            "quiet_loggers": list(dict.fromkeys(quiet_loggers)),
        }


@contextlib.contextmanager
def log_request_context(request_id: str | None) -> Iterator[None]:
    """Bind request_id into logging context for current execution scope."""
    token = _request_id_var.set(request_id or "-")
    try:
        yield
    finally:
        _request_id_var.reset(token)
