import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage


def test_coverage_inventory_is_conservative_and_tracks_exact_promotions() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
    )

    assert report["models"] == 46
    assert report["role_pairs"] == 131
    assert report["complete"] is False
    assert report["pair_counts"]["calibrated"] == 25
    assert report["pair_counts"]["partial"] == 5
    assert report["pair_counts"]["requires_canary"] > 0
    assert report["pair_counts"]["requires_tool_fixture"] > 0
    luna = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "codex_subscription" and row["model"] == "gpt-5.6-luna"
    )
    by_role = {row["role"]: row["status"] for row in luna["roles"]}
    assert by_role["context_curator"] == "calibrated"
    assert by_role["file_scout"] == "partial"
    assert by_role["worker"] == "calibrated"
    assert by_role["web_scout"] == "calibrated"
    assert luna["model_status"] == "partial"
    file_scout = next(role for role in luna["roles"] if role["role"] == "file_scout")
    worker = next(role for role in luna["roles"] if role["role"] == "worker")
    web_scout = next(role for role in luna["roles"] if role["role"] == "web_scout")
    assert file_scout["evaluation_reason"] == (
        "tier3_causal_quality_3_of_3_single_attempt_1_of_3"
    )
    assert file_scout["evidence_validation_errors"] == []
    assert worker["status"] == "calibrated"
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
    assert terra_reviewer["evaluated_at"] == "2026-07-22"
    assert terra_reviewer["provider_version"] == "0.145.0"
    assert terra_engineer["status"] == "calibrated"
    assert terra_engineer["evaluation_reason"] == (
        "coding_two_family_hidden_suite_6_of_6"
    )
    assert terra_engineer["evidence_validation_errors"] == []
    assert terra_qa["status"] == "calibrated"
    assert terra_qa["evaluation_reason"] == "adversarial_qa_two_family_6_of_6"
    assert terra_qa["evidence_validation_errors"] == []
    assert terra_test_designer["status"] == "calibrated"
    assert (
        terra_test_designer["evaluation_reason"]
        == "independent_test_designer_two_family_6_of_6"
    )
    assert terra_test_designer["evidence_validation_errors"] == []
    assert terra_mcp_operator["status"] == "calibrated"
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
        assert lead["status"] == "calibrated"
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
    for model_row in (sol, pro):
        auditor = next(
            role for role in model_row["roles"] if role["role"] == "quorum_auditor"
        )
        assert auditor["status"] == "calibrated"
        assert auditor["evidence_validation_errors"] == []
        assert auditor["prompt_version"] == "v2"
    for model_row in (sol, pro):
        team_lead = next(
            role for role in model_row["roles"] if role["role"] == "team_lead"
        )
        assert team_lead["status"] == "calibrated"
        assert team_lead["evidence_validation_errors"] == []


def test_manual_probe_gated_model_does_not_create_automatic_canary_debt() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
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
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
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
    assert reviewer["status"] == "calibrated"
    assert reviewer["evaluation_reason"] == "durable_review_behavioral_3_of_3"
    assert qa["status"] == "calibrated"
    assert qa["evaluation_reason"] == "antigravity_adversarial_qa_3_of_3"
    assert qa["evidence_validation_errors"] == []
    assert qa["diagnostic_reason"] == (
        "second_qa_family_attack_passed_verify_subscription_cli_timeout"
    )
    assert qa["diagnostic_validation_errors"] == []
    assert test_designer["status"] == "calibrated"
    assert (
        test_designer["evaluation_reason"]
        == "antigravity_mutation_test_designer_3_of_3"
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
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "antigravity_subscription": "1.1.5",
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
    assert gpt_roles["worker"]["status"] == "requires_canary"
    assert gpt_roles["worker"]["diagnostic_reason"].endswith("parse_failure")
    assert gpt_roles["worker"]["diagnostic_validation_errors"] == []

    gemma = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "local_gemma4_ollama"
        and row["model"] == "gemma4:26b"
    )
    gemma_roles = {row["role"]: row for row in gemma["roles"]}
    assert gemma_roles["engineer"]["status"] == "partial"
    assert gemma_roles["reviewer"]["status"] == "requires_canary"
    assert gemma_roles["test_designer"]["diagnostic_reason"] == (
        "baseline_suite_failed_despite_mutant_detection"
    )
    assert gemma_roles["test_designer"]["diagnostic_validation_errors"] == []


def test_tampered_critical_sample_invalidates_exact_role_evidence(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "critical-defaults-codex-sol-architect-aggregate.json"
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
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
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
        "antigravity-flash-high-test-designer-v2-aggregate.json"
    )
    aggregate = json.loads((repo_root / aggregate_rel).read_text(encoding="utf-8"))
    paths = [aggregate_rel, *(Path(item) for item in aggregate["source_receipts"])]
    for relative in paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    sample_path = tmp_path / Path(aggregate["source_receipts"][0])
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["mutation_evaluation"]["mutants_killed"] = 0
    sample_path.write_text(json.dumps(sample), encoding="utf-8")

    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
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
        error.startswith("antigravity_tier2_sample_evidence_hash:")
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
        "m83-qa-diversity-terra-aggregate.json"
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
    qa = next(role for role in terra["roles"] if role["role"] == "qa")
    assert qa["status"] == "partial"
    assert "evidence_receipt_invalid" in qa["stale_reasons"]
    assert any(
        error.startswith("qa_diversity_source_")
        for error in qa["evidence_validation_errors"]
    )


def test_tampered_test_designer_family_invalidates_diversity_evidence(
    tmp_path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    aggregate_rel = Path(
        "benchmarks/results/model_calibration/"
        "m83-test-designer-diversity-terra-aggregate.json"
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
    test_designer = next(
        role for role in terra["roles"] if role["role"] == "test_designer"
    )
    assert test_designer["status"] == "partial"
    assert "evidence_receipt_invalid" in test_designer["stale_reasons"]
    assert any(
        error.startswith("test_designer_diversity_source_")
        for error in test_designer["evidence_validation_errors"]
    )
