from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_orientation_study_prereg import validate_preregistration

ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "benchmarks" / "frontend_orientation" / "orientation-study-prereg-v1.json"
TEMPLATE = ROOT / "benchmarks" / "frontend_orientation" / "orientation-study-result-template-v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_orientation_study_is_preregistered_before_observation() -> None:
    prereg = _load(PREREG)
    template = _load(TEMPLATE)

    assert validate_preregistration(prereg, template) == []
    assert prereg["status"] == "preregistered_no_sessions_observed"
    assert prereg["results_must_be_written_to_separate_receipt"] is True
    assert "results" not in prereg


def test_validator_rejects_post_hoc_threshold_or_private_field() -> None:
    prereg = _load(PREREG)
    template = _load(TEMPLATE)
    prereg["gates"]["inbox_min_unassisted_completions"] = 3
    prereg["observer_fields"].append("transcript")

    errors = validate_preregistration(prereg, template)

    assert "inbox_gate_changed" in errors
    assert "observer_fields_include_private_content" in errors


def test_validator_requires_unambiguous_participant_flow_rows() -> None:
    prereg = _load(PREREG)
    template = _load(TEMPLATE)
    prereg["observer_fields"].remove("flow")
    prereg["observer_row_contract"]["unique_key"] = ["participant_code"]

    errors = validate_preregistration(prereg, template)

    assert "observer_rubric_incomplete" in errors
    assert "observer_row_unique_key_invalid" in errors
