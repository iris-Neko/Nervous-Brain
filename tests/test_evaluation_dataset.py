from __future__ import annotations

from nervos_brain.evaluation import (
    default_ai_generated_eval_path,
    default_human_collected_eval_path,
    load_all_eval_cases,
    load_eval_cases,
)


def test_ai_generated_eval_dataset_loads():
    cases = load_eval_cases(default_ai_generated_eval_path())
    assert len(cases) >= 6
    categories = {case["category"] for case in cases}
    assert categories >= {
        "solution_recommendation",
        "development_guidance",
        "troubleshooting",
    }


def test_ai_generated_eval_cases_are_dialogue_cases():
    cases = load_eval_cases(default_ai_generated_eval_path())
    assert all(len(case["conversation"]) >= 1 for case in cases)


def test_human_collected_eval_dataset_loads():
    cases = load_eval_cases(default_human_collected_eval_path())
    assert len(cases) >= 4
    assert {case["category"] for case in cases} >= {
        "answer_quality",
        "regression",
        "retrieval_gap",
    }


def test_all_eval_cases_include_ai_generated_seed_cases():
    cases = load_all_eval_cases()
    assert len(cases) >= 6
