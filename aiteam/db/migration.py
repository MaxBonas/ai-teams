from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.run_profiles import build_default_team_blueprint, normalize_run_profile


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_NAMESPACE = uuid.UUID("a1e2e770-5e1f-49c1-b6cf-8b2c4a9d9a01")


@dataclass(frozen=True)
class MigrationSummary:
    db_path: str
    applied: bool
    backup_path: str | None
    legacy_tasks: int
    goals: int
    agents: int
    team_blueprints: int
    issues: int
    issue_dependencies: int
    agent_assignments: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def migrate_to_v2(
    db_path: Path,
    *,
    apply: bool = False,
    backup: bool = True,
) -> MigrationSummary:
    """Preview or apply the v2 control-plane schema migration.

    The migration is intentionally parallel: it reads legacy `tasks` and
    `workflow_state_entries` data, writes normalized v2 tables only when
    `apply=True`, and never deletes or mutates legacy tables.
    """
    db_path = Path(db_path)
    if not db_path.exists() and not apply:
        return MigrationSummary(
            db_path=str(db_path),
            applied=False,
            backup_path=None,
            legacy_tasks=0,
            goals=0,
            agents=0,
            team_blueprints=0,
            issues=0,
            issue_dependencies=0,
            agent_assignments=0,
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path, readonly=not apply)
    with contextlib.closing(conn):
        legacy_tasks = _load_legacy_tasks(conn)
        workflow_state = _load_workflow_state(conn)
        plan = _build_plan(legacy_tasks, workflow_state)
        backup_path = None
        if apply:
            backup_path = _backup_database(db_path) if backup and db_path.exists() else None
            _apply_plan(conn, plan)
        return MigrationSummary(
            db_path=str(db_path),
            applied=apply,
            backup_path=str(backup_path) if backup_path else None,
            legacy_tasks=len(legacy_tasks),
            goals=len(plan["goals"]),
            agents=len(plan["agents"]),
            team_blueprints=len(plan["team_blueprints"]),
            issues=len(plan["issues"]),
            issue_dependencies=len(plan["issue_dependencies"]),
            agent_assignments=len(plan["agent_assignments"]),
        )


def _connect(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=20.0)
    else:
        conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_legacy_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "tasks"):
        return []
    rows = conn.execute("SELECT payload FROM tasks ORDER BY task_id").fetchall()
    tasks: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        if isinstance(payload, dict) and str(payload.get("task_id", "") or "").strip():
            tasks.append(payload)
    return tasks


def _load_workflow_state(conn: sqlite3.Connection) -> dict[str, Any]:
    if _table_exists(conn, "workflow_state_entries"):
        rows = conn.execute(
            "SELECT task_root, payload FROM workflow_state_entries ORDER BY task_root"
        ).fetchall()
        if rows:
            state: dict[str, Any] = {}
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except Exception:
                    continue
                if isinstance(payload, dict):
                    state[str(row["task_root"])] = payload
            return state
    if not _table_exists(conn, "workflow_state"):
        return {}
    row = conn.execute("SELECT payload FROM workflow_state WHERE id = 1").fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row["payload"])
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_plan(
    legacy_tasks: list[dict[str, Any]],
    workflow_state: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    role_set = {
        _clean(task.get("role")) or "team_lead"
        for task in legacy_tasks
    } | {"team_lead"}
    roles = ["team_lead", *sorted(role for role in role_set if role != "team_lead")]
    agents = [_agent_row(role) for role in roles]

    roots = sorted({_task_root(str(task.get("task_id", "") or "")) for task in legacy_tasks})
    goals = [_goal_row(root, workflow_state.get(root, {})) for root in roots if root]
    team_blueprints = [
        _team_blueprint_row(root, workflow_state.get(root, {}))
        for root in roots
        if root
    ]

    issues: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    task_ids = {str(task.get("task_id", "") or "") for task in legacy_tasks}

    for task in legacy_tasks:
        issue = _issue_row(task)
        issues.append(issue)
        role = _clean(task.get("role")) or "team_lead"
        if issue["assignee_agent_id"]:
            assignments.append(_assignment_row(issue["id"], role))
        for dep in list(task.get("dependencies", []) or []):
            dep_id = str(dep or "").strip()
            if dep_id and dep_id in task_ids:
                dependencies.append(
                    {
                        "issue_id": issue["id"],
                        "depends_on_issue_id": dep_id,
                        "relation_type": "blocks",
                    }
                )

    return {
        "goals": goals,
        "agents": agents,
        "team_blueprints": team_blueprints,
        "issues": issues,
        "issue_dependencies": dependencies,
        "agent_assignments": assignments,
    }


def _apply_plan(conn: sqlite3.Connection, plan: dict[str, list[dict[str, Any]]]) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute("BEGIN")
    try:
        _upsert_rows(conn, "goals", plan["goals"])
        _upsert_rows(conn, "agents", plan["agents"])
        _upsert_rows(conn, "team_blueprints", plan["team_blueprints"])
        _upsert_rows(conn, "issues", plan["issues"])
        _upsert_issue_dependencies(conn, plan["issue_dependencies"])
        _upsert_rows(conn, "agent_assignments", plan["agent_assignments"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _upsert_rows(
    conn: sqlite3.Connection,
    table_name: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "id")
    sql = (
        f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {update_sql}"
    )
    conn.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])


def _upsert_issue_dependencies(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR IGNORE INTO issue_dependencies
            (issue_id, depends_on_issue_id, relation_type)
        VALUES (?, ?, ?)
        """,
        [
            (
                row["issue_id"],
                row["depends_on_issue_id"],
                row.get("relation_type", "blocks"),
            )
            for row in rows
        ],
    )


def _backup_database(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.pre_v2_{stamp}{db_path.suffix}.bak")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _goal_row(root: str, workflow_entry: Any) -> dict[str, Any]:
    payload = workflow_entry if isinstance(workflow_entry, dict) else {}
    title = _clean(payload.get("objective")) or _clean(payload.get("title")) or root
    status = "done" if _clean(payload.get("status")) in {"completed", "done"} else "active"
    return {
        "id": root,
        "title": title,
        "description": _clean(payload.get("summary")) or None,
        "status": status,
        "source": "legacy_taskboard",
        "metadata_json": _json(payload),
    }


def _team_blueprint_row(root: str, workflow_entry: Any) -> dict[str, Any]:
    payload = workflow_entry if isinstance(workflow_entry, dict) else {}
    profile = normalize_run_profile(payload.get("run_profile") or payload.get("profile"))
    blueprint = build_default_team_blueprint(
        root,
        profile,
        objective=_clean(payload.get("objective")) or _clean(payload.get("title")),
        source="legacy_workflow_state",
    )
    blueprint_payload = blueprint.to_json_payload()
    blueprint_payload["legacy_workflow_state"] = payload
    return {
        "id": f"blueprint:{root}",
        "goal_id": root,
        "profile": profile,
        "status": "proposed",
        "proposed_by_agent_id": "role:team_lead",
        "rationale": blueprint.rationale,
        "cost_policy_json": _json(blueprint.cost_policy),
        "blueprint_json": _json(blueprint_payload),
    }


def _issue_row(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("task_id", "") or "").strip()
    role = _clean(task.get("role")) or None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    state = _clean(task.get("state")) or "pending"
    return {
        "id": task_id,
        "parent_id": None,
        "goal_id": _task_root(task_id),
        "title": _clean(task.get("title")) or task_id,
        "description": _clean(task.get("description")) or None,
        "status": _issue_status(state),
        "priority": _priority(metadata),
        "role": role,
        "complexity": _clean(task.get("complexity")) or None,
        "criticality": _clean(task.get("criticality")) or None,
        "assignee_agent_id": _agent_id(role) if role else None,
        "checkout_run_id": None,
        "execution_run_id": None,
        "execution_locked_at": None,
        "identifier": task_id,
        "source_task_id": task_id,
        "metadata_json": _json({"legacy_task": task}),
    }


def _assignment_row(issue_id: str, role: str) -> dict[str, Any]:
    return {
        "id": _stable_id("assignment", issue_id, role),
        "blueprint_id": f"blueprint:{_task_root(issue_id)}",
        "issue_id": issue_id,
        "agent_id": _agent_id(role),
        "assigned_by_agent_id": "role:team_lead" if role != "team_lead" else None,
        "assignment_reason": "Imported from legacy task role.",
        "cost_policy_json": _json({}),
        "status": "active",
    }


def _agent_row(role: str) -> dict[str, Any]:
    return {
        "id": _agent_id(role),
        "role": role,
        "name": role.replace("_", " ").title(),
        "seniority": _seniority_for_role(role),
        "adapter_type": None,
        "adapter_config_json": _json({}),
        "capabilities_json": _json(_capabilities_for_role(role)),
        "budget_monthly_cents": 0,
        "spent_monthly_cents": 0,
        "heartbeat_interval_sec": 0,
        "last_heartbeat_at": None,
        "status": "active",
        "supervisor_agent_id": None if role == "team_lead" else "role:team_lead",
        "metadata_json": _json({"source": "legacy_role_import"}),
    }


def _issue_status(state: str) -> str:
    return {
        "pending": "backlog",
        "ready": "todo",
        "claimed": "in_progress",
        "blocked": "blocked",
        "waiting_user": "blocked",
        "completed": "done",
        "skipped": "cancelled",
        "failed": "cancelled",
        "archived": "cancelled",
    }.get(state, "backlog")


def _priority(metadata: dict[str, Any]) -> int:
    raw = metadata.get("priority", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _seniority_for_role(role: str) -> str:
    if role == "team_lead":
        return "lead"
    if role in {"reviewer"}:
        return "senior"
    if role == "scout":
        return "cheap"
    return "standard"


def _capabilities_for_role(role: str) -> list[str]:
    return {
        "team_lead": ["planning", "supervision", "hiring", "cost_policy"],
        "scout": ["long_read", "context_compression", "simple_research"],
        "researcher": ["research", "analysis", "context"],
        "engineer": ["code_change", "implementation"],
        "reviewer": ["review", "risk_assessment"],
        "qa": ["test", "validation"],
    }.get(role, [])


def _task_root(task_id: str) -> str:
    return task_id.split("::", 1)[0].strip()


def _agent_id(role: str) -> str:
    return f"role:{role}"


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, ":".join(parts)))


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
