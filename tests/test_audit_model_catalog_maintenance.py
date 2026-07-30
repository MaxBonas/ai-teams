from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.audit_model_catalog_maintenance import build_audit, validate_audit
from tests.test_model_catalog_maintenance import _read_model


def test_audit_covers_all_dimensions_and_monthly_cadence() -> None:
    report = build_audit(_read_model())

    assert report["summary"] == {
        "cadence_ready": True,
        "dimensions_ready": True,
        "promotion_ready": True,
    }
    assert {row["dimension"] for row in report["dimensions"]} == {
        "model",
        "cli",
        "price",
        "quota",
        "prompt",
        "tool",
        "contract",
    }
    assert report["cadence"]["same_month_idempotent"] is True
    assert report["scope"]["retention"] == "append_only_no_age_deletion"


def test_audit_validator_fails_closed_on_tampering() -> None:
    report = build_audit(_read_model())
    tampered = deepcopy(report)
    tampered["dimensions"].pop()

    with pytest.raises(ValueError, match="dimension coverage drift"):
        validate_audit(tampered)
