"""Delegation role/work mismatch: a read-only (Tier 3) role can never satisfy
a task that explicitly asks it to modify files.

Live bug: the Lead delegated "Fix imports in CreateTestSceneEditor.cs" —
a perfectly-specified code fix — with role=file_scout instead of engineer.
file_scout is read-only by contract (read, report, close in the same run);
it re-confirmed the same diagnosis and closed done WITHOUT changing anything.
The Lead read that as "fix delegated" and moved on, leaving the review
permanently blocked on a fix that never materialized.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, ExecutionResult, build_default_registry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES"
            " ('role:lead', 'lead', 'Lead', 'lead', 'openai_api')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:root', 'goal-1', 'Root', 'in_progress', 'lead', 'role:lead')"
        )
        conn.commit()


class _LeadRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def __init__(self, actions: dict[str, Any]) -> None:
        self._actions = actions

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="delegated", actions=self._actions)


def _dispatch_lead(db_path: Path) -> Any:
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "issue:root"},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")


def _run_delegation(tmp_path: Path, *, role: str, description: str) -> Path:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    actions = {
        "create_issues": [{
            "title": "Fix imports in CreateTestSceneEditor.cs",
            "description": description,
            "role": role,
            "complexity": "low",
        }],
    }
    registry = build_default_registry()
    registry._items["openai_api"] = _LeadRuntime(actions)
    executor = RunExecutor(db_path, registry)
    executor.execute(_dispatch_lead(db_path))
    return db_path


_REAL_DESCRIPTION = (
    "Technology: Unity/C# editor script fix in the existing Unity project.\n\n"
    "Objective: apply the exact user-requested compile fix in "
    "`Assets/Editor/CreateTestSceneEditor.cs` by adding these two import "
    "directives near the existing `using` statements:\n\n"
    "using UnityEngine.SceneManagement;\nusing SurvivalPrototype;\n\n"
    "Files to modify: only `Assets/Editor/CreateTestSceneEditor.cs`.\n\n"
    "Acceptance criteria:\n"
    "1. Assets/Editor/CreateTestSceneEditor.cs contains "
    "`using UnityEngine.SceneManagement;` so the `Scene` type resolves."
)


def test_file_scout_editing_delegation_rejected(tmp_path: Path) -> None:
    db_path = _run_delegation(tmp_path, role="file_scout", description=_REAL_DESCRIPTION)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        created = conn.execute(
            "SELECT id FROM issues WHERE title = 'Fix imports in CreateTestSceneEditor.cs'"
        ).fetchone()
        rejection = conn.execute(
            "SELECT body FROM issue_comments WHERE issue_id = 'issue:root' AND author_user_id = 'system'"
        ).fetchone()
        denied = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'delegation.role_mismatch'"
        ).fetchone()

    assert created is None, "the read-only-role editing delegation must not be created"
    assert rejection is not None
    assert "file_scout" in rejection["body"] and "engineer" in rejection["body"]
    assert denied[0] == 1


def test_engineer_editing_delegation_allowed(tmp_path: Path) -> None:
    """The same editing description delegated to the correct role must go through."""
    db_path = _run_delegation(tmp_path, role="engineer", description=_REAL_DESCRIPTION)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        created = conn.execute(
            "SELECT id, role FROM issues WHERE title = 'Fix imports in CreateTestSceneEditor.cs'"
        ).fetchone()

    assert created is not None
    assert created["role"] == "engineer"


def test_file_scout_pure_read_task_still_allowed(tmp_path: Path) -> None:
    """A legitimate read-only scout task (no editing signal) must not be blocked."""
    db_path = _run_delegation(
        tmp_path, role="file_scout",
        description="Inspect Assets/Editor/CreateTestSceneEditor.cs and report which "
                     "using statements are missing so the Lead can decide next steps.",
    )

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        created = conn.execute(
            "SELECT id, role FROM issues WHERE title = 'Fix imports in CreateTestSceneEditor.cs'"
        ).fetchone()

    assert created is not None
    assert created["role"] == "file_scout"
