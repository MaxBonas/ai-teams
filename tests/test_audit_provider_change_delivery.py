from scripts.audit_provider_change_delivery import build_report


def test_provider_change_delivery_audit_is_hermetic_and_green() -> None:
    report = build_report()

    assert report["ok"] is True
    assert report["counts"] == {"passed": 12, "total": 12}
    assert report["scope"]["network_used"] is False
    assert report["scope"]["real_secrets_read"] is False
