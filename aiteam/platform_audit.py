from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aiteam.platform_runtime import (
    architecture_id,
    platform_id,
    probe_filesystem_boundary,
    probe_timeout_cleanup,
)


PLATFORM_BOUNDARY_CONSUMERS = (
    "aiteam/adapters/subprocess_adapter.py",
    "aiteam/adapters/subscription_cli_adapter.py",
    "aiteam/cli.py",
    "aiteam/mcp_runtime.py",
    "aiteam/notifications.py",
    "aiteam/user_config.py",
    "api/routers/user_adapters.py",
)
SOURCE_TREES = ("aiteam", "api", "scripts")
_PERSONAL_WINDOWS_PATH = re.compile(
    r"[A-Za-z]:\\Users\\(?!\.\.\.\\)[^\\\r\n]+\\",
    re.IGNORECASE,
)
_PERSONAL_POSIX_PATH = re.compile(r"/(?:home|Users)/(?!\.\.\./)[^/\s\"']+/")
_SHELL_TRUE = re.compile(r"\bshell\s*=\s*True\b")


def audit_platform_portability(
    root: Path,
    *,
    probe_dir: Path | None = None,
    run_process_probe: bool = True,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    source_findings = _scan_source(repo_root)
    boundary_findings = _check_boundary_consumers(repo_root)
    filesystem = probe_filesystem_boundary(probe_dir)
    process = (
        probe_timeout_cleanup()
        if run_process_probe
        else {
            "timeout_observed": None,
            "elapsed_sec": None,
            "process_group_strategy": "not_run",
        }
    )
    host_os = platform_id()
    host_architecture = architecture_id()
    support = _support_entry(repo_root, host_os, host_architecture)
    checks = {
        "filesystem_roundtrip": bool(
            filesystem["spaces_and_unicode_roundtrip"]
            and filesystem["utf8_roundtrip"]
            and filesystem["permission_probe"]
        ),
        "timeout_cleanup": process["timeout_observed"] is True,
        "no_personal_absolute_paths": not source_findings["personal_absolute_paths"],
        "no_shell_true": not source_findings["shell_true"],
        "critical_consumers_use_boundary": not boundary_findings,
    }
    if not run_process_probe:
        checks["timeout_cleanup"] = True
    return {
        "schema_version": "platform_portability_audit_v1",
        "ok": all(checks.values()),
        "host": {
            "os": host_os,
            "architecture": host_architecture,
            "support_id": support.get("id"),
            "support_status": support.get("status", "unlisted"),
        },
        "checks": checks,
        "filesystem": filesystem,
        "processes": process,
        "source": source_findings,
        "boundary_missing": boundary_findings,
        "scope": {
            "support_promotion": False,
            "notes": (
                "Este auditor valida fronteras locales; no promociona soporte de "
                "plataforma ni sustituye una aceptación en máquina independiente."
            ),
        },
    }


def render_platform_portability_summary(report: dict[str, Any]) -> str:
    host = report["host"]
    lines = [
        "AI Teams — auditoría de portabilidad de plataforma",
        f"Host: {host['os']}/{host['architecture']} ({host['support_status']})",
    ]
    for name, passed in report["checks"].items():
        lines.append(f"[{'OK' if passed else 'FAIL'}] {name}")
    lines.append(
        "Resultado: "
        + ("frontera local consistente" if report["ok"] else "hay bloqueos que corregir")
    )
    lines.append("La ejecución local no promociona soporte de plataforma.")
    return "\n".join(lines)


def _scan_source(root: Path) -> dict[str, list[dict[str, Any]]]:
    personal: list[dict[str, Any]] = []
    shell_true: list[dict[str, Any]] = []
    for tree in SOURCE_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix.lower() not in {".py", ".ps1", ".bat", ".cmd"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="utf-8", errors="replace")
            relative = path.relative_to(root).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                if _PERSONAL_WINDOWS_PATH.search(line) or _PERSONAL_POSIX_PATH.search(line):
                    personal.append({"file": relative, "line": line_number})
                if _SHELL_TRUE.search(line):
                    shell_true.append({"file": relative, "line": line_number})
    return {
        "personal_absolute_paths": personal,
        "shell_true": shell_true,
    }


def _check_boundary_consumers(root: Path) -> list[str]:
    missing: list[str] = []
    for relative in PLATFORM_BOUNDARY_CONSUMERS:
        path = root / relative
        if not path.is_file() or "aiteam.platform_runtime" not in path.read_text(
            encoding="utf-8"
        ):
            missing.append(relative)
    return missing


def _support_entry(root: Path, host_os: str, host_architecture: str) -> dict[str, Any]:
    contract_path = root / "config" / "installation_support.v1.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    for entry in contract.get("platforms", []):
        if (
            entry.get("os") == host_os
            and entry.get("architecture") == host_architecture
        ):
            return dict(entry)
    return {}
