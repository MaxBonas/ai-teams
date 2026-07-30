from __future__ import annotations

from scripts.audit_provider_change_persistence import build_report


def test_provider_change_persistence_audit_is_green() -> None:
    report = build_report()

    assert report["summary"] == {
        "passed": 9,
        "total": 9,
        "persistence_ready": True,
    }
    assert report["counts"] == {
        "components": 42,
        "safe_readers": 23,
        "scheduled_tick": 3,
        "events": 1,
        "triggers": 1,
    }
    assert report["scope"] == {
        "temporary_sqlite_only": True,
        "network_attempted": False,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "updates_attempted": False,
        "routing_mutated": False,
    }
