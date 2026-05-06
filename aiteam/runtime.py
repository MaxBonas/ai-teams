from __future__ import annotations

from pathlib import Path


class SandboxManager:
    """Crea workspaces aislados por agente y tarea."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def ensure_agent_workspace(self, agent_id: str) -> Path:
        path = self.base_dir / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def task_workspace(self, agent_id: str, task_id: str) -> Path:
        safe_task_id = self._safe_segment(task_id)
        path = self.ensure_agent_workspace(agent_id) / safe_task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_segment(value: str) -> str:
        invalid = '<>:"/\\|?*'
        sanitized = value
        for char in invalid:
            sanitized = sanitized.replace(char, "_")
        return sanitized.strip(" .") or "task"
