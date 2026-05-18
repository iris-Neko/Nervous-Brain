from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KNOWN_CATEGORIES = {
    "solution_recommendation",
    "development_guidance",
    "troubleshooting",
    "bad_case",
    "regression",
    "retrieval_gap",
    "answer_quality",
    "formatting",
    "ecosystem_research",
}


def evaluation_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "evaluation"


def default_ai_generated_eval_path() -> Path:
    return evaluation_dir() / "ai_generated_cases.jsonl"


def default_human_collected_eval_path() -> Path:
    return evaluation_dir() / "human_collected_cases.jsonl"


def default_eval_paths() -> list[Path]:
    return [default_ai_generated_eval_path(), default_human_collected_eval_path()]


def validate_eval_case(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(case.get("case_id", "")).strip():
        errors.append("missing case_id")
    if not str(case.get("category", "")).strip():
        errors.append("missing category")

    conversation = case.get("conversation")
    if not isinstance(conversation, list) or len(conversation) < 1:
        errors.append("conversation must contain at least one turn")
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
    target = Path(path) if path is not None else default_ai_generated_eval_path()
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


def load_all_eval_cases(paths: list[str | Path] | None = None) -> list[dict[str, Any]]:
    targets = [Path(path) for path in paths] if paths is not None else default_eval_paths()
    rows: list[dict[str, Any]] = []
    for target in targets:
        if not target.exists():
            continue
        rows.extend(load_eval_cases(target))
    return rows
