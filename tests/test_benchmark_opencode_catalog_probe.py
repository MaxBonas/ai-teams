from scripts.benchmark_opencode_catalog_probe import classify_probe


def _sample(*, schema_passed: bool, error_name: str | None = None) -> dict:
    return {
        "inference_runs": 1,
        "provider_error": {"name": error_name} if error_name else None,
        "gates": {
            "session_created": True,
            "request_completed": True,
            "session_deleted": True,
            "provider_accepted_submit_work_schema": schema_passed,
        },
    }


def test_schema_pass_opens_classification_without_granting_roles() -> None:
    report = classify_probe(
        model="opencode/ling-3.0-flash-free",
        cli_version="1.18.4",
        discovered_models={"opencode/ling-3.0-flash-free"},
        sample=_sample(schema_passed=True),
        server_teardown_ok=True,
        observed_at="2026-07-24T00:00:00+02:00",
    )

    assert report["candidate_gate_passed"] is True
    assert report["decision"] == {
        "status": "eligible_for_role_classification",
        "repeat_inference": True,
        "roles_granted": [],
        "automatic_selection_allowed": False,
        "quality_score_allowed": False,
    }


def test_structured_output_failure_closes_without_repeat() -> None:
    report = classify_probe(
        model="opencode/ling-3.0-flash-free",
        cli_version="1.18.4",
        discovered_models={"opencode/ling-3.0-flash-free"},
        sample=_sample(
            schema_passed=False,
            error_name="StructuredOutputError",
        ),
        server_teardown_ok=True,
        observed_at="2026-07-24T00:00:00+02:00",
    )

    assert report["probe_completed"] is True
    assert report["provider_gate_conclusive"] is True
    assert report["candidate_gate_passed"] is False
    assert report["decision"]["status"] == (
        "catalog_only_until_transport_or_model_change"
    )
    assert report["decision"]["repeat_inference"] is False


def test_structured_failure_remains_conclusive_when_cleanup_gate_is_red() -> None:
    report = classify_probe(
        model="opencode/ling-3.0-flash-free",
        cli_version="1.18.4",
        discovered_models={"opencode/ling-3.0-flash-free"},
        sample=_sample(
            schema_passed=False,
            error_name="StructuredOutputError",
        ),
        server_teardown_ok=False,
        observed_at="2026-07-24T00:00:00+02:00",
    )

    assert report["provider_gate_conclusive"] is True
    assert report["probe_completed"] is False
    assert report["decision"]["status"] == (
        "catalog_only_until_transport_or_model_change"
    )


def test_operational_failure_does_not_classify_model() -> None:
    sample = _sample(schema_passed=False)
    sample["gates"]["request_completed"] = False
    sample["transport_error"] = "TimeoutError: quota"

    report = classify_probe(
        model="opencode/ling-3.0-flash-free",
        cli_version="1.18.4",
        discovered_models={"opencode/ling-3.0-flash-free"},
        sample=sample,
        server_teardown_ok=True,
        observed_at="2026-07-24T00:00:00+02:00",
    )

    assert report["probe_completed"] is False
    assert report["decision"]["status"] == "operational_diagnostic"
