from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload, _parse_agent_report


def _init(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a1', 'engineer', 'E', 'standard')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, description, status, role, complexity, assignee_agent_id) "
            "VALUES ('i1', 'g1', 'Test Issue', 'Do the thing', 'in_progress', 'engineer', 'medium', 'a1')"
        )
    return db


def test_basic_payload(tmp_path):
    db = _init(tmp_path)
    payload = build_wake_payload(db, issue_id="i1")
    assert payload["issue_id"] == "i1"
    assert payload["issue"]["title"] == "Test Issue"
    assert payload["issue"]["status"] == "in_progress"
    assert payload["comments"] == []
    assert payload["comments_total"] == 0
    assert payload["fallback_fetch_needed"] is False
    assert payload["pending_interactions"] == []
    assert payload["plan_document"] is None


def test_payload_not_found(tmp_path):
    db = _init(tmp_path)
    payload = build_wake_payload(db, issue_id="missing")
    assert payload["issue_id"] == "missing"
    assert payload.get("error") == "issue_not_found"
    assert payload["fallback_fetch_needed"] is True


def test_payload_includes_comments(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, body, author_agent_id) VALUES ('c1', 'i1', 'Hello', 'a1')"
        )
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, body, author_user_id) VALUES ('c2', 'i1', 'World', 'user')"
        )
    payload = build_wake_payload(db, issue_id="i1")
    assert len(payload["comments"]) == 2
    assert payload["comments"][0]["body"] == "Hello"
    assert payload["comments"][1]["body"] == "World"
    assert payload["comments_total"] == 2


def test_payload_trigger_comment_highlighted(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, body, author_user_id) VALUES ('c1', 'i1', 'Reply here', 'user')"
        )
    payload = build_wake_payload(db, issue_id="i1", comment_id="c1")
    assert payload["trigger_comment_id"] == "c1"
    assert payload["comments"][0]["is_trigger"] is True


def test_payload_fallback_fetch_needed_when_many_comments(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        for i in range(15):
            conn.execute(
                f"INSERT INTO issue_comments (id, issue_id, body) VALUES ('c{i}', 'i1', 'msg {i}')"
            )
    payload = build_wake_payload(db, issue_id="i1", max_comments=10)
    assert payload["comments_total"] == 15
    assert len(payload["comments"]) == 10
    assert payload["fallback_fetch_needed"] is True


def test_payload_includes_pending_interactions(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_thread_interactions (id, issue_id, kind, status, payload_json, continuation_policy, title) "
            "VALUES ('int1', 'i1', 'request_confirmation', 'pending', '{}', 'wake_assignee', 'Approve me')"
        )
    payload = build_wake_payload(db, issue_id="i1")
    assert len(payload["pending_interactions"]) == 1
    assert payload["pending_interactions"][0]["title"] == "Approve me"


def test_payload_includes_plan_document(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_documents (id, issue_id, key, title, body, format, current_revision_id, revision_number) "
            "VALUES ('doc1', 'i1', 'plan', 'Plan', 'Step 1\nStep 2', 'markdown', 'rev1', 1)"
        )
    payload = build_wake_payload(db, issue_id="i1")
    assert payload["plan_document"] is not None
    assert payload["plan_document"]["title"] == "Plan"
    assert "Step 1" in payload["plan_document"]["body"]


def test_payload_truncates_long_plan(tmp_path):
    db = _init(tmp_path)
    long_body = "x" * 3000
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_documents (id, issue_id, key, title, body, format, current_revision_id, revision_number) "
            "VALUES ('doc1', 'i1', 'plan', 'Plan', ?, 'markdown', 'rev1', 1)",
            (long_body,),
        )
    payload = build_wake_payload(db, issue_id="i1")
    assert payload["plan_document"]["truncated"] is True
    assert len(payload["plan_document"]["body"]) <= 2010


def test_payload_includes_parent_summary(tmp_path):
    db = _init(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('parent', 'g1', 'Parent Issue', 'in_progress')"
        )
        conn.execute("UPDATE issues SET parent_id = 'parent' WHERE id = 'i1'")
    payload = build_wake_payload(db, issue_id="i1")
    assert payload["parent"] is not None
    assert payload["parent"]["title"] == "Parent Issue"


def test_payload_is_json_serializable(tmp_path):
    db = _init(tmp_path)
    payload = build_wake_payload(db, issue_id="i1", run_id="run-123")
    serialized = json.dumps(payload)
    back = json.loads(serialized)
    assert back["run_id"] == "run-123"
    assert back["issue"]["title"] == "Test Issue"


# ── _parse_agent_report ───────────────────────────────────────────────────────

class TestParseAgentReport:
    def test_returns_none_when_no_marker(self):
        assert _parse_agent_report("Just some prose comment.") is None

    def test_parses_full_block(self):
        body = (
            "Some prose.\n\n"
            "---AGENT-REPORT---\n"
            "role: qa\n"
            "result: blocked\n"
            "issue_status: blocked\n"
            "next_owner: engineer\n"
            "tech_match: no\n"
            "blocker: engineer used Python instead of HTML/JS\n"
            "evidence: leaderboard.py:1\n"
        )
        report = _parse_agent_report(body)
        assert report is not None
        assert report["role"] == "qa"
        assert report["result"] == "blocked"
        assert report["issue_status"] == "blocked"
        assert report["next_owner"] == "engineer"
        assert report["tech_match"] == "no"
        assert "Python" in report["blocker"]
        assert report["evidence"] == "leaderboard.py:1"

    def test_stops_at_next_section_marker(self):
        body = (
            "---AGENT-REPORT---\n"
            "role: engineer\n"
            "result: done\n"
            "---\n"
            "role: should_not_appear\n"
        )
        report = _parse_agent_report(body)
        assert report["role"] == "engineer"
        assert report["result"] == "done"
        assert "should_not_appear" not in report.values()

    def test_returns_none_for_empty_block(self):
        body = "---AGENT-REPORT---\n"
        assert _parse_agent_report(body) is None

    def test_handles_extra_whitespace(self):
        body = "---AGENT-REPORT---\n  role :  reviewer  \n  result :  approved  \n"
        report = _parse_agent_report(body)
        assert report is not None
        assert report["role"] == "reviewer"
        assert report["result"] == "approved"

    def test_ignores_comment_lines(self):
        body = "---AGENT-REPORT---\n# This is a comment\nrole: qa\nresult: passed\n"
        report = _parse_agent_report(body)
        assert report["role"] == "qa"
        assert report["result"] == "passed"


# ── children enrichment ───────────────────────────────────────────────────────

def _init_with_child(tmp_path: Path) -> tuple[Path, str]:
    """Returns (db_path, child_issue_id)."""
    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a1', 'lead', 'Lead', 'senior')"
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority) VALUES ('a2', 'qa', 'QA', 'standard')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('parent', 'g1', 'Parent', 'in_progress', 'lead', 'a1')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, parent_id) "
            "VALUES ('child', 'g1', 'QA issue', 'todo', 'qa', 'a2', 'parent')"
        )
    return db, "child"


class TestChildrenEnrichment:
    def test_completed_run_count_zero_with_no_runs(self, tmp_path):
        db, _ = _init_with_child(tmp_path)
        payload = build_wake_payload(db, issue_id="parent")
        child = payload["children"][0]
        assert child["completed_run_count"] == 0

    def test_completed_run_count_counts_only_completed(self, tmp_path):
        db, _ = _init_with_child(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO runs (id, agent_id, issue_id, status, provider, model, channel) "
                "VALUES ('r1', 'a2', 'child', 'completed', 'openai', 'gpt-4', 'api')"
            )
            conn.execute(
                "INSERT INTO runs (id, agent_id, issue_id, status, provider, model, channel) "
                "VALUES ('r2', 'a2', 'child', 'running', 'openai', 'gpt-4', 'api')"
            )
        payload = build_wake_payload(db, issue_id="parent")
        child = payload["children"][0]
        assert child["completed_run_count"] == 1  # only the completed one

    def test_last_agent_report_none_when_no_comment(self, tmp_path):
        db, _ = _init_with_child(tmp_path)
        payload = build_wake_payload(db, issue_id="parent")
        child = payload["children"][0]
        assert child["last_agent_report"] is None

    def test_last_agent_report_parsed_from_last_comment(self, tmp_path):
        db, _ = _init_with_child(tmp_path)
        body = (
            "QA complete.\n\n"
            "---AGENT-REPORT---\n"
            "role: qa\n"
            "result: blocked\n"
            "issue_status: blocked\n"
            "next_owner: engineer\n"
            "tech_match: no\n"
            "blocker: wrong technology\n"
            "evidence: leaderboard.py:1\n"
        )
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, body, author_agent_id) "
                "VALUES ('c1', 'child', ?, 'a2')",
                (body,),
            )
        payload = build_wake_payload(db, issue_id="parent")
        child = payload["children"][0]
        report = child["last_agent_report"]
        assert report is not None
        assert report["role"] == "qa"
        assert report["result"] == "blocked"
        assert report["tech_match"] == "no"

    def test_last_agent_report_uses_most_recent_comment(self, tmp_path):
        """Only the most recent comment is parsed for the report."""
        db, _ = _init_with_child(tmp_path)
        old_body = "---AGENT-REPORT---\nrole: qa\nresult: partial\n"
        new_body = "---AGENT-REPORT---\nrole: qa\nresult: blocked\n"
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, body, author_agent_id, created_at) "
                "VALUES ('c1', 'child', ?, 'a2', '2024-01-01T10:00:00')",
                (old_body,),
            )
            conn.execute(
                "INSERT INTO issue_comments (id, issue_id, body, author_agent_id, created_at) "
                "VALUES ('c2', 'child', ?, 'a2', '2024-01-01T11:00:00')",
                (new_body,),
            )
        payload = build_wake_payload(db, issue_id="parent")
        child = payload["children"][0]
        assert child["last_agent_report"]["result"] == "blocked"
