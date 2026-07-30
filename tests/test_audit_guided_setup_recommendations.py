from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_recommendations import (
    build_audit,
    validate_audit,
)


def test_guided_setup_recommendations_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "recommendations_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["inference_attempted"] is False


def test_guided_setup_recommendations_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("passed_install_is_not_recommended")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
