from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path


class FileLockRegistry:
    """Registro persistente de ownership de archivos por tarea."""

    def __init__(self, lock_file: Path) -> None:
        self.lock_file = lock_file
        self._lock = threading.RLock()
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.lock_file.exists():
            self.lock_file.write_text("{}\n", encoding="utf-8")

    def acquire(self, task_id: str, files: list[str]) -> tuple[bool, list[str]]:
        with self._lock:
            locks = self._load()
            conflicts = [f for f in files if f in locks and locks[f] != task_id]
            if conflicts:
                return False, conflicts
            for file_path in files:
                locks[file_path] = task_id
            self._save(locks)
            return True, []

    def release_for_task(self, task_id: str) -> None:
        with self._lock:
            locks = self._load()
            next_locks = {path: owner for path, owner in locks.items() if owner != task_id}
            self._save(next_locks)

    def _load(self) -> dict[str, str]:
        raw = self.lock_file.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, locks: dict[str, str]) -> None:
        content = json.dumps(locks, indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.lock_file.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp_path = Path(tmp.name)
            try:
                tmp.write(content)
                tmp.flush()
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        try:
            tmp_path.replace(self.lock_file)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


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
