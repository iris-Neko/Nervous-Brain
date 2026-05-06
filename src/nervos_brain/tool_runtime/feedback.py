"""CSAT feedback and BadCase storage helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CSAT_CALLBACK_PREFIX = "csat:"
BADCASE_SCORE_THRESHOLD = 3


@dataclass(frozen=True)
class CSATCallback:
    request_id: str
    score: int


def build_csat_callback_data(request_id: str, score: int) -> str:
    request_id = str(request_id).strip()
    if not request_id:
        raise ValueError("request_id is required")
    if score < 1 or score > 5:
        raise ValueError("score must be in 1..5")
    return f"{CSAT_CALLBACK_PREFIX}{request_id}:{score}"


def parse_csat_callback_data(data: str) -> CSATCallback:
    text = str(data or "").strip()
    if not text.startswith(CSAT_CALLBACK_PREFIX):
        raise ValueError("not a csat callback")
    rest = text[len(CSAT_CALLBACK_PREFIX) :]
    request_id, sep, raw_score = rest.rpartition(":")
    if not sep or not request_id.strip():
        raise ValueError("invalid csat callback payload")
    try:
        score = int(raw_score)
    except ValueError as exc:
        raise ValueError("score must be an integer") from exc
    if score < 1 or score > 5:
        raise ValueError("score must be in 1..5")
    return CSATCallback(request_id=request_id.strip(), score=score)


class FeedbackJsonlStore:
    """Append-only feedback storage for beta testing.

    The store keeps both answer metadata rows and user feedback rows in one
    JSONL file so weekly review can count total answered questions and join a
    CSAT score back to the original trace summary by request_id.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append a CSAT rating row.

        Repeated clicks from the same user for the same request are treated as
        idempotent: the first score wins and later duplicates are not written.
        """
        row = dict(record)
        request_id = str(row.get("request_id", "")).strip()
        if not request_id:
            raise ValueError("request_id is required")
        score = int(row.get("score", 0))
        if score < 1 or score > 5:
            raise ValueError("score must be in 1..5")
        row["request_id"] = request_id
        row["score"] = score
        row["kind"] = "csat"
        row.setdefault("created_ts_ms", int(time.time() * 1000))
        row["is_bad_case"] = score <= BADCASE_SCORE_THRESHOLD

        existing = self._find_existing_csat(row)
        if existing is not None:
            duplicate = dict(existing)
            duplicate["is_duplicate_rating"] = True
            return duplicate

        self._append_row(row)
        return row

    def append_answer(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append one bot-answer metadata row for later CSAT joins."""
        row = dict(record)
        request_id = str(row.get("request_id", "")).strip()
        if not request_id:
            raise ValueError("request_id is required")
        row["request_id"] = request_id
        row["kind"] = "answer"
        row.setdefault("platform", "telegram")
        row.setdefault("created_ts_ms", int(time.time() * 1000))
        row.setdefault("trace_summary", "")
        row.setdefault("tool_calls", 0)
        row.setdefault("evidence_count", 0)
        row.setdefault("final_text_preview", "")
        self._append_row(row)
        return row

    def append_comment(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append a text-only feedback row."""
        row = dict(record)
        request_id = str(row.get("request_id", "")).strip()
        if not request_id:
            raise ValueError("request_id is required")
        row["request_id"] = request_id
        row["kind"] = "comment"
        row.setdefault("platform", "telegram")
        row.setdefault("created_ts_ms", int(time.time() * 1000))
        row.setdefault("comment", "")
        row.setdefault("is_bad_case", False)
        self._append_row(row)
        return row

    def latest_answer(self, request_id: str) -> dict[str, Any] | None:
        request_id = str(request_id or "").strip()
        if not request_id:
            return None
        answer: dict[str, Any] | None = None
        for row in self.iter_records():
            if row.get("kind") == "answer" and str(row.get("request_id", "")) == request_id:
                answer = row
        return answer

    def _append_row(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _find_existing_csat(self, row: dict[str, Any]) -> dict[str, Any] | None:
        dedup_key = _csat_dedup_key(row)
        if dedup_key is None:
            return None
        for existing in self.iter_records():
            if existing.get("kind") != "csat":
                continue
            if _csat_dedup_key(existing) == dedup_key:
                return existing
        return None

    def iter_records(
        self,
        *,
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                text = raw.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _int_or_none(row.get("created_ts_ms"))
                if since_ts_ms is not None and ts is not None and ts < since_ts_ms:
                    continue
                if until_ts_ms is not None and ts is not None and ts > until_ts_ms:
                    continue
                rows.append(row)
        return rows

    def summary(
        self,
        *,
        since_ts_ms: int | None = None,
        until_ts_ms: int | None = None,
        bad_case_limit: int = 20,
    ) -> dict[str, Any]:
        rows = self.iter_records(since_ts_ms=since_ts_ms, until_ts_ms=until_ts_ms)
        answer_rows = [row for row in rows if row.get("kind") == "answer"]
        rating_rows = [row for row in rows if row.get("kind") == "csat"]
        comment_rows = [row for row in rows if row.get("kind") == "comment"]
        scores = [
            int(row["score"])
            for row in rating_rows
            if _int_or_none(row.get("score")) is not None
        ]
        all_bad_cases = [row for row in rating_rows if bool(row.get("is_bad_case"))]
        bad_cases = sorted(
            all_bad_cases,
            key=lambda row: int(row.get("created_ts_ms", 0) or 0),
            reverse=True,
        )[: max(0, int(bad_case_limit))]
        score_counts = {str(score): 0 for score in range(1, 6)}
        for score in scores:
            if 1 <= score <= 5:
                score_counts[str(score)] += 1
        total_questions = len(answer_rows)
        rating_count = len(rating_rows)
        return {
            "total_feedback": len(rows),
            "total_questions": total_questions,
            "total_ratings": rating_count,
            "rating_coverage": round(rating_count / total_questions, 3)
            if total_questions
            else 0.0,
            "average_csat": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "score_counts": score_counts,
            "comment_count": len(comment_rows),
            "bad_case_count": len(all_bad_cases),
            "bad_cases": bad_cases,
        }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _csat_dedup_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    request_id = str(row.get("request_id", "")).strip()
    user_id = str(row.get("user_id", "")).strip()
    if not request_id or not user_id:
        return None
    return (
        str(row.get("platform", "telegram")).strip() or "telegram",
        str(row.get("chat_id", "")).strip(),
        user_id,
        request_id,
    )
