import tempfile
import threading
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.adapters import FakeSuccessAdapter, SubscriptionAdapter
from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.sqlite_store import SqliteStore
from aiteam.taskboard import TaskBoard
from aiteam.types import Role, TaskState, WorkTask


class ParallelTaskBoardTests(unittest.TestCase):
    def test_multiple_taskboards_do_not_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "tasks.json"
            board_a = TaskBoard(storage)
            board_b = TaskBoard(storage)

            board_a.add_task(
                WorkTask(
                    task_id="A",
                    title="Task A",
                    description="x",
                    role=Role.ENGINEER,
                )
            )
            board_b.add_task(
                WorkTask(
                    task_id="B",
                    title="Task B",
                    description="x",
                    role=Role.REVIEWER,
                )
            )

            board_a.mark_completed("A", details="done")
            board_b = TaskBoard(storage)
            board_b.update_metadata("B", {"note": "keep"})

            reloaded = TaskBoard(storage)
            tasks = {task.task_id: task for task in reloaded.list_tasks()}
            self.assertEqual(set(tasks.keys()), {"A", "B"})
            self.assertEqual(tasks["A"].state, TaskState.COMPLETED)
            self.assertEqual(tasks["A"].metadata.get("result"), "done")
            self.assertEqual(tasks["B"].metadata.get("note"), "keep")

    def test_workflow_entries_preserve_other_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SqliteStore(Path(tmp) / "aiteam.db")
            store.save_workflow_entry("CHAT-A1B2C3D4", {"phase_outputs": {"build": "A"}})
            store.save_workflow_entry("CHAT-B1C2D3E4", {"phase_outputs": {"review": "B"}})
            store.save_workflow_entry("CHAT-A1B2C3D4", {"phase_outputs": {"build": "A2"}})

            state = store.load_workflow_state()
            self.assertEqual(
                (state.get("CHAT-A1B2C3D4", {}) or {}).get("phase_outputs", {}).get("build"),
                "A2",
            )
            self.assertEqual(
                (state.get("CHAT-B1C2D3E4", {}) or {}).get("phase_outputs", {}).get("review"),
                "B",
            )

    def test_workflow_entry_payload_includes_task_root(self) -> None:
        tmp_root = Path.cwd() / ".tmp-tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp = tmp_root / f"sqlite-store-{uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            store = SqliteStore(tmp / "aiteam.db")
            store.save_workflow_entry(
                "CHAT-A1B2C3D4",
                {"phase_outputs": {"build": "A"}},
            )

            entry = store.load_workflow_entry("CHAT-A1B2C3D4")
            self.assertEqual(str(entry.get("task_root", "")), "CHAT-A1B2C3D4")

            state = store.load_workflow_state()
            self.assertEqual(
                str((state.get("CHAT-A1B2C3D4", {}) or {}).get("task_root", "")),
                "CHAT-A1B2C3D4",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parallel_claim_is_safe_with_shared_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            parent = WorkTask(
                task_id="P", title="Parent", description="x", role=Role.ENGINEER
            )
            child = WorkTask(
                task_id="C",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["P"],
            )
            board.add_task(parent)
            board.add_task(child)

            self.assertTrue(board.claim_task("P", "worker-1"))
            board.mark_completed("P", details="done")

            results: list[bool] = []
            lock = threading.Lock()

            def try_claim(worker_id: str) -> None:
                result = board.claim_task("C", worker_id)
                with lock:
                    results.append(result)

            t1 = threading.Thread(target=try_claim, args=("worker-1",))
            t2 = threading.Thread(target=try_claim, args=("worker-2",))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 1)

    def test_child_not_claimable_while_parent_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            parent = WorkTask(
                task_id="P", title="Parent", description="x", role=Role.ENGINEER
            )
            child = WorkTask(
                task_id="C",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["P"],
            )
            board.add_task(parent)
            board.add_task(child)

            self.assertTrue(board.claim_task("P", "worker-1"))
            forced_child = board.get_task("C")
            assert forced_child is not None
            forced_child.state = TaskState.READY

            result = board.claim_task("C", "worker-2")
            self.assertFalse(result)
            child_task = board.get_task("C")
            assert child_task is not None
            self.assertEqual(child_task.state, TaskState.PENDING)

    def test_failed_dependency_remains_blocked_during_claim_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            board = TaskBoard(Path(tmp) / "tasks.json")
            parent = WorkTask(
                task_id="P", title="Parent", description="x", role=Role.ENGINEER
            )
            child = WorkTask(
                task_id="C",
                title="Child",
                description="x",
                role=Role.ENGINEER,
                dependencies=["P"],
            )
            board.add_task(parent)
            board.add_task(child)

            self.assertTrue(board.claim_task("P", "worker-1"))
            board.mark_failed("P", error="boom")
            forced_child = board.get_task("C")
            assert forced_child is not None
            forced_child.state = TaskState.READY

            result = board.claim_task("C", "worker-2")
            self.assertFalse(result)
            child_task = board.get_task("C")
            assert child_task is not None
            self.assertEqual(child_task.state, TaskState.BLOCKED)
            self.assertEqual(
                child_task.metadata.get("blocked_reason"), "dependency_failed"
            )
            self.assertEqual(child_task.metadata.get("blocked_dependencies"), ["P"])

    def test_parallel_tasks_with_shared_child_complete_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True)
            project_root.mkdir(parents=True)

            adapter = FakeSuccessAdapter(
                name="test_adapter",
                capabilities={"coding", "reasoning", "analysis", "review"},
            )
            router = HybridRouter(
                adapters=[adapter], policy=build_default_router_policy()
            )

            with patch.dict(
                "os.environ", {"AITEAM_MAX_PARALLEL_TASKS": "2"}, clear=False
            ):
                orchestrator = AITeamOrchestrator(
                    router=router,
                    runtime_dir=runtime_dir,
                    project_root=project_root,
                )
                a = WorkTask(
                    task_id="A",
                    title="Task A",
                    description="Implement A",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                b = WorkTask(
                    task_id="B",
                    title="Task B",
                    description="Implement B",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                c = WorkTask(
                    task_id="C",
                    title="Task C",
                    description="Implement C after A and B",
                    role=Role.ENGINEER,
                    dependencies=["A", "B"],
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                orchestrator.submit_task(a)
                orchestrator.submit_task(b)
                orchestrator.submit_task(c)
                orchestrator.run_until_idle(max_rounds=3)

                task_a = orchestrator.taskboard.get_task("A")
                task_b = orchestrator.taskboard.get_task("B")
                task_c = orchestrator.taskboard.get_task("C")
                assert task_a is not None
                assert task_b is not None
                assert task_c is not None
                self.assertEqual(task_a.state.value, "completed")
                self.assertEqual(task_b.state.value, "completed")
                self.assertEqual(task_c.state.value, "completed")
                self.assertEqual(task_c.metadata.get("execution_round"), 1)
                self.assertGreaterEqual(
                    int(task_c.metadata.get("execution_sub_iteration", 0)), 2
                )

    def test_parallel_run_emits_sub_iteration_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True)
            project_root.mkdir(parents=True)

            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="test_adapter",
                    provider="openai",
                    model="gpt-4.1",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )

            with patch.dict(
                "os.environ", {"AITEAM_MAX_PARALLEL_TASKS": "2"}, clear=False
            ):
                orchestrator = AITeamOrchestrator(
                    router=router,
                    runtime_dir=runtime_dir,
                    project_root=project_root,
                )
                for task_id in ("A", "B"):
                    orchestrator.submit_task(
                        WorkTask(
                            task_id=task_id,
                            title=f"Task {task_id}",
                            description=f"Implement {task_id}",
                            role=Role.ENGINEER,
                            metadata={
                                "required_capabilities": ["coding"],
                                "skip_quality_gates": True,
                                "skip_evidence_gate": True,
                                "skip_placeholder_check": True,
                            },
                        )
                    )
                orchestrator.run_until_idle(max_rounds=2)

                events = orchestrator.event_logger.recent_events(hours=1)
                barriers = [
                    item
                    for item in events
                    if item.get("event_type") == "sub_iteration_barrier"
                ]
                self.assertTrue(barriers)
                payload = barriers[-1].get("payload", {}) or {}
                self.assertEqual(int(payload.get("execution_round", 0)), 1)
                self.assertGreaterEqual(int(payload.get("sub_iteration", 0)), 1)
                self.assertGreaterEqual(
                    int(payload.get("tasks_processed_so_far", 0)), 1
                )


if __name__ == "__main__":
    unittest.main()
