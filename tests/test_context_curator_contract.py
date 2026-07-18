from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.adapters.registry import ExecutionResult, build_default_registry
from aiteam.adapters.work_contract import filter_forbidden_ops_for_role, ops_to_actions
from aiteam.db.comments import create_comment
from aiteam.db.documents import get_context_summary
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload
from aiteam.heartbeat.executor import RunExecutor


def _project(tmp_path: Path) -> tuple[Path, str, str]:
    db = tmp_path / "aiteam.db"
    parent_id = "issue:parent"
    child_id = "issue:curator"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id,title) VALUES ('g','Goal')")
        conn.execute(
            "INSERT INTO agents (id,role,name,adapter_type) VALUES "
            "('role:lead','lead','Lead','manual'),"
            "('role:context_curator','context_curator','Curator','manual')"
        )
        conn.execute(
            "INSERT INTO issues (id,goal_id,title,status,role,assignee_agent_id) "
            "VALUES (?,?,?,?,?,?)",
            (parent_id, "g", "Parent", "in_progress", "lead", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id,goal_id,parent_id,title,status,role,assignee_agent_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (child_id, "g", parent_id, "Curate", "in_progress", "context_curator", "role:context_curator"),
        )
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status) VALUES ('run:curator','role:context_curator',?,'running')",
            (child_id,),
        )
        conn.commit()
    create_comment(db, issue_id=parent_id, author_user_id="user", body="A" * 5_000)
    create_comment(db, issue_id=parent_id, author_agent_id="role:lead", body="B" * 4_000)
    return db, parent_id, child_id


def test_curator_payload_contains_exact_parent_slice(tmp_path: Path) -> None:
    db, parent_id, child_id = _project(tmp_path)

    target = build_wake_payload(db, issue_id=child_id)["context_curation_target"]

    assert target["target_issue_id"] == parent_id
    assert target["char_count_original"] == 9_000
    assert len(target["comments"]) == 2
    assert target["start_comment_id"] == target["comments"][0]["id"]
    assert target["end_comment_id"] == target["comments"][-1]["id"]


def test_append_context_summary_is_exclusive_to_curator() -> None:
    op = {"type": "append_context_summary", "path": "issue:x", "body": "summary"}

    curator_allowed, curator_dropped = filter_forbidden_ops_for_role([op], "context_curator")
    lead_allowed, lead_dropped = filter_forbidden_ops_for_role([op], "lead")

    assert curator_allowed == [op] and curator_dropped == []
    assert lead_allowed == [] and lead_dropped == [op]


def test_curator_persists_verified_block_before_closing(tmp_path: Path) -> None:
    db, parent_id, child_id = _project(tmp_path)
    target = build_wake_payload(db, issue_id=child_id)["context_curation_target"]
    actions = ops_to_actions([
        {
            "type": "append_context_summary",
            "path": parent_id,
            "body": "Decisión A; restricción B; owner Lead; evidencia preservada.",
            "start_comment_id": target["start_comment_id"],
            "end_comment_id": target["end_comment_id"],
            "char_count_original": target["char_count_original"],
            "start_char_offset": target["start_char_offset"],
            "end_char_offset": target["end_char_offset"],
        },
        {"type": "set_status", "status": "done"},
    ])

    RunExecutor(db, build_default_registry())._apply_result_actions(
        run={"id": "run:curator", "issue_id": child_id},
        agent_id="role:context_curator",
        agent_role="context_curator",
        result=ExecutionResult(status="completed", actions=actions),
    )

    summary = get_context_summary(db, issue_id=parent_id)
    assert summary is not None
    assert summary["synthesized_through_comment_id"] == target["end_comment_id"]
    assert summary["blocks"][0]["char_count_original"] == 9_000
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM issues WHERE id=?", (child_id,)).fetchone()[0] == "done"


def test_oversized_comment_is_sliced_by_durable_offsets_without_false_advance(tmp_path: Path) -> None:
    db, parent_id, child_id = _project(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM issue_comments WHERE issue_id=?", (parent_id,))
        conn.commit()
    oversized_id = create_comment(
        db, issue_id=parent_id, author_user_id="user", body="X" * 30_000
    )["id"]
    first = build_wake_payload(db, issue_id=child_id)["context_curation_target"]
    assert first["char_count_original"] == 24_000
    assert first["start_char_offset"] == 0
    assert first["end_char_offset"] == 24_000
    assert first["has_more_unsynthesized"] is True

    actions = ops_to_actions([
        {
            "type": "append_context_summary", "path": parent_id, "body": "Primer segmento causal.",
            "start_comment_id": oversized_id, "end_comment_id": oversized_id,
            "char_count_original": 24_000, "start_char_offset": 0, "end_char_offset": 24_000,
        },
        {"type": "set_status", "status": "done"},
    ])
    RunExecutor(db, build_default_registry())._apply_result_actions(
        run={"id": "run:curator", "issue_id": child_id}, agent_id="role:context_curator",
        agent_role="context_curator", result=ExecutionResult(status="completed", actions=actions),
    )

    summary = get_context_summary(db, issue_id=parent_id)
    assert summary.get("synthesized_through_comment_id") is None
    assert summary["partial_comment_id"] == oversized_id
    assert summary["partial_char_offset"] == 24_000
    second = build_wake_payload(db, issue_id=child_id)["context_curation_target"]
    assert second["char_count_original"] == 6_000
    assert second["start_char_offset"] == 24_000
    assert second["end_char_offset"] == 30_000
    assert second["comments"][0]["body"] == "X" * 6_000

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO issues (id,goal_id,parent_id,title,status,role,assignee_agent_id) "
            "VALUES ('issue:curator-2','g',?,'Curate remainder','in_progress','context_curator',"
            "'role:context_curator')", (parent_id,),
        )
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status) VALUES "
            "('run:curator-2','role:context_curator','issue:curator-2','running')"
        )
        conn.commit()
    final_actions = ops_to_actions([
        {
            "type": "append_context_summary", "path": parent_id, "body": "Segmento final causal.",
            "start_comment_id": oversized_id, "end_comment_id": oversized_id,
            "char_count_original": 6_000, "start_char_offset": 24_000,
            "end_char_offset": 30_000,
        },
        {"type": "set_status", "status": "done"},
    ])
    RunExecutor(db, build_default_registry())._apply_result_actions(
        run={"id": "run:curator-2", "issue_id": "issue:curator-2"},
        agent_id="role:context_curator", agent_role="context_curator",
        result=ExecutionResult(status="completed", actions=final_actions),
    )
    completed = get_context_summary(db, issue_id=parent_id)
    assert completed["synthesized_through_comment_id"] == oversized_id
    assert "partial_comment_id" not in completed
    assert "partial_char_offset" not in completed


def test_curator_gets_one_corrective_retry_then_escalates(tmp_path: Path) -> None:
    db, parent_id, child_id = _project(tmp_path)
    actions = ops_to_actions([
        {
            "type": "append_context_summary",
            "path": parent_id,
            "body": "Resumen",
            "start_comment_id": "wrong",
            "end_comment_id": "wrong",
            "char_count_original": 99_999,
        },
        {"type": "set_status", "status": "done"},
    ])

    RunExecutor(db, build_default_registry())._apply_result_actions(
        run={"id": "run:curator", "issue_id": child_id},
        agent_id="role:context_curator",
        agent_role="context_curator",
        result=ExecutionResult(status="completed", actions=actions),
    )

    assert get_context_summary(db, issue_id=parent_id) is None
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute(
            "SELECT status, metadata_json FROM issues WHERE id=?", (child_id,)
        ).fetchone()
        retry = conn.execute(
            "SELECT agent_id, reason, payload_json FROM wakeup_requests "
            "WHERE source='context_curator_recovery'"
        ).fetchone()
    assert issue["status"] == "in_progress"
    assert '"corrective_attempts": 1' in issue["metadata_json"]
    assert retry["agent_id"] == "role:context_curator"
    assert retry["reason"] == "context_summary_corrective_retry"
    assert "does not match the durable source slice" in retry["payload_json"]

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO runs (id,agent_id,issue_id,status) VALUES "
            "('run:curator-retry','role:context_curator',?,'running')",
            (child_id,),
        )
        conn.commit()
    RunExecutor(db, build_default_registry())._apply_result_actions(
        run={"id": "run:curator-retry", "issue_id": child_id},
        agent_id="role:context_curator",
        agent_role="context_curator",
        result=ExecutionResult(status="completed", actions=actions),
    )

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        issue = conn.execute(
            "SELECT status, metadata_json FROM issues WHERE id=?", (child_id,)
        ).fetchone()
        lead_wakes = conn.execute(
            "SELECT COUNT(*) AS n FROM wakeup_requests WHERE agent_id='role:lead'"
        ).fetchone()["n"]
        retry_wakes = conn.execute(
            "SELECT COUNT(*) AS n FROM wakeup_requests WHERE source='context_curator_recovery'"
        ).fetchone()["n"]
    assert issue["status"] == "blocked"
    assert '"corrective_attempts": 2' in issue["metadata_json"]
    assert '"state": "escalated"' in issue["metadata_json"]
    assert retry_wakes == 1
    assert lead_wakes == 1
