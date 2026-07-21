"""Tests for block-based context_curator auto-trigger (_maybe_spawn_context_curator).

Model change (2026-05-12):
  OLD: threshold = 8 comments; done curator blocks re-spawn.
  NEW: threshold = 8 000 unsynthesized chars; done curator does NOT block re-spawn.

Trigger conditions:
  C1. Unsynthesized char count >= _CONTEXT_CURATOR_CHAR_THRESHOLD (8 000)
      "Unsynthesized" = comments after synthesized_through_comment_id, or all if none.
  C2. No 'plan' document exists for the issue.
  C3. No ACTIVE (todo/in_progress/blocked) context_curator child exists.
      - done: does NOT block (allows new block when more content accumulates)
      - cancelled: does NOT block

Tests are organised as:
  - Char threshold (below / at / above)
  - Plan doc blocks spawn
  - Idempotency: active blocks; done / cancelled do NOT block
  - Curator child properties (role, complexity, description anchors)
  - Second block spawn after prior synthesis
  - Flow integration (does not interrupt child_report)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import build_default_registry
from aiteam.db.comments import create_comment
from aiteam.db.documents import append_summary_block, put_document
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.context_curator import CONTEXT_CURATOR_CHAR_THRESHOLD
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Shared DB helpers ────────────────────────────────────────────────────────

_CHAR_THRESHOLD = CONTEXT_CURATOR_CHAR_THRESHOLD  # fallback legacy: 8 000

_ENGINEER_DONE = (
    "Implementación completada.\n\n"
    "---AGENT-REPORT---\n"
    "role: engineer\n"
    "result: done\n"
    "issue_status: done\n"
    "next_owner: reviewer\n"
    "tech_match: yes\n"
    "blocker: none\n"
    "evidence: src/main.py:1-50\n"
)

_REVIEWER_DONE = (
    "Revisión completada.\n\n"
    "---AGENT-REPORT---\n"
    "role: reviewer\n"
    "result: done\n"
    "issue_status: done\n"
    "next_owner: lead\n"
    "tech_match: yes\n"
    "blocker: none\n"
    "evidence: src/main.py:1-50\n"
)


def _init_db(db_path: Path) -> None:
    """Minimal DB: lead + parent issue + done engineer + done reviewer children."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("role:lead", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:engineer", "engineer", "Engineer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, supervisor_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("role:reviewer", "reviewer", "Reviewer", "standard", "openai_api", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("issue:intake", "goal-1", "Build the app", "in_progress", "lead", "role:lead"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:intake:eng", "goal-1", "issue:intake", "Engineer task",
             "done", "engineer", "role:engineer"),
        )
        conn.execute(
            "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
            ("issue:intake:eng", "role:engineer", _ENGINEER_DONE),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:intake:rev", "goal-1", "issue:intake", "Reviewer task",
             "done", "reviewer", "role:reviewer"),
        )
        conn.execute(
            "INSERT INTO issue_comments (issue_id, author_agent_id, body) VALUES (?, ?, ?)",
            ("issue:intake:rev", "role:reviewer", _REVIEWER_DONE),
        )
        conn.commit()


def _add_comment(db_path: Path, body: str, issue_id: str = "issue:intake") -> str:
    """Insert one comment with a generated UUID and return its ID."""
    row = create_comment(db_path, issue_id=issue_id, body=body, author_agent_id="role:lead")
    return row["id"]


def _add_content(db_path: Path, total_chars: int, issue_id: str = "issue:intake") -> str:
    """Add comments totalling *total_chars* characters; return last comment's ID."""
    chunk = 1000
    last_id = ""
    remaining = total_chars
    while remaining > 0:
        size = min(chunk, remaining)
        last_id = _add_comment(db_path, "x" * size, issue_id)
        remaining -= size
    return last_id


def _count_curator_children(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM issues"
            " WHERE parent_id = 'issue:intake' AND lower(role) = 'context_curator'",
        ).fetchone()[0]


def _curator_status(db_path: Path) -> str | None:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status FROM issues"
            " WHERE parent_id = 'issue:intake' AND lower(role) = 'context_curator'"
            " ORDER BY rowid DESC LIMIT 1",
        ).fetchone()
    return row[0] if row else None


def _curator_description(db_path: Path) -> str | None:
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT description FROM issues"
            " WHERE parent_id = 'issue:intake' AND lower(role) = 'context_curator'"
            " ORDER BY rowid DESC LIMIT 1",
        ).fetchone()
    return row[0] if row else None


def _add_curator_child(db_path: Path, status: str) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("issue:intake:curator", "goal-1", "issue:intake",
             "Context curator — synthesize thread block",
             status, "context_curator", "role:lead"),
        )
        conn.commit()


def _set_lead_context_budget(db_path: Path, config: dict[str, Any]) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE agents SET adapter_config_json=? WHERE id='role:lead'",
            (json.dumps(config),),
        )
        conn.commit()


def _dispatch_and_run(db_path: Path) -> None:
    enqueue_wakeup(
        db_path,
        agent_id="role:lead",
        source="child_report",
        reason="child_report",
        payload={"issue_id": "issue:intake", "wake_reason": "child_report"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None, "Lead wakeup should be dispatched"
    RunExecutor(db_path, build_default_registry()).execute(dispatch)


# ── C1: char threshold ────────────────────────────────────────────────────────

class TestCharThreshold:

    def test_no_spawn_when_unsynthesized_chars_below_threshold(self, tmp_path: Path) -> None:
        """Below threshold → no curator spawned."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD - 1)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 0

    def test_spawn_when_unsynthesized_chars_at_threshold(self, tmp_path: Path) -> None:
        """Exactly at threshold → spawn."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1

    def test_spawn_when_unsynthesized_chars_above_threshold(self, tmp_path: Path) -> None:
        """Above threshold → spawn."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD + 2000)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1

    def test_curator_is_todo_when_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        assert _curator_status(db_path) == "todo"


class TestModelComfortBudget:

    def test_declared_large_window_delays_legacy_threshold(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _set_lead_context_budget(db_path, {
            "context_window_tokens": 100_000,
            "comfortable_context_ratio": 0.70,
            "reserved_output_tokens": 8_000,
            "reserved_tool_tokens": 8_000,
        })
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 0

    def test_declared_small_comfort_budget_triggers_and_persists_decision(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _set_lead_context_budget(db_path, {
            "context_window_tokens": 8_000,
            "comfortable_context_ratio": 0.50,
            "reserved_output_tokens": 1_000,
            "reserved_tool_tokens": 1_000,
        })
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            metadata = json.loads(conn.execute(
                "SELECT metadata_json FROM issues WHERE parent_id='issue:intake' "
                "AND role='context_curator'"
            ).fetchone()[0])
            event = conn.execute(
                "SELECT payload_json FROM activity_log WHERE action='context_compaction.triggered'"
            ).fetchone()
        assert metadata["context_budget"]["policy"] == "model_comfort_budget"
        assert metadata["context_budget"]["comfortable_input_tokens"] == 2_000
        assert event is not None


# ── C2: plan document blocks spawn ───────────────────────────────────────────

class TestPlanDocumentBlocks:

    def test_no_spawn_when_plan_doc_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        put_document(
            db_path,
            issue_id="issue:intake",
            key="plan",
            title="Plan",
            body="## Phase 1\n- Implement feature\n",
        )
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 0

    def test_spawn_when_no_plan_doc(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1


# ── C3: idempotency ───────────────────────────────────────────────────────────

class TestIdempotency:

    def test_no_second_curator_when_todo_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _add_curator_child(db_path, "todo")
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1

    def test_no_second_curator_when_in_progress(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _add_curator_child(db_path, "in_progress")
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1

    def test_no_second_curator_when_blocked(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _add_curator_child(db_path, "blocked")
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1

    def test_done_curator_does_not_block_respawn(self, tmp_path: Path) -> None:
        """KEY CHANGE: done curator no longer blocks re-spawn (new block model).

        A done curator means the prior block is complete.  When enough NEW content
        accumulates the Lead should spawn a fresh block — not be stuck waiting.
        """
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Add content and record a 'synthesized_through' so the NEW chars also exceed threshold
        last_id = _add_content(db_path, _CHAR_THRESHOLD)
        # Simulate prior synthesis: mark everything up through last_id as synthesized
        append_summary_block(
            db_path,
            issue_id="issue:intake",
            block={"summary_markdown": "Prior summary", "char_count_original": _CHAR_THRESHOLD},
            synthesized_through_comment_id=last_id,
        )
        # Curator from prior run is done
        _add_curator_child(db_path, "done")
        # New content beyond the threshold
        _add_content(db_path, _CHAR_THRESHOLD + 500)
        _dispatch_and_run(db_path)
        # A new curator must have been spawned (total = original done + new one)
        assert _count_curator_children(db_path) == 2, (
            "A done curator should NOT block a new spawn when new unsynthesized chars "
            "exceed the threshold"
        )

    def test_new_curator_spawned_when_prior_is_cancelled(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _add_curator_child(db_path, "cancelled")
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 2


# ── Second block spawn after synthesis ───────────────────────────────────────

class TestSecondBlockSpawn:

    def test_second_block_spawned_when_new_chars_exceed_threshold(
        self, tmp_path: Path
    ) -> None:
        """After a prior synthesis block, accumulating more chars triggers a new curator."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)

        # First batch — synthesized
        last_synth_id = _add_content(db_path, _CHAR_THRESHOLD)
        append_summary_block(
            db_path,
            issue_id="issue:intake",
            block={"summary_markdown": "Block 1", "char_count_original": _CHAR_THRESHOLD},
            synthesized_through_comment_id=last_synth_id,
        )

        # New unsynthesized content exceeds threshold
        _add_content(db_path, _CHAR_THRESHOLD + 1000)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 1, (
            "New curator must be spawned when unsynthesized content exceeds threshold"
        )

    def test_no_second_spawn_when_new_chars_below_threshold(
        self, tmp_path: Path
    ) -> None:
        """After a prior synthesis, new chars below threshold → no new curator."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)

        last_synth_id = _add_content(db_path, _CHAR_THRESHOLD)
        append_summary_block(
            db_path,
            issue_id="issue:intake",
            block={"summary_markdown": "Block 1", "char_count_original": _CHAR_THRESHOLD},
            synthesized_through_comment_id=last_synth_id,
        )

        # New content below threshold
        _add_content(db_path, _CHAR_THRESHOLD // 2)
        _dispatch_and_run(db_path)
        assert _count_curator_children(db_path) == 0


# ── Curator child properties ──────────────────────────────────────────────────

class TestCuratorChildProperties:

    def test_curator_role_is_context_curator(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT role FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert row["role"].lower() == "context_curator"

    def test_curator_complexity_is_low(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT complexity FROM issues WHERE parent_id = 'issue:intake'"
                " AND lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert str(row["complexity"] or "").lower() == "low"

    def test_curator_description_includes_target_issue_id(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        desc = _curator_description(db_path) or ""
        assert "Target issue: issue:intake" in desc

    def test_curator_description_includes_synthesize_from_all_on_first_spawn(
        self, tmp_path: Path
    ) -> None:
        """First curator (no prior synthesis) gets 'Synthesize from: comment:<id>'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        desc = _curator_description(db_path) or ""
        assert "Synthesize from:" in desc

    def test_curator_description_includes_synthesize_from_comment_id_on_second_block(
        self, tmp_path: Path
    ) -> None:
        """Second curator (after prior synthesis) gets 'Synthesize from: comment:<id>'."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)

        last_synth_id = _add_content(db_path, _CHAR_THRESHOLD)
        append_summary_block(
            db_path,
            issue_id="issue:intake",
            block={"summary_markdown": "Block 1", "char_count_original": _CHAR_THRESHOLD},
            synthesized_through_comment_id=last_synth_id,
        )
        _add_content(db_path, _CHAR_THRESHOLD + 500)
        _dispatch_and_run(db_path)

        desc = _curator_description(db_path) or ""
        assert f"Synthesize from: comment:" in desc

    def test_curator_parent_id_is_intake(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT parent_id FROM issues WHERE lower(role) = 'context_curator' LIMIT 1",
            ).fetchone()
        assert row is not None
        assert row["parent_id"] == "issue:intake"


# ── Flow integration ──────────────────────────────────────────────────────────

class TestDoesNotInterruptFlow:

    def test_cycle_close_deferred_while_curator_is_active(self, tmp_path: Path) -> None:
        """Spawned curator is non-terminal → _all_children_done False → no cycle-close yet."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)

        _TERMINAL = {"accepted", "rejected", "answered", "cancelled", "expired"}
        from aiteam.db.interactions import list_interactions
        interactions = list_interactions(db_path, issue_id="issue:intake")
        pending = [i for i in interactions if str(i.get("status") or "") not in _TERMINAL]
        reasons = [
            str((i.get("payload") or {}).get("reason") or "") for i in pending
        ]
        assert "initial_cycle_ready" not in reasons
        assert _curator_status(db_path) == "todo"

    def test_parent_issue_not_terminal_after_curator_spawn(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        _add_content(db_path, _CHAR_THRESHOLD)
        _dispatch_and_run(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            parent = conn.execute(
                "SELECT status FROM issues WHERE id = 'issue:intake'",
            ).fetchone()
        assert parent["status"] not in {"cancelled", "done"}
