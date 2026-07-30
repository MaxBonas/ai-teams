from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_needs import build_audit, validate_audit


def test_guided_setup_needs_audit_closes_contract() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "needs_interview_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["inference_attempted"] is False
    assert report["scope"]["secrets_read"] is False


def test_guided_setup_needs_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("local_requires_owner_opt_in")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
