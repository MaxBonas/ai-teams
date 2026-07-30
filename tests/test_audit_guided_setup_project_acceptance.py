from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_project_acceptance import (
    build_audit,
    validate_audit,
)


def test_guided_setup_project_acceptance_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "project_setup_acceptance_ready": True,
        "check_count": 13,
        "passed_count": 13,
    }
    assert report["scope"]["user_projects_mutated"] is False
    assert report["scope"]["remote_quota_consumed"] is False


def test_guided_setup_project_acceptance_rejects_matrix_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("stale_revision_is_rejected")

    with pytest.raises(ValueError, match="matrix drift"):
        validate_audit(tampered)


def test_guided_setup_project_acceptance_rejects_evidence_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["evidence"]["new_research_project"]["queued_wakeups"] = 99

    with pytest.raises(ValueError, match="evidence drift"):
        validate_audit(tampered)
