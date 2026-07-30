from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_project_preflight import (
    build_audit,
    validate_audit,
)


def test_guided_setup_project_preflight_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "preflight_contract_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["commands_executed"] is False
    assert report["scope"]["project_tests_executed"] is False
    assert report["scope"]["remote_quota_consumed"] is False


def test_guided_setup_project_preflight_audit_rejects_matrix_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("unsafe_path_is_a_hard_gate")

    with pytest.raises(ValueError, match="matrix drift"):
        validate_audit(tampered)


def test_guided_setup_project_preflight_audit_rejects_evidence_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["evidence"]["objective_kinds"].append("unknown")

    with pytest.raises(ValueError, match="evidence drift"):
        validate_audit(tampered)
