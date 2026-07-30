"""Ejecutor efímero y fail-fast del plan de preflight de proyecto."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from aiteam.ecosystem_validation import validate_ecosystem_fixtures
from aiteam.guided_setup_project_preflight_execution import (
    validate_project_preflight_execution_plan,
)

SCHEMA_VERSION = "guided_setup_project_preflight_execution_receipt_v1"
FixtureRunner = Callable[[str, int], Mapping[str, Any]]
RemoteProbeRunner = Callable[[str, str, int], Mapping[str, Any]]


def execute_project_preflight_plan(
    plan: Mapping[str, Any],
    *,
    plan_hash: str,
    confirm_local_fixture: bool,
    confirm_remote_probe: bool,
    acknowledge_possible_quota: bool,
    fixture_runner: FixtureRunner | None = None,
    remote_probe_runner: RemoteProbeRunner | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Execute the exact sealed plan once, without persisting side effects."""
    validate_project_preflight_execution_plan(plan)
    if plan_hash != plan["plan_hash"]:
        raise ValueError("guided_setup_execution_plan_stale")
    if plan["summary"]["status"] == "blocked":
        raise ValueError("guided_setup_execution_plan_blocked")
    actions = list(plan["actions"])
    _validate_consents(
        actions,
        confirm_local_fixture=confirm_local_fixture,
        confirm_remote_probe=confirm_remote_probe,
        acknowledge_possible_quota=acknowledge_possible_quota,
    )
    if (
        any(row["id"] == "exact_adapter_probe" for row in actions)
        and remote_probe_runner is None
    ):
        raise ValueError("guided_setup_remote_probe_runner_unavailable")

    local_runner = fixture_runner or _run_local_fixture
    action_results: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for action in actions:
        if action["id"] == "local_fixture":
            result, normalized, artifact = _execute_local_fixture(
                action,
                runner=local_runner,
            )
        else:
            result, normalized, artifact = _execute_remote_probe(
                action,
                runner=remote_probe_runner,
            )
        action_results.append(result)
        if normalized is not None:
            evidence.append(normalized)
        if artifact is not None:
            artifacts.append(artifact)
        if result["status"] != "passed":
            break

    expected_count = len(actions)
    executed_count = len(action_results)
    passed_count = sum(row["status"] == "passed" for row in action_results)
    status = (
        "nothing_to_run"
        if not actions
        else "passed"
        if executed_count == expected_count and passed_count == expected_count
        else "failed"
    )
    timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": timestamp.isoformat(),
        "inputs": {
            "execution_plan_hash": plan["plan_hash"],
            **dict(plan["inputs"]),
        },
        "scope": {
            "ephemeral": True,
            "user_workspace_mutated": False,
            "temporary_workspace_mutated": any(
                row["id"] == "local_fixture" for row in action_results
            ),
            "database_mutated": False,
            "health_or_catalog_persisted": False,
            "defaults_mutated": False,
            "automatic_install": False,
            "remote_calls": any(
                row["id"] == "exact_adapter_probe"
                for row in action_results
            ),
            "quota_possible": any(
                row["id"] == "exact_adapter_probe"
                for row in action_results
            ),
        },
        "policy": {
            "fail_fast": True,
            "max_attempts_per_action": 1,
            "all_consents_checked_before_execution": True,
            "redacted_receipts_only": True,
        },
        "actions": action_results,
        "fixture_evidence": evidence,
        "artifacts": artifacts,
        "summary": {
            "status": status,
            "planned_count": expected_count,
            "executed_count": executed_count,
            "passed_count": passed_count,
            "next_action": (
                "recompose_project_preflight"
                if status in {"passed", "nothing_to_run"}
                else "review_failed_preflight_action"
            ),
        },
    }
    receipt["receipt_hash"] = _hash(receipt)
    validate_project_preflight_execution_receipt(receipt)
    return receipt


def validate_project_preflight_execution_receipt(
    value: Mapping[str, Any],
) -> None:
    if set(value) != {
        "schema_version",
        "observed_at",
        "inputs",
        "scope",
        "policy",
        "actions",
        "fixture_evidence",
        "artifacts",
        "summary",
        "receipt_hash",
    }:
        raise ValueError("guided_setup_execution_receipt_fields_drift")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("guided_setup_execution_receipt_schema_drift")
    actions = value.get("actions")
    if not isinstance(actions, list) or len(actions) > 2:
        raise ValueError("guided_setup_execution_receipt_actions_drift")
    if any(
        row.get("status") not in {"passed", "failed", "blocked"}
        or row.get("attempts") != 1
        for row in actions
    ):
        raise ValueError("guided_setup_execution_receipt_action_state_drift")
    if value.get("policy") != {
        "fail_fast": True,
        "max_attempts_per_action": 1,
        "all_consents_checked_before_execution": True,
        "redacted_receipts_only": True,
    }:
        raise ValueError("guided_setup_execution_receipt_policy_drift")
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise TypeError("guided_setup_execution_receipt_summary_missing")
    passed_count = sum(row["status"] == "passed" for row in actions)
    planned_count = int(summary.get("planned_count") or 0)
    expected_status = (
        "nothing_to_run"
        if planned_count == 0
        else "passed"
        if len(actions) == planned_count and passed_count == planned_count
        else "failed"
    )
    if summary != {
        "status": expected_status,
        "planned_count": planned_count,
        "executed_count": len(actions),
        "passed_count": passed_count,
        "next_action": (
            "recompose_project_preflight"
            if expected_status in {"passed", "nothing_to_run"}
            else "review_failed_preflight_action"
        ),
    }:
        raise ValueError("guided_setup_execution_receipt_summary_drift")
    unhashed = dict(value)
    observed_hash = str(unhashed.pop("receipt_hash", ""))
    if observed_hash != _hash(unhashed):
        raise ValueError("guided_setup_execution_receipt_hash_drift")


def _validate_consents(
    actions: list[dict[str, Any]],
    *,
    confirm_local_fixture: bool,
    confirm_remote_probe: bool,
    acknowledge_possible_quota: bool,
) -> None:
    if (
        any(row["id"] == "local_fixture" for row in actions)
        and confirm_local_fixture is not True
    ):
        raise ValueError("guided_setup_local_fixture_consent_required")
    if any(row["id"] == "exact_adapter_probe" for row in actions):
        if confirm_remote_probe is not True:
            raise ValueError("guided_setup_remote_probe_consent_required")
        if acknowledge_possible_quota is not True:
            raise ValueError("guided_setup_remote_probe_quota_ack_required")


def _execute_local_fixture(
    action: Mapping[str, Any],
    *,
    runner: FixtureRunner,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    try:
        raw = dict(
            runner(
                str(action["case_id"]),
                int(action["timeout_seconds"]),
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return (
            {
                "id": "local_fixture",
                "status": "failed",
                "attempts": 1,
                "reason": f"runner_error:{exc.__class__.__name__}",
                "receipt_ref": None,
            },
            None,
            None,
        )
    cases = list(raw.get("cases") or ())
    selected = [
        row for row in cases if str(row.get("id") or "") == action["case_id"]
    ]
    passed = (
        len(selected) == 1
        and selected[0].get("status") == "passed"
        and int((raw.get("summary") or {}).get("total") or 0) == 1
    )
    raw_hash = _hash(raw)
    reference = f"sha256:{raw_hash}"
    status = "passed" if passed else "failed"
    result = {
        "id": "local_fixture",
        "status": status,
        "attempts": 1,
        "reason": (
            "allowlisted_fixture_passed"
            if passed
            else "allowlisted_fixture_failed"
        ),
        "receipt_ref": reference,
    }
    normalized = {
        "schema_version": "guided_setup_fixture_evidence_v1",
        "kind": "software_toolchain_smoke",
        "status": status,
        "receipt_ref": reference,
        "commands_executed": True,
        "tests_executed": True,
        "remote_calls": False,
        "quota_consumed": False,
        "workspace_mutated": False,
    }
    artifact = {
        "ref": reference,
        "kind": "ecosystem_validation_receipt",
        "content": raw,
    }
    return result, normalized, artifact


def _execute_remote_probe(
    action: Mapping[str, Any],
    *,
    runner: RemoteProbeRunner | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    if runner is None:
        raise ValueError("guided_setup_remote_probe_runner_unavailable")
    raw = dict(
        runner(
            str(action["profile_id"]),
            str(action["model_id"]),
            int(action["timeout_seconds"]),
        )
    )
    passed = raw.get("status") == "passed"
    raw_hash = _hash(raw)
    reference = f"sha256:{raw_hash}"
    return (
        {
            "id": "exact_adapter_probe",
            "status": "passed" if passed else "failed",
            "attempts": 1,
            "reason": (
                "exact_structured_probe_passed"
                if passed
                else "exact_structured_probe_failed"
            ),
            "receipt_ref": reference,
        },
        None,
        {
            "ref": reference,
            "kind": "adapter_contract_probe_receipt",
            "content": raw,
        },
    )


def _run_local_fixture(
    case_id: str,
    _timeout_seconds: int,
) -> Mapping[str, Any]:
    return validate_ecosystem_fixtures(
        selected_case_ids=(case_id,),
        execute=True,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
