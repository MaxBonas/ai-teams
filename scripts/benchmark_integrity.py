"""Auditoría offline de suficiencia para benchmarks de AI Teams.

No puntúa entregables ni llama a modelos. Decide si una colección de resultados
es comparable y suficiente para sostener una conclusión, manteniendo las runs
incompletas como evidencia de liveness fuera del delta A/B.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Iterable


INDEPENDENT_EVALUATOR_CLASSES = frozenset({
    "deterministic_behavioral",
    "static_analysis",
    "structural_contract",
    "state_invariant",
    "causal_judge",
    "sampled_human_review",
})


def code_evaluation_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "evaluators": [
            {"id": "hidden_acceptance", "class": "deterministic_behavioral", "blind": True},
            {"id": "ruff", "class": "static_analysis", "blind": True},
        ],
        "independent_semantic_or_structural": True,
        "goodhart_risk": "residual_hidden_suite_overfit",
        "constructs_not_measured": [
            "behavior outside the hidden suite",
            "long-term maintainability",
            "real-user acceptance",
        ],
    }


def quorum_evaluation_contract(
    *, base_structural: dict[str, Any], final_structural: dict[str, Any]
) -> dict[str, Any]:
    return {
        "version": 1,
        "evaluators": [
            {"id": "hidden_case_rubric", "class": "lexical_coverage", "blind": True},
            {"id": "plan_depth_contract", "class": "structural_contract", "blind": False},
        ],
        "independent_semantic_or_structural": True,
        "goodhart_risk": "material_no_independent_factual_judge",
        "constructs_not_measured": [
            "factual correctness outside the case fixture",
            "production feasibility of the proposed plan",
            "real-user preference",
        ],
        "base_structural": base_structural,
        "final_structural": final_structural,
    }


def audit_evaluation_contract(contract: dict[str, Any]) -> dict[str, Any]:
    evaluators = contract.get("evaluators") if isinstance(contract.get("evaluators"), list) else []
    evaluator_classes = sorted({
        str(item.get("class") or "").strip()
        for item in evaluators
        if isinstance(item, dict) and str(item.get("class") or "").strip()
    })
    independent_classes = sorted(set(evaluator_classes) & INDEPENDENT_EVALUATOR_CLASSES)
    limitations = [
        str(item).strip()
        for item in contract.get("constructs_not_measured", [])
        if str(item).strip()
    ] if isinstance(contract.get("constructs_not_measured"), list) else []
    declared_independent = contract.get("independent_semantic_or_structural") is True
    goodhart_declared = bool(str(contract.get("goodhart_risk") or "").strip())
    issues: list[str] = []
    if not evaluator_classes:
        issues.append("evaluators_missing")
    if not independent_classes:
        issues.append("independent_evaluator_missing")
    if not declared_independent:
        issues.append("independent_evidence_declaration_missing")
    if declared_independent and not independent_classes:
        issues.append("independent_evidence_flag_inconsistent")
    if not limitations:
        issues.append("constructs_not_measured_missing")
    if not goodhart_declared:
        issues.append("goodhart_risk_missing")
    return {
        "evaluator_classes": evaluator_classes,
        "independent_evidence_classes": independent_classes,
        "constructs_not_measured": limitations,
        "goodhart_risk_declared": goodhart_declared,
        "issues": issues,
        "independent_evidence_present": bool(independent_classes),
        "promotion_ready": bool(
            declared_independent and independent_classes and limitations and goodhart_declared
        ),
    }


def audit_ab_series(
    reports: Iterable[dict[str, Any]],
    *,
    required_arms: Iterable[str],
    min_seeds: int = 2,
) -> dict[str, Any]:
    required = tuple(dict.fromkeys(str(item).strip() for item in required_arms if str(item).strip()))
    cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
    cases: set[str] = set()
    unseeded = 0
    for report in reports:
        case = str(report.get("case") or "").strip()
        if case:
            cases.add(case)
        seed_raw = report.get("seed")
        if seed_raw is None or str(seed_raw).strip() == "":
            unseeded += 1
            continue
        seed = str(seed_raw)
        config = report.get("config") if isinstance(report.get("config"), dict) else {}
        arms = report.get("arms") if isinstance(report.get("arms"), dict) else {}
        for arm_key, metrics in arms.items():
            if not isinstance(metrics, dict):
                continue
            conceptual_arm = (
                str(config.get("team_run_profile") or "team")
                if arm_key == "team"
                else "codex_direct" if arm_key == "solo" else str(arm_key)
            )
            cells.setdefault((seed, conceptual_arm), []).append({
                "case": case,
                "score": metrics.get("score") if isinstance(metrics.get("score"), dict) else {},
                "contract": report.get("evaluation_contract")
                if isinstance(report.get("evaluation_contract"), dict) else {},
            })

    seeds = sorted({seed for seed, _arm in cells})
    missing = [
        {"seed": seed, "arm": arm}
        for seed in seeds for arm in required if (seed, arm) not in cells
    ]
    duplicates = [
        {"seed": seed, "arm": arm, "count": len(rows)}
        for (seed, arm), rows in sorted(cells.items()) if len(rows) > 1
    ]
    selected = [
        rows[0] for (seed, arm), rows in cells.items()
        if arm in required and len(rows) == 1
    ]
    hidden_totals = {
        int(row["score"].get("hidden_total") or 0)
        for row in selected if int(row["score"].get("hidden_total") or 0) > 0
    }
    behavioral_complete = bool(selected) and all(
        int(row["score"].get("hidden_total") or 0) > 0
        and row["score"].get("hidden_exit") is not None
        for row in selected
    )
    static_complete = bool(selected) and all(
        row["score"].get("ruff_issues") is not None for row in selected
    )
    contract_complete = bool(selected) and all(
        audit_evaluation_contract(row["contract"])["independent_evidence_present"]
        # Legacy code reports predate the explicit contract, but a non-empty
        # blind hidden suite is already independent behavioral/structural
        # evidence. Do not erase valid historical evidence for missing metadata.
        or int(row["score"].get("hidden_total") or 0) > 0
        for row in selected
    )
    promotion_contract_complete = bool(selected) and all(
        audit_evaluation_contract(row["contract"])["promotion_ready"] for row in selected
    )
    balance_ok = bool(seeds) and not missing and not duplicates
    same_case = len(cases) == 1
    same_hidden_contract = len(hidden_totals) == 1
    sufficient_seeds = len(seeds) >= max(1, int(min_seeds))
    conclusion_allowed = all((
        bool(required), same_case, balance_ok, sufficient_seeds,
        behavioral_complete, static_complete, contract_complete, same_hidden_contract,
    ))
    promotion_allowed = conclusion_allowed and promotion_contract_complete
    issues: list[str] = []
    if not required:
        issues.append("required_arms_empty")
    if not same_case:
        issues.append("mixed_or_missing_cases")
    if unseeded:
        issues.append("unseeded_reports_excluded")
    if missing:
        issues.append("arm_seed_matrix_incomplete")
    if duplicates:
        issues.append("duplicate_arm_seed_cells")
    if not sufficient_seeds:
        issues.append("insufficient_matched_seeds")
    if not behavioral_complete:
        issues.append("deterministic_behavioral_evidence_missing")
    if not static_complete:
        issues.append("static_analysis_missing")
    if not contract_complete:
        issues.append("independent_evaluation_contract_missing")
    if not promotion_contract_complete:
        issues.append("promotion_evaluation_contract_incomplete")
    if not same_hidden_contract:
        issues.append("hidden_suite_totals_differ")
    return {
        "audit": "ab_series_integrity",
        "cases": sorted(cases),
        "required_arms": list(required),
        "seeds": seeds,
        "matched_seed_count": len(seeds) if balance_ok else 0,
        "min_seeds": max(1, int(min_seeds)),
        "missing_cells": missing,
        "duplicate_cells": duplicates,
        "excluded_unseeded_reports": unseeded,
        "evidence_classes": [
            name for name, present in (
                ("deterministic_behavioral", behavioral_complete),
                ("static_analysis", static_complete),
                ("independent_semantic_or_structural", contract_complete),
            ) if present
        ],
        "promotion_contract_complete": promotion_contract_complete,
        "goodhart_risk": "residual" if promotion_allowed else "material" if conclusion_allowed else "high",
        "issues": issues,
        "conclusion_allowed": conclusion_allowed,
        "promotion_allowed": promotion_allowed,
    }


def audit_quorum_series(
    reports: Iterable[dict[str, Any]],
    *,
    min_sessions: int = 3,
    min_providers: int = 2,
) -> dict[str, Any]:
    completed: list[dict[str, Any]] = []
    incomplete = 0
    for report in reports:
        provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
        session = provenance.get("session") if isinstance(provenance.get("session"), dict) else {}
        if (
            report.get("completed") is False
            or not isinstance(report.get("final"), dict)
            or str(session.get("status") or "") != "accepted"
        ):
            incomplete += 1
            continue
        completed.append(report)
    deltas = [float(item.get("delta_score_pct") or 0.0) for item in completed]
    providers: set[str] = set()
    provenance_complete = True
    for report in completed:
        provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
        contributions = provenance.get("contributions") if isinstance(provenance.get("contributions"), list) else []
        valid = [item for item in contributions if isinstance(item, dict) and bool(item.get("valid"))]
        if not valid:
            provenance_complete = False
        for item in valid:
            provider = str(item.get("provider") or "").strip()
            if provider:
                providers.add(provider)
            if not all(str(item.get(key) or "").strip() for key in ("run_id", "provider", "model", "channel")):
                provenance_complete = False
    structural_complete = bool(completed) and all(
        isinstance(item.get("evaluation_contract"), dict)
        and audit_evaluation_contract(item["evaluation_contract"])["independent_evidence_present"]
        and isinstance(item["evaluation_contract"].get("final_structural"), dict)
        and item["evaluation_contract"]["final_structural"].get("valid") is True
        for item in completed
    )
    promotion_contract_complete = bool(completed) and all(
        isinstance(item.get("evaluation_contract"), dict)
        and audit_evaluation_contract(item["evaluation_contract"])["promotion_ready"]
        for item in completed
    )
    hard_gates_consistent = bool(completed) and all(
        bool(item.get("final", {}).get("passes_hard_gate")) for item in completed
    )
    sign_stable = not deltas or not (min(deltas) < 0 < max(deltas))
    enough_sessions = len(completed) >= max(1, int(min_sessions))
    enough_providers = len(providers) >= max(1, int(min_providers))
    conclusion_allowed = all((
        enough_sessions, enough_providers, provenance_complete,
        structural_complete, hard_gates_consistent, sign_stable,
    ))
    promotion_allowed = conclusion_allowed and promotion_contract_complete
    issues: list[str] = []
    if incomplete:
        issues.append("incomplete_sessions_excluded_from_delta")
    if not enough_sessions:
        issues.append("insufficient_accepted_sessions")
    if not enough_providers:
        issues.append("insufficient_provider_diversity")
    if not provenance_complete:
        issues.append("provenance_incomplete")
    if not structural_complete:
        issues.append("independent_structural_evidence_missing")
    if not promotion_contract_complete:
        issues.append("promotion_evaluation_contract_incomplete")
    if not hard_gates_consistent:
        issues.append("final_hard_gate_not_consistent")
    if not sign_stable:
        issues.append("delta_sign_unstable")
    return {
        "audit": "quorum_series_integrity",
        "accepted_sessions": len(completed),
        "excluded_incomplete_sessions": incomplete,
        "min_sessions": max(1, int(min_sessions)),
        "providers": sorted(providers),
        "min_providers": max(1, int(min_providers)),
        "delta_median": round(statistics.median(deltas), 2) if deltas else None,
        "delta_range": [round(min(deltas), 2), round(max(deltas), 2)] if deltas else None,
        "hard_gates_consistent": hard_gates_consistent,
        "sign_stable": sign_stable,
        "promotion_contract_complete": promotion_contract_complete,
        "goodhart_risk": "material" if promotion_allowed else "high",
        "issues": issues,
        "conclusion_allowed": conclusion_allowed,
        "promotion_allowed": promotion_allowed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--kind", choices=("ab", "quorum"), required=True)
    parser.add_argument("--required-arms", default="solo_lead,full_team")
    parser.add_argument("--min-seeds", type=int, default=2)
    parser.add_argument("--min-sessions", type=int, default=3)
    parser.add_argument("--min-providers", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.inputs]
    if args.kind == "ab":
        audit = audit_ab_series(
            reports,
            required_arms=[item.strip() for item in args.required_arms.split(",") if item.strip()],
            min_seeds=args.min_seeds,
        )
    else:
        audit = audit_quorum_series(
            reports, min_sessions=args.min_sessions, min_providers=args.min_providers
        )
    serialized = json.dumps(audit, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if audit["conclusion_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
