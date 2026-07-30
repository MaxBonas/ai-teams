from scripts.audit_provider_change_detection import build_report


def test_provider_change_detection_audit_is_green() -> None:
    report = build_report()

    assert report["summary"] == {
        "cases": 19,
        "passed": 19,
        "checks_passed": 8,
        "checks_total": 8,
        "detectors_ready": True,
    }
    assert all(report["checks"].values())
    assert report["scope"] == {
        "fixtures_only": True,
        "read_only": True,
        "network_attempted": False,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "updates_attempted": False,
    }
