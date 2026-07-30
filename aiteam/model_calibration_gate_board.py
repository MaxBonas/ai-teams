"""Tablero determinista del recorrido adapter → calibración → promoción."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "model_calibration_gate_board_v1"
STAGE_SEQUENCE = (
    "configuration_auth",
    "catalog_version",
    "adapter_health",
    "contract_probe",
    "role_canary",
    "multi_family_calibration",
    "promotion",
)
_TECHNICAL_OWNER = "AI Teams maintainer"


def build_model_calibration_gate_board(
    read_model: Mapping[str, Any],
) -> dict[str, Any]:
    rows = [
        build_model_calibration_gate_row(candidate, role_row)
        for candidate in read_model.get("candidates") or ()
        if isinstance(candidate, Mapping)
        for role_row in candidate.get("roles") or ()
        if isinstance(role_row, Mapping)
    ]
    stages = Counter(
        str((row.get("blocker") or {}).get("stage") or "complete")
        for row in rows
    )
    actions = Counter(str(row.get("next_action") or "none") for row in rows)
    board = {
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": read_model.get("schema_version"),
        "source_content_hash": read_model.get("content_hash"),
        "stage_sequence": list(STAGE_SEQUENCE),
        "counts": {
            "rows": len(rows),
            "actionable": sum(row["actionable"] for row in rows),
            "complete": sum(row["blocker"] is None for row in rows),
            "by_blocker_stage": dict(sorted(stages.items())),
            "by_next_action": dict(sorted(actions.items())),
        },
        "rows": rows,
    }
    validate_model_calibration_gate_board(board)
    return board


def build_model_calibration_gate_row(
    candidate: Mapping[str, Any],
    role_row: Mapping[str, Any],
) -> dict[str, Any]:
    identity = candidate.get("identity") or {}
    states = candidate.get("states") or {}
    metadata = candidate.get("model_metadata") or {}
    preference = candidate.get("owner_preference") or {}
    evaluation = role_row.get("evaluation") or {}
    compatibility = role_row.get("compatibility") or {}
    score = role_row.get("score") or {}
    hard_gates = (role_row.get("score_inputs") or {}).get("hard_gates") or {}
    role = str(role_row.get("canonical_role") or "")
    nominated = bool(
        (role_row.get("automatic_selection") or {}).get("eligible_by_policy")
        or hard_gates.get("automatic_policy") is True
    )
    policy = _maintenance_policy(preference, nominated)

    calibration_receipts = sorted(
        {
            str(item)
            for item in (
                *(evaluation.get("evidence_receipts") or ()),
                *(evaluation.get("diagnostic_receipts") or ()),
                *((role_row.get("provenance") or {}).get("evaluation_receipts") or ()),
                *((role_row.get("provenance") or {}).get("diagnostic_receipts") or ()),
            )
            if str(item)
        }
    )
    probe_receipts = sorted(
        str(item) for item in metadata.get("probe_receipts") or () if str(item)
    )
    domain_gate_names = ("compatible", "privacy", "tools", "workspace", "structured_output")
    domain_values = {name: hard_gates.get(name) for name in domain_gate_names}
    technical = [
        _boolean_stage(
            "configuration_auth",
            _state_value(states, "configured"),
            passed_reason="configured_reference_present",
            blocked_reason="configuration_or_auth_not_ready",
            owner="machine_owner",
        ),
        _catalog_stage(states),
        _boolean_stage(
            "adapter_health",
            _state_value(states, "adapter_green"),
            passed_reason="adapter_health_green",
            blocked_reason="adapter_health_not_green",
            owner="machine_owner",
        ),
        _contract_stage(
            compatibility=compatibility,
            domain_values=domain_values,
            probe_receipts=probe_receipts,
            calibration_receipts=calibration_receipts,
        ),
        _canary_stage(evaluation, calibration_receipts),
        _diversity_stage(evaluation, hard_gates, calibration_receipts),
        _promotion_stage(score),
    ]
    gates = _apply_sequence(technical, policy_allows=policy["allows_proactive"])
    blocker = _policy_blocker(policy)
    if blocker is None:
        blocker = next(
            (
                {
                    "stage": gate["stage"],
                    "code": gate["reason_code"],
                    "owner": gate["owner"],
                }
                for gate in gates
                if gate["status"] in {"blocked", "pending", "historical"}
            ),
            None,
        )
    next_action = _next_action(
        blocker=blocker,
        evaluation=evaluation,
        compatibility=compatibility,
    )
    actionable = next_action not in {
        "none",
        "wait_for_material_change",
        "owner_keep_archived",
        "owner_keep_low_priority",
        "owner_decide_manual_or_nominate",
        "owner_classify_before_maintenance",
    }
    return {
        "candidate_id": candidate.get("candidate_id"),
        "profile_id": identity.get("profile_id"),
        "model_id": identity.get("model_id"),
        "canonical_role": role,
        "owner_preference": {
            "state": str(preference.get("state") or "normal"),
            "reason": str(preference.get("reason") or ""),
            "source": str(preference.get("source") or "unknown"),
        },
        "maintenance_policy": policy,
        "gates": gates,
        "blocker": blocker,
        "owner": blocker["owner"] if blocker else _TECHNICAL_OWNER,
        "next_action": next_action,
        "actionable": actionable,
        "promotion_ready": blocker is None,
    }


def attach_calibration_gates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw_candidate in candidates:
        candidate = deepcopy(dict(raw_candidate))
        candidate["roles"] = [
            {
                **dict(role_row),
                "calibration_gate": build_model_calibration_gate_row(
                    candidate,
                    role_row,
                ),
            }
            for role_row in candidate.get("roles") or ()
        ]
        selected = candidate.get("role_evaluation")
        if isinstance(selected, Mapping):
            candidate["role_evaluation"] = {
                **dict(selected),
                "calibration_gate": build_model_calibration_gate_row(
                    candidate,
                    selected,
                ),
            }
        output.append(candidate)
    return output


def validate_model_calibration_gate_board(board: Mapping[str, Any]) -> None:
    if board.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("model calibration gate board schema drift")
    if board.get("stage_sequence") != list(STAGE_SEQUENCE):
        raise ValueError("model calibration gate stage sequence drift")
    rows = board.get("rows")
    if not isinstance(rows, list):
        raise TypeError("model calibration gate rows must be a list")
    counts = board.get("counts")
    if not isinstance(counts, Mapping):
        raise TypeError("model calibration gate counts must be a mapping")
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        identity = (
            str(row.get("profile_id") or ""),
            str(row.get("model_id") or ""),
            str(row.get("canonical_role") or ""),
        )
        if not all(identity) or identity in identities:
            raise ValueError("model calibration gate identity drift")
        identities.add(identity)
        gates = row.get("gates")
        if not isinstance(gates, list) or [
            gate.get("stage") for gate in gates
        ] != list(STAGE_SEQUENCE):
            raise ValueError("model calibration gate row sequence drift")
        first_open = next(
            (
                gate
                for gate in gates
                if gate.get("status") in {"blocked", "pending", "historical"}
            ),
            None,
        )
        policy = row.get("maintenance_policy") or {}
        blocker = row.get("blocker")
        if policy.get("allows_proactive") is True:
            expected = first_open.get("stage") if first_open else None
            observed = blocker.get("stage") if isinstance(blocker, Mapping) else None
            if observed != expected:
                raise ValueError("model calibration first blocker drift")
        all_passed = all(gate.get("status") == "passed" for gate in gates)
        if row.get("promotion_ready") is not (
            blocker is None and all_passed
        ):
            raise ValueError("model calibration promotion summary drift")
        if (
            next(
                (
                    gate.get("status")
                    for gate in gates
                    if gate.get("stage") == "adapter_health"
                ),
                None,
            )
            != "passed"
            and row.get("next_action") in {
                "run_exact_contract_probe",
                "run_exact_role_canary",
                "run_second_independent_family",
                "resolve_promotion_gate",
            }
        ):
            raise ValueError("red adapter bypassed remediation")
    expected_stages = Counter(
        str((row.get("blocker") or {}).get("stage") or "complete")
        for row in rows
    )
    expected_actions = Counter(
        str(row.get("next_action") or "none") for row in rows
    )
    if dict(counts) != {
        "rows": len(rows),
        "actionable": sum(row.get("actionable") is True for row in rows),
        "complete": sum(row.get("blocker") is None for row in rows),
        "by_blocker_stage": dict(sorted(expected_stages.items())),
        "by_next_action": dict(sorted(expected_actions.items())),
    }:
        raise ValueError("model calibration gate counts drift")


def _maintenance_policy(
    preference: Mapping[str, Any],
    nominated: bool,
) -> dict[str, Any]:
    state = str(preference.get("state") or "normal")
    if str(preference.get("source") or "") == "default":
        return {
            "allows_proactive": False,
            "code": "owner_unclassified",
            "owner": "project_owner",
        }
    if state == "archived":
        return {
            "allows_proactive": False,
            "code": "owner_archived",
            "owner": "project_owner",
        }
    if state == "low":
        return {
            "allows_proactive": False,
            "code": "owner_low_priority",
            "owner": "project_owner",
        }
    if not nominated:
        return {
            "allows_proactive": False,
            "code": "manual_or_not_nominated",
            "owner": "project_owner",
        }
    return {
        "allows_proactive": True,
        "code": "proactive_allowed",
        "owner": _TECHNICAL_OWNER,
    }


def _policy_blocker(policy: Mapping[str, Any]) -> dict[str, str] | None:
    if policy.get("allows_proactive") is True:
        return None
    return {
        "stage": "maintenance_policy",
        "code": str(policy.get("code") or "owner_policy_blocked"),
        "owner": str(policy.get("owner") or "project_owner"),
    }


def _boolean_stage(
    stage: str,
    value: Any,
    *,
    passed_reason: str,
    blocked_reason: str,
    owner: str,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "passed" if value is True else "blocked" if value is False else "pending",
        "reason_code": (
            passed_reason
            if value is True
            else blocked_reason
            if value is False
            else f"{stage}_not_observed"
        ),
        "owner": owner,
        "evidence": {"observed": value},
    }


def _catalog_stage(states: Mapping[str, Any]) -> dict[str, Any]:
    catalogued = _state_value(states, "catalogued")
    verified = _state_value(states, "model_verified")
    passed = catalogued is True and verified is True
    blocked = catalogued is False or verified is False
    return {
        "stage": "catalog_version",
        "status": "passed" if passed else "blocked" if blocked else "pending",
        "reason_code": (
            "exact_catalog_and_version_verified"
            if passed
            else "catalog_or_exact_version_not_verified"
            if blocked
            else "catalog_or_version_not_observed"
        ),
        "owner": _TECHNICAL_OWNER,
        "evidence": {
            "catalogued": catalogued,
            "model_verified": verified,
        },
    }


def _contract_stage(
    *,
    compatibility: Mapping[str, Any],
    domain_values: Mapping[str, Any],
    probe_receipts: list[str],
    calibration_receipts: list[str],
) -> dict[str, Any]:
    compatible = compatibility.get("allowed")
    failed_domains = sorted(
        key for key, value in domain_values.items() if value is False
    )
    evidence = probe_receipts or calibration_receipts
    if compatible is False or failed_domains:
        status = "blocked"
        reason = str(compatibility.get("code") or "") or (
            "contract_domain_failed:" + ",".join(failed_domains)
        )
    elif compatible is not True or any(
        value is not True for value in domain_values.values()
    ):
        status = "pending"
        reason = "contract_domain_not_observed"
    elif evidence:
        status = "passed"
        reason = (
            "exact_contract_probe_verified"
            if probe_receipts
            else "exact_behavioral_evidence_verifies_contract"
        )
    else:
        status = "pending"
        reason = "exact_structured_output_or_tool_probe_required"
    return {
        "stage": "contract_probe",
        "status": status,
        "reason_code": reason,
        "owner": _TECHNICAL_OWNER,
        "evidence": {
            "probe_receipt_count": len(probe_receipts),
            "behavioral_receipt_count": len(calibration_receipts),
            "failed_domains": failed_domains,
        },
    }


def _canary_stage(
    evaluation: Mapping[str, Any],
    receipts: list[str],
) -> dict[str, Any]:
    status = str(evaluation.get("status") or "untested")
    stale = bool(evaluation.get("stale_reasons"))
    if status == "calibrated" and receipts and not stale:
        gate_status = "passed"
        reason = "exact_role_canary_verified"
    elif receipts:
        gate_status = "historical"
        reason = (
            "role_canary_deferred_until_material_change"
            if status == "deferred_until_material_change"
            or evaluation.get("next_action") == "no_rerun_until_material_change"
            else "role_canary_historical_or_partial"
        )
    elif status == "incompatible":
        gate_status = "blocked"
        reason = str(evaluation.get("reason_code") or "role_incompatible")
    else:
        gate_status = "pending"
        reason = "exact_role_canary_required"
    return {
        "stage": "role_canary",
        "status": gate_status,
        "reason_code": reason,
        "owner": _TECHNICAL_OWNER,
        "evidence": {
            "evaluation_status": status,
            "receipt_count": len(receipts),
            "stale": stale,
        },
    }


def _diversity_stage(
    evaluation: Mapping[str, Any],
    hard_gates: Mapping[str, Any],
    receipts: list[str],
) -> dict[str, Any]:
    calibrated = str(evaluation.get("status") or "") == "calibrated"
    fresh = not evaluation.get("stale_reasons")
    diversity = hard_gates.get("case_diversity")
    if calibrated and fresh and diversity is True:
        status = "passed"
        reason = "multi_family_evidence_verified"
    elif receipts:
        status = "historical"
        reason = (
            "second_independent_family_required"
            if diversity is not True
            else "multi_family_evidence_not_current"
        )
    else:
        status = "pending"
        reason = "multi_family_waits_for_role_canary"
    return {
        "stage": "multi_family_calibration",
        "status": status,
        "reason_code": reason,
        "owner": _TECHNICAL_OWNER,
        "evidence": {
            "case_diversity": diversity,
            "receipt_count": len(receipts),
        },
    }


def _promotion_stage(score: Mapping[str, Any]) -> dict[str, Any]:
    eligible = score.get("auto_eligible") is True
    reasons = [str(item) for item in score.get("auto_ineligible_reasons") or ()]
    return {
        "stage": "promotion",
        "status": "passed" if eligible else "blocked",
        "reason_code": (
            "automatic_promotion_eligible"
            if eligible
            else reasons[0]
            if reasons
            else "automatic_promotion_not_eligible"
        ),
        "owner": _TECHNICAL_OWNER,
        "evidence": {
            "auto_eligible": eligible,
            "auto_ineligible_reasons": reasons,
        },
    }


def _apply_sequence(
    gates: list[dict[str, Any]],
    *,
    policy_allows: bool,
) -> list[dict[str, Any]]:
    open_seen = not policy_allows
    output: list[dict[str, Any]] = []
    for raw in gates:
        gate = deepcopy(raw)
        if open_seen:
            if gate["status"] == "passed" and gate["stage"] in {
                "role_canary",
                "multi_family_calibration",
            }:
                gate["status"] = "historical"
                gate["reason_code"] = "historical_evidence_waits_for_prior_gate"
            elif gate["status"] != "historical":
                gate["status"] = "waiting"
                gate["reason_code"] = "waiting_for_prior_gate"
        elif gate["status"] != "passed":
            open_seen = True
        output.append(gate)
    return output


def _next_action(
    *,
    blocker: Mapping[str, Any] | None,
    evaluation: Mapping[str, Any],
    compatibility: Mapping[str, Any],
) -> str:
    if blocker is None:
        return "none"
    stage = str(blocker.get("stage") or "")
    code = str(blocker.get("code") or "")
    if stage == "maintenance_policy":
        return {
            "owner_archived": "owner_keep_archived",
            "owner_low_priority": "owner_keep_low_priority",
            "manual_or_not_nominated": "owner_decide_manual_or_nominate",
            "owner_unclassified": "owner_classify_before_maintenance",
        }.get(code, "owner_review_policy")
    if stage == "configuration_auth":
        return "configure_adapter_and_secret_reference"
    if stage == "catalog_version":
        return "refresh_exact_catalog_and_version"
    if stage == "adapter_health":
        return "remediate_adapter_health"
    if stage == "contract_probe":
        if compatibility.get("allowed") is False:
            return "resolve_role_compatibility"
        return "run_exact_contract_probe"
    if stage == "role_canary":
        if (
            evaluation.get("next_action") == "no_rerun_until_material_change"
            or evaluation.get("rerun_policy") == "material_change_only"
        ):
            return "wait_for_material_change"
        return "run_exact_role_canary"
    if stage == "multi_family_calibration":
        return "run_second_independent_family"
    if stage == "promotion":
        return "resolve_promotion_gate"
    return "owner_review_required"


def _state_value(states: Mapping[str, Any], name: str) -> Any:
    value = states.get(name)
    return value.get("value") if isinstance(value, Mapping) else None
