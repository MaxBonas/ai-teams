from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_coverage import build_audit, validate_audit


def test_guided_setup_coverage_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "coverage_contract_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["inference_attempted"] is False


def test_guided_setup_coverage_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("api_cost_remains_metered")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
