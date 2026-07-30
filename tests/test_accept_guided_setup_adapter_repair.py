from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.accept_guided_setup_adapter_repair import (
    build_acceptance,
    validate_acceptance,
)


def test_adapter_repair_acceptance_is_green() -> None:
    report = build_acceptance(Path(__file__).resolve().parents[1])

    assert report["summary"] == {
        "repair_acceptance_ready": True,
        "scenario_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["commands_executed"] is False
    assert report["scope"]["remote_quota_consumed"] is False


def test_adapter_repair_acceptance_rejects_tampering() -> None:
    report = build_acceptance(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["scenarios"].pop("valid_personal_api")

    with pytest.raises(ValueError, match="coverage drift"):
        validate_acceptance(tampered)
