from scripts.benchmark_opencode_server_resilience import summarize_sdk_result


def _sdk_result(*, schema_ok: bool = False) -> dict:
    return {
        "sdk_version": "1.18.4",
        "model": "opencode/deepseek-v4-flash-free",
        "gates": {
            "busy_observed_before_abort": True,
            "server_abort_acknowledged": True,
            "idle_after_abort": True,
            "sdk_health_after": True,
            "recovery_prompt_completed": True,
            "recovery_marker_exact": True,
            "json_schema_accepted": schema_ok,
            "session_deleted": True,
        },
    }


def test_summary_separates_cancellation_recovery_from_unfinished_gates() -> None:
    report = summarize_sdk_result(
        _sdk_result(), cli_version="1.18.4", server_teardown_ok=True
    )

    assert report["gates"]["official_sdk_exercised"] is True
    assert report["gates"]["cancellation_tested"] is True
    assert report["gates"]["busy_abort_recovery_tested"] is True
    assert report["gates"]["json_schema_accepted"] is False
    assert report["gates"]["true_hang_fault_injection_tested"] is False
    assert report["gates"]["mcp_health_tested"] is False
    assert report["production_activation_allowed"] is False


def test_summary_fails_cancellation_if_busy_was_never_observed() -> None:
    result = _sdk_result(schema_ok=True)
    result["gates"]["busy_observed_before_abort"] = False

    report = summarize_sdk_result(result, cli_version="1.18.4", server_teardown_ok=True)

    assert report["gates"]["cancellation_tested"] is False
    assert report["production_activation_allowed"] is False
