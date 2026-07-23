from scripts.benchmark_critical_default_roles import (
    CASES,
    ROLES,
    aggregate_reports,
    build_prompt,
    compare_prompt_versions,
    evaluate_response,
    parse_json_output,
    reevaluate_report,
    run_sample,
)


def _valid_response(role: str, case_id: str) -> dict:
    shared = (
        "SQLite tenant_id atomic transaction rollback flag 100 reinicios 24 horas wakeup "
        if case_id == "tenant_queue_migration"
        else "5 % 2,4 % supera 1,0 % durante 10 min; v1, Release Engineer, Reviewer, "
        "logs y métricas, secret rotation "
    )
    role_text = {
        "architect": "interface boundary y supuesto",
        "lead": "owner accepted_by",
        "lead_executor": "Lead Executor trabaja personalmente y crea test",
        "quorum_auditor": "no-go ante failure y contraejemplo",
        "team_lead": "owner accepted_by",
    }[role]
    keys = {
        "architect": ("decision", "constraints", "interfaces", "risks", "verification", "rollback"),
        "lead": ("objective", "work_items", "risks", "verification", "escalation"),
        "lead_executor": ("objective", "execution_steps", "evidence", "risks", "rollback", "escalation"),
        "quorum_auditor": ("verdict", "challenges", "missing_evidence", "failure_modes", "recommendation"),
        "team_lead": ("objective", "assignments", "dependencies", "acceptance", "status_update", "escalation"),
    }[role]
    response = {key: f"{shared}{role_text}" for key in keys}
    if role == "lead_executor":
        response["execution_steps"] = [f"{shared}{role_text}"]
        response["evidence"] = {"required": [f"{shared}{role_text}"]}
    return response


def test_matrix_has_two_families_and_all_exact_critical_roles() -> None:
    assert set(ROLES) == {
        "architect", "lead", "lead_executor", "quorum_auditor", "team_lead",
    }
    assert set(CASES) == {"tenant_queue_migration", "auth_rollout_incident"}
    for role in ROLES:
        assert f"ROL EXACTO: {role}" in build_prompt(role, "tenant_queue_migration", 1)


def test_v2_prompt_adds_production_fact_retention_contract() -> None:
    assert "Tier 1 causal fact retention" not in build_prompt(
        "lead", "tenant_queue_migration", 1, "v1",
    )
    prompt = build_prompt("lead", "tenant_queue_migration", 1, "v2")
    assert "Tier 1 causal fact retention" in prompt
    assert "metric with value, unit, window and required action" in prompt


def test_evaluator_requires_exact_schema_hidden_anchors_and_no_discarded_option() -> None:
    for role in ROLES:
        for case_id in CASES:
            response = _valid_response(role, case_id)
            assert evaluate_response(role, case_id, response)["contract_passed"] is True
            response["unexpected"] = "noise"
            assert evaluate_response(role, case_id, response)["contract_passed"] is False
    incident = _valid_response("lead", "auth_rollout_incident")
    incident["objective"] += " desplegar el jueves"
    assert evaluate_response("lead", "auth_rollout_incident", incident)["contract_passed"] is False


def test_parser_accepts_clean_or_wrapped_json() -> None:
    assert parse_json_output('{"objective":"ok"}') == {"objective": "ok"}
    assert parse_json_output('texto\n```json\n{"objective":"ok"}\n```') == {"objective": "ok"}


def test_failed_parse_preserves_raw_output_and_cli_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.benchmark_critical_default_roles._run_antigravity",
        lambda *_args, **_kwargs: {
            "returncode": 0,
            "raw": '{"broken":',
            "stderr": "",
            "usage": {},
            "cli_version": "1.1.5",
        },
    )
    result = run_sample(
        profile_id="antigravity_subscription",
        model="gemini-3.1-pro-high",
        role="lead",
        case_id="tenant_queue_migration",
        seed=1,
        timeout=30,
    )
    assert result["status"] == "failed"
    assert result["cli_version"] == "1.1.5"
    assert result["raw_output_on_failure"] == '{"broken":'


def test_reevaluation_names_missing_anchor_without_provider_rerun() -> None:
    response = _valid_response("lead", "auth_rollout_incident")
    response = {
        key: value.replace("2,4 %", "error observado")
        for key, value in response.items()
    }
    report = {
        "status": "completed",
        "role": "lead",
        "case_id": "auth_rollout_incident",
        "response": response,
        "evaluation": {"contract_passed": True},
        "ok": True,
    }
    updated = reevaluate_report(report)
    assert updated["ok"] is False
    assert "case.observed_error" in updated["evaluation"]["missing_anchors"]
    assert updated["reevaluation"]["provider_rerun"] is False


def test_compound_numeric_anchors_require_value_and_unit_together() -> None:
    incident = {
        key: value.replace("5 %", "porcentaje limitado")
        for key, value in _valid_response("lead", "auth_rollout_incident").items()
    }
    incident_eval = evaluate_response("lead", "auth_rollout_incident", incident)
    assert "case.rollout_percent" in incident_eval["missing_anchors"]
    tenant = {
        key: value.replace("24 horas", "varias horas")
        for key, value in _valid_response("lead", "tenant_queue_migration").items()
    }
    tenant_eval = evaluate_response("lead", "tenant_queue_migration", tenant)
    assert "case.observation_window" in tenant_eval["missing_anchors"]


def test_lead_executor_requires_material_steps_and_evidence_not_role_echo() -> None:
    response = _valid_response("lead_executor", "tenant_queue_migration")
    response["execution_steps"] = ["Aplicar cambio", "Ejecutar prueba causal"]
    response["evidence"] = {"required": ["resultado del gate"]}
    evaluation = evaluate_response(
        "lead_executor", "tenant_queue_migration", response,
    )
    assert evaluation["contract_passed"] is True
    response["execution_steps"] = []
    evaluation = evaluate_response(
        "lead_executor", "tenant_queue_migration", response,
    )
    assert evaluation["contract_passed"] is False
    assert "execution_steps_nonempty" in evaluation["failed_role_checks"]


def test_aggregate_requires_two_cases_three_seeds_and_one_exact_pair() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-sol",
            "role": "lead",
            "case_id": case_id,
            "seed": seed,
            "wall_seconds": seed,
            "ok": True,
            "response": {"seed": seed, "case": case_id},
            "_source_receipt": f"{case_id}-seed-{seed}.json",
        }
        for case_id in CASES
        for seed in (1, 2, 3)
    ]
    result = aggregate_reports(reports)
    assert result["matrix_complete"] is True
    assert result["conclusion"]["exact_pair_calibrated"] is True
    assert result["conclusion"]["default_change_allowed"] is False
    assert aggregate_reports(reports[:-1])["conclusion"]["exact_pair_calibrated"] is False
    reports[-1]["role"] = "team_lead"
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is False


def test_aggregate_rejects_mixed_prompt_versions() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-sol",
            "role": "lead",
            "case_id": case_id,
            "seed": seed,
            "prompt_version": "v2",
            "ok": True,
            "response": {"seed": seed, "case": case_id},
            "_source_receipt": f"{case_id}-seed-{seed}.json",
        }
        for case_id in CASES
        for seed in (1, 2, 3)
    ]
    reports[-1]["prompt_version"] = "v1"
    result = aggregate_reports(reports)
    assert result["matrix_complete"] is False
    assert result["integrity"]["single_prompt_version"] is False
    assert result["conclusion"]["exact_pair_calibrated"] is False


def test_prompt_comparison_requires_same_family_and_never_calibrates() -> None:
    base = {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "lead",
        "case_id": "auth_rollout_incident",
    }
    v1 = [
        {
            **base, "seed": seed, "prompt_version": "v1", "ok": seed == 1,
            "_source_receipt": f"v1-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]
    v2 = [
        {
            **base, "seed": seed, "prompt_version": "v2", "ok": True,
            "_source_receipt": f"v2-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]
    result = compare_prompt_versions(v1, v2)
    assert result["comparable"] is True
    assert result["pass_delta"] == 2
    assert result["improvement_observed"] is True
    assert result["conclusion"]["calibration_allowed"] is False
    v2[-1]["case_id"] = "tenant_queue_migration"
    assert compare_prompt_versions(v1, v2)["comparable"] is False
