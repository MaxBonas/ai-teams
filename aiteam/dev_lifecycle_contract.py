from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from aiteam.platform_runtime import platform_id


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_VERSION = "dev_lifecycle_v1"
DEFAULT_CONTRACT_PATH = ROOT / "config" / "dev_lifecycle.v1.json"
ACTION_IDS = ("prepare", "start", "stop", "test", "migrate")
RECOVERY_CASE_IDS = (
    "unicode_checkout",
    "missing_dependency_lock",
    "bootstrap_lock_contention",
    "repeated_bootstrap",
    "occupied_port",
    "repeated_start",
    "partial_process_loss",
    "stale_process_registry",
    "corrupt_process_registry",
    "repeated_stop",
)


def load_dev_lifecycle_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or DEFAULT_CONTRACT_PATH
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    validate_dev_lifecycle_contract(payload, root=contract_path.parent.parent)
    return payload


def validate_dev_lifecycle_contract(
    payload: Mapping[str, Any],
    *,
    root: Path = ROOT,
) -> None:
    if set(payload) != {
        "schema_version",
        "contract_status",
        "actions",
        "recovery_matrix",
        "invariants",
        "known_gaps",
    }:
        raise ValueError("dev lifecycle contract fields drift")
    if payload.get("schema_version") != CONTRACT_VERSION:
        raise ValueError("unsupported dev lifecycle contract")
    if payload.get("contract_status") not in {"preview", "verified"}:
        raise ValueError("invalid dev lifecycle status")
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError("dev lifecycle actions must be a list")
    ids = [str(action.get("id") or "") for action in actions]
    if tuple(ids) != ACTION_IDS:
        raise ValueError("dev lifecycle action order or coverage drift")
    for action in actions:
        if set(action) != {
            "id",
            "purpose",
            "mutation_scope",
            "windows",
            "posix",
            "idempotency",
            "detached",
        }:
            raise ValueError("dev lifecycle action fields drift")
        if action.get("detached") is not False:
            raise ValueError("detached lifecycle is not governed yet")
        for target in ("windows", "posix"):
            command = action.get(target)
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(item, str) or not item for item in command)
            ):
                raise ValueError(f"invalid {target} lifecycle command")
            _validate_frontend_path(command, root=root)
    recovery_matrix = payload.get("recovery_matrix")
    if not isinstance(recovery_matrix, list):
        raise ValueError("dev lifecycle recovery matrix must be a list")
    if tuple(str(item.get("id") or "") for item in recovery_matrix) != RECOVERY_CASE_IDS:
        raise ValueError("dev lifecycle recovery matrix coverage drift")
    allowed_statuses = {"verified", "contract_tested", "preview"}
    for item in recovery_matrix:
        if set(item) != {"id", "windows", "posix", "invariant", "evidence"}:
            raise ValueError("dev lifecycle recovery case fields drift")
        if item["windows"] not in allowed_statuses or item["posix"] not in allowed_statuses:
            raise ValueError("dev lifecycle recovery case status invalid")
        if not str(item["invariant"]).strip() or not str(item["evidence"]).strip():
            raise ValueError("dev lifecycle recovery evidence is required")
    invariants = payload.get("invariants")
    expected_invariants = {
        "workspace_local_python": True,
        "workspace_local_node_modules": True,
        "versioned_python_lock": "requirements-dev.lock",
        "versioned_frontend_lock": "ide-frontend/package-lock.json",
        "bootstrap_concurrency_lock": True,
        "process_identity_registry": "runtime/ide_processes.json",
        "stop_policy": "pid_create_time_and_command_marker",
        "global_dependency_install": False,
        "provider_cli_install": False,
        "provider_login": False,
        "inference": False,
        "migrate_apply_requires": "--apply",
        "posix_start_mode": "foreground_ctrl_c",
        "platform_promotion_requires_independent_receipt": True,
    }
    if invariants != expected_invariants:
        raise ValueError("dev lifecycle invariants drift")
    gaps = payload.get("known_gaps")
    if not isinstance(gaps, list) or not gaps or any(not str(item).strip() for item in gaps):
        raise ValueError("dev lifecycle known gaps are required")


def lifecycle_command(
    action_id: str,
    *,
    target_platform: str | None = None,
    contract: Mapping[str, Any] | None = None,
) -> list[str]:
    payload = dict(contract or load_dev_lifecycle_contract())
    validate_dev_lifecycle_contract(payload)
    os_id = target_platform or platform_id()
    platform_key = "windows" if os_id == "windows" else "posix"
    if os_id not in {"windows", "linux", "macos"}:
        raise ValueError(f"unsupported lifecycle platform: {os_id}")
    action = next(
        (item for item in payload["actions"] if item["id"] == action_id),
        None,
    )
    if action is None:
        raise ValueError(f"unsupported lifecycle action: {action_id}")
    return list(action[platform_key])


def lifecycle_manifest(
    *,
    target_platform: str | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(contract or load_dev_lifecycle_contract())
    os_id = target_platform or platform_id()
    return {
        "schema_version": CONTRACT_VERSION,
        "platform": os_id,
        "contract_status": payload["contract_status"],
        "commands": {
            action_id: lifecycle_command(
                action_id,
                target_platform=os_id,
                contract=payload,
            )
            for action_id in ACTION_IDS
        },
        "recovery_matrix": [dict(item) for item in payload["recovery_matrix"]],
        "invariants": dict(payload["invariants"]),
        "known_gaps": list(payload["known_gaps"]),
    }


def _validate_frontend_path(command: list[str], *, root: Path) -> None:
    candidates = command[1:] if command[0] in {"sh"} else command[:1]
    script = next(
        (
            item
            for item in candidates
            if item.endswith((".bat", ".sh", ".py"))
        ),
        None,
    )
    if script is None:
        raise ValueError("lifecycle command must reference a versioned frontend")
    normalized = script.replace("\\", "/")
    if normalized.startswith("/") or ".." in Path(normalized).parts:
        raise ValueError("lifecycle frontend must stay inside checkout")
    if not (Path(root) / normalized).is_file():
        raise ValueError(f"lifecycle frontend missing: {normalized}")
