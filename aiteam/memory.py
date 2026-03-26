from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MemoryEntry:
    ts: str
    agent_id: str
    role: str
    kind: str
    content: str
    task_id: str | None = None
    tags: list[str] | None = None


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
    ) -> None:
        entry = MemoryEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            role=role,
            kind=kind,
            content=content,
            task_id=task_id,
            tags=tags,
        )
        with self._lock:
            path = self._path_for(agent_id)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=True) + "\n")
                f.flush()

    def recent(
        self,
        agent_id: str,
        limit: int = 5,
        exclude_kinds: set[str] | None = None,
    ) -> list[MemoryEntry]:
        entries = self._filter_kinds(self._read(agent_id), exclude_kinds)
        return entries[-limit:] if limit > 0 else entries

    def relevant(
        self,
        agent_id: str,
        query: str,
        limit: int = 5,
        exclude_kinds: set[str] | None = None,
    ) -> list[MemoryEntry]:
        entries = self._filter_kinds(self._read(agent_id), exclude_kinds)
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
    def _filter_kinds(entries: list[MemoryEntry], exclude_kinds: set[str] | None) -> list[MemoryEntry]:
        if not exclude_kinds:
            return entries
        normalized = {kind.strip().lower() for kind in exclude_kinds if kind.strip()}
        if not normalized:
            return entries
        return [entry for entry in entries if entry.kind.strip().lower() not in normalized]

    def relevant_across_agents(
        self,
        query: str,
        exclude_agent: str | None = None,
        limit: int = 5,
        max_chars_per_entry: int = 500,
        exclude_kinds: set[str] | None = None,
    ) -> list[MemoryEntry]:
        """Search all agents' memories for entries relevant to *query*.

        Returns up to *limit* entries sorted by relevance (keyword overlap),
        excluding memories from *exclude_agent* (typically the requesting agent).
        """
        if not query.strip():
            return []
        kinds_to_exclude = exclude_kinds if exclude_kinds is not None else {"meeting_minutes"}
        query_tokens = self._tokens(query)
        scored: list[tuple[int, MemoryEntry]] = []
        with self._lock:
            for path in self.base_dir.glob("*.jsonl"):
                agent_id = path.stem
                if exclude_agent and agent_id == self._safe_segment(exclude_agent):
                    continue
                entries = self._filter_kinds(self._read(agent_id), kinds_to_exclude)
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
                    continue
                try:
                    entries.append(MemoryEntry(**data))
                except TypeError:
                    continue
            return entries

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
