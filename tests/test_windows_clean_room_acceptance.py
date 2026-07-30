from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.accept_windows_clean_room import (
    _direct_footprint,
    _fixture_summary,
    _github_provenance,
    _guided_fixture_contract,
    _installation_lifecycle_contract,
    _installation_state,
    _materialize_guided_fixture,
    _redact,
    _tree_manifest,
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


def test_clean_room_uses_current_guided_commit_without_numbered_siblings(
    tmp_path: Path,
) -> None:
    target = tmp_path / "Acceptance Project"
    before = _direct_footprint(tmp_path)

    guided = _materialize_guided_fixture(target)

    assert _direct_footprint(tmp_path) == {
        **before,
        target.name: "directory",
    }
    assert guided["result"]["schema_version"] == "guided_setup_project_commit_v1"
    assert guided["result"]["lead_first"] is True
    assert guided["result"]["footprint_verified"] is True
    assert _guided_fixture_contract(Path(guided["result"]["database"])) == {
        "roles": ["lead"],
        "queued_wakeups": 1,
        "objective_kind": "research",
    }
    assert not list(tmp_path.glob("Acceptance Project *"))

    manifest = _tree_manifest(target)
    with pytest.raises(
        FileExistsError,
        match="guided_setup_project_target_collision",
    ):
        _materialize_guided_fixture(target)
    assert _tree_manifest(target) == manifest
    assert _direct_footprint(tmp_path) == {
        **before,
        target.name: "directory",
    }


def test_clean_room_source_does_not_reintroduce_legacy_project_create() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "accept_windows_clean_room.py"
    ).read_text(encoding="utf-8")

    assert '"project",\n                "create"' not in source
    assert "-m aiteam.cli project create" not in source
    assert "guided_fixture_project_commit" in source
    assert "provider_cli_clean_update_equivalence" in source
    assert "start_after_update" in source


def test_installation_entrypoints_do_not_register_persistent_cleanup() -> None:
    contract = _installation_lifecycle_contract(
        Path(__file__).resolve().parents[1]
    )

    assert contract["source_count"] == 5
    assert contract["scheduled_tasks_installed"] is False
    assert contract["services_installed"] is False
    assert contract["startup_entries_installed"] is False
    assert contract["runtime_requires_explicit_start"] is True
    assert contract["runtime_has_explicit_stop"] is True
    assert contract["findings"] == {
        "scheduled_task": [],
        "service": [],
        "startup_registry": [],
    }


def test_installation_state_separates_clean_clone_from_real_update() -> None:
    revision = "a" * 40

    assert _installation_state(
        "clean_clone",
        revision=revision,
        pre_update_revision=None,
    )["kind"] == "clean_clone"
    updated = _installation_state(
        "existing_checkout_updated",
        revision=revision,
        pre_update_revision="b" * 40,
    )
    assert updated["pre_update_revision"] == "b" * 40
    assert updated["updated_to_revision"] == revision

    with pytest.raises(ValueError, match="revisión anterior distinta"):
        _installation_state(
            "existing_checkout_updated",
            revision=revision,
            pre_update_revision=revision,
        )


def test_clean_room_script_bootstraps_import_path_from_arbitrary_cwd(
    tmp_path: Path,
) -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "accept_windows_clean_room.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--installation-state" in result.stdout
