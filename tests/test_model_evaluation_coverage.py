import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage


def test_coverage_inventory_is_conservative_and_tracks_exact_promotions() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.5",
            "gemini_api_free": "api:google:v1beta",
        },
    )

    assert report["models"] == 47
    # Web Scout is nominated only on transports that expose governed MCP.
    assert report["role_pairs"] == 124
    assert report["complete"] is False
    # Sol is fresh on 0.146.0-alpha.6; prior Terra/Luna evidence remains
    # visible but stale because it was sealed on 0.145.0.
    # Esta fotografía fuerza Antigravity 1.1.5: Lead y Quorum recalibrados en
    # 1.1.6 quedan stale, aunque su evidencia siga visible.
    # La fotografía es deliberadamente anterior a la renovación QA del 29:
    # Flash High queda future/partial y no se retropropaga como calibrado.
    assert report["pair_counts"]["calibrated"] == 15
    assert report["pair_counts"]["partial"] == 15
    assert report["pair_counts"]["deferred_until_material_change"] == 3
    assert report["pair_counts"]["requires_canary"] > 0
    assert report["pair_counts"]["requires_tool_fixture"] > 0
    gemini_free = next(
        row for row in report["rows"]
        if row["profile_id"] == "gemini_api_free"
        and row["model"] == "gemini-3.6-flash"
    )
    gemini_roles = {row["role"]: row for row in gemini_free["roles"]}
    assert gemini_roles["reviewer"]["status"] == "calibrated"
    assert gemini_roles["reviewer"]["evidence_validation_errors"] == []
    assert gemini_roles["qa"]["status"] == "deferred_until_material_change"
    assert gemini_roles["qa"]["diagnostic_reason"] == (
        "claimed_test_without_materialized_executable_artifact"
    )
    assert gemini_roles["test_designer"]["status"] == (
        "deferred_until_material_change"
    )
    assert gemini_roles["test_designer"]["diagnostic_reason"] == (
        "empty_test_artifact_baseline_no_tests"
    )
    assert report["policy"]["material_change_detection"]["automatic"] == [
        "provider_or_cli_version_changed",
        "diagnostic_age_exceeded",
        "diagnostic_receipt_invalid",
    ]
    flash_web_diagnostic = next(
        row for row in report["diagnostics"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-low"
        and row["role"] == "web_scout"
    )
    assert flash_web_diagnostic["reason"] == (
        "governed_mcp_transport_unsupported_fail_fast"
    )
    assert flash_web_diagnostic["validation_errors"] == []
    luna = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription" and row["model"] == "gpt-5.6-luna"
    )
    by_role = {row["role"]: row["status"] for row in luna["roles"]}
    assert by_role["context_curator"] == "partial"
    assert by_role["file_scout"] == "partial"
    assert by_role["worker"] == "partial"
    assert by_role["web_scout"] == "partial"
    assert luna["model_status"] == "partial"
    file_scout = next(role for role in luna["roles"] if role["role"] == "file_scout")
    worker = next(role for role in luna["roles"] if role["role"] == "worker")
    web_scout = next(role for role in luna["roles"] if role["role"] == "web_scout")
    assert file_scout["evaluation_reason"] == (
        "tier3_causal_quality_3_of_3_single_attempt_1_of_3"
    )
    assert file_scout["evidence_validation_errors"] == []
    assert worker["status"] == "partial"
    assert worker["evaluation_reason"] == "tier3_worker_two_family_causal_6_of_6"
    assert worker["evidence_validation_errors"] == []
    assert (
        web_scout["evaluation_reason"]
        == "tier3_web_scout_two_family_causal_6_of_6"
    )
    assert web_scout["evidence_validation_errors"] == []
    assert len(web_scout["evidence_receipts"]) == 1

    flash_medium = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-medium"
    )
    medium_worker = next(
        role for role in flash_medium["roles"] if role["role"] == "worker"
    )
    assert medium_worker["status"] == "calibrated"
    assert medium_worker["evaluation_reason"] == (
        "tier3_worker_two_family_causal_6_of_6"
    )
    assert medium_worker["evidence_validation_errors"] == []

    flash_low = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-low"
    )
    low_by_role = {role["role"]: role for role in flash_low["roles"]}
    assert low_by_role["context_curator"]["status"] == "calibrated"
    assert low_by_role["context_curator"]["evidence_validation_errors"] == []
    assert low_by_role["file_scout"]["status"] == "calibrated"
    assert low_by_role["file_scout"]["evidence_validation_errors"] == []
    assert low_by_role["file_scout"]["diagnostic_reason"] == (
        "second_file_scout_family_submit_work_json_parse_error"
    )
    assert low_by_role["file_scout"]["diagnostic_validation_errors"] == []
    assert low_by_role["worker"]["status"] == "partial"
    assert low_by_role["worker"]["evidence_validation_errors"] == []

    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription" and row["model"] == "gpt-5.6-terra"
    )
    terra_reviewer = next(role for role in terra["roles"] if role["role"] == "reviewer")
    terra_engineer = next(role for role in terra["roles"] if role["role"] == "engineer")
    terra_qa = next(role for role in terra["roles"] if role["role"] == "qa")
    terra_test_designer = next(
        role for role in terra["roles"] if role["role"] == "test_designer"
    )
    terra_mcp_operator = next(
        role for role in terra["roles"] if role["role"] == "mcp_operator"
    )
    assert terra_reviewer["status"] == "calibrated"
    assert terra_reviewer["evaluation_reason"] == "durable_review_behavioral_3_of_3"
    assert terra_reviewer["evaluated_at"] == "2026-07-24"
    assert terra_reviewer["provider_version"] == "0.146.0-alpha.6"
    assert terra_engineer["status"] == "deferred_until_material_change"
    assert terra_engineer["evaluation_reason"] == (
        "coding_two_family_hidden_suite_6_of_6"
    )
    assert terra_engineer["evidence_validation_errors"] == []
    assert terra_engineer["diagnostic_reason"] == (
        "provider_0_146_revalidation_hidden_9_of_9_but_ruff_2_fail_fast"
    )
    assert terra_engineer["diagnostic_validation_errors"] == []
    assert terra_qa["status"] == "partial"
    assert terra_qa["evaluation_reason"] == "adversarial_qa_two_family_6_of_6"
    assert terra_qa["evidence_validation_errors"] == []
    assert terra_test_designer["status"] == "partial"
    assert (
        terra_test_designer["evaluation_reason"]
        == "independent_test_designer_two_family_6_of_6"
    )
    assert terra_test_designer["evidence_validation_errors"] == []
    assert terra_mcp_operator["status"] == "partial"
    assert (
        terra_mcp_operator["evaluation_reason"]
        == "mcp_operator_two_family_governance_6_of_6"
    )
    assert terra_mcp_operator["evidence_validation_errors"] == []
    for profile_id, model in (
        ("codex_subscription", "gpt-5.6-sol"),
        ("antigravity_subscription", "gemini-3.1-pro-high"),
    ):
        model_row = next(
            row
            for row in report["rows"]
            if row["profile_id"] == profile_id and row["model"] == model
        )
        architect = next(
            role for role in model_row["roles"] if role["role"] == "architect"
        )
        assert architect["status"] == "calibrated"
        assert (
            architect["evaluation_reason"]
            == "critical_role_hidden_causal_contract_6_of_6"
        )
        assert architect["evidence_validation_errors"] == []
        lead = next(role for role in model_row["roles"] if role["role"] == "lead")
        assert lead["status"] == (
            "calibrated" if profile_id == "codex_subscription" else "partial"
        )
        assert lead["evaluation_reason"] == "critical_role_hidden_causal_contract_6_of_6"
        assert lead["evidence_validation_errors"] == []
        assert lead["prompt_version"] == "v2"
    sol = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-sol"
    )
    sol_executor = next(
        role for role in sol["roles"] if role["role"] == "lead_executor"
    )
    assert sol_executor["status"] == "calibrated"
    assert sol_executor["evidence_validation_errors"] == []
    pro = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.1-pro-high"
    )
    pro_executor = next(
        role for role in pro["roles"] if role["role"] == "lead_executor"
    )
    assert pro_executor["status"] == "calibrated"
    assert pro_executor["evidence_validation_errors"] == []
    assert pro_executor["prompt_version"] == "v2"
    sol_auditor = next(
        role for role in sol["roles"] if role["role"] == "quorum_auditor"
    )
    assert sol_auditor["status"] == "calibrated"
    assert sol_auditor["evidence_validation_errors"] == []
    assert sol_auditor["prompt_version"] == "v2"
    pro_auditor = next(
        role for role in pro["roles"] if role["role"] == "quorum_auditor"
    )
    assert pro_auditor["status"] == "partial"
    assert pro_auditor["provider_version"] == "1.1.8"
    for model_row in (sol, pro):
        team_lead = next(
            role for role in model_row["roles"] if role["role"] == "team_lead"
        )
        assert team_lead["status"] == "calibrated"
        assert team_lead["evidence_validation_errors"] == []


def test_antigravity_116_is_stale_after_118_authority_revalidation() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={"antigravity_subscription": "1.1.6"},
    )
    pro = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.1-pro-high"
    )
    roles = {role["role"]: role for role in pro["roles"]}

    assert roles["quorum_auditor"]["status"] == "partial"
    assert roles["quorum_auditor"]["provider_version"] == "1.1.8"
    assert roles["quorum_auditor"]["evidence_validation_errors"] == []
    assert roles["lead"]["status"] == "partial"
    assert roles["lead"]["provider_version"] == "1.1.8"
    assert roles["lead"]["evidence_validation_errors"] == []


def test_antigravity_118_promotes_recalibrated_lead_and_quorum_roles() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={"antigravity_subscription": "1.1.8"},
    )
    pro = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.1-pro-high"
    )
    roles = {role["role"]: role for role in pro["roles"]}

    for role in ("lead", "quorum_auditor"):
        assert roles[role]["status"] == "calibrated"
        assert roles[role]["provider_version"] == "1.1.8"
        assert roles[role]["prompt_version"] == "v2"
        assert roles[role]["evidence_validation_errors"] == []


def test_owner_preferences_prioritize_or_suppress_proactive_maintenance() -> None:
    observed_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
    versions = {
        "codex_subscription": "changed",
        "antigravity_subscription": "changed",
    }
    baseline = audit_model_evaluation_coverage(
        observed_at=observed_at,
        observed_versions=versions,
    )
    actionable = []
    for row in baseline["rows"]:
        has_technical_debt = any(
            role.get("next_action")
            in {"run_exact_canary", "run_exact_tool_fixture"}
            for role in row["roles"]
        )
        identity = (row["profile_id"], row["model"])
        if has_technical_debt and identity not in actionable:
            actionable.append(identity)
        if len(actionable) == 3:
            break
    assert len(actionable) == 3
    high, low, archived = actionable
    preferences = {
        "schema_version": "model_owner_preferences_v1",
        "updated_at": "2026-07-24T18:00:00+02:00",
        "preferences": [
            {
                "profile_id": profile_id,
                "model_id": model,
                "state": state,
                "reason": f"{state} test",
                "updated_at": "2026-07-24T18:00:00+02:00",
            }
            for (profile_id, model), state in (
                (high, "high"),
                (low, "low"),
                (archived, "archived"),
            )
        ],
    }

    report = audit_model_evaluation_coverage(
        observed_at=observed_at,
        observed_versions=versions,
        owner_preferences=preferences,
    )

    backlog_identities = {
        (item["profile_id"], item["model"])
        for item in report["maintenance_backlog"]
    }
    assert report["maintenance_backlog"][0]["owner_preference"]["state"] == "high"
    assert high in backlog_identities
    assert low not in backlog_identities
    assert archived not in backlog_identities
    archived_row = next(
        row for row in report["rows"]
        if (row["profile_id"], row["model"]) == archived
    )
    assert archived_row["maintenance_allowed"] is False
    assert archived_row["proactive_maintenance_allowed"] is False
    assert {
        role["next_action"]
        for role in archived_row["roles"]
        if "next_action" in role
    } == {"none_owner_archived"}


def test_manual_probe_gated_model_does_not_create_automatic_canary_debt() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
    )

    qwen = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "groq_api_free" and row["model"] == "qwen/qwen3.6-27b"
    )
    assert qwen["automatic"] is False
    assert {role["status"] for role in qwen["roles"]} == {"manual_candidate"}


def test_existing_behavioral_and_screening_receipts_are_not_lost() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
            "opencode_zen_free": "1.18.4",
        },
    )

    flash = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-high"
    )
    reviewer = next(role for role in flash["roles"] if role["role"] == "reviewer")
    qa = next(role for role in flash["roles"] if role["role"] == "qa")
    test_designer = next(
        role for role in flash["roles"] if role["role"] == "test_designer"
    )
    assert reviewer["status"] == "partial"
    assert reviewer["evaluation_reason"] == "durable_review_behavioral_3_of_3"
    assert qa["status"] == "calibrated"
    assert qa["evaluation_reason"] == "adversarial_qa_two_family_6_of_6"
    assert qa["evidence_validation_errors"] == []
    assert qa["diagnostic_reason"] == (
        "second_qa_family_attack_passed_verify_subscription_cli_timeout"
    )
    assert qa["diagnostic_validation_errors"] == []
    assert test_designer["status"] == "calibrated"
    assert (
        test_designer["evaluation_reason"]
        == "independent_test_designer_two_family_6_of_6"
    )
    assert test_designer["evidence_validation_errors"] == []
    assert test_designer["diagnostic_reason"] == (
        "second_test_designer_family_seed1_passed_seed2_cli_timeout"
    )
    assert test_designer["diagnostic_validation_errors"] == []

    deepseek = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "opencode_zen_free"
        and row["model"] == "opencode/deepseek-v4-flash-free"
    )
    reviewer = next(role for role in deepseek["roles"] if role["role"] == "reviewer")
    assert reviewer["status"] == "partial"
    assert reviewer["diagnostic_reason"] == (
        "structured_output_transport_unchanged_closed_without_inference"
    )
    assert reviewer["diagnostic_receipts"] == [
        "benchmarks/results/model_calibration/"
        "opencode-1.18.4-negative-closure-v1.json"
    ]
    assert reviewer["diagnostic_validation_errors"] == []


def test_nonblocking_pool_failures_remain_visible_without_false_promotion() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "antigravity_subscription": "1.1.6",
            "local_gemma4_ollama": "0.32.1",
            "local_qwen_ollama": "0.32.1",
        },
    )

    gpt_oss = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gpt-oss-120b-medium"
    )
    gpt_roles = {row["role"]: row for row in gpt_oss["roles"]}
    assert gpt_roles["file_scout"]["status"] == "partial"
    assert "parse_failed" in gpt_roles["file_scout"]["evaluation_reason"]
    assert gpt_roles["worker"]["status"] == "deferred_until_material_change"
    assert gpt_roles["worker"]["next_action"] == (
        "no_rerun_until_material_change"
    )
    assert gpt_roles["worker"]["diagnostic_stale_reasons"] == []
    assert gpt_roles["worker"]["diagnostic_reason"] == (
        "provider_1_1_6_exact_durable_contract_submit_work_parse_failure"
    )
    assert gpt_roles["worker"]["diagnostic_validation_errors"] == []

    gemma = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "local_gemma4_ollama"
        and row["model"] == "gemma4:26b"
    )
    gemma_roles = {row["role"]: row for row in gemma["roles"]}
    assert gemma_roles["engineer"]["status"] == "partial"
    assert gemma_roles["reviewer"]["status"] == (
        "deferred_until_material_change"
    )
    assert gemma_roles["test_designer"]["diagnostic_reason"] == (
        "baseline_suite_failed_despite_mutant_detection"
    )
    assert gemma_roles["test_designer"]["diagnostic_validation_errors"] == []


def test_engineer_fail_fast_receipts_are_deeply_validated() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.6",
        },
    )

    terra = next(
        row for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    terra_engineer = next(
        row for row in terra["roles"] if row["role"] == "engineer"
    )
    assert terra_engineer["status"] == "deferred_until_material_change"
    assert terra_engineer["diagnostic_reason"] == (
        "provider_0_146_revalidation_hidden_9_of_9_but_ruff_2_fail_fast"
    )
    assert terra_engineer["diagnostic_validation_errors"] == []
    assert terra_engineer["next_action"] == "no_rerun_until_material_change"

    sonnet = next(
        row for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "claude-sonnet-4-6"
    )
    sonnet_engineer = next(
        row for row in sonnet["roles"] if row["role"] == "engineer"
    )
    assert sonnet_engineer["status"] == "deferred_until_material_change"
    assert sonnet_engineer["diagnostic_validation_errors"] == []


def test_matching_provider_version_defers_current_negative_diagnostic() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={"antigravity_subscription": "1.1.6"},
    )

    gpt_oss = next(
        row for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gpt-oss-120b-medium"
    )
    worker = next(row for row in gpt_oss["roles"] if row["role"] == "worker")

    assert worker["status"] == "deferred_until_material_change"
    assert worker["next_action"] == "no_rerun_until_material_change"
    assert worker["diagnostic_stale_reasons"] == []


def test_material_provider_change_reopens_deferred_diagnostic() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={"antigravity_subscription": "1.1.7"},
    )

    gpt_oss = next(
        row for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gpt-oss-120b-medium"
    )
    worker = next(row for row in gpt_oss["roles"] if row["role"] == "worker")

    assert worker["status"] == "requires_canary"
    assert worker["next_action"] == "run_exact_canary"
    assert worker["diagnostic_stale_reasons"] == [
        "provider_version_changed_or_unobserved"
    ]


def test_tampered_critical_sample_invalidates_exact_role_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "critical-defaults-codex-sol-architect-aggregate-cli-0.146.0.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(aggregate["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["response"]["decision"] = "contenido manipulado"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.5",
        },
        repo_root=tmp_path,
    )
    sol = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-sol"
    )
    architect = next(role for role in sol["roles"] if role["role"] == "architect")
    assert architect["status"] == "partial"
    assert "evidence_receipt_invalid" in architect["stale_reasons"]
    assert any(
        error.startswith("sample_response_hash:")
        for error in architect["evidence_validation_errors"]
    )


def test_tampered_gemini_free_review_sample_invalidates_calibration(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "gemini-api-free-3.6-flash-review-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(aggregate["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["reject"]["ok"] = False
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        observed_versions={"gemini_api_free": "api:google:v1beta"},
        repo_root=tmp_path,
    )
    model = next(
        row for row in report["rows"]
        if row["profile_id"] == "gemini_api_free"
        and row["model"] == "gemini-3.6-flash"
    )
    reviewer = next(
        role for role in model["roles"] if role["role"] == "reviewer"
    )
    assert reviewer["status"] == "partial"
    assert "evidence_receipt_invalid" in reviewer["stale_reasons"]
    assert any(
        error.startswith("durable_review_source_hash:")
        or error.startswith("durable_review_source_reject:")
        for error in reviewer["evidence_validation_errors"]
    )


def test_tampered_tier3_artifact_invalidates_luna_worker_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "m83-tier3-diversity-luna-worker-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    family_paths = [Path(item) for item in aggregate["source_receipts"]]
    families = [
        json.loads((repo_root / relative).read_text(encoding="utf-8"))
        for relative in family_paths
    ]
    sample_paths = [
        Path(item)
        for family in families
        for item in family["source_receipts"]
    ]
    paths = [aggregate_rel, *family_paths, *sample_paths]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(families[0]["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["artifact"] += "\ncontenido manipulado"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
        repo_root=tmp_path,
    )
    luna = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-luna"
    )
    worker = next(role for role in luna["roles"] if role["role"] == "worker")
    assert worker["status"] == "partial"
    assert "evidence_receipt_invalid" in worker["stale_reasons"]
    assert any(
        error.startswith("tier3_worker_diversity_sample_artifact_hash:")
        for error in worker["evidence_validation_errors"]
    )


def test_tampered_antigravity_context_artifact_invalidates_evidence(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "antigravity-flash-low-context-curator-v1-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [
        aggregate_rel,
        *(Path(item) for item in aggregate["source_receipts"]),
        Path("benchmarks/context_quality/auth_migration_thread.md"),
        Path("benchmarks/context_quality/auth_migration_rubric.json"),
        Path("benchmarks/context_quality/queue_rollout_thread.md"),
        Path("benchmarks/context_quality/queue_rollout_rubric.json"),
    ]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(aggregate["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["summary"] += "\ncontenido manipulado"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
        repo_root=tmp_path,
    )
    flash_low = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-low"
    )
    curator = next(
        role for role in flash_low["roles"] if role["role"] == "context_curator"
    )
    assert curator["status"] == "partial"
    assert "evidence_receipt_invalid" in curator["stale_reasons"]
    assert any(
        error.startswith("context_curator_sample_artifact_hash:")
        for error in curator["evidence_validation_errors"]
    )


def test_missing_context_fixture_degrades_evidence_instead_of_crashing(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "antigravity-flash-low-context-curator-v1-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [
        aggregate_rel,
        *(Path(item) for item in aggregate["source_receipts"]),
        Path("benchmarks/context_quality/auth_migration_rubric.json"),
        Path("benchmarks/context_quality/queue_rollout_thread.md"),
        Path("benchmarks/context_quality/queue_rollout_rubric.json"),
    ]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={"antigravity_subscription": "1.1.5"},
        repo_root=tmp_path,
    )

    flash_low = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-low"
    )
    curator = next(
        role for role in flash_low["roles"] if role["role"] == "context_curator"
    )
    assert curator["status"] == "partial"
    assert "context_curator_fixture_invalid:auth_migration" in (
        curator["evidence_validation_errors"]
    )


def test_tampered_antigravity_tier2_sample_invalidates_flash_evidence(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "p0h2d4-flash-high-test-designer-diversity-v3-cli-1.1.8.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    families = []
    for relative in aggregate["source_receipts"]:
        family = json.loads((repo_root / relative).read_text(encoding="utf-8"))
        families.append(family)
        paths.extend(Path(item) for item in family["source_receipts"])
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(families[0]["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["mutation_evaluation"]["mutants_killed"] = 0
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
        repo_root=tmp_path,
    )
    flash = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "antigravity_subscription"
        and row["model"] == "gemini-3.5-flash-high"
    )
    test_designer = next(
        role for role in flash["roles"] if role["role"] == "test_designer"
    )
    assert test_designer["status"] == "partial"
    assert "evidence_receipt_invalid" in test_designer["stale_reasons"]
    assert any(
        error.startswith("test_designer_diversity_sample_hash:")
        for error in test_designer["evidence_validation_errors"]
    )


def test_tampered_coding_family_invalidates_diversity_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "m83-coding-diversity-terra-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    family_path = tmp_path / Path(aggregate["source_receipts"][0])
    family = json.loads(family_path.read_text(encoding="utf-8"))
    family["samples_passed"] = 2
    family_path.write_text(json.dumps(family), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
        repo_root=tmp_path,
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    engineer = next(role for role in terra["roles"] if role["role"] == "engineer")

    assert engineer["status"] == "partial"
    assert "evidence_receipt_invalid" in engineer["stale_reasons"]
    assert any(
        error.startswith("coding_diversity_source_")
        for error in engineer["evidence_validation_errors"]
    )


def test_tampered_qa_family_invalidates_diversity_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "p0h2d3-terra-qa-diversity-v5-cli-0.146.0.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    families = []
    for relative in aggregate["source_receipts"]:
        family = json.loads((repo_root / relative).read_text(encoding="utf-8"))
        families.append(family)
        paths.extend(Path(item) for item in family["source_receipts"])
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(families[0]["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["checks"]["verification_report_approved"] = False
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
        repo_root=tmp_path,
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    qa = next(role for role in terra["roles"] if role["role"] == "qa")
    assert qa["status"] == "partial"
    assert "evidence_receipt_invalid" in qa["stale_reasons"]
    assert any(
        error.startswith("qa_diversity_sample_hash:")
        for error in qa["evidence_validation_errors"]
    )


def test_current_two_family_qa_receipts_calibrate_two_capacity_pools() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
    )

    expected = {
        ("codex_subscription", "gpt-5.6-terra"),
        ("antigravity_subscription", "gemini-3.5-flash-high"),
    }
    for profile_id, model in expected:
        candidate = next(
            row for row in report["rows"]
            if row["profile_id"] == profile_id and row["model"] == model
        )
        qa = next(role for role in candidate["roles"] if role["role"] == "qa")
        assert qa["status"] == "calibrated"
        assert qa["evaluation_reason"] == "adversarial_qa_two_family_6_of_6"
        assert qa["evidence_validation_errors"] == []


def test_current_two_family_test_designer_receipts_calibrate_two_pools() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
    )

    expected = {
        ("codex_subscription", "gpt-5.6-terra"),
        ("antigravity_subscription", "gemini-3.5-flash-high"),
    }
    for profile_id, model in expected:
        candidate = next(
            row for row in report["rows"]
            if row["profile_id"] == profile_id and row["model"] == model
        )
        role = next(
            item for item in candidate["roles"]
            if item["role"] == "test_designer"
        )
        assert role["status"] == "calibrated"
        assert (
            role["evaluation_reason"]
            == "independent_test_designer_two_family_6_of_6"
        )
        assert role["evidence_validation_errors"] == []


def test_current_mcp_operator_receipt_calibrates_terra_only() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    role = next(
        item for item in terra["roles"] if item["role"] == "mcp_operator"
    )
    assert role["status"] == "calibrated"
    assert (
        role["evaluation_reason"]
        == "mcp_operator_two_family_governance_6_of_6"
    )
    assert role["evidence_validation_errors"] == []


def test_tampered_mcp_operator_tool_gate_invalidates_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "p0h2d5-terra-mcp-diversity-v3-cli-0.146.0.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    families = []
    for relative in aggregate["source_receipts"]:
        family = json.loads((repo_root / relative).read_text(encoding="utf-8"))
        families.append(family)
        paths.extend(Path(item) for item in family["source_receipts"])
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(families[0]["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["checks"]["approved_tool_called"] = False
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
        repo_root=tmp_path,
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    role = next(
        item for item in terra["roles"] if item["role"] == "mcp_operator"
    )
    assert role["status"] == "partial"
    assert "evidence_receipt_invalid" in role["stale_reasons"]
    assert any(
        error.startswith("mcp_operator_diversity_sample_mcp_governance:")
        for error in role["evidence_validation_errors"]
    )


def test_tampered_test_designer_sample_invalidates_diversity_evidence(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "p0h2d4-terra-test-designer-diversity-v3-cli-0.146.0.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    families = []
    for relative in aggregate["source_receipts"]:
        family = json.loads((repo_root / relative).read_text(encoding="utf-8"))
        families.append(family)
        paths.extend(Path(item) for item in family["source_receipts"])
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(families[0]["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["mutation_evaluation"]["mutants_killed"] = 0
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
        repo_root=tmp_path,
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    role = next(
        item for item in terra["roles"] if item["role"] == "test_designer"
    )
    assert role["status"] == "partial"
    assert "evidence_receipt_invalid" in role["stale_reasons"]
    assert any(
        error.startswith("test_designer_diversity_sample_hash:")
        for error in role["evidence_validation_errors"]
    )


def test_tampered_test_designer_family_invalidates_diversity_evidence(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "p0h2d4-terra-test-designer-diversity-v3-cli-0.146.0.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    family_path = tmp_path / Path(aggregate["source_receipts"][0])
    family = json.loads(family_path.read_text(encoding="utf-8"))
    family["samples_passed"] = 2
    family_path.write_text(json.dumps(family), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.146.0-alpha.6",
            "antigravity_subscription": "1.1.8",
        },
        repo_root=tmp_path,
    )
    terra = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription"
        and row["model"] == "gpt-5.6-terra"
    )
    test_designer = next(
        role for role in terra["roles"] if role["role"] == "test_designer"
    )
    assert test_designer["status"] == "partial"
    assert "evidence_receipt_invalid" in test_designer["stale_reasons"]
    assert any(
        error.startswith("test_designer_diversity_source_")
        for error in test_designer["evidence_validation_errors"]
    )
