from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.db.agent_reports import latest_agent_report, record_agent_report
from aiteam.db.migration import SCHEMA_PATH


def _init(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:reviewer', 'reviewer', 'R')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'L')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('i1', 'g1', 'Review', 'in_progress', 'reviewer', 'role:reviewer')"
        )
        conn.commit()


def test_assignee_report_is_trusted(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _init(db)

    record_agent_report(
        db, issue_id="i1", agent_id="role:reviewer", run_id=None, agent_role="reviewer",
        parsed={"role": "reviewer", "result": "approved", "evidence": "a.cs:1"},
    )

    report = latest_agent_report(db, issue_id="i1")
    assert report is not None
    assert report["result"] == "approved"
    assert report["evidence"] == "a.cs:1"


def test_non_assignee_report_is_not_trusted(tmp_path: Path) -> None:
    """A report written by someone other than the issue's assignee (e.g. the
    Lead re-narrating on the child's thread) must never drive gates."""
    db = tmp_path / "t.db"
    _init(db)

    record_agent_report(
        db, issue_id="i1", agent_id="role:lead", run_id=None, agent_role="lead",
        parsed={"role": "reviewer", "result": "approved"},
    )

    assert latest_agent_report(db, issue_id="i1") is None


def test_role_spoofing_invalidates_report(tmp_path: Path) -> None:
    """An agent cannot speak as another role: claimed role must match."""
    db = tmp_path / "t.db"
    _init(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:engineer', 'engineer', 'E')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('i2', 'g1', 'Build', 'in_progress', 'engineer', 'role:engineer')"
        )
        conn.commit()

    row = record_agent_report(
        db, issue_id="i2", agent_id="role:engineer", run_id=None, agent_role="engineer",
        parsed={"role": "reviewer", "result": "approved"},  # spoofed role claim
    )

    assert row["valid"] == 0
    assert latest_agent_report(db, issue_id="i2") is None


def test_unknown_result_invalidates_report(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _init(db)

    row = record_agent_report(
        db, issue_id="i1", agent_id="role:reviewer", run_id=None, agent_role="reviewer",
        parsed={"role": "reviewer", "result": "maybe-fine-ish"},
    )

    assert row["valid"] == 0
    assert latest_agent_report(db, issue_id="i1") is None


def test_latest_trusted_report_wins(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _init(db)
    record_agent_report(
        db, issue_id="i1", agent_id="role:reviewer", run_id=None, agent_role="reviewer",
        parsed={"role": "reviewer", "result": "changes_requested", "blocker": "stubs"},
    )
    record_agent_report(
        db, issue_id="i1", agent_id="role:reviewer", run_id=None, agent_role="reviewer",
        parsed={"role": "reviewer", "result": "approved"},
    )

    report = latest_agent_report(db, issue_id="i1")
    assert report is not None and report["result"] == "approved"


def test_missing_table_returns_none(tmp_path: Path) -> None:
    """Pre-migration DBs (no agent_reports table) fall back gracefully."""
    db = tmp_path / "legacy.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("CREATE TABLE issues (id TEXT PRIMARY KEY)")
        conn.commit()

    assert latest_agent_report(db, issue_id="i1") is None


def test_report_in_add_comment_is_recorded(tmp_path: Path) -> None:
    """Codex-style adapters carry the AGENT-REPORT in add_comment (not in
    result.output) — the executor must capture it from that path too."""
    import sqlite3 as _sqlite3
    from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
    from aiteam.db.wakeups import enqueue_wakeup
    from aiteam.heartbeat.executor import RunExecutor
    from aiteam.heartbeat.scheduler import HeartbeatScheduler
    from typing import Any

    db = tmp_path / "aiteam.db"
    _init(db)
    with _sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE agents SET adapter_type = 'subscription_cli' WHERE id = 'role:reviewer'")
        conn.commit()

    class _ReviewerRuntime:
        descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

        def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
            return {"AITEAM_RUN_ID": run_id}

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            return ExecutionResult(
                status="completed",
                output="Revisión completada.",  # short summary — no marker here
                actions={
                    "add_comments": [
                        "Findings...\n---AGENT-REPORT---\nrole: reviewer\nresult: changes_requested\nblocker: escena vacía\n"
                    ]
                },
            )

    executor = RunExecutor(db, AdapterRegistry([_ReviewerRuntime()]))
    enqueue_wakeup(
        db, agent_id="role:reviewer", source="manual", reason="manual",
        payload={"issue_id": "i1", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id="role:reviewer")
    assert dispatch is not None
    executor.execute(dispatch)

    report = latest_agent_report(db, issue_id="i1")
    assert report is not None
    assert report["result"] == "changes_requested"
    assert report["blocker"] == "escena vacía"


def test_report_survives_lead_directive_being_last_comment(tmp_path: Path) -> None:
    """Integration: the wake payload's child report must come from the
    validated record, not from whatever comment is last on the thread.
    (Observed bug: a Lead directive as last comment erased the report.)"""
    from aiteam.db.comments import create_comment
    from aiteam.db.wake_payload import build_wake_payload

    db = tmp_path / "t.db"
    _init(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('parent', 'g1', 'Parent', 'in_progress', 'lead', 'role:lead')"
        )
        conn.execute("UPDATE issues SET parent_id = 'parent' WHERE id = 'i1'")
        conn.commit()

    # Reviewer reports (persisted as validated record + comment)…
    record_agent_report(
        db, issue_id="i1", agent_id="role:reviewer", run_id=None, agent_role="reviewer",
        parsed={"role": "reviewer", "result": "changes_requested", "blocker": "empty scene"},
    )
    create_comment(db, issue_id="i1", author_agent_id="role:reviewer",
                   body="Findings...\n---AGENT-REPORT---\nrole: reviewer\nresult: changes_requested\n")
    # …then the Lead posts a directive, becoming the LAST comment.
    create_comment(db, issue_id="i1", author_agent_id="role:lead",
                   body="Reintenta la revisión con el workspace actual.")

    payload = build_wake_payload(db, issue_id="parent")
    child = next(c for c in payload["children"] if c["id"] == "i1")

    assert child["last_agent_report"] is not None
    assert child["last_agent_report"]["result"] == "changes_requested"
