"""Canarios OpenCode ``serve``: hang/restart real y health MCP aislado.

El hang suspende exclusivamente el proceso nativo lanzado por este harness.
Después lo termina, reinicia en el mismo puerto y recupera la misma sesión.
El segundo canario conecta un MCP local determinista con allowlist exacta.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.adapters.subscription_cli_adapter import _opencode_inline_config  # noqa: E402
from scripts.benchmark_opencode_transport import (  # noqa: E402
    DEFAULT_MODEL,
    POLICY,
    _free_port,
    _server_executable,
    _wait_for_port,
    _wait_for_port_closed,
)

MCP_FIXTURE = REPO_ROOT / "scripts" / "opencode_mcp_health_fixture.py"


def _request(
    base_url: str,
    password: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    token = base64.b64encode(f"opencode:{password}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def _start_server(executable: str, port: int, env: dict[str, str]) -> subprocess.Popen:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [
            _server_executable(executable),
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _stop_server(server: subprocess.Popen, port: int) -> bool:
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
    return _wait_for_port_closed(port)


def _windows_process_apis() -> tuple[Any, Any]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtSuspendProcess.restype = wintypes.LONG
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = wintypes.LONG
    return kernel32, ntdll


def _suspend_process(pid: int) -> None:
    if os.name == "nt":
        process_suspend_resume = 0x0800
        kernel32, ntdll = _windows_process_apis()
        handle = kernel32.OpenProcess(process_suspend_resume, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            status = ntdll.NtSuspendProcess(handle)
            if status != 0:
                raise OSError(status, "NtSuspendProcess failed")
        finally:
            kernel32.CloseHandle(handle)
        return
    os.kill(pid, signal.SIGSTOP)


def _resume_process(pid: int) -> None:
    if os.name == "nt":
        process_suspend_resume = 0x0800
        kernel32, ntdll = _windows_process_apis()
        handle = kernel32.OpenProcess(process_suspend_resume, False, pid)
        if not handle:
            return
        try:
            ntdll.NtResumeProcess(handle)
        finally:
            kernel32.CloseHandle(handle)
        return
    os.kill(pid, signal.SIGCONT)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        query = 0x1000
        kernel32, _ = _windows_process_apis()
        handle = kernel32.OpenProcess(query, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_pid_exit(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_is_alive(pid)


def _extract_text(response: dict[str, Any]) -> str:
    return "".join(
        str(part.get("text") or "")
        for part in response.get("parts") or []
        if part.get("type") == "text"
    )


def run_hang_recovery(executable: str, *, model: str) -> dict[str, Any]:
    port = _free_port()
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "OPENCODE_SERVER_PASSWORD": password,
        "OPENCODE_CONFIG_CONTENT": json.dumps(POLICY),
    }
    base_url = f"http://127.0.0.1:{port}"
    server = _start_server(executable, port, env)
    restarted: subprocess.Popen | None = None
    suspended = False
    session_id = ""
    gates = {
        "session_created_before_hang": False,
        "native_server_suspended": False,
        "port_open_while_hung": False,
        "health_times_out_while_hung": False,
        "faulted_server_stopped": False,
        "same_port_restarted": False,
        "health_after_restart": False,
        "same_session_recovered": False,
        "session_idle_after_restart": False,
        "recovery_marker_exact": False,
        "session_deleted": False,
        "server_teardown_guaranteed": False,
    }
    timings: dict[str, int] = {}
    try:
        if not _wait_for_port(port):
            raise RuntimeError("OpenCode readiness timeout before hang")
        _, session = _request(
            base_url, password, "POST", "/session", {"title": "AI Teams hang canary"}
        )
        session_id = str(session.get("id") or "")
        gates["session_created_before_hang"] = bool(session_id)

        _suspend_process(server.pid)
        suspended = True
        gates["native_server_suspended"] = server.poll() is None
        gates["port_open_while_hung"] = _wait_for_port(port, timeout=1)
        probe_started = time.monotonic()
        try:
            _request(base_url, password, "GET", "/global/health", timeout=0.5)
        except (TimeoutError, urllib.error.URLError):
            gates["health_times_out_while_hung"] = True
        timings["hung_health_timeout_ms"] = round(
            (time.monotonic() - probe_started) * 1000
        )

        server.kill()
        server.wait(timeout=10)
        suspended = False
        gates["faulted_server_stopped"] = _wait_for_port_closed(port)

        restarted = _start_server(executable, port, env)
        gates["same_port_restarted"] = _wait_for_port(port)
        _, health = _request(base_url, password, "GET", "/global/health")
        gates["health_after_restart"] = health.get("healthy") is True
        _, recovered = _request(base_url, password, "GET", f"/session/{session_id}")
        gates["same_session_recovered"] = recovered.get("id") == session_id
        _, statuses = _request(base_url, password, "GET", "/session/status")
        gates["session_idle_after_restart"] = (
            statuses.get(session_id) or {"type": "idle"}
        ).get("type") == "idle"

        provider, model_id = model.split("/", 1)
        if provider != "opencode":
            raise ValueError("the hang canary currently requires an opencode/* model")
        marker = "OPENCODE_HANG_RECOVERY_OK"
        prompt_started = time.monotonic()
        _, response = _request(
            base_url,
            password,
            "POST",
            f"/session/{session_id}/message",
            {
                "model": {"providerID": provider, "modelID": model_id},
                "tools": {},
                "parts": [
                    {
                        "type": "text",
                        "text": f"Return exactly {marker} and no other text. Do not use tools.",
                    }
                ],
            },
            timeout=180,
        )
        timings["recovery_prompt_ms"] = round(
            (time.monotonic() - prompt_started) * 1000
        )
        gates["recovery_marker_exact"] = _extract_text(response).strip() == marker
        _, deleted = _request(base_url, password, "DELETE", f"/session/{session_id}")
        gates["session_deleted"] = deleted is True
    finally:
        if suspended and server.poll() is None:
            _resume_process(server.pid)
        if server.poll() is None:
            _stop_server(server, port)
        if restarted is not None:
            gates["server_teardown_guaranteed"] = _stop_server(restarted, port)
        else:
            gates["server_teardown_guaranteed"] = _wait_for_port_closed(port)
    return {
        "session_id": session_id or None,
        "same_port": port,
        "timings_ms": timings,
        "gates": gates,
        "ok": all(gates.values()),
    }


def _restricted_server_env(password: str, config: dict[str, Any]) -> dict[str, str]:
    keep = {
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    env["OPENCODE_SERVER_PASSWORD"] = password
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    return env


def run_mcp_health(executable: str) -> dict[str, Any]:
    port = _free_port()
    password = secrets.token_urlsafe(32)
    gates = {
        "mcp_process_started": False,
        "mcp_connected": False,
        "mcp_initialize_observed": False,
        "mcp_tools_list_observed": False,
        "approved_tool_listed_by_mcp": False,
        "unapproved_tool_listed_by_mcp": False,
        "namespace_denied_by_default": False,
        "approved_tool_allowed_exactly": False,
        "unapproved_tool_not_allowed": False,
        "mcp_process_reaped": False,
        "server_teardown_guaranteed": False,
    }
    fixture_pid = 0
    observed_tool_ids: list[str] = []
    mcp_trace: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aiteams-opencode-mcp-") as temp_dir:
        pid_file = Path(temp_dir) / "fixture.pid"
        trace_file = Path(temp_dir) / "fixture.jsonl"
        config = _opencode_inline_config(
            [
                {
                    "name": "canary",
                    "command": sys.executable,
                    "args": [
                        str(MCP_FIXTURE),
                        "--pid-file",
                        str(pid_file),
                        "--trace-file",
                        str(trace_file),
                    ],
                    "enabled_tools": ["health_read"],
                    "env_required": [],
                }
            ]
        )
        env = _restricted_server_env(password, config)
        base_url = f"http://127.0.0.1:{port}"
        server = _start_server(executable, port, env)
        try:
            if not _wait_for_port(port):
                raise RuntimeError("OpenCode readiness timeout for MCP health")
            deadline = time.monotonic() + 15
            statuses: dict[str, Any] = {}
            while time.monotonic() < deadline:
                _, statuses = _request(base_url, password, "GET", "/mcp")
                if (statuses.get("canary") or {}).get("status") in {
                    "connected",
                    "failed",
                }:
                    break
                time.sleep(0.1)
            gates["mcp_connected"] = (statuses.get("canary") or {}).get(
                "status"
            ) == "connected"
            if pid_file.is_file():
                fixture_pid = int(pid_file.read_text(encoding="ascii").strip())
            gates["mcp_process_started"] = _pid_is_alive(fixture_pid)

            query = urllib.parse.urlencode({"directory": str(REPO_ROOT)})
            _, tool_ids = _request(
                base_url, password, "GET", f"/experimental/tool/ids?{query}"
            )
            observed_tool_ids = [str(item) for item in tool_ids]
            if trace_file.is_file():
                mcp_trace = [
                    json.loads(line)
                    for line in trace_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            observed_methods = {str(item.get("method") or "") for item in mcp_trace}
            listed_tools = {
                str(tool)
                for item in mcp_trace
                for tool in item.get("listed_tools") or []
            }
            gates["mcp_initialize_observed"] = "initialize" in observed_methods
            gates["mcp_tools_list_observed"] = "tools/list" in observed_methods
            gates["approved_tool_listed_by_mcp"] = "health_read" in listed_tools
            gates["unapproved_tool_listed_by_mcp"] = "forbidden_write" in listed_tools

            _, resolved = _request(base_url, password, "GET", "/config")
            permission = resolved.get("permission") or {}
            gates["namespace_denied_by_default"] = permission.get("canary_*") == "deny"
            gates["approved_tool_allowed_exactly"] = (
                permission.get("canary_health_read") == "allow"
            )
            gates["unapproved_tool_not_allowed"] = (
                permission.get("canary_forbidden_write") != "allow"
            )
        finally:
            gates["server_teardown_guaranteed"] = _stop_server(server, port)
            if fixture_pid:
                gates["mcp_process_reaped"] = _wait_pid_exit(fixture_pid)
    return {
        "fixture_pid": fixture_pid or None,
        "opencode_tool_ids_endpoint": observed_tool_ids,
        "mcp_trace": mcp_trace,
        "gates": gates,
        "ok": all(gates.values()),
    }


def summarize(
    *, hang: dict[str, Any], mcp: dict[str, Any], cli_version: str, model: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "benchmark": "opencode_server_faults",
        "cli_version": cli_version,
        "model": model,
        "contract": "suspended_server_same_session_restart_and_mcp_health_v1",
        "hang_recovery": hang,
        "mcp_health": mcp,
        "gates": {
            "server_process_hang_injected": all(
                hang["gates"].get(key) is True
                for key in ("native_server_suspended", "port_open_while_hung")
            ),
            "hung_health_detected": hang["gates"].get("health_times_out_while_hung")
            is True,
            "same_session_restart_recovered": all(
                hang["gates"].get(key) is True
                for key in (
                    "faulted_server_stopped",
                    "same_port_restarted",
                    "health_after_restart",
                    "same_session_recovered",
                    "session_idle_after_restart",
                    "recovery_marker_exact",
                    "session_deleted",
                )
            ),
            "mcp_health_tested": all(
                mcp["gates"].get(key) is True
                for key in ("mcp_process_started", "mcp_connected")
            ),
            "mcp_allowlist_observed": all(
                mcp["gates"].get(key) is True
                for key in (
                    "mcp_initialize_observed",
                    "mcp_tools_list_observed",
                    "approved_tool_listed_by_mcp",
                    "unapproved_tool_listed_by_mcp",
                    "namespace_denied_by_default",
                    "approved_tool_allowed_exactly",
                    "unapproved_tool_not_allowed",
                )
            ),
            "all_processes_reaped": all(
                (
                    hang["gates"].get("server_teardown_guaranteed") is True,
                    mcp["gates"].get("server_teardown_guaranteed") is True,
                    mcp["gates"].get("mcp_process_reaped") is True,
                )
            ),
        },
        "production_activation_allowed": False,
        "decision": "retain_ephemeral_cli",
        "reason": (
            "Hang/restart y health MCP quedan probados en una semilla, pero JSON Schema "
            "sigue fallando y faltan varias semillas de contaminación/override."
        ),
    }


def run_experiment(*, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    executable = shutil.which("opencode.cmd") or shutil.which("opencode")
    if not executable:
        raise RuntimeError("OpenCode CLI is not installed")
    version_proc = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    version = (
        (version_proc.stdout or version_proc.stderr or "unknown")
        .strip()
        .splitlines()[0]
    )
    hang = run_hang_recovery(executable, model=model)
    mcp = run_mcp_health(executable)
    return summarize(hang=hang, mcp=mcp, cli_version=version, model=model)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run_experiment(model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"gates": report["gates"], "decision": report["decision"]}))
    return 0 if all(report["gates"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
