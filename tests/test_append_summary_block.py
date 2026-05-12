"""Tests for append_summary_block() and get_context_summary() in aiteam/db/documents.py.

Covered:
  test_append_first_block_creates_doc
  test_append_second_block_updates_blocks_list
  test_append_sets_synthesized_through_comment_id
  test_append_second_block_updates_synthesized_through
  test_get_context_summary_returns_none_when_absent
  test_get_context_summary_returns_parsed_body
  test_append_block_increments_revision_number
  test_append_block_without_synthesized_through_preserves_none
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.documents import append_summary_block, get_context_summary, get_document
from aiteam.db.migration import SCHEMA_PATH


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("g1", "Goal"))
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role) VALUES (?, ?, ?, ?, ?)",
            ("iss:1", "g1", "Test issue", "in_progress", "lead"),
        )
        conn.commit()


class TestAppendFirstBlock:

    def test_append_first_block_creates_doc(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        doc = append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1 summary", "char_count_original": 5000},
        )

        assert doc is not None
        assert doc["key"] == "context_summary"
        assert doc["issue_id"] == "iss:1"

    def test_append_first_block_body_has_blocks_list(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 5000},
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert data is not None
        assert isinstance(data["blocks"], list)
        assert len(data["blocks"]) == 1
        assert data["blocks"][0]["summary_markdown"] == "Block 1"

    def test_append_first_block_sets_synthesized_through(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 5000},
            synthesized_through_comment_id="comment:abc",
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert data is not None
        assert data.get("synthesized_through_comment_id") == "comment:abc"

    def test_append_block_without_through_id_leaves_it_absent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 5000},
            synthesized_through_comment_id=None,
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert data is not None
        assert "synthesized_through_comment_id" not in data


class TestAppendSecondBlock:

    def test_append_second_block_accumulates_in_blocks_list(self, tmp_path: Path) -> None:
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
            block={"summary_markdown": "Block 2", "char_count_original": 5000},
            synthesized_through_comment_id="c2",
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert data is not None
        assert len(data["blocks"]) == 2
        assert data["blocks"][0]["summary_markdown"] == "Block 1"
        assert data["blocks"][1]["summary_markdown"] == "Block 2"

    def test_append_second_block_advances_synthesized_through(self, tmp_path: Path) -> None:
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
            block={"summary_markdown": "Block 2", "char_count_original": 5000},
            synthesized_through_comment_id="c2",
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert data["synthesized_through_comment_id"] == "c2"

    def test_append_increments_document_revision(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        doc1 = append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 1", "char_count_original": 4000},
        )
        doc2 = append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Block 2", "char_count_original": 5000},
        )

        assert int(doc1["revision_number"]) == 1
        assert int(doc2["revision_number"]) == 2


class TestGetContextSummary:

    def test_returns_none_when_no_doc(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        assert get_context_summary(db_path, issue_id="iss:1") is None

    def test_returns_parsed_body(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Summary", "char_count_original": 3000},
        )
        data = get_context_summary(db_path, issue_id="iss:1")

        assert isinstance(data, dict)
        assert "blocks" in data

    def test_returns_none_for_wrong_issue(self, tmp_path: Path) -> None:
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)

        append_summary_block(
            db_path,
            issue_id="iss:1",
            block={"summary_markdown": "Summary", "char_count_original": 3000},
        )
        assert get_context_summary(db_path, issue_id="iss:nonexistent") is None
