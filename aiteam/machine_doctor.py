from __future__ import annotations

import json
import os
import platform
import re
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from aiteam.installation_support import (
    load_installation_support_contract,
    version_meets_minimum,
)
from aiteam.platform_runtime import (
    architecture_id,
    platform_id,
    resolve_executable,
    run_command,
)
from aiteam.user_config import load_adapter_profiles


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "machine_doctor_v1"
DEFAULT_SCHEMA_PATH = ROOT / "config" / "machine_doctor.v1.schema.json"
DEFAULT_PORTS = (
    ("backend", 8010),
    ("frontend", 9490),
)
_BASE_RUNTIME_IDS = ("python", "node", "npm", "git", "powershell", "sqlite")
_TOOLCHAIN_PROBES = (
    ("python", ("pyproject.toml", "requirements.txt", "setup.py"), ("python",), ("--version",)),
    ("javascript", ("package.json",), ("node",), ("--version",)),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts"), ("java",), ("--version",)),
    ("kotlin", ("build.gradle.kts", "settings.gradle.kts"), ("kotlinc",), ("-version",)),
    ("go", ("go.mod",), ("go",), ("version",)),
    ("rust", ("Cargo.toml",), ("rustc",), ("--version",)),
    ("dotnet", ("*.sln", "*.csproj", "*.fsproj"), ("dotnet",), ("--version",)),
    ("php", ("composer.json",), ("php",), ("--version",)),
    ("ruby", ("Gemfile",), ("ruby",), ("--version",)),
    ("swift", ("Package.swift",), ("swift",), ("--version",)),
    (
        "containers",
        ("Dockerfile", "compose.yaml", "docker-compose.yml", ".devcontainer/devcontainer.json"),
        ("docker",),
        ("--version",),
    ),
)
_SAFE_ENV_KEYS = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}


def load_machine_doctor_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or DEFAULT_SCHEMA_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("title") != SCHEMA_VERSION:
        raise ValueError("unsupported machine doctor schema")
    if schema.get("additionalProperties") is not False:
        raise ValueError("machine doctor schema must fail closed")
    required = schema.get("required")
    if not isinstance(required, list) or len(required) != len(set(required)):
        raise ValueError("machine doctor schema has invalid required fields")
    return schema


def build_machine_inventory(
    *,
    root: Path = ROOT,
    support_contract: Mapping[str, Any] | None = None,
    command_probe: Callable[[list[str]], tuple[bool, str | None]] | None = None,
    port_probe: Callable[[int], str] | None = None,
    host: tuple[str, str, str] | None = None,
    adapter_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the read-only machine inventory without reading secret values."""
    support = dict(support_contract or load_installation_support_contract())
    os_id, architecture, release = host or (
        platform_id(),
        architecture_id(),
        platform.release(),
    )
    probe_command = command_probe or _probe_version_command
    probe_port = port_probe or _probe_loopback_port
    platform_row = next(
        (
            row
            for row in support["platforms"]
            if row.get("os") == os_id and row.get("architecture") == architecture
        ),
        None,
    )

    runtimes: list[dict[str, Any]] = []
    for runtime in support["runtimes"]:
        applies = runtime["requirement"] != "required_on_windows" or os_id == "windows"
        if runtime["id"] == "python":
            installed = True
            version = platform.python_version()
            executable = Path(sys.executable).name
            source = "current_process"
        elif not applies:
            installed = False
            version = None
            executable = None
            source = "path_lookup"
        else:
            resolved = _resolve_runtime(runtime["commands"], os_id=os_id)
            executable = Path(resolved).name if resolved else None
            installed, version = (
                probe_command([resolved, *runtime["version_args"]])
                if resolved
                else (False, None)
            )
            source = "path_lookup"
        runtimes.append(
            {
                "id": runtime["id"],
                "requirement": runtime["requirement"],
                "installed": installed,
                "version": version,
                "minimum_version": runtime.get("minimum_version"),
                "ready": (not applies)
                or (
                    installed
                    and version_meets_minimum(version, runtime.get("minimum_version"))
                ),
                "source": source,
                "executable": executable,
            }
        )
    runtimes.append(
        {
            "id": "sqlite",
            "requirement": "embedded",
            "installed": True,
            "version": sqlite3.sqlite_version,
            "minimum_version": None,
            "ready": True,
            "source": "stdlib",
            "executable": None,
        }
    )

    ports = [
        {
            "id": probe_id,
            "port": port,
            "interface": "loopback",
            "state": probe_port(port),
            "source": "loopback_connect",
        }
        for probe_id, port in DEFAULT_PORTS
    ]
    checkout = Path(root)
    permissions = [
        {
            "id": "checkout_root",
            "exists": checkout.is_dir(),
            "readable": os.access(checkout, os.R_OK),
            "writable": os.access(checkout, os.W_OK),
            "searchable": os.access(checkout, os.X_OK),
            "source": "os_access",
        }
    ]
    toolchains = _observe_toolchains(
        checkout,
        os_id=os_id,
        command_probe=probe_command,
    )
    profiles = load_adapter_profiles() if adapter_profiles is None else adapter_profiles
    adapters = _observe_adapter_profiles(
        profiles,
        support=support,
        os_id=os_id,
        command_probe=probe_command,
    )
    missing_required = sorted(
        item["id"]
        for item in runtimes
        if item["requirement"] in {"required", "required_on_windows"}
        and not item["ready"]
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
            "personal_paths_emitted": False,
        },
        "host": {
            "os": os_id,
            "architecture": architecture,
            "release": str(release),
            "support_id": platform_row.get("id") if platform_row else None,
            "support_status": platform_row.get("status", "unlisted")
            if platform_row
            else "unlisted",
        },
        "runtimes": runtimes,
        "ports": ports,
        "permissions": permissions,
        "toolchains": toolchains,
        "adapters": adapters,
    }
    diagnostics = diagnose_machine_inventory(report)
    severity_counts = {
        severity: sum(item["severity"] == severity for item in diagnostics)
        for severity in ("blocker", "warning", "info")
    }
    report["diagnostics"] = diagnostics
    report["summary"] = {
        "inventory_complete": (
            {item["id"] for item in runtimes} == set(_BASE_RUNTIME_IDS)
            and all(item["state"] != "probe_error" for item in ports)
        ),
        "required_runtimes_ready": not missing_required,
        "missing_required": missing_required,
        "profiles_observed": len(adapters),
        "toolchains_manifest_detected": sorted(
            item["id"] for item in toolchains if item["manifest_detected"]
        ),
        "status": _overall_status(diagnostics),
        "strict_pass": not severity_counts["blocker"],
        "severity_counts": severity_counts,
        "next_action_codes": list(
            dict.fromkeys(item["next_action"]["code"] for item in diagnostics)
        ),
    }
    validate_machine_inventory(report)
    return report


def validate_machine_inventory(report: Mapping[str, Any]) -> None:
    """Small fail-closed validator for invariants not delegated to a dependency."""
    load_machine_doctor_schema()
    if set(report) != {
        "schema_version",
        "scope",
        "host",
        "runtimes",
        "ports",
        "permissions",
        "toolchains",
        "adapters",
        "diagnostics",
        "summary",
    }:
        raise ValueError("machine doctor report fields drift")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("machine doctor report schema drift")
    scope = report.get("scope")
    if scope != {
        "read_only": True,
        "secrets_read": False,
        "credentials_probed": False,
        "personal_paths_emitted": False,
    }:
        raise ValueError("machine doctor privacy scope drift")
    runtimes = report.get("runtimes")
    if not isinstance(runtimes, list):
        raise ValueError("machine doctor runtimes must be a list")
    runtime_ids = [str(item.get("id") or "") for item in runtimes]
    if set(runtime_ids) != set(_BASE_RUNTIME_IDS) or len(runtime_ids) != len(
        set(runtime_ids)
    ):
        raise ValueError("machine doctor runtime inventory drift")
    for runtime in runtimes:
        executable = runtime.get("executable")
        if executable and ("/" in executable or "\\" in executable):
            raise ValueError("machine doctor executable must not expose a path")
    ports = report.get("ports")
    if not isinstance(ports, list) or {
        (item.get("id"), item.get("port")) for item in ports
    } != set(DEFAULT_PORTS):
        raise ValueError("machine doctor port inventory drift")
    for collection in ("toolchains", "adapters"):
        if not isinstance(report.get(collection), list):
            raise ValueError(f"machine doctor {collection} must be a list")
    toolchain_ids = [str(item.get("id") or "") for item in report["toolchains"]]
    expected_toolchains = {item[0] for item in _TOOLCHAIN_PROBES}
    if set(toolchain_ids) != expected_toolchains or len(toolchain_ids) != len(
        set(toolchain_ids)
    ):
        raise ValueError("machine doctor toolchain inventory drift")
    adapter_ids = [str(item.get("id") or "") for item in report["adapters"]]
    if any(not item for item in adapter_ids) or len(adapter_ids) != len(set(adapter_ids)):
        raise ValueError("machine doctor adapter inventory drift")
    for item in [*report["toolchains"], *report["adapters"]]:
        serialized = json.dumps(item, ensure_ascii=False)
        if _contains_personal_path(serialized):
            raise ValueError("machine doctor observation must not expose a path")
    diagnostics = report.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise ValueError("machine doctor diagnostics must be a list")
    diagnostic_ids = [str(item.get("id") or "") for item in diagnostics]
    if any(not item for item in diagnostic_ids) or len(diagnostic_ids) != len(
        set(diagnostic_ids)
    ):
        raise ValueError("machine doctor diagnostic inventory drift")
    allowed_states = {
        "absent",
        "not_authenticated",
        "incompatible",
        "unverified",
        "degraded",
    }
    allowed_severities = {"blocker", "warning", "info"}
    for diagnostic in diagnostics:
        if diagnostic.get("state") not in allowed_states:
            raise ValueError("machine doctor diagnostic state drift")
        if diagnostic.get("severity") not in allowed_severities:
            raise ValueError("machine doctor diagnostic severity drift")
        action = diagnostic.get("next_action")
        if not isinstance(action, dict) or set(action) != {
            "code",
            "description",
            "requires_human",
            "mutates_state",
        }:
            raise ValueError("machine doctor next action drift")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("machine doctor summary must be an object")
    expected_counts = {
        severity: sum(item["severity"] == severity for item in diagnostics)
        for severity in ("blocker", "warning", "info")
    }
    if summary.get("severity_counts") != expected_counts:
        raise ValueError("machine doctor diagnostic counts drift")
    if summary.get("status") != _overall_status(diagnostics):
        raise ValueError("machine doctor overall status drift")
    if summary.get("strict_pass") is not (expected_counts["blocker"] == 0):
        raise ValueError("machine doctor strict status drift")
    expected_actions = list(
        dict.fromkeys(item["next_action"]["code"] for item in diagnostics)
    )
    if summary.get("next_action_codes") != expected_actions:
        raise ValueError("machine doctor next action summary drift")


def render_machine_inventory(report: Mapping[str, Any]) -> str:
    host = report["host"]
    lines = [
        "AI Teams — inventario base de máquina",
        (
            f"Host: {host['os']}/{host['architecture']} "
            f"({host['support_status']})"
        ),
    ]
    for runtime in report["runtimes"]:
        state = "listo" if runtime["ready"] else "ausente/incompatible"
        version = runtime["version"] or "no observado"
        lines.append(f"[{state}] {runtime['id']}: {version}")
    for port in report["ports"]:
        lines.append(f"[puerto] {port['id']}:{port['port']} {port['state']}")
    for adapter in report["adapters"]:
        cli = adapter["cli"]
        presence = "sin CLI" if cli is None else (
            "instalado" if cli["installed"] else "ausente"
        )
        lines.append(
            f"[adapter] {adapter['id']}: {presence}; "
            f"auth={adapter['authentication_status']}; "
            f"health={adapter['health_status']}"
        )
    detected = [item["id"] for item in report["toolchains"] if item["manifest_detected"]]
    lines.append(
        "[toolchains] manifests raíz: " + (", ".join(detected) if detected else "ninguno")
    )
    for diagnostic in report["diagnostics"]:
        lines.append(
            f"[{diagnostic['severity']}] {diagnostic['code']}: "
            f"{diagnostic['message']}"
        )
        lines.append(f"  Siguiente acción: {diagnostic['next_action']['description']}")
    lines.append(f"Resultado: {report['summary']['status']}")
    lines.append("No se leyeron secretos, credenciales ni paths personales.")
    return "\n".join(lines)


def _observe_toolchains(
    root: Path,
    *,
    os_id: str,
    command_probe: Callable[[list[str]], tuple[bool, str | None]],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for toolchain_id, patterns, commands, version_args in _TOOLCHAIN_PROBES:
        manifests = sorted(
            {
                candidate.name
                for pattern in patterns
                for candidate in root.glob(pattern)
                if candidate.is_file()
            }
        )
        resolved = _resolve_runtime(list(commands), os_id=os_id)
        installed, version = (
            command_probe([resolved, *version_args]) if resolved else (False, None)
        )
        observations.append(
            {
                "id": toolchain_id,
                "manifest_detected": bool(manifests),
                "manifests": manifests,
                "binary_installed": installed,
                "version": version,
                "executable": Path(resolved).name if resolved else None,
                "source": "root_manifest_and_path_lookup",
                "support_claim": False,
                "diagnostic_state": (
                    "not_detected"
                    if not manifests
                    else "absent"
                    if not installed
                    else "unverified"
                ),
            }
        )
    return observations


def _observe_adapter_profiles(
    profiles: list[dict[str, Any]],
    *,
    support: Mapping[str, Any],
    os_id: str,
    command_probe: Callable[[list[str]], tuple[bool, str | None]],
) -> list[dict[str, Any]]:
    support_by_id = {str(item["id"]): item for item in support["adapters"]}
    cli_cache: dict[str, dict[str, Any]] = {}

    def observe_cli(cli_id: str, candidates: list[str], args: list[str]) -> dict[str, Any]:
        cache_key = json.dumps([cli_id, candidates, args])
        if cache_key not in cli_cache:
            resolved = _resolve_runtime(candidates, os_id=os_id)
            installed, version = (
                command_probe([resolved, *args]) if resolved else (False, None)
            )
            cli_cache[cache_key] = {
                "id": cli_id,
                "installed": installed,
                "version": version,
                "executable": Path(resolved).name if resolved else None,
                "source": "path_lookup_version_only",
            }
        return dict(cli_cache[cache_key])

    observations: list[dict[str, Any]] = []
    for profile in profiles:
        profile_id = str(profile.get("id") or "").strip()
        if not profile_id:
            continue
        config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
        support_row = support_by_id.get(profile_id)
        command = config.get("command")
        command_candidates = (
            [str(command[0])]
            if isinstance(command, list) and command and str(command[0]).strip()
            else []
        )
        if support_row:
            command_candidates = list(support_row["commands"])
        cli = (
            observe_cli(
                str((support_row or {}).get("cli_id") or config.get("cli_kind") or command_candidates[0]),
                command_candidates,
                list((support_row or {}).get("version_args") or ["--version"]),
            )
            if command_candidates
            else None
        )
        provider_runtime = None
        provider = str(profile.get("provider") or "")
        provider_support = support_by_id.get(provider)
        if provider_support and provider_support is not support_row:
            provider_runtime = observe_cli(
                str(provider_support["cli_id"]),
                list(provider_support["commands"]),
                list(provider_support["version_args"]),
            )
        health = profile.get("health") if isinstance(profile.get("health"), dict) else {}
        health_status = _normalized_health_status(health.get("status"))
        declared_status = str(profile.get("status") or "active").strip().lower()
        setup_class = str(
            profile.get("setup_class")
            or (support_row or {}).get("setup_class")
            or "unclassified"
        )
        auth_status = _authentication_status(
            channel=str(profile.get("channel") or ""),
            health=health,
        )
        selected_model = str(config.get("model") or "")
        model_options = (
            profile.get("model_options")
            if isinstance(profile.get("model_options"), list)
            else []
        )
        selected_option = next(
            (
                option
                for option in model_options
                if isinstance(option, dict)
                and str(option.get("value") or "") == selected_model
            ),
            {},
        )
        primary_candidate = setup_class == "primary_option" or (
            str(profile.get("channel") or "") == "api"
            and any(
                role in {"lead", "team_lead", "lead_executor"}
                for role in selected_option.get("best_for", [])
            )
        )
        observations.append(
            {
                "id": profile_id,
                "provider": provider,
                "channel": str(profile.get("channel") or "unknown"),
                "adapter_type": str(profile.get("adapter_type") or "unknown"),
                "setup_class": setup_class,
                "declared_status": declared_status,
                "primary_candidate": primary_candidate,
                "declared": True,
                "cli": cli,
                "provider_runtime": provider_runtime,
                "authentication_status": auth_status,
                "authentication_source": (
                    "channel_contract"
                    if str(profile.get("channel") or "") == "local"
                    else "adapter_health"
                ),
                "health_status": health_status,
                "health_source": "adapter_health",
                "diagnostic_state": _adapter_diagnostic_state(
                    declared_status=declared_status,
                    cli=cli,
                    provider_runtime=provider_runtime,
                    authentication_status=auth_status,
                    health_status=health_status,
                ),
            }
        )
    return sorted(observations, key=lambda item: item["id"])


def _normalized_health_status(value: Any) -> str:
    status = str(value or "untested").strip().lower()
    return status if status in {
        "ok", "installed", "failed", "degraded", "unavailable", "untested"
    } else "unknown"


def _authentication_status(*, channel: str, health: Mapping[str, Any]) -> str:
    if channel == "local":
        return "not_applicable"
    explicit = str(health.get("auth_status") or "").strip().lower()
    if explicit in {"authenticated", "not_authenticated", "not_checked"}:
        return explicit
    reason = str(health.get("reason") or "").strip().lower()
    if reason == "auth_present":
        return "authenticated"
    if reason in {"auth_missing", "not_authenticated", "api_key_missing"}:
        return "not_authenticated"
    return "not_checked"


def _adapter_diagnostic_state(
    *,
    declared_status: str,
    cli: Mapping[str, Any] | None,
    provider_runtime: Mapping[str, Any] | None,
    authentication_status: str,
    health_status: str,
) -> str:
    if declared_status not in {"active", "enabled"}:
        return "incompatible"
    if (cli is not None and not cli["installed"]) or (
        provider_runtime is not None and not provider_runtime["installed"]
    ):
        return "absent"
    if authentication_status == "not_authenticated":
        return "not_authenticated"
    if health_status in {"failed", "degraded", "unavailable"}:
        return "degraded"
    if health_status == "ok" and authentication_status in {
        "authenticated",
        "not_applicable",
    }:
        return "ready"
    return "unverified"


def diagnose_machine_inventory(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic, non-mutating blockers and next actions."""
    diagnostics: list[dict[str, Any]] = []

    def add(
        *,
        diagnostic_id: str,
        subject_kind: str,
        subject_id: str,
        state: str,
        severity: str,
        code: str,
        message: str,
        source: str,
        action_code: str,
        action: str,
        requires_human: bool,
        mutates_state: bool,
    ) -> None:
        diagnostics.append(
            {
                "id": diagnostic_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "state": state,
                "severity": severity,
                "code": code,
                "message": message,
                "source": source,
                "next_action": {
                    "code": action_code,
                    "description": action,
                    "requires_human": requires_human,
                    "mutates_state": mutates_state,
                },
            }
        )

    for runtime in report["runtimes"]:
        if runtime["requirement"] not in {"required", "required_on_windows"}:
            continue
        if runtime["ready"]:
            continue
        if not runtime["installed"]:
            add(
                diagnostic_id=f"runtime:{runtime['id']}:absent",
                subject_kind="runtime",
                subject_id=runtime["id"],
                state="absent",
                severity="blocker",
                code="required_runtime_absent",
                message=f"Falta el runtime obligatorio {runtime['id']}.",
                source=runtime["source"],
                action_code="install_required_runtime",
                action="Instala el runtime requerido y repite el doctor.",
                requires_human=True,
                mutates_state=True,
            )
        elif not runtime["ready"]:
            add(
                diagnostic_id=f"runtime:{runtime['id']}:incompatible",
                subject_kind="runtime",
                subject_id=runtime["id"],
                state="incompatible",
                severity="blocker",
                code="required_runtime_incompatible",
                message=f"La versión de {runtime['id']} no satisface el mínimo declarado.",
                source=runtime["source"],
                action_code="update_required_runtime",
                action="Actualiza el runtime a una versión compatible y repite el doctor.",
                requires_human=True,
                mutates_state=True,
            )
    for permission in report["permissions"]:
        if not (
            permission["exists"]
            and permission["readable"]
            and permission["writable"]
            and permission["searchable"]
        ):
            add(
                diagnostic_id=f"permission:{permission['id']}:incompatible",
                subject_kind="permission",
                subject_id=permission["id"],
                state="incompatible",
                severity="blocker",
                code="checkout_permission_incompatible",
                message="El checkout no ofrece todos los permisos requeridos.",
                source=permission["source"],
                action_code="repair_checkout_permissions",
                action="Corrige los permisos del checkout y vuelve a ejecutar el doctor.",
                requires_human=True,
                mutates_state=True,
            )
    for port in report["ports"]:
        if port["state"] == "probe_error":
            add(
                diagnostic_id=f"port:{port['id']}:degraded",
                subject_kind="port",
                subject_id=port["id"],
                state="degraded",
                severity="warning",
                code="port_probe_degraded",
                message=f"No se pudo observar el puerto {port['port']} en loopback.",
                source=port["source"],
                action_code="inspect_loopback_port",
                action="Inspecciona el puerto local y repite el doctor.",
                requires_human=False,
                mutates_state=False,
            )
        elif port["state"] == "listening":
            add(
                diagnostic_id=f"port:{port['id']}:occupied",
                subject_kind="port",
                subject_id=port["id"],
                state="degraded",
                severity="info",
                code="port_already_listening",
                message=f"El puerto {port['port']} ya está escuchando; el doctor no identifica el proceso.",
                source=port["source"],
                action_code="identify_listening_process",
                action="Confirma si el proceso existente pertenece a AI Teams antes de iniciar servicios.",
                requires_human=False,
                mutates_state=False,
            )
    for toolchain in report["toolchains"]:
        if not toolchain["manifest_detected"]:
            continue
        if toolchain["diagnostic_state"] == "absent":
            add(
                diagnostic_id=f"toolchain:{toolchain['id']}:absent",
                subject_kind="toolchain",
                subject_id=toolchain["id"],
                state="absent",
                severity="blocker",
                code="project_toolchain_absent",
                message=f"Hay manifest de {toolchain['id']}, pero no se encontró su binario.",
                source=toolchain["source"],
                action_code="install_project_toolchain",
                action="Instala la toolchain detectada por el proyecto y repite el doctor.",
                requires_human=True,
                mutates_state=True,
            )
        elif toolchain["diagnostic_state"] == "unverified":
            add(
                diagnostic_id=f"toolchain:{toolchain['id']}:unverified",
                subject_kind="toolchain",
                subject_id=toolchain["id"],
                state="unverified",
                severity="info",
                code="project_toolchain_unverified",
                message=f"{toolchain['id']} está presente, pero discovery no demuestra soporte operativo.",
                source=toolchain["source"],
                action_code="verify_project_toolchain",
                action="Ejecuta el contrato de build/test específico cuando exista su descriptor versionado.",
                requires_human=False,
                mutates_state=False,
            )
    for adapter in report["adapters"]:
        state = adapter["diagnostic_state"]
        if state == "ready":
            continue
        severity = (
            "warning"
            if adapter["setup_class"] == "primary_option"
            else "info"
        )
        action_by_state = {
            "absent": (
                "adapter_cli_absent",
                "install_adapter_cli",
                "Instala explícitamente el CLI/runtime del perfil si deseas usarlo.",
                True,
                True,
            ),
            "not_authenticated": (
                "adapter_not_authenticated",
                "authenticate_adapter",
                "Completa el login humano del perfil y después ejecuta su prueba explícita.",
                True,
                True,
            ),
            "incompatible": (
                "adapter_incompatible",
                "review_adapter_compatibility",
                "Revisa el bloqueo declarado o actualiza el adapter antes de seleccionarlo.",
                True,
                True,
            ),
            "degraded": (
                "adapter_health_degraded",
                "retest_adapter_health",
                "Inspecciona el fallo durable y lanza una prueba explícita del perfil.",
                True,
                False,
            ),
            "unverified": (
                "adapter_unverified",
                "test_adapter_profile",
                "Prueba explícitamente el perfil; presencia del CLI no demuestra auth ni health.",
                True,
                False,
            ),
        }
        code, action_code, action, requires_human, mutates_state = action_by_state[state]
        state_label = {
            "absent": "ausente",
            "not_authenticated": "no autenticado",
            "incompatible": "incompatible",
            "degraded": "degradado",
            "unverified": "no verificado",
        }[state]
        add(
            diagnostic_id=f"adapter:{adapter['id']}:{state}",
            subject_kind="adapter",
            subject_id=adapter["id"],
            state=state,
            severity=severity,
            code=code,
            message=f"El perfil {adapter['id']} está {state_label}.",
            source=(
                adapter["health_source"]
                if state in {"not_authenticated", "degraded", "unverified"}
                else "profile_and_path_observation"
            ),
            action_code=action_code,
            action=action,
            requires_human=requires_human,
            mutates_state=mutates_state,
        )
    primary_ready = any(
        adapter["primary_candidate"]
        and adapter["diagnostic_state"] == "ready"
        for adapter in report["adapters"]
    )
    if not primary_ready:
        add(
            diagnostic_id="system:primary_adapter:unverified",
            subject_kind="system",
            subject_id="primary_adapter",
            state="unverified",
            severity="blocker",
            code="primary_adapter_not_ready",
            message="No hay ninguna opción primaria con autenticación y health verificados.",
            source="adapter_profile_composition",
            action_code="verify_primary_adapter",
            action="Configura y prueba Codex o Antigravity, o una vía Lead-capable gobernada.",
            requires_human=True,
            mutates_state=False,
        )
    severity_order = {"blocker": 0, "warning": 1, "info": 2}
    return sorted(
        diagnostics,
        key=lambda item: (
            severity_order[item["severity"]],
            item["subject_kind"],
            item["subject_id"],
            item["code"],
        ),
    )


def _overall_status(diagnostics: list[dict[str, Any]]) -> str:
    if any(item["severity"] == "blocker" for item in diagnostics):
        return "blocked"
    if any(item["state"] == "degraded" for item in diagnostics):
        return "degraded"
    if any(item["state"] == "unverified" for item in diagnostics):
        return "ready_with_unknowns"
    return "ready"


def _contains_personal_path(value: str) -> bool:
    """Reject common absolute-path shapes while allowing ordinary slashes in IDs."""
    return bool(re.search(r"[A-Za-z]:\\\\|(?:^|[\" ])/(?:Users|home|private)/", value))


def _resolve_runtime(commands: list[str], *, os_id: str) -> str | None:
    for command in commands:
        resolved = resolve_executable(command, os_id=os_id)
        if resolved:
            return resolved
    return None


def _probe_version_command(command: list[str]) -> tuple[bool, str | None]:
    env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_KEYS}
    try:
        completed = run_command(command, env=env, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        return True, "installed_version_unavailable"
    version = output.splitlines()[0].strip() if output else "installed_version_unavailable"
    return True, version[:200]


def _probe_loopback_port(port: int) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            return (
                "listening"
                if sock.connect_ex(("127.0.0.1", int(port))) == 0
                else "not_listening"
            )
    except OSError:
        return "probe_error"
