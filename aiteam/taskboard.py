from __future__ import annotations

import json
import threading
from pathlib import Path

from aiteam.runtime import FileLockRegistry
from aiteam.types import TaskState, WorkTask


class TaskBoard:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self._tasks: dict[str, WorkTask] = {}
        self._file_locks = FileLockRegistry(storage_path.parent / "file_locks.json")
        self._load()

    def add_task(self, task: WorkTask) -> None:
        with self._lock:
            if task.task_id in self._tasks:
                raise ValueError(f"Task already exists: {task.task_id}")
            if task.dependencies:
                task.state = TaskState.PENDING
            else:
                task.state = TaskState.READY
            self._tasks[task.task_id] = task
            self._save()

    def get_task(self, task_id: str) -> WorkTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[WorkTask]:
        with self._lock:
            return list(self._tasks.values())

    def ready_tasks(self) -> list[WorkTask]:
        with self._lock:
            self._refresh_readiness()
            return [t for t in self._tasks.values() if t.state == TaskState.READY]

    def claim_task(self, task_id: str, assignee: str) -> bool:
        with self._lock:
            self._refresh_readiness()
            task = self._tasks.get(task_id)
            if not task or task.state != TaskState.READY:
                return False
            owned_files = self._owned_files(task)
            if owned_files:
                acquired, conflicts = self._file_locks.acquire(
                    task_id=task.task_id, files=owned_files
                )
                if not acquired:
                    task.state = TaskState.BLOCKED
                    task.metadata["blocked_by_files"] = conflicts
                    task.metadata["owned_files"] = owned_files
                    self._save()
                    return False
            task.state = TaskState.CLAIMED
            task.assignee = assignee
            self._save()
            return True

    def mark_completed(self, task_id: str, details: str = "") -> None:
        with self._lock:
            task = self._require(task_id)
            task.state = TaskState.COMPLETED
            self._file_locks.release_for_task(task_id)
            if details:
                task.metadata["result"] = details
            self._refresh_readiness()
            self._save()

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._require(task_id)
            task.state = TaskState.FAILED
            self._file_locks.release_for_task(task_id)
            task.metadata["error"] = error
            self._refresh_readiness()
            self._save()

    def mark_blocked(self, task_id: str, reason: str) -> None:
        with self._lock:
            task = self._require(task_id)
            task.state = TaskState.BLOCKED
            task.metadata["blocked_reason"] = reason
            self._save()

    def update_metadata(self, task_id: str, patch: dict) -> None:
        with self._lock:
            task = self._require(task_id)
            task.metadata.update(patch)
            self._save()

    def remove_tasks(self, task_ids: list[str]) -> None:
        """Elimina tareas del taskboard (usado para limpiar gates antes de re-iteracion)."""
        with self._lock:
            for task_id in task_ids:
                if task_id in self._tasks:
                    self._file_locks.release_for_task(task_id)
                    del self._tasks[task_id]
            self._save()

    def retry_task(
        self, task_id: str, reason: str, assignee: str | None = None
    ) -> None:
        with self._lock:
            task = self._require(task_id)
            self._file_locks.release_for_task(task_id)
            task.state = TaskState.READY
            task.assignee = assignee
            task.metadata.pop("error", None)
            task.metadata.pop("blocked_reason", None)
            task.metadata.pop("blocked_dependencies", None)
            task.metadata["retry_reason"] = reason
            task.metadata["retry_count"] = int(task.metadata.get("retry_count", 0)) + 1
            self._refresh_readiness()
            self._save()

    def _refresh_readiness(self) -> None:
        for task in self._tasks.values():
            if task.state in (TaskState.COMPLETED, TaskState.CLAIMED, TaskState.FAILED):
                continue
            if task.state == TaskState.BLOCKED:
                if task.metadata.get("blocked_by_files"):
                    # Se reevalua solo al liberar locks, manteniendo trazabilidad de bloqueo.
                    task.state = TaskState.PENDING
                    task.metadata.pop("blocked_by_files", None)
                elif task.metadata.get("blocked_reason") != "dependency_failed":
                    continue
            if not task.dependencies:
                task.state = TaskState.READY
                continue

            failed_dependencies = [
                dep
                for dep in task.dependencies
                if dep in self._tasks and self._tasks[dep].state == TaskState.FAILED
            ]
            if failed_dependencies:
                task.state = TaskState.BLOCKED
                task.metadata["blocked_reason"] = "dependency_failed"
                task.metadata["blocked_dependencies"] = failed_dependencies
                continue

            if task.metadata.get("blocked_reason") == "dependency_failed":
                task.metadata.pop("blocked_reason", None)
                task.metadata.pop("blocked_dependencies", None)

            unresolved = [
                dep
                for dep in task.dependencies
                if dep not in self._tasks
                or self._tasks[dep].state != TaskState.COMPLETED
            ]
            task.state = TaskState.PENDING if unresolved else TaskState.READY

    @staticmethod
    def _owned_files(task: WorkTask) -> list[str]:
        raw = task.metadata.get("owned_files", [])
        if not isinstance(raw, list):
            return []
        return [str(path) for path in raw]

    def _require(self, task_id: str) -> WorkTask:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Task not found: {task_id}")
        return task

    def _save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [self._task_to_dict(task) for task in self._tasks.values()]
        import tempfile

        content = json.dumps(payload, indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.storage_path.parent,
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
        import time

        last_err: Exception | None = None
        for attempt in range(5):
            try:
                tmp_path.replace(self.storage_path)
                last_err = None
                break
            except PermissionError as exc:
                last_err = exc
                time.sleep(0.05 * (attempt + 1))
        if last_err is not None:
            # Fallback: write directly (non-atomic but safe enough under the lock)
            try:
                self.storage_path.write_text(content, encoding="utf-8")
                tmp_path.unlink(missing_ok=True)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise last_err

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = self.storage_path.read_text(encoding="utf-8", errors="replace")
        if not raw.strip():
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, list):
            return
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                task = self._task_from_dict(item)
            except (KeyError, ValueError, TypeError):
                continue
            self._tasks[task.task_id] = task

    @staticmethod
    def _task_to_dict(task: WorkTask) -> dict:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "role": task.role.value,
            "complexity": task.complexity.value,
            "criticality": task.criticality.value,
            "dependencies": task.dependencies,
            "state": task.state.value,
            "assignee": task.assignee,
            "metadata": task.metadata,
        }

    @staticmethod
    def _task_from_dict(item: dict) -> WorkTask:
        from aiteam.types import Complexity, Criticality, Role

        return WorkTask(
            task_id=item["task_id"],
            title=item["title"],
            description=item["description"],
            role=Role(item["role"]),
            complexity=Complexity(item["complexity"]),
            criticality=Criticality(item["criticality"]),
            dependencies=item.get("dependencies", []),
            state=TaskState(item.get("state", TaskState.PENDING.value)),
            assignee=item.get("assignee"),
            metadata=item.get("metadata", {}),
        )
