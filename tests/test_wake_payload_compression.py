"""Tests for wake_payload context_summary injection and comment filtering.

Covered:
  test_payload_includes_no_context_summary_when_no_doc
  test_payload_includes_context_summary_blocks_when_doc_exists
  test_payload_includes_synthesized_through_when_set
  test_payload_filters_comments_after_synthesized_through
  test_payload_unchanged_comments_when_no_context_summary
  test_payload_context_summary_has_blocks_list
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from aiteam.db.comments import create_comment
from aiteam.db.documents import append_summary_block
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload


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
            ("iss:1", "g1", "Test issue", "in_progress", "lead", "a1"),
        )
        conn.commit()


def _add_comment(db_path: Path, body: str, issue_id: str = "iss:1") -> str:
    """Insert a comment with a generated UUID id and return it."""
    row = create_comment(db_path, issue_id=issue_id, body=body, author_agent_id="a1")
    return row["id"]


class TestPayloadNoContextSummary:

    def test_context_summary_is_none_when_no_doc(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        payload = build_wake_payload(db_path, issue_id="iss:1")
        assert payload.get("context_summary") is None

    def test_all_comments_shown_when_no_context_summary(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _add_comment(db_path, "comment A")
        _add_comment(db_path, "comment B")
        _add_comment(db_path, "comment C")
        payload = build_wake_payload(db_path, issue_id="iss:1")
        assert payload["comments_shown"] == 3


class TestPayloadWithContextSummary:

    def test_context_summary_included_when_doc_exists(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Summary of first batch", "char_count_original": 5000},
            synthesized_through_comment_id="c-old",
        )
        payload = build_wake_payload(db_path, issue_id="iss:1")
        assert payload.get("context_summary") is not None

    def test_context_summary_has_blocks_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 3000},
        )
        payload = build_wake_payload(db_path, issue_id="iss:1")
        cs = payload["context_summary"]
        assert isinstance(cs["blocks"], list)
        assert len(cs["blocks"]) == 1
        assert cs["blocks"][0]["summary_markdown"] == "Block 1"

    def test_context_summary_has_synthesized_through(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        c_id = _add_comment(db_path, "An old comment")
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 3000},
            synthesized_through_comment_id=c_id,
        )
        payload = build_wake_payload(db_path, issue_id="iss:1")
        cs = payload["context_summary"]
        assert cs["synthesized_through"] == c_id

    def test_comments_filtered_after_synthesized_through(self, tmp_path: Path) -> None:
        """Comments before/at synthesized_through are hidden; only newer ones shown."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        c_old = _add_comment(db_path, "old comment — synthesized")
        c_new1 = _add_comment(db_path, "new comment 1")
        c_new2 = _add_comment(db_path, "new comment 2")

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Summary of old", "char_count_original": 1000},
            synthesized_through_comment_id=c_old,
        )

        payload = build_wake_payload(db_path, issue_id="iss:1")
        comment_ids = [c["id"] for c in payload["comments"]]

        assert c_old not in comment_ids, "synthesized comment should be filtered out"
        assert c_new1 in comment_ids, "new comment 1 should be present"
        assert c_new2 in comment_ids, "new comment 2 should be present"

    def test_multiple_blocks_accumulated(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 4000},
            synthesized_through_comment_id="c1",
        )
        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 2", "char_count_original": 4000},
            synthesized_through_comment_id="c2",
        )
        payload = build_wake_payload(db_path, issue_id="iss:1")
        cs = payload["context_summary"]
        assert len(cs["blocks"]) == 2
        assert cs["synthesized_through"] == "c2"
