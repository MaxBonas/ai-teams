"""Readers seguros y loop de máquina para provider-change intelligence."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aiteam.db.provider_change_workflows import (
    reconcile_provider_change_cases,
)
from aiteam.db.provider_changes import (
    provider_component_key,
    register_provider_change_schedules,
    run_scheduled_provider_checks,
)
from aiteam.installation_support import load_installation_support_contract
from aiteam.machine_doctor import _probe_version_command
from aiteam.platform_runtime import resolve_provider_cli
from aiteam.provider_change_delivery import (
    deliver_provider_change_outbox,
    sync_provider_change_outbox,
)
from aiteam.provider_change_intelligence import (
    build_provider_change_inventory,
)
from aiteam.user_config import codex_catalog_snapshot, user_config_dir

DEFAULT_TICK_SEC = 60.0
DEFAULT_MAX_CHECKS = 3
logger = logging.getLogger(__name__)


def machine_provider_change_db_path() -> Path:
    return user_config_dir() / "guided_setup.db"


def build_safe_provider_change_runtime(
    *,
    command_probe: Callable[[list[str]], tuple[bool, str | None]] | None = None,
    codex_catalog_reader: Callable[[], Mapping[str, Any]] | None = None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, Callable[[], Mapping[str, Any]]],
]:
    """Construye readers locales que no abren login ni consultan releases."""
    inventory = build_provider_change_inventory()
    components = {
        provider_component_key(row): row for row in inventory["components"]
    }
    support = load_installation_support_contract()
    probe = command_probe or _probe_version_command
    catalog_reader = codex_catalog_reader or codex_catalog_snapshot
    readers: dict[str, Callable[[], Mapping[str, Any]]] = {}
    for key, component in components.items():
        surface = str(component["surface"])
        if surface == "cli_package":
            readers[key] = _cli_reader(
                component,
                support=support,
                command_probe=probe,
            )
        elif surface == "internal_adapter":
            readers[key] = _internal_adapter_reader(component)
        elif component["component_id"] == "catalog:codex_subscription":
            readers[key] = _codex_catalog_reader(catalog_reader)
    return components, readers


async def run_provider_change_monitor(
    *,
    db_path: Path | None = None,
    tick_sec: float = DEFAULT_TICK_SEC,
    max_checks: int = DEFAULT_MAX_CHECKS,
) -> None:
    """Registra y drena únicamente readers seguros hasta cancelación."""
    target = Path(db_path or machine_provider_change_db_path())
    components, readers = build_safe_provider_change_runtime()
    register_provider_change_schedules(
        target, list(components.values())
    )
    while True:
        try:
            await asyncio.to_thread(
                run_scheduled_provider_checks,
                target,
                components,
                readers,
                max_checks=max_checks,
            )
            await asyncio.to_thread(
                reconcile_provider_change_cases,
                target,
            )
            await asyncio.to_thread(sync_provider_change_outbox, target)
            await asyncio.to_thread(deliver_provider_change_outbox, target)
        except Exception:
            logger.exception(
                "provider change monitor tick failed; retrying next cadence"
            )
        await asyncio.sleep(max(5.0, float(tick_sec)))


def _cli_reader(
    component: Mapping[str, Any],
    *,
    support: Mapping[str, Any],
    command_probe: Callable[[list[str]], tuple[bool, str | None]],
) -> Callable[[], Mapping[str, Any]]:
    raw_id = str(component["component_id"]).split(":", 1)[1]
    cli_id = {
        "antigravity": "agy",
        "lmstudio": "lms",
    }.get(raw_id, raw_id)
    adapter = next(
        (
            row
            for row in support["adapters"]
            if str(row.get("cli_id") or row.get("id")) == cli_id
        ),
        None,
    )
    commands = (
        list(adapter["commands"])
        if adapter
        else [f"{raw_id}.cmd", raw_id]
    )
    version_args = (
        list(adapter.get("version_args") or ["--version"])
        if adapter
        else ["--version"]
    )

    def read() -> Mapping[str, Any]:
        executable = resolve_provider_cli(cli_id, commands)
        installed, version = (
            command_probe([executable, *version_args])
            if executable
            else (False, None)
        )
        return {
            "status": "observed",
            "installed_version": version if installed else None,
            "latest_known_version": None,
            "compatibility": {
                "installed": "unknown",
                "latest_known": "unknown",
            },
            "dimensions": {
                "executable_contract": Path(executable).name
                if executable
                else None,
            },
        }

    return read


def _internal_adapter_reader(
    component: Mapping[str, Any],
) -> Callable[[], Mapping[str, Any]]:
    supported = component["facts"]["supported_version"].get("value")
    dimensions = {
        name: _source_digest(reference)
        for name, reference in {
            "adapter_contract": "aiteam/adapters/registry.py",
            "config_schema": "aiteam/user_config.py",
            "translation_contract": str(
                component["facts"]["supported_version"]["source"][
                    "reference"
                ]
            ),
        }.items()
    }

    def read() -> Mapping[str, Any]:
        return {
            "status": "observed",
            "installed_version": supported,
            "latest_known_version": None,
            "compatibility": {
                "installed": "compatible",
                "latest_known": "unknown",
            },
            "dimensions": dimensions,
        }

    return read


def _codex_catalog_reader(
    catalog_reader: Callable[[], Mapping[str, Any]],
) -> Callable[[], Mapping[str, Any]]:
    def read() -> Mapping[str, Any]:
        catalog = catalog_reader()
        status = str(catalog.get("status") or "")
        if status != "current":
            return {
                "status": (
                    "auth_required"
                    if status in {"not_authenticated", "auth_required"}
                    else "failed"
                )
            }
        models = []
        for raw in catalog.get("models") or []:
            model_id = (
                str(raw.get("id") or raw.get("value") or "").strip()
                if isinstance(raw, Mapping)
                else str(raw).strip()
            )
            if model_id:
                models.append({"id": model_id})
        version = str(
            catalog.get("catalog_client_version")
            or catalog.get("installed_version")
            or ""
        ).strip()
        return {
            "status": "observed",
            "installed_version": version or None,
            "latest_known_version": version or None,
            "compatibility": {
                "installed": "unknown",
                "latest_known": "unknown",
            },
            "dimensions": {"model_id": models},
        }

    return read


def _source_digest(reference: str) -> str:
    path = Path(__file__).resolve().parents[1] / reference
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()
