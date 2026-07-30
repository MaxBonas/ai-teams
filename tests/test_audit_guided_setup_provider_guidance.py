from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_guided_setup_provider_guidance import (
    build_audit,
    validate_audit,
)


def test_provider_guidance_audit_is_green() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "provider_guidance_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["commands_executed"] is False
    assert report["scope"]["secrets_read"] is False


def test_provider_guidance_audit_rejects_tampering() -> None:
    report = build_audit(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"].pop("all_actions_are_manual")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_audit(tampered)
