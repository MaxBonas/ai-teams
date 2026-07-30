from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.audit_model_calibration_gate_board import (
    _fixture,
    build_audit,
    validate_audit,
)


def test_audit_closes_all_gate_invariants_without_inference() -> None:
    report = build_audit(_fixture())

    assert report["summary"] == {
        "promotion_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["board"]["stage_sequence"] == [
        "configuration_auth",
        "catalog_version",
        "adapter_health",
        "contract_probe",
        "role_canary",
        "multi_family_calibration",
        "promotion",
    ]
    assert report["scope"]["inference_attempted"] is False
    assert report["scope"]["live_catalog_identifiers_emitted"] is False


def test_audit_validator_fails_closed_on_tampering() -> None:
    report = build_audit(_fixture())
    tampered = deepcopy(report)
    tampered["checks"].pop("red_adapter_cannot_bypass")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
