from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_provider_evidence import (
    build_audit,
    validate_audit,
)


def test_provider_evidence_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "provider_evidence_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["canonical_api_wired"] is True
    assert report["scope"]["remote_probes_executed"] is False


def test_provider_evidence_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("structured_output_is_required")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
