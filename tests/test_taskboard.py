import tempfile
import unittest
from pathlib import Path

from aiteam.taskboard import TaskBoard
from aiteam.types import Role, WorkTask


class TaskBoardTests(unittest.TestCase):
    def test_dependency_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            t1 = WorkTask(
                task_id="A", title="Root", description="x", role=Role.TEAM_LEAD
            )
            t2 = WorkTask(
                task_id="B",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["A"],
            )
            board.add_task(t1)
            board.add_task(t2)

            ready_ids = {task.task_id for task in board.ready_tasks()}
            self.assertIn("A", ready_ids)
            self.assertNotIn("B", ready_ids)

            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_completed("A", details="done")

            ready_ids = {task.task_id for task in board.ready_tasks()}
            self.assertIn("B", ready_ids)

    def test_file_lock_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            t1 = WorkTask(
                task_id="A",
                title="Task A",
                description="x",
                role=Role.ENGINEER,
                metadata={"owned_files": ["src/a.py"]},
            )
            t2 = WorkTask(
                task_id="B",
                title="Task B",
                description="x",
                role=Role.ENGINEER,
                metadata={"owned_files": ["src/a.py"]},
            )
            board.add_task(t1)
            board.add_task(t2)

            self.assertTrue(board.claim_task("A", assignee="eng-1"))
            self.assertFalse(board.claim_task("B", assignee="eng-2"))
            blocked = board.get_task("B")
            assert blocked is not None
            self.assertEqual(blocked.state.value, "blocked")

    def test_failed_dependency_blocks_child_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            parent = WorkTask(
                task_id="A", title="Root", description="x", role=Role.TEAM_LEAD
            )
            child = WorkTask(
                task_id="B",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["A"],
            )
            board.add_task(parent)
            board.add_task(child)

            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_failed("A", error="root_failed")

            blocked = board.get_task("B")
            assert blocked is not None
            self.assertEqual(blocked.state.value, "blocked")
            self.assertEqual(
                blocked.metadata.get("blocked_reason"), "dependency_failed"
            )
            self.assertEqual(blocked.metadata.get("blocked_dependencies"), ["A"])

    def test_retrying_failed_parent_unblocks_child_when_parent_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            parent = WorkTask(
                task_id="A", title="Root", description="x", role=Role.TEAM_LEAD
            )
            child = WorkTask(
                task_id="B",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["A"],
            )
            board.add_task(parent)
            board.add_task(child)

            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_failed("A", error="root_failed")
            board.retry_task("A", reason="manual_retry", assignee="lead-1")
            self.assertTrue(board.claim_task("A", assignee="lead-1"))
            board.mark_completed("A", details="done")

            unblocked = board.get_task("B")
            assert unblocked is not None
            self.assertEqual(unblocked.state.value, "ready")
            self.assertIsNone(unblocked.metadata.get("blocked_reason"))
            self.assertIsNone(unblocked.metadata.get("blocked_dependencies"))

    def test_load_ignores_corrupted_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "tasks.json"
            storage.write_text("{not-valid-json", encoding="utf-8")
            board = TaskBoard(storage)
            self.assertEqual(board.list_tasks(), [])


if __name__ == "__main__":
    unittest.main()
