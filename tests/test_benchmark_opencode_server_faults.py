from scripts.benchmark_opencode_server_faults import summarize


def _hang() -> dict:
    return {
        "gates": {
            "native_server_suspended": True,
            "port_open_while_hung": True,
            "health_times_out_while_hung": True,
            "faulted_server_stopped": True,
            "same_port_restarted": True,
            "health_after_restart": True,
            "same_session_recovered": True,
            "session_idle_after_restart": True,
            "recovery_marker_exact": True,
            "session_deleted": True,
            "server_teardown_guaranteed": True,
        }
    }


def _mcp() -> dict:
    return {
        "gates": {
            "mcp_process_started": True,
            "mcp_connected": True,
            "mcp_initialize_observed": True,
            "mcp_tools_list_observed": True,
            "approved_tool_listed_by_mcp": True,
            "unapproved_tool_listed_by_mcp": True,
            "namespace_denied_by_default": True,
            "approved_tool_allowed_exactly": True,
            "unapproved_tool_not_allowed": True,
            "server_teardown_guaranteed": True,
            "mcp_process_reaped": True,
        }
    }


def test_summary_requires_hang_recovery_mcp_health_and_process_cleanup() -> None:
    report = summarize(
        hang=_hang(),
        mcp=_mcp(),
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
    )

    assert all(report["gates"].values())
    assert report["production_activation_allowed"] is False


def test_summary_does_not_confuse_global_health_with_mcp_health() -> None:
    mcp = _mcp()
    mcp["gates"]["mcp_connected"] = False

    report = summarize(
        hang=_hang(),
        mcp=mcp,
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
    )

    assert report["gates"]["mcp_health_tested"] is False
    assert report["production_activation_allowed"] is False


def test_summary_requires_unapproved_tool_to_remain_unallowed() -> None:
    mcp = _mcp()
    mcp["gates"]["unapproved_tool_not_allowed"] = False

    report = summarize(
        hang=_hang(),
        mcp=mcp,
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
    )

    assert report["gates"]["mcp_allowlist_observed"] is False
