from __future__ import annotations

import json
from pathlib import Path


CASE = Path(__file__).resolve().parent.parent / "benchmarks" / "inventory_snapshot_diff"


def test_inventory_snapshot_benchmark_is_well_formed() -> None:
    goal = (CASE / "goal.md").read_text(encoding="utf-8")
    hidden = (CASE / "hidden_tests" / "test_hidden_acceptance.py").read_text(encoding="utf-8")
    assert "reconcile_inventory" in goal
    assert "quantity_delta" in goal
    assert "no modifica" in goal
    assert hidden.count("def test_") >= 10
    assert "goal.md" not in hidden


def test_selector_matrix_labels_inventory_diff_as_bounded_reversible_data_work() -> None:
    cases = json.loads((CASE.parent / "profile_selector_cases.json").read_text(encoding="utf-8"))
    case = next(item for item in cases if item["id"] == "inventory_snapshot_diff")
    assert case == {
        "id": "inventory_snapshot_diff",
        "family": "data",
        "difficulty": "medium",
        "expected": "solo_lead",
        "criticality": "medium",
        "ambiguity": "low",
        "independent_verification": False,
        "parallel_workstreams": 1,
        "reversible": True,
    }
