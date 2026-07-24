from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.accept_windows_clean_room import (
    _fixture_summary,
    _github_provenance,
    _redact,
)


def test_clean_room_receipt_redacts_repo_fixture_and_user_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_root = tmp_path / "fixtures"
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))

    text = (
        f"repo={Path(__file__).resolve().parents[1]} "
        f"fixture={fixture_root} profile={tmp_path / 'profile'}"
    )
    redacted = _redact(text, fixture_root=fixture_root)

    assert "<repo>" in redacted
    assert "<fixture_root>" in redacted
    assert "<user_profile>" in redacted
    assert str(tmp_path) not in redacted


def test_fixture_summary_requires_control_plane_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "fixture.db"
    with sqlite3.connect(db_path) as conn:
        for name in ("agents", "goals", "issues", "runs", "wakeup_requests"):
            conn.execute(f"CREATE TABLE {name} (id TEXT)")
        conn.execute("INSERT INTO issues (id) VALUES ('issue-1')")
        conn.commit()

    assert _fixture_summary(db_path) == {"issues": 1, "goals": 0, "tables": 5}


def test_fixture_summary_fails_closed_when_schema_is_incomplete(tmp_path: Path) -> None:
    db_path = tmp_path / "fixture.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE issues (id TEXT)")

    with pytest.raises(RuntimeError, match="tablas requeridas"):
        _fixture_summary(db_path)


def test_independent_ci_requires_complete_matching_github_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "GITHUB_ACTIONS": "true",
        "CI": "true",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "windows-clean-room",
        "GITHUB_SHA": "abc123",
        "AITEAM_EXPECTED_SOURCE_SHA": "abc123",
        "RUNNER_OS": "Windows",
        "RUNNER_ARCH": "X64",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    independent, provenance = _github_provenance("abc123")
    assert independent is True
    assert provenance["run_id"] == "123"

    monkeypatch.setenv("AITEAM_EXPECTED_SOURCE_SHA", "other")
    assert _github_provenance("abc123")[0] is False
