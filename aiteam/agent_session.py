"""
Agent Session Tracking — Sesiones auditables por agente.

Cada vez que un agente es invocado para ejecutar un task, se crea una sesion
con ID unico que registra todas las acciones: invocaciones de herramientas,
llamadas a LLM, accesos a archivos, uso de MCPs, etc.

Patron inspirado en: Magentic-One (ledger), Devin (persistent sessions),
Claude Code (full tool call tracing).
"""
from __future__ import annotations

import json
import uuid
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SessionAction:
    """Una accion individual dentro de una sesion de agente."""
    ts: str
    action_type: str  # llm_call, tool_invoke, file_access, mcp_call, command_exec, message_sent
    detail: str
    success: bool = True
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentSession:
    """Sesion de trabajo de un agente en un task."""
    session_id: str
    agent_id: str
    role: str
    task_id: str
    started_at: str
    ended_at: str | None = None
    status: str = "active"  # active, completed, failed, timeout
    actions: list[SessionAction] = field(default_factory=list)
    summary: str = ""
    gate_iteration: int = 0
    tool_access: list[str] = field(default_factory=list)  # herramientas usadas
    llm_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    def record_action(
        self,
        action_type: str,
        detail: str,
        success: bool = True,
        duration_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        action = SessionAction(
            ts=datetime.now(timezone.utc).isoformat(),
            action_type=action_type,
            detail=detail,
            success=success,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )
        self.actions.append(action)
        if action_type == "llm_call":
            self.llm_calls += 1
        if action_type == "tool_invoke" and detail not in self.tool_access:
            self.tool_access.append(detail)

    def complete(self, summary: str = "", status: str = "completed") -> None:
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.summary = summary

    def elapsed_ms(self) -> int:
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at) if self.ended_at else datetime.now(timezone.utc)
            return int((end - start).total_seconds() * 1000)
        except (ValueError, TypeError):
            return 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["elapsed_ms"] = self.elapsed_ms()
        data["action_count"] = len(self.actions)
        return data

    def to_summary_dict(self) -> dict[str, Any]:
        """Resumen compacto sin la lista completa de acciones."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "summary": self.summary[:300],
            "gate_iteration": self.gate_iteration,
            "tool_access": self.tool_access,
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "elapsed_ms": self.elapsed_ms(),
            "action_count": len(self.actions),
        }


class SessionStore:
    """Almacen persistente de sesiones de agentes."""

    def __init__(self, runtime_dir: Path) -> None:
        self.sessions_dir = runtime_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = runtime_dir / "session_index.jsonl"
        self._lock = threading.RLock()
        self._active_sessions: dict[str, AgentSession] = {}

    def create_session(
        self,
        agent_id: str,
        role: str,
        task_id: str,
        gate_iteration: int = 0,
    ) -> AgentSession:
        session = AgentSession(
            session_id=str(uuid.uuid4())[:12],
            agent_id=agent_id,
            role=role,
            task_id=task_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            gate_iteration=gate_iteration,
        )
        with self._lock:
            self._active_sessions[session.session_id] = session
        return session

    def close_session(
        self,
        session: AgentSession,
        summary: str = "",
        status: str = "completed",
    ) -> None:
        session.complete(summary=summary, status=status)
        with self._lock:
            self._active_sessions.pop(session.session_id, None)
        self._persist_session(session)
        self._append_index(session)

    def get_active_sessions(self) -> list[AgentSession]:
        with self._lock:
            return list(self._active_sessions.values())

    def get_session(self, session_id: str) -> AgentSession | None:
        with self._lock:
            if session_id in self._active_sessions:
                return self._active_sessions[session_id]
        return self._load_session(session_id)

    def list_sessions(
        self,
        agent_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Lista sesiones del indice con filtros opcionales."""
        entries = self._read_index()
        if agent_id:
            entries = [e for e in entries if e.get("agent_id") == agent_id]
        if task_id:
            entries = [e for e in entries if e.get("task_id") == task_id]
        return entries[-limit:]

    def sessions_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Todas las sesiones de un task (incluyendo re-iteraciones)."""
        return self.list_sessions(task_id=task_id, limit=100)

    def agent_activity(self, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Timeline de actividad de un agente."""
        return self.list_sessions(agent_id=agent_id, limit=limit)

    # ── Persistence ──

    def _persist_session(self, session: AgentSession) -> None:
        path = self.sessions_dir / f"{session.session_id}.json"
        content = json.dumps(session.to_dict(), indent=2, ensure_ascii=False, default=str)
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.sessions_dir, suffix=".tmp",
                delete=False, encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
            tmp_path.replace(path)
        except Exception:
            if "tmp_path" in dir():
                tmp_path.unlink(missing_ok=True)

    def _append_index(self, session: AgentSession) -> None:
        entry = session.to_summary_dict()
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with self._index_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()

    def _read_index(self) -> list[dict[str, Any]]:
        if not self._index_path.exists():
            return []
        with self._lock:
            raw = self._index_path.read_text(encoding="utf-8")
        entries: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def _load_session(self, session_id: str) -> AgentSession | None:
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            actions = [SessionAction(**a) for a in raw.pop("actions", [])]
            raw.pop("elapsed_ms", None)
            raw.pop("action_count", None)
            session = AgentSession(**raw)
            session.actions = actions
            return session
        except (json.JSONDecodeError, TypeError, KeyError):
            return None
