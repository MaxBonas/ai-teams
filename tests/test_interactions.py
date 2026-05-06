from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.interactions import (
    ConflictError,
    create_interaction,
    get_interaction,
    list_interactions,
    resolve_interaction,
)
from aiteam.db.migration import SCHEMA_PATH


def _init_db(db_path: Path, *, with_assignee: bool = False) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name) VALUES (?, ?, ?)",
            ("agent-1", "engineer", "Engineer"),
        )
        assignee = "agent-1" if with_assignee else None
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Fix bug", "in_progress", assignee),
        )
        conn.commit()


# ── create ────────────────────────────────────────────────────────────────────

def test_create_request_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    row = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"message": "Deploy to prod?"},
        title="Confirm deploy",
    )

    assert row["issue_id"] == "issue-1"
    assert row["kind"] == "request_confirmation"
    assert row["status"] == "pending"
    assert row["continuation_policy"] == "wake_assignee"
    assert json.loads(row["payload_json"])["message"] == "Deploy to prod?"
    assert row["title"] == "Confirm deploy"


def test_create_ask_user_questions(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    row = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="ask_user_questions",
        payload={"questions": [{"id": "q1", "text": "Which branch?"}]},
    )

    assert row["kind"] == "ask_user_questions"
    assert row["status"] == "pending"


def test_create_rejects_unknown_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    with pytest.raises(ValueError, match="unknown interaction kind"):
        create_interaction(db_path, issue_id="issue-1", kind="bad_kind", payload={})


def test_create_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"message": "ok?"},
        idempotency_key="deploy-gate-1",
    )
    second = create_interaction(
        db_path,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"message": "ok?"},
        idempotency_key="deploy-gate-1",
    )

    assert first["id"] == second["id"]
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM issue_thread_interactions").fetchone()[0]
    assert count == 1


# ── list / get ────────────────────────────────────────────────────────────────

def test_list_interactions_ordered_by_created_at(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    create_interaction(db_path, issue_id="issue-1", kind="request_confirmation", payload={"n": 1})
    create_interaction(db_path, issue_id="issue-1", kind="ask_user_questions", payload={"n": 2})

    rows = list_interactions(db_path, issue_id="issue-1")
    assert len(rows) == 2
    assert rows[0]["kind"] == "request_confirmation"
    assert rows[1]["kind"] == "ask_user_questions"


def test_get_interaction_not_found(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    assert get_interaction(db_path, interaction_id="missing") is None


# ── resolve: request_confirmation ─────────────────────────────────────────────

def test_accept_request_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )

    updated = resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    assert updated["status"] == "accepted"
    assert updated["resolved_at"] is not None
    assert json.loads(updated["result_json"])["outcome"] == "accept"


def test_reject_request_confirmation(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )

    updated = resolve_interaction(
        db_path,
        interaction_id=interaction["id"],
        action="reject",
        result={"version": 1, "outcome": "rejected", "reason": "not ready"},
        resolved_by_user_id="user-123",
    )

    assert updated["status"] == "rejected"
    assert updated["resolved_by_user_id"] == "user-123"


def test_resolve_ask_user_questions_with_answer(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="ask_user_questions",
        payload={"questions": [{"id": "q1", "text": "Branch?"}]},
    )

    updated = resolve_interaction(
        db_path,
        interaction_id=interaction["id"],
        action="answer",
        result={"version": 1, "answers": [{"questionId": "q1", "value": "main"}]},
    )

    assert updated["status"] == "answered"


def test_resolve_any_kind_with_cancel(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="ask_user_questions", payload={}
    )

    updated = resolve_interaction(db_path, interaction_id=interaction["id"], action="cancel")
    assert updated["status"] == "cancelled"


def test_resolve_invalid_action_for_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="ask_user_questions", payload={}
    )

    with pytest.raises(ValueError, match="not valid for kind"):
        resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")


def test_resolve_already_resolved_raises_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )
    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    with pytest.raises(ConflictError):
        resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")


def test_resolve_not_found_raises_lookup_error(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    with pytest.raises(LookupError):
        resolve_interaction(db_path, interaction_id="ghost", action="accept")


# ── continuation: wake_assignee ───────────────────────────────────────────────

def test_accept_enqueues_wakeup_for_assignee(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation",
        payload={}, continuation_policy="wake_assignee",
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE agent_id = ?",
            ("agent-1",),
        ).fetchone()

    assert wakeup is not None
    assert wakeup["reason"] == "interaction_resolved"
    assert wakeup["source"] == "interaction"
    payload = json.loads(wakeup["payload_json"])
    assert payload["interaction_id"] == interaction["id"]
    assert payload["action"] == "accept"


def test_cancel_does_not_enqueue_wakeup(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="cancel")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 0


def test_accept_without_assignee_no_wakeup(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=False)  # no assignee
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 0


def test_wakeup_idempotent_on_double_resolution(tmp_path: Path) -> None:
    """Second resolve call raises ConflictError before enqueueing a second wakeup."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation", payload={}
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")
    with pytest.raises(ConflictError):
        resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 1


# ── API integration ───────────────────────────────────────────────────────────

def test_interaction_api_create_and_resolve(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    import api.utils as utils
    from api.main import app

    utils.set_current_workspace(tmp_path)
    db_path = tmp_path / "runtime" / "aiteam.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "G"))
        conn.execute("INSERT INTO agents (id, role, name) VALUES (?, ?, ?)", ("agent-1", "lead", "Lead"))
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Impl", "in_progress", "agent-1"),
        )
        conn.commit()

    client = TestClient(app, raise_server_exceptions=True)

    # Create
    resp = client.post("/api/issues/issue-1/interactions", json={
        "kind": "request_confirmation",
        "payload": {"message": "Ship it?"},
        "title": "Confirm ship",
    })
    assert resp.status_code == 200
    interaction_id = resp.json()["interaction"]["id"]

    # List
    resp = client.get("/api/issues/issue-1/interactions")
    assert resp.status_code == 200
    assert len(resp.json()["interactions"]) == 1

    # Get
    resp = client.get(f"/api/interactions/{interaction_id}")
    assert resp.status_code == 200
    assert resp.json()["interaction"]["status"] == "pending"

    # Resolve
    resp = client.patch(f"/api/interactions/{interaction_id}", json={"action": "accept"})
    assert resp.status_code == 200
    assert resp.json()["interaction"]["status"] == "accepted"

    resp = client.get("/api/timeline?issue_id=issue-1")
    assert resp.status_code == 200
    actions = [item["title"] for item in resp.json()["items"] if item["type"] == "activity"]
    assert "interaction.created" in actions
    assert "interaction.accept" in actions

    # Double resolve → 409
    resp = client.patch(f"/api/interactions/{interaction_id}", json={"action": "accept"})
    assert resp.status_code == 409


# ── continuation: wake_assignee_on_accept ─────────────────────────────────────

def test_wake_assignee_on_accept_fires_wakeup_on_accept(tmp_path: Path) -> None:
    """wake_assignee_on_accept: accept → wakeup enqueued."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation",
        payload={}, continuation_policy="wake_assignee_on_accept",
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="accept")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests WHERE agent_id = ?", ("agent-1",)).fetchone()[0]
    assert count == 1


def test_wake_assignee_on_accept_no_wakeup_on_reject(tmp_path: Path) -> None:
    """wake_assignee_on_accept: reject → NO wakeup enqueued."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation",
        payload={}, continuation_policy="wake_assignee_on_accept",
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="reject")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 0


def test_wake_assignee_on_accept_no_wakeup_on_cancel(tmp_path: Path) -> None:
    """wake_assignee_on_accept: cancel → NO wakeup enqueued."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation",
        payload={}, continuation_policy="wake_assignee_on_accept",
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="cancel")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 0


def test_wake_assignee_default_still_fires_on_reject(tmp_path: Path) -> None:
    """wake_assignee (default): reject → wakeup still fired (existing behaviour)."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, with_assignee=True)
    interaction = create_interaction(
        db_path, issue_id="issue-1", kind="request_confirmation",
        payload={}, continuation_policy="wake_assignee",
    )

    resolve_interaction(db_path, interaction_id=interaction["id"], action="reject")

    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests WHERE agent_id = ?", ("agent-1",)).fetchone()[0]
    assert count == 1
