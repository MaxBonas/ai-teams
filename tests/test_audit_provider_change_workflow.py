from scripts.audit_provider_change_workflow import build_report


def test_provider_change_workflow_audit_is_green() -> None:
    report = build_report()

    assert report["summary"] == {
        "passed": 9,
        "total": 9,
        "workflow_ready": True,
    }
    assert report["scope"] == {
        "temporary_sqlite_only": True,
        "network_attempted": False,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "commands_executed": False,
        "updates_executed": False,
        "routing_mutated": False,
    }
