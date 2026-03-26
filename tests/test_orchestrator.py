import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from aiteam.adapters import ApiAdapter, SubscriptionAdapter
from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.types import AdapterResponse, Complexity, Criticality, Role, WorkTask


class OrchestratorTests(unittest.TestCase):
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            with patch.dict(
                "os.environ",
                {
                    "AITEAM_MAX_PARALLEL_TASKS": "4",
                    "AITEAM_MAX_PARALLEL_TASKS_PROD": "2",
                },
                clear=False,
            ):
                prod = AITeamOrchestrator(router=router, runtime_dir=runtime_dir, environment="prod")
                stage = AITeamOrchestrator(router=router, runtime_dir=runtime_dir, environment="stage")
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir, environment="stage")
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                    metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
                )
                t2 = WorkTask(
                    task_id="LB-2",
                    title="Task 2",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            with patch.dict("os.environ", {"AITEAM_MAX_PARALLEL_TASKS": "2"}, clear=False):
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
                    metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
                )
                second = WorkTask(
                    task_id="PAR-2",
                    title="Parallel two",
                    description="Implement",
                    role=Role.ENGINEER,
                    metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
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
                self.assertNotEqual(task_one.metadata.get("execution_order"), task_two.metadata.get("execution_order"))

                events = orchestrator.event_logger.recent_events(hours=1)
                started = [item for item in events if item.get("event_type") == "task_started"]
                self.assertGreaterEqual(len(started), 2)

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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                metadata={"required_capabilities": ["coding"], "skip_evidence_gate": True, "skip_placeholder_check": True},
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
                if "Review implement feature" in prompt or "Review Implement feature" in prompt:
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=4)

            failed = orchestrator.taskboard.get_task("FAIL-1")
            assert failed is not None
            self.assertEqual(failed.state.value, "failed")

            subjects = [msg.subject for msg in orchestrator.mailbox.list_messages()]
            self.assertTrue(any("Event task_failed" in subject for subject in subjects))

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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                        {"type": "cmd", "command": "echo publish playstore", "timeout": 10}
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
            self.assertIn("sensitive_commands_require_approval", blocked.metadata.get("error", ""))

            subjects = [msg.subject for msg in orchestrator.mailbox.list_messages()]
            self.assertTrue(any("Task blocked by compliance" in subject for subject in subjects))

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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="SEC-2",
                title="Publicar Android",
                description="Ejecucion sensible aprobada",
                role=Role.TEAM_LEAD,
                metadata={
                    "execution_plan": [
                        {"type": "cmd", "command": "echo publish playstore", "timeout": 10}
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
                        {"type": "cmd", "command": "echo publish playstore", "timeout": 10}
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
            self.assertIn("insufficient_approvers_required_2", failed.metadata.get("error", ""))

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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="ENG-SEC",
                title="Implementar cambio critico",
                description="Ajustar flujo de release en modulo sensible",
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                metadata={"required_capabilities": ["coding"], "skip_evidence_gate": True, "skip_placeholder_check": True},
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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
            guidance_entries = [item for item in entries if item.kind == "skill_mcp_guidance"]
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(router=router, runtime_dir=runtime_dir)

            task = WorkTask(
                task_id="DECIDE-1",
                title="Implement decision protocol",
                description="Add governance-aware delivery",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={"required_capabilities": ["coding"], "skip_quality_gates": True, "skip_evidence_gate": True, "skip_placeholder_check": True},
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
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
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


if __name__ == "__main__":
    unittest.main()
