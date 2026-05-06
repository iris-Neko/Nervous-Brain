from __future__ import annotations

from nervos_brain.evaluation import default_week7_eval_path, load_eval_cases


def test_week7_eval_dataset_loads():
    cases = load_eval_cases(default_week7_eval_path())
    assert len(cases) >= 6
    categories = {case["category"] for case in cases}
    assert categories == {
        "solution_recommendation",
        "development_guidance",
        "troubleshooting",
    }


def test_week7_eval_cases_are_multi_turn():
    cases = load_eval_cases(default_week7_eval_path())
    assert all(len(case["conversation"]) >= 2 for case in cases)
