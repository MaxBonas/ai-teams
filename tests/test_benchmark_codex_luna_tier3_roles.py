from scripts.benchmark_codex_luna_tier3_roles import (
    adapter_config,
    aggregate_diverse_family_reports,
    aggregate_reports,
    bootstrap_profile_ids,
    evaluate_role_artifact,
    reevaluate_report,
)


def test_antigravity_adapter_config_does_not_invent_reasoning_effort() -> None:
    config = adapter_config(
        "antigravity_subscription", "gemini-3.5-flash-low", "low"
    )

    assert config["cli_kind"] == "antigravity"
    assert config["command"] == ["agy"]
    assert config["model"] == "gemini-3.5-flash-low"
    assert "model_reasoning_effort" not in config


def test_local_adapter_config_preserves_oss_transport_and_read_only_scope() -> None:
    config = adapter_config(
        "local_gemma4_ollama", "gemma4:e4b", None
    )

    assert config["cli_kind"] == "codex"
    assert config["local_provider"] == "ollama"
    assert config["oss"] is True
    assert config["model"] == "gemma4:e4b"
    assert config["sandbox"] == "read-only"
    assert config["model_reasoning_effort"] == "none"
    assert bootstrap_profile_ids("local_gemma4_ollama") == [
        "local_gemma4_ollama",
        "codex_subscription",
    ]


def test_file_scout_evaluator_requires_causal_workspace_findings() -> None:
    text = (
        "src/tenant_checkout.py omite tenant_id; SELECT y UPDATE están separados, sin transacción "
        "atómica. El UPDATE no usa RETURNING ni confirma el ganador. tests/test_checkout.py no "
        "cubre concurrencia. Siguiente owner: Lead."
    )
    assert evaluate_role_artifact("file_scout", text)["contract_passed"] is True
    assert evaluate_role_artifact("file_scout", text.replace("tenant_id", "scope"))["contract_passed"] is False


def test_file_scout_evaluator_accepts_explicit_no_verification_wording() -> None:
    text = (
        "src/tenant_checkout.py omite tenant_id; SELECT y UPDATE están separados, sin "
        "transacción atómica. El UPDATE es incondicional y ni verifica las filas afectadas. "
        "tests/test_checkout.py no cubre concurrencia ni multi-tenant. Siguiente owner: Lead."
    )

    assert evaluate_role_artifact("file_scout", text)["contract_passed"] is True


def test_worker_evaluator_rejects_discarded_option() -> None:
    text = (
        "W3: Release Engineer pausa ante 2,1 % > 1,0 % durante 5 minutos y vuelve a W2. "
        "Reviewer acepta cuando logs y métrica confirmen recuperación."
    )
    assert evaluate_role_artifact("worker", text)["contract_passed"] is True
    assert evaluate_role_artifact("worker", text + " Desplegar el jueves.")["contract_passed"] is False


def test_worker_second_family_requires_dependency_order() -> None:
    text = (
        "INC-42: Database SRE congela escrituras por lag 47 s > 10 s durante 3 minutos. "
        "El checksum debe coincidir antes del failover a replica-eu-2. Incident Commander "
        "acepta cuando lag < 10 s y checksum coincide."
    )
    assert evaluate_role_artifact(
        "worker", text, "incident_dependency_handoff"
    )["contract_passed"] is True
    assert evaluate_role_artifact(
        "worker",
        text.replace(
            "El checksum debe coincidir antes del failover a replica-eu-2",
            "El failover a replica-eu-2 queda bloqueado hasta que el checksum coincida",
        ),
        "incident_dependency_handoff",
    )["contract_passed"] is True
    assert evaluate_role_artifact(
        "worker",
        text + " Se omite la opción descartada de reiniciar la caché.",
        "incident_dependency_handoff",
    )["contract_passed"] is True
    assert evaluate_role_artifact(
        "worker", text + " Reiniciar caché.", "incident_dependency_handoff"
    )["contract_passed"] is False


def test_file_scout_second_family_requires_idempotency_race() -> None:
    text = (
        "src/payment_retry.py: tenant_id se omite en SELECT; SELECT e INSERT están separados "
        "y crean una carrera no atómica. Falta UNIQUE por tenant. tests/test_payment_retry.py "
        "no cubre concurrencia ni multi-tenant. Siguiente owner: Lead."
    )
    assert evaluate_role_artifact(
        "file_scout", text, "payment_idempotency_inspection"
    )["contract_passed"] is True


def test_web_scout_second_family_requires_queue_advisory() -> None:
    text = (
        "ACME-2026-021 afecta acme-queue >=7.1.0,<7.4.0; corregido en 7.4.0. "
        "Expuesto si delayed-retry es público en internet. Publicado 2026-07-22 en "
        "https://security.example.invalid/ACME-2026-021. Siguiente owner: Security Engineer."
    )
    assert evaluate_role_artifact(
        "web_scout", text, "governed_queue_advisory_lookup"
    )["contract_passed"] is True
    translated = text.replace(
        "delayed-retry es público en internet",
        "el endpoint de reintentos diferidos es accesible desde Internet",
    ).replace("Security Engineer", "Lead")
    assert evaluate_role_artifact(
        "web_scout", translated, "governed_queue_advisory_lookup"
    )["contract_passed"] is True


def test_web_scout_evaluator_requires_complete_advisory_and_safe_handoff() -> None:
    text = (
        "ACME-2026-017 afecta acme-auth >=4.2.0,<4.2.3; corregido en 4.2.3. "
        "Expuesto si refresh-token es accesible desde internet. Publicado 2026-07-20 en "
        "https://security.example.invalid/ACME-2026-017. Siguiente owner: Security Engineer."
    )
    assert evaluate_role_artifact("web_scout", text)["contract_passed"] is True
    assert evaluate_role_artifact("web_scout", text + " Ejecuta publish_advisory.")["contract_passed"] is False


def test_mcp_operator_evaluator_requires_recovery_and_deny_evidence() -> None:
    text = (
        "Health recuperado: version mismatch 0.9.0 frente al pin 1.0.0; estado active. "
        "release_advisory_lookup devolvió ACME-2026-017 para acme-auth, corregido en 4.2.3. "
        "publish_advisory quedó denegada y no autorizada."
    )

    assert evaluate_role_artifact("mcp_operator", text)["contract_passed"] is True
    assert evaluate_role_artifact("mcp_operator", text.replace("denegada", "disponible").replace("no autorizada", "ok"))[
        "contract_passed"
    ] is False


def test_mcp_operator_second_family_requires_policy_governance() -> None:
    text = (
        "Health recovered and active after version mismatch 0.9.0 vs 1.0.0. "
        "dependency_policy_lookup POLICY-2026-04: runtime minimum Python 3.13; "
        "GPL-3.0 denied; owner Platform Security; "
        "source https://policy.example.invalid/POLICY-2026-04. "
        "publish_policy está denegada y no autorizada."
    )
    assert evaluate_role_artifact(
        "mcp_operator", text, "dependency_policy_governance"
    )["contract_passed"] is True
    assert evaluate_role_artifact(
        "mcp_operator",
        text.replace("denegada y no autorizada", "disponible"),
        "dependency_policy_governance",
    )["contract_passed"] is False


def test_aggregate_requires_three_passing_same_role_samples() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "role": "mcp_operator",
            "reasoning_effort": "medium",
            "contract_version": "tier3_causal_report_v2",
            "seed": seed,
            "ok": True,
            "wall_seconds": 10 + seed,
            "checks": {"allow": True, "deny": True},
            "runtime": {"input_tokens": 100, "output_tokens": 10},
            "artifact": f"artifact-{seed}",
            "_source_receipt": f"seed-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["samples_artifact_passed"] == 0
    assert result["samples_single_attempt"] == 0
    assert result["checks_passed"] == result["checks_total"] == 6
    assert result["usage"]["input_tokens"] == 300
    assert result["usage"]["telemetry_status"] == "observed"
    assert result["conclusion"]["exact_pair_calibrated"] is True


def test_antigravity_aggregate_keeps_missing_usage_unknown() -> None:
    reports = [
        {
            "profile_id": "antigravity_subscription",
            "model": "gemini-3.5-flash-low",
            "role": "worker",
            "reasoning_effort": None,
            "contract_version": "tier3_causal_report_v2",
            "seed": seed,
            "ok": True,
            "wall_seconds": 10,
            "checks": {
                "artifact_contract": True,
                "valid_assignee_report": True,
                "single_attempt": True,
            },
            "runtime": {"input_tokens": 0, "output_tokens": 0},
            "artifact": f"artifact-{seed}",
            "_source_receipt": f"seed-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["profile_id"] == "antigravity_subscription"
    assert result["usage"]["telemetry_status"] == "unknown"


def test_aggregate_rejects_duplicate_seed_or_unbound_sources() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-luna",
            "role": "worker",
            "reasoning_effort": "low",
            "contract_version": "tier3_causal_report_v2",
            "seed": seed,
            "ok": True,
            "wall_seconds": 10,
            "checks": {
                "artifact_contract": True,
                "valid_assignee_report": True,
                "single_attempt": True,
            },
            "runtime": {},
            "artifact": f"artifact-{seed}",
            "_source_receipt": f"seed-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is True
    reports[-1]["seed"] = 2
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is False
    reports[-1]["seed"] = 3
    reports[-1]["_source_receipt"] = reports[0]["_source_receipt"]
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is False


def test_diversity_aggregate_requires_two_exact_tier3_families() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-luna",
            "role": "worker",
            "reasoning_effort": "low",
            "case_family": family,
            "matrix_complete": True,
            "samples_passed": 3,
            "samples_artifact_passed": 3,
            "samples_single_attempt": 3,
            "conclusion": {"exact_pair_calibrated": True},
            "_source_receipt": f"{family}.json",
        }
        for family in ("release_rollback_checklist", "incident_dependency_handoff")
    ]
    result = aggregate_diverse_family_reports(
        reports,
        model="gpt-5.6-luna",
        profile_id="codex_subscription",
        role="worker",
        reasoning_effort="low",
    )
    assert result["samples_total"] == 6
    assert result["conclusion"]["case_diversity_passed"] is True
    reports[-1]["case_family"] = "release_rollback_checklist"
    assert (
        aggregate_diverse_family_reports(
            reports,
            model="gpt-5.6-luna",
            profile_id="codex_subscription",
            role="worker",
            reasoning_effort="low",
        )["conclusion"]["case_diversity_passed"]
        is False
    )


def test_reevaluation_accepts_english_health_recovery_without_provider_rerun() -> None:
    artifact = (
        "ACME-2026-017 release_advisory_lookup: acme-auth fixed 4.2.3; "
        "version mismatch 0.9.0 then 1.0.0 health recovered; publish_advisory denied."
    )
    report = {
        "role": "mcp_operator",
        "artifact": artifact,
        "checks": {"artifact_contract": False, "tool_called": True},
        "ok": False,
    }

    updated = reevaluate_report(report)

    assert updated["ok"] is True
    assert updated["reevaluation"]["provider_rerun"] is False
