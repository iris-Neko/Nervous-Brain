from __future__ import annotations

from pathlib import Path

import pytest

from nervos_brain.tool_runtime.feedback import (
    FeedbackJsonlStore,
    build_csat_callback_data,
    parse_csat_callback_data,
)


def test_build_and_parse_csat_callback_data():
    payload = build_csat_callback_data("tg-123", 4)
    assert payload == "csat:tg-123:4"

    parsed = parse_csat_callback_data(payload)
    assert parsed.request_id == "tg-123"
    assert parsed.score == 4


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "rate:tg-123:4",
        "csat::4",
        "csat:tg-123:0",
        "csat:tg-123:6",
        "csat:tg-123:nope",
    ],
)
def test_parse_csat_callback_rejects_invalid_payload(payload: str):
    with pytest.raises(ValueError):
        parse_csat_callback_data(payload)


def test_feedback_store_marks_low_score_bad_case(tmp_path: Path):
    store = FeedbackJsonlStore(tmp_path / "feedback.jsonl")
    row = store.append(
        {
            "request_id": "tg-1",
            "platform": "telegram",
            "chat_id": "-100",
            "user_id": "42",
            "score": 3,
            "comment": "",
            "created_ts_ms": 1000,
        }
    )

    assert row["kind"] == "csat"
    assert row["is_bad_case"] is True
    assert store.iter_records()[0]["is_bad_case"] is True


def test_feedback_store_deduplicates_same_user_request(tmp_path: Path):
    store = FeedbackJsonlStore(tmp_path / "feedback.jsonl")
    first = store.append(
        {
            "request_id": "tg-1",
            "platform": "telegram",
            "chat_id": "-100",
            "user_id": "42",
            "score": 5,
        }
    )
    duplicate = store.append(
        {
            "request_id": "tg-1",
            "platform": "telegram",
            "chat_id": "-100",
            "user_id": "42",
            "score": 1,
        }
    )

    assert first["score"] == 5
    assert duplicate["score"] == 5
    assert duplicate["is_duplicate_rating"] is True
    assert len(store.iter_records()) == 1


def test_feedback_store_summary_counts_answers_scores_and_bad_cases(tmp_path: Path):
    store = FeedbackJsonlStore(tmp_path / "feedback.jsonl")
    store.append_answer(
        {
            "request_id": "tg-1",
            "chat_id": "-100",
            "user_id": "42",
            "trace_summary": "tools=s1:qdrant_search=ok:2",
            "tool_calls": 1,
            "evidence_count": 2,
            "final_text_preview": "answer one",
            "created_ts_ms": 1000,
        }
    )
    store.append_answer(
        {
            "request_id": "tg-2",
            "chat_id": "-100",
            "user_id": "43",
            "created_ts_ms": 2000,
        }
    )
    store.append(
        {
            "request_id": "tg-1",
            "platform": "telegram",
            "chat_id": "-100",
            "user_id": "42",
            "score": 2,
            "created_ts_ms": 3000,
        }
    )
    store.append(
        {
            "request_id": "tg-2",
            "platform": "telegram",
            "chat_id": "-100",
            "user_id": "43",
            "score": 5,
            "created_ts_ms": 4000,
        }
    )
    store.append_comment(
        {
            "request_id": "tg-2",
            "chat_id": "-100",
            "user_id": "43",
            "comment": "good but terse",
            "created_ts_ms": 5000,
        }
    )

    summary = store.summary()
    assert summary["total_questions"] == 2
    assert summary["total_ratings"] == 2
    assert summary["rating_coverage"] == 1.0
    assert summary["average_csat"] == 3.5
    assert summary["score_counts"] == {"1": 0, "2": 1, "3": 0, "4": 0, "5": 1}
    assert summary["comment_count"] == 1
    assert summary["bad_case_count"] == 1
    assert summary["bad_cases"][0]["request_id"] == "tg-1"


def test_latest_answer_returns_last_answer_for_request(tmp_path: Path):
    store = FeedbackJsonlStore(tmp_path / "feedback.jsonl")
    store.append_answer({"request_id": "tg-1", "final_text_preview": "old"})
    store.append_answer({"request_id": "tg-1", "final_text_preview": "new"})

    assert store.latest_answer("tg-1")["final_text_preview"] == "new"
