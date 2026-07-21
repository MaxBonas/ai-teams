from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.agent_reports import record_agent_report
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.mcp_needs import reconcile_mcp_needs


def _init(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal', 'Goal')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('lead', 'lead', 'Lead')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('eng', 'engineer', 'Engineer')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('root', 'goal', 'Root', 'in_progress', 'lead', 'lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) "
            "VALUES ('child', 'goal', 'root', 'Child', 'blocked', 'engineer', 'eng')"
        )
        conn.commit()


def _report(db_path: Path, run_id: str, blocker: str, *, evidence: str = "") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES (?, 'eng', 'child', 'failed')",
            (run_id,),
        )
        conn.commit()
    record_agent_report(
        db_path,
        issue_id="child",
        agent_id="eng",
        run_id=run_id,
        agent_role="engineer",
        parsed={
            "role": "engineer",
            "result": "blocked",
            "issue_status": "blocked",
            "next_owner": "lead",
            "blocker": blocker,
            "evidence": evidence,
        },
    )


def test_explicit_capability_gap_wakes_lead_once_with_evidence(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    _report(db_path, "run-1", "capability_gap — browser automation unavailable")

    assert reconcile_mcp_needs(db_path) == ["root"]
    assert reconcile_mcp_needs(db_path) == []

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wakes = conn.execute(
            "SELECT reason, payload_json FROM wakeup_requests WHERE agent_id='lead'"
        ).fetchall()
        events = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='extension.need_suggested'"
        ).fetchone()[0]
    assert len(wakes) == 1
    payload = json.loads(wakes[0]["payload_json"])
    assert wakes[0]["reason"] == "mcp_need_suggested"
    assert payload["suggestion_only"] is True
    assert payload["evidence"][0]["run_id"] == "run-1"
    assert events == 1


def test_lower_confidence_capability_blocker_requires_distinct_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    _report(db_path, "run-1", "hardware unavailable for device validation")
    assert reconcile_mcp_needs(db_path) == []

    _report(db_path, "run-2", "hardware unavailable for device validation")
    assert reconcile_mcp_needs(db_path) == ["root"]


def test_unrelated_low_confidence_gaps_do_not_combine_into_threshold(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    _report(db_path, "run-1", "hardware unavailable for device validation")
    _report(db_path, "run-2", "no tool for publishing documentation")

    assert reconcile_mcp_needs(db_path) == []


def test_single_blocked_unverifiable_item_is_actionable_but_ordinary_bug_is_not(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    _report(db_path, "run-1", "cannot verify the external deployment result")
    assert reconcile_mcp_needs(db_path) == ["root"]

    other = tmp_path / "other.db"
    _init(other)
    _report(other, "run-bug", "unit test assertion failed at line 42")
    assert reconcile_mcp_needs(other) == []


def test_detector_preserves_existing_lead_wakeup_and_uses_durable_comment(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    enqueue_wakeup(
        db_path,
        agent_id="lead",
        source="existing",
        reason="child_report",
        payload={"issue_id": "root", "important": "do-not-overwrite"},
        idempotency_key="existing-root-wake",
    )
    _report(db_path, "run-1", "capability_gap — browser automation unavailable")

    assert reconcile_mcp_needs(db_path) == ["root"]

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        wake = conn.execute(
            "SELECT reason, payload_json FROM wakeup_requests WHERE agent_id='lead'"
        ).fetchone()
        comment = conn.execute(
            "SELECT body FROM issue_comments WHERE issue_id='root' AND author_user_id='system'"
        ).fetchone()
    assert wake["reason"] == "child_report"
    assert json.loads(wake["payload_json"])["important"] == "do-not-overwrite"
    assert "sugerencia de investigación" in comment["body"]
