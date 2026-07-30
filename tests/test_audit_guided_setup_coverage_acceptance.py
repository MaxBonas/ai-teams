from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_coverage_acceptance import (
    build_audit,
    validate_audit,
)


def test_guided_setup_coverage_acceptance_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "coverage_acceptance_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["remote_quota_consumed"] is False


def test_guided_setup_coverage_acceptance_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("coverage_matches_canonical_selector_gate")

    with pytest.raises(ValueError, match="matrix drift"):
        validate_audit(tampered)
