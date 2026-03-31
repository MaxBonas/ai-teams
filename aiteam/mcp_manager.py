"""
MCP Server Manager — Gestion de ciclo de vida de servidores MCP.

Maneja el start/stop de servidores MCP via stdio transport (JSON-RPC 2.0),
invocacion de herramientas, y registro de actividad en sesiones de agente.

Patron inspirado en: Claude Code (MCP tool use), Cursor (tool integration),
Claude Desktop (MCP server config).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.agent_session import AgentSession


@dataclass
class MCPServerConfig:
    """Configuracion de un servidor MCP."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    enabled: bool = False
    requires_approval: bool = False
    source_type: str = "npm"
    source: str = ""
    capabilities: list[str] = field(default_factory=list)
    role_targets: list[str] = field(default_factory=list)
    health_status: str = "unknown"
    health_reason: str = ""
    last_checked: str = ""
    bootstrap_source: str = ""


@dataclass
class MCPToolInfo:
    """Metadata de una herramienta expuesta por un servidor MCP."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPInvokeResult:
    """Resultado de invocar una herramienta MCP."""
    tool_name: str
    server_name: str
    success: bool
    content: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    is_error: bool = False

    @property
    def text(self) -> str:
        """Extrae texto plano del contenido."""
        parts = []
        for item in self.content:
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts) if parts else self.error


class MCPServerProcess:
    """Proceso de un servidor MCP con comunicacion stdio JSON-RPC."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._request_id = 0
        self._initialized = False
        self._server_info: dict[str, Any] = {}
        self._tools: list[MCPToolInfo] = []
        self._read_buffer = ""

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self.process is not None and self.process.poll() is None

    def start(self, timeout: int = 30) -> tuple[bool, str]:
        """Inicia el servidor MCP y ejecuta el handshake initialize."""
        with self._lock:
            if self.is_running:
                return True, "already_running"

            env = dict(os.environ)
            env.update(self.config.env)

            command = self.config.command
            args = list(self.config.args)

            try:
                self.process = subprocess.Popen(
                    [command, *args],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=False,  # binary mode for proper JSON-RPC framing
                )
            except (OSError, FileNotFoundError) as exc:
                return False, f"start_failed: {exc}"

            # Initialize handshake (MCP protocol)
            try:
                init_result = self._send_request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "aiteam-orchestrator",
                            "version": "0.1.0",
                        },
                    },
                    timeout=timeout,
                )
                if init_result is None:
                    self.stop()
                    return False, "initialize_timeout"

                self._server_info = init_result.get("result", {})
                self._initialized = True

                # Send initialized notification
                self._send_notification("notifications/initialized", {})

                # Discover tools
                self._discover_tools(timeout=timeout)

                return True, "started"
            except Exception as exc:
                self.stop()
                return False, f"handshake_failed: {exc}"

    def stop(self) -> None:
        """Detiene el servidor MCP."""
        with self._lock:
            if self.process is not None:
                try:
                    if self.process.poll() is None:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait(timeout=3)
                except OSError:
                    pass
                finally:
                    self.process = None
                    self._initialized = False
                    self._tools = []

    def list_tools(self) -> list[MCPToolInfo]:
        """Lista herramientas disponibles del servidor."""
        return list(self._tools)

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> MCPInvokeResult:
        """Invoca una herramienta del servidor MCP."""
        start_time = time.perf_counter()

        if not self.is_running or not self._initialized:
            return MCPInvokeResult(
                tool_name=tool_name,
                server_name=self.config.name,
                success=False,
                error="server_not_running",
                duration_ms=0,
            )

        try:
            response = self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
                timeout=timeout,
            )
            duration_ms = int((time.perf_counter() - start_time) * 1000)

            if response is None:
                return MCPInvokeResult(
                    tool_name=tool_name,
                    server_name=self.config.name,
                    success=False,
                    error="timeout",
                    duration_ms=duration_ms,
                )

            if "error" in response:
                err = response["error"]
                return MCPInvokeResult(
                    tool_name=tool_name,
                    server_name=self.config.name,
                    success=False,
                    error=f"{err.get('code', -1)}: {err.get('message', 'unknown')}",
                    duration_ms=duration_ms,
                    is_error=True,
                )

            result = response.get("result", {})
            content = result.get("content", [])
            is_error = result.get("isError", False)

            return MCPInvokeResult(
                tool_name=tool_name,
                server_name=self.config.name,
                success=not is_error,
                content=content if isinstance(content, list) else [],
                duration_ms=duration_ms,
                is_error=is_error,
            )

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return MCPInvokeResult(
                tool_name=tool_name,
                server_name=self.config.name,
                success=False,
                error=str(exc)[:500],
                duration_ms=duration_ms,
            )

    def _discover_tools(self, timeout: int = 15) -> None:
        """Descubre herramientas del servidor via tools/list."""
        try:
            response = self._send_request("tools/list", {}, timeout=timeout)
            if response and "result" in response:
                tools_raw = response["result"].get("tools", [])
                self._tools = []
                for t in tools_raw:
                    if not isinstance(t, dict):
                        continue
                    self._tools.append(MCPToolInfo(
                        name=str(t.get("name", "")),
                        description=str(t.get("description", "")),
                        input_schema=t.get("inputSchema", {}),
                        server_name=self.config.name,
                    ))
        except Exception:
            pass

    def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: int = 30,
    ) -> dict[str, Any] | None:
        """Envia un JSON-RPC request y espera la respuesta."""
        with self._lock:
            if self.process is None or self.process.stdin is None or self.process.stdout is None:
                return None

            self._request_id += 1
            request_id = self._request_id

            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }

            try:
                payload = json.dumps(message) + "\n"
                self.process.stdin.write(payload.encode("utf-8"))
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                return None

            # Read response (line-delimited JSON)
            return self._read_response(request_id, timeout)

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Envia una notificacion JSON-RPC (sin id, sin respuesta)."""
        with self._lock:
            if self.process is None or self.process.stdin is None:
                return
            message = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            try:
                payload = json.dumps(message) + "\n"
                self.process.stdin.write(payload.encode("utf-8"))
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def _read_response(self, request_id: int, timeout: int) -> dict[str, Any] | None:
        """Lee una respuesta JSON-RPC con el id esperado."""
        if self.process is None or self.process.stdout is None:
            return None

        deadline = time.monotonic() + timeout
        buffer = b""

        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                # Process died
                remaining = self.process.stdout.read()
                if remaining:
                    buffer += remaining
                break

            # Non-blocking read attempt
            try:
                import select
                ready, _, _ = select.select([self.process.stdout], [], [], 0.1)
                if not ready:
                    continue
                chunk = self.process.stdout.read1(4096) if hasattr(self.process.stdout, 'read1') else self.process.stdout.read(4096)
                if not chunk:
                    continue
                buffer += chunk
            except (OSError, ValueError):
                # On Windows, select doesn't work on pipes — use threaded read
                return self._read_response_threaded(request_id, timeout, buffer)

            # Try to parse complete JSON lines
            lines = buffer.split(b"\n")
            buffer = lines[-1]  # keep incomplete line

            for line in lines[:-1]:
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                    if isinstance(msg, dict) and msg.get("id") == request_id:
                        return msg
                    # Skip notifications or other messages
                except json.JSONDecodeError:
                    continue

        return None

    def _read_response_threaded(
        self,
        request_id: int,
        timeout: int,
        initial_buffer: bytes = b"",
    ) -> dict[str, Any] | None:
        """Fallback para Windows: lee en un thread separado."""
        result_holder: list[dict[str, Any] | None] = [None]
        buffer = initial_buffer

        def _reader():
            nonlocal buffer
            if self.process is None or self.process.stdout is None:
                return
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    chunk = self.process.stdout.read(1)
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    buffer += chunk
                    if chunk == b"\n":
                        line_str = buffer.decode("utf-8", errors="replace").strip()
                        buffer = b""
                        if not line_str:
                            continue
                        try:
                            msg = json.loads(line_str)
                            if isinstance(msg, dict) and msg.get("id") == request_id:
                                result_holder[0] = msg
                                return
                        except json.JSONDecodeError:
                            continue
                except (OSError, ValueError):
                    return

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        return result_holder[0]


class MCPServerManager:
    """Gestor de ciclo de vida de servidores MCP."""

    def __init__(
        self,
        runtime_dir: Path,
        catalog_path: Path | None = None,
        environment: str = "dev",
    ) -> None:
        self.runtime_dir = runtime_dir
        self.catalog_path = catalog_path
        self.environment = environment
        self._mcp_config_path = runtime_dir / "mcp_servers.json"
        self._servers: dict[str, MCPServerProcess] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._lock = threading.RLock()
        self._event_log_path = runtime_dir / "mcp_events.jsonl"
        self._load_configs()

    @staticmethod
    def _current_username() -> str:
        username = str(os.getenv("USERNAME", "") or "").strip()
        if username:
            return username
        try:
            return Path.home().name.strip()
        except Exception:
            return ""

    @staticmethod
    def current_machine_profile() -> dict[str, str]:
        machine_name = (
            str(os.getenv("COMPUTERNAME", "") or "").strip()
            or str(os.getenv("HOSTNAME", "") or "").strip()
        )
        username = MCPServerManager._current_username()
        userprofile = str(os.getenv("USERPROFILE", "") or "").strip()
        return {
            "machine_name": machine_name,
            "username": username,
            "userprofile": userprofile,
        }

    @staticmethod
    def _normalize_path_placeholders(value: str) -> str:
        text = str(value or "")
        username = MCPServerManager._current_username()
        userprofile = str(os.getenv("USERPROFILE", "") or "").strip()
        output = text
        if username:
            output = output.replace("${USERNAME}", username)
            output = output.replace("%USERNAME%", username)
        if userprofile:
            output = output.replace("%USERPROFILE%", userprofile)
        if output.startswith("~") and userprofile:
            output = output.replace("~", userprofile, 1)
        return output

    @staticmethod
    def _auto_repair_user_path_string(value: str) -> tuple[str, dict[str, str] | None]:
        text = MCPServerManager._normalize_path_placeholders(str(value or ""))
        if not text:
            return text, None

        current_user = MCPServerManager._current_username()
        if not current_user:
            return text, None

        repaired = text
        details: dict[str, str] | None = None

        windows_match = re.search(r"([A-Za-z]:\\Users\\)([^\\]+)(\\)", text)
        posix_match = re.search(r"(/Users/)([^/]+)(/)", text)

        if windows_match:
            wrong_user = str(windows_match.group(2) or "").strip()
            if wrong_user and wrong_user.lower() != current_user.lower():
                repaired = (
                    text[: windows_match.start(2)]
                    + current_user
                    + text[windows_match.end(2):]
                )
                details = {
                    "path_style": "windows",
                    "from_user": wrong_user,
                    "to_user": current_user,
                }
        elif posix_match:
            wrong_user = str(posix_match.group(2) or "").strip()
            if wrong_user and wrong_user.lower() != current_user.lower():
                repaired = (
                    text[: posix_match.start(2)]
                    + current_user
                    + text[posix_match.end(2):]
                )
                details = {
                    "path_style": "posix",
                    "from_user": wrong_user,
                    "to_user": current_user,
                }

        return repaired, details

    def auto_repair_user_paths(self) -> list[dict[str, str]]:
        """Normaliza rutas hardcodeadas con otro usuario al usuario actual."""
        repairs: list[dict[str, str]] = []
        changed = False

        for config in self._configs.values():
            command, command_details = self._auto_repair_user_path_string(config.command)
            if command_details is not None and command != config.command:
                repairs.append(
                    {
                        "server": config.name,
                        "field": "command",
                        "before": config.command,
                        "after": command,
                        **command_details,
                    }
                )
                config.command = command
                changed = True

            repaired_args: list[str] = []
            for index, arg in enumerate(config.args):
                repaired_arg, arg_details = self._auto_repair_user_path_string(str(arg or ""))
                repaired_args.append(repaired_arg)
                if arg_details is not None and repaired_arg != str(arg or ""):
                    repairs.append(
                        {
                            "server": config.name,
                            "field": f"args[{index}]",
                            "before": str(arg or ""),
                            "after": repaired_arg,
                            **arg_details,
                        }
                    )
                    changed = True
            config.args = repaired_args

            repaired_env: dict[str, str] = {}
            for key, raw_value in config.env.items():
                repaired_value, env_details = self._auto_repair_user_path_string(str(raw_value or ""))
                repaired_env[str(key)] = repaired_value
                if env_details is not None and repaired_value != str(raw_value or ""):
                    repairs.append(
                        {
                            "server": config.name,
                            "field": f"env.{key}",
                            "before": str(raw_value or ""),
                            "after": repaired_value,
                            **env_details,
                        }
                    )
                    changed = True
            config.env = repaired_env

            source, source_details = self._auto_repair_user_path_string(config.source)
            if source_details is not None and source != config.source:
                repairs.append(
                    {
                        "server": config.name,
                        "field": "source",
                        "before": config.source,
                        "after": source,
                        **source_details,
                    }
                )
                config.source = source
                changed = True

        if changed:
            self._save_configs()
            for repair in repairs:
                self._log_event(
                    "mcp_path_auto_repaired",
                    str(repair.get("server", "") or ""),
                    {
                        "field": str(repair.get("field", "") or ""),
                        "before": str(repair.get("before", "") or ""),
                        "after": str(repair.get("after", "") or ""),
                        "from_user": str(repair.get("from_user", "") or ""),
                        "to_user": str(repair.get("to_user", "") or ""),
                    },
                )
        return repairs

    @staticmethod
    def portability_status(config: MCPServerConfig) -> tuple[str, str]:
        machine = MCPServerManager.current_machine_profile()
        username = str(machine.get("username", "") or "").strip().lower()
        values: list[str] = [str(config.command or ""), str(config.source or "")]
        values.extend(str(arg or "") for arg in list(config.args or []))
        values.extend(str(value or "") for value in dict(config.env or {}).values())
        joined = "\n".join(values)
        lowered = joined.lower()
        if any(token in joined for token in ("${USERNAME}", "%USERNAME%", "%USERPROFILE%", "~")):
            return "portable", "uses_placeholders"
        path_match = re.search(r"c:\\users\\([^\\/\s]+)", lowered)
        if not path_match:
            return "portable", "no_user_bound_paths"
        bound_user = str(path_match.group(1) or "").strip().lower()
        if username and bound_user == username:
            return "user_bound", f"bound_to_current_user:{bound_user}"
        if bound_user:
            return "user_bound", f"bound_to_other_user:{bound_user}"
        return "portable", "unknown"

    @staticmethod
    def _parse_iso_ts(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except Exception:
            return None

    @staticmethod
    def classify_health_reason(reason: str, status: str = "unknown") -> str:
        normalized_status = str(status or "").strip().lower()
        if normalized_status == "healthy":
            return "healthy"

        text = str(reason or "").strip().lower()
        if not text:
            return "unknown"
        if "enoent" in text or "no such file or directory" in text or "error accessing directory" in text:
            return "path_missing"
        if "please set " in text or "environment variable" in text or "api key" in text or "token" in text:
            return "credentials_missing"
        if "please provide" in text or "command-line argument" in text or "missing required" in text:
            return "configuration_required"
        if "e404" in text or "404 not found" in text or "not found - get https://registry.npmjs.org" in text:
            return "package_unavailable"
        if text.startswith("probe_error:") or "winerror" in text or "start_failed:" in text:
            return "command_unavailable"
        if "timeout" in text:
            return "timeout"
        return "runtime_failure"

    @staticmethod
    def health_recommendation(
        *,
        category: str,
        enabled: bool,
        requires_approval: bool,
        source_type: str,
    ) -> str:
        normalized = str(category or "unknown").strip().lower()
        if normalized == "healthy":
            return "usable_now" if enabled else "enable_when_needed"
        if normalized == "path_missing":
            return "repair_path_or_workspace"
        if normalized == "credentials_missing":
            return "configure_credentials"
        if normalized == "configuration_required":
            return "provide_required_arguments"
        if normalized == "package_unavailable":
            return "replace_or_disable_catalog_entry"
        if normalized == "command_unavailable":
            return (
                "install_runtime_or_binary"
                if str(source_type or "").strip().lower() in {"npm", "uvx", "custom"}
                else "verify_command_path"
            )
        if normalized == "timeout":
            return "retry_with_longer_timeout"
        if requires_approval and not enabled:
            return "enable_and_request_approval_when_needed"
        return "inspect_runtime_logs"

    def _maybe_retry_unhealthy_servers(self, *, retry_after_seconds: int = 900, timeout: int = 10) -> None:
        """Intenta reactivar servidores unhealthy habilitados si ya pasó la ventana de retry."""
        now = datetime.now(timezone.utc)
        pending_retries: list[str] = []

        with self._lock:
            for name, config in self._configs.items():
                if not config.enabled or str(config.health_status or "").strip().lower() != "unhealthy":
                    continue
                last_checked = self._parse_iso_ts(config.last_checked)
                if last_checked is not None:
                    elapsed = (now - last_checked).total_seconds()
                    if elapsed < max(0, int(retry_after_seconds)):
                        continue
                pending_retries.append(name)

        for name in pending_retries:
            self._log_event(
                "mcp_health_retry_attempted",
                name,
                {"retry_after_seconds": int(retry_after_seconds)},
            )
            self.start_server(name, timeout=timeout)

    @staticmethod
    def _opencode_mcp_list_candidates() -> list[Path]:
        override = str(os.getenv("AITEAM_OPENCODE_MCP_LIST_PATH", "") or "").strip()
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override))

        local_appdata = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        appdata = str(os.getenv("APPDATA", "") or "").strip()
        userprofile = str(os.getenv("USERPROFILE", "") or "").strip()

        known_paths = [
            Path(local_appdata) / "OpenCode" / "mcp_list.txt" if local_appdata else None,
            Path(appdata) / "ai.opencode.desktop" / "mcp_list.txt" if appdata else None,
            Path(appdata) / "OpenCode" / "mcp_list.txt" if appdata else None,
            Path(userprofile) / ".cache" / "opencode" / "mcp_list.txt" if userprofile else None,
        ]
        for item in known_paths:
            if item is None:
                continue
            candidates.append(item)

        output: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(path)
        return output

    @staticmethod
    def parse_opencode_mcp_list(text: str) -> list[dict[str, Any]]:
        cleaned_lines: list[str] = []
        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
        for raw_line in str(text or "").splitlines():
            line = ansi_pattern.sub("", str(raw_line or "")).strip()
            if line:
                cleaned_lines.append(line)

        entries: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        status_pattern = re.compile(r"([A-Za-z0-9_.-]+)\s+(connected|failed)\s*$", re.IGNORECASE)
        for line in cleaned_lines:
            status_match = status_pattern.search(line)
            if status_match:
                if current and current.get("name") and current.get("command"):
                    entries.append(current)
                current = {
                    "name": status_match.group(1).strip().lower(),
                    "health_status": (
                        "healthy"
                        if status_match.group(2).strip().lower() == "connected"
                        else "unhealthy"
                    ),
                }
                continue

            if current is None:
                continue
            if current.get("command"):
                continue
            if line.lower().startswith("mcp error"):
                continue
            if line.startswith("npx ") or line.startswith("node ") or line.startswith("python ") or line.startswith("uvx ") or line.startswith("bunx "):
                try:
                    parts = shlex.split(line, posix=False)
                except ValueError:
                    parts = [item for item in line.split(" ") if item.strip()]
                if not parts:
                    continue
                current["command"] = parts[0]
                current["args"] = parts[1:]
                source_type = "npm" if parts[0].lower() in {"npx", "npx.cmd", "bunx", "bunx.cmd"} else "custom"
                current["source_type"] = source_type
                current["source"] = MCPServerManager._infer_opencode_source(parts)
                current["capabilities"] = MCPServerManager._infer_opencode_capabilities(
                    name=str(current.get("name", "") or ""),
                    source=str(current.get("source", "") or ""),
                )
                current["role_targets"] = MCPServerManager._infer_opencode_role_targets(
                    capabilities=list(current.get("capabilities", []) or [])
                )
                current["transport"] = "stdio"
                current["enabled"] = bool(current.get("health_status") == "healthy")
                current["requires_approval"] = False
                current["bootstrap_source"] = "opencode_mcp_list"
                continue

        if current and current.get("name") and current.get("command"):
            entries.append(current)
        return entries

    @staticmethod
    def _infer_opencode_source(parts: list[str]) -> str:
        lowered = [str(item or "").strip() for item in parts]
        if not lowered:
            return ""
        if lowered[0].lower() in {"npx", "npx.cmd", "bunx", "bunx.cmd"}:
            for item in lowered[1:]:
                if not item or item.startswith("-"):
                    continue
                return item
        return lowered[0]

    @staticmethod
    def _infer_opencode_capabilities(*, name: str, source: str) -> list[str]:
        blob = f"{name} {source}".lower()
        capabilities = {"external_mcp"}
        if "filesystem" in blob:
            capabilities.update({"repo_read"})
        if "fetch" in blob:
            capabilities.update({"documentation"})
        if "memory" in blob:
            capabilities.update({"analysis"})
        if "puppeteer" in blob or "playwright" in blob or "browser" in blob:
            capabilities.update({"browser_nav", "browser_test"})
        return sorted(capabilities)

    @staticmethod
    def _infer_opencode_role_targets(*, capabilities: list[str]) -> list[str]:
        normalized = {str(item or "").strip().lower() for item in capabilities}
        if {"browser_nav", "browser_test"} & normalized:
            return ["qa", "researcher"]
        if "repo_read" in normalized:
            return ["scout", "researcher", "engineer"]
        return []

    def _load_configs(self) -> None:
        """Carga configuraciones desde mcp_servers.json."""
        if not self._mcp_config_path.exists():
            return
        try:
            raw = json.loads(self._mcp_config_path.read_text(encoding="utf-8"))
            for item in raw.get("servers", []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                self._configs[name] = MCPServerConfig(
                    name=name,
                    command=str(item.get("command", "")).strip(),
                    args=item.get("args", []),
                    env=item.get("env", {}),
                    transport=str(item.get("transport", "stdio")).strip(),
                    enabled=bool(item.get("enabled", False)),
                    requires_approval=bool(item.get("requires_approval", False)),
                    source_type=str(item.get("source_type", "npm")).strip(),
                    source=str(item.get("source", "")).strip(),
                    capabilities=item.get("capabilities", []),
                    role_targets=item.get("role_targets", []),
                    health_status=str(item.get("health_status", "unknown")),
                    health_reason=str(item.get("health_reason", "") or "").strip(),
                    last_checked=str(item.get("last_checked", "") or "").strip(),
                    bootstrap_source=str(item.get("bootstrap_source", "") or "").strip(),
                )
            self.auto_repair_user_paths()
        except (json.JSONDecodeError, OSError):
            pass

    def sync_from_catalog(self) -> int:
        """Sincroniza servidores MCP desde el catalogo de herramientas al mcp_servers.json.

        Solo agrega MCPs nuevos que no existan ya en la config. No sobreescribe existentes.
        Retorna cantidad de servidores nuevos registrados.
        """
        if self.catalog_path is None or not self.catalog_path.exists():
            return 0

        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0

        new_count = 0
        for item in raw.get("tools", []):
            if not isinstance(item, dict):
                continue
            if str(item.get("category", "")).strip().lower() != "mcp":
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in self._configs:
                continue

            # Build command from source
            source_type = str(item.get("source_type", "npm")).strip()
            source = str(item.get("source", "")).strip()
            if source_type == "npm" and source:
                npx_bin = "npx.cmd" if os.name == "nt" else "npx"
                command = npx_bin
                args = ["-y", source]
            else:
                continue  # solo soportamos npm MCPs por ahora

            config = MCPServerConfig(
                name=name,
                command=command,
                args=args,
                transport="stdio",
                enabled=bool(item.get("enabled", False)),
                requires_approval=bool(item.get("requires_approval", False)),
                source_type=source_type,
                source=source,
                capabilities=item.get("capabilities", []),
                role_targets=item.get("role_targets", []),
            )
            self._configs[name] = config
            new_count += 1

        if new_count > 0:
            self._save_configs()

        return new_count

    def bootstrap_from_opencode(self) -> int:
        """Importa MCPs visibles en OpenCode al runtime local.

        Fuente actual: salida textual previamente exportada por `opencode mcp list`,
        por ejemplo `C:\\Users\\<user>\\AppData\\Local\\OpenCode\\mcp_list.txt`.
        Solo agrega servidores nuevos; no pisa configuraciones ya presentes.
        """
        for candidate in self._opencode_mcp_list_candidates():
            if not candidate.exists():
                continue
            try:
                raw = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            imported = self.parse_opencode_mcp_list(raw)
            if not imported:
                continue

            new_count = 0
            for item in imported:
                name = str(item.get("name", "")).strip()
                if not name or name in self._configs:
                    continue
                self._configs[name] = MCPServerConfig(
                    name=name,
                    command=str(item.get("command", "")).strip(),
                    args=list(item.get("args", []) or []),
                    env={},
                    transport=str(item.get("transport", "stdio") or "stdio").strip(),
                    enabled=bool(item.get("enabled", False)),
                    requires_approval=bool(item.get("requires_approval", False)),
                    source_type=str(item.get("source_type", "custom") or "custom").strip(),
                    source=str(item.get("source", "") or "").strip(),
                    capabilities=list(item.get("capabilities", []) or []),
                    role_targets=list(item.get("role_targets", []) or []),
                    health_status=str(item.get("health_status", "unknown") or "unknown").strip(),
                    bootstrap_source=str(item.get("bootstrap_source", "opencode_mcp_list") or "opencode_mcp_list").strip(),
                )
                new_count += 1

            if new_count > 0:
                self._save_configs()
                self._log_event(
                    "opencode_bootstrap_imported",
                    "opencode",
                    {
                        "count": new_count,
                        "path": str(candidate),
                    },
                )
            return new_count
        return 0

    def enable_servers(self, names: list[str]) -> list[str]:
        """Habilita servidores MCP por nombre. Retorna los habilitados."""
        enabled = []
        for name in names:
            config = self._configs.get(name)
            if config is not None:
                config.enabled = True
                enabled.append(name)
        if enabled:
            self._save_configs()
        return enabled

    def disable_servers(self, names: list[str]) -> list[str]:
        """Deshabilita servidores MCP y los detiene si estan corriendo."""
        disabled = []
        for name in names:
            config = self._configs.get(name)
            if config is not None:
                config.enabled = False
                disabled.append(name)
                self.stop_server(name)
        if disabled:
            self._save_configs()
        return disabled

    def start_server(self, name: str, timeout: int = 30) -> tuple[bool, str]:
        """Inicia un servidor MCP por nombre."""
        with self._lock:
            config = self._configs.get(name)
            if config is None:
                return False, f"server '{name}' not found"
            if not config.enabled:
                return False, f"server '{name}' is disabled"

            if name in self._servers and self._servers[name].is_running:
                return True, "already_running"

            proc = MCPServerProcess(config)
            ok, reason = proc.start(timeout=timeout)
            config.last_checked = datetime.now(timezone.utc).isoformat()

            if ok:
                self._servers[name] = proc
                config.health_status = "healthy"
                config.health_reason = "started"
                self._log_event("server_started", name, {"tools": len(proc.list_tools())})
            else:
                config.health_status = "unhealthy"
                config.health_reason = str(reason or "")[:500]
                self._log_event("server_start_failed", name, {"reason": reason})

            self._save_configs()
            return ok, reason

    def list_healthy(self, *, retry_unhealthy: bool = True, retry_after_seconds: int = 900, timeout: int = 10) -> list[str]:
        """Lista nombres de servidores MCP saludables y habilitados.

        Si `retry_unhealthy=True`, intenta reactivar servidores `unhealthy`
        cuya última comprobación ya quedó suficientemente atrás.
        """
        if retry_unhealthy:
            self._maybe_retry_unhealthy_servers(
                retry_after_seconds=retry_after_seconds,
                timeout=timeout,
            )
        with self._lock:
            return sorted(
                [
                    name
                    for name, config in self._configs.items()
                    if config.enabled and str(config.health_status or "").strip().lower() == "healthy"
                ]
            )

    def stop_server(self, name: str) -> None:
        """Detiene un servidor MCP."""
        with self._lock:
            proc = self._servers.pop(name, None)
            if proc is not None:
                proc.stop()
                self._log_event("server_stopped", name)

    def stop_all(self) -> None:
        """Detiene todos los servidores MCP."""
        with self._lock:
            for name in list(self._servers.keys()):
                self.stop_server(name)

    def start_enabled(self, timeout: int = 30) -> dict[str, str]:
        """Inicia todos los servidores habilitados. Retorna {name: status}."""
        results = {}
        for name, config in self._configs.items():
            if config.enabled:
                ok, reason = self.start_server(name, timeout=timeout)
                results[name] = "running" if ok else f"failed: {reason}"
        return results

    def get_server(self, name: str) -> MCPServerProcess | None:
        """Obtiene un proceso de servidor MCP activo."""
        with self._lock:
            proc = self._servers.get(name)
            if proc is not None and proc.is_running:
                return proc
            return None

    def list_tools(self, server_name: str | None = None) -> list[MCPToolInfo]:
        """Lista herramientas de un servidor especifico o de todos los activos."""
        tools = []
        with self._lock:
            targets = (
                {server_name: self._servers[server_name]}
                if server_name and server_name in self._servers
                else dict(self._servers)
            )
            for name, proc in targets.items():
                if proc.is_running:
                    tools.extend(proc.list_tools())
        return tools

    def find_tool(self, tool_name: str) -> tuple[MCPServerProcess | None, MCPToolInfo | None]:
        """Busca una herramienta en todos los servidores activos."""
        with self._lock:
            for proc in self._servers.values():
                if not proc.is_running:
                    continue
                for tool in proc.list_tools():
                    if tool.name == tool_name:
                        return proc, tool
        return None, None

    def invoke_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        session: AgentSession | None = None,
        timeout: int = 120,
    ) -> MCPInvokeResult:
        """Invoca una herramienta en un servidor MCP especifico."""
        proc = self.get_server(server_name)
        if proc is None:
            return MCPInvokeResult(
                tool_name=tool_name,
                server_name=server_name,
                success=False,
                error=f"server '{server_name}' not running",
            )

        result = proc.call_tool(tool_name, arguments, timeout=timeout)

        # Record in session
        if session is not None:
            session.record_action(
                action_type="mcp_call",
                detail=f"{server_name}/{tool_name}",
                success=result.success,
                duration_ms=result.duration_ms,
                metadata={
                    "server": server_name,
                    "tool": tool_name,
                    "is_error": result.is_error,
                },
            )

        self._log_event(
            "tool_invoked",
            server_name,
            {
                "tool": tool_name,
                "success": result.success,
                "duration_ms": result.duration_ms,
                "session_id": session.session_id if session else None,
            },
        )

        return result

    def invoke_by_capability(
        self,
        capability: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        role: str | None = None,
        session: AgentSession | None = None,
        timeout: int = 120,
    ) -> MCPInvokeResult:
        """Busca un servidor con la capability requerida e invoca la herramienta."""
        with self._lock:
            for name, proc in self._servers.items():
                if not proc.is_running:
                    continue
                config = self._configs.get(name)
                if config is None:
                    continue
                if capability not in config.capabilities:
                    continue
                if role and config.role_targets and role not in config.role_targets:
                    continue
                # Found matching server
                return self.invoke_tool(name, tool_name, arguments, session, timeout)

        return MCPInvokeResult(
            tool_name=tool_name,
            server_name="",
            success=False,
            error=f"no server with capability '{capability}' available",
        )

    def server_status(self) -> list[dict[str, Any]]:
        """Estado de todos los servidores configurados."""
        status_list = []
        with self._lock:
            for name, config in self._configs.items():
                proc = self._servers.get(name)
                running = proc is not None and proc.is_running
                tool_count = len(proc.list_tools()) if running and proc else 0
                health_category = self.classify_health_reason(
                    config.health_reason,
                    config.health_status,
                )
                portability_status, portability_reason = self.portability_status(config)
                status_list.append({
                    "name": name,
                    "enabled": config.enabled,
                    "running": running,
                    "transport": config.transport,
                    "source": config.source,
                    "capabilities": config.capabilities,
                    "role_targets": config.role_targets,
                    "health_status": config.health_status,
                    "health_reason": config.health_reason,
                    "health_category": health_category,
                    "health_recommendation": self.health_recommendation(
                        category=health_category,
                        enabled=config.enabled,
                        requires_approval=config.requires_approval,
                        source_type=config.source_type,
                    ),
                    "portability_status": portability_status,
                    "portability_reason": portability_reason,
                    "bootstrap_source": config.bootstrap_source,
                    "requires_approval": config.requires_approval,
                    "tool_count": tool_count,
                    "tools": [t.name for t in proc.list_tools()] if running and proc else [],
                })
        return status_list

    def opencode_bootstrap_status(self) -> dict[str, Any]:
        candidates = self._opencode_mcp_list_candidates()
        existing_candidates: list[str] = []
        for path in candidates:
            try:
                if path.exists():
                    existing_candidates.append(str(path))
            except OSError:
                continue
        bootstrapped = [
            config.name
            for config in self._configs.values()
            if str(config.bootstrap_source or "").strip().lower() == "opencode_mcp_list"
        ]
        latest = {}
        history = self.event_history(server_name="opencode", limit=20)
        for entry in reversed(history):
            if str(entry.get("event", "") or "") != "opencode_bootstrap_imported":
                continue
            latest = {
                "ts": str(entry.get("ts", "") or ""),
                "count": int(entry.get("count", 0) or 0),
                "path": str(entry.get("path", "") or ""),
            }
            break
        return {
            "candidate_paths": [str(path) for path in candidates],
            "existing_candidate_paths": existing_candidates,
            "available": bool(existing_candidates),
            "bootstrapped_servers": sorted(bootstrapped),
            "bootstrapped_count": len(bootstrapped),
            "last_import": latest,
        }

    def _save_configs(self) -> None:
        """Persiste la configuracion de servidores MCP."""
        servers = []
        for config in self._configs.values():
            servers.append({
                "name": config.name,
                "command": config.command,
                "args": config.args,
                "env": config.env,
                "transport": config.transport,
                "enabled": config.enabled,
                "requires_approval": config.requires_approval,
                "source_type": config.source_type,
                "source": config.source,
                "capabilities": config.capabilities,
                "role_targets": config.role_targets,
                "health_status": config.health_status,
                "health_reason": config.health_reason,
                "last_checked": config.last_checked,
                "bootstrap_source": config.bootstrap_source,
            })

        payload = {"servers": servers}
        self._mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        self._mcp_config_path.write_text(content, encoding="utf-8")

    def _log_event(self, event: str, server_name: str, metadata: dict[str, Any] | None = None) -> None:
        """Registra evento MCP en log."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "server": server_name,
            **(metadata or {}),
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._event_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError:
            pass

    def event_history(self, server_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Historial de eventos MCP."""
        if not self._event_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            raw = self._event_log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if server_name and entry.get("server") != server_name:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return entries[-limit:]
