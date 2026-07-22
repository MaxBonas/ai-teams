from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.migration import migrate_to_v2
from aiteam.sqlite_store import SqliteStore


def _table_exists(db_path: Path, table_name: str) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    return row is not None


def _count_rows(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0])


def _seed_legacy_db(db_path: Path) -> None:
    store = SqliteStore(db_path)
    store.save_all_tasks(
        [
            {
                "task_id": "CHAT-001::lead_intake",
                "title": "Lead intake",
                "description": "Understand request",
                "role": "team_lead",
                "complexity": "high",
                "criticality": "medium",
                "dependencies": [],
                "state": "completed",
                "assignee": "lead-1",
                "metadata": {"phase": "lead_intake", "result": "Plan ready"},
            },
            {
                "task_id": "CHAT-001::build",
                "title": "Implement feature",
                "description": "Make the scoped code change",
                "role": "engineer",
                "complexity": "medium",
                "criticality": "high",
                "dependencies": ["CHAT-001::lead_intake"],
                "state": "ready",
                "assignee": None,
                "metadata": {"phase": "build", "priority": 3},
            },
            {
                "task_id": "CHAT-001::scout_context",
                "title": "Read context",
                "description": "Summarize long files",
                "role": "scout",
                "complexity": "low",
                "criticality": "low",
                "dependencies": ["CHAT-001::lead_intake"],
                "state": "pending",
                "assignee": None,
                "metadata": {"phase": "scout_context"},
            },
        ]
    )
    store.save_workflow_state(
        {
            "CHAT-001": {
                "objective": "Build a durable software team control plane",
                "run_profile": "full_team",
            }
        }
    )


def test_v2_migration_dry_run_does_not_create_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _seed_legacy_db(db_path)

    summary = migrate_to_v2(db_path, apply=False)

    assert summary.applied is False
    assert summary.legacy_tasks == 3
    assert summary.goals == 1
    assert summary.agents == 3
    assert summary.team_blueprints == 1
    assert summary.issues == 3
    assert summary.issue_dependencies == 2
    assert summary.agent_assignments == 3
    assert not _table_exists(db_path, "issues")


def test_v2_migration_apply_creates_parallel_control_plane(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _seed_legacy_db(db_path)

    summary = migrate_to_v2(db_path, apply=True, backup=False)

    assert summary.applied is True
    assert summary.backup_path is None
    assert _table_exists(db_path, "issues")
    assert _table_exists(db_path, "runs")
    assert _table_exists(db_path, "wakeup_requests")
    assert _table_exists(db_path, "dispatch_candidate_decisions")
    assert _table_exists(db_path, "team_blueprints")
    assert _count_rows(db_path, "tasks") == 3
    assert _count_rows(db_path, "goals") == 1
    assert _count_rows(db_path, "agents") == 3
    assert _count_rows(db_path, "issues") == 3
    assert _count_rows(db_path, "issue_dependencies") == 2
    assert _count_rows(db_path, "agent_assignments") == 3

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        build = conn.execute(
            "SELECT status, role, priority, assignee_agent_id FROM issues WHERE id = ?",
            ("CHAT-001::build",),
        ).fetchone()
        blueprint = conn.execute(
            "SELECT profile, proposed_by_agent_id, cost_policy_json, blueprint_json FROM team_blueprints WHERE id = ?",
            ("blueprint:CHAT-001",),
        ).fetchone()

    assert dict(build) == {
        "status": "todo",
        "role": "engineer",
        "priority": 3,
        "assignee_agent_id": "role:engineer",
    }
    assert blueprint["profile"] == "full_team"
    assert blueprint["proposed_by_agent_id"] == "role:team_lead"
    cost_policy = json.loads(blueprint["cost_policy_json"])
    blueprint_payload = json.loads(blueprint["blueprint_json"])
    assert cost_policy["delegation_allowed"] is True
    assert cost_policy["cheap_delegate_roles"] == ["engineer"]
    assert [agent["role"] for agent in blueprint_payload["agents"]] == [
        "team_lead",
        "engineer",
        "reviewer",
    ]


def test_v2_migration_apply_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _seed_legacy_db(db_path)

    first = migrate_to_v2(db_path, apply=True, backup=False)
    second = migrate_to_v2(db_path, apply=True, backup=False)

    assert first.to_dict() == second.to_dict()
    assert _count_rows(db_path, "issues") == 3
    assert _count_rows(db_path, "agent_assignments") == 3
