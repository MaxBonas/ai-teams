from datetime import datetime, timezone

from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage


def test_coverage_inventory_is_conservative_and_tracks_exact_promotions() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        observed_versions={
            "codex_subscription": "0.145.0",
            "antigravity_subscription": "1.1.5",
        },
    )

    assert report["models"] == 46
    assert report["role_pairs"] == 131
    assert report["complete"] is False
    assert report["pair_counts"]["calibrated"] == 8
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
    assert by_role["file_scout"] == "requires_canary"
    assert by_role["web_scout"] == "partial"
    assert luna["model_status"] == "partial"
    file_scout = next(role for role in luna["roles"] if role["role"] == "file_scout")
    worker = next(role for role in luna["roles"] if role["role"] == "worker")
    web_scout = next(role for role in luna["roles"] if role["role"] == "web_scout")
    assert (
        file_scout["diagnostic_reason"]
        == "low_and_medium_screening_failed_semantic_contract"
    )
    assert len(file_scout["diagnostic_receipts"]) == 2
    assert worker["diagnostic_reason"] == "low_invalid_result_and_medium_missing_report"
    assert worker["status"] == "requires_canary"
    assert (
        web_scout["evaluation_reason"]
        == "governed_mcp_behavioral_2_of_3_single_attempt"
    )
    assert len(web_scout["evidence_receipts"]) == 3

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
    assert terra_engineer["evaluation_reason"] == "coding_hidden_suite_and_ruff_3_of_3"
    assert terra_qa["status"] == "calibrated"
    assert terra_qa["evaluation_reason"] == "adversarial_test_then_fix_3_of_3"
    assert terra_test_designer["status"] == "calibrated"
    assert (
        terra_test_designer["evaluation_reason"]
        == "independent_suite_kills_hidden_mutants_3_of_3"
    )
    assert terra_mcp_operator["status"] == "calibrated"
    assert (
        terra_mcp_operator["evaluation_reason"]
        == "governed_mcp_allow_deny_health_recovery_3_of_3"
    )


def test_manual_probe_gated_model_does_not_create_automatic_canary_debt() -> None:
    report = audit_model_evaluation_coverage(
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
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
        observed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
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
    assert reviewer["status"] == "calibrated"
    assert reviewer["evaluation_reason"] == "durable_review_behavioral_3_of_3"

    deepseek = next(
        row
        for row in report["rows"]
        if row["profile_id"] == "opencode_zen_free"
        and row["model"] == "opencode/deepseek-v4-flash-free"
    )
    reviewer = next(role for role in deepseek["roles"] if role["role"] == "reviewer")
    assert reviewer["status"] == "partial"
