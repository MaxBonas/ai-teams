from __future__ import annotations

from pathlib import Path

import pytest

import conftest as suite_config
from scripts.cleanup_test_artifacts import _cleanup_session_root, pid_is_running


def test_conftest_keeps_live_sibling_session(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "sessions"
    live = root / "session-4242-deadbeef"
    current = root / "session-9999-cafebabe"
    live.mkdir(parents=True)
    monkeypatch.setattr(suite_config, "pid_is_running", lambda pid: pid == 4242)

    suite_config._clean_stale_root(root, current=current)

    assert live.is_dir()


def test_conftest_warns_and_keeps_locked_stale_session(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "sessions"
    stale = root / "session-4242-deadbeef"
    current = root / "session-9999-cafebabe"
    stale.mkdir(parents=True)
    monkeypatch.setattr(suite_config, "pid_is_running", lambda _pid: False)
    monkeypatch.setattr(
        suite_config,
        "_remove_test_tree",
        lambda _path: (_ for _ in ()).throw(PermissionError("locked")),
    )

    with pytest.warns(RuntimeWarning, match="retained locked stale path"):
        suite_config._clean_stale_root(root, current=current)

    assert stale.is_dir()


def test_external_cleanup_does_not_remove_live_session(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    live = root / f"session-{__import__('os').getpid()}-deadbeef"
    live.mkdir(parents=True)
    failures: list[str] = []

    _cleanup_session_root(root, include_live=False, failures=failures)

    assert live.is_dir()
    assert failures == []


def test_windows_pid_probe_observes_current_process_without_terminating_it() -> None:
    assert pid_is_running(__import__("os").getpid()) is True


def test_user_config_is_scoped_to_same_pytest_session() -> None:
    assert suite_config._USER_CONFIG_SESSION.name == suite_config._TEMP_SESSION.name
    assert suite_config._TEST_ENV_OVERRIDES["AITEAM_USER_CONFIG_DIR"] == str(
        suite_config._USER_CONFIG_SESSION
    )
