from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

import psutil


SCHEMA_VERSION = "dev_process_registry_v1"
DEFAULT_REGISTRY = Path("runtime") / "ide_processes.json"


class ProcessRegistryError(RuntimeError):
    pass


def _root_id(root: Path) -> str:
    normalized = os.path.normcase(str(root.resolve())).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_registry(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProcessRegistryError(f"process_registry_invalid: {exc}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ProcessRegistryError("process_registry_schema_unsupported")
    if not isinstance(payload.get("processes"), list):
        raise ProcessRegistryError("process_registry_processes_invalid")
    return payload


def _matches(process: psutil.Process, record: dict[str, Any]) -> tuple[bool, str]:
    try:
        observed_time = process.create_time()
        command = " ".join(process.cmdline()).casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
        return False, type(exc).__name__.casefold()

    expected_time = float(record.get("create_time", -1))
    if abs(observed_time - expected_time) > 0.01:
        return False, "create_time_mismatch"
    marker = str(record.get("command_marker", "")).strip().casefold()
    if not marker or marker not in command:
        return False, "command_marker_mismatch"
    return True, "owned"


def _record(pid: int, role: str, marker: str) -> dict[str, Any]:
    if pid <= 0 or not role.strip() or not marker.strip():
        raise ProcessRegistryError("process_spec_invalid")
    try:
        process = psutil.Process(pid)
        create_time = process.create_time()
        command = " ".join(process.cmdline()).casefold()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as exc:
        raise ProcessRegistryError(f"process_not_observable:{role}:{pid}") from exc
    if marker.casefold() not in command:
        raise ProcessRegistryError(f"process_marker_not_observed:{role}:{pid}")
    return {
        "role": role,
        "pid": pid,
        "create_time": create_time,
        "command_marker": marker,
    }


def assert_clear(*, root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or root / DEFAULT_REGISTRY
    payload = _read_registry(path)
    if payload is None:
        return {"ok": True, "status": "clear"}
    if payload.get("root_id") != _root_id(root):
        raise ProcessRegistryError("process_registry_root_mismatch")

    owned_live: list[str] = []
    for record in payload["processes"]:
        try:
            process = psutil.Process(int(record["pid"]))
        except (KeyError, TypeError, ValueError, psutil.NoSuchProcess):
            continue
        matches, _reason = _matches(process, record)
        if matches:
            owned_live.append(str(record.get("role", "unknown")))
    if owned_live:
        raise ProcessRegistryError(
            "owned_processes_already_running:" + ",".join(sorted(owned_live))
        )
    path.unlink(missing_ok=True)
    return {"ok": True, "status": "stale_registry_removed"}


def register_processes(
    *,
    root: Path,
    process_specs: Iterable[tuple[str, int, str]],
    ports: dict[str, int],
    registry_path: Path | None = None,
) -> dict[str, Any]:
    path = registry_path or root / DEFAULT_REGISTRY
    assert_clear(root=root, registry_path=path)
    records = [_record(pid, role, marker) for role, pid, marker in process_specs]
    if not records:
        raise ProcessRegistryError("process_registry_empty")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "root_id": _root_id(root),
        "registered_at": time.time(),
        "ports": {key: int(value) for key, value in sorted(ports.items())},
        "processes": records,
    }
    _atomic_json(path, payload)
    return {"ok": True, "status": "registered", "process_count": len(records)}


def register_process(
    *,
    root: Path,
    role: str,
    pid: int,
    marker: str,
    port_key: str,
    port: int,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    path = registry_path or root / DEFAULT_REGISTRY
    payload = _read_registry(path)
    if payload is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "root_id": _root_id(root),
            "registered_at": time.time(),
            "ports": {},
            "processes": [],
        }
    else:
        if payload.get("root_id") != _root_id(root):
            raise ProcessRegistryError("process_registry_root_mismatch")
        for existing in payload["processes"]:
            try:
                existing_pid = int(existing["pid"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProcessRegistryError("process_registry_entry_invalid") from exc
            try:
                process = psutil.Process(existing_pid)
            except psutil.NoSuchProcess:
                continue
            matches, reason = _matches(process, existing)
            if not matches:
                raise ProcessRegistryError(
                    f"process_registry_identity_mismatch:{reason}"
                )
        if any(str(item.get("role")) == role for item in payload["processes"]):
            raise ProcessRegistryError(f"process_role_already_registered:{role}")

    payload["processes"].append(_record(pid, role, marker))
    payload["ports"][port_key] = int(port)
    payload["processes"].sort(key=lambda item: str(item["role"]))
    payload["ports"] = dict(sorted(payload["ports"].items()))
    _atomic_json(path, payload)
    return {
        "ok": True,
        "status": "registered",
        "process_count": len(payload["processes"]),
    }


def _terminate_tree(process: psutil.Process, timeout: float) -> list[int]:
    try:
        descendants = process.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        descendants = []
    targets = [*reversed(descendants), process]
    for target in targets:
        try:
            target.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _gone, alive = psutil.wait_procs(targets, timeout=timeout)
    for target in alive:
        try:
            target.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if alive:
        _gone, alive = psutil.wait_procs(alive, timeout=timeout)
    lingering = []
    for target in alive:
        try:
            if target.is_running() and target.status() != psutil.STATUS_ZOMBIE:
                lingering.append(target.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if lingering:
        raise ProcessRegistryError(
            "owned_processes_not_stopped:" + ",".join(map(str, sorted(lingering)))
        )
    return [target.pid for target in targets]


def stop_registered(
    *,
    root: Path,
    registry_path: Path | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    path = registry_path or root / DEFAULT_REGISTRY
    payload = _read_registry(path)
    if payload is None:
        return {"ok": True, "status": "not_running", "stopped": [], "mismatches": []}
    if payload.get("root_id") != _root_id(root):
        raise ProcessRegistryError("process_registry_root_mismatch")

    stopped: list[int] = []
    mismatches: list[dict[str, Any]] = []
    for record in payload["processes"]:
        try:
            process = psutil.Process(int(record["pid"]))
        except (KeyError, TypeError, ValueError, psutil.NoSuchProcess):
            continue
        matches, reason = _matches(process, record)
        if not matches:
            mismatches.append(
                {
                    "role": str(record.get("role", "unknown")),
                    "pid": int(record.get("pid", 0)),
                    "reason": reason,
                }
            )
            continue
        stopped.extend(_terminate_tree(process, timeout))

    if not mismatches:
        path.unlink(missing_ok=True)
    return {
        "ok": not mismatches,
        "status": "stopped" if not mismatches else "identity_mismatch",
        "stopped": sorted(set(stopped)),
        "mismatches": mismatches,
    }
