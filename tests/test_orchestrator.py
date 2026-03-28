import tempfile
import unittest
import json
import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.adapters import ApiAdapter, SubscriptionAdapter
from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.types import (
    AdapterResponse,
    Complexity,
    Criticality,
    Role,
    TaskState,
    WorkTask,
)


class OrchestratorTests(unittest.TestCase):
    def test_workflow_build_phase_is_not_auto_conversational_from_question(self) -> None:
        task = WorkTask(
            task_id="CHAT-TEST::build",
            title="Build highest-impact slice",
            description="Solicitud: que juego han creado?",
            role=Role.ENGINEER,
            metadata={"phase": "build"},
        )

        self.assertFalse(AITeamOrchestrator._detect_conversational_task(task))

    def test_assess_output_quality_rejects_placeholder_output_for_reviewer(self) -> None:
        ok, reason = AITeamOrchestrator._assess_output_quality(
            "[SIMULADO | openai:gpt-4.1] Respuesta mock para review.",
            Role.REVIEWER,
            "review",
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "placeholder_output")

    def test_verify_task_evidence_accepts_non_empty_output_in_mock_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="EVID-UNIT-1",
                title="Implement feature",
                description="Implement a concrete backend change",
                role=Role.ENGINEER,
                metadata={
                    "_last_agent_output": "Processed prompt with useful mock output."
                },
            )

            with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
                has_evidence, reason = orchestrator._verify_task_evidence(
                    task, project_root
                )

            self.assertTrue(has_evidence)
            self.assertEqual(reason, "simulated_mode_accepted")

    def test_verify_task_evidence_rejects_simulated_workflow_build_without_artifacts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="CHAT-TEST::build",
                title="Build highest-impact slice",
                description="Implementa cambios concretos",
                role=Role.ENGINEER,
                metadata={
                    "_last_agent_output": "[SIMULADO | openai:gpt-4.1] Respuesta mock para build."
                },
            )

            with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
                has_evidence, reason = orchestrator._verify_task_evidence(
                    task, project_root
                )

            self.assertFalse(has_evidence)
            self.assertEqual(reason, "simulated_placeholder_blocked:placeholder_output")

    def test_non_conversational_engineer_task_completes_in_mock_mode_without_git_diff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="EVID-INTEG-1",
                title="Implement feature",
                description="Implement a concrete backend change",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                },
            )

            with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
                orchestrator.submit_task(task)
                orchestrator.run_until_idle(max_rounds=4)

            completed = orchestrator.taskboard.get_task("EVID-INTEG-1")
            assert completed is not None
            self.assertEqual(completed.state.value, "completed")
            self.assertEqual(
                completed.metadata.get("evidence_reason"), "simulated_mode_accepted"
            )

    def test_workflow_build_phase_fails_in_simulated_mode_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="CHAT-TEST::build",
                title="Build highest-impact slice",
                description="Implementa cambios concretos",
                role=Role.ENGINEER,
                metadata={
                    "phase": "build",
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                },
            )

            with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
                orchestrator.submit_task(task)
                orchestrator.run_until_idle(max_rounds=4)

            failed = orchestrator.taskboard.get_task("CHAT-TEST::build")
            assert failed is not None
            self.assertEqual(failed.state.value, "failed")
            self.assertIn(
                "simulated_placeholder_blocked:placeholder_output",
                str(failed.metadata.get("error", "") or ""),
            )

    def test_environment_specific_parallel_limits_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            with patch.dict(
                "os.environ",
                {
                    "AITEAM_MAX_PARALLEL_TASKS": "4",
                    "AITEAM_MAX_PARALLEL_TASKS_PROD": "2",
                },
                clear=False,
            ):
                prod = AITeamOrchestrator(
                    router=router, runtime_dir=runtime_dir, environment="prod"
                )
                stage = AITeamOrchestrator(
                    router=router, runtime_dir=runtime_dir, environment="stage"
                )
                self.assertEqual(prod.max_parallel_tasks, 2)
                self.assertEqual(stage.max_parallel_tasks, 4)

    def test_parallel_autotune_reduces_and_increases_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            with patch.dict(
                "os.environ",
                {
                    "AITEAM_MAX_PARALLEL_TASKS": "4",
                    "AITEAM_MIN_PARALLEL_TASKS": "1",
                    "AITEAM_PARALLEL_AUTOTUNE": "1",
                    "AITEAM_PARALLEL_TARGET_LATENCY_MS": "100",
                    "AITEAM_PARALLEL_MAX_FAILURE_RATE": "20",
                },
                clear=False,
            ):
                orchestrator = AITeamOrchestrator(
                    router=router, runtime_dir=runtime_dir, environment="stage"
                )
                orchestrator._dynamic_parallel_tasks = 4

                orchestrator.event_logger.emit(
                    "task_execution",
                    {
                        "task_id": "AUTO-1",
                        "execution_round": 1,
                        "success": True,
                        "latency_ms": 500,
                    },
                )
                orchestrator._autotune_parallelism(1)
                self.assertEqual(orchestrator._dynamic_parallel_tasks, 3)

                orchestrator._dynamic_parallel_tasks = 2
                orchestrator.event_logger.emit(
                    "task_execution",
                    {
                        "task_id": "AUTO-2",
                        "execution_round": 2,
                        "success": True,
                        "latency_ms": 10,
                    },
                )
                orchestrator._autotune_parallelism(2)
                self.assertEqual(orchestrator._dynamic_parallel_tasks, 3)

    def test_assignee_balances_load_within_role_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            with patch.dict(
                "os.environ",
                {
                    "AITEAM_ROLE_ENGINEER_POOL": "eng-1,eng-2",
                    "AITEAM_AGENT_ENG_1_ENABLED": "1",
                    "AITEAM_AGENT_ENG_2_ENABLED": "1",
                },
                clear=False,
            ):
                orchestrator = AITeamOrchestrator(
                    router=router,
                    runtime_dir=runtime_dir,
                    project_root=Path.cwd(),
                )

                t1 = WorkTask(
                    task_id="LB-1",
                    title="Task 1",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                t2 = WorkTask(
                    task_id="LB-2",
                    title="Task 2",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )

                orchestrator.submit_task(t1)
                orchestrator.submit_task(t2)
                orchestrator.run_until_idle(max_rounds=2)

                task_one = orchestrator.taskboard.get_task("LB-1")
                task_two = orchestrator.taskboard.get_task("LB-2")
                assert task_one is not None
                assert task_two is not None
                assignees = {
                    task_one.assignee,
                    task_two.assignee,
                }
                self.assertEqual(assignees, {"eng-1", "eng-2"})

    def test_parallel_execution_assigns_deterministic_round_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
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
                    project_root=Path.cwd(),
                )
                first = WorkTask(
                    task_id="PAR-1",
                    title="Parallel one",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                second = WorkTask(
                    task_id="PAR-2",
                    title="Parallel two",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={
                        "required_capabilities": ["coding"],
                        "skip_quality_gates": True,
                        "skip_evidence_gate": True,
                        "skip_placeholder_check": True,
                    },
                )
                orchestrator.submit_task(first)
                orchestrator.submit_task(second)
                orchestrator.run_until_idle(max_rounds=3)

                task_one = orchestrator.taskboard.get_task("PAR-1")
                task_two = orchestrator.taskboard.get_task("PAR-2")
                assert task_one is not None
                assert task_two is not None
                self.assertEqual(task_one.state.value, "completed")
                self.assertEqual(task_two.state.value, "completed")
                self.assertEqual(task_one.metadata.get("execution_round"), 1)
                self.assertEqual(task_two.metadata.get("execution_round"), 1)
                self.assertNotEqual(
                    task_one.metadata.get("execution_order"),
                    task_two.metadata.get("execution_order"),
                )

                events = orchestrator.event_logger.recent_events(hours=1)
                started = [
                    item for item in events if item.get("event_type") == "task_started"
                ]
                self.assertGreaterEqual(len(started), 2)

    def test_eager_dependency_chain_tracks_sub_iterations_within_same_round(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            parent = WorkTask(
                task_id="CHAIN-1",
                title="Parent task",
                description="Implement first step",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            child = WorkTask(
                task_id="CHAIN-2",
                title="Child task",
                description="Implement second step",
                role=Role.ENGINEER,
                dependencies=["CHAIN-1"],
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(parent)
            orchestrator.submit_task(child)
            orchestrator.run_until_idle(max_rounds=2)

            parent_task = orchestrator.taskboard.get_task("CHAIN-1")
            child_task = orchestrator.taskboard.get_task("CHAIN-2")
            assert parent_task is not None
            assert child_task is not None
            self.assertEqual(parent_task.metadata.get("execution_round"), 1)
            self.assertEqual(child_task.metadata.get("execution_round"), 1)
            self.assertEqual(parent_task.metadata.get("execution_sub_iteration"), 1)
            self.assertEqual(child_task.metadata.get("execution_sub_iteration"), 2)

            events = orchestrator.event_logger.recent_events(hours=1)
            sub_events = [
                item
                for item in events
                if item.get("event_type") == "round_sub_iteration"
            ]
            self.assertTrue(
                any(
                    int((item.get("payload", {}) or {}).get("sub_iteration", 0)) == 1
                    and str((item.get("payload", {}) or {}).get("phase", ""))
                    == "execute_batch"
                    for item in sub_events
                )
            )
            self.assertTrue(
                any(
                    int((item.get("payload", {}) or {}).get("sub_iteration", 0)) == 2
                    and str((item.get("payload", {}) or {}).get("phase", ""))
                    == "execute_batch"
                    for item in sub_events
                )
            )

            round_completed = [
                item for item in events if item.get("event_type") == "round_completed"
            ]
            self.assertTrue(round_completed)
            payload = round_completed[-1].get("payload", {}) or {}
            self.assertEqual(int(payload.get("execution_round", 0)), 1)
            self.assertEqual(int(payload.get("sub_iterations_used", 0)), 3)
            self.assertEqual(int(payload.get("tasks_processed", 0)), 2)

    def test_assignee_prefers_lower_latency_and_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "reasoning", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            with patch.dict(
                "os.environ",
                {
                    "AITEAM_ROLE_ENGINEER_POOL": "eng-1,eng-2",
                    "AITEAM_AGENT_ENG_1_ENABLED": "1",
                    "AITEAM_AGENT_ENG_2_ENABLED": "1",
                },
                clear=False,
            ):
                orchestrator = AITeamOrchestrator(
                    router=router,
                    runtime_dir=runtime_dir,
                    project_root=Path.cwd(),
                )
                orchestrator._agent_latency_ewma_ms["eng-1"] = 2400.0
                orchestrator._agent_latency_ewma_ms["eng-2"] = 200.0
                orchestrator._agent_failure_penalty["eng-1"] = 3
                orchestrator._agent_failure_penalty["eng-2"] = 0
                assignee = orchestrator._assignee_for_role(Role.ENGINEER)
                self.assertEqual(assignee, "eng-2")

    def test_engineer_task_creates_and_passes_quality_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                ),
                ApiAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-api",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                ),
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="ENG-1",
                title="Implement feature",
                description="Implementar cambio solicitado.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=8)

            parent = orchestrator.taskboard.get_task("ENG-1")
            review = orchestrator.taskboard.get_task("ENG-1::review")
            qa = orchestrator.taskboard.get_task("ENG-1::qa")

            assert parent is not None
            assert review is not None
            assert qa is not None
            self.assertEqual(parent.state.value, "completed")
            self.assertEqual(review.state.value, "completed")
            self.assertEqual(qa.state.value, "completed")

    def test_parent_task_fails_when_quality_gate_fails(self) -> None:
        class ReviewFailAdapter(SubscriptionAdapter):
            def invoke(self, prompt: str) -> AdapterResponse:
                if (
                    "Review implement feature" in prompt
                    or "Review Implement feature" in prompt
                ):
                    return AdapterResponse(
                        success=False,
                        content="",
                        latency_ms=1,
                        input_tokens=max(1, len(prompt) // 4),
                        output_tokens=0,
                        error="forced_review_failure",
                    )
                return super().invoke(prompt)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                ReviewFailAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="ENG-ZOMBIE-1",
                title="Implement feature",
                description="Implementar cambio solicitado.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "max_gate_iterations": 0,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=10)

            parent = orchestrator.taskboard.get_task("ENG-ZOMBIE-1")
            review = orchestrator.taskboard.get_task("ENG-ZOMBIE-1::review")
            assert parent is not None
            assert review is not None
            self.assertEqual(review.state.value, "failed")
            self.assertEqual(parent.state.value, "failed")
            self.assertIn("quality_gates_failed", str(parent.metadata.get("error", "")))

    def test_gate_retry_events_include_gate_iteration_and_execution_context(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="ENG-RETRY-1",
                title="Implement feature",
                description="Implementar cambio solicitado.",
                role=Role.ENGINEER,
                metadata={
                    "quality_gate_tasks": ["ENG-RETRY-1::review"],
                    "quality_gate_spawned": True,
                    "execution_round": 3,
                    "execution_sub_iteration": 2,
                    "max_gate_iterations": 1,
                },
                state=TaskState.BLOCKED,
            )
            failed_gate = WorkTask(
                task_id="ENG-RETRY-1::review",
                title="Review Implement feature",
                description="Gate failed",
                role=Role.REVIEWER,
                state=TaskState.FAILED,
                metadata={"error": "forced_review_failure_once"},
            )
            orchestrator.taskboard.add_task(task)
            orchestrator.taskboard.add_task(failed_gate)

            parent_before = orchestrator.taskboard.get_task("ENG-RETRY-1")
            assert parent_before is not None
            parent_before.state = TaskState.BLOCKED
            gate_before = orchestrator.taskboard.get_task("ENG-RETRY-1::review")
            assert gate_before is not None
            gate_before.state = TaskState.FAILED

            orchestrator._release_blocked_parent_tasks()

            retried = orchestrator.taskboard.get_task("ENG-RETRY-1")
            assert retried is not None
            self.assertEqual(retried.state.value, "ready")
            self.assertEqual(int(retried.metadata.get("gate_iteration", 0)), 1)

            events = orchestrator.event_logger.recent_events(hours=1)
            gate_events = [
                item for item in events if item.get("event_type") == "gate_iteration"
            ]
            self.assertTrue(gate_events)
            gate_payload = gate_events[-1].get("payload", {}) or {}
            self.assertEqual(int(gate_payload.get("iteration", 0)), 1)
            self.assertEqual(int(gate_payload.get("execution_round", 0)), 3)
            self.assertEqual(int(gate_payload.get("execution_sub_iteration", 0)), 2)

    def test_failed_parent_blocks_dependent_task_instead_of_leaving_it_pending(
        self,
    ) -> None:
        class FailRootAdapter(SubscriptionAdapter):
            def invoke(self, prompt: str) -> AdapterResponse:
                if "Force root failure" in prompt:
                    return AdapterResponse(
                        success=False,
                        content="",
                        latency_ms=1,
                        input_tokens=max(1, len(prompt) // 4),
                        output_tokens=0,
                        error="forced_root_failure",
                    )
                return super().invoke(prompt)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                FailRootAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            parent = WorkTask(
                task_id="ROOT-FAIL-1",
                title="Force root failure",
                description="This task should fail before child can run",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            child = WorkTask(
                task_id="ROOT-FAIL-1::child",
                title="Dependent child",
                description="Should not stay pending forever",
                role=Role.REVIEWER,
                dependencies=["ROOT-FAIL-1"],
            )
            orchestrator.submit_task(parent)
            orchestrator.submit_task(child)
            orchestrator.run_until_idle(max_rounds=4)

            failed_parent = orchestrator.taskboard.get_task("ROOT-FAIL-1")
            blocked_child = orchestrator.taskboard.get_task("ROOT-FAIL-1::child")
            assert failed_parent is not None
            assert blocked_child is not None
            self.assertEqual(failed_parent.state.value, "failed")
            self.assertEqual(blocked_child.state.value, "blocked")
            self.assertEqual(
                blocked_child.metadata.get("blocked_reason"), "dependency_failed"
            )
            self.assertEqual(
                blocked_child.metadata.get("blocked_dependencies"), ["ROOT-FAIL-1"]
            )

    def test_failure_triggers_event_meeting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="FAIL-1",
                title="Force failure",
                description="FORCE_API_FALLBACK",
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            failed = orchestrator.taskboard.get_task("FAIL-1")
            assert failed is not None
            self.assertEqual(failed.state.value, "failed")

            subjects = [msg.subject for msg in orchestrator.mailbox.list_messages()]
            self.assertTrue(any("Event task_failed" in subject for subject in subjects))

    def test_team_lead_mailbox_message_is_consumed_into_agent_thread_and_replied(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )
            orchestrator.mailbox.send(
                sender="team_lead",
                recipient="eng-1",
                subject="Feedback on implementation",
                body="Integra tambien 2FA en el flujo actual.",
                task_id="MAIL-1",
            )

            task = WorkTask(
                task_id="MAIL-1",
                title="Implement feature",
                description="Implementar cambio solicitado.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            thread = orchestrator.thread_store.get_thread(
                "eng-1", str(orchestrator.project_root)
            )
            mailbox_turns = [turn for turn in thread.turns if turn.source == "mailbox"]
            self.assertTrue(mailbox_turns)
            self.assertTrue(any("2FA" in turn.content for turn in mailbox_turns))
            llm_turns = [turn for turn in thread.turns if turn.role == "assistant"]
            self.assertTrue(llm_turns)

            inbox = orchestrator.mailbox.list_messages(recipient="team_lead")
            self.assertTrue(any(msg.subject == "Reply: MAIL-1" for msg in inbox))

            eng_inbox = orchestrator.mailbox.list_messages(recipient="eng-1")
            feedback = next(
                msg for msg in eng_inbox if msg.subject == "Feedback on implementation"
            )
            self.assertTrue(orchestrator.mailbox.is_read(feedback.message_id))
            self.assertTrue(feedback.consumed)
            self.assertEqual(feedback.consumed_by, "eng-1")

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_mailbox_consumed"
                    for item in events
                )
            )
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_mailbox_reply"
                    for item in events
                )
            )

    def test_orchestrator_sends_efficient_messages_with_history_and_feedback(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        class CaptureMessagesAdapter(SubscriptionAdapter):
            def invoke(
                self,
                prompt: str,
                messages: list[dict[str, str]] | None = None,
            ) -> AdapterResponse:
                captured["prompt"] = prompt
                captured["messages"] = messages
                return super().invoke(prompt, messages=messages)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                CaptureMessagesAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )
            thread = orchestrator.thread_store.get_thread(
                "eng-1", str(orchestrator.project_root)
            )
            thread.append_turn(
                "user",
                "Primera propuesta: usar JWT.",
                source="task",
                task_id="PREV-1",
            )
            thread.append_turn(
                "assistant",
                "De acuerdo, JWT con refresh tokens.",
                source="llm",
                task_id="PREV-1",
            )
            orchestrator.thread_store.save_thread(thread)

            task = WorkTask(
                task_id="MSG-1",
                title="Implement feature",
                description="Implementar autenticacion segura.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "gate_iteration": 1,
                    "review_feedback": "Anade 2FA y documenta impacto en sesiones.",
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            messages = captured.get("messages")
            self.assertTrue(isinstance(messages, list))
            typed_messages = list(messages or [])
            self.assertGreaterEqual(len(typed_messages), 3)
            self.assertEqual(typed_messages[0].get("role"), "system")
            self.assertTrue(
                any(msg.get("role") == "assistant" for msg in typed_messages)
            )
            final_user = str(typed_messages[-1].get("content", ""))
            self.assertIn("Feedback de revision", final_user)
            self.assertIn("2FA", final_user)
            self.assertIn("Gate iteration: 1", final_user)
            self.assertTrue(
                any(
                    "JWT con refresh tokens" in str(msg.get("content", ""))
                    for msg in typed_messages
                )
            )
            self.assertLessEqual(len(typed_messages), 8)

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_messages_built"
                    for item in events
                )
            )

    def test_gate_retry_builds_compact_retry_message_and_persists_task_retry_turn(
        self,
    ) -> None:
        captured: dict[str, object] = {}

        class CaptureRetryAdapter(SubscriptionAdapter):
            def invoke(
                self,
                prompt: str,
                messages: list[dict[str, str]] | None = None,
            ) -> AdapterResponse:
                captured["messages"] = messages
                return super().invoke(prompt, messages=messages)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                CaptureRetryAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )
            task = WorkTask(
                task_id="RETRY-MSG-1",
                title="Implement feature",
                description="Implementar autenticacion segura.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "gate_iteration": 1,
                    "review_feedback": "Anade 2FA y revisa expiracion de sesiones.",
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            typed_messages = list(captured.get("messages") or [])
            self.assertTrue(typed_messages)
            final_user = str(typed_messages[-1].get("content", ""))
            self.assertIn("Retry de la tarea RETRY-MSG-1.", final_user)
            self.assertIn("Feedback de revision", final_user)
            self.assertNotIn("Contexto de equipo", final_user)

            thread = orchestrator.thread_store.get_thread(
                "eng-1", str(orchestrator.project_root)
            )
            retry_turns = [
                turn
                for turn in thread.turns
                if turn.source == "task_retry" and turn.task_id == "RETRY-MSG-1"
            ]
            self.assertTrue(retry_turns)

    def test_peer_consultation_uses_compact_messages_protocol(self) -> None:
        captured_calls: list[dict[str, object]] = []

        class CapturePeerMessagesAdapter(SubscriptionAdapter):
            def invoke(
                self,
                prompt: str,
                messages: list[dict[str, str]] | None = None,
            ) -> AdapterResponse:
                captured_calls.append({"prompt": prompt, "messages": messages})
                return super().invoke(prompt, messages=messages)

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                CapturePeerMessagesAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="PEER-1",
                title="Implement feature",
                description="Implementar autenticacion segura.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "require_peer_consultation": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            peer_calls = []
            for call in captured_calls:
                messages = call.get("messages")
                if isinstance(messages, list) and len(messages) == 2:
                    if str(messages[0].get("role", "")) == "system":
                        user_content = str(messages[-1].get("content", ""))
                        if "Consulta para" in user_content:
                            peer_calls.append(messages)

            self.assertTrue(peer_calls)
            first_peer = peer_calls[0]
            self.assertEqual(first_peer[0].get("role"), "system")
            self.assertEqual(first_peer[1].get("role"), "user")
            self.assertIn("Modo: round1", str(first_peer[1].get("content", "")))
            self.assertLessEqual(len(str(first_peer[1].get("content", ""))), 1200)

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(item.get("event_type") == "peer_messages_built" for item in events)
            )

    def test_conversational_e2e_flow_handles_team_lead_feedback_and_gate_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            orchestrator_ref: dict[str, AITeamOrchestrator] = {}

            class E2EAdapter(SubscriptionAdapter):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.review_calls = 0

                def invoke(
                    self,
                    prompt: str,
                    messages: list[dict[str, str]] | None = None,
                ) -> AdapterResponse:
                    all_text = "\n".join(
                        str(item.get("content", "")) for item in (messages or [])
                    )
                    if "Review Implement feature" in prompt:
                        self.review_calls += 1
                        if self.review_calls == 1:
                            return AdapterResponse(
                                success=False,
                                content="",
                                latency_ms=1,
                                input_tokens=max(1, len(prompt) // 4),
                                output_tokens=0,
                                error="missing_2fa",
                            )
                        return AdapterResponse(
                            success=True,
                            content="Review OK: feedback aplicado y riesgo residual aceptable.",
                            latency_ms=1,
                            input_tokens=max(1, len(prompt) // 4),
                            output_tokens=20,
                        )
                    if "QA Implement feature" in prompt:
                        return AdapterResponse(
                            success=True,
                            content="QA OK: criterios de salida cumplidos.",
                            latency_ms=1,
                            input_tokens=max(1, len(prompt) // 4),
                            output_tokens=18,
                        )
                    if "Feedback de revision" in all_text or "2FA" in all_text:
                        return AdapterResponse(
                            success=True,
                            content=(
                                "Decision: implementar JWT con refresh, 2FA y expiracion de sesiones. "
                                "Evidencia: feedback de Team Lead y gate incorporados. "
                                "Siguiente accion: actualizar backend, pruebas y docs."
                            ),
                            latency_ms=1,
                            input_tokens=max(1, len(all_text) // 4),
                            output_tokens=42,
                        )
                    return AdapterResponse(
                        success=True,
                        content=(
                            "Decision: implementar JWT con refresh. "
                            "Evidencia: base funcional inicial. "
                            "Siguiente accion: abrir quality gates."
                        ),
                        latency_ms=1,
                        input_tokens=max(1, len(all_text or prompt) // 4),
                        output_tokens=30,
                    )

            adapters: list[ModelAdapter] = [
                E2EAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )
            orchestrator_ref["instance"] = orchestrator
            orchestrator.mailbox.send(
                sender="team_lead",
                recipient="engineer",
                subject="Refuerzo de liderazgo",
                body="Anade 2FA y expiracion de sesiones antes de cerrar.",
                task_id="E2E-1",
            )

            task = WorkTask(
                task_id="E2E-1",
                title="Implement feature",
                description="Implementar autenticacion segura.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "max_gate_iterations": 1,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=10)

            parent = orchestrator.taskboard.get_task("E2E-1")
            review = orchestrator.taskboard.get_task("E2E-1::review")
            qa = orchestrator.taskboard.get_task("E2E-1::qa")
            assert parent is not None
            assert review is not None
            assert qa is not None
            self.assertEqual(parent.state.value, "completed")
            self.assertEqual(review.state.value, "completed")
            self.assertEqual(qa.state.value, "completed")
            assignee = parent.assignee or "eng-1"

            engineer_mail = orchestrator.mailbox.list_messages(recipient="engineer")
            lead_feedback = next(
                msg for msg in engineer_mail if msg.subject == "Refuerzo de liderazgo"
            )
            self.assertTrue(orchestrator.mailbox.is_read(lead_feedback.message_id))

            team_lead_mail = orchestrator.mailbox.list_messages(recipient="team_lead")
            self.assertTrue(
                any(msg.subject == "Reply: E2E-1" for msg in team_lead_mail)
            )
            reply_msg = next(
                msg for msg in team_lead_mail if msg.subject == "Reply: E2E-1"
            )
            self.assertIn("2FA", reply_msg.body)
            self.assertGreaterEqual(adapters[0].review_calls, 2)

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_mailbox_consumed"
                    for item in events
                )
            )
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_mailbox_reply"
                    for item in events
                )
            )
            self.assertTrue(
                any(
                    item.get("event_type") == "conversation_messages_built"
                    for item in events
                )
            )
            self.assertTrue(
                any(
                    item.get("event_type") == "task_started"
                    and str((item.get("payload", {}) or {}).get("task_id", ""))
                    == "E2E-1"
                    for item in events
                )
            )

    def test_peer_prompt_high_risk_uses_hypothesis_sections(self) -> None:
        task = WorkTask(
            task_id="T-HIGH",
            title="Diagnosticar error critico",
            description="Analizar causa raiz en concurrencia",
            role=Role.ENGINEER,
            complexity=Complexity.HIGH,
            criticality=Criticality.HIGH,
        )
        prompt = AITeamOrchestrator._peer_prompt_for_task(task, Role.RESEARCHER)
        self.assertIn("Hipotesis principal", prompt)
        self.assertIn("Contra-hipotesis", prompt)

    def test_sensitive_execution_plan_requires_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"reasoning", "coding", "analysis", "review"},
                ),
                ApiAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-api",
                    capabilities={"reasoning", "coding", "analysis", "review"},
                ),
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="SEC-1",
                title="Publicar Android",
                description="Intento de publicacion automatica",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "execution_plan": [
                        {
                            "type": "cmd",
                            "command": "echo publish playstore",
                            "timeout": 10,
                        }
                    ],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=3)

            blocked = orchestrator.taskboard.get_task("SEC-1")
            assert blocked is not None
            self.assertEqual(blocked.state.value, "failed")
            self.assertIn(
                "sensitive_commands_require_approval", blocked.metadata.get("error", "")
            )

            subjects = [msg.subject for msg in orchestrator.mailbox.list_messages()]
            self.assertTrue(
                any("Task blocked by compliance" in subject for subject in subjects)
            )

    def test_sensitive_execution_plan_runs_when_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"reasoning", "coding", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="SEC-2",
                title="Publicar Android",
                description="Ejecucion sensible aprobada",
                role=Role.TEAM_LEAD,
                metadata={
                    "execution_plan": [
                        {
                            "type": "cmd",
                            "command": "echo publish playstore",
                            "timeout": 10,
                        }
                    ],
                    "approved_sensitive_ops": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=3)

            completed = orchestrator.taskboard.get_task("SEC-2")
            assert completed is not None
            self.assertEqual(completed.state.value, "completed")

    def test_prod_sensitive_plan_needs_two_approvers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"reasoning", "coding", "analysis", "review"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                environment="prod",
            )

            task = WorkTask(
                task_id="SEC-PROD-1",
                title="Publicar Android",
                description="Operacion sensible en produccion",
                role=Role.TEAM_LEAD,
                metadata={
                    "execution_plan": [
                        {
                            "type": "cmd",
                            "command": "echo publish playstore",
                            "timeout": 10,
                        }
                    ],
                    "approved_sensitive_ops": True,
                    "approved_by": ["lead-1"],
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=3)

            failed = orchestrator.taskboard.get_task("SEC-PROD-1")
            assert failed is not None
            self.assertEqual(failed.state.value, "failed")
            self.assertIn(
                "insufficient_approvers_required_2", failed.metadata.get("error", "")
            )

    def test_high_risk_engineer_task_opens_security_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="ENG-SEC",
                title="Implementar cambio critico",
                description="Ajustar flujo de release en modulo sensible",
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=10)

            security_gate = orchestrator.taskboard.get_task("ENG-SEC::security")
            parent = orchestrator.taskboard.get_task("ENG-SEC")
            assert security_gate is not None
            assert parent is not None
            self.assertEqual(security_gate.state.value, "completed")
            self.assertEqual(parent.state.value, "completed")

    def test_auto_discovers_and_integrates_tool_when_no_adapter_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root = Path(tmp)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            catalog_path = config_dir / "tool_sources.catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "auto_special_tool",
                                "category": "cli",
                                "source_type": "npm",
                                "source": "auto-special-tool",
                                "command": ["python", "-c", "print('auto-special-ok')"],
                                "capabilities": ["special_capability"],
                                "role_targets": ["engineer"],
                                "enabled": True,
                                "requires_approval": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "analysis"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="AUTO-TOOLS-1",
                title="Usar herramienta especial",
                description="Necesita capacidad no disponible inicialmente",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["special_capability"],
                    "skip_quality_gates": True,
                    "auto_discover_tools": True,
                    "skip_evidence_gate": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            completed = orchestrator.taskboard.get_task("AUTO-TOOLS-1")
            assert completed is not None
            self.assertEqual(completed.state.value, "completed")
            self.assertTrue((runtime_dir / "adapters.json").exists())

    def test_records_skill_mcp_guidance_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="qa_tool",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"browser_testing", "analysis", "reasoning"},
                    role_targets={"qa"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="GUIDE-1",
                title="QA Browser Flow",
                description="Run browser e2e with assertions",
                role=Role.QA,
                metadata={
                    "required_capabilities": ["browser_testing"],
                    "skip_quality_gates": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            entries = orchestrator.memory.recent("qa-1", limit=20)
            guidance_entries = [
                item for item in entries if item.kind == "skill_mcp_guidance"
            ]
            self.assertTrue(guidance_entries)

    def test_records_decision_rank_and_justification_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-5.3-codex",
                    capabilities={"coding", "analysis", "review", "reasoning"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="DECIDE-1",
                title="Implement decision protocol",
                description="Add governance-aware delivery",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=5)

            completed = orchestrator.taskboard.get_task("DECIDE-1")
            assert completed is not None
            self.assertEqual(completed.state.value, "completed")
            self.assertEqual(completed.metadata.get("decision_rank"), 4)
            self.assertIn("decision_justification", completed.metadata)
            self.assertIsInstance(completed.metadata.get("consulted_roles", []), list)

    def test_handoff_retries_with_substitute_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-5.3-codex",
                    capabilities={"coding", "analysis"},
                    role_targets={"engineer"},
                )
            ]
            router = HybridRouter(
                adapters=adapters, policy=build_default_router_policy()
            )
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="HANDOFF-1",
                title="Force model failure",
                description="FORCE_API_FALLBACK",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "max_handoff_retries": 1,
                    "skip_evidence_gate": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=6)

            final_task = orchestrator.taskboard.get_task("HANDOFF-1")
            assert final_task is not None
            self.assertEqual(final_task.state.value, "failed")
            self.assertIn(final_task.metadata.get("handoff_to"), {"eng-2", "eng-3"})

            handoff_target = str(final_task.metadata.get("handoff_to"))
            handoff_memory = [
                item
                for item in orchestrator.memory.recent(handoff_target, limit=20)
                if item.kind == "handoff_context"
            ]
            self.assertTrue(handoff_memory)
            self.assertIn("Handoff Task:", handoff_memory[-1].content)
            self.assertIn("Siguiente accion esperada:", handoff_memory[-1].content)

            lead_mail = orchestrator.mailbox.list_messages(recipient="team_lead")
            self.assertTrue(
                any(msg.subject == "Handoff executed: HANDOFF-1" for msg in lead_mail)
            )

            events = orchestrator.event_logger.recent_events(hours=1)
            handoff_events = [
                item for item in events if item.get("event_type") == "agent_handoff"
            ]
            self.assertTrue(handoff_events)
            self.assertIn("summary", handoff_events[-1].get("payload", {}))


if __name__ == "__main__":
    unittest.main()
