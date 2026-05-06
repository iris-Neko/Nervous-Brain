from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_CATEGORIES = {
    "solution_recommendation",
    "development_guidance",
    "troubleshooting",
}


def default_week7_eval_path() -> Path:
    return Path(__file__).resolve().parents[3] / "evaluation" / "week7_multiturn_eval.jsonl"


def validate_eval_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(case.get("case_id", "")).strip():
        errors.append("missing case_id")
    if case.get("category") not in VALID_CATEGORIES:
        errors.append("invalid category")

    conversation = case.get("conversation")
    if not isinstance(conversation, list) or len(conversation) < 2:
        errors.append("conversation must contain at least two turns")
    else:
        for idx, turn in enumerate(conversation):
            if not isinstance(turn, dict):
                errors.append(f"turn {idx} is not an object")
                continue
            if turn.get("role") not in {"user", "assistant"}:
                errors.append(f"turn {idx} has invalid role")
            if not str(turn.get("content", "")).strip():
                errors.append(f"turn {idx} missing content")

    success = case.get("success_criteria")
    if not isinstance(success, list) or not success:
        errors.append("success_criteria must be a non-empty list")

    signals = case.get("expected_signals", {})
    if not isinstance(signals, dict):
        errors.append("expected_signals must be an object")
    return errors


def load_eval_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else default_week7_eval_path()
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            case = json.loads(text)
            if not isinstance(case, dict):
                failures.append(f"line {line_no}: case must be an object")
                continue
            errors = validate_eval_case(case)
            if errors:
                failures.append(f"line {line_no}: {'; '.join(errors)}")
                continue
            rows.append(case)
    if failures:
        raise ValueError("invalid evaluation dataset:\n" + "\n".join(failures))
    return rows
