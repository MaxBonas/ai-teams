"""Materialización recuperable de una propuesta sellada del asistente."""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aiteam.objective_classification import classify_objective

SCHEMA_VERSION = "guided_setup_project_commit_v1"


def materialize_project_proposal(
    proposal: Mapping[str, Any],
    *,
    profiles: Sequence[Mapping[str, Any]],
    schema_path: Path,
) -> dict[str, Any]:
    """Create/import exactly one sealed proposal using staged filesystem writes."""
    if proposal.get("schema_version") != "guided_setup_project_proposal_v1":
        raise ValueError("guided_setup_project_proposal_schema_mismatch")
    if (proposal.get("save_gate") or {}).get("allowed") is not True:
        raise ValueError("guided_setup_project_save_blocked")
    proposal_hash = str(proposal.get("proposal_hash") or "")
    if len(proposal_hash) != 64:
        raise ValueError("guided_setup_project_proposal_hash_invalid")

    project = dict(proposal.get("project") or {})
    mode = str(project.get("mode") or "")
    target = Path(str(project.get("target") or "")).resolve()
    if mode not in {"create", "import"}:
        raise ValueError("guided_setup_project_mode_invalid")

    transaction_id = uuid.uuid4().hex
    staging: Path
    final_runtime: Path
    footprint_root: Path
    expected_entry: str
    if mode == "create":
        if target.exists():
            raise FileExistsError("guided_setup_project_target_collision")
        if not target.parent.is_dir():
            raise FileNotFoundError("guided_setup_project_parent_missing")
        staging = target.parent / f".aiteam-project-staging-{transaction_id}"
        final_runtime = target / ".aiteam"
        footprint_root = target.parent
        expected_entry = target.name
    else:
        if not target.is_dir():
            raise FileNotFoundError("guided_setup_project_import_target_invalid")
        final_runtime = target / ".aiteam"
        if final_runtime.exists():
            raise FileExistsError("guided_setup_project_runtime_collision")
        staging = target / f".aiteam-staging-{transaction_id}"
        footprint_root = target
        expected_entry = ".aiteam"

    initial_footprint = _snapshot_footprint(footprint_root)
    published = False
    git_managed = False
    try:
        staging.mkdir(parents=False, exist_ok=False)
        staging_runtime = staging / ".aiteam" if mode == "create" else staging
        if mode == "create":
            staging_runtime.mkdir(parents=True, exist_ok=False)
        _write_runtime(
            staging_runtime,
            proposal,
            profiles=profiles,
            schema_path=schema_path,
        )
        if mode == "create":
            from aiteam.workspace_git import init_managed_repo

            git_managed = init_managed_repo(staging)
        if mode == "create":
            staging.replace(target)
        else:
            staging.replace(final_runtime)
        published = True
        _assert_exact_footprint(
            footprint_root,
            initial_footprint | {expected_entry},
        )
        db_path = final_runtime / "aiteam.db"
        return {
            "schema_version": SCHEMA_VERSION,
            "proposal_hash": proposal_hash,
            "mode": mode,
            "workspace": str(target.as_posix()),
            "runtime": str(final_runtime.as_posix()),
            "database": str(db_path.as_posix()),
            "profile": str((proposal.get("profile") or {}).get("selected") or ""),
            "agent_ids": [
                _runtime_agent_id(str(row.get("agent_id") or ""))
                for row in (proposal.get("team") or {}).get("assignments") or ()
            ],
            "lead_first": True,
            "wakeup_created": True,
            "staged": True,
            "footprint_verified": True,
            "git_managed": git_managed,
        }
    except Exception:
        if published:
            rollback_target = target if mode == "create" else final_runtime
            cleanup_target = rollback_target
        else:
            cleanup_target = staging
        try:
            _remove_owned_tree(cleanup_target)
            _assert_exact_footprint(footprint_root, initial_footprint)
        except Exception as cleanup_exc:
            raise RuntimeError(
                "guided_setup_project_cleanup_failed:"
                f"{cleanup_target.name}"
            ) from cleanup_exc
        raise


def rollback_materialized_project(result: Mapping[str, Any]) -> None:
    """Undo only the tree owned by a failed guided-setup commit."""
    mode = str(result.get("mode") or "")
    workspace = Path(str(result.get("workspace") or "")).resolve()
    target = workspace if mode == "create" else workspace / ".aiteam"
    _remove_owned_tree(target)


def _write_runtime(
    runtime_dir: Path,
    proposal: Mapping[str, Any],
    *,
    profiles: Sequence[Mapping[str, Any]],
    schema_path: Path,
) -> None:
    project = dict(proposal["project"])
    team = dict(proposal["team"])
    assignments = [dict(row) for row in team.get("assignments") or ()]
    creation_order = [str(item) for item in team.get("creation_order") or ()]
    if [str(row.get("agent_id") or "") for row in assignments] != creation_order:
        raise ValueError("guided_setup_project_assignment_order_mismatch")
    if not creation_order or creation_order[0] != "role:team_lead":
        raise ValueError("guided_setup_project_lead_first_required")

    resolved = [
        _resolve_exact_adapter(row, profiles, proposal)
        for row in assignments
    ]
    profile_ids = list(dict.fromkeys(
        str(row["candidate"]["profile_id"]) for row in assignments
    ))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "instructions.md").write_text(
        str(project.get("instructions_preview") or "").rstrip() + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "project_config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "adapter_profile_ids": profile_ids,
                "adapter_policy": {
                    "senior_preference": "advanced",
                    "junior_preference": "cheap_or_local",
                    "source": "guided_setup_project_commit",
                    "proposal_hash": proposal["proposal_hash"],
                },
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    db_path = runtime_dir / "aiteam.db"
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        try:
            conn.execute("BEGIN IMMEDIATE")
            _insert_project_state(conn, proposal, assignments, resolved)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _resolve_exact_adapter(
    assignment: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = dict(assignment.get("candidate") or {})
    profile_id = str(candidate.get("profile_id") or "")
    model_id = str(candidate.get("model_id") or "")
    matching = [
        dict(row) for row in profiles
        if str(row.get("id") or "") == profile_id
    ]
    if len(matching) != 1:
        raise ValueError("guided_setup_project_adapter_profile_changed")
    profile = matching[0]
    options = [
        dict(row)
        for row in profile.get("model_options") or ()
        if isinstance(row, Mapping)
    ]
    selected_option = next(
        (
            row
            for row in options
            if str(row.get("value") or "") == model_id
        ),
        None,
    )
    if options and selected_option is None:
        raise ValueError("guided_setup_project_model_changed")
    adapter_type = str(profile.get("adapter_type") or "")
    if not adapter_type or not profile_id or not model_id:
        raise ValueError("guided_setup_project_adapter_profile_changed")
    config = {"profile_id": profile_id, "model": model_id}
    efforts = (selected_option or {}).get("reasoning_effort_by_role")
    role = str(assignment.get("role") or "")
    if isinstance(efforts, Mapping) and efforts.get(role):
        config["model_reasoning_effort"] = str(efforts[role])
    config["selection_intent"] = {
        "schema_version": "model_selection_intent_v1",
        "mode": (
            "owner_explicit"
            if assignment.get("selection_mode") == "owner_explicit"
            else "sealed_automatic"
        ),
        "source": "guided_setup_project_commit",
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "proposal_hash": str(proposal.get("proposal_hash") or ""),
    }
    return {
        "adapter_type": adapter_type,
        "adapter_config": config,
    }


def _insert_project_state(
    conn: sqlite3.Connection,
    proposal: Mapping[str, Any],
    assignments: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
) -> None:
    project = dict(proposal["project"])
    profile = str(proposal["profile"]["selected"])
    blueprint = dict(proposal["team"]["blueprint"])
    objective = str(project.get("objective") or "")
    title = objective[:160] or "Nuevo proyecto — cuéntame qué quieres construir"
    classification = classify_objective(
        title,
        objective,
        explicit_kind=str(project.get("objective_kind") or "auto"),
    )
    metadata = {
        "profile": profile,
        "source": "guided_setup_project_commit",
        "proposal_hash": proposal["proposal_hash"],
        "objective_classification": classification.to_metadata(),
    }
    conn.execute(
        """
        INSERT INTO goals (id, title, description, source, metadata_json)
        VALUES ('goal:intake', ?, ?, 'guided_setup_project_commit', ?)
        """,
        (title, objective, _json(metadata)),
    )

    blueprint_agents = {
        str(row["agent_id"]): dict(row)
        for row in blueprint.get("agents") or ()
    }
    for assignment, adapter in zip(assignments, resolved, strict=True):
        source_id = str(assignment["agent_id"])
        spec = blueprint_agents[source_id]
        agent_id = _runtime_agent_id(source_id)
        supervisor = spec.get("supervisor_agent_id")
        conn.execute(
            """
            INSERT INTO agents (
                id, role, name, seniority, adapter_type,
                adapter_config_json, capabilities_json,
                budget_monthly_cents, heartbeat_interval_sec,
                supervisor_agent_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (
                agent_id,
                _runtime_role(str(assignment["role"])),
                str(assignment["name"]),
                str(spec["seniority"]),
                adapter["adapter_type"],
                _json(adapter["adapter_config"]),
                _json(spec.get("capabilities") or []),
                _runtime_agent_id(str(supervisor)) if supervisor else None,
                _json({
                    "source": "guided_setup_project_commit",
                    "proposal_hash": proposal["proposal_hash"],
                    "candidate": assignment["candidate"],
                    "selection_mode": assignment["selection_mode"],
                    "accountability": assignment["accountability"],
                }),
            ),
        )

    conn.execute(
        """
        INSERT INTO issues (
            id, goal_id, title, description, status, role,
            complexity, criticality, assignee_agent_id, metadata_json
        ) VALUES (
            'issue:intake', 'goal:intake', ?, ?, 'todo', 'lead',
            'medium', 'medium', 'role:lead', ?
        )
        """,
        (
            title,
            objective,
            _json({
                **metadata,
                "wake_reason": "new_project",
                "data_class": project.get("data_class"),
            }),
        ),
    )
    if objective:
        conn.execute(
            """
            INSERT INTO issue_comments (
                id, issue_id, author_user_id, body, metadata_json
            ) VALUES (
                'comment:intake:user', 'issue:intake', 'user', ?, ?
            )
            """,
            (objective, _json({"source": "guided_setup_project_commit"})),
        )

    blueprint_id = f"blueprint:{proposal['proposal_hash'][:16]}"
    conn.execute(
        """
        INSERT INTO team_blueprints (
            id, goal_id, profile, status, proposed_by_agent_id,
            rationale, cost_policy_json, blueprint_json
        ) VALUES (?, 'goal:intake', ?, 'active', 'role:lead', ?, ?, ?)
        """,
        (
            blueprint_id,
            profile,
            str(blueprint.get("rationale") or ""),
            _json(blueprint.get("cost_policy") or {}),
            _json(blueprint),
        ),
    )
    for assignment in assignments:
        agent_id = _runtime_agent_id(str(assignment["agent_id"]))
        conn.execute(
            """
            INSERT INTO agent_assignments (
                id, blueprint_id, issue_id, agent_id,
                assigned_by_agent_id, assignment_reason,
                cost_policy_json, status
            ) VALUES (?, ?, 'issue:intake', ?, 'role:lead', ?, ?, 'active')
            """,
            (
                f"assignment:{agent_id.removeprefix('role:')}",
                blueprint_id,
                agent_id,
                str(assignment.get("assignment_reason") or ""),
                _json({
                    "proposal_hash": proposal["proposal_hash"],
                    "budget": proposal.get("budget") or {},
                }),
            ),
        )
    conn.execute(
        """
        INSERT INTO wakeup_requests (
            id, agent_id, source, reason, status,
            payload_json, idempotency_key
        ) VALUES (?, 'role:lead', 'guided_setup_project_commit',
                  'new_project', 'queued', ?, ?)
        """,
        (
            str(uuid.uuid4()),
            _json({
                "issue_id": "issue:intake",
                "wake_reason": "new_project",
                "profile": profile,
                "proposal_hash": proposal["proposal_hash"],
            }),
            f"bootstrap:{proposal['proposal_hash']}:role:lead",
        ),
    )


def _runtime_agent_id(agent_id: str) -> str:
    return "role:lead" if agent_id == "role:team_lead" else agent_id


def _runtime_role(role: str) -> str:
    return "lead" if role == "team_lead" else role


def _remove_owned_tree(path: Path) -> None:
    if not path.exists():
        return
    if (
        path.name.startswith(".aiteam-project-staging-")
        or path.name.startswith(".aiteam-staging-")
        or path.name == ".aiteam"
        or (path / ".aiteam" / "aiteam.db").exists()
    ):
        shutil.rmtree(path)
        if path.exists():
            raise OSError(f"guided_setup_project_owned_tree_still_exists:{path.name}")
        return
    raise ValueError(f"guided_setup_project_cleanup_target_not_owned:{path.name}")


def _snapshot_footprint(root: Path) -> set[str]:
    if not root.is_dir():
        raise FileNotFoundError("guided_setup_project_footprint_root_missing")
    return {entry.name for entry in root.iterdir()}


def _assert_exact_footprint(root: Path, expected: set[str]) -> None:
    observed = _snapshot_footprint(root)
    if observed != expected:
        added = sorted(observed - expected)
        removed = sorted(expected - observed)
        raise RuntimeError(
            "guided_setup_project_footprint_mismatch:"
            f"added={added}:removed={removed}"
        )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
