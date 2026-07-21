"""Runtime MCP gobernado y efímero para adapters de suscripción.

Solo activa servidores ya aprobados que puedan demostrar un handshake MCP real.
No usa shell, no instala paquetes y nunca persiste valores de secretos.
"""
from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiteam.extensions import (
    read_extensions,
    set_mcp_server_health,
    set_mcp_server_status,
    slugify_skill_name,
)


class McpHealthError(ValueError):
    """El servidor no cumple el contrato seguro de activación."""


_HEALTH_TTL = timedelta(hours=24)
_HEALTH_LOCK = threading.Lock()


def check_and_activate_mcp_server(
    runtime_dir: Path,
    *,
    name: str,
    timeout_sec: float = 8.0,
) -> dict[str, Any]:
    if not _HEALTH_LOCK.acquire(blocking=False):
        raise McpHealthError("MCP health check already in progress")
    try:
        return _check_and_activate_mcp_server(
            runtime_dir,
            name=name,
            timeout_sec=timeout_sec,
        )
    finally:
        _HEALTH_LOCK.release()


def _check_and_activate_mcp_server(
    runtime_dir: Path,
    *,
    name: str,
    timeout_sec: float,
) -> dict[str, Any]:
    registry = read_extensions(runtime_dir)
    slug = slugify_skill_name(name)
    entry = registry["mcp_servers"].get(slug)
    if not isinstance(entry, dict):
        raise McpHealthError("MCP server not found")
    if str(entry.get("status") or "") not in {"approved", "failed", "active"}:
        raise McpHealthError("MCP server must be owner-approved before health check")

    try:
        command = _resolved_command(entry)
        identity_before = artifact_identity(command)
        response, tools = _probe_stdio(
            command,
            timeout_sec=timeout_sec,
            env=_restricted_mcp_env(entry),
        )
        identity_after = artifact_identity(command)
        if identity_after["digest"] != identity_before["digest"]:
            raise McpHealthError("MCP artifact changed during health check")
        server_info = response.get("serverInfo") if isinstance(response, dict) else None
        actual_version = str((server_info or {}).get("version") or "").strip()
        pinned_version = str(entry.get("version") or "").strip()
        if not actual_version:
            raise McpHealthError("MCP initialize response has no serverInfo.version")
        if actual_version != pinned_version:
            raise McpHealthError(
                f"MCP version mismatch: expected {pinned_version!r}, got {actual_version!r}"
            )
    except Exception as exc:
        previous_health = entry.get("health") if isinstance(entry.get("health"), dict) else {}
        failures = int(previous_health.get("consecutive_failures") or 0) + 1
        retired = failures >= 3
        status = "retired" if retired else "failed"
        failed = set_mcp_server_status(runtime_dir, name=slug, status=status) or {"name": slug}
        retry_delay = timedelta(hours=1 if failures == 1 else 6)
        health = {
            "status": "failed",
            "detail": str(exc),
            "consecutive_failures": failures,
            "next_check_at": None if retired else (datetime.now(timezone.utc) + retry_delay).isoformat(),
            "retired_after_failures": retired,
        }
        _persist_health(runtime_dir, slug, **health)
        if retired:
            failed = set_mcp_server_status(runtime_dir, name=slug, status="retired") or failed
        return {**failed, "health": health}

    active = set_mcp_server_status(runtime_dir, name=slug, status="active") or {"name": slug}
    health = {
        "status": "ok",
        "protocol_version": str(response.get("protocolVersion") or ""),
        "server_name": str((server_info or {}).get("name") or slug),
        "server_version": actual_version,
        "tools": tools,
        "artifact_identity": identity_after,
        "consecutive_failures": 0,
        "next_check_at": (datetime.now(timezone.utc) + _HEALTH_TTL).isoformat(),
    }
    _persist_health(runtime_dir, slug, **health)
    return {**active, "health": health}


def refresh_due_mcp_servers(
    runtime_dir: Path,
    *,
    max_checks: int = 1,
    timeout_sec: float = 8.0,
) -> list[dict[str, Any]]:
    """Probe at most ``max_checks`` due active/failed servers per loop tick."""
    now = datetime.now(timezone.utc)
    due: list[str] = []
    for name, entry in sorted(read_extensions(runtime_dir)["mcp_servers"].items()):
        if not isinstance(entry, dict) or entry.get("status") not in {"active", "failed"}:
            continue
        health = entry.get("health") if isinstance(entry.get("health"), dict) else {}
        raw_due = str(health.get("next_check_at") or "").strip()
        if raw_due:
            try:
                due_at = datetime.fromisoformat(raw_due.replace("Z", "+00:00"))
                if due_at.tzinfo is None:
                    due_at = due_at.replace(tzinfo=timezone.utc)
            except ValueError:
                due_at = now
        else:
            due_at = now if not _health_is_fresh(health) else now + _HEALTH_TTL
        if due_at <= now:
            due.append(name)
    results: list[dict[str, Any]] = []
    for name in due[: max(0, int(max_checks))]:
        try:
            results.append(
                check_and_activate_mcp_server(runtime_dir, name=name, timeout_sec=timeout_sec)
            )
        except McpHealthError:
            continue
    return results


def mcp_servers_for_run(
    runtime_dir: Path,
    *,
    role: str,
    capabilities: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Devuelve grants inyectables y denegaciones auditables para una run."""
    role_key = _normalize_role(role)
    grants: list[dict[str, Any]] = []
    denials: list[dict[str, Any]] = []
    for name, entry in sorted(read_extensions(runtime_dir)["mcp_servers"].items()):
        if not isinstance(entry, dict) or str(entry.get("status") or "") != "active":
            continue
        roles = {_normalize_role(item) for item in entry.get("applies_to_roles") or []}
        if roles and role_key not in roles:
            continue
        health = entry.get("health") if isinstance(entry.get("health"), dict) else {}
        if health.get("status") != "ok" or health.get("server_version") != entry.get("version"):
            denials.append({"name": name, "reason": "mcp_health_not_current"})
            continue
        if not _health_is_fresh(health):
            denials.append({"name": name, "reason": "mcp_health_expired"})
            continue
        if "external_mcp" not in capabilities:
            denials.append({"name": name, "reason": "capability_not_granted:external_mcp"})
            continue
        missing_env = [key for key in entry.get("env_required") or [] if not os.environ.get(str(key))]
        if missing_env:
            denials.append({"name": name, "reason": f"mcp_env_missing:{','.join(missing_env)}"})
            continue
        try:
            command = _resolved_command(entry)
        except McpHealthError as exc:
            denials.append({"name": name, "reason": str(exc)})
            continue
        expected_identity = health.get("artifact_identity")
        try:
            current_identity = artifact_identity(command)
        except McpHealthError as exc:
            denials.append({"name": name, "reason": str(exc)})
            continue
        if (
            not isinstance(expected_identity, dict)
            or expected_identity.get("digest") != current_identity.get("digest")
        ):
            denials.append({"name": name, "reason": "mcp_artifact_changed"})
            continue
        tools = health.get("tools") if isinstance(health.get("tools"), list) else []
        approved_policy = {
            str(item.get("name") or ""): str(item.get("access") or "")
            for item in entry.get("approved_tools") or []
            if isinstance(item, dict)
        }
        if not approved_policy:
            denials.append({"name": name, "reason": "mcp_tools_not_owner_approved"})
            continue
        may_write = "repo_write" in capabilities
        enabled_tools: list[str] = []
        denied_tools: list[str] = []
        tool_decisions: list[dict[str, str]] = []
        for tool in tools:
            if not isinstance(tool, dict) or not str(tool.get("name") or "").strip():
                continue
            tool_name = str(tool["name"])
            approved_access = approved_policy.get(tool_name)
            allowed = approved_access == "read" or (approved_access == "write" and may_write)
            if allowed:
                enabled_tools.append(tool_name)
                tool_decisions.append({
                    "name": tool_name,
                    "decision": "allowed",
                    "reason": "owner_approved_read_tool" if approved_access == "read" else "owner_approved_write_tool",
                })
            else:
                denied_tools.append(tool_name)
                reason = (
                    "mcp_tool_write_not_granted"
                    if approved_access == "write"
                    else "mcp_tool_not_owner_approved"
                )
                tool_decisions.append({"name": tool_name, "decision": "denied", "reason": reason})
        if not enabled_tools:
            denials.append({
                "name": name,
                "reason": "mcp_no_authorized_tools",
                "tool_decisions": tool_decisions,
            })
            continue
        grants.append({
            "name": name,
            "command": command[0],
            "args": command[1:],
            "version": entry["version"],
            "env_required": [str(key) for key in entry.get("env_required") or []],
            "enabled_tools": enabled_tools,
            "denied_tools": denied_tools,
            "tool_decisions": tool_decisions,
        })
    return grants, denials


def _health_is_fresh(health: dict[str, Any]) -> bool:
    raw = str(health.get("checked_at") or "").strip()
    if not raw:
        return False
    try:
        checked_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked_at <= _HEALTH_TTL


def artifact_identity(command: list[str]) -> dict[str, Any]:
    """Hash the resolved executable, literal contract and file arguments."""
    if not command:
        raise McpHealthError("MCP command is empty")
    contract: list[dict[str, str]] = []
    files: list[dict[str, str]] = []
    for index, value in enumerate(command):
        raw = str(value)
        candidate = Path(raw)
        is_required_file = index == 0
        if candidate.is_file():
            resolved = candidate.resolve()
            try:
                digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except OSError as exc:
                raise McpHealthError(f"MCP artifact cannot be read: {resolved}") from exc
            item = {"path": str(resolved), "sha256": digest}
            files.append(item)
            contract.append({"kind": "file", **item})
        elif is_required_file:
            raise McpHealthError(f"MCP executable is not a file: {raw}")
        else:
            contract.append({"kind": "literal", "value": raw})
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "algorithm": "sha256",
        "digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "files": files,
    }


def _resolved_command(entry: dict[str, Any]) -> list[str]:
    source = str(entry.get("source") or "").strip()
    version = str(entry.get("version") or "").strip()
    if not source or not version:
        raise McpHealthError("MCP source and pinned version are required")
    candidate = Path(source)
    if candidate.is_file():
        resolved = str(candidate.resolve())
    else:
        if any(char.isspace() for char in source):
            raise McpHealthError("MCP source must be one executable path; shell commands are forbidden")
        resolved = shutil.which(source)
        if not resolved:
            raise McpHealthError(f"MCP executable not found: {source}")
    args = entry.get("args") or []
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise McpHealthError("MCP args must be a string list")
    return [resolved, *args]


def _probe_stdio(
    command: list[str],
    *,
    timeout_sec: float,
    env: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        env=env,
    )
    def _exchange(message: dict[str, Any]) -> dict[str, Any]:
        messages: queue.Queue[str] = queue.Queue(maxsize=1)

        def _readline() -> None:
            assert proc.stdout is not None
            messages.put(proc.stdout.readline())

        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        threading.Thread(target=_readline, daemon=True).start()
        try:
            raw = messages.get(timeout=max(0.1, float(timeout_sec)))
        except queue.Empty as exc:
            raise McpHealthError(f"MCP request timed out after {timeout_sec:g}s") from exc
        if not raw.strip():
            detail = (
                proc.stderr.read(2000).strip()
                if proc.stderr is not None and proc.poll() is not None
                else ""
            )
            raise McpHealthError(f"MCP request returned no response{': ' + detail if detail else ''}")
        parsed = json.loads(raw)
        if parsed.get("id") != message.get("id") or not isinstance(parsed.get("result"), dict):
            raise McpHealthError("invalid MCP JSON-RPC response")
        return parsed["result"]

    try:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "aiteam-health", "version": "1"},
            },
        }
        initialized = _exchange(request)
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        raw_tools: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page in range(20):
            params = {"cursor": cursor} if cursor else {}
            listed = _exchange({
                "jsonrpc": "2.0",
                "id": 2 + page,
                "method": "tools/list",
                "params": params,
            })
            page_tools = listed.get("tools")
            if not isinstance(page_tools, list):
                raise McpHealthError("MCP tools/list response has no tools array")
            raw_tools.extend(page_tools)
            next_cursor = str(listed.get("nextCursor") or "").strip() or None
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise McpHealthError("MCP tools/list repeated its pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise McpHealthError("MCP tools/list exceeded 20 pages")
        tools = []
        tool_names: set[str] = set()
        for item in raw_tools:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            tool_name = str(item["name"])
            if tool_name in tool_names:
                raise McpHealthError(f"MCP tools/list contains duplicate tool: {tool_name}")
            tool_names.add(tool_name)
            annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
            tools.append({
                "name": tool_name,
                "read_only": annotations.get("readOnlyHint") is True,
            })
        if not tools:
            raise McpHealthError("MCP server exposes no tools")
        return initialized, tools
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def _persist_health(runtime_dir: Path, name: str, **health: Any) -> None:
    set_mcp_server_health(runtime_dir, name=name, health=health)


def _normalize_role(role: str) -> str:
    return str(role or "").strip().lower().replace(" ", "_").replace("-", "_")


def _restricted_mcp_env(entry: dict[str, Any]) -> dict[str, str]:
    """Minimal process environment plus explicitly declared secret names."""
    keep = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}
    required = [str(key) for key in entry.get("env_required") or []]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise McpHealthError(f"MCP required environment missing: {','.join(missing)}")
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in keep or key in required
    }
