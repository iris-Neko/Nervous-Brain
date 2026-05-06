from __future__ import annotations

import logging
from pathlib import Path

from nervos_brain.logging_system import log_request_context, setup_logging


def _flush_handlers() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()


def test_setup_logging_writes_rotating_file(tmp_path: Path):
    out = setup_logging(
        service_name="unit_test_logger",
        debug=True,
        log_dir=tmp_path,
        file_logging=True,
        force_reconfigure=True,
    )
    assert out["configured"] is True
    log_file = tmp_path / "unit_test_logger.log"

    logger = logging.getLogger("nervos_brain.test")
    with log_request_context("req-abc"):
        logger.info("hello logging")
    _flush_handlers()

    assert log_file.exists()
    text = log_file.read_text(encoding="utf-8")
    assert "hello logging" in text
    assert "req-abc" in text


def test_setup_logging_debug_changes_root_level(tmp_path: Path):
    setup_logging(
        service_name="unit_test_level_info",
        debug=False,
        level="INFO",
        log_dir=tmp_path,
        file_logging=False,
        force_reconfigure=True,
    )
    assert logging.getLogger().level == logging.INFO

    setup_logging(
        service_name="unit_test_level_debug",
        debug=True,
        log_dir=tmp_path,
        file_logging=False,
        force_reconfigure=True,
    )
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_sets_quiet_logger_levels(tmp_path: Path):
    out = setup_logging(
        service_name="unit_test_quiet",
        debug=False,
        log_dir=tmp_path,
        file_logging=False,
        force_reconfigure=True,
    )
    assert out["third_party_level"] == "WARNING"
    assert "httpx" in out["quiet_loggers"]
    assert logging.getLogger("httpx").level == logging.WARNING
