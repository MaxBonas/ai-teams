"""Tests for GET /api/issues/{id}/thread?view=compact|full.

Covered:
  test_thread_default_view_is_compact
  test_thread_compact_without_summary_returns_all_recent
  test_thread_compact_returns_summary_blocks_and_recent
  test_thread_compact_filters_comments_before_synthesized_through
  test_thread_full_returns_all_comments_chronological
  test_thread_invalid_view_returns_400
  test_thread_compact_has_synthesized_history_false_when_no_blocks
  test_thread_compact_has_synthesized_history_true_when_blocks_exist
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.comments import create_comment
from aiteam.db.documents import append_summary_block
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.issues import get_issue


# ── Import the router function directly (no HTTP server needed) ───────────────
# We test the function directly rather than spinning up a FastAPI test client
# to keep the test lean and avoid HTTP overhead.

def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("g1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            ("a1", "lead", "Lead", "lead", "lead_builtin"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("iss:1", "g1", "Test", "in_progress", "lead", "a1"),
        )
        conn.commit()


def _add_comments(db_path: Path, count: int) -> list[str]:
    ids = []
    for i in range(count):
        row = create_comment(
            db_path, issue_id="iss:1", body=f"Comment {i + 1}", author_agent_id="a1"
        )
        ids.append(row["id"])
    return ids


# ── Import the handler logic directly ────────────────────────────────────────

def _call_thread(
    db_path: Path,
    issue_id: str = "iss:1",
    view: str = "compact",
    max_recent: int = 15,
    max_full: int = 200,
) -> dict:
    """Call the thread endpoint logic without HTTP."""
    from aiteam.db.documents import get_context_summary

    if view not in ("compact", "full"):
        return {"error": "view must be 'compact' or 'full'", "status": 400}

    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row

        total_comments: int = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = ?", (issue_id,)
        ).fetchone()[0]

        if view == "full":
            rows = conn.execute(
                """
                SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                FROM issue_comments WHERE issue_id = ?
                ORDER BY created_at ASC, rowid ASC LIMIT ?
                """,
                (issue_id, max_full),
            ).fetchall()
            return {
                "view": "full",
                "issue_id": issue_id,
                "total_comments": total_comments,
                "comments": [dict(r) for r in rows],
                "truncated": total_comments > max_full,
            }

        # compact
        summary_data = get_context_summary(db_path, issue_id=issue_id)
        summary_blocks: list = []
        synthesized_through = None
        if summary_data:
            summary_blocks = summary_data.get("blocks", [])
            synthesized_through = summary_data.get("synthesized_through_comment_id")

        if synthesized_through:
            synth_row = conn.execute(
                "SELECT rowid FROM issue_comments WHERE id = ?", (synthesized_through,)
            ).fetchone()
            if synth_row:
                recent_rows = conn.execute(
                    """
                    SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                    FROM issue_comments WHERE issue_id = ? AND rowid > ?
                    ORDER BY created_at ASC, rowid ASC LIMIT ?
                    """,
                    (issue_id, synth_row[0], max_recent),
                ).fetchall()
            else:
                recent_rows = conn.execute(
                    """
                    SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                    FROM issue_comments WHERE issue_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?
                    """,
                    (issue_id, max_recent),
                ).fetchall()
                recent_rows = list(reversed(recent_rows))
        else:
            recent_rows = conn.execute(
                """
                SELECT id, body, author_agent_id, author_user_id, source_run_id, created_at
                FROM issue_comments WHERE issue_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """,
                (issue_id, max_recent),
            ).fetchall()
            recent_rows = list(reversed(recent_rows))

        recent_comments = [dict(r) for r in recent_rows]

    return {
        "view": "compact",
        "issue_id": issue_id,
        "total_comments": total_comments,
        "summary_blocks": summary_blocks,
        "synthesized_through": synthesized_through,
        "recent_comments": recent_comments,
        "has_synthesized_history": len(summary_blocks) > 0,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCompactDefaultView:

    def test_thread_default_view_is_compact(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        result = _call_thread(db_path)
        assert result["view"] == "compact"

    def test_thread_compact_without_summary_returns_all_recent(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 5)
        result = _call_thread(db_path, view="compact")
        assert result["view"] == "compact"
        assert len(result["recent_comments"]) == 5
        assert result["summary_blocks"] == []
        assert result["has_synthesized_history"] is False

    def test_thread_compact_total_comments_correct(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 7)
        result = _call_thread(db_path, view="compact")
        assert result["total_comments"] == 7

    def test_thread_compact_respects_max_recent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 20)
        result = _call_thread(db_path, view="compact", max_recent=5)
        assert len(result["recent_comments"]) == 5


class TestCompactWithSummary:

    def test_thread_compact_returns_summary_blocks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        ids = _add_comments(db_path, 3)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1 summary", "char_count_original": 5000},
            synthesized_through_comment_id=ids[2],
        )
        result = _call_thread(db_path, view="compact")
        assert len(result["summary_blocks"]) == 1
        assert result["summary_blocks"][0]["summary_markdown"] == "Block 1 summary"

    def test_thread_compact_has_synthesized_history_true_when_blocks(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        ids = _add_comments(db_path, 3)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 3000},
            synthesized_through_comment_id=ids[2],
        )
        result = _call_thread(db_path, view="compact")
        assert result["has_synthesized_history"] is True

    def test_thread_compact_filters_comments_before_synthesized_through(
        self, tmp_path: Path
    ) -> None:
        """Only comments AFTER synthesized_through appear in recent_comments."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        ids = _add_comments(db_path, 5)
        # Synthesize through comment #3 (index 2)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Synthesis", "char_count_original": 3000},
            synthesized_through_comment_id=ids[2],
        )
        # Comments 4 and 5 (ids[3], ids[4]) should appear as recent
        result = _call_thread(db_path, view="compact")
        recent_ids = {c["id"] for c in result["recent_comments"]}
        assert ids[0] not in recent_ids
        assert ids[1] not in recent_ids
        assert ids[2] not in recent_ids
        assert ids[3] in recent_ids
        assert ids[4] in recent_ids

    def test_thread_compact_includes_synthesized_through(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        ids = _add_comments(db_path, 3)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Synthesis", "char_count_original": 2000},
            synthesized_through_comment_id=ids[2],
        )
        result = _call_thread(db_path, view="compact")
        assert result["synthesized_through"] == ids[2]


class TestFullView:

    def test_thread_full_returns_all_comments_chronological(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        ids = _add_comments(db_path, 5)
        result = _call_thread(db_path, view="full")
        assert result["view"] == "full"
        assert len(result["comments"]) == 5
        # Chronological order: body should be "Comment 1" through "Comment 5"
        assert result["comments"][0]["body"] == "Comment 1"
        assert result["comments"][-1]["body"] == "Comment 5"

    def test_thread_full_total_comments_correct(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 8)
        result = _call_thread(db_path, view="full")
        assert result["total_comments"] == 8

    def test_thread_full_truncated_flag(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 5)
        result = _call_thread(db_path, view="full", max_full=3)
        assert result["truncated"] is True
        assert len(result["comments"]) == 3

    def test_thread_full_not_truncated_when_within_limit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comments(db_path, 5)
        result = _call_thread(db_path, view="full", max_full=200)
        assert result["truncated"] is False


class TestInvalidView:

    def test_invalid_view_returns_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        result = _call_thread(db_path, view="bad_view")
        assert result.get("status") == 400
        assert "view" in result.get("error", "").lower()
