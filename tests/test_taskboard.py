import contextlib
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.taskboard import TaskBoard
from aiteam.types import Role, TaskState, WorkTask


@contextmanager
def taskboard_tmp_dir():
    root = Path.cwd() / ".pytest-workspace-tmp" / "taskboard"
    root.mkdir(parents=True, exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        # Best-effort cleanup; Windows may keep sqlite handles briefly.
        import shutil

        shutil.rmtree(path, ignore_errors=True)


class TaskBoardTests(unittest.TestCase):
    def test_add_task_preserves_explicit_completed_state(self) -> None:
        with taskboard_tmp_dir() as tmp:
            board = TaskBoard(tmp / "tasks.json")
            task = WorkTask(
                task_id="A",
                title="Recovered",
                description="x",
                role=Role.ENGINEER,
            )
            task.state = TaskState.COMPLETED

            board.add_task(task)

            stored = board.get_task("A")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            self.assertEqual(board.ready_tasks(), [])

    def test_dependency_flow(self) -> None:
        with taskboard_tmp_dir() as tmp:
            board = TaskBoard(tmp / "tasks.json")
            board.add_task(WorkTask(task_id="A", title="Root", description="x", role=Role.TEAM_LEAD))
            board.add_task(
                WorkTask(
                    task_id="B",
                    title="Child",
                    description="x",
                    role=Role.ENGINEER,
                    dependencies=["A"],
                )
            )

            self.assertEqual({task.task_id for task in board.ready_tasks()}, {"A"})
            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_completed("A", details="done")

            self.assertEqual({task.task_id for task in board.ready_tasks()}, {"B"})

    def test_failed_dependency_blocks_child_with_reason(self) -> None:
        with taskboard_tmp_dir() as tmp:
            board = TaskBoard(tmp / "tasks.json")
            board.add_task(WorkTask(task_id="A", title="Root", description="x", role=Role.TEAM_LEAD))
            board.add_task(
                WorkTask(
                    task_id="B",
                    title="Child",
                    description="x",
                    role=Role.ENGINEER,
                    dependencies=["A"],
                )
            )

            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_failed("A", error="root_failed")

            blocked = board.get_task("B")
            assert blocked is not None
            self.assertEqual(blocked.state, TaskState.BLOCKED)
            self.assertEqual(blocked.metadata.get("blocked_reason"), "dependency_failed")
            self.assertEqual(blocked.metadata.get("blocked_dependencies"), ["A"])

    def test_retrying_failed_parent_unblocks_child_when_parent_completes(self) -> None:
        with taskboard_tmp_dir() as tmp:
            board = TaskBoard(tmp / "tasks.json")
            board.add_task(WorkTask(task_id="A", title="Root", description="x", role=Role.TEAM_LEAD))
            board.add_task(
                WorkTask(
                    task_id="B",
                    title="Child",
                    description="x",
                    role=Role.ENGINEER,
                    dependencies=["A"],
                )
            )

            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_failed("A", error="root_failed")
            board.retry_task("A", reason="manual_retry", assignee="lead-1")
            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_completed("A", details="done")

            unblocked = board.get_task("B")
            assert unblocked is not None
            self.assertEqual(unblocked.state, TaskState.READY)
            self.assertIsNone(unblocked.metadata.get("blocked_reason"))
            self.assertIsNone(unblocked.metadata.get("blocked_dependencies"))

    def test_load_ignores_corrupted_legacy_snapshot(self) -> None:
        with taskboard_tmp_dir() as tmp:
            storage = tmp / "tasks.json"
            storage.write_text("{not-valid-json", encoding="utf-8")
            board = TaskBoard(storage)
            self.assertEqual(board.list_tasks(), [])

    def test_runtime_factory_uses_sqlite_primary_and_legacy_snapshot_aux(self) -> None:
        with taskboard_tmp_dir() as tmp:
            runtime_dir = tmp / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            board = TaskBoard.from_runtime_dir(runtime_dir)
            board.add_task(WorkTask(task_id="A", title="Root", description="x", role=Role.TEAM_LEAD))

            self.assertEqual(board.db_path, runtime_dir / "aiteam.db")
            self.assertEqual(board.legacy_snapshot_path, runtime_dir / "tasks.json")
            self.assertTrue((runtime_dir / "aiteam.db").exists())
            self.assertFalse((runtime_dir / "tasks.json").exists())

    def test_claim_task_uses_v2_checkout_when_issue_exists(self) -> None:
        with taskboard_tmp_dir() as tmp:
            runtime_dir = tmp / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            board = TaskBoard.from_runtime_dir(runtime_dir)
            task = WorkTask(
                task_id="ISSUE-1",
                title="Implement",
                description="x",
                role=Role.ENGINEER,
                metadata={"owned_files": ["src/a.py"], "run_profile": "full_team"},
            )
            board.add_task(task)
            self._seed_v2_issue(runtime_dir / "aiteam.db", task_id="ISSUE-1")

            self.assertTrue(board.claim_task("ISSUE-1", assignee="eng-1"))

            claimed = board.get_task("ISSUE-1")
            assert claimed is not None
            self.assertEqual(claimed.state, TaskState.CLAIMED)
            self.assertTrue(claimed.metadata.get("v2_issue_checkout"))
            self.assertTrue(str(claimed.metadata.get("checkout_run_id", "")).startswith("legacy-claim:"))
            with contextlib.closing(sqlite3.connect(str(runtime_dir / "aiteam.db"))) as conn:
                conn.row_factory = sqlite3.Row
                issue = conn.execute(
                    "SELECT status, assignee_agent_id, checkout_run_id, execution_run_id FROM issues WHERE id = ?",
                    ("ISSUE-1",),
                ).fetchone()
                runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            self.assertEqual(issue["status"], "in_progress")
            self.assertEqual(issue["assignee_agent_id"], "role:engineer")
            self.assertEqual(issue["checkout_run_id"], claimed.metadata["checkout_run_id"])
            self.assertEqual(issue["execution_run_id"], claimed.metadata["checkout_run_id"])
            self.assertEqual(runs, 1)

    def test_claim_task_v2_conflict_does_not_fall_back_to_legacy_claim(self) -> None:
        with taskboard_tmp_dir() as tmp:
            runtime_dir = tmp / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            board = TaskBoard.from_runtime_dir(runtime_dir)
            board.add_task(
                WorkTask(
                    task_id="ISSUE-1",
                    title="Implement",
                    description="x",
                    role=Role.ENGINEER,
                    metadata={"owned_files": ["src/a.py"]},
                )
            )
            self._seed_v2_issue(
                runtime_dir / "aiteam.db",
                task_id="ISSUE-1",
                status="in_progress",
                checkout_run_id="run-existing",
            )

            self.assertFalse(board.claim_task("ISSUE-1", assignee="eng-1"))

            blocked = board.get_task("ISSUE-1")
            assert blocked is not None
            self.assertEqual(blocked.state, TaskState.READY)
            self.assertTrue(blocked.metadata.get("checkout_conflict"))

    def test_skip_task_marks_terminal_state_and_reason(self) -> None:
        with taskboard_tmp_dir() as tmp:
            board = TaskBoard(tmp / "tasks.json")
            board.add_task(WorkTask(task_id="A", title="Root", description="x", role=Role.TEAM_LEAD))
            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.skip_task("A", reason="lead_close_skip_phase")

            stored = board.get_task("A")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.SKIPPED)
            self.assertEqual(stored.metadata.get("skipped_reason"), "lead_close_skip_phase")
            self.assertEqual(stored.metadata.get("skipped_from_state"), "claimed")

    @staticmethod
    def _seed_v2_issue(
        db_path: Path,
        *,
        task_id: str,
        status: str = "todo",
        checkout_run_id: str | None = None,
    ) -> None:
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("INSERT OR IGNORE INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
            conn.execute(
                "INSERT OR IGNORE INTO agents (id, role, name) VALUES (?, ?, ?)",
                ("role:engineer", "engineer", "Engineer"),
            )
            if checkout_run_id:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO runs (id, agent_id, invocation_source, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (checkout_run_id, "role:engineer", "test", "running"),
                )
            conn.execute(
                """
                INSERT INTO issues
                    (id, goal_id, title, status, role, assignee_agent_id, checkout_run_id, execution_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    "goal-1",
                    "Implement",
                    status,
                    "engineer",
                    "role:engineer",
                    checkout_run_id,
                    checkout_run_id,
                ),
            )
            conn.commit()


if __name__ == "__main__":
    unittest.main()
