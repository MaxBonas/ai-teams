from scripts.audit_provider_change_notifications import build_report


def test_provider_change_notification_audit_passes_all_invariants() -> None:
    report = build_report()

    assert report["ok"] is True
    assert report["counts"] == {"passed": 10, "total": 10}
    assert report["scope"]["external_notifications_sent"] is False
