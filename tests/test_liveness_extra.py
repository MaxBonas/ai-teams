"""Reconciler de padres inactivos con todos los hijos terminales."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.db.liveness import reconcile_idle_parents
from aiteam.db.migration import SCHEMA_PATH


def _db(tmp_path: Path, *, parent_updated: str = "datetime('now', '-5 minutes')") -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'L')")
        conn.execute(
            f"INSERT INTO issues (id, goal_id, title, status, assignee_agent_id, updated_at) "
            f"VALUES ('root', 'g1', 'Root', 'in_progress', 'role:lead', {parent_updated})"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status) VALUES "
            "('child', 'g1', 'root', 'C', 'done')"
        )
        conn.commit()
    return db


def test_wakes_parent_with_all_children_terminal_and_no_wakeups(tmp_path: Path) -> None:
    db = _db(tmp_path)

    woken = reconcile_idle_parents(db)

    assert woken == ["root"]
    with sqlite3.connect(str(db)) as conn:
        wake = conn.execute(
            "SELECT reason, status FROM wakeup_requests WHERE agent_id='role:lead'"
        ).fetchone()
    assert wake == ("children_terminal", "queued")

    # Segunda pasada: el wakeup en cola ya cubre al padre → no-op, sin spam.
    assert reconcile_idle_parents(db) == []
    with sqlite3.connect(str(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM wakeup_requests WHERE agent_id='role:lead'").fetchone()[0]
    assert n == 1


def test_skips_parent_with_open_child_or_pending_interaction(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status) VALUES "
            "('child2', 'g1', 'root', 'C2', 'in_progress')"
        )
        conn.commit()
    assert reconcile_idle_parents(db) == []

    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE issues SET status='done' WHERE id='child2'")
        conn.execute(
            "INSERT INTO issue_thread_interactions (id, issue_id, kind, status, payload_json) "
            "VALUES ('x1', 'root', 'request_confirmation', 'pending', '{}')"
        )
        conn.commit()
    assert reconcile_idle_parents(db) == [], "una decisión humana pendiente ES motivo legítimo para esperar"


def test_respects_recent_activity_margin(tmp_path: Path) -> None:
    db = _db(tmp_path, parent_updated="datetime('now')")

    assert reconcile_idle_parents(db) == [], "60s de margen: el flujo normal aún puede despertar al padre"
