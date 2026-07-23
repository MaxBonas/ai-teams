"""Métricas normalizadas derivadas solo de calibraciones exactas validadas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from aiteam.model_evidence_taxonomy import (
    contract_case_families,
    diversity_status,
    exact_evidence_kind,
)
from aiteam.policies import canonical_role


NORMALIZED_METRICS_VERSION = "model_normalized_metrics_v1"

_CONTRACTS_BY_REASON: dict[str, dict[str, Any]] = {
    "critical_role_hidden_causal_contract_6_of_6": {
        "contract": "critical_role_hidden_causal_v2",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "governed_mcp_allow_deny_health_recovery_3_of_3": {
        "contract": "governed_mcp_allow_deny_recovery_v1",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 3,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "independent_suite_kills_hidden_mutants_3_of_3": {
        "contract": "independent_test_designer_mutation_v2",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "independent_test_designer_two_family_6_of_6": {
        "contract": "independent_test_designer_two_family_v3",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "adversarial_test_then_fix_3_of_3": {
        "contract": "adversarial_qa_fix_cycle_v2",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "adversarial_qa_two_family_6_of_6": {
        "contract": "adversarial_qa_two_family_v3",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "coding_hidden_suite_and_ruff_3_of_3": {
        "contract": "coding_hidden_suite_v1",
        "classes": ["behavioral_deterministic", "static_analysis"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "coding_two_family_hidden_suite_6_of_6": {
        "contract": "coding_hidden_suite_two_family_v4",
        "classes": ["behavioral_deterministic", "static_analysis"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "durable_review_behavioral_3_of_3": {
        "contract": "durable_review_reject_approve_v1",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 2,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "tier3_causal_report_contract_3_of_3": {
        "contract": "tier3_causal_report_v2",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "tier3_worker_two_family_causal_6_of_6": {
        "contract": "tier3_two_family_causal_report_v3",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "tier3_web_scout_two_family_causal_6_of_6": {
        "contract": "tier3_two_family_causal_report_v3",
        "classes": ["causal_judge", "tool_governance"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "mcp_operator_two_family_governance_6_of_6": {
        "contract": "tier3_two_family_causal_report_v3",
        "classes": ["behavioral_deterministic", "tool_governance"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    "antigravity_adversarial_qa_3_of_3": {
        "contract": "adversarial_qa_fix_cycle_v2",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "antigravity_mutation_test_designer_3_of_3": {
        "contract": "independent_test_designer_mutation_v2",
        "classes": ["behavioral_deterministic"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "antigravity_tier3_causal_report_3_of_3": {
        "contract": "tier3_causal_report_v2",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
    "antigravity_context_curator_causal_6_of_6": {
        "contract": "context_curator_two_slice_v1",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
}

_CONTRACTS_BY_PAIR: dict[tuple[str, str, str], dict[str, Any]] = {
    (
        "codex_subscription",
        "gpt-5.6-luna",
        "context_curator",
    ): {
        "contract": "context_curator_two_slice_v3",
        "classes": ["causal_judge"],
        "seeds": 3,
        "cases": 2,
        "passed": 6,
        "total": 6,
        "goodhart_risk": "moderate",
    },
    (
        "antigravity_subscription",
        "claude-sonnet-4-6",
        "engineer",
    ): {
        "contract": "coding_hidden_suite_v3",
        "classes": ["behavioral_deterministic", "static_analysis"],
        "seeds": 3,
        "cases": 1,
        "passed": 3,
        "total": 3,
        "goodhart_risk": "moderate",
    },
}


def normalized_metrics_from_evaluation(
    evaluation_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Normaliza calidad/evidencia; cualquier hueco queda diagnosticado."""
    metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    for model_row in evaluation_report.get("rows") or ():
        profile_id = str(model_row.get("profile_id") or "")
        model = str(model_row.get("model") or "")
        for role_row in model_row.get("roles") or ():
            role = canonical_role(str(role_row.get("role") or ""))
            if not profile_id or not model or not role:
                continue
            key = (profile_id, model, role)
            if role_row.get("status") != "calibrated":
                continue
            validation_errors = list(
                role_row.get("evidence_validation_errors") or ()
            )
            stale_reasons = list(role_row.get("stale_reasons") or ())
            if validation_errors or stale_reasons:
                diagnostics.append(
                    {
                        "profile_id": profile_id,
                        "model": model,
                        "role": role,
                        "reason": "calibrated_row_not_fresh_or_valid",
                        "validation_errors": validation_errors,
                        "stale_reasons": stale_reasons,
                    }
                )
                continue
            reason = str(role_row.get("evaluation_reason") or "")
            contract = _CONTRACTS_BY_PAIR.get(key) or _CONTRACTS_BY_REASON.get(
                reason
            )
            if contract is None:
                diagnostics.append(
                    {
                        "profile_id": profile_id,
                        "model": model,
                        "role": role,
                        "reason": "normalization_contract_missing",
                    }
                )
                continue
            receipts = [
                str(item) for item in role_row.get("evidence_receipts") or ()
            ]
            if not receipts:
                diagnostics.append(
                    {
                        "profile_id": profile_id,
                        "model": model,
                        "role": role,
                        "reason": "normalization_receipts_missing",
                    }
                )
                continue
            total = int(contract["total"])
            passed = int(contract["passed"])
            quality = round(100.0 * passed / total, 4)
            contract_id = str(contract["contract"])
            case_families = contract_case_families(contract_id, role)
            if not case_families:
                diagnostics.append(
                    {
                        "profile_id": profile_id,
                        "model": model,
                        "role": role,
                        "reason": "case_family_contract_missing",
                        "contract": contract_id,
                    }
                )
                continue
            diversity = diversity_status(case_families)
            metrics[key] = {
                "components": {
                    "quality": {
                        "value": quality,
                        "reason": f"exact_contract_pass_rate:{passed}/{total}",
                        "source": f"evaluation_contract:{contract_id}",
                        "comparison_group": f"role_contract:{role}:{contract_id}",
                        "samples_passed": passed,
                        "samples_total": total,
                    }
                },
                "evidence": {
                    "status": "calibrated",
                    "kind": exact_evidence_kind(role),
                    "classes": list(contract["classes"]),
                    "seeds": int(contract["seeds"]),
                    "cases": int(contract["cases"]),
                    "case_families": list(case_families),
                    "case_family_count": len(case_families),
                    "case_diversity": diversity,
                    "fresh": True,
                    "provider_version": role_row.get("provider_version"),
                    "evaluated_at": role_row.get("evaluated_at"),
                    "receipts": receipts,
                    "goodhart_risk": (
                        str(contract["goodhart_risk"])
                        if diversity == "multi_family"
                        else "material"
                    ),
                    "unmeasured_constructs": [
                        "generalización fuera del contrato exacto",
                    ],
                },
                "normalization": {
                    "version": NORMALIZED_METRICS_VERSION,
                    "contract": contract_id,
                    "scope": "exact_profile_model_role",
                    "evidence_kind": exact_evidence_kind(role),
                },
            }
    diversity_counts = {"multi_family": 0, "single_family": 0}
    for metric in metrics.values():
        status = str(metric["evidence"]["case_diversity"])
        diversity_counts[status] += 1
    return {
        "schema_version": NORMALIZED_METRICS_VERSION,
        "metrics": metrics,
        "pair_count": len(metrics),
        "case_diversity_counts": diversity_counts,
        "diagnostics": diagnostics,
    }
