from __future__ import annotations

from pathlib import Path


CASE = Path(__file__).resolve().parent.parent / "benchmarks" / "accessible_checkout_form"


def test_accessible_checkout_benchmark_is_well_formed() -> None:
    goal = (CASE / "goal.md").read_text(encoding="utf-8")
    hidden = (CASE / "hidden_tests" / "test_hidden_acceptance.py").read_text(encoding="utf-8")
    assert "validateCheckout" in goal
    assert "role=\"alert\"" in goal
    assert "prefers-reduced-motion" in goal
    assert hidden.count("def test_") >= 10
    assert "goal.md" not in hidden


def test_selector_matrix_contains_the_empirical_case() -> None:
    import json

    cases = json.loads((CASE.parent / "profile_selector_cases.json").read_text(encoding="utf-8"))
    case = next(item for item in cases if item["id"] == "accessible_checkout_redesign")
    assert case["family"] == "frontend"
    assert case["expected"] == "full_team"
    assert case["independent_verification"] is True
    assert case["reversible"] is True
