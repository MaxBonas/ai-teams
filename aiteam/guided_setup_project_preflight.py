"""Contrato puro de preflight proporcional para una propuesta de proyecto."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from aiteam.guided_setup_needs import validate_needs_submission

SCHEMA_VERSION = "guided_setup_project_preflight_v1"
PATH_OBSERVATION_VERSION = "guided_setup_project_path_observation_v1"
FIXTURE_EVIDENCE_VERSION = "guided_setup_fixture_evidence_v1"
_OBJECTIVE_KINDS = {"software", "research", "operations", "mixed"}
_FIXTURE_FIELDS = {
    "schema_version",
    "kind",
    "status",
    "receipt_ref",
    "commands_executed",
    "tests_executed",
    "remote_calls",
    "quota_consumed",
    "workspace_mutated",
}
_PATH_FIELDS = {
    "schema_version",
    "mode",
    "target_exists",
    "target_is_dir",
    "target_readable",
    "target_writable",
    "parent_exists",
    "parent_writable",
    "confined_to_projects_root",
}


def build_project_preflight(
    needs: Mapping[str, Any],
    proposal: Mapping[str, Any],
    preparation: Mapping[str, Any],
    machine_inventory: Mapping[str, Any],
    path_observation: Mapping[str, Any],
    *,
    fixture_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compose server-derived evidence without executing or persisting work."""
    sealed_needs = validate_needs_submission(needs, scope="project_setup")
    _validate_proposal(proposal, sealed_needs)
    _validate_preparation(preparation)
    _validate_inventory(machine_inventory)
    path = _validate_path_observation(path_observation, proposal)
    fixtures = [_validate_fixture_evidence(item) for item in fixture_evidence]

    objective_kind = str(
        (proposal.get("project") or {}).get("objective_kind") or ""
    )
    detected_ids = tuple(
        str(item)
        for item in (proposal.get("ecosystems") or {}).get("detected_ids") or ()
        if str(item)
    )
    selected_profiles = tuple(dict.fromkeys(
        str((row.get("candidate") or {}).get("profile_id") or "")
        for row in (proposal.get("team") or {}).get("assignments") or ()
        if str((row.get("candidate") or {}).get("profile_id") or "")
    ))

    gates = [
        _gate(
            "sealed_proposal",
            status="passed",
            required=True,
            evidence={
                "proposal_hash": str(proposal["proposal_hash"]),
                "objective_kind": objective_kind,
            },
        ),
        _path_gate(path),
        _runtime_gate(preparation),
        _adapter_gate(preparation, selected_profiles),
    ]
    toolchain_gate, toolchain_warnings = _toolchain_gate(
        objective_kind,
        detected_ids,
        machine_inventory,
    )
    gates.append(toolchain_gate)
    fixture_gate, fixture_policy, fixture_warnings = _fixture_gate(
        objective_kind,
        detected_ids,
        fixtures,
    )
    gates.append(fixture_gate)

    blockers = [
        {
            "gate": gate["id"],
            "code": gate["code"],
            "message": gate["message"],
            "next_action": gate["next_action"],
        }
        for gate in gates
        if gate["required"] and gate["status"] != "passed"
    ]
    warnings = [*toolchain_warnings, *fixture_warnings]
    if (proposal.get("ecosystems") or {}).get("scan_truncated") is True:
        warnings.append({
            "code": "ecosystem_detection_truncated",
            "message": "La detección fue truncada; confirma el stack antes de entrar.",
            "next_action": "review_detected_ecosystems",
        })
    warnings = _dedupe_rows(warnings)
    status = "go" if not blockers else "no_go"
    result = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "read_only": True,
            "filesystem_mutated": False,
            "database_mutated": False,
            "commands_executed": False,
            "tests_executed": False,
            "remote_probes_executed": False,
            "inference_attempted": False,
            "secrets_read": False,
            "quota_consumed": False,
        },
        "inputs": {
            "needs_hash": sealed_needs["assessment_hash"],
            "proposal_hash": str(proposal["proposal_hash"]),
            "preparation_hash": _hash(preparation),
            "machine_inventory_hash": _hash(machine_inventory),
            "path_observation_hash": _hash(path),
            "fixture_evidence_hash": _hash(fixtures),
        },
        "objective": {
            "kind": objective_kind,
            "software_surface_detected": bool(detected_ids),
            "detected_ecosystems": list(detected_ids),
        },
        "selected_profile_ids": list(selected_profiles),
        "fixture_policy": fixture_policy,
        "gates": gates,
        "summary": {
            "status": status,
            "go": status == "go",
            "commit_allowed": status == "go",
            "enter_project_allowed": False,
            "blockers": blockers,
            "warnings": warnings,
            "optional_pending": [],
            "next_action": (
                blockers[0]["next_action"]
                if blockers
                else "persist_preflight_before_commit"
            ),
        },
    }
    result["preflight_hash"] = _hash(result)
    validate_project_preflight(result)
    return result


def validate_project_preflight(value: Mapping[str, Any]) -> None:
    if set(value) != {
        "schema_version",
        "scope",
        "inputs",
        "objective",
        "selected_profile_ids",
        "fixture_policy",
        "gates",
        "summary",
        "preflight_hash",
    }:
        raise ValueError("guided_setup_preflight_fields_drift")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("guided_setup_preflight_schema_drift")
    if value.get("scope") != {
        "read_only": True,
        "filesystem_mutated": False,
        "database_mutated": False,
        "commands_executed": False,
        "tests_executed": False,
        "remote_probes_executed": False,
        "inference_attempted": False,
        "secrets_read": False,
        "quota_consumed": False,
    }:
        raise ValueError("guided_setup_preflight_scope_drift")
    gates = value.get("gates")
    expected_gate_ids = {
        "sealed_proposal",
        "project_path",
        "required_runtimes",
        "selected_adapters",
        "project_toolchains",
        "proportional_fixture",
    }
    if (
        not isinstance(gates, list)
        or {str(row.get("id") or "") for row in gates} != expected_gate_ids
        or len(gates) != len(expected_gate_ids)
    ):
        raise ValueError("guided_setup_preflight_gate_matrix_drift")
    if any(
        row.get("status")
        not in {"passed", "blocked", "not_checked", "not_applicable"}
        or not isinstance(row.get("required"), bool)
        for row in gates
    ):
        raise ValueError("guided_setup_preflight_gate_state_drift")
    blockers = [
        {
            "gate": row["id"],
            "code": row["code"],
            "message": row["message"],
            "next_action": row["next_action"],
        }
        for row in gates
        if row["required"] and row["status"] != "passed"
    ]
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise TypeError("guided_setup_preflight_summary_missing")
    expected_go = not blockers
    if (
        summary.get("status") != ("go" if expected_go else "no_go")
        or summary.get("go") is not expected_go
        or summary.get("commit_allowed") is not expected_go
        or summary.get("enter_project_allowed") is not False
        or summary.get("blockers") != blockers
        or summary.get("next_action")
        != (
            blockers[0]["next_action"]
            if blockers
            else "persist_preflight_before_commit"
        )
    ):
        raise ValueError("guided_setup_preflight_summary_drift")
    unhashed = dict(value)
    observed_hash = str(unhashed.pop("preflight_hash", ""))
    if observed_hash != _hash(unhashed):
        raise ValueError("guided_setup_preflight_hash_drift")


def _validate_proposal(
    proposal: Mapping[str, Any],
    needs: Mapping[str, Any],
) -> None:
    if proposal.get("schema_version") != "guided_setup_project_proposal_v1":
        raise ValueError("guided_setup_preflight_proposal_schema_mismatch")
    proposal_hash = str(proposal.get("proposal_hash") or "")
    if len(proposal_hash) != 64:
        raise ValueError("guided_setup_preflight_proposal_hash_invalid")
    if (proposal.get("save_gate") or {}).get("allowed") is not True:
        raise ValueError("guided_setup_preflight_proposal_blocked")
    project = proposal.get("project")
    if not isinstance(project, Mapping):
        raise TypeError("guided_setup_preflight_project_missing")
    kind = str(project.get("objective_kind") or "")
    expected_kind = str((needs.get("assessment") or {}).get("objective", {}).get("kind") or "")
    if kind not in _OBJECTIVE_KINDS or kind != expected_kind:
        raise ValueError("guided_setup_preflight_objective_kind_mismatch")
    if str(project.get("objective") or "") != str(
        (needs.get("answers") or {}).get("goal") or ""
    ):
        raise ValueError("guided_setup_preflight_objective_mismatch")


def _validate_preparation(preparation: Mapping[str, Any]) -> None:
    if preparation.get("schema_version") != "guided_setup_preparation_v1":
        raise ValueError("guided_setup_preflight_preparation_schema_mismatch")
    scope = preparation.get("scope")
    if not isinstance(scope, Mapping) or any(
        scope.get(key) is not expected
        for key, expected in (
            ("read_only", True),
            ("secrets_read", False),
            ("credentials_probed", False),
            ("installations_attempted", False),
            ("terms_accepted", False),
        )
    ):
        raise ValueError("guided_setup_preflight_preparation_scope_unsafe")


def _validate_inventory(inventory: Mapping[str, Any]) -> None:
    if inventory.get("schema_version") != "machine_doctor_v1":
        raise ValueError("guided_setup_preflight_inventory_schema_mismatch")
    scope = inventory.get("scope")
    if not isinstance(scope, Mapping) or any(
        scope.get(key) is not expected
        for key, expected in (
            ("read_only", True),
            ("secrets_read", False),
            ("credentials_probed", False),
        )
    ):
        raise ValueError("guided_setup_preflight_inventory_scope_unsafe")


def _validate_path_observation(
    observation: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    if set(observation) != _PATH_FIELDS:
        raise ValueError("guided_setup_preflight_path_fields_invalid")
    if observation.get("schema_version") != PATH_OBSERVATION_VERSION:
        raise ValueError("guided_setup_preflight_path_schema_mismatch")
    clean = dict(observation)
    mode = str((proposal.get("project") or {}).get("mode") or "")
    if clean.get("mode") != mode:
        raise ValueError("guided_setup_preflight_path_mode_mismatch")
    for field in _PATH_FIELDS - {"schema_version", "mode"}:
        if not isinstance(clean.get(field), bool):
            raise TypeError("guided_setup_preflight_path_state_invalid")
    return clean


def _validate_fixture_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _FIXTURE_FIELDS:
        raise ValueError("guided_setup_preflight_fixture_fields_invalid")
    if value.get("schema_version") != FIXTURE_EVIDENCE_VERSION:
        raise ValueError("guided_setup_preflight_fixture_schema_mismatch")
    if value.get("status") not in {"passed", "failed", "blocked"}:
        raise ValueError("guided_setup_preflight_fixture_status_invalid")
    for field in (
        "commands_executed",
        "tests_executed",
        "remote_calls",
        "quota_consumed",
        "workspace_mutated",
    ):
        if not isinstance(value.get(field), bool):
            raise TypeError("guided_setup_preflight_fixture_state_invalid")
    receipt_ref = str(value.get("receipt_ref") or "")
    path = PurePosixPath(receipt_ref)
    if (
        not receipt_ref
        or Path(receipt_ref).is_absolute()
        or ".." in path.parts
        or "\\" in receipt_ref
    ):
        raise ValueError("guided_setup_preflight_fixture_receipt_unsafe")
    return dict(value)


def _path_gate(path: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(path["mode"])
    if path["confined_to_projects_root"] is not True:
        return _gate(
            "project_path",
            status="blocked",
            required=True,
            code="project_path_outside_projects_root",
            message="La ruta queda fuera de la raíz de proyectos.",
            next_action="choose_confined_project_path",
        )
    if mode == "create":
        passed = (
            path["target_exists"] is False
            and path["parent_exists"] is True
            and path["parent_writable"] is True
        )
        code = "project_create_path_ready" if passed else "project_create_path_blocked"
        action = "continue" if passed else "fix_create_path_or_collision"
    else:
        passed = all(
            path[key] is True
            for key in (
                "target_exists",
                "target_is_dir",
                "target_readable",
                "target_writable",
            )
        )
        code = "project_import_path_ready" if passed else "project_import_path_blocked"
        action = "continue" if passed else "fix_import_path_permissions"
    return _gate(
        "project_path",
        status="passed" if passed else "blocked",
        required=True,
        code=code,
        message=(
            "La ruta está confinada y permite la operación."
            if passed
            else "La ruta no satisface existencia, colisión o permisos."
        ),
        next_action=action,
    )


def _runtime_gate(preparation: Mapping[str, Any]) -> dict[str, Any]:
    blocked = [
        str(row.get("id") or "")
        for row in preparation.get("runtimes") or ()
        if isinstance(row, Mapping) and row.get("state") != "ready"
    ]
    return _gate(
        "required_runtimes",
        status="passed" if not blocked else "blocked",
        required=True,
        code="required_runtimes_ready" if not blocked else "required_runtimes_blocked",
        message=(
            "Los runtimes base obligatorios están listos."
            if not blocked
            else f"Runtimes base pendientes: {', '.join(sorted(blocked))}."
        ),
        next_action="continue" if not blocked else "repair_required_runtimes",
        evidence={"blocked_ids": sorted(blocked)},
    )


def _adapter_gate(
    preparation: Mapping[str, Any],
    selected_profiles: tuple[str, ...],
) -> dict[str, Any]:
    prepared = {
        str(row.get("id") or ""): row
        for row in preparation.get("adapters") or ()
        if isinstance(row, Mapping)
    }
    blocked = []
    for profile_id in selected_profiles:
        row = prepared.get(profile_id)
        stages = (row or {}).get("stages") or {}
        if (
            row is None
            or row.get("state") != "ready"
            or stages.get("contract") != "passed"
        ):
            blocked.append(profile_id)
    return _gate(
        "selected_adapters",
        status="passed" if selected_profiles and not blocked else "blocked",
        required=True,
        code=(
            "selected_adapters_ready"
            if selected_profiles and not blocked
            else "selected_adapters_not_ready"
        ),
        message=(
            "Cada adapter asignado conserva health y contrato exactos."
            if selected_profiles and not blocked
            else "Algún adapter asignado no está preparado o carece de probe contractual."
        ),
        next_action=(
            "continue"
            if selected_profiles and not blocked
            else "repair_selected_adapters"
        ),
        evidence={
            "selected_profile_ids": list(selected_profiles),
            "blocked_profile_ids": sorted(blocked),
            "discovery_grants_ready": False,
        },
    )


def _toolchain_gate(
    objective_kind: str,
    detected_ids: tuple[str, ...],
    inventory: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if objective_kind in {"research", "operations"}:
        return (
            _gate(
                "project_toolchains",
                status="not_applicable",
                required=False,
                code="non_programming_objective",
                message="Este objetivo no requiere toolchains de software.",
                next_action="continue",
            ),
            [],
        )
    if not detected_ids:
        return (
            _gate(
                "project_toolchains",
                status="not_checked",
                required=False,
                code="toolchain_deferred_to_fixture",
                message="No hay manifiestos; el fixture proporcional comprobará el stack.",
                next_action="run_proportional_fixture",
            ),
            [{
                "code": "no_ecosystem_detected",
                "message": "No se detectó un ecosistema programativo en la propuesta.",
                "next_action": "confirm_stack_and_run_fixture",
            }],
        )
    observed = {
        str(row.get("id") or ""): row
        for row in inventory.get("toolchains") or ()
        if isinstance(row, Mapping)
    }
    blocked = [
        ecosystem_id
        for ecosystem_id in detected_ids
        if (observed.get(ecosystem_id) or {}).get("binary_installed") is not True
    ]
    return (
        _gate(
            "project_toolchains",
            status="passed" if not blocked else "blocked",
            required=True,
            code="toolchains_ready" if not blocked else "toolchains_missing",
            message=(
                "Los toolchains detectados están instalados."
                if not blocked
                else f"Toolchains pendientes: {', '.join(sorted(blocked))}."
            ),
            next_action="continue" if not blocked else "install_selected_toolchains",
            evidence={"blocked_ecosystem_ids": sorted(blocked)},
        ),
        [],
    )


def _fixture_gate(
    objective_kind: str,
    detected_ids: tuple[str, ...],
    fixtures: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    software_required = objective_kind == "software" or (
        objective_kind == "mixed" and bool(detected_ids)
    )
    expected_kind = (
        "software_toolchain_smoke"
        if software_required
        else "research_evidence_contract"
        if objective_kind == "research"
        else "operations_receipt_contract"
        if objective_kind == "operations"
        else "mixed_scope_contract"
    )
    policy = {
        "kind": expected_kind,
        "software_fixture_required": software_required,
        "remote_probe_requires_consent": True,
        "possible_quota_must_be_confirmed": True,
        "automatic_install": False,
        "max_attempts": 1,
    }
    if not software_required:
        if fixtures and any(
            row["commands_executed"]
            or row["tests_executed"]
            or row["remote_calls"]
            or row["quota_consumed"]
            or row["workspace_mutated"]
            for row in fixtures
        ):
            raise ValueError("guided_setup_preflight_non_programming_fixture_unsafe")
        return (
            _gate(
                "proportional_fixture",
                status="passed",
                required=True,
                code=f"{expected_kind}_passed",
                message="El contrato determinista del objetivo está completo sin tests.",
                next_action="continue",
                evidence={
                    "kind": expected_kind,
                    "commands_executed": False,
                    "tests_executed": False,
                },
            ),
            policy,
            [],
        )
    matching = [row for row in fixtures if row["kind"] == expected_kind]
    if any(
        row["remote_calls"] or row["quota_consumed"]
        for row in matching
    ):
        raise ValueError("guided_setup_preflight_fixture_remote_side_effect")
    passed = (
        len(matching) == 1
        and matching[0]["status"] == "passed"
        and matching[0]["commands_executed"] is True
        and matching[0]["workspace_mutated"] is False
    )
    return (
        _gate(
            "proportional_fixture",
            status="passed" if passed else "not_checked",
            required=True,
            code="software_fixture_passed" if passed else "software_fixture_required",
            message=(
                "El fixture programativo proporcional pasó sin mutar el workspace."
                if passed
                else "Falta ejecutar y sellar el fixture programativo proporcional."
            ),
            next_action="continue" if passed else "run_proportional_fixture",
            evidence={
                "kind": expected_kind,
                "matching_receipt_count": len(matching),
                "tests_executed": matching[0]["tests_executed"] if matching else False,
            },
        ),
        policy,
        [],
    )


def _gate(
    gate_id: str,
    *,
    status: str,
    required: bool,
    code: str | None = None,
    message: str = "",
    next_action: str = "continue",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "required": required,
        "status": status,
        "code": code or f"{gate_id}_{status}",
        "message": message,
        "next_action": next_action,
        "evidence": dict(evidence or {}),
    }


def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return list({row["code"]: row for row in rows}.values())


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
