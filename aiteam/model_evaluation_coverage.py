"""Cobertura conductual conservadora del catálogo por perfil+modelo+rol."""

from __future__ import annotations

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
        "model": "gpt-5.6-terra",
        "role": "mcp_operator",
        "status": "calibrated",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "governed_mcp_allow_deny_health_recovery_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-terra-mcp-operator-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "test_designer",
        "status": "calibrated",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "independent_suite_kills_hidden_mutants_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-terra-test-designer-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "qa",
        "status": "calibrated",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "adversarial_test_then_fix_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-terra-qa-aggregate.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "role": "engineer",
        "status": "calibrated",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "coding_hidden_suite_and_ruff_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-terra-engineer-aggregate.json",
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
        "role": "web_scout",
        "status": "partial",
        "evaluated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "governed_mcp_behavioral_2_of_3_single_attempt",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/codex-luna-web-scout-low-seed-1-v2.json",
            "benchmarks/results/model_calibration/codex-luna-web-scout-low-seed-2.json",
            "benchmarks/results/model_calibration/codex-luna-web-scout-low-seed-3.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gemini-3.1-pro-high",
        "role": "lead",
        "status": "partial",
        "evaluated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "reason": "screening_structural_3_of_3_goodhart_material",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-1.1.5-role-calibration-aggregate.json",
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
        "model": "gemini-3.5-flash-low",
        "role": "context_curator",
        "status": "partial",
        "evaluated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "reason": "scout_surrogate_structural_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-1.1.5-role-calibration-aggregate.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "gpt-oss-120b-medium",
        "role": "file_scout",
        "status": "partial",
        "evaluated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "reason": "scout_surrogate_structural_3_of_3",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-1.1.5-role-calibration-aggregate.json",
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
)


# Negative screenings remain visible without being promoted to ``partial``:
# failing one frozen case is useful routing evidence, but it does not satisfy
# the three-seed contract and must not make the remaining canary debt disappear.
MODEL_ROLE_EVALUATION_DIAGNOSTICS: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "file_scout",
        "observed_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "low_and_medium_screening_failed_semantic_contract",
        "receipts": (
            "benchmarks/results/model_calibration/codex-luna-file-scout-low-seed-1-gated.json",
            "benchmarks/results/model_calibration/codex-luna-file-scout-medium-seed-1.json",
        ),
    },
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "worker",
        "observed_at": "2026-07-22",
        "provider_version": "0.145.0",
        "reason": "low_invalid_result_and_medium_missing_report",
        "receipts": (
            "benchmarks/results/model_calibration/codex-luna-worker-low-seed-1.json",
            "benchmarks/results/model_calibration/codex-luna-worker-medium-seed-1.json",
        ),
    },
)


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
        if (observation - evaluated_on).days > CALIBRATION_MAX_AGE_DAYS:
            stale_reasons.append("evaluation_age_exceeded")
        observed_version = str(observed_versions.get(key[0]) or "")
        if observed_version != str(evidence.get("provider_version") or ""):
            stale_reasons.append("provider_version_changed_or_unobserved")
        if not receipts or any(
            not (evidence_root / path).is_file() for path in receipts
        ):
            stale_reasons.append("evidence_receipt_missing")
        evaluation_evidence[key] = {
            **evidence,
            "evidence_receipts": receipts,
            "stale_reasons": stale_reasons,
            "effective_status": (
                "calibrated"
                if evidence.get("status") == "calibrated" and not stale_reasons
                else "partial"
            ),
        }
    diagnostics = {
        (
            str(entry["profile_id"]),
            str(entry["model"]),
            canonical_role(str(entry["role"])),
        ): entry
        for entry in MODEL_ROLE_EVALUATION_DIAGNOSTICS
    }
    profiles = {
        str(profile.get("id") or ""): profile for profile in DEFAULT_ADAPTER_PROFILES
    }
    rows: list[dict[str, Any]] = []
    pair_counts = {
        "calibrated": 0,
        "partial": 0,
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
                                else None
                            ),
                            "provider_version": (
                                entry.get("provider_version")
                                if entry
                                else supplemental.get("provider_version")
                                if supplemental
                                else None
                            ),
                            "evaluation_reason": supplemental.get("reason")
                            if supplemental
                            else None,
                            "diagnostic_reason": diagnostic.get("reason")
                            if diagnostic
                            else None,
                            "diagnostic_receipts": (
                                list(diagnostic.get("receipts") or ())
                                if diagnostic
                                else []
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
            "removal_rule": "age_alone_never_retires_a_model",
        },
        "models": len(rows),
        "role_pairs": sum(pair_counts.values()),
        "pair_counts": pair_counts,
        "rows": rows,
        "complete": (
            pair_counts["requires_canary"] == 0
            and pair_counts["requires_tool_fixture"] == 0
            and pair_counts["partial"] == 0
        ),
    }
