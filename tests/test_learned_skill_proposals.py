"""Recorrido runtime de propuestas de skills aprendidas y su RBAC."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.extensions import list_project_skills, project_skills_for_role
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


class _SkillProposalRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="neutral-test")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict, env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="propuesta",
            actions={"skill_proposals": [{
                "name": "local-test-command",
                "body": "Usa scripts/pytest_local.bat.",
                "applies_to_roles": ["engineer"],
                "evidence": ["run anterior falló con pytest global", "launcher local pasó"],
            }]},
        )


def _run(tmp_path: Path, *, role: str) -> Path:
    db = tmp_path / "aiteam.db"
    agent_id = f"role:{role}"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, 'openai_api')",
            (agent_id, role, role),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('issue:intake', 'g1', 'Build', 'in_progress', ?, ?)",
            (role, agent_id),
        )
        conn.commit()
    enqueue_wakeup(
        db, agent_id=agent_id, source="test", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id=agent_id)
    RunExecutor(db, AdapterRegistry([_SkillProposalRuntime()])).execute(dispatch)
    return db


def test_lead_runtime_persists_inert_evidence_backed_proposal(tmp_path: Path) -> None:
    db = _run(tmp_path, role="lead")

    skills = list_project_skills(db.parent)
    assert len(skills) == 1
    assert skills[0]["origin"] == "learned"
    assert skills[0]["status"] == "proposed"
    assert len(skills[0]["evidence"]) == 2
    assert project_skills_for_role(db.parent, "engineer") == []
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='skill.proposed'"
        ).fetchone()[0] == 1


def test_worker_runtime_cannot_persist_learned_proposal(tmp_path: Path) -> None:
    db = _run(tmp_path, role="engineer")

    assert list_project_skills(db.parent) == []
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='role.op_denied'"
        ).fetchone()[0] >= 1
