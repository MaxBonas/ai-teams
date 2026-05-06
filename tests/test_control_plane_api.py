from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.control_plane import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.runs import append_run_event, create_run


def _client_for_workspace(workspace: Path) -> tuple[TestClient, Path]:
    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    return TestClient(app), previous


def _restore_workspace(previous: Path) -> None:
    set_current_workspace(previous)


def _init_db(workspace: Path) -> Path:
    runtime_dir = workspace / ".aiteam"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.executemany(
            "INSERT INTO agents (id, role, name) VALUES (?, ?, ?)",
            [
                ("role:team_lead", "team_lead", "Team Lead"),
                ("role:engineer", "engineer", "Engineer"),
                ("role:reviewer", "reviewer", "Reviewer"),
            ],
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status)
            VALUES (?, ?, ?, ?)
            """,
            ("issue-1", "goal-1", "Implement", "todo"),
        )
        conn.executemany(
            """
            INSERT INTO runs (id, agent_id, issue_id, invocation_source, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("run-eng", "role:engineer", "issue-1", "manual", "queued"),
                ("run-review", "role:reviewer", "issue-1", "manual", "queued"),
            ],
        )
        conn.commit()
    return db_path


def test_checkout_endpoint_returns_issue_on_success_and_409_on_conflict(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    client, previous = _client_for_workspace(workspace)
    try:
        ok = client.post(
            "/api/issues/issue-1/checkout",
            json={
                "agent_id": "role:engineer",
                "run_id": "run-eng",
                "expected_statuses": ["todo"],
                "locked_at": "2026-05-04T12:00:00+00:00",
            },
        )
        conflict = client.post(
            "/api/issues/issue-1/checkout",
            json={
                "agent_id": "role:reviewer",
                "run_id": "run-review",
                "expected_statuses": ["todo"],
            },
        )
    finally:
        _restore_workspace(previous)

    assert ok.status_code == 200
    assert ok.json()["issue"]["checkout_run_id"] == "run-eng"
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Issue checkout conflict"
    with sqlite3.connect(str(workspace / ".aiteam" / "aiteam.db")) as conn:
        activity = conn.execute(
            "SELECT action FROM activity_log WHERE target_type = ? AND target_id = ?",
            ("issue", "issue-1"),
        ).fetchall()
    assert ("issue.checkout",) in activity


def test_checkout_endpoint_returns_503_when_v2_schema_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".aiteam").mkdir(parents=True)
    client, previous = _client_for_workspace(workspace)
    try:
        response = client.post(
            "/api/issues/issue-1/checkout",
            json={"agent_id": "role:engineer", "run_id": "run-eng"},
        )
    finally:
        _restore_workspace(previous)

    assert response.status_code == 503
    assert response.json()["detail"] == "Control-plane v2 schema is not available"


def test_run_endpoint_returns_run_with_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    create_run(
        db_path,
        run_id="run-extra",
        agent_id="role:engineer",
        issue_id="issue-1",
        context_snapshot={"wake_reason": "manual"},
    )
    append_run_event(
        db_path,
        run_id="run-extra",
        event_type="stdout",
        stream="stdout",
        payload={"text": "hello"},
    )
    client, previous = _client_for_workspace(workspace)
    try:
        response = client.get("/api/runs/run-extra")
    finally:
        _restore_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["id"] == "run-extra"
    assert payload["run"]["context_snapshot"] == {"wake_reason": "manual"}
    assert payload["events"][0]["payload"] == {"text": "hello"}


def test_wakeup_endpoints_enqueue_claim_and_finish(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _init_db(workspace)
    client, previous = _client_for_workspace(workspace)
    try:
        first = client.post(
            "/api/wakeup-requests",
            json={
                "agent_id": "role:team_lead",
                "source": "timer",
                "reason": "timer",
                "payload": {"n": 1},
                "idempotency_key": "timer:lead",
            },
        )
        second = client.post(
            "/api/wakeup-requests",
            json={
                "agent_id": "role:team_lead",
                "source": "timer",
                "reason": "timer",
                "payload": {"n": 2},
                "idempotency_key": "timer:lead",
            },
        )
        claim = client.post(
            "/api/wakeup-requests/claim",
            json={"agent_id": "role:team_lead", "claimed_at": "2026-05-04T12:00:00+00:00"},
        )
        wakeup_id = claim.json()["wakeup_request"]["id"]
        finish = client.patch(
            f"/api/wakeup-requests/{wakeup_id}",
            json={
                "status": "finished",
                "run_id": "run-eng",
                "finished_at": "2026-05-04T12:01:00+00:00",
            },
        )
    finally:
        _restore_workspace(previous)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["wakeup_request"]["id"] == second.json()["wakeup_request"]["id"]
    assert second.json()["wakeup_request"]["coalesced_count"] == 1
    assert second.json()["wakeup_request"]["payload"] == {"n": 2}
    assert claim.status_code == 200
    assert claim.json()["wakeup_request"]["status"] == "claimed"
    assert finish.status_code == 200
    assert finish.json()["wakeup_request"]["status"] == "finished"
    assert finish.json()["wakeup_request"]["run_id"] == "run-eng"
    with sqlite3.connect(str(workspace / ".aiteam" / "aiteam.db")) as conn:
        actions = [
            row[0]
            for row in conn.execute(
                "SELECT action FROM activity_log WHERE target_type = ? ORDER BY created_at ASC, rowid ASC",
                ("wakeup",),
            ).fetchall()
        ]
    assert "wakeup.enqueued" in actions
    assert "wakeup.claimed" in actions
    assert "wakeup.finished" in actions


def test_run_once_endpoint_dispatches_against_request_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    client, previous = _client_for_workspace(workspace)
    try:
        enqueue = client.post(
            "/api/wakeup-requests",
            json={
                "agent_id": "role:team_lead",
                "source": "manual",
                "reason": "manual",
                "payload": {"issue_id": "issue-1"},
            },
        )
        run_once = client.post(
            "/api/control-plane/run-once",
            json={"agent_id": "role:team_lead", "max_runs": 5},
        )
    finally:
        _restore_workspace(previous)

    assert enqueue.status_code == 200
    assert run_once.status_code == 200
    assert run_once.json()["dispatched_count"] == 1
    created_run_id = run_once.json()["dispatched"][0]["run"]["id"]
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (created_run_id,)).fetchone()
        wakeup = conn.execute(
            "SELECT * FROM wakeup_requests WHERE id = ?",
            (enqueue.json()["wakeup_request"]["id"],),
        ).fetchone()
        comment = conn.execute(
            "SELECT * FROM issue_comments WHERE issue_id = ? ORDER BY created_at DESC",
            ("issue-1",),
        ).fetchone()

    assert run["agent_id"] == "role:team_lead"
    assert run["status"] == "completed"
    assert wakeup["status"] == "finished"
    assert comment["author_agent_id"] == "role:team_lead"
    assert "Propuesta inicial del Lead" in comment["body"]
    with sqlite3.connect(str(db_path)) as conn:
        activity = conn.execute(
            "SELECT action FROM activity_log WHERE action = ?",
            ("control_plane.run_once",),
        ).fetchone()
    assert activity is not None


def test_run_once_reconciles_assigned_issue_without_live_wakeup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM runs")
        conn.execute(
            "UPDATE agents SET role = ?, adapter_type = ? WHERE id = ?",
            ("engineer", "role_builtin", "role:engineer"),
        )
        conn.execute(
            "UPDATE issues SET assignee_agent_id = ?, status = ? WHERE id = ?",
            ("role:engineer", "todo", "issue-1"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue-parent", "goal-1", "Parent", "in_progress", "team_lead", "role:team_lead"),
        )
        conn.execute("UPDATE issues SET parent_id = ? WHERE id = ?", ("issue-parent", "issue-1"))
        conn.commit()
    client, previous = _client_for_workspace(workspace)
    try:
        run_once = client.post("/api/control-plane/run-once", json={"max_runs": 5})
    finally:
        _restore_workspace(previous)

    assert run_once.status_code == 200
    payload = run_once.json()
    assert payload["enqueued_issue_ids"] == ["issue-1"]
    assert payload["dispatched_count"] == 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        comment = conn.execute(
            "SELECT * FROM issue_comments WHERE issue_id = ?",
            ("issue-1",),
        ).fetchone()
    assert "Engineer intake" in comment["body"]


def test_run_once_reconciles_leaf_in_progress_issue_but_not_parent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM runs")
        conn.execute(
            "UPDATE agents SET role = ?, adapter_type = ? WHERE id = ?",
            ("engineer", "role_builtin", "role:engineer"),
        )
        conn.execute(
            "UPDATE issues SET assignee_agent_id = ?, status = ? WHERE id = ?",
            ("role:engineer", "in_progress", "issue-1"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, parent_id, goal_id, title, status, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue-child", "issue-1", "goal-1", "Child", "in_progress", "role:engineer"),
        )
        conn.commit()
    client, previous = _client_for_workspace(workspace)
    try:
        run_once = client.post("/api/control-plane/run-once", json={"max_runs": 5})
    finally:
        _restore_workspace(previous)

    assert run_once.status_code == 200
    payload = run_once.json()
    assert payload["enqueued_issue_ids"] == ["issue-child"]
    assert payload["dispatched_count"] == 1


def test_run_once_does_not_drain_wakeups_created_during_same_turn(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM runs")
        conn.execute(
            "UPDATE agents SET role = ?, adapter_type = ?, supervisor_agent_id = ? WHERE id = ?",
            ("engineer", "role_builtin", "role:team_lead", "role:engineer"),
        )
        conn.execute(
            "UPDATE issues SET assignee_agent_id = ?, status = ? WHERE id = ?",
            ("role:engineer", "todo", "issue-1"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue-parent", "goal-1", "Parent", "in_progress", "team_lead", "role:team_lead"),
        )
        conn.execute("UPDATE issues SET parent_id = ? WHERE id = ?", ("issue-parent", "issue-1"))
        conn.commit()
    client, previous = _client_for_workspace(workspace)
    try:
        run_once = client.post("/api/control-plane/run-once", json={"max_runs": 5})
    finally:
        _restore_workspace(previous)

    assert run_once.status_code == 200
    payload = run_once.json()
    assert payload["dispatched_count"] == 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        supervisor = conn.execute(
            "SELECT * FROM wakeup_requests WHERE reason = 'child_report'"
        ).fetchone()
    assert supervisor is not None
    assert supervisor["status"] == "queued"


def test_run_once_can_opt_into_legacy_full_drain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = _init_db(workspace)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DELETE FROM runs")
        conn.execute(
            "UPDATE agents SET role = ?, adapter_type = ?, supervisor_agent_id = ? WHERE id = ?",
            ("engineer", "role_builtin", "role:team_lead", "role:engineer"),
        )
        conn.execute(
            "UPDATE issues SET assignee_agent_id = ?, status = ? WHERE id = ?",
            ("role:engineer", "todo", "issue-1"),
        )
        conn.execute(
            """
            INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("issue-parent", "goal-1", "Parent", "in_progress", "team_lead", "role:team_lead"),
        )
        conn.execute("UPDATE issues SET parent_id = ? WHERE id = ?", ("issue-parent", "issue-1"))
        conn.commit()
    client, previous = _client_for_workspace(workspace)
    try:
        run_once = client.post(
            "/api/control-plane/run-once",
            json={"max_runs": 5, "include_new_wakeups": True},
        )
    finally:
        _restore_workspace(previous)

    assert run_once.status_code == 200
    assert run_once.json()["dispatched_count"] == 2
