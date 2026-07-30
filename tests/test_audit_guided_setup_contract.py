from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_contract import build_audit, validate_audit


def test_guided_setup_audit_closes_contract() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "contract_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["inference_attempted"] is False
    assert report["contracts"] == {
        "machine_onboarding": {"step_count": 6},
        "project_setup": {"step_count": 7},
        "installation_repair": {"step_count": 4},
    }


def test_guided_setup_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("api_is_wired")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
