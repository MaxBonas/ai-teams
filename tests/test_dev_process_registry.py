from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from aiteam.dev_process_registry import (
    ProcessRegistryError,
    assert_clear,
    register_process,
    register_processes,
    stop_registered,
)


def _sleeper() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _reap(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_stop_terminates_only_registered_identity(tmp_path: Path) -> None:
    owned = _sleeper()
    unrelated = _sleeper()
    registry = tmp_path / "ide_processes.json"
    try:
        register_processes(
            root=tmp_path,
            registry_path=registry,
            process_specs=[("backend", owned.pid, "time.sleep")],
            ports={"backend": 8010},
        )

        result = stop_registered(root=tmp_path, registry_path=registry, timeout=2)

        owned.wait(timeout=5)
        assert result["ok"] is True
        assert owned.pid in result["stopped"]
        assert unrelated.poll() is None
        assert not registry.exists()
    finally:
        _reap(owned)
        _reap(unrelated)


def test_stop_fails_closed_when_pid_identity_changed(tmp_path: Path) -> None:
    process = _sleeper()
    registry = tmp_path / "ide_processes.json"
    try:
        register_processes(
            root=tmp_path,
            registry_path=registry,
            process_specs=[("backend", process.pid, "time.sleep")],
            ports={"backend": 8010},
        )
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["processes"][0]["create_time"] = time.time() - 1000
        registry.write_text(json.dumps(payload), encoding="utf-8")

        result = stop_registered(root=tmp_path, registry_path=registry, timeout=1)

        assert result["ok"] is False
        assert result["status"] == "identity_mismatch"
        assert result["mismatches"][0]["reason"] == "create_time_mismatch"
        assert psutil.pid_exists(process.pid)
        assert registry.exists()
    finally:
        _reap(process)


def test_stop_is_idempotent_without_registry(tmp_path: Path) -> None:
    first = stop_registered(root=tmp_path, registry_path=tmp_path / "missing.json")
    second = stop_registered(root=tmp_path, registry_path=tmp_path / "missing.json")

    assert first == second
    assert first["status"] == "not_running"


def test_incremental_registration_survives_interruption_between_spawns(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout con espacio ñ 日本語"
    root.mkdir()
    backend = _sleeper()
    registry = root / "runtime" / "ide_processes.json"
    try:
        result = register_process(
            root=root,
            registry_path=registry,
            role="backend",
            pid=backend.pid,
            marker="time.sleep",
            port_key="backend",
            port=8010,
        )

        assert result["process_count"] == 1
        with pytest.raises(ProcessRegistryError, match="already_running"):
            assert_clear(root=root, registry_path=registry)

        stopped = stop_registered(root=root, registry_path=registry, timeout=2)
        backend.wait(timeout=5)
        assert stopped["ok"] is True
        assert not registry.exists()
    finally:
        _reap(backend)


def test_incremental_registration_rejects_duplicate_role(tmp_path: Path) -> None:
    first = _sleeper()
    second = _sleeper()
    registry = tmp_path / "ide_processes.json"
    try:
        register_process(
            root=tmp_path,
            registry_path=registry,
            role="backend",
            pid=first.pid,
            marker="time.sleep",
            port_key="backend",
            port=8010,
        )
        with pytest.raises(ProcessRegistryError, match="role_already_registered"):
            register_process(
                root=tmp_path,
                registry_path=registry,
                role="backend",
                pid=second.pid,
                marker="time.sleep",
                port_key="backend",
                port=8011,
            )
        assert second.poll() is None
    finally:
        _reap(first)
        _reap(second)


def test_incremental_registration_keeps_new_process_when_first_died(
    tmp_path: Path,
) -> None:
    backend = _sleeper()
    frontend = _sleeper()
    registry = tmp_path / "ide_processes.json"
    try:
        register_process(
            root=tmp_path,
            registry_path=registry,
            role="backend",
            pid=backend.pid,
            marker="time.sleep",
            port_key="backend",
            port=8010,
        )
        _reap(backend)

        result = register_process(
            root=tmp_path,
            registry_path=registry,
            role="frontend",
            pid=frontend.pid,
            marker="time.sleep",
            port_key="frontend",
            port=9490,
        )
        stopped = stop_registered(root=tmp_path, registry_path=registry, timeout=2)

        frontend.wait(timeout=5)
        assert result["process_count"] == 2
        assert stopped["ok"] is True
        assert frontend.pid in stopped["stopped"]
        assert not registry.exists()
    finally:
        _reap(backend)
        _reap(frontend)


def test_assert_clear_recovers_registry_after_owned_process_died(
    tmp_path: Path,
) -> None:
    process = _sleeper()
    registry = tmp_path / "ide_processes.json"
    register_process(
        root=tmp_path,
        registry_path=registry,
        role="backend",
        pid=process.pid,
        marker="time.sleep",
        port_key="backend",
        port=8010,
    )
    _reap(process)

    result = assert_clear(root=tmp_path, registry_path=registry)

    assert result["status"] == "stale_registry_removed"
    assert not registry.exists()


def test_invalid_registry_fails_closed_without_touching_process(
    tmp_path: Path,
) -> None:
    process = _sleeper()
    registry = tmp_path / "ide_processes.json"
    registry.write_text("{not-json", encoding="utf-8")
    try:
        with pytest.raises(ProcessRegistryError, match="registry_invalid"):
            stop_registered(root=tmp_path, registry_path=registry)
        assert process.poll() is None
        assert registry.exists()
    finally:
        _reap(process)
