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
    action_type: (
        str  # llm_call, tool_invoke, file_access, mcp_call, command_exec, message_sent
    )
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
            end = (
                datetime.fromisoformat(self.ended_at)
                if self.ended_at
                else datetime.now(timezone.utc)
            )
            return int((end - start).total_seconds() * 1000)
        except (ValueError, TypeError):
            return 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["elapsed_ms"] = self.elapsed_ms()
        data["action_count"] = len(self.actions)
        return data

    def to_summary_dict(self) -> dict[str, Any]:
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


@dataclass
class ConversationTurn:
    ts: str
    role: str
    content: str
    source: str = "task"
    task_id: str | None = None
    message_id: str | None = None


@dataclass
class ConversationThread:
    thread_id: str
    agent_id: str
    project_key: str
    created_at: str
    last_updated: str
    turns: list[ConversationTurn] = field(default_factory=list)
    consumed_message_ids: list[str] = field(default_factory=list)

    def append_turn(
        self,
        role: str,
        content: str,
        source: str = "task",
        task_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        normalized = content.strip()
        if not normalized:
            return
        if self.turns:
            last = self.turns[-1]
            if (
                last.role == role
                and last.source == source
                and (last.task_id or "") == (task_id or "")
                and last.content.strip() == normalized
            ):
                if message_id and message_id not in self.consumed_message_ids:
                    self.consumed_message_ids.append(message_id)
                return
        now = datetime.now(timezone.utc).isoformat()
        self.turns.append(
            ConversationTurn(
                ts=now,
                role=role,
                content=normalized,
                source=source,
                task_id=task_id,
                message_id=message_id,
            )
        )
        self.last_updated = now
        if message_id and message_id not in self.consumed_message_ids:
            self.consumed_message_ids.append(message_id)
        self._compact_turns()

    def recent_turns(self, limit: int = 8) -> list[ConversationTurn]:
        return self.turns[-limit:] if limit > 0 else list(self.turns)

    def has_consumed_message(self, message_id: str) -> bool:
        return message_id in self.consumed_message_ids

    def _compact_turns(
        self,
        max_turns: int = 9,
        keep_recent: int = 8,
        max_chars: int = 60_000,
    ) -> None:
        """Compacta el thread cuando supera max_turns O max_chars de contenido.

        El limite de caracteres evita context overflow en modelos con ventana
        limitada (aprox. 60k chars ~ 15k tokens, dejando margen para system +
        task actual). Cuando hay overflow de chars, ajusta keep_recent dinamicamente
        para que los turnos retenidos queden bajo el 70% del limite.
        """
        total_chars = sum(len(t.content) for t in self.turns)
        turns_overflow = len(self.turns) > max_turns
        chars_overflow = total_chars > max_chars

        if not turns_overflow and not chars_overflow:
            return

        # En overflow de chars: recalcular cuantos turnos recientes caben.
        if chars_overflow:
            target = int(max_chars * 0.7)
            chars_accumulated = 0
            keep_count = 0
            for turn in reversed(self.turns):
                chars_accumulated += len(turn.content)
                if chars_accumulated > target:
                    break
                keep_count += 1
            keep_recent = max(2, keep_count)

        overflow = self.turns[:-keep_recent]
        kept = self.turns[-keep_recent:]
        snippets = []
        for turn in overflow[:3]:
            text = turn.content.strip().replace("\n", " ")[:80]
            snippets.append(f"{turn.role}:{text}")
        summary_text = (
            f"Resumen de {len(overflow)} turnos previos"
            + (f" ({total_chars} chars)" if chars_overflow else "")
            + ": "
            + " | ".join(snippets)
        )
        summary = ConversationTurn(
            ts=datetime.now(timezone.utc).isoformat(),
            role="system",
            content=summary_text[:400],
            source="summary",
        )
        self.turns = [summary] + kept


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
        content = json.dumps(
            session.to_dict(), indent=2, ensure_ascii=False, default=str
        )
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.sessions_dir,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
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


class ThreadStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.threads_dir = runtime_dir / "sessions" / "threads"
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get_thread(self, agent_id: str, project_key: str) -> ConversationThread:
        path = self._path_for(agent_id=agent_id, project_key=project_key)
        with self._lock:
            thread = self._load_thread(path)
            if thread is not None:
                return thread
            now = datetime.now(timezone.utc).isoformat()
            created = ConversationThread(
                thread_id=str(uuid.uuid4())[:12],
                agent_id=agent_id,
                project_key=project_key,
                created_at=now,
                last_updated=now,
            )
            self._persist_thread(path, created)
            return created

    def save_thread(self, thread: ConversationThread) -> None:
        path = self._path_for(agent_id=thread.agent_id, project_key=thread.project_key)
        with self._lock:
            self._persist_thread(path, thread)

    def _path_for(self, agent_id: str, project_key: str) -> Path:
        safe_agent = self._slug(agent_id)
        safe_project = self._slug(project_key)
        return self.threads_dir / f"{safe_agent}__{safe_project}.json"

    @staticmethod
    def _slug(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
        compact = "_".join(part for part in cleaned.split("_") if part)
        return compact[:120] or "default"

    def _persist_thread(self, path: Path, thread: ConversationThread) -> None:
        content = json.dumps(asdict(thread), indent=2, ensure_ascii=False, default=str)
        import tempfile

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.threads_dir,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
            tmp_path.replace(path)
        except Exception:
            if "tmp_path" in dir():
                tmp_path.unlink(missing_ok=True)

    def _load_thread(self, path: Path) -> ConversationThread | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            turns = [ConversationTurn(**item) for item in raw.pop("turns", [])]
            thread = ConversationThread(**raw)
            thread.turns = turns
            return thread
        except (json.JSONDecodeError, TypeError, KeyError):
            return None
