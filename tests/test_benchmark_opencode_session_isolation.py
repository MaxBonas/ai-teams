from scripts.benchmark_opencode_session_isolation import summarize


def _schema(model: str, *, supported: bool = False) -> dict:
    return {
        "model": model,
        "gates": {
            "session_created": True,
            "request_completed": True,
            "session_deleted": True,
            "provider_accepted_schema": supported,
        },
    }


def _isolation(seed: int) -> dict:
    return {
        "seed": seed,
        "session_a": f"ses_a_{seed}",
        "session_b": f"ses_b_{seed}",
        "ok": True,
    }


def test_summary_accepts_complete_isolation_without_promoting_server() -> None:
    report = summarize(
        schema_rows=[_schema("opencode/a"), _schema("opencode/b", supported=True)],
        isolation_rows=[_isolation(seed) for seed in (1, 2, 3)],
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
        server_teardown_ok=True,
    )

    assert report["gates"]["schema_screen_complete"] is True
    assert report["gates"]["at_least_one_model_accepts_json_schema"] is True
    assert report["gates"]["isolation_matrix_3seed"] is True
    assert report["gates"]["six_distinct_sessions"] is True
    assert report["production_activation_allowed"] is False


def test_summary_rejects_reused_session_even_when_rows_claim_success() -> None:
    rows = [_isolation(seed) for seed in (1, 2, 3)]
    rows[2]["session_b"] = rows[0]["session_a"]

    report = summarize(
        schema_rows=[_schema("opencode/a")],
        isolation_rows=rows,
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
        server_teardown_ok=True,
    )

    assert report["gates"]["isolation_matrix_3seed"] is False
    assert report["gates"]["six_distinct_sessions"] is False


def test_schema_support_is_not_inferred_from_valid_text() -> None:
    report = summarize(
        schema_rows=[_schema("opencode/a", supported=False)],
        isolation_rows=[_isolation(seed) for seed in (1, 2, 3)],
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
        server_teardown_ok=True,
    )

    assert report["gates"]["schema_screen_complete"] is True
    assert report["gates"]["at_least_one_model_accepts_json_schema"] is False
    assert report["json_schema_supported_models"] == []


def test_schema_screen_rejects_request_without_completed_response() -> None:
    schema_row = _schema("opencode/a")
    schema_row["gates"]["request_completed"] = False

    report = summarize(
        schema_rows=[schema_row],
        isolation_rows=[_isolation(seed) for seed in (1, 2, 3)],
        cli_version="1.18.4",
        model="opencode/deepseek-v4-flash-free",
        server_teardown_ok=True,
    )

    assert report["gates"]["schema_screen_complete"] is False
