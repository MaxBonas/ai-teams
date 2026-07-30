"""Propuesta read-only de proyecto y equipo para el asistente guiado."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from aiteam.guided_setup_coverage import SCHEMA_VERSION as COVERAGE_SCHEMA_VERSION
from aiteam.guided_setup_needs import validate_needs_submission
from aiteam.run_profiles import (
    CANONICAL_RUN_PROFILES,
    build_default_team_blueprint,
)

SCHEMA_VERSION = "guided_setup_project_proposal_v1"
_IDENTITY_FIELDS = {
    "mode",
    "name",
    "target",
    "target_exists",
    "target_is_dir",
}
_IDENTITY_INTENT_FIELDS = {"mode", "name", "path"}


def normalize_project_identity_intent(
    value: Mapping[str, Any],
) -> dict[str, str]:
    """Validate owner intent before any server-side path resolution."""
    if set(value) != _IDENTITY_INTENT_FIELDS:
        raise ValueError("guided_setup_project_identity_intent_fields_invalid")
    mode = str(value.get("mode") or "").strip()
    name = str(value.get("name") or "").strip()
    path = str(value.get("path") or "").strip()
    if mode not in {"create", "import"}:
        raise ValueError("guided_setup_project_mode_invalid")
    if not 1 <= len(name) <= 120:
        raise ValueError("guided_setup_project_name_invalid")
    if len(path) > 1000 or mode == "import" and not path:
        raise ValueError("guided_setup_project_path_invalid")
    return {"mode": mode, "name": name, "path": path}


def build_project_team_proposal(
    needs: Mapping[str, Any],
    project_identity: Mapping[str, Any],
    ecosystem_detection: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    requested_profile: str | None = None,
    instructions: str = "",
    overrides_by_agent_id: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a sealed preview without reading or mutating machine state."""
    sealed_needs = validate_needs_submission(needs, scope="project_setup")
    identity = _project_identity(project_identity)
    ecosystems = _ecosystem_detection(ecosystem_detection)
    if coverage.get("schema_version") != COVERAGE_SCHEMA_VERSION:
        raise ValueError("guided_setup_project_coverage_schema_mismatch")
    recommended_profile = str(
        sealed_needs["assessment"]["recommended_run_profile"]
    )
    profile = str(requested_profile or recommended_profile)
    if profile not in CANONICAL_RUN_PROFILES:
        raise ValueError("guided_setup_project_profile_invalid")
    if profile not in (coverage.get("profiles") or {}):
        raise ValueError("guided_setup_project_profile_missing")
    clean_instructions = _instructions(instructions)
    blueprint = build_default_team_blueprint(
        "goal:preview",
        profile,
        objective=str(sealed_needs["answers"].get("goal") or ""),
        source="guided_setup_project_proposal",
    ).to_json_payload()
    overrides = {
        str(agent_id): str(candidate_id)
        for agent_id, candidate_id in (overrides_by_agent_id or {}).items()
        if str(agent_id) and str(candidate_id)
    }
    valid_agent_ids = {
        str(agent["agent_id"]) for agent in blueprint["agents"]
    }
    if not set(overrides).issubset(valid_agent_ids):
        raise ValueError("guided_setup_project_override_agent_invalid")

    assignments: list[dict[str, Any]] = []
    blockers: list[str] = []
    used_candidate_ids: set[str] = set()
    for agent in blueprint["agents"]:
        assignment = _assign_agent(
            agent,
            coverage,
            override_candidate_id=overrides.get(str(agent["agent_id"])),
            used_candidate_ids=used_candidate_ids,
            assignments=assignments,
        )
        if assignment is None:
            blockers.append(f"assignment:{agent['agent_id']}")
            continue
        assignments.append(assignment)
        used_candidate_ids.add(assignment["candidate"]["candidate_id"])

    diversity = _quorum_diversity(assignments)
    if profile == "lead_quorum" and not diversity["ready"]:
        blockers.append("quorum_diversity")
    profile_coverage = coverage["profiles"][profile]
    automatic_coverage_ready = profile_coverage.get("ready") is True
    manual_override_count = sum(
        row["selection_mode"] == "owner_explicit" for row in assignments
    )
    degradations = []
    if not automatic_coverage_ready:
        degradations.append("automatic_profile_coverage_incomplete")
    if manual_override_count:
        degradations.append("owner_override_does_not_grant_automatic_coverage")
    if ecosystems["scan_truncated"]:
        degradations.append("ecosystem_scan_truncated")
    if not ecosystems["detected_ids"]:
        degradations.append("no_ecosystem_detected")
    profile_override = profile != recommended_profile
    if profile_override:
        degradations.append("owner_changed_recommended_profile")

    save_allowed = not blockers
    result = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "read_only": True,
            "filesystem_mutated": False,
            "database_mutated": False,
            "project_created": False,
            "agents_created": False,
            "wakeups_created": False,
        },
        "project": {
            **identity,
            "instructions_target": ".aiteam/instructions.md",
            "instructions_preview": clean_instructions,
            "objective": str(sealed_needs["answers"].get("goal") or ""),
            "objective_kind": sealed_needs["assessment"]["objective"]["kind"],
            "data_class": sealed_needs["answers"]["data_sensitivity"],
        },
        "ecosystems": ecosystems,
        "profile": {
            "recommended": recommended_profile,
            "selected": profile,
            "owner_override": profile_override,
            "automatic_coverage_ready": automatic_coverage_ready,
            "coverage_status": profile_coverage["status"],
            "coverage_blockers": list(profile_coverage["blockers"]),
        },
        "team": {
            "lead_first": blueprint["metadata"]["lead_first"] is True,
            "creation_order": [
                str(agent["agent_id"]) for agent in blueprint["agents"]
            ],
            "blueprint": blueprint,
            "assignments": assignments,
            "quorum_diversity": diversity,
            "manual_override_count": manual_override_count,
        },
        "budget": blueprint["cost_policy"],
        "degradations": sorted(set(degradations)),
        "save_gate": {
            "allowed": save_allowed,
            "blockers": blockers,
            "requires_owner_confirmation": bool(
                profile_override or manual_override_count or degradations
            ),
        },
    }
    result["proposal_hash"] = _hash(result)
    return result


def _assign_agent(
    agent: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    override_candidate_id: str | None,
    used_candidate_ids: set[str],
    assignments: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    role = str(agent["role"])
    role_row = (coverage.get("roles") or {}).get(role) or {}
    eligible = [
        dict(row)
        for row in role_row.get("candidates") or ()
        if isinstance(row, Mapping)
    ]
    excluded = [
        dict(row)
        for row in role_row.get("excluded_candidates") or ()
        if isinstance(row, Mapping)
    ]
    selection_mode = "automatic"
    if override_candidate_id:
        candidate = next(
            (
                row
                for row in [*eligible, *excluded]
                if row.get("candidate_id") == override_candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("guided_setup_project_override_candidate_missing")
        if str(candidate.get("candidate_id") or "") in used_candidate_ids:
            raise ValueError("guided_setup_project_override_candidate_reused")
        if (
            candidate.get("owner_selectable") is not True
            or candidate.get("privacy", {}).get("allowed") is not True
            or "adapter_not_prepared_in_setup"
            in set(candidate.get("exclusion_reasons") or ())
        ):
            raise ValueError("guided_setup_project_override_not_selectable")
        selection_mode = "owner_explicit"
    else:
        candidate = _automatic_candidate(
            role,
            eligible,
            used_candidate_ids=used_candidate_ids,
            assignments=assignments,
        )
    if candidate is None:
        return None
    return {
        "agent_id": str(agent["agent_id"]),
        "role": role,
        "name": str(agent["name"]),
        "supervisor_agent_id": agent.get("supervisor_agent_id"),
        "assignment_reason": str(agent["assignment_reason"]),
        "selection_mode": selection_mode,
        "candidate": candidate,
        "accountability": {
            "reports_to": agent.get("supervisor_agent_id"),
            "acceptance_owner": (
                "owner"
                if agent.get("supervisor_agent_id") is None
                else agent.get("supervisor_agent_id")
            ),
            "required_evidence": "agent_report_and_role_contract",
        },
    }


def _automatic_candidate(
    role: str,
    candidates: list[dict[str, Any]],
    *,
    used_candidate_ids: set[str],
    assignments: list[Mapping[str, Any]],
) -> dict[str, Any] | None:
    available = [
        row
        for row in candidates
        if str(row.get("candidate_id") or "") not in used_candidate_ids
    ]
    if role != "quorum_auditor" or not assignments:
        return available[0] if available else None
    prior_quorum = [
        row["candidate"]
        for row in assignments
        if row.get("role") == "quorum_auditor"
    ]
    if not prior_quorum:
        return available[0] if available else None
    perspectives = {
        str(row.get("perspective_key") or "") for row in prior_quorum
    }
    pools = {str(row.get("capacity_pool") or "") for row in prior_quorum}
    return next(
        (
            row
            for row in available
            if str(row.get("perspective_key") or "") not in perspectives
            and str(row.get("capacity_pool") or "") not in pools
        ),
        None,
    )


def _quorum_diversity(
    assignments: list[Mapping[str, Any]],
) -> dict[str, Any]:
    quorum = [
        row["candidate"]
        for row in assignments
        if row.get("role") == "quorum_auditor"
    ]
    perspectives = {
        str(row.get("perspective_key") or "") for row in quorum
        if row.get("perspective_key")
    }
    pools = {
        str(row.get("capacity_pool") or "") for row in quorum
        if row.get("capacity_pool")
    }
    required = 2 if quorum else 0
    return {
        "required_count": required,
        "assigned_count": len(quorum),
        "perspective_count": len(perspectives),
        "capacity_pool_count": len(pools),
        "ready": (
            required == 0
            or len(quorum) >= required
            and len(perspectives) >= required
            and len(pools) >= required
        ),
    }


def _project_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _IDENTITY_FIELDS:
        raise ValueError("guided_setup_project_identity_fields_invalid")
    mode = str(value.get("mode") or "")
    name = str(value.get("name") or "").strip()
    target = str(value.get("target") or "").strip()
    exists = value.get("target_exists")
    is_dir = value.get("target_is_dir")
    if mode not in {"create", "import"}:
        raise ValueError("guided_setup_project_mode_invalid")
    if not 1 <= len(name) <= 120 or not target:
        raise ValueError("guided_setup_project_identity_invalid")
    if not isinstance(exists, bool) or not isinstance(is_dir, bool):
        raise TypeError("guided_setup_project_target_state_invalid")
    if mode == "create" and exists:
        raise ValueError("guided_setup_project_target_collision")
    if mode == "import" and (not exists or not is_dir):
        raise ValueError("guided_setup_project_import_target_invalid")
    return {
        "mode": mode,
        "name": name,
        "target": target,
        "target_exists": exists,
        "target_is_dir": is_dir,
    }


def _ecosystem_detection(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != "ecosystem_registry_v1":
        raise ValueError("guided_setup_project_ecosystem_schema_mismatch")
    if any(
        value.get(field) is not expected
        for field, expected in (
            ("commands_executed", False),
            ("installation_performed", False),
            ("mutated", False),
        )
    ):
        raise ValueError("guided_setup_project_ecosystem_detection_unsafe")
    rows = [
        {
            "id": str(row.get("id") or ""),
            "label": str(row.get("label") or ""),
            "status": str(row.get("status") or ""),
            "manifests": list(row.get("manifests") or ()),
            "extension_count": int(row.get("extension_count") or 0),
            "available_actions": list(row.get("available_actions") or ()),
            "support_claim": row.get("support_claim") is True,
        }
        for row in value.get("ecosystems") or ()
        if isinstance(row, Mapping) and row.get("id")
    ]
    return {
        "schema_version": "ecosystem_registry_v1",
        "workspace_observed": value.get("workspace_observed") is True,
        "scan_truncated": value.get("scan_truncated") is True,
        "files_observed": int(value.get("files_observed") or 0),
        "detected_ids": [row["id"] for row in rows],
        "ecosystems": rows,
        "support_claims": [],
        "commands_executed": False,
        "installation_performed": False,
        "mutated": False,
    }


def _instructions(value: str) -> str:
    clean = str(value or "").replace("\r\n", "\n").strip()
    if len(clean) > 20_000:
        raise ValueError("guided_setup_project_instructions_too_long")
    return clean


def _hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
