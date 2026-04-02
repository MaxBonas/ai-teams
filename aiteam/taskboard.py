from __future__ import annotations

import json
import threading
from pathlib import Path

from aiteam.runtime import FileLockRegistry
from aiteam.types import TaskState, WorkTask


class TaskBoard:
    def __init__(
        self,
        storage_path: Path,
        *,
        legacy_snapshot_path: Path | None = None,
    ) -> None:
        from aiteam.sqlite_store import SqliteStore
        storage_path = Path(storage_path)
        if storage_path.name.lower() == "aiteam.db":
            self.db_path = storage_path
            self.legacy_snapshot_path = legacy_snapshot_path or storage_path.with_name("tasks.json")
        else:
            self.legacy_snapshot_path = legacy_snapshot_path or storage_path
            self.db_path = storage_path.with_name("aiteam.db")
        self.storage_path = self.legacy_snapshot_path
        self._lock = threading.RLock()
        self._tasks: dict[str, WorkTask] = {}
        self._file_locks = FileLockRegistry(self.db_path.parent / "file_locks.json")
        self._store = SqliteStore(self.db_path)
        self._load()

    @classmethod
    def from_runtime_dir(cls, runtime_dir: Path) -> "TaskBoard":
        runtime_dir = Path(runtime_dir)
        return cls(
            runtime_dir / "aiteam.db",
            legacy_snapshot_path=runtime_dir / "tasks.json",
        )

    def add_task(self, task: WorkTask) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            if task.task_id in self._tasks:
                raise ValueError(f"Task already exists: {task.task_id}")
            if task.dependencies:
                task.state = TaskState.PENDING
            else:
                task.state = TaskState.READY
            self._tasks[task.task_id] = task
            self._persist_task_changes(before)

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

    def checkpoint(self) -> None:
        with self._lock:
            return

    def persist_tasks(self, task_ids: list[str]) -> None:
        with self._lock:
            payload = [
                self._task_to_dict(self._tasks[task_id])
                for task_id in task_ids
                if task_id in self._tasks
            ]
            self._store.upsert_tasks(payload)

    def claim_task(self, task_id: str, assignee: str) -> bool:
        with self._lock:
            before = self._snapshot_tasks()
            self._refresh_readiness()
            task = self._tasks.get(task_id)
            if not task or task.state != TaskState.READY:
                return False
            if task.dependencies:
                unmet = [
                    dep
                    for dep in task.dependencies
                    if dep not in self._tasks
                    or self._tasks[dep].state != TaskState.COMPLETED
                ]
                if unmet:
                    failed_deps = [
                        dep
                        for dep in unmet
                        if dep in self._tasks
                        and self._tasks[dep].state in (TaskState.FAILED, TaskState.BLOCKED)
                    ]
                    if failed_deps:
                        task.state = TaskState.BLOCKED
                        task.metadata["blocked_reason"] = "dependency_failed"
                        task.metadata["blocked_dependencies"] = failed_deps
                    else:
                        task.state = TaskState.PENDING
                    self._persist_task_changes(before)
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
                    self._persist_task_changes(before)
                    return False
            task.state = TaskState.CLAIMED
            task.assignee = assignee
            self._persist_task_changes(before)
            return True

    def mark_completed(self, task_id: str, details: str = "") -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.COMPLETED
            self._file_locks.release_for_task(task_id)
            if details:
                task.metadata["result"] = details
            self._refresh_readiness()
            self._persist_task_changes(before)

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.FAILED
            self._file_locks.release_for_task(task_id)
            task.metadata["error"] = error
            self._refresh_readiness()
            self._persist_task_changes(before)

    def mark_blocked(self, task_id: str, reason: str) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.BLOCKED
            task.metadata["blocked_reason"] = reason
            self._persist_task_changes(before)

    def mark_waiting_user(self, task_id: str, question: str) -> None:
        """E7-D4: Pausa una tarea mid-run esperando respuesta del usuario.

        La tarea queda en estado WAITING_USER hasta que el usuario responda
        via POST /api/aiteam/chat/clarify. _refresh_readiness ignora este estado,
        por lo que las tareas downstream permanecen PENDING hasta que se reanude.
        """
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            self._file_locks.release_for_task(task_id)
            task.state = TaskState.WAITING_USER
            task.metadata["clarify_question"] = question
            import datetime as _dt
            task.metadata["waiting_since"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            self._persist_task_changes(before)

    def update_metadata(self, task_id: str, patch: dict) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.metadata.update(patch)
            self._persist_task_changes(before)

    def archive_incomplete_tasks(self, reason: str) -> list[str]:
        """C2: Marca como ARCHIVED las tareas no terminadas de runs previas.

        Las tareas en PENDING, BLOCKED o WAITING_USER quedan archivadas con el
        motivo indicado. Las tareas COMPLETED, FAILED o ARCHIVED no se tocan.
        Retorna la lista de task_ids archivadas.
        """
        archivable_states = {TaskState.PENDING, TaskState.READY, TaskState.BLOCKED, TaskState.WAITING_USER}
        with self._lock:
            before = self._snapshot_tasks()
            archived: list[str] = []
            for task in self._tasks.values():
                if task.state in archivable_states:
                    self._file_locks.release_for_task(task.task_id)
                    task.state = TaskState.ARCHIVED
                    task.metadata["archived_reason"] = reason
                    archived.append(task.task_id)
            if archived:
                self._persist_task_changes(before)
            return archived

    def remove_tasks(self, task_ids: list[str]) -> None:
        """Elimina tareas del taskboard (usado para limpiar gates antes de re-iteracion)."""
        with self._lock:
            before = self._snapshot_tasks()
            for task_id in task_ids:
                if task_id in self._tasks:
                    self._file_locks.release_for_task(task_id)
                    del self._tasks[task_id]
            self._persist_task_changes(before, deleted_ids=task_ids)

    def retry_task(
        self, task_id: str, reason: str, assignee: str | None = None
    ) -> None:
        with self._lock:
            before = self._snapshot_tasks()
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
            self._persist_task_changes(before)

    def _refresh_readiness(self) -> None:
        for task in self._tasks.values():
            if task.state in (
                TaskState.COMPLETED, TaskState.CLAIMED, TaskState.FAILED,
                TaskState.WAITING_USER,  # E7-D4: no propagar ni resetear tareas en pausa
                TaskState.ARCHIVED,      # C2: terminal state, no propagate or reset
            ):
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
                if dep in self._tasks and self._tasks[dep].state in (TaskState.FAILED, TaskState.BLOCKED)
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
        payload = [self._task_to_dict(task) for task in self._tasks.values()]
        self._store.upsert_tasks(payload)

    def _snapshot_tasks(self) -> dict[str, str]:
        return {
            task_id: json.dumps(
                self._task_to_dict(task),
                ensure_ascii=False,
                sort_keys=True,
            )
            for task_id, task in self._tasks.items()
        }

    def _persist_task_changes(
        self,
        before: dict[str, str],
        *,
        deleted_ids: list[str] | None = None,
    ) -> None:
        after = self._snapshot_tasks()
        changed_payload = [
            self._task_to_dict(self._tasks[task_id])
            for task_id, serialized in after.items()
            if before.get(task_id) != serialized and task_id in self._tasks
        ]
        if changed_payload:
            self._store.upsert_tasks(changed_payload)
        removed = [
            task_id
            for task_id in list(deleted_ids or [])
            if task_id in before and task_id not in after
        ]
        if removed:
            self._store.delete_tasks(removed)

    def _load(self) -> None:
        import sqlite3
        try:
            payload = self._store.load_all_tasks()
        except sqlite3.OperationalError:
            payload = []
        except Exception:
            payload = []
            
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
