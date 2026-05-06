from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import threading
import uuid
from pathlib import Path

from aiteam.db.issues import checkout_issue
from aiteam.db.runs import create_run
from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask


class TaskBoard:
    """Temporary compatibility shim for legacy task payloads.

    New runtime behavior should use the v2 `issues`, `runs` and `wakeup_requests`
    tables directly. This class only keeps old task-shaped tests and migration
    helpers alive while the issue repository becomes the primary surface.
    """

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
            if task.state == TaskState.PENDING and not task.dependencies:
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
            return [task for task in self._tasks.values() if task.state == TaskState.READY]

    def checkpoint(self) -> None:
        return

    def persist_tasks(self, task_ids: list[str]) -> None:
        with self._lock:
            self._store.upsert_tasks(
                [
                    self._task_to_dict(self._tasks[task_id])
                    for task_id in task_ids
                    if task_id in self._tasks
                ]
            )

    def claim_task(self, task_id: str, assignee: str) -> bool:
        with self._lock:
            before = self._snapshot_tasks()
            self._refresh_readiness()
            task = self._tasks.get(task_id)
            if task is None or task.state != TaskState.READY:
                return False

            if self._has_unmet_dependencies(task):
                self._mark_dependency_state(task)
                self._persist_task_changes(before)
                return False

            v2_claimed = self._try_claim_issue_v2(task, assignee)
            if v2_claimed is not None:
                if not v2_claimed:
                    task.metadata["checkout_conflict"] = True
                    self._persist_task_changes(before)
                    return False
                task.metadata.pop("checkout_conflict", None)

            task.state = TaskState.CLAIMED
            task.assignee = assignee
            self._persist_task_changes(before)
            return True

    def mark_completed(self, task_id: str, details: str = "") -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.COMPLETED
            if details:
                task.metadata["result"] = details
            self._refresh_readiness()
            self._persist_task_changes(before)

    def skip_task(self, task_id: str, reason: str) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.metadata["skipped_from_state"] = task.state.value
            task.state = TaskState.SKIPPED
            task.metadata["skipped_reason"] = reason
            self._refresh_readiness()
            self._persist_task_changes(before)

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.FAILED
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
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
            task.state = TaskState.WAITING_USER
            task.metadata["clarify_question"] = question
            task.metadata["waiting_since"] = dt.datetime.now(dt.timezone.utc).isoformat()
            self._persist_task_changes(before)

    def update_metadata(self, task_id: str, patch: dict) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            self._require(task_id).metadata.update(patch)
            self._persist_task_changes(before)

    def archive_incomplete_tasks(
        self,
        reason: str,
        exclude_chat_root: str | None = None,
    ) -> list[str]:
        archivable = {
            TaskState.PENDING,
            TaskState.READY,
            TaskState.BLOCKED,
            TaskState.WAITING_USER,
        }
        with self._lock:
            before = self._snapshot_tasks()
            archived: list[str] = []
            for task in self._tasks.values():
                if task.state not in archivable:
                    continue
                if exclude_chat_root:
                    root = task.task_id.split("::")[0] if "::" in task.task_id else task.task_id
                    if root == exclude_chat_root:
                        continue
                task.state = TaskState.ARCHIVED
                task.metadata["archived_reason"] = reason
                archived.append(task.task_id)
            if archived:
                self._persist_task_changes(before)
            return archived

    def remove_tasks(self, task_ids: list[str]) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            for task_id in task_ids:
                self._tasks.pop(task_id, None)
            self._persist_task_changes(before, deleted_ids=task_ids)

    def retry_task(
        self,
        task_id: str,
        reason: str,
        assignee: str | None = None,
    ) -> None:
        with self._lock:
            before = self._snapshot_tasks()
            task = self._require(task_id)
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
            if task.state in {
                TaskState.COMPLETED,
                TaskState.SKIPPED,
                TaskState.CLAIMED,
                TaskState.FAILED,
                TaskState.WAITING_USER,
                TaskState.ARCHIVED,
            }:
                continue
            if task.state == TaskState.BLOCKED and task.metadata.get("blocked_reason") != "dependency_failed":
                continue
            if not task.dependencies:
                task.state = TaskState.READY
                continue
            self._mark_dependency_state(task)

    def _has_unmet_dependencies(self, task: WorkTask) -> bool:
        return any(
            dep not in self._tasks or self._tasks[dep].state != TaskState.COMPLETED
            for dep in task.dependencies
        )

    def _mark_dependency_state(self, task: WorkTask) -> None:
        failed = [
            dep
            for dep in task.dependencies
            if dep in self._tasks and self._tasks[dep].state in {TaskState.FAILED, TaskState.BLOCKED}
        ]
        if failed:
            task.state = TaskState.BLOCKED
            task.metadata["blocked_reason"] = "dependency_failed"
            task.metadata["blocked_dependencies"] = failed
            return
        task.metadata.pop("blocked_reason", None)
        task.metadata.pop("blocked_dependencies", None)
        task.state = TaskState.PENDING if self._has_unmet_dependencies(task) else TaskState.READY

    def _try_claim_issue_v2(self, task: WorkTask, assignee: str) -> bool | None:
        issue = self._load_v2_issue(task.task_id)
        if issue is None:
            return None
        agent_id = str(issue.get("assignee_agent_id") or "").strip() or f"role:{task.role.value}"
        run_id = str(task.metadata.get("checkout_run_id") or "").strip() or f"legacy-claim:{uuid.uuid4()}"
        try:
            create_run(
                self.db_path,
                run_id=run_id,
                agent_id=agent_id,
                issue_id=task.task_id,
                profile=str(task.metadata.get("run_profile", "") or "") or None,
                invocation_source="legacy_taskboard",
                trigger_detail="TaskBoard.claim_task",
                context_snapshot={
                    "wake_reason": "legacy_claim",
                    "source_task_id": task.task_id,
                    "legacy_assignee": assignee,
                },
                complexity=task.complexity.value,
            )
            row = checkout_issue(
                self.db_path,
                issue_id=task.task_id,
                agent_id=agent_id,
                expected_statuses=["todo", "backlog"],
                run_id=run_id,
            )
        except (sqlite3.Error, ValueError):
            return None
        if row is None:
            return False
        task.metadata["checkout_run_id"] = run_id
        task.metadata["execution_run_id"] = run_id
        task.metadata["v2_issue_checkout"] = True
        task.metadata["v2_agent_id"] = agent_id
        return True

    def _load_v2_issue(self, issue_id: str) -> dict | None:
        try:
            with contextlib.closing(sqlite3.connect(str(self.db_path), timeout=20.0)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
                return dict(row) if row is not None else None
        except sqlite3.Error:
            return None

    def _require(self, task_id: str) -> WorkTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return task

    def _snapshot_tasks(self) -> dict[str, str]:
        return {
            task_id: json.dumps(self._task_to_dict(task), ensure_ascii=False, sort_keys=True)
            for task_id, task in self._tasks.items()
        }

    def _persist_task_changes(
        self,
        before: dict[str, str],
        *,
        deleted_ids: list[str] | None = None,
    ) -> None:
        after = self._snapshot_tasks()
        changed = [
            self._task_to_dict(self._tasks[task_id])
            for task_id, serialized in after.items()
            if before.get(task_id) != serialized and task_id in self._tasks
        ]
        if changed:
            self._store.upsert_tasks(changed)
        removed = [
            task_id
            for task_id in list(deleted_ids or [])
            if task_id in before and task_id not in after
        ]
        if removed:
            self._store.delete_tasks(removed)

    def _load(self) -> None:
        try:
            payload = self._store.load_all_tasks()
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
