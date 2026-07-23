from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def short_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(prefix="ati2-") as value:
        yield Path(value)


@pytest.mark.skipif(os.name != "nt", reason="Windows update contract")
def test_windows_update_fast_forwards_bootstraps_and_preserves_local_runtime(
    short_tmp_path: Path,
) -> None:
    tmp_path = short_tmp_path
    remote, publisher = _create_remote_fixture(tmp_path)
    install = tmp_path / "installed"
    _git(tmp_path, "clone", str(remote), str(install))
    local_state = install / "runtime" / "user-local.json"
    local_state.parent.mkdir()
    local_state.write_text('{"keep": true}', encoding="utf-8")
    previous = _git(install, "rev-parse", "HEAD").stdout.strip()

    (publisher / "version.txt").write_text("v2", encoding="utf-8")
    _git(publisher, "add", "version.txt")
    _git(publisher, "commit", "-m", "fixture v2")
    _git(publisher, "push")

    result = _run_update(install)

    assert result.returncode == 0, result.stderr or result.stdout
    assert (install / "version.txt").read_text(encoding="utf-8") == "v2"
    assert (install / "bootstrap-ran.txt").is_file()
    assert local_state.read_text(encoding="utf-8") == '{"keep": true}'
    receipt = json.loads(
        (install / "runtime" / "last_update.json").read_text(encoding="utf-8-sig")
    )
    assert receipt["status"] == "ready_to_start"
    assert receipt["previous_revision"] == previous
    assert receipt["current_revision"] == _git(install, "rev-parse", "HEAD").stdout.strip()
    assert receipt["current_revision"] != previous


@pytest.mark.skipif(os.name != "nt", reason="Windows update contract")
def test_windows_update_refuses_dirty_checkout_without_overwriting(
    short_tmp_path: Path,
) -> None:
    tmp_path = short_tmp_path
    remote, _publisher = _create_remote_fixture(tmp_path)
    install = tmp_path / "installed"
    _git(tmp_path, "clone", str(remote), str(install))
    version = install / "version.txt"
    version.write_text("my local edit", encoding="utf-8")

    result = _run_update(install)

    assert result.returncode == 1
    assert version.read_text(encoding="utf-8") == "my local edit"
    assert not (install / "bootstrap-ran.txt").exists()
    receipt = json.loads(
        (install / "runtime" / "last_update.json").read_text(encoding="utf-8-sig")
    )
    assert receipt["status"] == "failed"
    assert "commit" in receipt["detail"]
    assert "stash" in receipt["detail"]


def _create_remote_fixture(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    publisher = tmp_path / "publisher"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(remote, "config", "core.longpaths", "true")
    _git(tmp_path, "clone", str(remote), str(publisher))
    _git(publisher, "config", "core.longpaths", "true")
    _git(publisher, "config", "user.email", "fixture@example.invalid")
    _git(publisher, "config", "user.name", "AI Teams fixture")
    scripts = publisher / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "update_windows.ps1", scripts / "update_windows.ps1")
    (scripts / "prepare_dev_env.bat").write_text(
        "@echo off\r\n> \"%~dp0..\\bootstrap-ran.txt\" echo ready\r\nexit /b 0\r\n",
        encoding="utf-8",
    )
    (publisher / "stop_ide.bat").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    (publisher / ".gitignore").write_text("runtime/\n", encoding="utf-8")
    (publisher / "version.txt").write_text("v1", encoding="utf-8")
    _git(publisher, "add", ".")
    _git(publisher, "commit", "-m", "fixture v1")
    _git(publisher, "push", "-u", "origin", "master")
    return remote, publisher


def _run_update(install: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(install / "scripts" / "update_windows.ps1"),
            "-SkipStop",
            "-Quiet",
        ],
        cwd=install,
        env=_git_long_paths_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_git_long_paths_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def _git_long_paths_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.longpaths",
            "GIT_CONFIG_VALUE_0": "true",
        }
    )
    return env
