from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_project_preflight_ui import (
    build_audit,
    validate_audit,
)


def test_guided_setup_project_preflight_ui_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "ui_contract_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["user_projects_mutated"] is False
    assert report["scope"]["remote_calls"] is False


def test_preflight_ui_audit_rejects_matrix_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("commit_requires_durable_go")

    with pytest.raises(ValueError, match="matrix drift"):
        validate_audit(tampered)


def test_preflight_ui_audit_rejects_evidence_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["evidence"]["authority_boundary"] = "La UI decide."

    with pytest.raises(ValueError, match="evidence drift"):
        validate_audit(tampered)
