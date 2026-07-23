from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from aiteam.installation_support import load_installation_support_contract
from aiteam.machine_doctor import (
    ROOT,
    build_machine_inventory,
    validate_machine_inventory,
)
from aiteam.platform_runtime import platform_id, resolve_executable
from aiteam.user_config import user_config_dir


RECEIPT_SCHEMA_VERSION = "machine_doctor_receipt_v1"
REMEDIATION_SCHEMA_VERSION = "machine_doctor_remediation_v1"
RECEIPT_SCHEMA_PATH = ROOT / "config" / "machine_doctor_receipt.v1.schema.json"
REMEDIATION_SCHEMA_PATH = ROOT / "config" / "machine_doctor_remediation.v1.schema.json"
_CHECKOUT_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "runtime",
    "venv",
}


def canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_machine_doctor_receipt(
    *,
    root: Path = ROOT,
    config_root: Path | None = None,
    inventory_builder: Callable[[], dict[str, Any]] | None = None,
    metadata_snapshot: Callable[[Path, set[str]], Mapping[str, Any]] | None = None,
    cli_snapshot: Callable[[], Mapping[str, bool]] | None = None,
) -> dict[str, Any]:
    """Run discovery between opaque snapshots and return a deterministic receipt."""
    snapshot = metadata_snapshot or _metadata_snapshot
    local_config = config_root or user_config_dir()
    observe_clis = cli_snapshot or _known_cli_inventory
    checkout_before = snapshot(Path(root), _CHECKOUT_EXCLUDED_DIRS)
    config_before = snapshot(Path(local_config), set())
    clis_before = dict(observe_clis())

    report = (
        inventory_builder()
        if inventory_builder is not None
        else build_machine_inventory(root=Path(root))
    )
    validate_machine_inventory(report)

    checkout_after = snapshot(Path(root), _CHECKOUT_EXCLUDED_DIRS)
    config_after = snapshot(Path(local_config), set())
    clis_after = dict(observe_clis())
    guard = {
        "checkout": _surface_result(checkout_before, checkout_after),
        "user_config": _surface_result(config_before, config_after),
        "known_cli_inventory": {
            "observation": "path_presence_only",
            "unchanged": clis_before == clis_after,
            "entries_before": len(clis_before),
            "entries_after": len(clis_after),
        },
    }
    core: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "doctor_schema_version": report["schema_version"],
        "report_sha256": canonical_sha256(report),
        "report": report,
        "contract": {
            "discovery_entrypoint": "scripts/machine_doctor.py",
            "read_only": True,
            "secret_contents_read_by_guard": False,
            "install_commands_allowed": False,
            "login_commands_allowed": False,
            "inference_allowed": False,
            "receipt_write_is_explicit_and_outside_discovery": True,
            "remediation_entrypoint": "scripts/machine_doctor_remediate.py",
        },
        "mutation_guard": {
            "verified": all(surface["unchanged"] for surface in guard.values()),
            "surfaces": guard,
        },
    }
    receipt = {**core, "receipt_id": canonical_sha256(core)}
    validate_machine_doctor_receipt(receipt)
    return receipt


def build_remediation_plan(
    report: Mapping[str, Any],
    *,
    action_code: str,
    subject_id: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic guided-manual plan; this function never applies it."""
    validate_machine_inventory(report)
    matches = [
        item
        for item in report["diagnostics"]
        if item["next_action"]["code"] == action_code
        and (subject_id is None or item["subject_id"] == subject_id)
    ]
    if not matches:
        raise ValueError("requested remediation action is not present in report")
    signatures = {
        (
            item["next_action"]["description"],
            item["next_action"]["requires_human"],
            item["next_action"]["mutates_state"],
        )
        for item in matches
    }
    if len(signatures) != 1:
        raise ValueError("remediation action has divergent contracts")
    description, requires_human, mutates_state = next(iter(signatures))
    core: dict[str, Any] = {
        "schema_version": REMEDIATION_SCHEMA_VERSION,
        "mode": "guided_manual",
        "applied": False,
        "report_sha256": canonical_sha256(report),
        "action": {
            "code": action_code,
            "description": description,
            "requires_human": requires_human,
            "mutates_state": mutates_state,
            "targets": sorted(
                (
                    {
                        "subject_kind": item["subject_kind"],
                        "subject_id": item["subject_id"],
                        "diagnostic_code": item["code"],
                    }
                    for item in matches
                ),
                key=lambda item: (
                    item["subject_kind"],
                    item["subject_id"],
                    item["diagnostic_code"],
                ),
            ),
        },
        "execution": {
            "status": "not_executed",
            "reason": "manual_or_future_governed_executor_required",
        },
    }
    plan = {**core, "receipt_id": canonical_sha256(core)}
    validate_remediation_plan(plan)
    return plan


def write_explicit_receipt(
    path: Path,
    payload: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> None:
    """Persist only after an explicit output path and overwrite decision."""
    target = Path(path)
    if not target.parent.is_dir():
        raise ValueError("receipt parent directory must already exist")
    if target.exists() and not overwrite:
        raise FileExistsError("receipt exists; pass --force to replace it")
    temporary = target.with_name(f".{target.name}.tmp")
    if temporary.exists():
        raise FileExistsError("temporary receipt path already exists")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_machine_doctor_receipt(receipt: Mapping[str, Any]) -> None:
    _validate_schema_header(RECEIPT_SCHEMA_PATH, RECEIPT_SCHEMA_VERSION)
    if set(receipt) != {
        "schema_version",
        "doctor_schema_version",
        "report_sha256",
        "report",
        "contract",
        "mutation_guard",
        "receipt_id",
    }:
        raise ValueError("machine doctor receipt fields drift")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ValueError("machine doctor receipt schema drift")
    report = receipt.get("report")
    if not isinstance(report, dict):
        raise ValueError("machine doctor receipt report must be an object")
    validate_machine_inventory(report)
    if receipt.get("report_sha256") != canonical_sha256(report):
        raise ValueError("machine doctor receipt report hash drift")
    expected_contract = {
        "discovery_entrypoint": "scripts/machine_doctor.py",
        "read_only": True,
        "secret_contents_read_by_guard": False,
        "install_commands_allowed": False,
        "login_commands_allowed": False,
        "inference_allowed": False,
        "receipt_write_is_explicit_and_outside_discovery": True,
        "remediation_entrypoint": "scripts/machine_doctor_remediate.py",
    }
    if receipt.get("contract") != expected_contract:
        raise ValueError("machine doctor receipt contract drift")
    core = {key: value for key, value in receipt.items() if key != "receipt_id"}
    if receipt.get("receipt_id") != canonical_sha256(core):
        raise ValueError("machine doctor receipt id drift")
    guard = receipt.get("mutation_guard")
    if not isinstance(guard, dict) or set(guard) != {"verified", "surfaces"}:
        raise ValueError("machine doctor mutation guard drift")
    surfaces = guard.get("surfaces")
    if not isinstance(surfaces, dict) or set(surfaces) != {
        "checkout",
        "user_config",
        "known_cli_inventory",
    }:
        raise ValueError("machine doctor mutation surfaces drift")
    expected = all(bool(surface.get("unchanged")) for surface in surfaces.values())
    if guard.get("verified") is not expected:
        raise ValueError("machine doctor mutation verdict drift")
    for surface_id, surface in surfaces.items():
        expected_observation = (
            "path_presence_only"
            if surface_id == "known_cli_inventory"
            else "metadata_only"
        )
        if not isinstance(surface, dict) or set(surface) != {
            "observation",
            "unchanged",
            "entries_before",
            "entries_after",
        }:
            raise ValueError("machine doctor mutation surface fields drift")
        if surface.get("observation") != expected_observation:
            raise ValueError("machine doctor mutation observation drift")
        if not isinstance(surface.get("entries_before"), int) or not isinstance(
            surface.get("entries_after"), int
        ):
            raise ValueError("machine doctor mutation counts drift")


def validate_remediation_plan(plan: Mapping[str, Any]) -> None:
    _validate_schema_header(REMEDIATION_SCHEMA_PATH, REMEDIATION_SCHEMA_VERSION)
    if set(plan) != {
        "schema_version",
        "mode",
        "applied",
        "report_sha256",
        "action",
        "execution",
        "receipt_id",
    }:
        raise ValueError("machine doctor remediation fields drift")
    if plan.get("schema_version") != REMEDIATION_SCHEMA_VERSION:
        raise ValueError("machine doctor remediation schema drift")
    if plan.get("mode") != "guided_manual" or plan.get("applied") is not False:
        raise ValueError("machine doctor remediation must remain plan-only")
    if plan.get("execution") != {
        "status": "not_executed",
        "reason": "manual_or_future_governed_executor_required",
    }:
        raise ValueError("machine doctor remediation execution drift")
    action = plan.get("action")
    if not isinstance(action, dict) or set(action) != {
        "code",
        "description",
        "requires_human",
        "mutates_state",
        "targets",
    }:
        raise ValueError("machine doctor remediation action drift")
    targets = action.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("machine doctor remediation targets drift")
    if any(
        not isinstance(target, dict)
        or set(target) != {"subject_kind", "subject_id", "diagnostic_code"}
        for target in targets
    ):
        raise ValueError("machine doctor remediation target fields drift")
    core = {key: value for key, value in plan.items() if key != "receipt_id"}
    if plan.get("receipt_id") != canonical_sha256(core):
        raise ValueError("machine doctor remediation id drift")


def _surface_result(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "observation": "metadata_only",
        "unchanged": before == after,
        "entries_before": len(before),
        "entries_after": len(after),
    }


def _metadata_snapshot(root: Path, excluded_dirs: set[str]) -> dict[str, Any]:
    """Observe names and stat metadata only; never open file contents."""
    if not root.exists():
        return {}
    entries: dict[str, Any] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(
            item for item in dirs if item not in excluded_dirs
        )
        current_path = Path(current)
        for name in sorted([*dirs, *files]):
            candidate = current_path / name
            try:
                stat = candidate.stat(follow_symlinks=False)
            except OSError:
                entries[candidate.relative_to(root).as_posix()] = ["stat_error"]
                continue
            entries[candidate.relative_to(root).as_posix()] = [
                "directory" if candidate.is_dir() else "file",
                stat.st_size,
                stat.st_mtime_ns,
            ]
    return entries


def _known_cli_inventory() -> dict[str, bool]:
    support = load_installation_support_contract()
    os_id = platform_id()
    return {
        str(item["id"]): any(
            resolve_executable(str(candidate), os_id=os_id)
            for candidate in item["commands"]
        )
        for item in support["adapters"]
    }


def _validate_schema_header(path: Path, expected_title: str) -> None:
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("title") != expected_title:
        raise ValueError("machine doctor auxiliary schema drift")
    if schema.get("additionalProperties") is not False:
        raise ValueError("machine doctor auxiliary schema must fail closed")
    required = schema.get("required")
    if not isinstance(required, list) or len(required) != len(set(required)):
        raise ValueError("machine doctor auxiliary schema required fields drift")
