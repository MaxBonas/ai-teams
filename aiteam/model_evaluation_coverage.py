"""Cobertura conductual conservadora del catálogo por perfil+modelo+rol."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from aiteam.model_calibration import (
    CALIBRATION_MAX_AGE_DAYS,
    audit_promoted_model_calibrations,
)
from aiteam.policies import canonical_role
from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.user_config import DEFAULT_ADAPTER_PROFILES, model_options


MODEL_ROLE_EVALUATION_EVIDENCE: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "lead",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "prompt_version": "v2",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-v2-codex-sol-lead-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "lead_executor",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "prompt_version": "v2",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-v2-gemini-3.1-pro-high-lead-executor-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "quorum_auditor",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "prompt_version": "v2",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-v2-codex-sol-quorum-auditor-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "quorum_auditor",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "prompt_version": "v2",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-v2-gemini-3.1-pro-high-quorum-auditor-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "team_lead",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-codex-sol-team-lead-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "team_lead",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-gemini-3.1-pro-high-team-lead-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "lead_executor",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-codex-sol-lead-executor-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-sol",
        "role": "architect",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-codex-sol-architect-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "architect",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-gemini-3.1-pro-high-architect-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "mcp_operator",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "tier3_two_family_causal_report_v3",
        "reason": "mcp_operator_two_family_governance_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-mcp-operator-diversity-terra-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "test_designer",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "independent_test_designer_two_family_v3",
        "reason": "independent_test_designer_two_family_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-test-designer-diversity-terra-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "qa",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "adversarial_qa_two_family_v3",
        "reason": "adversarial_qa_two_family_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-qa-diversity-terra-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "engineer",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "coding_hidden_suite_two_family_v4",
        "reason": "coding_two_family_hidden_suite_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-coding-diversity-terra-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "reviewer",
        "status": "calibrated",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "durable_review_behavioral_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-terra-durable-review-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "worker",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "tier3_two_family_causal_report_v3",
        "reason": "tier3_worker_two_family_causal_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-tier3-diversity-luna-worker-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "web_scout",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "tier3_two_family_causal_report_v3",
        "reason": "tier3_web_scout_two_family_causal_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-tier3-diversity-luna-web-scout-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "file_scout",
        "status": "partial",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.145.0",
        "contract_version": "tier3_causal_report_v2",
        "reason": "tier3_causal_quality_3_of_3_single_attempt_1_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-luna-file-scout-low-v2-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "lead",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "prompt_version": "v2",
        "reason": "critical_role_hidden_causal_contract_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/critical-defaults-v2-gemini-3.1-pro-high-lead-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-high",
        "role": "reviewer",
        "status": "calibrated",
        "evaluated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "reason": "durable_review_behavioral_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-durable-review-v4-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-high",
        "role": "qa",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "adversarial_qa_fix_cycle_v2",
        "reason": "antigravity_adversarial_qa_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-flash-high-qa-v2-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-high",
        "role": "test_designer",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "independent_test_designer_mutation_v2",
        "reason": "antigravity_mutation_test_designer_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-flash-high-test-designer-v2-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-medium",
        "role": "worker",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "tier3_two_family_causal_report_v3",
        "reason": "tier3_worker_two_family_causal_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "m83-tier3-diversity-flash-medium-worker-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-low",
        "role": "context_curator",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "two_causal_slices_three_seeds_each_v1",
        "reason": "antigravity_context_curator_causal_6_of_6",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-flash-low-context-curator-v1-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-low",
        "role": "file_scout",
        "status": "calibrated",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "tier3_causal_report_v2",
        "reason": "antigravity_tier3_causal_report_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-flash-low-file-scout-v2-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-low",
        "role": "worker",
        "status": "partial",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "contract_version": "tier3_causal_report_v2",
        "reason": "antigravity_tier3_quality_2_of_3_single_attempt_2_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-flash-low-worker-v2-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gpt-oss-120b-medium",
        "role": "file_scout",
        "status": "partial",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "scout_surrogate_3_of_3_but_exact_durable_parse_failed",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-1.1.5-role-calibration-aggregate.json",
            "benchmarks/results/model_calibration/"
            "antigravity-gpt-oss-file-scout-v2-seed-1.json",
        ),
    },
    {
        "profile_id": "opencode_zen_free",
        "model": "opencode/deepseek-v4-flash-free",
        "role": "reviewer",
        "status": "partial",
        "evaluated_at": "2026-07-21",
        "provider_version": "1.18.4",
        "reason": "durable_review_behavioral_1_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/opencode-durable-review-v1-laguna-vs-deepseek-aggregate.json",
        ),
    },
    {
        "profile_id": "local_gemma4_ollama",
        "model": "gemma4:26b",
        "role": "engineer",
        "status": "partial",
        "evaluated_at": "2026-07-23",
        "provider_version": "0.32.1",
        "reason": "local_exact_engineer_hidden_suite_1_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/"
            "local-gemma26b-engineer-aggregate.json",
        ),
    },
)


# Negative screenings remain visible without being promoted to ``partial``.
# A screening can defer another identical run only when it declares an explicit
# material-change policy and its version, age and receipt gates still hold.
DEFERRED_UNTIL_MATERIAL_CHANGE = "deferred_until_material_change"
MATERIAL_CHANGE_TRIGGERS = (
    "provider_or_cli_version_changed",
    "model_or_catalog_identity_changed",
    "prompt_or_role_contract_changed",
    "transport_or_tooling_changed",
    "diagnostic_age_exceeded",
)

MODEL_ROLE_EVALUATION_DIAGNOSTICS: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-low",
        "role": "web_scout",
        "evaluated_at": "2026-07-24",
        "provider_version": "1.1.6",
        "reason": "governed_mcp_transport_unsupported_fail_fast",
        "rerun_policy": "material_change_only",
        "receipts": (
            "benchmarks/results/model_calibration/"
            "antigravity-1.1.6-flash-low-web-scout-seed-1.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-low",
        "role": "file_scout",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "second_file_scout_family_submit_work_json_parse_error",
        "rerun_policy": "material_change_only",
        "receipts": (
            "benchmarks/results/model_calibration/"
            "m83-idempotency-flash-low-file-scout-seed-1.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-high",
        "role": "test_designer",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "second_test_designer_family_seed1_passed_seed2_cli_timeout",
        "rerun_policy": "material_change_only",
        "receipts": (
            "benchmarks/results/model_calibration/"
            "m83-job-state-flash-high-seed-1.json",
            "benchmarks/results/model_calibration/"
            "m83-job-state-flash-high-seed-2.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.5-flash-high",
        "role": "qa",
        "evaluated_at": "2026-07-23",
        "provider_version": "1.1.5",
        "reason": "second_qa_family_attack_passed_verify_subscription_cli_timeout",
        "rerun_policy": "material_change_only",
        "receipts": (
            "benchmarks/results/model_calibration/"
            "m83-webhook-flash-high-seed-1.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "claude-sonnet-4-6",
        "role": "engineer",
        "evaluated_at": "2026-07-24",
        "provider_version": "1.1.6",
        "reason": (
            "provider_1_1_6_revalidation_hidden_3_of_3_but_ruff_7_fail_fast"
        ),
        "rerun_policy": "material_change_only",
        "receipts": (
            "benchmarks/results/model_calibration/"
            "m83-config-redactor-sonnet-seed-1.json",
            "benchmarks/results/model_calibration/"
            "antigravity-1.1.6-config-redactor-sonnet-seed-1.json",
        ),
    },
    *(
        {
            "profile_id": "opencode_zen_free",
            "model": model,
            "role": role,
            "evaluated_at": "2026-07-23",
            "provider_version": "1.18.4",
            "reason": "structured_output_transport_unchanged_closed_without_inference",
            "rerun_policy": "material_change_only",
            "receipts": (
                "benchmarks/results/model_calibration/"
                "opencode-1.18.4-negative-closure-v1.json",
            ),
        }
        for model, roles in (
            (
                "opencode/nemotron-3-ultra-free",
                ("lead", "team_lead", "architect", "quorum_auditor"),
            ),
            ("opencode/deepseek-v4-flash-free", ("reviewer",)),
            ("opencode/mimo-v2.5-free", ("reviewer", "web_scout")),
            (
                "opencode/north-mini-code-free",
                ("file_scout", "web_scout", "context_curator"),
            ),
        )
        for role in roles
    ),
    *(
        {
            "profile_id": "antigravity_subscription",
            "model": "gpt-oss-120b-medium",
            "role": role,
            "evaluated_at": "2026-07-23",
            "provider_version": "1.1.5",
            "reason": "exact_durable_contract_submit_work_parse_failure",
            "rerun_policy": "material_change_only",
            "receipts": (receipt,),
        }
        for role, receipt in (
            (
                "file_scout",
                "benchmarks/results/model_calibration/"
                "antigravity-gpt-oss-file-scout-v2-seed-1.json",
            ),
            (
                "web_scout",
                "benchmarks/results/model_calibration/"
                "antigravity-gpt-oss-web-scout-v2-seed-1-retry.json",
            ),
            (
                "worker",
                "benchmarks/results/model_calibration/"
                "antigravity-gpt-oss-worker-v2-seed-1.json",
            ),
        )
    ),
    *(
        {
            "profile_id": profile_id,
            "model": model,
            "role": role,
            "evaluated_at": "2026-07-23",
            "provider_version": "0.32.1",
            "reason": reason,
            "rerun_policy": "material_change_only",
            "receipts": (receipt,),
        }
        for profile_id, model, role, reason, receipt in (
            (
                "local_qwen_ollama",
                "qwen2.5-coder:14b",
                "file_scout",
                "exact_contract_incorrect_checkout_claim_and_missing_report",
                "benchmarks/results/model_calibration/"
                "local-qwen14b-file-scout-v2-seed-1.json",
            ),
            (
                "local_qwen_ollama",
                "qwen2.5-coder:14b",
                "context_curator",
                "exact_contract_zero_of_nine_anchors",
                "benchmarks/results/model_calibration/"
                "local-qwen14b-context-auth-v1-seed-1.json",
            ),
            (
                "local_gemma4_ollama",
                "gemma4:e4b",
                "file_scout",
                "exact_contract_missing_report_and_failed_retry",
                "benchmarks/results/model_calibration/"
                "local-gemma-e4b-file-scout-v2-seed-1.json",
            ),
            (
                "local_gemma4_ollama",
                "gemma4:e4b",
                "context_curator",
                "exact_contract_zero_of_nine_anchors",
                "benchmarks/results/model_calibration/"
                "local-gemma-e4b-context-auth-v1-seed-1.json",
            ),
            (
                "local_gemma4_ollama",
                "gemma4:e4b",
                "worker",
                "exact_contract_generic_unrelated_artifact",
                "benchmarks/results/model_calibration/"
                "local-gemma-e4b-worker-v2-seed-1.json",
            ),
            (
                "local_gemma4_ollama",
                "gemma4:26b",
                "reviewer",
                "exact_durable_review_reject_failed",
                "benchmarks/results/model_calibration/"
                "local-gemma26b-reviewer-seed-1.json",
            ),
            (
                "local_gemma4_ollama",
                "gemma4:26b",
                "test_designer",
                "baseline_suite_failed_despite_mutant_detection",
                "benchmarks/results/model_calibration/"
                "local-gemma26b-test-designer-v2-seed-1.json",
            ),
        )
    ),
)


def _canonical_response_hash(response: Any) -> str:
    payload = json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _critical_role_evidence_errors(
    evidence: dict[str, Any], *, evidence_root: Path, receipts: list[str]
) -> list[str]:
    if evidence.get("reason") != "critical_role_hidden_causal_contract_6_of_6":
        return []
    if len(receipts) != 1:
        return ["critical_aggregate_missing_or_duplicate"]
    aggregate_path = evidence_root / receipts[0]
    try:
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["critical_aggregate_invalid"]
    conclusion = aggregate.get("conclusion") or {}
    integrity = aggregate.get("integrity") or {}
    expected_prompt_version = str(evidence.get("prompt_version") or "v1")
    aggregate_prompt_version = str(aggregate.get("prompt_version") or "v1")
    checks = {
        "aggregate_benchmark": (
            aggregate.get("benchmark") == "critical_default_role_canary_aggregate"
        ),
        "aggregate_profile": aggregate.get("profile_id") == evidence.get("profile_id"),
        "aggregate_model": aggregate.get("model") == evidence.get("model"),
        "aggregate_role": canonical_role(str(aggregate.get("role") or ""))
        == canonical_role(str(evidence.get("role") or "")),
        "aggregate_matrix": aggregate.get("matrix_complete") is True,
        "aggregate_samples": (
            aggregate.get("samples_observed") == 6
            and aggregate.get("samples_passed") == 6
        ),
        "aggregate_calibrated": conclusion.get("exact_pair_calibrated") is True,
        "aggregate_no_default_mutation": conclusion.get("default_change_allowed") is False,
        "aggregate_sources_bound": integrity.get("sources_bound") is True,
        "aggregate_responses_hashed": integrity.get("responses_hashed") is True,
        "aggregate_prompt_version": aggregate_prompt_version
        == expected_prompt_version,
        "aggregate_single_prompt_version": (
            integrity.get("single_prompt_version") is True
            or (
                "single_prompt_version" not in integrity
                and expected_prompt_version == "v1"
            )
        ),
    }
    errors = [name for name, valid in checks.items() if not valid]
    manifest = aggregate.get("sample_manifest") or []
    source_receipts = aggregate.get("source_receipts") or []
    expected_cells = {
        (case_id, seed)
        for case_id in ("tenant_queue_migration", "auth_rollout_incident")
        for seed in (1, 2, 3)
    }
    observed_cells = {
        (row.get("case_id"), row.get("seed"))
        for row in manifest
        if isinstance(row, dict)
    }
    if len(manifest) != 6 or observed_cells != expected_cells:
        errors.append("sample_manifest_matrix")
    manifest_receipts = [
        str(row.get("receipt") or "") for row in manifest if isinstance(row, dict)
    ]
    if (
        len(source_receipts) != 6
        or len(set(source_receipts)) != 6
        or source_receipts != manifest_receipts
    ):
        errors.append("sample_source_receipts")
    root = evidence_root.resolve()
    for row in manifest:
        if not isinstance(row, dict):
            continue
        receipt = str(row.get("receipt") or "")
        source_path = (evidence_root / receipt).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            errors.append(f"sample_outside_root:{receipt}")
            continue
        try:
            sample = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"sample_invalid:{receipt}")
            continue
        sample_checks = {
            "benchmark": sample.get("benchmark") == "critical_default_role_canary",
            "profile": sample.get("profile_id") == evidence.get("profile_id"),
            "model": sample.get("model") == evidence.get("model"),
            "role": canonical_role(str(sample.get("role") or ""))
            == canonical_role(str(evidence.get("role") or "")),
            "case": sample.get("case_id") == row.get("case_id"),
            "seed": sample.get("seed") == row.get("seed"),
            "status": sample.get("status") == "completed",
            "ok": sample.get("ok") is True and row.get("ok") is True,
            "evaluation": (sample.get("evaluation") or {}).get("contract_passed")
            is True,
            "version": str(sample.get("cli_version") or "").endswith(
                str(evidence.get("provider_version") or "")
            ),
            "prompt_version": str(sample.get("prompt_version") or "v1")
            == expected_prompt_version,
            "response_hash": _canonical_response_hash(sample.get("response"))
            == row.get("response_sha256"),
        }
        errors.extend(
            f"sample_{name}:{receipt}"
            for name, valid in sample_checks.items()
            if not valid
        )
    return errors


def _tier3_role_evidence_errors(
    evidence: dict[str, Any], *, evidence_root: Path, receipts: list[str]
) -> list[str]:
    reason = str(evidence.get("reason") or "")
    expected_metrics = {
        "tier3_causal_report_contract_3_of_3": {
            "samples_passed": 3,
            "samples_artifact_passed": 3,
            "samples_single_attempt": 3,
            "calibrated": True,
        },
        "tier3_causal_quality_3_of_3_single_attempt_1_of_3": {
            "samples_passed": 1,
            "samples_artifact_passed": 3,
            "samples_single_attempt": 1,
            "calibrated": False,
            "benchmark": "codex_tier_role_canary_aggregate",
            "sample_benchmark": "codex_luna_tier3_role_canary",
            "effort": "low",
        },
        "antigravity_tier3_causal_report_3_of_3": {
            "samples_passed": 3,
            "samples_artifact_passed": 3,
            "samples_single_attempt": 3,
            "calibrated": True,
            "benchmark": "codex_tier_role_canary_aggregate",
            "sample_benchmark": "tier3_role_canary",
            "effort": None,
            "usage": "unknown",
        },
        "antigravity_tier3_quality_2_of_3_single_attempt_2_of_3": {
            "samples_passed": 2,
            "samples_artifact_passed": 2,
            "samples_single_attempt": 2,
            "calibrated": False,
            "benchmark": "codex_tier_role_canary_aggregate",
            "sample_benchmark": "tier3_role_canary",
            "effort": None,
            "usage": "unknown",
        },
    }.get(reason)
    if expected_metrics is None:
        return []
    if len(receipts) != 1:
        return ["tier3_aggregate_missing_or_duplicate"]
    try:
        aggregate = json.loads(
            (evidence_root / receipts[0]).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ["tier3_aggregate_invalid"]
    conclusion = aggregate.get("conclusion") or {}
    integrity = aggregate.get("integrity") or {}
    expected_contract = str(evidence.get("contract_version") or "")
    checks = {
        "aggregate_benchmark": aggregate.get("benchmark")
        == expected_metrics.get("benchmark", "codex_tier_role_canary_aggregate"),
        "aggregate_profile": aggregate.get("profile_id") == evidence.get("profile_id"),
        "aggregate_model": aggregate.get("model") == evidence.get("model"),
        "aggregate_role": canonical_role(str(aggregate.get("role") or ""))
        == canonical_role(str(evidence.get("role") or "")),
        "aggregate_effort": aggregate.get("reasoning_effort")
        == expected_metrics.get("effort", "low"),
        "aggregate_contract": aggregate.get("contract_version") == expected_contract,
        "aggregate_matrix": aggregate.get("matrix_complete") is True,
        "aggregate_samples_passed": aggregate.get("samples_passed")
        == expected_metrics["samples_passed"],
        "aggregate_artifact_passed": aggregate.get("samples_artifact_passed")
        == expected_metrics["samples_artifact_passed"],
        "aggregate_single_attempt": aggregate.get("samples_single_attempt")
        == expected_metrics["samples_single_attempt"],
        "aggregate_calibrated": conclusion.get("exact_pair_calibrated")
        is expected_metrics["calibrated"],
        "aggregate_no_default_mutation": conclusion.get("default_change_allowed")
        is False,
        "aggregate_sources_bound": integrity.get("sources_bound") is True,
        "aggregate_artifacts_hashed": integrity.get("artifacts_hashed") is True,
    }
    if expected_metrics.get("usage"):
        checks["aggregate_usage"] = (
            (aggregate.get("usage") or {}).get("telemetry_status")
            == expected_metrics["usage"]
        )
    errors = [name for name, valid in checks.items() if not valid]
    manifest = aggregate.get("sample_manifest") or []
    source_receipts = aggregate.get("source_receipts") or []
    observed_seeds = {
        row.get("seed") for row in manifest if isinstance(row, dict)
    }
    if len(manifest) != 3 or observed_seeds != {1, 2, 3}:
        errors.append("tier3_sample_manifest_matrix")
    manifest_receipts = [
        str(row.get("receipt") or "") for row in manifest if isinstance(row, dict)
    ]
    if (
        len(source_receipts) != 3
        or len(set(source_receipts)) != 3
        or source_receipts != manifest_receipts
    ):
        errors.append("tier3_sample_source_receipts")
    root = evidence_root.resolve()
    for row in manifest:
        if not isinstance(row, dict):
            continue
        receipt = str(row.get("receipt") or "")
        source_path = (evidence_root / receipt).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            errors.append(f"tier3_sample_outside_root:{receipt}")
            continue
        try:
            sample = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"tier3_sample_invalid:{receipt}")
            continue
        sample_checks = sample.get("checks") or {}
        manifest_checks = row.get("checks") or {}
        expected_ok = all(
            sample_checks.get(name) is True
            for name in (
                "artifact_contract",
                "valid_assignee_report",
                "issue_done",
                "run_completed",
                "workspace_unchanged",
                "single_attempt",
            )
        )
        validations = {
            "benchmark": sample.get("benchmark")
            == expected_metrics.get(
                "sample_benchmark", "codex_luna_tier3_role_canary"
            ),
            "profile": sample.get("profile_id") == evidence.get("profile_id"),
            "model": sample.get("model") == evidence.get("model"),
            "role": canonical_role(str(sample.get("role") or ""))
            == canonical_role(str(evidence.get("role") or "")),
            "effort": sample.get("reasoning_effort")
            == expected_metrics.get("effort", "low"),
            "contract": sample.get("contract_version") == expected_contract,
            "seed": sample.get("seed") == row.get("seed"),
            "artifact": row.get("checks", {}).get("artifact_contract")
            is (sample_checks.get("artifact_contract") is True),
            "report": sample_checks.get("valid_assignee_report") is True,
            "issue_done": sample_checks.get("issue_done") is True,
            "run_completed": sample_checks.get("run_completed") is True,
            "workspace": sample_checks.get("workspace_unchanged") is True,
            "ok": sample.get("ok") is expected_ok and row.get("ok") is expected_ok,
            "manifest_checks": manifest_checks
            == {
                "artifact_contract": sample_checks.get("artifact_contract") is True,
                "valid_assignee_report": sample_checks.get("valid_assignee_report")
                is True,
                "single_attempt": sample_checks.get("single_attempt") is True,
            },
            "artifact_hash": hashlib.sha256(
                str(sample.get("artifact") or "").encode("utf-8")
            ).hexdigest()
            == row.get("artifact_sha256"),
        }
        errors.extend(
            f"tier3_sample_{name}:{receipt}"
            for name, valid in validations.items()
            if not valid
        )
    return errors


def _context_curator_evidence_errors(
    evidence: dict[str, Any], *, evidence_root: Path, receipts: list[str]
) -> list[str]:
    if evidence.get("reason") != "antigravity_context_curator_causal_6_of_6":
        return []
    if len(receipts) != 1:
        return ["context_curator_aggregate_missing_or_duplicate"]
    try:
        aggregate = json.loads(
            (evidence_root / receipts[0]).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ["context_curator_aggregate_invalid"]
    conclusion = aggregate.get("conclusion") or {}
    integrity = aggregate.get("integrity") or {}
    checks = {
        "aggregate_benchmark": aggregate.get("benchmark")
        == "context_curator_role_canary_aggregate",
        "aggregate_profile": aggregate.get("profile_id") == evidence.get("profile_id"),
        "aggregate_model": aggregate.get("model") == evidence.get("model"),
        "aggregate_role": aggregate.get("role") == "context_curator",
        "aggregate_effort": aggregate.get("reasoning_effort") is None,
        "aggregate_contract": aggregate.get("contract_version")
        == evidence.get("contract_version"),
        "aggregate_matrix": aggregate.get("matrix_complete") is True,
        "aggregate_samples": aggregate.get("samples_passed") == 6,
        "aggregate_calibrated": conclusion.get("exact_pair_calibrated") is True,
        "aggregate_no_default_mutation": conclusion.get("default_change_allowed")
        is False,
        "aggregate_sources_bound": integrity.get("sources_bound") is True,
        "aggregate_fixtures_hashed": integrity.get("fixtures_hashed") is True,
        "aggregate_artifacts_hashed": integrity.get("artifacts_hashed") is True,
        "aggregate_usage_unknown": (aggregate.get("usage") or {}).get(
            "telemetry_status"
        )
        == "unknown",
    }
    errors = [name for name, valid in checks.items() if not valid]
    manifest = aggregate.get("sample_manifest") or []
    source_receipts = aggregate.get("source_receipts") or []
    expected_cells = {
        (case_id, seed)
        for case_id in ("auth_migration", "queue_rollout")
        for seed in (1, 2, 3)
    }
    observed_cells = {
        (row.get("case_id"), row.get("seed"))
        for row in manifest
        if isinstance(row, dict)
    }
    if len(manifest) != 6 or observed_cells != expected_cells:
        errors.append("context_curator_sample_manifest_matrix")
    manifest_receipts = [
        str(row.get("receipt") or "") for row in manifest if isinstance(row, dict)
    ]
    if (
        len(source_receipts) != 6
        or len(set(source_receipts)) != 6
        or source_receipts != manifest_receipts
    ):
        errors.append("context_curator_sample_source_receipts")
    fixture_paths = {
        "auth_migration": (
            "benchmarks/context_quality/auth_migration_thread.md",
            "benchmarks/context_quality/auth_migration_rubric.json",
        ),
        "queue_rollout": (
            "benchmarks/context_quality/queue_rollout_thread.md",
            "benchmarks/context_quality/queue_rollout_rubric.json",
        ),
    }
    root = evidence_root.resolve()
    for row in manifest:
        if not isinstance(row, dict):
            continue
        receipt = str(row.get("receipt") or "")
        source_path = (evidence_root / receipt).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            errors.append(f"context_curator_sample_outside_root:{receipt}")
            continue
        try:
            sample = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"context_curator_sample_invalid:{receipt}")
            continue
        case_id = str(row.get("case_id") or "")
        fixture_pair = fixture_paths.get(case_id)
        if fixture_pair:
            try:
                source_fixture = (evidence_root / fixture_pair[0]).read_text(
                    encoding="utf-8"
                )
                rubric_fixture = json.loads(
                    (evidence_root / fixture_pair[1]).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                errors.append(f"context_curator_fixture_invalid:{case_id}")
                expected_source_hash = ""
                expected_rubric_hash = ""
            else:
                expected_source_hash = hashlib.sha256(
                    source_fixture.encode("utf-8")
                ).hexdigest()
                expected_rubric_hash = hashlib.sha256(
                    json.dumps(
                        rubric_fixture, ensure_ascii=False, sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
        else:
            expected_source_hash = ""
            expected_rubric_hash = ""
        artifact_hash = hashlib.sha256(
            json.dumps(
                {
                    "summary": sample.get("summary"),
                    "causal_units": sample.get("causal_units"),
                    "criteria": sample.get("criteria"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        runtime = sample.get("runtime") or {}
        validations = {
            "profile": sample.get("profile_id") == evidence.get("profile_id"),
            "model": (sample.get("execution_config") or {}).get("model")
            == evidence.get("model"),
            "role": (sample.get("execution_config") or {}).get("role")
            == "context_curator",
            "effort": (sample.get("execution_config") or {}).get(
                "reasoning_effort_override"
            )
            is None,
            "contract": sample.get("contract_version")
            == evidence.get("contract_version"),
            "case": sample.get("case_id") == case_id,
            "seed": sample.get("seed") == row.get("seed"),
            "accepted": sample.get("accepted") is True
            and row.get("accepted") is True,
            "single_attempt": runtime.get("attempts") == 1
            and row.get("single_attempt") is True,
            "issue_done": runtime.get("issue_status") == "done",
            "run_completed": (runtime.get("run") or {}).get("status")
            == "completed",
            "usage_unknown": runtime.get("telemetry_status") == "unknown",
            "source_hash": sample.get("source_sha256")
            == row.get("source_sha256")
            == expected_source_hash,
            "rubric_hash": sample.get("rubric_sha256")
            == row.get("rubric_sha256")
            == expected_rubric_hash,
            "artifact_hash": artifact_hash == row.get("artifact_sha256"),
        }
        errors.extend(
            f"context_curator_sample_{name}:{receipt}"
            for name, valid in validations.items()
            if not valid
        )
    return errors


def _antigravity_tier2_evidence_errors(
    evidence: dict[str, Any], *, evidence_root: Path, receipts: list[str]
) -> list[str]:
    reason = str(evidence.get("reason") or "")
    contract = {
        "antigravity_adversarial_qa_3_of_3": {
            "benchmark": "codex_terra_adversarial_qa_aggregate",
            "role": "qa",
        },
        "antigravity_mutation_test_designer_3_of_3": {
            "benchmark": "codex_terra_independent_test_designer_aggregate",
            "role": "test_designer",
        },
    }.get(reason)
    if contract is None:
        return []
    if len(receipts) != 1:
        return ["antigravity_tier2_aggregate_missing_or_duplicate"]
    try:
        aggregate = json.loads(
            (evidence_root / receipts[0]).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ["antigravity_tier2_aggregate_invalid"]
    conclusion = aggregate.get("conclusion") or {}
    integrity = aggregate.get("integrity") or {}
    expected_contract = str(evidence.get("contract_version") or "")
    checks = {
        "aggregate_benchmark": aggregate.get("benchmark") == contract["benchmark"],
        "aggregate_profile": aggregate.get("profile_id") == evidence.get("profile_id"),
        "aggregate_model": aggregate.get("model") == evidence.get("model"),
        "aggregate_role": aggregate.get("role") == contract["role"],
        "aggregate_contract": aggregate.get("contract_version") == expected_contract,
        "aggregate_matrix": aggregate.get("matrix_complete") is True,
        "aggregate_samples": aggregate.get("samples_passed") == 3,
        "aggregate_calibrated": conclusion.get("exact_pair_calibrated") is True,
        "aggregate_no_default_mutation": conclusion.get("default_change_allowed")
        is False,
        "aggregate_sources_bound": integrity.get("sources_bound") is True,
        "aggregate_evidence_hashed": integrity.get("evidence_hashed") is True,
        "aggregate_usage_unknown": (aggregate.get("usage") or {}).get(
            "telemetry_status"
        )
        == "unknown",
    }
    errors = [name for name, valid in checks.items() if not valid]
    manifest = aggregate.get("sample_manifest") or []
    source_receipts = aggregate.get("source_receipts") or []
    observed_seeds = {
        row.get("seed") for row in manifest if isinstance(row, dict)
    }
    if len(manifest) != 3 or observed_seeds != {1, 2, 3}:
        errors.append("antigravity_tier2_sample_manifest_matrix")
    manifest_receipts = [
        str(row.get("receipt") or "") for row in manifest if isinstance(row, dict)
    ]
    if (
        len(source_receipts) != 3
        or len(set(source_receipts)) != 3
        or source_receipts != manifest_receipts
    ):
        errors.append("antigravity_tier2_sample_source_receipts")
    root = evidence_root.resolve()
    for row in manifest:
        if not isinstance(row, dict):
            continue
        receipt = str(row.get("receipt") or "")
        source_path = (evidence_root / receipt).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            errors.append(f"antigravity_tier2_sample_outside_root:{receipt}")
            continue
        try:
            sample = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"antigravity_tier2_sample_invalid:{receipt}")
            continue
        if contract["role"] == "qa":
            evidence_payload = {
                "checks": sample.get("checks"),
                "attack_evaluation": sample.get("attack_evaluation"),
                "failing_test_run": sample.get("failing_test_run"),
            }
            runtime_valid = all(
                (sample.get("phases") or {}).get(phase, {}).get("run", {}).get(
                    "status"
                )
                == "completed"
                for phase in ("attack", "verify_fix")
            )
        else:
            evidence_payload = {
                "checks": sample.get("checks"),
                "mutation_evaluation": sample.get("mutation_evaluation"),
                "authored_files": sample.get("authored_files"),
                "report": sample.get("report"),
            }
            runtime_valid = (sample.get("run") or {}).get("status") == "completed"
        validations = {
            "profile": sample.get("profile_id") == evidence.get("profile_id"),
            "model": sample.get("model") == evidence.get("model"),
            "role": sample.get("role") == contract["role"],
            "contract": sample.get("contract_version") == expected_contract,
            "seed": sample.get("seed") == row.get("seed"),
            "ok": sample.get("ok") is True and row.get("ok") is True,
            "checks": bool(sample.get("checks"))
            and all((sample.get("checks") or {}).values()),
            "runtime": runtime_valid,
            "evidence_hash": _canonical_response_hash(evidence_payload)
            == row.get("evidence_sha256"),
        }
        errors.extend(
            f"antigravity_tier2_sample_{name}:{receipt}"
            for name, valid in validations.items()
            if not valid
        )
    return errors


def _role_diversity_evidence_errors(
    evidence: dict[str, Any], *, evidence_root: Path, receipts: list[str]
) -> list[str]:
    config = {
        "coding_two_family_hidden_suite_6_of_6": {
            "prefix": "coding_diversity",
            "benchmark": "coding_behavioral_diversity_aggregate",
            "contract": "coding_hidden_suite_two_family_v4",
            "families": {"cli_conversor", "config_redactor"},
        },
        "adversarial_qa_two_family_6_of_6": {
            "prefix": "qa_diversity",
            "benchmark": "qa_behavioral_diversity_aggregate",
            "contract": "adversarial_qa_two_family_v3",
            "families": {"authorization_boundary", "webhook_replay_boundary"},
        },
        "independent_test_designer_two_family_6_of_6": {
            "prefix": "test_designer_diversity",
            "benchmark": "test_designer_behavioral_diversity_aggregate",
            "contract": "independent_test_designer_two_family_v3",
            "families": {
                "pricing_boundary_mutation",
                "job_state_machine_mutation",
            },
        },
        "tier3_worker_two_family_causal_6_of_6": {
            "prefix": "tier3_worker_diversity",
            "benchmark": "tier3_behavioral_diversity_aggregate",
            "contract": "tier3_two_family_causal_report_v3",
            "role": "worker",
            "families": {
                "release_rollback_checklist",
                "incident_dependency_handoff",
            },
            "tier3_sources": True,
        },
        "tier3_web_scout_two_family_causal_6_of_6": {
            "prefix": "tier3_web_scout_diversity",
            "benchmark": "tier3_behavioral_diversity_aggregate",
            "contract": "tier3_two_family_causal_report_v3",
            "role": "web_scout",
            "families": {
                "governed_advisory_lookup",
                "governed_queue_advisory_lookup",
            },
            "tier3_sources": True,
        },
        "mcp_operator_two_family_governance_6_of_6": {
            "prefix": "mcp_operator_diversity",
            "benchmark": "tier3_behavioral_diversity_aggregate",
            "contract": "tier3_two_family_causal_report_v3",
            "role": "mcp_operator",
            "families": {
                "advisory_recovery_governance",
                "dependency_policy_governance",
            },
            "tier3_sources": True,
        },
    }.get(str(evidence.get("reason") or ""))
    if config is None:
        return []
    prefix = str(config["prefix"])
    if len(receipts) != 1:
        return [f"{prefix}_aggregate_missing_or_duplicate"]
    try:
        aggregate = json.loads(
            (evidence_root / receipts[0]).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return [f"{prefix}_aggregate_invalid"]
    conclusion = aggregate.get("conclusion") or {}
    integrity = aggregate.get("integrity") or {}
    checks = {
        "benchmark": aggregate.get("benchmark") == config["benchmark"],
        "profile": aggregate.get("profile_id") == evidence.get("profile_id"),
        "model": aggregate.get("model") == evidence.get("model"),
        "role": (
            canonical_role(str(aggregate.get("role") or ""))
            == canonical_role(str(config["role"]))
            if config.get("role")
            else True
        ),
        "contract": (
            aggregate.get("contract_version")
            == evidence.get("contract_version")
            == config["contract"]
        ),
        "families": set(aggregate.get("case_families") or ())
        == config["families"],
        "family_count": aggregate.get("case_family_count") == 2,
        "samples": aggregate.get("samples_total") == 6,
        "calibrated": conclusion.get("exact_pair_calibrated") is True,
        "diversity": conclusion.get("case_diversity_passed") is True,
        "no_default": conclusion.get("default_change_allowed") is False,
        "same_pair": integrity.get("same_exact_pair") is True,
        "two_families": integrity.get("two_distinct_families") is True,
        "sources_bound": integrity.get("sources_bound") is True,
        "sources_hashed": integrity.get("sources_hashed") is True,
    }
    errors = [f"{prefix}_{name}" for name, valid in checks.items() if not valid]
    sources = list(aggregate.get("source_receipts") or ())
    hashes = list(aggregate.get("source_sha256") or ())
    if len(sources) != 2 or len(hashes) != 2:
        return [*errors, f"{prefix}_source_matrix"]
    for source, expected_hash in zip(sources, hashes, strict=True):
        try:
            family = json.loads((evidence_root / source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"{prefix}_source_invalid:{source}")
            continue
        observed_hash = hashlib.sha256(
            json.dumps(
                family,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        family_checks = {
            "hash": observed_hash == expected_hash,
            "profile": family.get("profile_id") == evidence.get("profile_id"),
            "model": family.get("model") == evidence.get("model"),
            "matrix": family.get("matrix_complete") is True,
            "samples": family.get("samples_passed") == 3,
            "calibrated": (
                family.get("conclusion", {}).get("exact_pair_calibrated") is True
            ),
            "sources": len(family.get("source_receipts") or ()) == 3,
            "role": (
                canonical_role(str(family.get("role") or ""))
                == canonical_role(str(config["role"]))
                if config.get("role")
                else True
            ),
            "artifact_samples": (
                family.get("samples_artifact_passed") == 3
                if config.get("tier3_sources")
                else True
            ),
            "single_attempt_samples": (
                family.get("samples_single_attempt") == 3
                if config.get("tier3_sources")
                else True
            ),
        }
        errors.extend(
            f"{prefix}_source_{name}:{source}"
            for name, valid in family_checks.items()
            if not valid
        )
        if not config.get("tier3_sources"):
            continue
        manifest = list(family.get("sample_manifest") or ())
        family_sources = list(family.get("source_receipts") or ())
        family_name = str(family.get("case_family") or "")
        if len(manifest) != 3 or len(family_sources) != 3:
            errors.append(f"{prefix}_sample_matrix:{source}")
            continue
        for row, sample_receipt in zip(manifest, family_sources, strict=True):
            if not isinstance(row, dict) or row.get("receipt") != sample_receipt:
                errors.append(f"{prefix}_sample_manifest_receipt:{source}")
                continue
            try:
                sample = json.loads(
                    (evidence_root / sample_receipt).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                errors.append(f"{prefix}_sample_invalid:{sample_receipt}")
                continue
            sample_checks = sample.get("checks") or {}
            observed_family = str(sample.get("case_family") or "")
            if not observed_family:
                observed_family = {
                    "worker": "release_rollback_checklist",
                    "file_scout": "tenant_checkout_inspection",
                    "web_scout": "governed_advisory_lookup",
                    "mcp_operator": "advisory_recovery_governance",
                }.get(str(config.get("role") or ""), "")
            validations = {
                "profile": sample.get("profile_id") == evidence.get("profile_id"),
                "model": sample.get("model") == evidence.get("model"),
                "role": canonical_role(str(sample.get("role") or ""))
                == canonical_role(str(config["role"])),
                "contract": sample.get("contract_version")
                == "tier3_causal_report_v2",
                "family": observed_family == family_name,
                "seed": sample.get("seed") == row.get("seed"),
                "checks": all(
                    sample_checks.get(name) is True
                    for name in (
                        "artifact_contract",
                        "valid_assignee_report",
                        "issue_done",
                        "run_completed",
                        "workspace_unchanged",
                        "single_attempt",
                    )
                ),
                "artifact_hash": hashlib.sha256(
                    str(sample.get("artifact") or "").encode("utf-8")
                ).hexdigest()
                == row.get("artifact_sha256"),
            }
            errors.extend(
                f"{prefix}_sample_{name}:{sample_receipt}"
                for name, valid in validations.items()
                if not valid
            )
    return errors


def audit_model_evaluation_coverage(
    *,
    observed_at: datetime | date,
    observed_versions: dict[str, str | None],
    repo_root: Path | None = None,
    executable_models_by_profile: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Enumera qué destinos recomendados tienen calibración fresca exacta.

    La compatibilidad hermética cubre todos los roles permitidos. Este inventario
    más estricto exige canario conductual solo para destinos ``best_for`` que
    pueden entrar automáticamente en routing; los candidatos manuales esperan a
    una propuesta de promoción.
    """
    calibration_kwargs: dict[str, Any] = {
        "observed_at": observed_at,
        "observed_versions": observed_versions,
    }
    if repo_root is not None:
        calibration_kwargs["repo_root"] = repo_root
    calibration = audit_promoted_model_calibrations(**calibration_kwargs)
    registered = {
        (entry["profile_id"], entry["model"], canonical_role(entry["role"])): entry
        for entry in calibration["entries"]
    }
    observation = (
        observed_at.date() if isinstance(observed_at, datetime) else observed_at
    )
    evaluation_evidence: dict[tuple[str, str, str], dict[str, Any]] = {}
    evidence_root = repo_root or Path(__file__).resolve().parent.parent
    for evidence in MODEL_ROLE_EVALUATION_EVIDENCE:
        key = (
            str(evidence["profile_id"]),
            str(evidence["model"]),
            canonical_role(str(evidence["role"])),
        )
        evaluated_on = date.fromisoformat(str(evidence["evaluated_at"]))
        receipts = [str(path) for path in evidence.get("evidence_receipts") or ()]
        stale_reasons: list[str] = []
        age_days = (observation - evaluated_on).days
        if age_days < 0:
            stale_reasons.append("evaluation_date_in_future")
        elif age_days > CALIBRATION_MAX_AGE_DAYS:
            stale_reasons.append("evaluation_age_exceeded")
        observed_version = str(observed_versions.get(key[0]) or "")
        if observed_version != str(evidence.get("provider_version") or ""):
            stale_reasons.append("provider_version_changed_or_unobserved")
        if not receipts or any(
            not (evidence_root / path).is_file() for path in receipts
        ):
            stale_reasons.append("evidence_receipt_missing")
        evidence_validation_errors = [
            *_critical_role_evidence_errors(
                evidence, evidence_root=evidence_root, receipts=receipts,
            ),
            *_role_diversity_evidence_errors(
                evidence, evidence_root=evidence_root, receipts=receipts,
            ),
            *_tier3_role_evidence_errors(
                evidence, evidence_root=evidence_root, receipts=receipts,
            ),
            *_antigravity_tier2_evidence_errors(
                evidence, evidence_root=evidence_root, receipts=receipts,
            ),
            *_context_curator_evidence_errors(
                evidence, evidence_root=evidence_root, receipts=receipts,
            ),
        ]
        if evidence_validation_errors:
            stale_reasons.append("evidence_receipt_invalid")
        evaluation_evidence[key] = {
            **evidence,
            "evidence_receipts": receipts,
            "evidence_validation_errors": evidence_validation_errors,
            "stale_reasons": stale_reasons,
            "effective_status": (
                "calibrated"
                if evidence.get("status") == "calibrated" and not stale_reasons
                else "partial"
            ),
        }
    diagnostics: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in MODEL_ROLE_EVALUATION_DIAGNOSTICS:
        key = (
            str(entry["profile_id"]),
            str(entry["model"]),
            canonical_role(str(entry["role"])),
        )
        receipts = [str(path) for path in entry.get("receipts") or ()]
        validation_errors = []
        if not receipts:
            validation_errors.append("diagnostic_receipt_missing")
        elif any(not (evidence_root / path).is_file() for path in receipts):
            validation_errors.append("diagnostic_receipt_not_found")
        if key in diagnostics:
            validation_errors.append("duplicate_diagnostic_identity")
        stale_reasons: list[str] = []
        try:
            evaluated_on = date.fromisoformat(str(entry["evaluated_at"]))
            age_days = (observation - evaluated_on).days
            if age_days < 0:
                stale_reasons.append("diagnostic_date_in_future")
            elif age_days > CALIBRATION_MAX_AGE_DAYS:
                stale_reasons.append("diagnostic_age_exceeded")
        except (KeyError, ValueError):
            stale_reasons.append("diagnostic_date_invalid")
        observed_version = str(observed_versions.get(key[0]) or "")
        if observed_version != str(entry.get("provider_version") or ""):
            stale_reasons.append("provider_version_changed_or_unobserved")
        if validation_errors:
            stale_reasons.append("diagnostic_receipt_invalid")
        diagnostics[key] = {
            **entry,
            "receipts": receipts,
            "validation_errors": validation_errors,
            "stale_reasons": stale_reasons,
            "material_change_triggers": list(MATERIAL_CHANGE_TRIGGERS),
        }
    profiles = {
        str(profile.get("id") or ""): profile for profile in DEFAULT_ADAPTER_PROFILES
    }
    rows: list[dict[str, Any]] = []
    pair_counts = {
        "calibrated": 0,
        "partial": 0,
        DEFERRED_UNTIL_MATERIAL_CHANGE: 0,
        "requires_canary": 0,
        "requires_tool_fixture": 0,
        "manual_candidate": 0,
        "blocked": 0,
    }
    for profile_id, options in model_options().items():
        profile = profiles.get(profile_id, {})
        blocked = str(profile.get("status") or "").lower() in {
            "blocked",
            "blocked_by_provider",
            "disabled",
            "retired",
        }
        for option in options:
            model = str(option.get("value") or "")
            executable = (
                model in executable_models_by_profile.get(profile_id, set())
                if executable_models_by_profile is not None
                else None
            )
            roles = sorted(
                {canonical_role(str(role)) for role in option.get("best_for") or []}
            )
            automatic = option.get("automatic", True) is not False and not bool(
                option.get("requires_probe")
            )
            role_rows: list[dict[str, Any]] = []
            if not roles:
                status = (
                    "blocked" if blocked or executable is False else "manual_candidate"
                )
                pair_counts[status] += 1
                role_rows.append(
                    {"role": None, "status": status, "evidence_receipts": []}
                )
            else:
                for role in roles:
                    entry = registered.get((profile_id, model, role))
                    supplemental = evaluation_evidence.get((profile_id, model, role))
                    diagnostic = diagnostics.get((profile_id, model, role))
                    if blocked or executable is False:
                        status = "blocked"
                    elif entry and entry["status"] == "fresh":
                        status = "calibrated"
                    elif entry:
                        status = "partial"
                    elif supplemental:
                        status = str(supplemental["effective_status"])
                    elif (
                        diagnostic
                        and diagnostic.get("rerun_policy") == "material_change_only"
                        and not diagnostic.get("stale_reasons")
                    ):
                        status = DEFERRED_UNTIL_MATERIAL_CHANGE
                    elif automatic and "external_mcp" in default_capabilities_for_role(
                        role
                    ):
                        status = "requires_tool_fixture"
                    elif automatic:
                        status = "requires_canary"
                    else:
                        status = "manual_candidate"
                    pair_counts[status] += 1
                    role_rows.append(
                        {
                            "role": role,
                            "status": status,
                            "evidence_receipts": (
                                list(entry["evidence_receipts"])
                                if entry
                                else list(supplemental["evidence_receipts"])
                                if supplemental
                                else []
                            ),
                            "stale_reasons": (
                                list(entry["stale_reasons"])
                                if entry
                                else list(supplemental["stale_reasons"])
                                if supplemental
                                else []
                            ),
                            "evaluated_at": (
                                entry.get("calibrated_at")
                                if entry
                                else supplemental.get("evaluated_at")
                                if supplemental
                                else diagnostic.get("evaluated_at")
                                if diagnostic
                                else None
                            ),
                            "provider_version": (
                                entry.get("provider_version")
                                if entry
                                else supplemental.get("provider_version")
                                if supplemental
                                else diagnostic.get("provider_version")
                                if diagnostic
                                else None
                            ),
                            "prompt_version": (
                                supplemental.get("prompt_version", "v1")
                                if supplemental
                                else None
                            ),
                            "evaluation_reason": supplemental.get("reason")
                            if supplemental
                            else None,
                            "evidence_validation_errors": (
                                list(supplemental.get("evidence_validation_errors") or ())
                                if supplemental
                                else []
                            ),
                            "diagnostic_reason": diagnostic.get("reason")
                            if diagnostic
                            else None,
                            "diagnostic_receipts": (
                                list(diagnostic.get("receipts") or ())
                                if diagnostic
                                else []
                            ),
                            "diagnostic_validation_errors": (
                                list(diagnostic.get("validation_errors") or ())
                                if diagnostic
                                else []
                            ),
                            "diagnostic_stale_reasons": (
                                list(diagnostic.get("stale_reasons") or ())
                                if diagnostic
                                else []
                            ),
                            "rerun_policy": (
                                diagnostic.get("rerun_policy")
                                if diagnostic
                                else None
                            ),
                            "material_change_triggers": (
                                list(diagnostic.get("material_change_triggers") or ())
                                if diagnostic
                                else []
                            ),
                            **(
                                {
                                    "next_action": (
                                        "no_rerun_until_material_change"
                                        if status
                                        == DEFERRED_UNTIL_MATERIAL_CHANGE
                                        else "run_exact_tool_fixture"
                                        if status == "requires_tool_fixture"
                                        else "run_exact_canary"
                                    )
                                }
                                if status
                                in {
                                    DEFERRED_UNTIL_MATERIAL_CHANGE,
                                    "requires_tool_fixture",
                                    "requires_canary",
                                }
                                else {}
                            ),
                        }
                    )
            statuses = {row["status"] for row in role_rows}
            if statuses == {"calibrated"}:
                model_status = "calibrated"
            elif statuses == {"blocked"}:
                model_status = "blocked"
            elif statuses == {"manual_candidate"}:
                model_status = "manual_candidate"
            elif statuses == {DEFERRED_UNTIL_MATERIAL_CHANGE}:
                model_status = DEFERRED_UNTIL_MATERIAL_CHANGE
            elif statuses == {"requires_tool_fixture"}:
                model_status = "requires_tool_fixture"
            elif "calibrated" in statuses or "partial" in statuses:
                model_status = "partial"
            else:
                model_status = "requires_canary"
            rows.append(
                {
                    "profile_id": profile_id,
                    "model": model,
                    "tier": option.get("tier"),
                    "economy": option.get("economy"),
                    "speed_class": option.get("speed_class"),
                    "speed_source": option.get("speed_source"),
                    "automatic": automatic,
                    "executable": executable,
                    "model_status": model_status,
                    "roles": role_rows,
                }
            )
    return {
        "schema_version": 1,
        "benchmark": "model_role_evaluation_coverage",
        "observed_at": observed_at.isoformat(),
        "policy": {
            "scope": "automatic_best_for_pairs",
            "manual_candidates": "evaluate_before_promotion",
            "negative_diagnostics": (
                "defer_only_while_explicit_policy_receipt_age_and_version_match"
            ),
            "material_change_detection": {
                "automatic": [
                    "provider_or_cli_version_changed",
                    "diagnostic_age_exceeded",
                    "diagnostic_receipt_invalid",
                ],
                "registry_revision_required": [
                    "model_or_catalog_identity_changed",
                    "prompt_or_role_contract_changed",
                    "transport_or_tooling_changed_without_version_change",
                ],
            },
            "removal_rule": "age_alone_never_retires_a_model",
        },
        "models": len(rows),
        "role_pairs": sum(pair_counts.values()),
        "pair_counts": pair_counts,
        # Diagnostics are evidence history, not automatic-selection debt.
        # Keep them independently addressable when a role nomination is
        # removed after a transport or compatibility hard gate changes.
        "diagnostics": [
            {
                "profile_id": key[0],
                "model": key[1],
                "role": key[2],
                **value,
            }
            for key, value in sorted(diagnostics.items())
        ],
        "rows": rows,
        "complete": (
            pair_counts["requires_canary"] == 0
            and pair_counts["requires_tool_fixture"] == 0
            and pair_counts["partial"] == 0
        ),
    }
