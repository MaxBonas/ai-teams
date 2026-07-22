"""Valida que el preregistro de orientación siga completo y sin resultados."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREREG = REPO_ROOT / "benchmarks" / "frontend_orientation" / "orientation-study-prereg-v1.json"
DEFAULT_TEMPLATE = REPO_ROOT / "benchmarks" / "frontend_orientation" / "orientation-study-result-template-v1.json"

EXPECTED_FLOWS = {"inbox", "profile_selection", "accepted_plan_to_task"}
EXPECTED_EVENT_FIELDS = ["flow", "event", "profile"]
EXPECTED_PROFILES = {"solo_lead", "lead_quorum", "full_team"}
FORBIDDEN_OBSERVER_FIELDS = {"name", "email", "prompt", "quote", "transcript", "issue_id", "workspace", "path"}


def validate_preregistration(prereg: dict[str, Any], template: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if prereg.get("status") != "preregistered_no_sessions_observed":
        errors.append("status_must_confirm_no_observed_sessions")
    if any(key in prereg for key in ("results", "observations", "gate_results", "decision")):
        errors.append("preregistration_must_not_contain_results")

    sample = prereg.get("sample") if isinstance(prereg.get("sample"), dict) else {}
    required = int(sample.get("required_completed_sessions") or 0)
    maximum = int(sample.get("maximum_recruited_sessions") or 0)
    strata = sample.get("strata") if isinstance(sample.get("strata"), list) else []
    strata_total = sum(int(item.get("required_completed_sessions") or 0) for item in strata if isinstance(item, dict))
    if required != 8 or maximum < required or strata_total != required:
        errors.append("sample_or_strata_inconsistent")
    if sample.get("performance_based_exclusion_allowed") is not False:
        errors.append("performance_based_exclusion_must_be_forbidden")

    orders = prereg.get("participant_orders") if isinstance(prereg.get("participant_orders"), dict) else {}
    if len(orders) != required:
        errors.append("participant_order_count_mismatch")
    for code, order in orders.items():
        if not isinstance(code, str) or not isinstance(order, list) or set(order) != EXPECTED_FLOWS or len(order) != 3:
            errors.append("participant_orders_must_be_flow_permutations")
            break

    privacy = prereg.get("privacy") if isinstance(prereg.get("privacy"), dict) else {}
    if privacy.get("explicit_opt_in_required") is not True:
        errors.append("explicit_opt_in_required")
    if privacy.get("external_transmission") is not False:
        errors.append("external_transmission_must_be_false")
    if privacy.get("product_event_fields") != EXPECTED_EVENT_FIELDS:
        errors.append("product_event_allowlist_changed")
    if privacy.get("free_text_or_quotes_retained") is not False:
        errors.append("free_text_must_not_be_retained")

    tasks = prereg.get("tasks") if isinstance(prereg.get("tasks"), dict) else {}
    if set(tasks) != EXPECTED_FLOWS:
        errors.append("task_set_mismatch")
    scenarios = tasks.get("profile_selection", {}).get("scenarios", []) if isinstance(tasks.get("profile_selection"), dict) else []
    if {item.get("correct_profile") for item in scenarios if isinstance(item, dict)} != EXPECTED_PROFILES:
        errors.append("profile_reference_answers_incomplete")

    observer_fields = set(prereg.get("observer_fields") or [])
    if observer_fields & FORBIDDEN_OBSERVER_FIELDS:
        errors.append("observer_fields_include_private_content")
    required_observer_fields = {
        "flow", "completed", "assisted", "actions", "unnecessary_actions", "ui_error",
        "abandoned", "profile_choices_correct", "cost_risk_statements_correct",
        "dangerous_misconception",
    }
    if not required_observer_fields.issubset(observer_fields):
        errors.append("observer_rubric_incomplete")
    row_contract = prereg.get("observer_row_contract") if isinstance(
        prereg.get("observer_row_contract"), dict
    ) else {}
    if row_contract.get("unit") != "participant_flow":
        errors.append("observer_row_unit_must_be_participant_flow")
    if int(row_contract.get("rows_per_completed_session") or 0) != len(EXPECTED_FLOWS):
        errors.append("observer_row_count_mismatch")
    if set(row_contract.get("required_flows") or []) != EXPECTED_FLOWS:
        errors.append("observer_row_flows_incomplete")
    if row_contract.get("unique_key") != ["participant_code", "flow"]:
        errors.append("observer_row_unique_key_invalid")

    amendments = prereg.get("amendments_before_observation")
    if not isinstance(amendments, list) or not amendments:
        errors.append("pre_observation_amendment_missing")
    elif any(
        not isinstance(item, dict)
        or item.get("thresholds_changed") is not False
        or item.get("sessions_observed_before_amendment") != 0
        for item in amendments
    ):
        errors.append("pre_observation_amendment_invalid")

    gates = prereg.get("gates") if isinstance(prereg.get("gates"), dict) else {}
    if int(gates.get("inbox_min_unassisted_completions") or 0) != 7:
        errors.append("inbox_gate_changed")
    if int(gates.get("accepted_plan_min_unassisted_completions") or 0) != 7:
        errors.append("accepted_plan_gate_changed")
    if int(gates.get("profile_min_participants_passing") or 0) != 6:
        errors.append("profile_gate_changed")
    for key in ("max_ui_errors", "max_browser_errors", "max_privacy_schema_violations"):
        if gates.get(key) != 0:
            errors.append(f"{key}_must_be_zero")

    constructs = set(prereg.get("constructs_not_measured") or [])
    if not {"market_adoption", "causal_improvement", "universal_clarity"}.issubset(constructs):
        errors.append("interpretation_limits_incomplete")

    if template.get("status") != "awaiting_human_sessions":
        errors.append("result_template_must_be_empty")
    if template.get("protocol_version") != prereg.get("protocol_version"):
        errors.append("result_template_protocol_version_mismatch")
    if template.get("decision") is not None or template.get("gate_results") != {}:
        errors.append("result_template_contains_decision")
    if template.get("individual_rows_included") is not False or template.get("free_text_or_quotes_included") is not False:
        errors.append("result_template_privacy_contract_invalid")
    if template.get("constructs_not_measured") != prereg.get("constructs_not_measured"):
        errors.append("result_template_interpretation_mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = parser.parse_args()
    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    template = json.loads(args.template.read_text(encoding="utf-8"))
    errors = validate_preregistration(prereg, template)
    print(json.dumps({
        "valid": not errors,
        "status": prereg.get("status"),
        "protocol_version": prereg.get("protocol_version"),
        "errors": errors,
    }, ensure_ascii=False))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
