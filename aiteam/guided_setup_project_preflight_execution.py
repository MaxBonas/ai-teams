"""Plan puro para ejecutar un preflight de proyecto de forma proporcional."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from aiteam.guided_setup_needs import validate_needs_submission
from aiteam.guided_setup_project_preflight import (
    validate_project_preflight,
)

SCHEMA_VERSION = "guided_setup_project_preflight_execution_plan_v1"
_FIXTURE_CASE_BY_ECOSYSTEM = {
    "c_cpp": "c_cpp_cmake",
    "dotnet": "dotnet_xunit",
    "go": "go_builtin",
    "java_kotlin": "java_maven_junit",
    "javascript_typescript": "javascript_npm",
    "python": "python_pytest",
    "rust": "rust_cargo",
    "web_frontend": "web_vite_react_typescript",
}
_LANGUAGE_CASES = (
    (
        {"react", "typescript", "javascript", "css", "html", "frontend"},
        "web_vite_react_typescript",
    ),
    ({"python"}, "python_pytest"),
    ({"java", "kotlin"}, "java_maven_junit"),
    ({"c#", "csharp", ".net", "dotnet"}, "dotnet_xunit"),
    ({"go", "golang"}, "go_builtin"),
    ({"rust"}, "rust_cargo"),
    ({"c", "c++", "cpp", "cmake"}, "c_cpp_cmake"),
)
_EXECUTABLE_GATES = {"selected_adapters", "proportional_fixture"}
_SAFE_SCOPE = {
    "plan_only": True,
    "filesystem_mutated": False,
    "commands_executed": False,
    "tests_executed": False,
    "remote_calls": False,
    "secrets_read": False,
    "quota_consumed": False,
    "automatic_install": False,
}


def build_project_preflight_execution_plan(
    needs: Mapping[str, Any],
    proposal: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive at most one local fixture and one exact remote model probe."""
    sealed_needs = validate_needs_submission(needs, scope="project_setup")
    validate_project_preflight(preflight)
    proposal_hash = str(proposal.get("proposal_hash") or "")
    if proposal_hash != preflight["inputs"]["proposal_hash"]:
        raise ValueError("guided_setup_execution_proposal_hash_mismatch")
    if sealed_needs["assessment_hash"] != preflight["inputs"]["needs_hash"]:
        raise ValueError("guided_setup_execution_needs_hash_mismatch")

    gates = {str(row["id"]): row for row in preflight["gates"]}
    hard_blockers = [
        row
        for row in preflight["summary"]["blockers"]
        if row["gate"] not in _EXECUTABLE_GATES
    ]
    actions: list[dict[str, Any]] = []
    planning_blockers: list[dict[str, str]] = [
        {
            "code": str(row["code"]),
            "message": str(row["message"]),
            "next_action": str(row["next_action"]),
        }
        for row in hard_blockers
    ]

    fixture_gate = gates["proportional_fixture"]
    if (
        not hard_blockers
        and fixture_gate["required"]
        and fixture_gate["status"] != "passed"
    ):
        fixture_case = _select_fixture_case(sealed_needs, preflight)
        if fixture_case is None:
            planning_blockers.append(
                {
                    "code": "software_fixture_case_unknown",
                    "message": (
                        "No hay un fixture allowlisted para el stack declarado."
                    ),
                    "next_action": "confirm_project_stack",
                }
            )
        else:
            actions.append(
                {
                    "id": "local_fixture",
                    "kind": "software_toolchain_smoke",
                    "runner": "ecosystem_validation",
                    "case_id": fixture_case,
                    "timeout_seconds": 300,
                    "remote": False,
                    "quota_possible": False,
                    "workspace": "isolated_temporary_copy",
                    "max_attempts": 1,
                    "consent_requirements": ["confirm_local_fixture"],
                }
            )

    adapter_gate = gates["selected_adapters"]
    blocked_profiles = list(
        adapter_gate.get("evidence", {}).get("blocked_profile_ids") or ()
    )
    if not hard_blockers and not planning_blockers and blocked_profiles:
        exact = _first_blocked_model(proposal, blocked_profiles)
        if exact is None:
            planning_blockers.append(
                {
                    "code": "adapter_probe_identity_missing",
                    "message": (
                        "No se pudo resolver el adapter+modelo exacto a probar."
                    ),
                    "next_action": "review_project_team_assignment",
                }
            )
        else:
            actions.append(
                {
                    "id": "exact_adapter_probe",
                    "kind": "structured_output_probe",
                    "runner": "adapter_contract_probe",
                    "profile_id": exact["profile_id"],
                    "model_id": exact["model_id"],
                    "timeout_seconds": 90,
                    "remote": True,
                    "quota_possible": True,
                    "workspace": "none",
                    "max_attempts": 1,
                    "consent_requirements": [
                        "confirm_remote_probe",
                        "acknowledge_possible_quota",
                    ],
                }
            )

    status = (
        "blocked"
        if planning_blockers
        else "ready"
        if actions
        else "nothing_to_run"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "scope": dict(_SAFE_SCOPE),
        "inputs": {
            "needs_hash": sealed_needs["assessment_hash"],
            "proposal_hash": proposal_hash,
            "preflight_hash": preflight["preflight_hash"],
        },
        "policy": {
            "execution_order": [
                "local_fixture",
                "exact_adapter_probe",
            ],
            "fail_fast": True,
            "max_local_fixtures": 1,
            "max_remote_probes": 1,
            "max_attempts_per_action": 1,
            "automatic_install": False,
            "health_or_catalog_persisted": False,
        },
        "actions": actions,
        "planning_blockers": planning_blockers,
        "summary": {
            "status": status,
            "action_count": len(actions),
            "remote_action_count": sum(
                row["remote"] is True for row in actions
            ),
            "requires_consent": bool(actions),
            "next_action": (
                planning_blockers[0]["next_action"]
                if planning_blockers
                else "confirm_preflight_execution"
                if actions
                else "persist_preflight_before_commit"
            ),
        },
    }
    result["plan_hash"] = _hash(result)
    validate_project_preflight_execution_plan(result)
    return result


def validate_project_preflight_execution_plan(
    value: Mapping[str, Any],
) -> None:
    if set(value) != {
        "schema_version",
        "scope",
        "inputs",
        "policy",
        "actions",
        "planning_blockers",
        "summary",
        "plan_hash",
    }:
        raise ValueError("guided_setup_execution_plan_fields_drift")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("guided_setup_execution_plan_schema_drift")
    if value.get("scope") != _SAFE_SCOPE:
        raise ValueError("guided_setup_execution_scope_drift")
    inputs = value.get("inputs")
    if not isinstance(inputs, Mapping) or any(
        len(str(inputs.get(field) or "")) != 64
        for field in ("needs_hash", "proposal_hash", "preflight_hash")
    ):
        raise ValueError("guided_setup_execution_inputs_drift")
    actions = value.get("actions")
    if not isinstance(actions, list):
        raise TypeError("guided_setup_execution_actions_invalid")
    action_ids = [str(row.get("id") or "") for row in actions]
    if (
        len(action_ids) != len(set(action_ids))
        or action_ids
        != [
            action_id
            for action_id in ("local_fixture", "exact_adapter_probe")
            if action_id in action_ids
        ]
        or action_ids.count("local_fixture") > 1
        or action_ids.count("exact_adapter_probe") > 1
    ):
        raise ValueError("guided_setup_execution_action_matrix_drift")
    if any(
        row.get("max_attempts") != 1
        or not isinstance(row.get("timeout_seconds"), int)
        or row["timeout_seconds"] <= 0
        for row in actions
    ):
        raise ValueError("guided_setup_execution_action_bounds_drift")
    for row in actions:
        if row["id"] == "local_fixture":
            if (
                set(row)
                != {
                    "id",
                    "kind",
                    "runner",
                    "case_id",
                    "timeout_seconds",
                    "remote",
                    "quota_possible",
                    "workspace",
                    "max_attempts",
                    "consent_requirements",
                }
                or row["kind"] != "software_toolchain_smoke"
                or row["runner"] != "ecosystem_validation"
                or row["case_id"]
                not in set(_FIXTURE_CASE_BY_ECOSYSTEM.values())
                or row["timeout_seconds"] != 300
                or row["remote"] is not False
                or row["quota_possible"] is not False
                or row["workspace"] != "isolated_temporary_copy"
                or row["consent_requirements"]
                != ["confirm_local_fixture"]
            ):
                raise ValueError("guided_setup_execution_local_action_drift")
        elif (
            set(row)
            != {
                "id",
                "kind",
                "runner",
                "profile_id",
                "model_id",
                "timeout_seconds",
                "remote",
                "quota_possible",
                "workspace",
                "max_attempts",
                "consent_requirements",
            }
            or row["kind"] != "structured_output_probe"
            or row["runner"] != "adapter_contract_probe"
            or not str(row["profile_id"])
            or not str(row["model_id"])
            or row["timeout_seconds"] != 90
            or row["remote"] is not True
            or row["quota_possible"] is not True
            or row["workspace"] != "none"
            or row["consent_requirements"]
            != [
                "confirm_remote_probe",
                "acknowledge_possible_quota",
            ]
        ):
            raise ValueError("guided_setup_execution_remote_action_drift")
    if value.get("policy") != {
        "execution_order": ["local_fixture", "exact_adapter_probe"],
        "fail_fast": True,
        "max_local_fixtures": 1,
        "max_remote_probes": 1,
        "max_attempts_per_action": 1,
        "automatic_install": False,
        "health_or_catalog_persisted": False,
    }:
        raise ValueError("guided_setup_execution_policy_drift")
    blockers = value.get("planning_blockers")
    if not isinstance(blockers, list):
        raise TypeError("guided_setup_execution_blockers_invalid")
    status = "blocked" if blockers else "ready" if actions else "nothing_to_run"
    expected_summary = {
        "status": status,
        "action_count": len(actions),
        "remote_action_count": sum(
            row.get("remote") is True for row in actions
        ),
        "requires_consent": bool(actions),
        "next_action": (
            blockers[0]["next_action"]
            if blockers
            else "confirm_preflight_execution"
            if actions
            else "persist_preflight_before_commit"
        ),
    }
    if value.get("summary") != expected_summary:
        raise ValueError("guided_setup_execution_summary_drift")
    unhashed = dict(value)
    observed_hash = str(unhashed.pop("plan_hash", ""))
    if observed_hash != _hash(unhashed):
        raise ValueError("guided_setup_execution_plan_hash_drift")


def _select_fixture_case(
    needs: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> str | None:
    detected = [
        str(item)
        for item in preflight["objective"]["detected_ecosystems"]
    ]
    if "web_frontend" in detected:
        return _FIXTURE_CASE_BY_ECOSYSTEM["web_frontend"]
    for ecosystem_id in detected:
        selected = _FIXTURE_CASE_BY_ECOSYSTEM.get(ecosystem_id)
        if selected:
            return selected
    languages = {
        str(item).strip().lower()
        for item in needs["answers"].get("languages") or ()
        if str(item).strip()
    }
    for aliases, case_id in _LANGUAGE_CASES:
        if languages & aliases:
            return case_id
    return None


def _first_blocked_model(
    proposal: Mapping[str, Any],
    blocked_profiles: list[str],
) -> dict[str, str] | None:
    blocked = set(blocked_profiles)
    for assignment in (proposal.get("team") or {}).get("assignments") or ():
        candidate = assignment.get("candidate") or {}
        profile_id = str(candidate.get("profile_id") or "")
        model_id = str(candidate.get("model_id") or "")
        if profile_id in blocked and model_id:
            return {"profile_id": profile_id, "model_id": model_id}
    return None


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
