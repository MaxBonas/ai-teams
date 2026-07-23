from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = ROOT / "config" / "installation_support.v1.json"
SUPPORT_STATUSES = {"verified", "preview", "planned", "unsupported"}


def load_installation_support_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or DEFAULT_CONTRACT_PATH
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "installation_support_v1":
        raise ValueError("unsupported installation support schema")
    if set(payload.get("statuses") or []) != SUPPORT_STATUSES:
        raise ValueError("installation support statuses drift")
    acceptance = payload.get("acceptance_contract")
    if not isinstance(acceptance, dict):
        raise ValueError("acceptance_contract must be an object")
    if acceptance.get("schema_version") != "windows_clean_room_acceptance_v1":
        raise ValueError("unsupported clean-room acceptance schema")
    required_steps = acceptance.get("required_steps")
    if (
        not isinstance(required_steps, list)
        or not required_steps
        or len(required_steps) != len(set(required_steps))
    ):
        raise ValueError("acceptance_contract required_steps are invalid")
    for collection in ("distributions", "platforms", "runtimes", "adapters"):
        rows = payload.get(collection)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{collection} must be a non-empty list")
        ids = [str(row.get("id") or "") for row in rows if isinstance(row, dict)]
        if len(ids) != len(rows) or any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"{collection} contains invalid or duplicate ids")
    for row in payload["platforms"]:
        if row.get("status") not in SUPPORT_STATUSES:
            raise ValueError(f"invalid platform status: {row.get('id')}")
    for row in payload["adapters"]:
        if row.get("automatic_install") is not False:
            raise ValueError(f"adapter must not install automatically: {row.get('id')}")
    return payload


def _version_tuple(value: str | None) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+|\d+", str(value or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def version_meets_minimum(observed: str | None, minimum: str | None) -> bool:
    if not minimum:
        return bool(observed)
    actual = _version_tuple(observed)
    required = _version_tuple(minimum)
    if not actual or not required:
        return False
    width = max(len(actual), len(required))
    return actual + (0,) * (width - len(actual)) >= required + (0,) * (width - len(required))


def _resolve_command(candidates: list[str]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _observe_version(candidates: list[str], args: list[str]) -> str | None:
    executable = _resolve_command(candidates)
    if not executable:
        return None
    try:
        proc = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return "installed_version_unavailable"
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return "installed_version_unavailable"
    return output.splitlines()[0].strip() if output else "installed_version_unavailable"


def _host_identity() -> tuple[str, str]:
    os_id = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    return os_id, architecture


def audit_installation_support(
    contract: dict[str, Any] | None = None,
    *,
    observed_versions: Mapping[str, str | None] | None = None,
    host: tuple[str, str] | None = None,
) -> dict[str, Any]:
    support = contract or load_installation_support_contract()
    os_id, architecture = host or _host_identity()
    supplied = dict(observed_versions) if observed_versions is not None else None

    def observe(item_id: str, commands: list[str], args: list[str]) -> str | None:
        if supplied is not None:
            return supplied.get(item_id)
        if item_id == "python":
            return platform.python_version()
        return _observe_version(commands, args)

    runtimes: list[dict[str, Any]] = []
    for item in support["runtimes"]:
        applies = item["requirement"] != "required_on_windows" or os_id == "windows"
        version = observe(item["id"], item["commands"], item["version_args"]) if applies else None
        ready = (not applies) or version_meets_minimum(version, item.get("minimum_version"))
        runtimes.append(
            {
                "id": item["id"],
                "requirement": item["requirement"],
                "applies": applies,
                "installed": version is not None,
                "version": version,
                "minimum_version": item.get("minimum_version"),
                "ready": ready,
            }
        )

    adapters: list[dict[str, Any]] = []
    for item in support["adapters"]:
        version = observe(item["id"], item["commands"], item["version_args"])
        adapters.append(
            {
                "id": item["id"],
                "setup_class": item["setup_class"],
                "installed": version is not None,
                "version": version,
                "auth_status": "not_checked",
                "automatic_install": False,
            }
        )

    platform_row = next(
        (
            item
            for item in support["platforms"]
            if item.get("os") == os_id and item.get("architecture") == architecture
        ),
        None,
    )
    primary_installed = any(
        item["installed"] and item["setup_class"] == "primary_option" for item in adapters
    )
    missing_required = [
        item["id"] for item in runtimes if item["applies"] and not item["ready"]
    ]
    next_actions: list[str] = []
    if missing_required:
        next_actions.append("Instala o actualiza runtimes requeridos: " + ", ".join(missing_required))
    if not primary_installed:
        next_actions.append(
            "Instala y autentica al menos una opción primaria (Codex o Antigravity), "
            "o configura una API Lead-capable desde la UI."
        )
    else:
        next_actions.append(
            "Prueba en Config el adapter primario instalado; presencia no demuestra auth ni health."
        )
    if not any(item["id"] == "opencode_zen_free" and item["installed"] for item in adapters):
        next_actions.append(
            "OpenCode Zen es opcional: instálalo solo si quieres el carril económico temporal."
        )

    return {
        "schema_version": "installation_support_audit_v1",
        "contract_updated_at": support["updated_at"],
        "acceptance_contract": {
            "schema_version": support["acceptance_contract"]["schema_version"],
            "platform_id": support["acceptance_contract"]["platform_id"],
            "workflow": support["acceptance_contract"]["workflow"],
        },
        "host": {
            "os": os_id,
            "architecture": architecture,
            "support_status": platform_row.get("status") if platform_row else "unsupported",
            "support_id": platform_row.get("id") if platform_row else None,
        },
        "control_plane_ready": not missing_required,
        "live_runs": {
            "status": (
                "adapter_installed_auth_health_required"
                if primary_installed
                else "primary_adapter_required"
            ),
            "ready": False,
            "reason": "Este auditor I.1 no ejecuta login ni canarios vivos.",
        },
        "runtimes": runtimes,
        "adapters": adapters,
        "next_actions": next_actions,
    }


def render_installation_summary(report: dict[str, Any]) -> str:
    host = report["host"]
    lines = [
        (
            f"[installation_support] {host['support_id'] or 'host desconocido'}: "
            f"{host['support_status']}"
        ),
        (
            "[installation_support] Control plane: "
            + ("listo" if report["control_plane_ready"] else "faltan requisitos")
        ),
    ]
    for item in report["adapters"]:
        marker = "instalado" if item["installed"] else "ausente"
        lines.append(
            f"[installation_support] Adapter {item['id']} ({item['setup_class']}): {marker}"
        )
    lines.extend(f"[installation_support] Siguiente: {action}" for action in report["next_actions"])
    lines.append(
        "[installation_support] Ollama/LM Studio son opcionales y nunca se instalan automáticamente."
    )
    return "\n".join(lines)
