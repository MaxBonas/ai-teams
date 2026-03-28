from __future__ import annotations

import json
import logging
import os
import re
import threading

_log = logging.getLogger(__name__)
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Límite de entradas por agente. Cuando se supera por 25%, se compacta al límite.
_MAX_ENTRIES: int = int(os.getenv("AITEAM_MEMORY_MAX_ENTRIES", "2000"))

# Patrones para redactar secretos en contenido de memoria
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}", re.IGNORECASE),        # OpenAI/Anthropic keys
    re.compile(r"gsk_[A-Za-z0-9]{20,}", re.IGNORECASE),          # Groq keys
    re.compile(r"AIza[A-Za-z0-9\-_]{30,}", re.IGNORECASE),       # Google API keys
    re.compile(r"(?i)(api[_\-]?key|token|secret|password)\s*[=:]\s*\S{8,}", re.IGNORECASE),
]


def _redact_secrets(text: str) -> str:
    """Redacta patrones de secretos conocidos del texto antes de persistir."""
    if not text:
        return text
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


@dataclass
class MemoryEntry:
    ts: str
    agent_id: str
    role: str
    kind: str
    content: str
    task_id: str | None = None
    tags: list[str] | None = None
    project_key: str | None = None


class AgentMemoryStore:
    """Memoria persistente por agente con busqueda simple por relevancia."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._lock = threading.RLock()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def remember(
        self,
        agent_id: str,
        role: str,
        kind: str,
        content: str,
        task_id: str | None = None,
        tags: list[str] | None = None,
        project_key: str | None = None,
    ) -> None:
        entry = MemoryEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            role=role,
            kind=kind,
            content=_redact_secrets(content),
            task_id=task_id,
            tags=tags,
            project_key=project_key,
        )
        with self._lock:
            path = self._path_for(agent_id)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=True) + "\n")
                f.flush()
            self._compact_if_needed(path)

    def recent(
        self,
        agent_id: str,
        limit: int = 5,
        exclude_kinds: set[str] | None = None,
        project_key: str | None = None,
    ) -> list[MemoryEntry]:
        entries = self._filter_project(
            self._filter_kinds(self._read(agent_id), exclude_kinds),
            project_key,
        )
        return entries[-limit:] if limit > 0 else entries

    def relevant(
        self,
        agent_id: str,
        query: str,
        limit: int = 5,
        exclude_kinds: set[str] | None = None,
        project_key: str | None = None,
    ) -> list[MemoryEntry]:
        entries = self._filter_project(
            self._filter_kinds(self._read(agent_id), exclude_kinds),
            project_key,
        )
        if not query.strip() or not entries:
            return []

        query_tokens = self._tokens(query)
        scored: list[tuple[int, MemoryEntry]] = []
        for entry in entries:
            text = f"{entry.kind} {entry.content} {' '.join(entry.tags or [])}"
            overlap = len(query_tokens.intersection(self._tokens(text)))
            if overlap > 0:
                scored.append((overlap, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    @staticmethod
    def _filter_kinds(
        entries: list[MemoryEntry], exclude_kinds: set[str] | None
    ) -> list[MemoryEntry]:
        if not exclude_kinds:
            return entries
        normalized = {kind.strip().lower() for kind in exclude_kinds if kind.strip()}
        if not normalized:
            return entries
        return [
            entry for entry in entries if entry.kind.strip().lower() not in normalized
        ]

    @staticmethod
    def _filter_project(
        entries: list[MemoryEntry], project_key: str | None
    ) -> list[MemoryEntry]:
        if not project_key:
            return entries
        return [entry for entry in entries if entry.project_key == project_key]

    def relevant_across_agents(
        self,
        query: str,
        exclude_agent: str | None = None,
        limit: int = 5,
        max_chars_per_entry: int = 500,
        exclude_kinds: set[str] | None = None,
        project_key: str | None = None,
    ) -> list[MemoryEntry]:
        """Search all agents' memories for entries relevant to *query*.

        Returns up to *limit* entries sorted by relevance (keyword overlap),
        excluding memories from *exclude_agent* (typically the requesting agent).
        """
        if not query.strip():
            return []
        kinds_to_exclude = (
            exclude_kinds if exclude_kinds is not None else {"meeting_minutes"}
        )
        query_tokens = self._tokens(query)
        scored: list[tuple[int, MemoryEntry]] = []
        with self._lock:
            for path in self.base_dir.glob("*.jsonl"):
                agent_id = path.stem
                if exclude_agent and agent_id == self._safe_segment(exclude_agent):
                    continue
                entries = self._filter_project(
                    self._filter_kinds(self._read(agent_id), kinds_to_exclude),
                    project_key,
                )
                for entry in entries:
                    text = f"{entry.kind} {entry.content} {' '.join(entry.tags or [])}"
                    overlap = len(query_tokens.intersection(self._tokens(text)))
                    if overlap > 0:
                        scored.append((overlap, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        results = scored[:limit]
        # Truncate content to keep prompt size reasonable
        trimmed: list[MemoryEntry] = []
        for _score, entry in results:
            if len(entry.content) > max_chars_per_entry:
                entry = MemoryEntry(
                    ts=entry.ts,
                    agent_id=entry.agent_id,
                    role=entry.role,
                    kind=entry.kind,
                    content=entry.content[:max_chars_per_entry] + "...",
                    task_id=entry.task_id,
                    tags=entry.tags,
                    project_key=entry.project_key,
                )
            trimmed.append(entry)
        return trimmed

    def list_agents(self) -> list[str]:
        with self._lock:
            agents = []
            for path in self.base_dir.glob("*.jsonl"):
                agents.append(path.stem)
            return sorted(agents)

    def count(self, agent_id: str) -> int:
        return len(self._read(agent_id))

    def _path_for(self, agent_id: str) -> Path:
        safe = self._safe_segment(agent_id)
        path = self.base_dir / f"{safe}.jsonl"
        if not path.exists():
            path.write_text("", encoding="utf-8")
        return path

    def _read(self, agent_id: str) -> list[MemoryEntry]:
        with self._lock:
            path = self._path_for(agent_id)
            raw = path.read_text(encoding="utf-8")
            entries: list[MemoryEntry] = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    _log.warning("memory: línea JSON corrupta ignorada en %s", path.name)
                    continue
                try:
                    entries.append(MemoryEntry(**data))
                except TypeError:
                    continue
            return entries

    def _compact_if_needed(self, path: Path) -> None:
        """Si el archivo supera _MAX_ENTRIES * 1.25 líneas, compacta al límite."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            threshold = int(_MAX_ENTRIES * 1.25)
            if len(lines) <= threshold:
                return
            kept = [ln for ln in lines if ln.strip()][-_MAX_ENTRIES:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _safe_segment(value: str) -> str:
        invalid = '<>:"/\\|?*'
        sanitized = value
        for char in invalid:
            sanitized = sanitized.replace(char, "_")
        return sanitized.strip(" .") or "agent"

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return set(re.findall(r"[A-Za-z0-9_\-]+", value.lower()))
