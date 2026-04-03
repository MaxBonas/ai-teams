"""
Tool Dispatch System — Acceso auditable a herramientas, MCPs y CLIs.

Los agentes solicitan herramientas por capability, y el dispatcher:
1. Busca en el catalogo la herramienta adecuada
2. Verifica permisos (role_targets, requires_approval, compliance)
3. Invoca la herramienta
4. Registra la accion en la sesion del agente
5. Retorna el resultado

Inspirado en: CrewAI (agent tools), LangGraph (tool nodes), Claude Code (tool use).
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiteam.agent_session import AgentSession


@dataclass
class ToolResult:
    """Resultado de una invocacion de herramienta."""
    tool_name: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """Definicion de una herramienta disponible."""
    name: str
    category: str  # mcp, cli, skill, adapter, builtin
    capabilities: list[str]
    role_targets: list[str]
    enabled: bool = False
    requires_approval: bool = False
    source: str = ""
    source_type: str = ""
    description: str = ""
    uses_internet: bool = False
    priority: str = "secondary"


class ToolDispatcher:
    """Despacha herramientas a agentes con auditoria."""

    def __init__(
        self,
        catalog_path: Path,
        runtime_dir: Path,
        environment: str = "dev",
    ) -> None:
        self.catalog_path = catalog_path
        self.runtime_dir = runtime_dir
        self.environment = environment
        self._tools: dict[str, ToolDefinition] = {}
        self._tool_access_log_path = runtime_dir / "tool_access.jsonl"
        self._mcp_manager = None  # lazy init
        self._load_catalog()

    def _load_catalog(self) -> None:
        if not self.catalog_path.exists():
            return
        try:
            raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            for item in raw.get("tools", []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                self._tools[name] = ToolDefinition(
                    name=name,
                    category=str(item.get("category", "unknown")),
                    capabilities=item.get("capabilities", []),
                    role_targets=item.get("role_targets", []),
                    enabled=bool(item.get("enabled", False)),
                    requires_approval=bool(item.get("requires_approval", False)),
                    source=str(item.get("source", "")),
                    source_type=str(item.get("source_type", "")),
                    description=str(item.get("description", "")),
                    uses_internet=bool(item.get("uses_internet", False)),
                    priority=str(item.get("priority", "secondary")),
                )
        except (json.JSONDecodeError, OSError):
            pass

    def available_tools(self, role: str | None = None) -> list[ToolDefinition]:
        """Lista herramientas disponibles, opcionalmente filtradas por rol."""
        tools = list(self._tools.values())
        if role:
            tools = [t for t in tools if role in t.role_targets or not t.role_targets]
        return tools

    def enabled_tools(self, role: str | None = None) -> list[ToolDefinition]:
        """Solo herramientas habilitadas."""
        return [t for t in self.available_tools(role) if t.enabled]

    def find_by_capability(self, capability: str, role: str | None = None) -> list[ToolDefinition]:
        """Busca herramientas por capability requerida."""
        return [
            t for t in self.available_tools(role)
            if capability in t.capabilities
        ]

    def can_access(self, tool_name: str, role: str, approved: bool = False) -> tuple[bool, str]:
        """Verifica si un rol puede acceder a una herramienta."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return False, f"tool '{tool_name}' not found in catalog"
        if not tool.enabled:
            return False, f"tool '{tool_name}' is disabled"
        if tool.role_targets and role not in tool.role_targets:
            return False, f"role '{role}' not in tool targets: {tool.role_targets}"
        if tool.requires_approval and not approved:
            return False, f"tool '{tool_name}' requires approval"
        if tool.uses_internet and self.environment == "prod" and not approved:
            return False, f"tool '{tool_name}' uses internet (prod requires approval)"
        return True, "access_granted"

    def invoke_cli_tool(
        self,
        tool_name: str,
        command: str,
        session: AgentSession | None = None,
        timeout: int = 120,
        cwd: str | None = None,
    ) -> ToolResult:
        """Invoca una herramienta CLI con auditoria."""
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            success = proc.returncode == 0
            result = ToolResult(
                tool_name=tool_name,
                success=success,
                output=proc.stdout[:5000] if proc.stdout else "",
                error=proc.stderr[:2000] if proc.stderr else "",
                duration_ms=duration_ms,
                metadata={"exit_code": proc.returncode, "command": command[:200]},
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"timeout after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(exc)[:500],
                duration_ms=duration_ms,
            )

        if session:
            session.record_action(
                action_type="tool_invoke",
                detail=tool_name,
                success=result.success,
                duration_ms=result.duration_ms,
                metadata={"command": command[:200], "exit_code": result.metadata.get("exit_code")},
            )
        self._log_access(tool_name, session, result)
        return result

    def recommend_tools_for_task(
        self,
        role: str,
        required_capabilities: set[str] | None = None,
        task_description: str = "",
    ) -> list[tuple[str, str]]:
        """Recommend tools for a task based on capabilities and description.

        Returns list of (tool_name, reason) tuples.
        """
        recommendations: list[tuple[str, str]] = []
        if required_capabilities:
            for cap in required_capabilities:
                matches = self.find_by_capability(cap, role)
                for t in matches[:2]:
                    recommendations.append(
                        (t.name, f"capability '{cap}': {t.description}")
                    )

        # Keyword-based recommendation from task description
        if task_description:
            keywords_map = {
                "test": ["testing", "validation", "qa"],
                "lint": ["linting", "code_quality"],
                "security": ["security", "scanning"],
                "deploy": ["deployment", "ci_cd"],
                "database": ["database", "migration"],
                "api": ["api_testing", "http"],
                "git": ["git_operations", "version_control"],
            }
            desc_lower = task_description.lower()
            for keyword, caps in keywords_map.items():
                if keyword in desc_lower:
                    for cap in caps:
                        matches = self.find_by_capability(cap, role)
                        for t in matches[:1]:
                            if t.name not in {r[0] for r in recommendations}:
                                recommendations.append(
                                    (t.name, f"detected '{keyword}' in task: {t.description}")
                                )
        return recommendations[:6]

    def build_tool_context_for_agent(self, role: str, required_capabilities: set[str] | None = None, task_description: str = "") -> str:
        """Genera contexto de herramientas disponibles para incluir en el prompt del agente."""
        lines: list[str] = []

        # Proactive recommendations based on task
        recommendations = self.recommend_tools_for_task(role, required_capabilities, task_description)
        if recommendations:
            lines.append("Herramientas RECOMENDADAS para esta tarea:")
            for tool_name, reason in recommendations:
                lines.append(f"  * {tool_name} — {reason}")
            lines.append("")

        # Catalog tools
        tools = self.enabled_tools(role)
        if tools:
            lines.append("Herramientas disponibles para tu rol:")
            for t in tools:
                caps = ", ".join(t.capabilities)
                approval = " [requiere aprobacion]" if t.requires_approval else ""
                lines.append(f"- {t.name} ({t.category}): {t.description} [{caps}]{approval}")

        # Active MCP server tools
        mgr = self._get_mcp_manager()
        if mgr is not None:
            # Arranque lazy de filesystem_mcp para roles que escriben archivos.
            # Se hace aqui (no en orchestrator init) para no bloquear el arranque
            # del orchestrator ni crear procesos duplicados entre requests.
            _FILE_ROLES = {"engineer", "reviewer", "qa"}
            if role in _FILE_ROLES:
                self._ensure_filesystem_mcp_running(mgr)
            mcp_tools = mgr.list_tools()
            # Filter by role if possible
            role_tools = []
            for mt in mcp_tools:
                config = mgr._configs.get(mt.server_name)
                if config and config.role_targets and role not in config.role_targets:
                    continue
                role_tools.append(mt)
            if role_tools:
                lines.append("Servidores MCP activos con herramientas:")
                by_server: dict[str, list] = {}
                for mt in role_tools:
                    by_server.setdefault(mt.server_name, []).append(mt)
                for server, server_tools in by_server.items():
                    tool_names = ", ".join(t.name for t in server_tools[:10])
                    lines.append(f"- {server}: {tool_names}")

        _fs_active = role_tools and any(mt.server_name == "filesystem_mcp" for mt in role_tools)
        _FILE_ROLES = {"engineer", "reviewer", "qa"}

        # Instrucciones de escritura de archivos — SIEMPRE visibles para Engineer.
        # El sistema extrae y escribe los bloques path= automáticamente, tanto si
        # filesystem_mcp está activo como si no.  USE_TOOL write_file también funciona
        # cuando el servidor está activo, pero path= es el mecanismo principal porque:
        # - No requiere escape de JSON (no hay problemas con {} en el contenido)
        # - El LLM lo produce de forma más natural
        # - El orchestrator extrae y escribe SIEMPRE (fix_b: role == ENGINEER)
        if role == "engineer":
            if _fs_active:
                header = (
                    "ESCRITURA DE ARCHIVOS — filesystem_mcp activo. "
                    "Usa bloques path= (preferido) o [USE_TOOL server=filesystem_mcp tool=write_file ...]:"
                )
            else:
                header = (
                    "ESCRITURA DE ARCHIVOS — escribe el contenido directamente con bloques path=. "
                    "El sistema los extrae y guarda automáticamente:"
                )
            lines.append(
                f"{header}\n"
                "  ```python path=src/modulo/cli.py\n"
                "  ... contenido completo del archivo Python ...\n"
                "  ```\n"
                "  ```toml path=pyproject.toml\n"
                "  ... contenido completo del TOML ...\n"
                "  ```\n"
                "  ```markdown path=README.md\n"
                "  ... contenido del README ...\n"
                "  ```\n"
                "  ```text path=.gitignore\n"
                "  ... contenido de texto plano ...\n"
                "  ```\n"
                "Reglas de escritura:\n"
                "- Path RELATIVO al workspace. Un archivo por bloque. Máximo 10 archivos.\n"
                "- Contenido COMPLETO y FUNCIONAL — sin fragmentos ni pseudocódigo.\n"
                "- El sistema escribe los archivos automáticamente al parsear tu output."
            )
        elif role in _FILE_ROLES and not _fs_active:
            # Para Reviewer/QA cuando filesystem_mcp no está disponible
            lines.append(
                "filesystem_mcp no disponible. Para modificar archivos usa bloques path=:\n"
                "  ```lang path=ruta/relativa/archivo.py\n  ... contenido ...\n  ```"
            )

        if lines:
            lines.append("")
            lines.append(
                "Para invocar herramientas (comandos, tests, git):\n"
                '  [USE_TOOL server=<server> tool=<tool_name> args={"key": "value"}]\n'
                "  Sin server (CLI): [USE_TOOL tool=<tool_name> args={\"command\": \"cmd args\"}]\n"
                "Maximo 8 invocaciones por tarea. Los resultados se anexaran a tu output."
            )

        return "\n".join(lines) if lines else ""

    # ── MCP Integration ──────────────────────────────────────────

    _FILESYSTEM_MCP_START_TIMEOUT = 20  # segundos

    def _ensure_filesystem_mcp_running(self, mgr) -> None:
        """Arranca filesystem_mcp si está habilitado y configurado con workspace.

        Se llama de forma lazy desde build_tool_context_for_agent, evitando:
        - Bloquear el init del orchestrator (20s de timeout)
        - Spawnear procesos duplicados: si ya está en _servers y corriendo, no-op

        Requisito: el workspace ya debe estar inyectado en config.args por
        AITeamOrchestrator._inject_filesystem_mcp_workspace().
        """
        try:
            config = mgr._configs.get("filesystem_mcp")
            if config is None or not config.enabled:
                return

            # Solo arrancar si hay al menos un path de workspace en los args
            # (más allá de los flags de npx como -y y el nombre del paquete)
            npm_flags = {"-y", "--yes", "-g", "--global"}
            has_workspace_path = any(
                not a.startswith("-") and "@" not in a
                for a in config.args
                if a not in npm_flags
            )
            if not has_workspace_path:
                return  # workspace no inyectado aún — no tiene sentido arrancar

            # Si ya está corriendo en esta instancia, no-op
            proc = mgr._servers.get("filesystem_mcp")
            if proc is not None and proc.is_running:
                return

            mgr.start_server("filesystem_mcp", timeout=self._FILESYSTEM_MCP_START_TIMEOUT)
        except Exception:
            pass  # fallo no-fatal: el Engineer simplemente no verá las herramientas

    def _get_mcp_manager(self):
        """Lazy-init del MCPServerManager."""
        if self._mcp_manager is None:
            try:
                from aiteam.mcp_manager import MCPServerManager
                self._mcp_manager = MCPServerManager(
                    runtime_dir=self.runtime_dir,
                    catalog_path=self.catalog_path,
                    environment=self.environment,
                )
            except Exception:
                pass
        return self._mcp_manager

    @property
    def mcp_manager(self):
        return self._get_mcp_manager()

    def invoke_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        session: AgentSession | None = None,
        timeout: int = 120,
    ) -> ToolResult:
        """Invoca una herramienta MCP a traves del MCPServerManager."""
        mgr = self._get_mcp_manager()
        if mgr is None:
            return ToolResult(
                tool_name=f"{server_name}/{tool_name}",
                success=False,
                error="mcp_manager_not_available",
            )

        # Ensure server is running
        proc = mgr.get_server(server_name)
        if proc is None:
            ok, reason = mgr.start_server(server_name)
            if not ok:
                result = ToolResult(
                    tool_name=f"{server_name}/{tool_name}",
                    success=False,
                    error=f"server_start_failed: {reason}",
                )
                self._log_access(f"{server_name}/{tool_name}", session, result)
                return result

        mcp_result = mgr.invoke_tool(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
            session=session,
            timeout=timeout,
        )

        result = ToolResult(
            tool_name=f"{server_name}/{tool_name}",
            success=mcp_result.success,
            output=mcp_result.text[:5000],
            error=mcp_result.error[:2000] if mcp_result.error else "",
            duration_ms=mcp_result.duration_ms,
            metadata={
                "server": server_name,
                "mcp_tool": tool_name,
                "is_error": mcp_result.is_error,
            },
        )
        self._log_access(f"{server_name}/{tool_name}", session, result)
        return result

    def invoke_mcp_by_capability(
        self,
        capability: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        role: str | None = None,
        session: AgentSession | None = None,
        timeout: int = 120,
    ) -> ToolResult:
        """Busca un MCP con la capability requerida e invoca la herramienta."""
        mgr = self._get_mcp_manager()
        if mgr is None:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error="mcp_manager_not_available",
            )

        mcp_result = mgr.invoke_by_capability(
            capability=capability,
            tool_name=tool_name,
            arguments=arguments,
            role=role,
            session=session,
            timeout=timeout,
        )

        result = ToolResult(
            tool_name=f"{mcp_result.server_name}/{tool_name}" if mcp_result.server_name else tool_name,
            success=mcp_result.success,
            output=mcp_result.text[:5000],
            error=mcp_result.error[:2000] if mcp_result.error else "",
            duration_ms=mcp_result.duration_ms,
            metadata={
                "server": mcp_result.server_name,
                "mcp_tool": tool_name,
                "capability": capability,
            },
        )
        self._log_access(tool_name, session, result)
        return result

    def list_mcp_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """Lista herramientas MCP disponibles en servidores activos."""
        mgr = self._get_mcp_manager()
        if mgr is None:
            return []
        tools = mgr.list_tools(server_name)
        return [
            {
                "name": t.name,
                "description": t.description,
                "server": t.server_name,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    def mcp_server_status(self) -> list[dict[str, Any]]:
        """Estado de todos los servidores MCP."""
        mgr = self._get_mcp_manager()
        if mgr is None:
            return []
        return mgr.server_status()

    def _log_access(self, tool_name: str, session: AgentSession | None, result: ToolResult) -> None:
        record = {
            "ts": result.metadata.get("ts", ""),
            "tool_name": tool_name,
            "session_id": session.session_id if session else None,
            "agent_id": session.agent_id if session else None,
            "task_id": session.task_id if session else None,
            "success": result.success,
            "duration_ms": result.duration_ms,
            "error": result.error[:200] if result.error else "",
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._tool_access_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError:
            pass

    def tool_access_history(
        self,
        agent_id: str | None = None,
        tool_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Historial de acceso a herramientas con filtros."""
        if not self._tool_access_log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            raw = self._tool_access_log_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if agent_id and entry.get("agent_id") != agent_id:
                        continue
                    if tool_name and entry.get("tool_name") != tool_name:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return entries[-limit:]
