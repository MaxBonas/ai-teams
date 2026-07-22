from scripts.benchmark_codex_luna_tier3_roles import aggregate_reports, evaluate_role_artifact, reevaluate_report


def test_file_scout_evaluator_requires_causal_workspace_findings() -> None:
    text = (
        "src/tenant_checkout.py omite tenant_id y separa SELECT/UPDATE, creando carrera no atómica. "
        "Debe usar RETURNING para confirmar el ganador. tests/test_checkout.py necesita concurrencia. "
        "Siguiente owner: Engineer."
    )
    assert evaluate_role_artifact("file_scout", text)["contract_passed"] is True
    assert evaluate_role_artifact("file_scout", text.replace("tenant_id", "scope"))["contract_passed"] is False


def test_worker_evaluator_rejects_discarded_option() -> None:
    text = (
        "W3: Release Engineer pausa ante 2,1 % > 1,0 % durante 5 minutos y vuelve a W2. "
        "Reviewer acepta cuando logs y métrica confirmen recuperación."
    )
    assert evaluate_role_artifact("worker", text)["contract_passed"] is True
    assert evaluate_role_artifact("worker", text + " Desplegar el jueves.")["contract_passed"] is False


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


def test_aggregate_requires_three_passing_same_role_samples() -> None:
    reports = [
        {
            "model": "gpt-5.6-terra",
            "role": "mcp_operator",
            "seed": seed,
            "ok": True,
            "wall_seconds": 10 + seed,
            "checks": {"allow": True, "deny": True},
            "runtime": {"input_tokens": 100, "output_tokens": 10},
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["checks_passed"] == result["checks_total"] == 6
    assert result["usage"]["input_tokens"] == 300
    assert result["conclusion"]["exact_pair_calibrated"] is True


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
