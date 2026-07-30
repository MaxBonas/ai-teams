from pathlib import Path

from aiteam.model_automation_enforcement import _audit_consumer_wiring
from aiteam.model_selection import candidate_is_automation_eligible


def test_automation_gate_is_stricter_than_owner_selection() -> None:
    manual_only = {
        "owner_selectable": True,
        "selection_score": {
            "auto_eligible": False,
            "auto_ineligible_reasons": ["gate:calibrated:no"],
        },
    }
    calibrated = {
        "owner_selectable": True,
        "selection_score": {"auto_eligible": True},
    }

    assert candidate_is_automation_eligible(manual_only) is False
    assert candidate_is_automation_eligible(calibrated) is True
    assert candidate_is_automation_eligible(None) is False


def test_defaults_hiring_and_recovery_share_the_automation_gate() -> None:
    failures: list[dict] = []

    report = _audit_consumer_wiring(
        Path(__file__).resolve().parents[1],
        failures,
    )

    assert report == {"checks": 5, "passed": 5}
    assert failures == []
