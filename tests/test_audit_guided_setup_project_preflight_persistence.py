from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_project_preflight_persistence import (
    build_audit,
    validate_audit,
)


def test_guided_setup_project_preflight_persistence_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "persistence_contract_ready": True,
        "check_count": 6,
        "passed_count": 6,
    }
    assert report["scope"]["user_projects_mutated"] is False
    assert report["scope"]["remote_calls"] is False


def test_persistence_audit_rejects_matrix_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("evidence_is_session_confined")

    with pytest.raises(ValueError, match="matrix drift"):
        validate_audit(tampered)


def test_persistence_audit_rejects_evidence_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["evidence"]["row_counts"]["receipts"] = 2

    with pytest.raises(ValueError, match="evidence drift"):
        validate_audit(tampered)
