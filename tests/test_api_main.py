from __future__ import annotations

import sqlite3
from pathlib import Path

from api.main import _has_control_plane_schema
from aiteam.db.migration import SCHEMA_PATH


def test_has_control_plane_schema_is_false_for_missing_db(tmp_path: Path) -> None:
    assert _has_control_plane_schema(tmp_path / "missing.db") is False


def test_has_control_plane_schema_is_false_for_empty_db(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(str(db_path)):
        pass

    assert _has_control_plane_schema(db_path) is False


def test_has_control_plane_schema_is_true_for_migrated_db(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert _has_control_plane_schema(db_path) is True
