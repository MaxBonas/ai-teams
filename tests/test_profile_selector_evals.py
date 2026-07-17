from __future__ import annotations

from scripts.profile_selector_evals import evaluate_cases


def test_profile_selector_calibration_cases_have_no_unsafe_solo() -> None:
    report = evaluate_cases()
    assert report["cases"] >= 28
    assert report["unsafe_solo"] == 0
    assert report["passes_safety_gate"] is True
    assert report["accuracy"] == 1.0
    assert set(report["by_family"]) >= {
        "maintenance", "backend", "frontend", "security", "data", "architecture", "incomplete",
    }
    assert all(bucket["cases"] >= 4 for bucket in report["by_family"].values())
