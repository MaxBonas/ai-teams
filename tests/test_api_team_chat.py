import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.chat_quality import _evaluate_phase_evidence_gate
from api.utils import PROJECT_ROOT, _read_runtime_workflow_state, resolve_runtime_dir
from aiteam.config import build_default_router_policy
from aiteam.context_curator import ContextCuratorStore
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.adapters.base import ModelAdapter
from aiteam.sqlite_store import SqliteStore
from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask
from aiteam.types import AdapterResponse, ChannelType
from aiteam.workflow_planner import PhaseSpec


def _parse_sse_result(response) -> dict:
    """Parse an SSE streaming response and return the data of the 'result' event."""
    text = response.text
    current_event = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: ") and current_event == "result":
            return json.loads(line[6:])
    # Fallback: try parsing as plain JSON (backward compat)
    try:
        return response.json()
    except Exception:
        return {}


@contextmanager
def _safe_tempdir(prefix: str):
    root = Path(".pytest-tmp-safe") / f"{prefix}-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _load_runtime_tasks(runtime_dir: Path) -> list[dict]:
    return SqliteStore(runtime_dir / "aiteam.db").load_all_tasks()


def _runtime_dir_for(workspace: Path) -> Path:
    return resolve_runtime_dir(workspace, PROJECT_ROOT)


def _plan_markdown_files(workspace: Path) -> list[Path]:
    candidates = [workspace / "docs" / "aiteam", workspace / "planning"]
    files: list[Path] = []
    for directory in candidates:
        if not directory.exists():
            continue
        files.extend(sorted(path for path in directory.glob("*.md") if path.is_file()))
    return files


pytestmark = pytest.mark.slow


class GatePhaseResolutionTests(unittest.TestCase):
    def test_default_phases_are_bound_to_authoritative_lead_objective(self) -> None:
        phases = [
            PhaseSpec(
                phase_id="plan_engineering",
                role="ENGINEER",
                objective="Define corte de implementacion.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="build",
                role="ENGINEER",
                objective="Ejecuta exactamente el slice aprobado.",
                depends_on=["plan_engineering"],
            ),
        ]

        bound = api_main._bind_default_phases_to_lead_objective(
            phases,
            plan_source="default",
            lead_output=(
                "## [OBJECTIVE]\n"
                "Implementar generación de Tabla de Contenidos navegable con profundidad configurable.\n"
                "## Siguiente"
            ),
            user_message="Start next slice",
        )

        self.assertIn("Tabla de Contenidos navegable", bound[0].objective)
        self.assertIn("No sustituyas este objetivo", bound[0].objective)
        self.assertIn("Tabla de Contenidos navegable", bound[1].objective)
        self.assertIn("menor cambio coherente", bound[1].objective)

    def test_authoritative_lead_objective_uses_slice_objective_not_yaml_pipe(self) -> None:
        objective = api_main._extract_authoritative_lead_objective(
            lead_output=(
                "```yaml\n"
                "[RUN_MODE]\n"
                "slice_objective: \"Implementar TOC con anclas MD y flags CLI\"\n"
                "[CONTEXT]\n"
                "workspace_state: |\n"
                "  src/md_report/{cli.py,generator.py}\n"
                "```\n"
            ),
            user_message="Start next slice",
        )

        self.assertEqual(objective, "Implementar TOC con anclas MD y flags CLI")

    def test_phase_defaults_to_skip_peer_consultation_for_planning_and_advisory(self) -> None:
        self.assertTrue(
            api_main._phase_defaults_to_skip_peer_consultation(
                "plan_engineering",
                "ENGINEER",
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_peer_consultation(
                "research_current_toc_state",
                "RESEARCHER",
                advisory_context_phase=True,
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_peer_consultation(
                "build",
                "ENGINEER",
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_peer_consultation(
                "lead_close",
                "TEAM_LEAD",
            )
        )

    def test_phase_defaults_to_skip_specialist_prefetch_for_planning_and_advisory(self) -> None:
        self.assertTrue(
            api_main._phase_defaults_to_skip_specialist_prefetch(
                "plan_risks",
                "REVIEWER",
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_specialist_prefetch(
                "research_current_toc_state",
                "RESEARCHER",
                advisory_context_phase=True,
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_specialist_prefetch(
                "build",
                "ENGINEER",
            )
        )
        self.assertTrue(
            api_main._phase_defaults_to_skip_specialist_prefetch(
                "lead_close",
                "TEAM_LEAD",
            )
        )

    def test_normalize_advisory_context_phase_specs_removes_hard_dependency_from_build_flow(self) -> None:
        phases = [
            PhaseSpec(
                phase_id="research_current_toc_state",
                role="RESEARCHER",
                objective="Audita el estado actual del repo.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="implement_toc_feature",
                role="ENGINEER",
                objective="Implementa TOC.",
                depends_on=["research_current_toc_state"],
            ),
            PhaseSpec(
                phase_id="review_toc_implementation",
                role="REVIEWER",
                objective="Revisa TOC.",
                depends_on=["implement_toc_feature", "research_current_toc_state"],
            ),
        ]
        normalized = api_main._normalize_advisory_context_phase_specs(phases)
        normalized_by_id = {spec.phase_id: spec for spec in normalized}
        self.assertEqual(
            normalized_by_id["research_current_toc_state"].role,
            "SCOUT",
        )
        self.assertEqual(
            normalized_by_id["implement_toc_feature"].depends_on,
            [],
        )
        self.assertEqual(
            normalized_by_id["review_toc_implementation"].depends_on,
            ["implement_toc_feature"],
        )

    def test_normalize_advisory_planning_phase_specs_removes_hard_dependency_from_incremental_flow(self) -> None:
        phases = [
            PhaseSpec(
                phase_id="plan_research",
                role="RESEARCHER",
                objective="Compacta restricciones y riesgos.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="plan_engineering",
                role="ENGINEER",
                objective="Define el slice implementable.",
                depends_on=["plan_research"],
            ),
            PhaseSpec(
                phase_id="plan_risks",
                role="REVIEWER",
                objective="Lista riesgos y quality gates.",
                depends_on=["plan_research", "plan_engineering"],
            ),
            PhaseSpec(
                phase_id="build",
                role="ENGINEER",
                objective="Implementa el slice.",
                depends_on=["plan_engineering", "plan_risks"],
            ),
        ]
        normalized = api_main._normalize_advisory_context_phase_specs(phases)
        normalized_by_id = {spec.phase_id: spec for spec in normalized}
        self.assertEqual(normalized_by_id["plan_engineering"].depends_on, [])
        self.assertEqual(normalized_by_id["plan_risks"].depends_on, ["plan_engineering"])
        self.assertEqual(
            normalized_by_id["build"].depends_on,
            ["plan_engineering", "plan_risks"],
        )

    def test_actionable_failed_phase_ignores_advisory_context_research_phase(self) -> None:
        task_rows = {
            "research_current_toc_state": WorkTask(
                task_id="CHAT-A1::research_current_toc_state",
                title="Research current state",
                description="",
                role=Role.RESEARCHER,
                state=TaskState.FAILED,
            ),
            "implement_toc_feature": WorkTask(
                task_id="CHAT-A1::implement_toc_feature",
                title="Implement TOC",
                description="",
                role=Role.ENGINEER,
                state=TaskState.PENDING,
            ),
        }
        self.assertFalse(
            api_main._is_actionable_failed_phase(
                "research_current_toc_state",
                task_rows,
            )
        )
        self.assertTrue(
            api_main._is_actionable_failed_phase(
                "implement_toc_feature",
                task_rows,
            )
        )

    def test_actionable_failed_phase_ignores_delegate_support_phase(self) -> None:
        task_rows = {
            "delegate_review_test_runner_0": WorkTask(
                task_id="CHAT-ADELEGATE::delegate_review_test_runner_0",
                title="Support evidence",
                description="",
                role=Role.QA,
                state=TaskState.FAILED,
            ),
            "review": WorkTask(
                task_id="CHAT-ADELEGATE::review",
                title="Review",
                description="",
                role=Role.REVIEWER,
                state=TaskState.PENDING,
            ),
        }

        self.assertFalse(
            api_main._is_actionable_failed_phase(
                "delegate_review_test_runner_0",
                task_rows,
            )
        )
        self.assertTrue(api_main._is_actionable_failed_phase("review", task_rows))

    def test_actionable_failed_phase_ignores_advisory_plan_research_when_engineering_exists(self) -> None:
        task_rows = {
            "plan_research": WorkTask(
                task_id="CHAT-A2::plan_research",
                title="Plan Research",
                description="",
                role=Role.RESEARCHER,
                state=TaskState.FAILED,
            ),
            "plan_engineering": WorkTask(
                task_id="CHAT-A2::plan_engineering",
                title="Plan Engineering",
                description="",
                role=Role.ENGINEER,
                state=TaskState.PENDING,
            ),
            "build": WorkTask(
                task_id="CHAT-A2::build",
                title="Build",
                description="",
                role=Role.ENGINEER,
                state=TaskState.PENDING,
            ),
        }
        self.assertFalse(
            api_main._is_actionable_failed_phase(
                "plan_research",
                task_rows,
            )
        )
        self.assertTrue(
            api_main._is_actionable_failed_phase(
                "plan_engineering",
                task_rows,
            )
        )

    def test_preplanning_support_failure_detected_before_workflow_starts(self) -> None:
        detected, failed_support, reason_codes = api_main._detect_preplanning_support_failure(
            phase_states={
                "scout_context_curator": "failed",
                "lead_intake": "blocked",
                "plan_research": "pending",
                "build": "pending",
            },
            task_rows_by_phase={
                "lead_intake": WorkTask(
                    task_id="CHAT-P0::lead_intake",
                    title="Lead intake",
                    description="",
                    role=Role.TEAM_LEAD,
                    state=TaskState.BLOCKED,
                ),
                "scout_context_curator": WorkTask(
                    task_id="CHAT-P0::scout_context_curator",
                    title="Curator",
                    description="",
                    role=Role.SCOUT,
                    state=TaskState.FAILED,
                ),
                "plan_research": WorkTask(
                    task_id="CHAT-P0::plan_research",
                    title="Plan Research",
                    description="",
                    role=Role.RESEARCHER,
                    state=TaskState.PENDING,
                ),
            },
        )
        self.assertTrue(detected)
        self.assertEqual(failed_support, ["scout_context_curator"])
        self.assertIn("phase_failed:scout_context_curator", reason_codes)
        self.assertIn("lead_intake:blocked_by_support_context", reason_codes)

    def test_run_failed_phases_use_support_failures_when_preplanning_detected(self) -> None:
        resolved = api_main._resolve_run_failed_phases(
            failed_phases=[],
            preplanning_support_failure_detected=True,
            preplanning_support_failed_phases=[
                "scout_session_history",
                "scout_context_curator",
            ],
        )
        self.assertEqual(
            resolved,
            ["scout_session_history", "scout_context_curator"],
        )

    def test_routing_pause_is_skipped_when_review_rejected_is_authoritative(self) -> None:
        skip = api_main._should_skip_pause_for_user_due_to_authoritative_policy(
            (
                "El pipeline está bloqueado por falta de adaptadores disponibles "
                "para la fase de revisión. ¿Reconfiguro routing?"
            ),
            {
                "authoritative_close_state": "rejected",
                "primary_blocking_signals": ["review_rejected"],
                "blocking_signals": ["review_rejected", "qa_blocked"],
            },
        )
        self.assertTrue(skip)

    def test_non_routing_pause_is_not_skipped_when_review_rejected(self) -> None:
        skip = api_main._should_skip_pause_for_user_due_to_authoritative_policy(
            "La review pide elegir entre alcance mínimo o refactor completo. ¿Qué prefieres?",
            {
                "authoritative_close_state": "rejected",
                "primary_blocking_signals": ["review_rejected"],
                "blocking_signals": ["review_rejected"],
            },
        )
        self.assertFalse(skip)

    def test_review_rework_phase_specs_replace_rejected_review_tail(self) -> None:
        repaired = api_main._review_rework_phase_specs(
            [
                PhaseSpec(
                    phase_id="build_core",
                    role="ENGINEER",
                    objective="Implementar core",
                    depends_on=[],
                ),
                PhaseSpec(
                    phase_id="review_core",
                    role="REVIEWER",
                    objective="Revisar core",
                    depends_on=["build_core"],
                ),
                PhaseSpec(
                    phase_id="qa_core",
                    role="QA",
                    objective="Validar core",
                    depends_on=["review_core"],
                ),
            ],
            rejected_review_phase="review_core",
            review_feedback="status: rejected; falta cubrir imports rotos y tests.",
        )

        self.assertEqual([spec.phase_id for spec in repaired], [
            "build_core",
            "repair_after_review_core",
            "review_after_repair_after_review_core",
            "qa_after_repair_after_review_core",
        ])
        self.assertEqual(repaired[1].role, "ENGINEER")
        self.assertEqual(repaired[1].depends_on, ["build_core"])
        self.assertIn("review_core", repaired[1].objective)
        self.assertIn("imports rotos", repaired[1].objective)

    def test_failure_origin_prefers_preplanning_support(self) -> None:
        origin = api_main._determine_run_failure_origin(
            preplanning_support_failure_detected=True,
            planning_failed_phases=["plan_research"],
            failed_phases=["scout_session_history"],
            blocked_phases=[],
            semantic_gate_failures=[],
            evidence_gate_failures=[],
        )
        self.assertEqual(origin, "preplanning_support")

    def test_failure_origin_uses_execution_when_phase_is_blocked(self) -> None:
        origin = api_main._determine_run_failure_origin(
            preplanning_support_failure_detected=False,
            planning_failed_phases=[],
            failed_phases=[],
            blocked_phases=["validate_core_logic"],
            semantic_gate_failures=[],
            evidence_gate_failures=[],
        )
        self.assertEqual(origin, "execution")

    def test_failure_origin_uses_execution_when_only_quality_failures_exist(self) -> None:
        origin = api_main._determine_run_failure_origin(
            preplanning_support_failure_detected=False,
            planning_failed_phases=[],
            failed_phases=[],
            blocked_phases=[],
            semantic_gate_failures=["qa:blocked_status"],
            evidence_gate_failures=["qa:blocked"],
        )
        self.assertEqual(origin, "execution")

    def test_cascade_blocked_phases_do_not_become_evidence_root_causes(self) -> None:
        build = WorkTask(
            task_id="CHAT-CASCADE::build",
            title="Build",
            description="",
            role=Role.ENGINEER,
            state=TaskState.FAILED,
            metadata={"phase": "build", "error": "ungrounded_phase_block_detected"},
        )
        review = WorkTask(
            task_id="CHAT-CASCADE::review",
            title="Review",
            description="",
            role=Role.REVIEWER,
            dependencies=["CHAT-CASCADE::build"],
            state=TaskState.BLOCKED,
            metadata={
                "phase": "review",
                "blocked_reason": "dependency_failed",
                "blocked_dependencies": ["CHAT-CASCADE::build"],
            },
        )
        qa = WorkTask(
            task_id="CHAT-CASCADE::qa",
            title="QA",
            description="",
            role=Role.QA,
            dependencies=["CHAT-CASCADE::review"],
            state=TaskState.BLOCKED,
            metadata={
                "phase": "qa",
                "blocked_reason": "dependency_failed",
                "blocked_dependencies": ["CHAT-CASCADE::review"],
            },
        )

        cascade = api_main._cascade_blocked_phases(
            ["review", "qa"],
            {"build": build, "review": review, "qa": qa},
        )
        failures = api_main._filter_cascade_blocked_evidence_failures(
            ["build:ungrounded_phase_block", "review:blocked", "qa:blocked"],
            cascade_blocked_phases=cascade,
        )

        self.assertEqual(cascade, ["review", "qa"])
        self.assertEqual(failures, ["build:ungrounded_phase_block"])

    def test_build_preplanning_run_verdict_uses_support_failures_not_build_noise(self) -> None:
        verdict = api_main._build_preplanning_run_verdict(
            lead_state="blocked",
            preplanning_support_failure_detected=True,
            preplanning_support_failed_phases=[
                "scout_session_history",
                "scout_context_curator",
            ],
            preplanning_support_reason_codes=[
                "phase_failed:scout_session_history",
                "phase_failed:scout_context_curator",
                "lead_intake:blocked_by_support_context",
            ],
        )
        self.assertEqual(verdict["failure_origin"], "preplanning_support")
        self.assertEqual(
            verdict["failed_phases"],
            ["scout_session_history", "scout_context_curator"],
        )
        self.assertNotIn("build:not_completed", verdict["reason_codes"])

    def test_build_preplanning_run_verdict_for_plain_lead_block(self) -> None:
        verdict = api_main._build_preplanning_run_verdict(
            lead_state="blocked",
            preplanning_support_failure_detected=False,
            preplanning_support_failed_phases=[],
            preplanning_support_reason_codes=[],
        )
        self.assertEqual(verdict["failure_origin"], "none")
        self.assertEqual(verdict["failed_phases"], [])
        self.assertEqual(verdict["reason_codes"], ["lead_intake:blocked_before_workflow"])

    def test_generic_retry_requires_objective_clarification_when_workspace_has_no_product_scope(self) -> None:
        self.assertTrue(
            api_main._requires_explicit_project_objective_clarification(
                "Start the next highest-impact slice for the same project objective. "
                "Treat this as a clean retry from the current validated project state.",
                workspace_snapshot={"pyproject.toml": (1, 1), "README.md": (1, 1)},
            )
        )

    def test_generic_retry_does_not_require_clarification_when_workspace_has_product_scope(self) -> None:
        self.assertFalse(
            api_main._requires_explicit_project_objective_clarification(
                "Start the next highest-impact slice for the same project objective. "
                "Treat this as a clean retry from the current validated project state.",
                workspace_snapshot={"src/md_report/cli.py": (1, 1), "tests/test_cli.py": (1, 1)},
            )
        )

    def test_chat_waits_for_explicit_objective_before_planning_generic_retry_on_sparse_workspace(self) -> None:
        tmp_root = Path.cwd() / ".tmp-tests"
        tmp_root.mkdir(parents=True, exist_ok=True)
        workspace = tmp_root / f"objective-clarify-{uuid4().hex}"
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": (
                            "Start the next highest-impact slice for the same project objective. "
                            "Treat this as a clean retry from the current validated project state."
                        ),
                        "mode": "sprint5",
                        "max_rounds": 6,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("state", "")), "waiting_user")
                self.assertTrue(bool(payload.get("waiting_user")))
                self.assertIn(
                    "¿Cuál es el objetivo específico del proyecto que debo planificar?",
                    str(payload.get("clarification_question", "")),
                )
            finally:
                api_main.set_current_workspace(previous_workspace)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_failed_phase_root_cause_reason_codes_include_ungrounded_phase_block(self) -> None:
        task_rows_by_phase = {
            "code_review": WorkTask(
                task_id="CHAT-X::code_review",
                title="Code review",
                description="",
                role=Role.REVIEWER,
                state=TaskState.FAILED,
                metadata={
                    "error": "ungrounded_phase_block_detected: ungrounded_phase_block | visible=tests/test_md_report.py"
                },
            )
        }
        self.assertEqual(
            api_main._failed_phase_root_cause_reason_codes(
                task_rows_by_phase,
                ["code_review"],
            ),
            ["phase_failed:code_review:ungrounded_phase_block"],
        )

    def test_semantic_gate_uses_custom_plan_phase_slice_for_build_drift(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "plan_engineering_retry": WorkTask(
                    task_id="CHAT-G0::plan_engineering_retry",
                    title="Plan Engineering Retry",
                    description="",
                    role=Role.ENGINEER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Slice 2 aprobado con restricciones."},
                ),
                "engineer_css_integration": WorkTask(
                    task_id="CHAT-G0::engineer_css_integration",
                    title="Engineer Css Integration",
                    description="",
                    role=Role.ENGINEER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Se implemento Slice 9 fuera del alcance aprobado."},
                ),
            },
            phase_verdicts={
                "plan_engineering_retry": {
                    "status": "completed",
                    "slice_id": "2",
                },
                "engineer_css_integration": {
                    "status": "completed",
                    "role_hint": "engineer",
                    "contract_status": "drift",
                    "reason_codes": ["slice_drift"],
                    "slice_id": "9",
                },
            },
        )
        self.assertIn("build:slice_drift:2->9", failures)

    def test_semantic_gate_uses_custom_review_and_qa_phase_ids(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "review_slice2_code": WorkTask(
                    task_id="CHAT-G1::review_slice2_code",
                    title="Review Slice2 Code",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Veredicto: REJECTED — falta evidencia revisable."},
                ),
                "qa_slice2_validation": WorkTask(
                    task_id="CHAT-G1::qa_slice2_validation",
                    title="Qa Slice2 Validation",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Decision: BLOCKED — falta build verificable."},
                ),
            },
            phase_verdicts={
                "review_slice2_code": {
                    "status": "rejected",
                    "role_hint": "reviewer",
                },
                "qa_slice2_validation": {
                    "status": "blocked",
                    "role_hint": "qa",
                },
            },
        )
        self.assertIn("review:rejected_decision", failures)
        self.assertIn("qa:blocked_status", failures)

    def test_semantic_gate_detects_review_json_changes_requested(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "review_toc_implementation": WorkTask(
                    task_id="CHAT-G1::review_toc_implementation",
                    title="Review Toc Implementation",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={
                        "result": (
                            '{"summary":"Unicode handling needs work",'
                            '"recommendation":"CHANGES_REQUESTED"}'
                        )
                    },
                ),
            },
            phase_verdicts={},
        )

        self.assertIn("review:rejected_decision", failures)

    def test_semantic_gate_detects_review_json_blocked(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "review_toc_implementation": WorkTask(
                    task_id="CHAT-G1::review_toc_implementation",
                    title="Review Toc Implementation",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={
                        "result": (
                            '{"summary":"missing upstream artifacts",'
                            '"status":"BLOCKED"}'
                        )
                    },
                ),
            },
            phase_verdicts={},
        )

        self.assertIn("review:blocked_status", failures)

    def test_semantic_gate_detects_qa_json_failed(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "qa_toc_functionality": WorkTask(
                    task_id="CHAT-G1::qa_toc_functionality",
                    title="Qa Toc Functionality",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={
                        "result": (
                            '{"summary":"required validation evidence is missing",'
                            '"recommendation":"FAILED"}'
                        )
                    },
                ),
            },
            phase_verdicts={},
        )

        self.assertIn("qa:blocked_status", failures)

    def test_evidence_gate_uses_custom_engineer_reviewer_qa_phase_ids(self) -> None:
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase={
                "engineer_css_integration": WorkTask(
                    task_id="CHAT-G2::engineer_css_integration",
                    title="Engineer Css Integration",
                    description="",
                    role=Role.ENGINEER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Se modificaron archivos y se ejecutaron checks."},
                ),
                "review_slice2_code": WorkTask(
                    task_id="CHAT-G2::review_slice2_code",
                    title="Review Slice2 Code",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "APPROVED con findings menores."},
                ),
                "qa_slice2_validation": WorkTask(
                    task_id="CHAT-G2::qa_slice2_validation",
                    title="Qa Slice2 Validation",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={"result": "PASSED con evidencia de regresion."},
                ),
            },
            execution_steps=2,
            execution_steps_success=1,
            successful_checks=["test"],
            artifact_created=1,
            artifact_modified=1,
            require_test_or_build_check=True,
        )
        self.assertEqual(failures, [])

    def test_evidence_gate_surfaces_specific_failed_gate_phase_reason(self) -> None:
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase={
                "build": WorkTask(
                    task_id="CHAT-G3::build",
                    title="Build",
                    description="",
                    role=Role.ENGINEER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "Build ok."},
                ),
                "code_review": WorkTask(
                    task_id="CHAT-G3::code_review",
                    title="Code review",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.FAILED,
                    metadata={
                        "error": "ungrounded_phase_block_detected: ungrounded_phase_block"
                    },
                ),
                "qa_validation": WorkTask(
                    task_id="CHAT-G3::qa_validation",
                    title="Qa validation",
                    description="",
                    role=Role.QA,
                    state=TaskState.BLOCKED,
                    metadata={},
                ),
            },
            execution_steps=1,
            execution_steps_success=1,
            successful_checks=["test"],
            artifact_created=1,
            artifact_modified=1,
            require_test_or_build_check=True,
        )
        self.assertIn("review:ungrounded_phase_block", failures)
        self.assertIn("qa:blocked", failures)

    def test_evidence_gate_does_not_require_build_for_review_revalidation_flow(self) -> None:
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase={
                "code_review_revalidation": WorkTask(
                    task_id="CHAT-R1::code_review_revalidation",
                    title="Code review revalidation",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={"result": "APPROVED tras revisar artefactos visibles."},
                ),
                "qa_validation_toc": WorkTask(
                    task_id="CHAT-R1::qa_validation_toc",
                    title="QA validation toc",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={"result": "PASSED con evidencia de validacion."},
                ),
            },
            execution_steps=0,
            execution_steps_success=0,
            successful_checks=[],
            artifact_created=0,
            artifact_modified=0,
            require_test_or_build_check=True,
        )
        self.assertEqual(failures, [])

    def test_evidence_gate_does_not_require_review_for_qa_only_validation_flow(self) -> None:
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase={
                "qa_core_functionality": WorkTask(
                    task_id="CHAT-QA1::qa_core_functionality",
                    title="QA core functionality",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={"result": "FAILED con evidencia real de validacion."},
                ),
            },
            execution_steps=1,
            execution_steps_success=1,
            successful_checks=["test"],
            artifact_created=0,
            artifact_modified=0,
            require_test_or_build_check=True,
        )
        self.assertEqual(failures, [])

    def test_evidence_gate_preserves_missing_execution_plan_for_failed_build(self) -> None:
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase={
                "build": WorkTask(
                    task_id="CHAT-GPLAN::build",
                    title="Build",
                    description="",
                    role=Role.ENGINEER,
                    state=TaskState.FAILED,
                    metadata={
                        "phase": "build",
                        "require_execution_plan": True,
                        "error": "missing_execution_plan_required",
                    },
                ),
                "review": WorkTask(
                    task_id="CHAT-GPLAN::review",
                    title="Review",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.BLOCKED,
                    metadata={"blocked_reason": "dependency_failed"},
                ),
                "qa": WorkTask(
                    task_id="CHAT-GPLAN::qa",
                    title="Qa",
                    description="",
                    role=Role.QA,
                    state=TaskState.BLOCKED,
                    metadata={"blocked_reason": "dependency_failed"},
                ),
            },
            execution_steps=0,
            execution_steps_success=0,
            successful_checks=[],
            artifact_created=0,
            artifact_modified=0,
            require_test_or_build_check=True,
        )

        self.assertIn("build:missing_execution_plan", failures)
        self.assertIn("build:phase_failed", failures)

    def test_evidence_gate_does_not_treat_file_delivery_as_build_validation(self) -> None:
        base_tasks = {
            "build": WorkTask(
                task_id="CHAT-G4::build",
                title="Build",
                description="",
                role=Role.ENGINEER,
                state=TaskState.COMPLETED,
                metadata={"result": "Se entrego el archivo solicitado."},
            ),
            "review": WorkTask(
                task_id="CHAT-G4::review",
                title="Review",
                description="",
                role=Role.REVIEWER,
                state=TaskState.COMPLETED,
                metadata={"result": "APPROVED con alcance revisado."},
            ),
            "qa": WorkTask(
                task_id="CHAT-G4::qa",
                title="Qa",
                description="",
                role=Role.QA,
                state=TaskState.COMPLETED,
                metadata={"result": "PASSED con validacion declarada."},
            ),
        }
        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase=base_tasks,
            execution_steps=1,
            execution_steps_success=1,
            successful_checks=["file_delivery"],
            artifact_created=0,
            artifact_modified=1,
            require_test_or_build_check=True,
        )
        self.assertIn("build:missing_test_or_build_check", failures)

        failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase=base_tasks,
            execution_steps=1,
            execution_steps_success=1,
            successful_checks=["import"],
            artifact_created=0,
            artifact_modified=1,
            require_test_or_build_check=True,
        )
        self.assertNotIn("build:missing_test_or_build_check", failures)

    def test_continuation_with_modified_artifacts_keeps_build_validation_failure(self) -> None:
        failures = api_main._filter_continuation_evidence_gate_failures(
            ["build:missing_test_or_build_check"],
            continuation_requested=True,
            artifact_created=0,
            artifact_modified=1,
        )
        self.assertEqual(failures, ["build:missing_test_or_build_check"])

    def test_continuation_without_artifacts_can_suppress_delivery_validation_noise(self) -> None:
        failures = api_main._filter_continuation_evidence_gate_failures(
            [
                "build:no_execution_evidence",
                "build:no_successful_execution_steps",
                "build:missing_test_or_build_check",
            ],
            continuation_requested=True,
            artifact_created=0,
            artifact_modified=0,
        )
        self.assertEqual(failures, [])

    def test_auto_post_validation_failure_replaces_missing_check_noise(self) -> None:
        failures = api_main._merge_auto_post_validation_failure(
            ["build:missing_test_or_build_check"],
            {"success": False, "skipped": False, "command": "python syntax smoke src"},
        )

        self.assertEqual(failures, ["build:auto_post_build_validation_failed"])

    def test_semantic_gate_detects_review_and_qa_evidence_blockers_from_text(self) -> None:
        failures = api_main._evaluate_phase_semantic_gate(
            task_rows_by_phase={
                "review": WorkTask(
                    task_id="CHAT-G5::review",
                    title="Review",
                    description="",
                    role=Role.REVIEWER,
                    state=TaskState.COMPLETED,
                    metadata={
                        "result": "No puedo revisar la implementacion: insufficient evidence."
                    },
                ),
                "qa": WorkTask(
                    task_id="CHAT-G5::qa",
                    title="Qa",
                    description="",
                    role=Role.QA,
                    state=TaskState.COMPLETED,
                    metadata={
                        "result": "No puedo validar el comportamiento: faltan checks ejecutados."
                    },
                ),
            },
            phase_verdicts={},
        )
        self.assertIn("review:blocked_status", failures)
        self.assertIn("qa:blocked_status", failures)

    def test_classify_check_detects_import_and_compile_checks(self) -> None:
        self.assertEqual(
            api_main._classify_check_from_command("python -c \"import md_report\""),
            "import",
        )
        self.assertEqual(
            api_main._classify_check_from_command("python syntax smoke src/md_report/cli.py"),
            "build",
        )
        self.assertEqual(
            api_main._classify_check_from_command("py -m compileall src"),
            "build",
        )

    def test_auto_post_build_validation_prefers_changed_python_artifact_smoke(self) -> None:
        with _safe_tempdir("auto-post-ok") as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_ok.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="1 passed",
                stderr="",
            )
            with patch.object(api_main.subprocess, "run", return_value=completed) as run_mock:
                result = api_main._run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO",
                    phase_task_set={"CHAT-AUTO::build"},
                    artifact_files=["src/pkg/module.py"],
                    event_logger=_Logger(),
                )

            run_mock.assert_called_once()
            self.assertTrue(bool(result.get("success")))
            self.assertEqual(result.get("reason"), "auto_post_build_validation")
            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertEqual(len(step_payloads), 1)
            self.assertTrue(bool(step_payloads[0].get("success")))
            self.assertEqual(
                api_main._classify_check_from_command(str(step_payloads[0].get("command", ""))),
                "build",
            )
            self.assertIn("src/pkg/module.py", str(step_payloads[0].get("command", "")))

    def test_direct_auto_post_build_validation_replays_failed_pytest_check(self) -> None:
        with _safe_tempdir("auto-post-replay-pytest") as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_ok.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="1 passed",
                stderr="",
            )
            with patch.object(api_main.subprocess, "run", return_value=completed) as run_mock:
                result = api_main._run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO",
                    phase_task_set={"CHAT-AUTO::build"},
                    artifact_files=["src/pkg/module.py"],
                    event_logger=_Logger(),
                    run_profile="solo_lead",
                    failed_validation_result={
                        "success": False,
                        "command": "python -m pytest -q --tb=short",
                        "exit_code": 1,
                    },
                )

            run_mock.assert_called_once()
            called_args = list(run_mock.call_args.args[0])
            self.assertIn("-m", called_args)
            self.assertIn("pytest", called_args)
            self.assertNotIn("-c", called_args)
            self.assertTrue(bool(result.get("success")))
            self.assertTrue(bool(result.get("replayed_failed_command")))
            self.assertEqual(result.get("command"), "python -m pytest -q --tb=short")
            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertEqual(len(step_payloads), 1)
            self.assertTrue(bool(step_payloads[0].get("replayed_failed_command")))

    def test_direct_auto_post_build_validation_replays_failed_check_without_artifacts(self) -> None:
        with _safe_tempdir("auto-post-replay-no-artifacts") as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_ok.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="still failing",
                stderr="",
            )
            with patch.object(api_main.subprocess, "run", return_value=completed) as run_mock:
                result = api_main._run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO",
                    phase_task_set={"CHAT-AUTO::build"},
                    artifact_files=[],
                    event_logger=_Logger(),
                    run_profile="solo_lead",
                    failed_validation_result={
                        "success": False,
                        "command": "python -m pytest -q --tb=short",
                        "exit_code": 1,
                    },
                )

            run_mock.assert_called_once()
            self.assertFalse(bool(result.get("success")))
            self.assertTrue(bool(result.get("replayed_failed_command")))
            self.assertEqual(result.get("target_task_id"), "CHAT-AUTO::build")
            self.assertEqual(result.get("command"), "python -m pytest -q --tb=short")
            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertEqual(len(step_payloads), 1)
            self.assertTrue(bool(step_payloads[0].get("replayed_failed_command")))

    def test_safe_replay_auto_validation_command_accepts_own_syntax_smoke(self) -> None:
        with _safe_tempdir("auto-post-replay-syntax-smoke") as workspace:
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )

            args, label = api_main._safe_replay_auto_validation_command(
                workspace,
                "python syntax smoke imports src/pkg/module.py",
            )

            self.assertTrue(args)
            self.assertEqual(label, "python syntax smoke imports src/pkg/module.py")
            self.assertIn("--import-modules", args)
            self.assertIn("src/pkg/module.py", args)

    def test_auto_post_build_validation_records_failed_artifact_smoke_without_success_check(self) -> None:
        with _safe_tempdir("auto-post-fail") as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_fail.py").write_text(
                "def test_fail():\n    assert False\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="failed",
                stderr="",
            )
            with patch.object(api_main.subprocess, "run", return_value=completed):
                result = api_main._run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO",
                    phase_task_set={"CHAT-AUTO::repair_generator"},
                    artifact_files=["src/pkg/module.py"],
                    event_logger=_Logger(),
                )

            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertFalse(bool(result.get("success")))
            self.assertEqual(result.get("exit_code"), 1)
            self.assertEqual(result.get("target_task_id"), "CHAT-AUTO::repair_generator")
            self.assertEqual(len(step_payloads), 1)
            self.assertFalse(bool(step_payloads[0].get("success")))
            self.assertEqual(
                api_main._classify_check_from_command(str(step_payloads[0].get("command", ""))),
                "build",
            )
            self.assertEqual(step_payloads[0].get("target_task_id"), None)

    def test_auto_post_build_validation_skip_returns_structured_reason(self) -> None:
        with _safe_tempdir("auto-post-skip") as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            result = api_main._run_auto_post_build_validation(
                runtime_dir=runtime_dir,
                workspace=workspace,
                task_root="CHAT-AUTO",
                phase_task_set={"CHAT-AUTO::build"},
                artifact_files=["README.md"],
                event_logger=_Logger(),
            )

            self.assertTrue(bool(result.get("skipped")))
            self.assertEqual(result.get("reason"), "no_safe_validation_command")
            self.assertTrue(
                any(event_type == "chat_auto_validation_skipped" for event_type, _ in events)
            )

    def test_auto_validation_artifact_smoke_ignores_unrelated_broken_tests(self) -> None:
        with _safe_tempdir("auto-validation-artifact-smoke") as workspace:
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_broken.py").write_text(
                "assert 'unterminated\n",
                encoding="utf-8",
            )

            args, command_label = api_main._auto_validation_command_for_workspace(
                workspace,
                ["src/pkg/module.py"],
            )
            completed = subprocess.run(
                args,
                cwd=str(workspace),
                env=api_main._auto_validation_env(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertIn("syntax smoke", command_label)
            self.assertIn("src/pkg/module.py", command_label)
            self.assertEqual(
                api_main._classify_check_from_command(command_label),
                "build",
            )
            self.assertEqual(completed.returncode, 0)

    def test_auto_validation_artifact_smoke_imports_changed_test_artifacts(self) -> None:
        workspace = Path.cwd() / ".tmp" / f"auto_validation_changed_test_{uuid4().hex}"
        try:
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_module.py").write_text(
                "from src.pkg.module import MISSING\n\n"
                "def test_missing():\n"
                "    assert MISSING\n",
                encoding="utf-8",
            )

            args, command_label = api_main._auto_validation_command_for_workspace(
                workspace,
                ["src/pkg/module.py", "tests/test_module.py"],
            )
            completed = subprocess.run(
                args,
                cwd=str(workspace),
                env=api_main._auto_validation_env(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertIn("syntax smoke", command_label)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("ImportError", completed.stdout)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_auto_validation_solo_lead_imports_related_src_package_modules(self) -> None:
        workspace = Path.cwd() / ".tmp" / f"auto_validation_related_imports_{uuid4().hex}"
        try:
            package_dir = workspace / "src" / "md_report"
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "toc_generator.py").write_text(
                "class TocGenerator:\n"
                "    pass\n",
                encoding="utf-8",
            )
            (package_dir / "report_generator.py").write_text(
                "from src.md_report.toc_generator import generate_toc\n",
                encoding="utf-8",
            )
            (package_dir / "cli.py").write_text(
                "from .report_generator import ReportGenerator\n",
                encoding="utf-8",
            )
            (package_dir / "test_integration_toc.py").write_text(
                "def test_placeholder():\n"
                "    assert True\n",
                encoding="utf-8",
            )

            args, command_label = api_main._auto_validation_command_for_workspace(
                workspace,
                ["src/md_report/test_integration_toc.py"],
                import_related_modules=True,
            )
            completed = subprocess.run(
                args,
                cwd=str(workspace),
                env=api_main._auto_validation_env(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertIn("syntax smoke", command_label)
            self.assertIn("src/md_report/cli.py", command_label)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("ImportError", completed.stdout)
            self.assertIn("generate_toc", completed.stdout)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_auto_validation_non_python_artifact_does_not_run_global_pytest(self) -> None:
        with _safe_tempdir("auto-validation-non-python") as workspace:
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (workspace / "docs").mkdir(parents=True, exist_ok=True)
            (workspace / "docs" / "plan.md").write_text("plan\n", encoding="utf-8")
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_broken.py").write_text(
                "assert 'unterminated\n",
                encoding="utf-8",
            )

            args, command_label = api_main._auto_validation_command_for_workspace(
                workspace,
                ["docs/plan.md"],
            )

            self.assertIn("syntax smoke", command_label)
            self.assertIn("src", command_label)
            self.assertNotIn("pytest", command_label)
            self.assertNotIn("-m", args)

    def test_auto_pre_phase_validation_runs_for_validation_only_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            workspace = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_ok.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="1 passed",
                stderr="",
            )
            phases = [
                PhaseSpec(
                    phase_id="validate_core_logic",
                    role="RESEARCHER",
                    objective="Ejecutar pytest tests/ y validar la logica actual.",
                    depends_on=[],
                ),
                PhaseSpec(
                    phase_id="qa_core_functionality",
                    role="QA",
                    objective="Ejecutar suite completa de tests.",
                    depends_on=["validate_core_logic"],
                ),
            ]

            with patch.object(api_main.subprocess, "run", return_value=completed) as run_mock:
                result = api_main._run_auto_pre_phase_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO-PRE",
                    phases=phases,
                    phase_task_ids={
                        "validate_core_logic": "CHAT-AUTO-PRE::validate_core_logic",
                        "qa_core_functionality": "CHAT-AUTO-PRE::qa_core_functionality",
                    },
                    event_logger=_Logger(),
                )

            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.args[0][-2], "-c")
            self.assertTrue(bool(result.get("success")))
            self.assertEqual(result.get("command"), "python test import smoke")
            self.assertEqual(result.get("target_phase"), "validate_core_logic")
            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertEqual(len(step_payloads), 1)
            self.assertEqual(step_payloads[0].get("reason"), "auto_pre_phase_validation")
            self.assertEqual(
                api_main._classify_check_from_command(str(step_payloads[0].get("command", ""))),
                "test",
            )

    def test_auto_pre_phase_validation_runs_for_solo_lead_build(self) -> None:
        with _safe_tempdir("auto-pre-solo-lead") as tmp:
            runtime_dir = tmp / "runtime"
            workspace = tmp / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="src/pkg/module.py: ImportError: broken import\n",
                stderr="",
            )
            phases = [
                PhaseSpec(
                    phase_id="build",
                    role="TEAM_LEAD",
                    objective="Perfil solo_lead/direct: reparar primero el estado actual.",
                    depends_on=[],
                )
            ]

            with patch.object(api_main.subprocess, "run", return_value=completed):
                result = api_main._run_auto_pre_phase_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO-PRE-SOLO",
                    phases=phases,
                    phase_task_ids={"build": "CHAT-AUTO-PRE-SOLO::build"},
                    event_logger=_Logger(),
                    run_profile="solo_lead",
                )

            self.assertFalse(bool(result.get("success")))
            self.assertEqual(result.get("target_phase"), "build")
            self.assertEqual(result.get("task_id"), "CHAT-AUTO-PRE-SOLO::build")
            self.assertIn("syntax smoke", str(result.get("command", "")))
            step_payloads = [
                payload for event_type, payload in events if event_type == "execution_step"
            ]
            self.assertEqual(len(step_payloads), 1)
            self.assertEqual(step_payloads[0].get("reason"), "auto_pre_phase_validation")

    def test_auto_validation_context_tells_solo_lead_to_repair_failed_precheck(self) -> None:
        block = api_main._format_auto_validation_context(
            {
                "command": "python -m pytest -q --tb=short",
                "success": False,
                "exit_code": 2,
                "stdout": "SyntaxError: unterminated string literal\n",
                "stderr": "",
            },
            direct_profile=True,
            repair_first_mode=True,
        )

        self.assertIn("repara primero", block.lower())
        self.assertIn("no abras un slice nuevo", block.lower())
        self.assertIn("path=...", block)
        self.assertIn("SyntaxError", block)

    def test_direct_repair_first_rewrites_build_task_contract(self) -> None:
        task = WorkTask(
            task_id="CHAT-REPAIR::build",
            title="Build",
            description="Original build objective\n",
            role=Role.TEAM_LEAD,
            metadata={
                "phase_contract": {
                    "phase_id": "build",
                    "role": "TEAM_LEAD",
                    "objective": "Start the next slice",
                }
            },
        )

        changed = api_main._apply_direct_repair_first_to_phase_task(
            task,
            {
                "success": False,
                "command": "python -m pytest -q --tb=short",
                "exit_code": 2,
                "stdout": "SyntaxError: unterminated string literal",
                "stderr": "",
            },
        )

        self.assertTrue(changed)
        self.assertTrue(task.title.startswith("Repair "))
        self.assertIn("[REPAIR_FIRST_DIRECTIVE]", task.description)
        self.assertIn("Prohibido: cerrar solo con diagnostico", task.description)
        self.assertTrue(task.metadata.get("repair_first_required"))
        phase_contract = task.metadata.get("phase_contract", {})
        self.assertEqual(phase_contract.get("contract_kind"), "solo_lead_repair_first")
        self.assertIn("Repair the failed pre-build validation", phase_contract.get("objective", ""))
        self.assertEqual(
            phase_contract.get("repair_first_original_objective"),
            "Start the next slice",
        )

    def test_direct_post_build_repair_rewrites_build_task_contract(self) -> None:
        task = WorkTask(
            task_id="CHAT-REPAIR-POST::build",
            title="Build",
            description="[REPAIR_FIRST_DIRECTIVE]\nprecheck anterior\n[/REPAIR_FIRST_DIRECTIVE]\n",
            role=Role.TEAM_LEAD,
            metadata={
                "phase_contract": {
                    "phase_id": "build",
                    "role": "TEAM_LEAD",
                    "objective": "Repair syntax-only failure",
                }
            },
        )

        changed = api_main._apply_direct_post_build_repair_to_phase_task(
            task,
            {
                "success": False,
                "command": "python syntax smoke imports tests/test_report_generator.py",
                "exit_code": 1,
                "stdout": (
                    "ImportError: cannot import name 'generate_report' from "
                    "'src.md_report.generator'\n"
                ),
                "stderr": "",
            },
        )

        self.assertTrue(changed)
        self.assertIn("generate_report", task.description)
        self.assertIn("modificar todos los archivos relacionados", task.description)
        self.assertEqual(task.metadata.get("repair_first_origin"), "auto_post_build_validation")
        self.assertTrue(task.metadata.get("auto_post_build_repair_attempted"))
        phase_contract = task.metadata.get("phase_contract", {})
        self.assertEqual(phase_contract.get("contract_kind"), "solo_lead_post_build_repair")
        self.assertIn("post-build validation", phase_contract.get("objective", ""))
        self.assertIn("all minimal related files", phase_contract.get("objective", ""))
        self.assertEqual(
            phase_contract.get("repair_first_original_objective"),
            "Repair syntax-only failure",
        )

    def test_auto_post_build_validation_ignores_prior_pre_phase_smoke(self) -> None:
        with _safe_tempdir("auto-post-after-pre") as tmp:
            runtime_dir = tmp / "runtime"
            workspace = tmp / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "pkg" / "module.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "event_type": "execution_step",
                        "payload": {
                            "task_id": "CHAT-AUTO-POST::build",
                            "success": True,
                            "command": "python syntax smoke src",
                            "reason": "auto_pre_phase_validation",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events: list[tuple[str, dict]] = []

            class _Logger:
                def emit(self, event_type: str, payload: dict) -> None:
                    events.append((event_type, payload))

            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="python_syntax_smoke: 1 files, 0 failed\n",
                stderr="",
            )
            with patch.object(api_main.subprocess, "run", return_value=completed) as run_mock:
                result = api_main._run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root="CHAT-AUTO-POST",
                    phase_task_set={"CHAT-AUTO-POST::build"},
                    artifact_files=["src/pkg/module.py"],
                    event_logger=_Logger(),
                    run_profile="solo_lead",
                )

            run_mock.assert_called_once()
            self.assertTrue(bool(result.get("success")))
            self.assertEqual(result.get("reason"), "auto_post_build_validation")

    def test_auto_pre_validation_command_uses_fast_test_import_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_imports.py").write_text(
                "import missing_dependency_for_precheck\n",
                encoding="utf-8",
            )

            args, command_label = api_main._auto_pre_validation_command_for_workspace(workspace)
            completed = subprocess.run(
                args,
                cwd=str(workspace),
                env=api_main._auto_validation_env(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(command_label, "python test import smoke")
            self.assertNotIn("-m", args)
            self.assertEqual(api_main._classify_check_from_command(command_label), "test")
            self.assertEqual(completed.returncode, 1)
            self.assertIn("missing_dependency_for_precheck", completed.stdout)
            self.assertIn("test_import_smoke: 1 failed", completed.stdout)

    def test_auto_validation_prefers_workspace_python_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            workspace_python = workspace / ".venv" / "Scripts" / "python.exe"
            workspace_python.parent.mkdir(parents=True, exist_ok=True)
            workspace_python.write_text("", encoding="utf-8")

            args, command_label = api_main._auto_pre_validation_command_for_workspace(workspace)

            self.assertEqual(args, [])
            self.assertEqual(command_label, "")
            self.assertEqual(api_main._python_executable_for_workspace(workspace), str(workspace_python))

    def test_auto_pre_phase_validation_skips_when_plan_has_implementation_phase(self) -> None:
        phases = [
            PhaseSpec(
                phase_id="build",
                role="ENGINEER",
                objective="Implementar el cambio aprobado.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="qa",
                role="QA",
                objective="Ejecutar tests despues del build.",
                depends_on=["build"],
            ),
        ]

        self.assertFalse(api_main._plan_allows_pre_phase_validation(phases))

    def test_auto_pre_phase_validation_skips_engineer_phase_that_mentions_tests(self) -> None:
        phases = [
            PhaseSpec(
                phase_id="engineer_core_implementation",
                role="ENGINEER",
                objective="Implementar la funcionalidad principal y sus tests de regresion.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="qa_core_functionality",
                role="QA",
                objective="Ejecutar tests despues de la implementacion.",
                depends_on=["engineer_core_implementation"],
            ),
        ]

        self.assertFalse(api_main._phase_requests_validation_execution(phases[0]))
        self.assertFalse(api_main._plan_allows_pre_phase_validation(phases))


class ReplanIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora." in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[REPLAN]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: recuperar contexto antes de implementar\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar con contexto validado\n"
                    "depends_on: [discovery]\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar build replanificado\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar build replanificado\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Hace falta discovery antes del build."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=60,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice inicial\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar resultado\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar salida\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan inicial preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nWorkflow phases actualizadas tras replan.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class ProbeIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: architecture_review]\n"
                    "Conviene revisar arquitectura antes de construir."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Scout/probe output.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class PlanModeIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "MODO PLAN" in joined or "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "Plan del Lead:\n"
                    "- Objetivo: definir alcance sin ejecutar.\n"
                    "- Riesgo principal: validar dependencias antes del build.\n"
                    "- Siguiente paso: lanzar Sprint solo cuando el objetivo este cerrado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        return AdapterResponse(
            success=True,
            content="Scout output for plan mode.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class ForceGateIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora." in joined:
            return AdapterResponse(
                success=True,
                content="Autorizado para continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if "Fase origen: review" in joined:
                return AdapterResponse(
                    success=True,
                    content='[FORCE_GATE: "build"]\nLa build debe regatearse de nuevo.',
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Review Completed build" in joined or "QA Completed build" in joined:
            return AdapterResponse(
                success=True,
                content="Gate reabierta aprobada.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe forzo gate adicional sobre build.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class DelegateSpecialistHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_root = Path(".tmp_delegate_specialist_helpers")
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self_outer = self

        class _WorkspaceTemporaryDirectory:
            def __init__(self, *args, **kwargs) -> None:
                self.name = str(self_outer._tmp_root / f"tmp_{uuid4().hex}")
                Path(self.name).mkdir(parents=True, exist_ok=True)

            def __enter__(self):
                return self.name

            def __exit__(self, exc_type, exc, tb):
                shutil.rmtree(self.name, ignore_errors=True)

        tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.TemporaryDirectory = self._previous_temporary_directory
        shutil.rmtree(self._tmp_root, ignore_errors=True)

    def test_workspace_artifact_snapshot_ignores_aiteam_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = _runtime_dir_for(workspace)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "events.jsonl").write_text("{}", encoding="utf-8")
            (workspace / "feature.py").write_text("print('ok')\n", encoding="utf-8")

            snapshot = api_main._workspace_artifact_snapshot(workspace)

            self.assertIn("feature.py", snapshot)
            self.assertNotIn(".aiteam/events.jsonl", snapshot)

    def test_extract_delegate_request_wrapper_supports_specialized_intents(self) -> None:
        request = api_main._extract_delegate_request(
            '[DELEGATE_BROWSER_REPRO: "reproduce el bug"]\n'
            "[WAIT_POLICY: quorum]\n"
            "[DELEGATE_BUDGET: +4]"
        )

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.intent, "delegate_browser_repro")
        self.assertEqual(request.wait_policy, "quorum")
        self.assertEqual(request.delegate_budget, 4)

    def test_detect_preplan_surface_hints_combines_surfaces(self) -> None:
        hints = api_main._detect_preplan_surface_hints(
            "Research the API docs, run a semgrep security audit, and validate the browser flow"
        )

        self.assertIn("browser", list(hints.get("surfaces", []) or []))
        self.assertIn("security", list(hints.get("surfaces", []) or []))
        self.assertIn("research", list(hints.get("surfaces", []) or []))
        self.assertIn("delegate_browser_repro", list(hints.get("recommended_delegate_intents", []) or []))
        self.assertIn("delegate_mcp_probe", list(hints.get("recommended_delegate_intents", []) or []))
        self.assertIn("skill_worker", list(hints.get("recommended_specialists", []) or []))

    def test_detect_preplan_surface_hints_does_not_treat_cli_html_css_as_browser(self) -> None:
        hints = api_main._detect_preplan_surface_hints(
            "Crea un CLI Python que convierta Markdown a HTML con CSS embebido y tests con pytest"
        )

        self.assertNotIn("browser", list(hints.get("surfaces", []) or []))
        self.assertNotIn(
            "delegate_browser_repro",
            list(hints.get("recommended_delegate_intents", []) or []),
        )

    def test_build_preplan_signal_block_includes_detected_hints(self) -> None:
        block = api_main._build_preplan_signal_block(
            {
                "surfaces": ["browser", "security"],
                "recommended_delegate_intents": ["delegate_browser_repro", "delegate_mcp_probe"],
                "recommended_specialists": ["browser_operator", "skill_worker"],
            }
        )

        self.assertIn("[PREPLAN_SIGNALS]", block)
        self.assertIn("surfaces=browser, security", block)
        self.assertIn("delegate_browser_repro", block)
        self.assertIn("skill_worker", block)

    def test_build_context_curator_prompt_compacts_by_surface(self) -> None:
        prompt = api_main._build_context_curator_prompt(
            message="Audit security and inspect browser regressions",
            surface_hints={"surfaces": ["security", "browser"]},
            project_state_raw="FILES: app.py, auth.py",
            session_history_raw="CHAT-1: se toco login flow",
        )

        self.assertIn("Superficies detectadas: security, browser", prompt)
        self.assertIn("[PROJECT_STATE]", prompt)
        self.assertIn("[SESSION_HISTORY]", prompt)
        self.assertIn("Compacta el contexto del proyecto", prompt)

    def test_estimate_preplan_context_pressure_reads_previous_chat_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = _runtime_dir_for(workspace)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            workflow_state = {
                "CHAT-prev-ctx": {
                    "delegate_batches": [{"id": "b1"}, {"id": "b2"}],
                    "phase_outputs": {
                        "discovery": "D" * 900,
                        "build": "B" * 1300,
                    },
                    "project_context_summary": "Proyecto compacto",
                    "chat_context_summary": "Chat compacto",
                    "phase_context_summaries": {
                        "discovery": "ctx 1",
                        "build": "ctx 2",
                        "review": "ctx 3",
                        "qa": "ctx 4",
                    },
                }
            }
            SqliteStore(runtime_dir / "aiteam.db").save_workflow_state(workflow_state)
            chat_context_dir = runtime_dir / "context" / "chats"
            chat_context_dir.mkdir(parents=True, exist_ok=True)
            (chat_context_dir / "CHAT-prev-ctx.json").write_text(
                json.dumps(
                    {
                        "version": "project_context_v1",
                        "project_key": str(workspace.resolve()),
                        "chat_root": "CHAT-prev-ctx",
                        "working_set": [],
                        "durable_facts": [],
                        "decisions": [],
                        "open_questions": [{"text": "revisar auth", "confidence": 0.6}],
                        "invalidations": [{"text": "replan_partial", "confidence": 0.8}],
                        "next_actions": [],
                        "source_task_ids": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            pressure = api_main._estimate_preplan_context_pressure(
                runtime_dir=runtime_dir,
                continuation_requested=True,
                continuation_of="CHAT-prev-ctx",
                continuation_snapshot="build:failed, review:pending",
            )

            self.assertTrue(pressure["recommend_context_curator"])
            self.assertIn(pressure["level"], {"medium", "high"})
            self.assertIn("continuation_requested", pressure["signals"])
            self.assertEqual(
                str((pressure.get("context_compaction", {}) or {}).get("level", "")),
                "medium",
            )
            self.assertTrue(
                bool((pressure.get("context_compaction", {}) or {}).get("priority_boost", False))
            )

    def test_build_curated_context_block_reads_project_and_chat_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = _runtime_dir_for(workspace)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            api_main._persist_preplan_context(
                runtime_dir=runtime_dir,
                workspace=workspace,
                task_root="CHAT-curated-1",
                user_message="Research auth flow",
                surface_hints={"surfaces": ["research"], "recommended_delegate_intents": ["delegate_mcp_probe"]},
                curator_summary="- auth.py relevante",
                lead_summary="P0 investigar auth",
                source_task_ids=["CHAT-curated-1::lead_intake"],
            )

            block = api_main._build_curated_context_block(
                runtime_dir=runtime_dir,
                workspace=workspace,
                continuation_of="CHAT-curated-1",
            )

            self.assertIn("Contexto historico del proyecto (no confirmado; revalidar antes de usar):", block)
            self.assertIn("Contexto curado de CHAT-curated-1 (actual y operativo):", block)
            self.assertIn("Contexto historico de CHAT-curated-1 (no confirmado; revalidar antes de usar):", block)

    def test_build_curated_context_block_filters_explicit_foreign_project_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = _runtime_dir_for(workspace)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            store = ContextCuratorStore(runtime_dir)
            project_key = str(workspace.resolve())
            project_ctx = store.load_project_context(project_key)
            project_ctx["historical_context"] = [
                {"text": "Proyecto: Book-TTS-Cloneable | pipeline multi-libro", "confidence": 0.4},
                {"text": f"Proyecto: {workspace.resolve().name} | parser_toc pendiente", "confidence": 0.5},
            ]
            store._write_project_context(project_key, project_ctx)

            block = api_main._build_curated_context_block(
                runtime_dir=runtime_dir,
                workspace=workspace,
                continuation_of="",
            )

            self.assertNotIn("Book-TTS-Cloneable", block)
            self.assertIn("parser_toc pendiente", block)

    def test_resolve_delegate_plan_maps_browser_intent_to_specialist(self) -> None:
        request = api_main._extract_delegate_request(
            '[DELEGATE_BROWSER_REPRO: "reproduce el bug"]'
        )
        assert request is not None

        plan = api_main._resolve_delegate_plan(request)

        self.assertEqual(plan["role"], Role.QA)
        self.assertEqual(plan["specialist"], "browser_operator")
        self.assertEqual(plan["phase_prefix"], "delegate_browser_repro")
        self.assertIn("browser_test", plan["required_capabilities"])

    def test_resolve_delegate_round_budget_applies_wait_policy_caps(self) -> None:
        all_request = api_main._extract_delegate_request(
            '[DELEGATE_REPO_SCAN: "mapea el repo"]\n[WAIT_POLICY: all]\n[DELEGATE_BUDGET: 9]'
        )
        best_effort_request = api_main._extract_delegate_request(
            '[DELEGATE_REPO_SCAN: "mapea el repo"]\n[WAIT_POLICY: best_effort]\n[DELEGATE_BUDGET: 5]'
        )
        quorum_request = api_main._extract_delegate_request(
            '[DELEGATE_REPO_SCAN: "mapea el repo"]\n[WAIT_POLICY: quorum]\n[DELEGATE_BUDGET: 5]'
        )
        assert all_request is not None
        assert best_effort_request is not None
        assert quorum_request is not None

        self.assertEqual(api_main._resolve_delegate_round_budget(all_request), 6)
        self.assertEqual(
            api_main._resolve_delegate_round_budget(best_effort_request), 2
        )
        self.assertEqual(api_main._resolve_delegate_round_budget(quorum_request), 4)

    def test_synthesize_default_phase_evidence_plan_for_standard_build(self) -> None:
        plan = api_main._synthesize_default_phase_evidence_plan(
            [
                PhaseSpec("build", "ENGINEER", "Implementa formulario React", []),
                PhaseSpec("review", "REVIEWER", "Revisa cambios", ["build"]),
                PhaseSpec("qa", "QA", "Valida flujo final", ["review"]),
            ],
            message="Implement React login form with browser validation",
            run_mode="standard",
        )

        self.assertIn("build", plan)
        self.assertIn("delegate_test_run", plan["build"]["delegate_intents"])
        self.assertIn("delegate_browser_repro", plan["build"]["delegate_intents"])
        self.assertEqual(plan["build"]["wait_policy"], "quorum")
        self.assertEqual(plan["review"]["delegate_intents"], ["delegate_lsp_impact"])
        self.assertIn("delegate_test_run", plan["qa"]["delegate_intents"])

    def test_role_required_capabilities_are_richer_and_role_aligned(self) -> None:
        self.assertEqual(
            api_main._role_required_capabilities("RESEARCHER"),
            ["analysis", "repo_read", "reasoning"],
        )
        self.assertEqual(
            api_main._role_required_capabilities("ENGINEER"),
            ["coding", "repo_read"],
        )
        self.assertEqual(
            api_main._role_required_capabilities("REVIEWER"),
            ["review", "repo_read", "reasoning"],
        )
        self.assertEqual(
            api_main._role_required_capabilities("QA"),
            ["analysis", "test_execute", "build_execute"],
        )

    def test_synthesize_default_phase_evidence_plan_adds_security_probe_for_sensitive_work(self) -> None:
        plan = api_main._synthesize_default_phase_evidence_plan(
            [
                PhaseSpec("build", "ENGINEER", "Implementa auth segura", []),
                PhaseSpec("review", "REVIEWER", "Audita seguridad", ["build"]),
                PhaseSpec("qa", "QA", "Valida hardening", ["review"]),
            ],
            message="Implement secure authentication and run a semgrep security audit",
            run_mode="standard",
        )

        self.assertIn("delegate_mcp_probe", plan["build"]["delegate_intents"])
        self.assertIn("delegate_mcp_probe", plan["review"]["delegate_intents"])
        self.assertIn("delegate_mcp_probe", plan["qa"]["delegate_intents"])

    def test_synthesize_default_phase_evidence_plan_adds_research_probe_for_discovery(self) -> None:
        plan = api_main._synthesize_default_phase_evidence_plan(
            [
                PhaseSpec("discovery", "RESEARCHER", "Investiga la integracion", []),
                PhaseSpec("build", "ENGINEER", "Implementa la integracion", ["discovery"]),
                PhaseSpec("review", "REVIEWER", "Revisa el cambio", ["build"]),
            ],
            message="Research the API documentation and best practices before implementing the integration",
            run_mode="standard",
        )

        self.assertIn("delegate_repo_scan", plan["discovery"]["delegate_intents"])
        self.assertIn("delegate_mcp_probe", plan["discovery"]["delegate_intents"])
        self.assertIn("delegate_mcp_probe", plan["review"]["delegate_intents"])

    def test_estimate_delegate_batch_economics_returns_positive_summary(self) -> None:
        economics = api_main._estimate_delegate_batch_economics(
            [
                {"specialist": "browser_operator", "state": "completed"},
                {"specialist": "repo_scout", "state": "completed"},
                {"specialist": "test_runner", "state": "failed"},
            ]
        )

        self.assertEqual(economics["economics_version"], "delegate_economics_v1")
        self.assertTrue(bool(economics["estimated"]))
        self.assertGreater(int(economics["estimated_lead_tokens_avoided"]), 0)
        self.assertGreater(int(economics["estimated_operator_tokens_used"]), 0)
        self.assertIn("browser_operator", economics["specialist_breakdown"])
        self.assertIn(
            "estimated_net_tokens_saved",
            economics["specialist_breakdown"]["browser_operator"],
        )

    def test_resolve_delegate_assignments_rewires_replaceable_mcp_in_browser_quorum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_dir = workspace / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "playwright_mcp",
                                "category": "mcp",
                                "capabilities": ["browser_testing", "e2e", "web_automation"],
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["playwright_qa_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "skills.library.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "playwright_qa_skill",
                                "description": "Playwright skill",
                                "capabilities": ["browser_testing", "e2e", "web_automation"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            request = api_main._extract_delegate_request(
                '[DELEGATE_BROWSER_REPRO: "reproduce el bug"]\n[WAIT_POLICY: quorum]'
            )
            assert request is not None

            assignments = api_main._resolve_delegate_assignments(request, workspace=workspace)

            self.assertGreaterEqual(len(assignments), 3)
            self.assertEqual(assignments[0]["specialist"], "browser_operator")
            self.assertIn("skill_worker", [row["specialist"] for row in assignments])
            self.assertNotIn("mcp_operator", [row["specialist"] for row in assignments])
            rewired = [row for row in assignments if row["specialist"] == "skill_worker"][0]
            self.assertTrue(bool(rewired.get("tool_rewiring_active")))
            self.assertEqual(str(rewired.get("tool_rewiring_preferred_specialist", "")), "skill_worker")
            self.assertIn("playwright_qa_skill", list(rewired.get("skill_targets", []) or []))
            self.assertIn("test_runner", [row["specialist"] for row in assignments])

    def test_resolve_delegate_assignments_rewires_mcp_probe_to_skill_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_dir = workspace / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "semgrep_mcp",
                                "category": "mcp",
                                "capabilities": ["security_scan", "sast", "code_quality"],
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["semgrep_security_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "skills.library.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "semgrep_security_skill",
                                "description": "Semgrep skill",
                                "capabilities": ["security_scan", "sast", "code_quality"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            request = api_main._extract_delegate_request(
                '[DELEGATE_MCP_PROBE: "usa semgrep para inspeccionar seguridad"]\n[WAIT_POLICY: best_effort]'
            )
            assert request is not None

            assignments = api_main._resolve_delegate_assignments(request, workspace=workspace)

            self.assertEqual([row["specialist"] for row in assignments], ["skill_worker"])
            self.assertIn("semgrep_security_skill", list(assignments[0].get("skill_targets", []) or []))

    def test_aggregate_delegate_results_marks_quorum_met_with_majority(self) -> None:
        summary, quorum_met = api_main._aggregate_delegate_results(
            [
                {
                    "phase": "delegate_browser_repro_0_browser_operator",
                    "specialist": "browser_operator",
                    "state": "completed",
                    "report_contract_version": "operator_report_v1",
                    "result": "Pasos reproducidos correctamente.",
                },
                {
                    "phase": "delegate_browser_repro_0_repo_scout",
                    "specialist": "repo_scout",
                    "state": "completed",
                    "report_contract_version": "operator_report_v1",
                    "result": "Archivos y rutas relevantes localizados.",
                },
                {
                    "phase": "delegate_browser_repro_0_test_runner",
                    "specialist": "test_runner",
                    "state": "failed",
                    "report_contract_version": "operator_report_v1",
                    "result": "",
                },
            ],
            wait_policy="quorum",
        )

        self.assertTrue(quorum_met)
        self.assertIn("quorum_target=2", summary)
        self.assertIn("quorum_met=yes", summary)
        self.assertIn("contract=operator_report_v1", summary)

    def test_delegate_specialist_targets_attach_playwright_and_lsp_hints(self) -> None:
        browser_skill_targets, browser_lsp_targets = api_main._delegate_specialist_targets(
            intent="delegate_browser_repro",
            specialist="browser_operator",
        )
        lsp_skill_targets, lsp_lsp_targets = api_main._delegate_specialist_targets(
            intent="delegate_lsp_impact",
            specialist="lsp_navigator",
        )

        self.assertEqual(browser_skill_targets, ["playwright_qa_skill"])
        self.assertEqual(browser_lsp_targets, [])
        self.assertEqual(lsp_skill_targets, [])
        self.assertEqual(lsp_lsp_targets, ["symbols", "references", "impact"])

    def test_delegate_report_contract_for_browser_and_mcp_is_compact(self) -> None:
        browser_contract = api_main._delegate_report_contract(
            intent="delegate_browser_repro",
            specialist="browser_operator",
        )
        mcp_contract = api_main._delegate_report_contract(
            intent="delegate_mcp_probe",
            specialist="mcp_operator",
        )

        self.assertIn("steps_reproduced", browser_contract)
        self.assertIn("no pegues transcripts crudos", browser_contract)
        self.assertIn("eres soporte", browser_contract)
        self.assertIn("no declares la fase principal blocked/rejected", browser_contract)
        self.assertIn("recommendation", mcp_contract)
        self.assertIn("MCP", mcp_contract)
        self.assertIn("observed_failure", mcp_contract)

    def test_extract_delegate_request_from_mid_run_outputs(self) -> None:
        request = api_main._extract_delegate_request_from_outputs(
            {
                "lead_report_build": (
                    '[DELEGATE_TEST_RUN: "ejecuta humo"]\n'
                    "[WAIT_POLICY: best_effort]\n"
                    "[DELEGATE_BUDGET: 2]"
                )
            }
        )

        self.assertIsNotNone(request)
        assert request is not None
        source_phase, delegate_request = request
        self.assertEqual(source_phase, "lead_report_build")
        self.assertEqual(delegate_request.intent, "delegate_test_run")
        self.assertEqual(delegate_request.wait_policy, "best_effort")

    def test_extract_delegate_request_from_failure_checkpoint_outputs(self) -> None:
        request = api_main._extract_delegate_request_from_outputs(
            {
                "lead_failure_build": (
                    '[DELEGATE_REPO_SCAN: "investiga el fallo"]\n'
                    "[WAIT_POLICY: best_effort]\n"
                    "[DELEGATE_BUDGET: 2]"
                )
            }
        )

        self.assertIsNotNone(request)
        assert request is not None
        source_phase, delegate_request = request
        self.assertEqual(source_phase, "lead_failure_build")
        self.assertEqual(delegate_request.intent, "delegate_repo_scan")
        self.assertEqual(delegate_request.wait_policy, "best_effort")

    def test_extract_delegate_request_from_lead_close_outputs(self) -> None:
        request = api_main._extract_delegate_request_from_outputs(
            {
                "lead_close": (
                    '[DELEGATE_BROWSER_REPRO: "valida el flujo final"]\n'
                    "[WAIT_POLICY: quorum]\n"
                    "[DELEGATE_BUDGET: 3]"
                )
            }
        )

        self.assertIsNotNone(request)
        assert request is not None
        source_phase, delegate_request = request
        self.assertEqual(source_phase, "lead_close")
        self.assertEqual(delegate_request.intent, "delegate_browser_repro")
        self.assertEqual(delegate_request.wait_policy, "quorum")

    def test_supporting_control_phase_accepts_delegate_prefixes(self) -> None:
        self.assertTrue(api_main._is_supporting_control_phase("lead_intake"))
        self.assertTrue(api_main._is_supporting_control_phase("scout_context_curator"))
        self.assertTrue(
            api_main._is_supporting_control_phase(
                "delegate_browser_repro_0_browser_operator"
            )
        )
        self.assertFalse(api_main._is_supporting_control_phase("build"))

    def test_strip_selected_directives_removes_delegate_controls_only(self) -> None:
        cleaned = api_main._strip_selected_directives(
            (
                '[DELEGATE_BROWSER_REPRO: "reproduce"]\n'
                "[WAIT_POLICY: quorum]\n"
                "[DELEGATE_BUDGET: 4]\n"
                '[REPLAN]\n[WORKFLOW_PLAN]\nphase_id: build\nrole: ENGINEER\nobjective: x\n[/WORKFLOW_PLAN]'
            ),
            [
                "DELEGATE_BROWSER_REPRO",
                "WAIT_POLICY",
                "DELEGATE_BUDGET",
            ],
        )

        self.assertNotIn("DELEGATE_BROWSER_REPRO", cleaned)
        self.assertNotIn("WAIT_POLICY", cleaned)
        self.assertNotIn("DELEGATE_BUDGET", cleaned)
        self.assertIn("[REPLAN]", cleaned)

    def test_extract_evidence_plan_wrapper_parses_structured_block(self) -> None:
        plan = api_main._extract_evidence_plan(
            "[EVIDENCE_PLAN]\n"
            "phase_id: build\n"
            "delegate: delegate_test_run\n"
            "wait_policy: quorum\n"
            "delegate_budget: 4\n"
            "[/EVIDENCE_PLAN]"
        )

        self.assertEqual(plan["build"]["delegate_intents"], ["delegate_test_run"])
        self.assertEqual(plan["build"]["wait_policy"], "quorum")
        self.assertEqual(plan["build"]["delegate_budget"], 4)

    def test_structured_evidence_specs_expand_to_specialist_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            config_dir = workspace / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "tool_sources.catalog.json").write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "playwright_mcp",
                                "category": "mcp",
                                "capabilities": ["browser_testing", "e2e", "web_automation"],
                                "fallback_strategy": "prefer_skill_or_cli",
                                "replacement_candidates": ["playwright_qa_skill"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "skills.library.json").write_text(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "playwright_qa_skill",
                                "description": "Playwright skill",
                                "capabilities": ["browser_testing", "e2e", "web_automation"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            specs = api_main._structured_evidence_specs_for_phase(
                "build",
                {
                    "build": {
                        "delegate_intents": ["delegate_browser_repro"],
                        "wait_policy": "quorum",
                        "delegate_budget": 4,
                    }
                },
                workspace=workspace,
            )

            self.assertGreaterEqual(len(specs), 3)
            self.assertTrue(all(spec["source_phase"] == "build" for spec in specs))
            self.assertIn("browser_operator", [spec["specialist"] for spec in specs])
            self.assertIn("skill_worker", [spec["specialist"] for spec in specs])
            browser_specs = [spec for spec in specs if spec["specialist"] == "browser_operator"]
            self.assertTrue(browser_specs)
            self.assertEqual(browser_specs[0]["skill_targets"], ["playwright_qa_skill"])
            self.assertIn("steps_reproduced", str(browser_specs[0]["report_contract"]))
            rewired_specs = [spec for spec in specs if spec["specialist"] == "skill_worker"]
            self.assertTrue(rewired_specs)
            self.assertIn("playwright_qa_skill", list(rewired_specs[0].get("skill_targets", []) or []))

    def test_structured_evidence_specs_mark_review_and_qa_as_pre_phase(self) -> None:
        build_specs = api_main._structured_evidence_specs_for_phase(
            "build",
            {
                "build": {
                    "delegate_intents": ["delegate_test_run"],
                    "wait_policy": "quorum",
                    "delegate_budget": 3,
                }
            },
        )
        review_specs = api_main._structured_evidence_specs_for_phase(
            "review",
            {
                "review": {
                    "delegate_intents": ["delegate_lsp_impact"],
                    "wait_policy": "quorum",
                    "delegate_budget": 3,
                }
            },
        )
        qa_specs = api_main._structured_evidence_specs_for_phase(
            "qa",
            {
                "qa": {
                    "delegate_intents": ["delegate_test_run"],
                    "wait_policy": "quorum",
                    "delegate_budget": 3,
                }
            },
        )
        named_qa_specs = api_main._structured_evidence_specs_for_phase(
            "qa_validate_toc",
            {
                "qa_validate_toc": {
                    "delegate_intents": ["delegate_test_run"],
                    "wait_policy": "quorum",
                    "delegate_budget": 3,
                }
            },
        )
        validate_specs = api_main._structured_evidence_specs_for_phase(
            "validate_cli_output",
            {
                "validate_cli_output": {
                    "delegate_intents": ["delegate_test_run"],
                    "wait_policy": "quorum",
                    "delegate_budget": 3,
                }
            },
        )

        self.assertTrue(build_specs)
        self.assertTrue(review_specs)
        self.assertTrue(qa_specs)
        self.assertTrue(named_qa_specs)
        self.assertTrue(validate_specs)
        self.assertTrue(
            all(spec.get("evidence_position") == "post_phase" for spec in build_specs)
        )
        self.assertTrue(
            all(spec.get("evidence_position") == "pre_phase" for spec in review_specs)
        )
        self.assertTrue(
            all(spec.get("evidence_position") == "pre_phase" for spec in qa_specs)
        )
        self.assertTrue(
            all(spec.get("evidence_position") == "pre_phase" for spec in named_qa_specs)
        )
        self.assertTrue(
            all(spec.get("evidence_position") == "pre_phase" for spec in validate_specs)
        )


class AbortPhasesIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if "Fase origen: build" in joined:
                return AdapterResponse(
                    success=True,
                    content='[ABORT_PHASES: "El build ya es suficiente; cerrar en advisory."]',
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nCorrida convertida a advisory tras build.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SkipMidRunIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if "Fase origen: build" in joined:
                return AdapterResponse(
                    success=True,
                    content='[SKIP: "review qa"]\nNo hace falta ejecutar review ni qa.',
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe omitieron review y qa por decision del Lead.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class RetryRouteIntegrationAdapter(ModelAdapter):
    def __init__(self, name: str, shared_state: dict[str, object]) -> None:
        model_name = "gpt-pro" if "primary" in name else "gpt-4o"
        super().__init__(
            name=name,
            provider="openai",
            model=model_name,
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self.shared_state = shared_state

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if (
                "Fase origen: build" in joined
                and not bool(self.shared_state.get("retry_emitted"))
            ):
                self.shared_state["retry_emitted"] = True
                return AdapterResponse(
                    success=True,
                    content='[RETRY_ROUTE: "build"]\nPrueba otra ruta/modelo para build.',
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe reintentó build con otra ruta.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "implementar slice bajo deliberacion" in joined:
            label = "secondary" if "secondary" in self.name else "primary"
            return AdapterResponse(
                success=True,
                content=f"Build completada via {label} route.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SetBudgetIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if "Fase origen: build" in joined:
                return AdapterResponse(
                    success=True,
                    content="[SET_BUDGET: 3]\nRecorta budget tras validar el build.",
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nBudget ajustado mid-run.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class AdvisoryModeIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            if "Fase origen: build" in joined:
                return AdapterResponse(
                    success=True,
                    content='[ADVISORY_MODE: "No hay evidencia live suficiente; cerrar como advisory."]',
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice bajo deliberacion\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan deliberativo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nCierre en advisory mode por decision del Lead.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SkipPhaseIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar al cierre.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice con evidencia\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    '[SKIP_PHASE: "build" reason="gate rechazado repetidamente y output placeholder"]\n'
                    "Lead summary:\nAcepto cerrar sin rescatar build."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class DegradeIntegrationAdapter(ModelAdapter):
    def __init__(self, scope: str) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self.scope = scope

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar al cierre.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice con evidencia\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    f'[DEGRADE: scope="{self.scope}" reason="build no recuperable; cierro con diagnostico visible"]\n'
                    "Lead summary:\nCierre degradado documentado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class PauseForUserIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self.seen_resume_answer = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, revisa este informe delegado antes del cierre." in joined:
            return AdapterResponse(
                success=True,
                content="Checkpoint revisado; continuar al cierre.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if "Lead intake and planning" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: intentar slice inicial\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar implementacion\n"
                    "depends_on: [build]\n"
                    "phase_id: qa\n"
                    "role: QA\n"
                    "objective: validar implementacion\n"
                    "depends_on: [review]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        if (
            "Lead synthesis and response" in joined
            and "Como Team Lead senior, sintetiza el trabajo del equipo y responde al usuario." in joined
        ):
            if "[Respuesta del usuario a tu pregunta previa '" in joined:
                self.seen_resume_answer = True
                return AdapterResponse(
                    success=True,
                    content=(
                        "Lead summary:\n"
                        "Reanudé el cierre con la respuesta del usuario y la dejé reflejada en el diagnóstico final."
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
            return AdapterResponse(
                success=True,
                content=(
                    '[PAUSE_FOR_USER: "El gate de build quedó bloqueado. ¿Quieres reintentar con otra ruta o ajustar el objetivo?"]\n'
                    "Lead summary:\nNecesito una decisión del usuario antes de cerrar."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class AgentsMdIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self.intake_prompt = ""

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Lead intake and planning" in joined:
            self.intake_prompt = joined
            return AdapterResponse(
                success=True,
                content="[DIRECT_ANSWER]\nLeidas instrucciones del proyecto.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=12,
            )
        return AdapterResponse(
            success=True,
            content="Respuesta generica.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class MissingApiKeyCapabilitiesAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_api",
            provider="openai",
            model="gpt-4o",
            channel=ChannelType.API,
            capabilities={"coding", "reasoning", "analysis"},
        )

    def available(self) -> bool:
        return False

    def invoke(self, prompt, messages=None, tools=None):
        return AdapterResponse(
            success=False,
            content="API key missing.",
            error="missing_api_key",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
        )


class StaticMcpManager:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    def server_status(self) -> list[dict]:
        return list(self._rows)


class LeadMemoryIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self.intake_prompt = ""

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Lead intake and planning" in joined:
            self.intake_prompt = joined
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar una mejora minima verificable\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan listo."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nEntrega completada con una fase principal.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class CuratorFailureButLeadContinuesAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Scout: estado del proyecto" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "```json\n"
                    "{\n"
                    '  "summary": "Workspace con src/sample_cli/cli.py y tests/test_cli.py.",\n'
                    '  "evidence": ["workspace snapshot autoritativo: src/sample_cli/cli.py", "workspace snapshot autoritativo: tests/test_cli.py"]\n'
                    "}\n```"
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        if "Scout: historial de sesiones" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "```json\n"
                    "{\n"
                    '  "summary": "Sin carryover relevante para esta prueba.",\n'
                    '  "evidence": ["No hay sesiones previas con valor autoritativo."]\n'
                    "}\n```"
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "Scout: context curator" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "Resumen: el proyecto ya contiene src/invented/report.py y tests/test_report.py "
                    "validados en el estado actual."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "Lead intake and planning" in joined or (
            "Eres Team Lead senior." in joined and "Solicitud original:" in joined
        ):
            return AdapterResponse(
                success=True,
                content="[DIRECT_ANSWER]\nEl Lead puede continuar con scouts basicos aunque falle el curator.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=18,
            )
        return AdapterResponse(
            success=True,
            content="Resultado generico.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class QuorumPlanningIntegrationAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        record: list[dict[str, str]],
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        self._record = record

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Modo quorum: consolidacion final del Lead." in joined:
            self._record.append({"adapter": self.name, "stage": "final"})
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: architecture_review]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: consolidar contexto confirmado\n"
                    "phase_id: architecture_options\n"
                    "role: REVIEWER\n"
                    "objective: comparar opciones con tradeoffs\n"
                    "depends_on: [discovery]\n"
                    "phase_id: adr_document\n"
                    "role: REVIEWER\n"
                    "objective: documentar la decision consolidada\n"
                    "depends_on: [architecture_options]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan final consolidado con quorum."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=60,
            )
        if "Modo quorum: consultor independiente." in joined:
            self._record.append({"adapter": self.name, "stage": "consultant"})
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: architecture_review]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: ampliar restricciones y riesgos\n"
                    "phase_id: architecture_options\n"
                    "role: REVIEWER\n"
                    "objective: contrastar dos alternativas de arquitectura\n"
                    "depends_on: [discovery]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Recomiendo reforzar discovery antes del ADR."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            self._record.append({"adapter": self.name, "stage": "lead"})
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: architecture_review]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: recopilar contexto tecnico base\n"
                    "phase_id: architecture_options\n"
                    "role: REVIEWER\n"
                    "objective: revisar arquitectura objetivo\n"
                    "depends_on: [discovery]\n"
                    "phase_id: adr_document\n"
                    "role: REVIEWER\n"
                    "objective: redactar ADR inicial\n"
                    "depends_on: [architecture_options]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan inicial del Lead."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=50,
            )
        return AdapterResponse(
            success=True,
            content="Scout/probe output.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class LeadFailureDelegateIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "repo_read"},
        )
        self.failure_delegate_emitted = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora." in joined:
            return AdapterResponse(
                success=True,
                content="Autorizado para continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if (
            "Eres Team Lead." in joined
            and "Como Team Lead, interviene tras un fallo de fase" in joined
        ):
            if not self.failure_delegate_emitted:
                self.failure_delegate_emitted = True
                return AdapterResponse(
                    success=True,
                    content=(
                        '[DELEGATE_REPO_SCAN: "inspecciona por que falla build y resume los hechos"]\n'
                        "[WAIT_POLICY: best_effort]\n"
                        "[DELEGATE_BUDGET: 2]"
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=30,
                )
            return AdapterResponse(
                success=True,
                content='[ABORT_PHASES: "Cerrar tras investigar el fallo inicial."]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: forzar fallo inicial y luego investigar\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe delegó investigación tras el fallo y se cerró la corrida.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "forzar fallo inicial y luego investigar" in joined:
            return AdapterResponse(
                success=False,
                content="",
                latency_ms=1,
                input_tokens=10,
                output_tokens=0,
                error="forced_build_failure",
            )
        return AdapterResponse(
            success=True,
            content="Informe delegado con hechos compactos del repo.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class LeadIntakeRepeatedDelegateIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "repo_read"},
        )
        self.lead_invocations = 0

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora." in joined:
            return AdapterResponse(
                success=True,
                content="Autorizado para continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            self.lead_invocations += 1
            if self.lead_invocations == 1:
                return AdapterResponse(
                    success=True,
                    content=(
                        '[DELEGATE_REPO_SCAN: "mapea el repo y resume hechos confirmados"]\n'
                        "[WAIT_POLICY: best_effort]\n"
                        "[DELEGATE_BUDGET: 2]"
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=25,
                )
            if self.lead_invocations == 2:
                return AdapterResponse(
                    success=True,
                    content=(
                        '[DELEGATE_REPO_SCAN: "mapea el repo y resume hechos confirmados"]\n'
                        "[WAIT_POLICY: best_effort]\n"
                        "[DELEGATE_BUDGET: 2]\n"
                        "[WORKFLOW_PLAN]\n"
                        "phase_id: build\n"
                        "role: ENGINEER\n"
                        "objective: implementar el modulo base confirmado\n"
                        "[/WORKFLOW_PLAN]\n"
                        "Plan listo."
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=60,
                )
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar el modulo base confirmado\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan listo."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        if "implementar el modulo base confirmado" in joined:
            return AdapterResponse(
                success=True,
                content="Fase completada con evidencia compacta.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary: se evitó repetir la misma delegación y se continuó con el plan.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Informe delegado con hechos compactos del repo.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class LeadCloseDelegateIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "browser_test"},
        )
        self.close_delegate_emitted = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora." in joined:
            return AdapterResponse(
                success=True,
                content="Autorizado para continuar.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=10,
            )
        if (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar slice con evidencia textual suficiente\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan corto preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=30,
            )
        if "Eres Team Lead." in joined and "Lead synthesis and response" in joined:
            if not self.close_delegate_emitted:
                self.close_delegate_emitted = True
                return AdapterResponse(
                    success=True,
                    content=(
                        '[DELEGATE_BROWSER_REPRO: "reproduce el flujo final y resume evidencia visual"]\n'
                        "[WAIT_POLICY: best_effort]\n"
                        "[DELEGATE_BUDGET: 2]"
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=30,
                )
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe cerró tras delegar verificación final desde lead_close.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class APITeamChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self._local_temp_root = Path.cwd() / ".tmp_api_team_chat_tests"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

        class _WorkspaceTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | os.PathLike[str] | None = None,
                ignore_cleanup_errors: bool = False,
            ) -> None:
                inner_self._ignore_cleanup_errors = ignore_cleanup_errors
                inner_self._root = Path(dir) if dir else self._local_temp_root
                inner_self._prefix = prefix or "tmp"
                inner_self._suffix = suffix or ""
                inner_self.name = ""

            def __enter__(inner_self) -> str:
                candidate = (
                    inner_self._root
                    / f"{inner_self._prefix}{uuid4().hex}{inner_self._suffix}"
                )
                candidate.mkdir(parents=True, exist_ok=False)
                inner_self.name = str(candidate)
                return inner_self.name

            def __exit__(inner_self, exc_type, exc, tb) -> bool:
                shutil.rmtree(inner_self.name, ignore_errors=True)
                return False

            def cleanup(inner_self) -> None:
                shutil.rmtree(inner_self.name, ignore_errors=True)

        tempfile.TemporaryDirectory = _WorkspaceTemporaryDirectory

    def tearDown(self) -> None:
        tempfile.tempdir = self._previous_tempdir
        tempfile.TemporaryDirectory = self._previous_temporary_directory

    def test_continuation_message_accepts_spanish_continuad(self) -> None:
        self.assertTrue(api_main._is_continuation_message("continuad"))

    def test_extract_chat_root_ignores_placeholder_chat_ids(self) -> None:
        self.assertEqual(
            api_main._extract_chat_root_from_message(
                "Indica `Continue from CHAT-XXXXXXXX` o usa el boton Continue."
            ),
            "",
        )
        self.assertEqual(
            api_main._extract_chat_root_from_message("Continue from CHAT-A1B2C3D4"),
            "CHAT-A1B2C3D4",
        )

    def test_implicit_continuation_defaults_to_latest_actionable_project_run(self) -> None:
        source = api_main._default_implicit_continuation_source(
            [
                {
                    "root_id": "CHAT-11111111",
                    "run_status": "waiting_user",
                    "continuation_requested": True,
                    "continuation_effective": False,
                    "continuation_block_reason": "ambiguous_target_required",
                },
                {
                    "root_id": "CHAT-A1B2C3D4",
                    "run_status": "rejected",
                    "continuation_requested": False,
                    "continuation_effective": False,
                    "continuation_block_reason": "",
                },
            ]
        )
        self.assertEqual(str(source.get("root_id", "")), "CHAT-A1B2C3D4")

    def test_chat_progress_terminal_verdict_overrides_stale_waiting_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            runtime_dir = workspace / ".aiteam"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            task_root = "CHAT-A1B2C3D4"
            SqliteStore(runtime_dir / "aiteam.db").save_workflow_state(
                {
                    task_root: {
                        "run_status": "waiting_user",
                        "phase_states": {"lead_intake": "waiting_user"},
                        "run_verdict": {
                            "state": "rejected",
                            "reason_codes": ["review:phase_failed"],
                        },
                    }
                }
            )
            SqliteStore(runtime_dir / "aiteam.db").save_all_tasks(
                [
                    {
                        "task_id": f"{task_root}::lead_intake",
                        "state": "waiting_user",
                        "metadata": {"phase": "lead_intake"},
                    }
                ]
            )
            (runtime_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-04-12T10:00:00+00:00",
                        "event_type": "chat_plan_created",
                        "payload": {
                            "task_id": task_root,
                            "chat_mode": "sprint5",
                            "round_budget": 5,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            progress = api_main._build_chat_progress(runtime_dir, task_root)

            self.assertEqual(progress.state, "rejected")
            self.assertEqual(progress.workflow_run_status, "waiting_user")

    def test_presentable_decision_text_hides_placeholder_payloads(self) -> None:
        self.assertEqual(
            api_main._presentable_decision_text(
                "[SIMULADO | openai:gpt-4.1] Respuesta mock"
            ),
            "",
        )

    def test_compact_delegated_result_collapses_placeholder_output(self) -> None:
        self.assertEqual(
            api_main._compact_delegated_result(
                "[SIMULADO | openai:gpt-4.1] Respuesta mock para build.",
                state="completed",
            ),
            "placeholder/simulado",
        )

    def test_limit_chat_response_preserves_user_summary_section(self) -> None:
        response = "\n".join(
            [
                "Lead summary:",
                "Delegation results:",
                *[f"- item {idx}: {'x' * 120}" for idx in range(40)],
                "",
                "Lead message for user:",
                "Resumen del Team Lead para ti:",
                "Linea importante " * 80,
            ]
        )

        limited = api_main._limit_chat_response(response, limit=2400)

        self.assertLessEqual(len(limited), 2400)
        self.assertIn("Lead message for user:", limited)
        self.assertIn("Resumen del Team Lead para ti:", limited)

    def test_decision_fallback_summarizes_blocked_run_without_reusing_intake_text(
        self,
    ) -> None:
        decision = api_main._resolve_chat_decision_text(
            lead_response="",
            intake_response=(
                "Objetivo inmediato\nEntregar un prototipo funcional de un juego original."
            ),
            phase_states={
                "lead_intake": "completed",
                "plan_research": "failed",
                "plan_engineering": "failed",
                "plan_risks": "failed",
                "build": "blocked",
                "review": "pending",
                "qa": "pending",
                "lead_close": "pending",
            },
            workflow_phase_keys=[
                "lead_intake",
                "plan_research",
                "plan_engineering",
                "plan_risks",
                "build",
                "review",
                "qa",
                "lead_close",
            ],
            phase_results={
                "lead_intake": "Objetivo inmediato\nEntregar un prototipo funcional.",
                "plan_research": "All adapter attempts failed",
                "plan_engineering": "All adapter attempts failed",
                "plan_risks": "All adapter attempts failed",
                "build": "",
                "review": "",
                "qa": "",
                "lead_close": "",
            },
        )

        self.assertIn("Corrida sin cierre final.", decision)
        self.assertIn("completado=lead_intake", decision)
        self.assertIn("plan_research (All adapter attempts failed)", decision)
        self.assertIn("bloqueado=build", decision)
        self.assertIn("pendiente=review, qa, lead_close", decision)
        self.assertNotIn("Objetivo inmediato", decision)

    def test_format_pending_phase_summary_compacts_downstream_after_planning_failure(self) -> None:
        summary = api_main._format_pending_phase_summary(
            ["build", "review", "qa", "lead_close"],
            ["plan_engineering"],
        )
        self.assertEqual(
            summary,
            "downstream bloqueado por planning: build, review, qa, lead_close",
        )

    def test_replan_window_is_open_only_when_dynamic_phases_not_started(self) -> None:
        self.assertTrue(
            api_main._replan_window_is_open(
                {
                    "lead_intake": "completed",
                    "build": "ready",
                    "review": "pending",
                    "lead_close": "pending",
                },
                ["lead_intake", "build", "review", "lead_close"],
            )
        )
        self.assertFalse(
            api_main._replan_window_is_open(
                {
                    "lead_intake": "completed",
                    "build": "completed",
                    "review": "pending",
                    "lead_close": "pending",
                },
                ["lead_intake", "build", "review", "lead_close"],
            )
        )

    def test_extract_replan_phases_from_outputs_requires_directive_and_plan(self) -> None:
        replan = api_main._extract_replan_phases_from_outputs(
            {
                "lead_preflight_build": (
                    "[REPLAN]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: re-evaluar alcance\n"
                    "[/WORKFLOW_PLAN]"
                )
            }
        )
        self.assertIsNotNone(replan)
        assert replan is not None
        phase_name, phases = replan
        self.assertEqual(phase_name, "lead_preflight_build")
        self.assertEqual([item.phase_id for item in phases], ["discovery"])

    def test_extract_force_gate_request_from_outputs(self) -> None:
        force_gate = api_main._extract_force_gate_request_from_outputs(
            {
                "lead_report_review": '[FORCE_GATE: "build"]',
            }
        )
        self.assertEqual(force_gate, ("lead_report_review", "build"))

    def test_extract_abort_request_from_outputs(self) -> None:
        abort_request = api_main._extract_abort_request_from_outputs(
            {
                "lead_report_build": '[ABORT_PHASES: "Cerrar en advisory"]',
            }
        )
        self.assertEqual(abort_request, ("lead_report_build", "Cerrar en advisory"))

    def test_extract_skip_request_from_outputs(self) -> None:
        skip_request = api_main._extract_skip_request_from_outputs(
            {
                "lead_report_build": '[SKIP: "review qa"]',
            }
        )
        self.assertEqual(skip_request, ("lead_report_build", ["review", "qa"]))

    def test_extract_retry_route_request_from_outputs(self) -> None:
        retry_request = api_main._extract_retry_route_request_from_outputs(
            {
                "lead_report_build": '[RETRY_ROUTE: "build"]',
            }
        )
        self.assertEqual(retry_request, ("lead_report_build", "build"))

    def test_extract_budget_adjustments_from_outputs(self) -> None:
        adjustments = api_main._extract_budget_adjustments_from_outputs(
            {
                "lead_report_build": "[SET_BUDGET: 3]\n[EXTEND_BUDGET: +2]",
            }
        )
        self.assertEqual(len(adjustments), 1)
        phase_name, payload = adjustments[0]
        self.assertEqual(phase_name, "lead_report_build")
        self.assertEqual(payload.get("set_budget"), 3)
        self.assertEqual(payload.get("extend_budget"), 2)

    def test_phase_started_for_replan_detects_claimed_and_blocked_with_execution(self) -> None:
        claimed = WorkTask(
            task_id="CHAT::build",
            title="Build",
            description="",
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            state=TaskState.CLAIMED,
        )
        blocked_after_start = WorkTask(
            task_id="CHAT::review",
            title="Review",
            description="",
            role=Role.REVIEWER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            state=TaskState.BLOCKED,
            metadata={"execution_round": 1},
        )
        pending = WorkTask(
            task_id="CHAT::qa",
            title="QA",
            description="",
            role=Role.QA,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
            state=TaskState.PENDING,
        )

        self.assertTrue(api_main._phase_started_for_replan(claimed))
        self.assertTrue(api_main._phase_started_for_replan(blocked_after_start))
        self.assertFalse(api_main._phase_started_for_replan(pending))

    def test_merge_replanned_phases_preserves_started_and_replaces_pending_tail(self) -> None:
        current_phases = [
            PhaseSpec("build", "ENGINEER", "Implementar", []),
            PhaseSpec("review", "REVIEWER", "Revisar", ["build"]),
            PhaseSpec("qa", "QA", "Validar", ["review"]),
        ]
        tasks_by_phase = {
            "build": WorkTask(
                task_id="CHAT::build",
                title="Build",
                description="",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.COMPLETED,
            ),
            "review": WorkTask(
                task_id="CHAT::review",
                title="Review",
                description="",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.PENDING,
            ),
            "qa": WorkTask(
                task_id="CHAT::qa",
                title="QA",
                description="",
                role=Role.QA,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.READY,
            ),
        }
        replan_phases = [
            PhaseSpec("build", "ENGINEER", "Mantener build existente", []),
            PhaseSpec("review_options", "REVIEWER", "Replantear opciones", ["build"]),
            PhaseSpec("qa", "QA", "Nueva validacion", ["review_options"]),
        ]

        merged, preserved_ids, preserved_task_ids = api_main._merge_replanned_phases(
            current_phases,
            tasks_by_phase,
            replan_phases,
        )

        self.assertEqual(preserved_ids, ["build"])
        self.assertEqual(preserved_task_ids, ["CHAT::build"])
        self.assertEqual(
            [item.phase_id for item in merged],
            ["build", "review_options", "qa"],
        )

    def test_prune_phases_for_mid_run_lead_action_removes_pending_tail(self) -> None:
        current_phases = [
            PhaseSpec("build", "ENGINEER", "Implementar", []),
            PhaseSpec("review", "REVIEWER", "Revisar", ["build"]),
            PhaseSpec("qa", "QA", "Validar", ["review"]),
        ]
        tasks_by_phase = {
            "build": WorkTask(
                task_id="CHAT::build",
                title="Build",
                description="",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.COMPLETED,
            ),
            "review": WorkTask(
                task_id="CHAT::review",
                title="Review",
                description="",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.PENDING,
            ),
            "qa": WorkTask(
                task_id="CHAT::qa",
                title="QA",
                description="",
                role=Role.QA,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                state=TaskState.PENDING,
            ),
        }

        merged, removed_ids, preserved_started_ids, skipped_started = (
            api_main._prune_phases_for_mid_run_lead_action(
                current_phases,
                tasks_by_phase,
                target_phase_ids=["review"],
            )
        )

        self.assertEqual([item.phase_id for item in merged], ["build"])
        self.assertEqual(removed_ids, ["qa", "review"])
        self.assertEqual(preserved_started_ids, ["build"])
        self.assertEqual(skipped_started, [])

    def test_retry_route_removal_phase_ids_includes_target_and_downstream(self) -> None:
        current_phases = [
            PhaseSpec("discovery", "RESEARCHER", "Descubrir", []),
            PhaseSpec("build", "ENGINEER", "Implementar", ["discovery"]),
            PhaseSpec("review", "REVIEWER", "Revisar", ["build"]),
            PhaseSpec("qa", "QA", "Validar", ["review"]),
        ]
        removed = api_main._retry_route_removal_phase_ids(current_phases, "build")
        self.assertEqual(removed, ["build", "review", "qa"])

    def test_chat_is_led_by_team_lead_and_returns_delegation(self) -> None:
        temp_root = Path.cwd() / ".tmp_api_team_chat_tests"
        workspace = temp_root / f"case_{uuid4().hex}"
        previous_workspace = api_main.get_current_workspace()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
            api_main.set_current_workspace(workspace)
            client = TestClient(api_main.app)
            response = client.post(
                "/api/aiteam/chat",
                json={
                    "message": "Implement tests and refactor auth flow",
                    "role": "qa",
                    "complexity": "medium",
                    "criticality": "medium",
                },
            )
            self.assertEqual(response.status_code, 200)
            payload = _parse_sse_result(response)
            self.assertEqual(payload.get("role"), "team_lead")
            self.assertTrue(
                str(payload.get("lead_task_id", "")).endswith("::lead_intake")
            )
            self.assertGreaterEqual(len(payload.get("delegated_task_ids", [])), 4)
            phase_task_ids = payload.get("phase_task_ids", {})
            self.assertIn("lead_intake", phase_task_ids)
            self.assertIn("lead_close", phase_task_ids)
            self.assertIn("Lead summary", payload.get("response", ""))
            self.assertIn("Workflow phases", payload.get("response", ""))
            self.assertGreaterEqual(int(payload.get("productivity_score", 0)), 0)
            self.assertLessEqual(int(payload.get("productivity_score", 0)), 100)
            self.assertGreaterEqual(int(payload.get("reasoning_score", 0)), 0)
            self.assertLessEqual(int(payload.get("reasoning_score", 0)), 100)
            self.assertIn(
                str(payload.get("productivity_status", "")),
                {"weak", "moderate", "strong"},
            )
            self.assertIn(
                str(payload.get("execution_mode", "")),
                {"text_only", "hybrid", "live"},
            )
            self.assertGreaterEqual(int(payload.get("placeholder_outputs", 0)), 0)
            self.assertTrue(isinstance(payload.get("evidence_gate_applied"), bool))
            self.assertTrue(isinstance(payload.get("evidence_gate_failures", []), list))
        finally:
            api_main.set_current_workspace(previous_workspace)
            shutil.rmtree(workspace, ignore_errors=True)

    def test_chat_replan_integration_rebuilds_pending_plan(self) -> None:
        temp_root = Path.cwd() / ".tmp_api_team_chat_tests"
        workspace = temp_root / f"case_{uuid4().hex}"
        previous_workspace = api_main.get_current_workspace()

        def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
            return AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[ReplanIntegrationAdapter()],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=workspace,
                browser_mode=browser_mode,
                environment=environment,
            )

        try:
            workspace.mkdir(parents=True, exist_ok=True)
            api_main.set_current_workspace(workspace)
            client = TestClient(api_main.app)
            with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Implement endpoint with context recovery before build",
                            "mode": "sprint5",
                            "max_rounds": 6,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
            self.assertEqual(response.status_code, 200)
            payload = _parse_sse_result(response)
            phase_task_ids = payload.get("phase_task_ids", {})
            self.assertIn("discovery", phase_task_ids)
            self.assertIn("build", phase_task_ids)

            events_file = _runtime_dir_for(workspace) / "events.jsonl"
            self.assertTrue(events_file.exists())
            events_text = events_file.read_text(encoding="utf-8")
            self.assertIn('"directive": "replan"', events_text)
            self.assertIn('"source_phase": "lead_preflight_build"', events_text)
        finally:
            api_main.set_current_workspace(previous_workspace)
            shutil.rmtree(workspace, ignore_errors=True)

    def test_chat_probe_mode_returns_plan_without_executing_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[ProbeIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Haz un analisis de arquitectura del sistema actual",
                            "mode": "probe",
                            "max_rounds": 4,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("probe_mode", False)))
                self.assertEqual(str(payload.get("chat_mode", "")), "probe")
                self.assertEqual(
                    str(payload.get("lead_run_mode", "")), "architecture_review"
                )
                self.assertEqual(
                    [item.get("phase_id") for item in payload.get("planned_phases", [])],
                    ["discovery", "architecture_options", "adr_document"],
                )
                self.assertEqual(
                    payload.get("phase_task_ids", {}),
                    {"lead_intake": payload.get("lead_task_id")},
                )
                self.assertEqual(payload.get("delegated_task_ids", []), [])

                tasks_text = json.dumps(
                    _load_runtime_tasks(_runtime_dir_for(workspace)),
                    ensure_ascii=False,
                )
                self.assertNotIn("::architecture_options", tasks_text)
                self.assertNotIn("::adr_document", tasks_text)
                self.assertNotIn("::lead_close", tasks_text)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "chat_probe_completed"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_plan_mode_is_lead_only_without_deliverables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[PlanModeIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica el siguiente slice, sin ejecutar nada",
                            "mode": "plan",
                            "max_rounds": 4,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("chat_mode", "")), "plan")
                self.assertFalse(bool(payload.get("probe_mode", False)))
                self.assertEqual(str(payload.get("lead_run_mode", "")), "planning_only")
                self.assertEqual(int(payload.get("artifact_created", 0) or 0), 0)
                self.assertEqual(int(payload.get("artifact_modified", 0) or 0), 0)
                self.assertEqual(payload.get("phase_task_ids", {}), {"lead_intake": payload.get("lead_task_id")})
                self.assertEqual(payload.get("delegated_task_ids", []), [])
                self.assertFalse(bool(payload.get("evidence_gate_applied", False)))
                self.assertEqual(_plan_markdown_files(workspace), [])

                workflow_state = _read_runtime_workflow_state(_runtime_dir_for(workspace))
                run_state = dict(workflow_state.get(payload.get("task_id"), {}) or {})
                self.assertEqual(str(run_state.get("run_status", "")), "completed")
                self.assertEqual(
                    str((run_state.get("run_verdict", {}) or {}).get("result", "")),
                    "planificado",
                )

                tasks_text = json.dumps(
                    _load_runtime_tasks(_runtime_dir_for(workspace)),
                    ensure_ascii=False,
                )
                self.assertNotIn("::build", tasks_text)
                self.assertNotIn("::lead_close", tasks_text)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "chat_plan_mode_completed"', events_text)
                self.assertNotIn('"event_type": "chat_plan_persisted"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_planning_mode_persists_plan_as_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "docs").mkdir(parents=True, exist_ok=True)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[ProbeIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Haz un analisis de arquitectura del sistema actual",
                            "mode": "probe",
                            "max_rounds": 4,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                plan_files = _plan_markdown_files(workspace)
                self.assertEqual(len(plan_files), 1)
                self.assertEqual(plan_files[0].parent, workspace / "docs" / "aiteam")
                plan_text = plan_files[0].read_text(encoding="utf-8")
                self.assertIn("# Plan:", plan_text)
                self.assertIn("## Solicitud", plan_text)
                self.assertIn("## Fases planificadas", plan_text)
                self.assertIn("## Salida del Lead", plan_text)
                self.assertIn("architecture_review", plan_text)
                self.assertIn("discovery", plan_text)
                self.assertGreaterEqual(int(payload.get("artifact_created", 0) or 0), 1)
                self.assertTrue(any(str(item).endswith(".md") for item in payload.get("artifact_files", [])))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "chat_plan_persisted"', events_text)
                self.assertIn(str(plan_files[0]).replace("\\", "\\\\"), events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_standard_mode_does_not_persist_plan_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[ReplanIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Implementa la siguiente mejora y revisa el resultado",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                _ = _parse_sse_result(response)
                self.assertEqual(_plan_markdown_files(workspace), [])

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertNotIn('"event_type": "chat_plan_persisted"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_quorum_runs_multiple_models_for_planning_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "docs").mkdir(parents=True, exist_ok=True)
            previous_workspace = api_main.get_current_workspace()
            record: list[dict[str, str]] = []

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[
                            QuorumPlanningIntegrationAdapter(
                                name="openai_pro",
                                provider="openai",
                                model="gpt-pro",
                                record=record,
                            ),
                            QuorumPlanningIntegrationAdapter(
                                name="claude_pro",
                                provider="anthropic",
                                model="claude-pro",
                                record=record,
                            ),
                        ],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Haz una revision de arquitectura y prepara ADR",
                            "mode": "probe",
                            "max_rounds": 4,
                            "quorum": True,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("lead_run_mode", "")), "architecture_review")
                self.assertIn("consultant", [item.get("stage") for item in record])
                self.assertIn("final", [item.get("stage") for item in record])
                self.assertTrue(
                    any(
                        item.get("stage") == "consultant"
                        and item.get("adapter") == "claude_pro"
                        for item in record
                    )
                )
                self.assertTrue(
                    any(
                        item.get("stage") == "final"
                        and item.get("adapter") == "openai_pro"
                        for item in record
                    )
                )

                plan_files = _plan_markdown_files(workspace)
                self.assertEqual(len(plan_files), 1)
                plan_text = plan_files[0].read_text(encoding="utf-8")
                self.assertIn("Plan final consolidado con quorum.", plan_text)
                self.assertIn("## Quorum del Lead", plan_text)
                self.assertIn("### Consultores", plan_text)
                self.assertIn("claude_pro", plan_text)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "chat_quorum_applied"', events_text)
                self.assertIn('"consultant_count": 1', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_no_quorum_by_default_for_planning_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            record: list[dict[str, str]] = []

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[
                            QuorumPlanningIntegrationAdapter(
                                name="openai_pro",
                                provider="openai",
                                model="gpt-pro",
                                record=record,
                            ),
                            QuorumPlanningIntegrationAdapter(
                                name="claude_pro",
                                provider="anthropic",
                                model="claude-pro",
                                record=record,
                            ),
                        ],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Haz una revision de arquitectura y prepara ADR",
                            "mode": "probe",
                            "max_rounds": 4,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                _ = _parse_sse_result(response)
                self.assertFalse(
                    any(item.get("stage") == "consultant" for item in record)
                )
                self.assertFalse(any(item.get("stage") == "final" for item in record))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertNotIn('"event_type": "chat_quorum_applied"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_force_gate_integration_reopens_completed_phase(self) -> None:
        temp_root = Path.cwd() / ".tmp_api_team_chat_tests"
        workspace = temp_root / f"case_{uuid4().hex}"
        previous_workspace = api_main.get_current_workspace()

        def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
            return AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[ForceGateIntegrationAdapter()],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=workspace,
                browser_mode=browser_mode,
                environment=environment,
            )

        try:
            workspace.mkdir(parents=True, exist_ok=True)
            api_main.set_current_workspace(workspace)
            client = TestClient(api_main.app)
            with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Implement and then revalidate build under team decision mode",
                            "mode": "sprint5",
                            "max_rounds": 8,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
            self.assertEqual(response.status_code, 200)
            payload = _parse_sse_result(response)
            self.assertIn("build", payload.get("phase_task_ids", {}))

            tasks_text = json.dumps(
                _load_runtime_tasks(_runtime_dir_for(workspace)),
                ensure_ascii=False,
            )
            self.assertIn("::build::review", tasks_text)
            self.assertIn("::build::qa", tasks_text)

            events_file = _runtime_dir_for(workspace) / "events.jsonl"
            events_text = events_file.read_text(encoding="utf-8")
            self.assertIn('"directive": "force_gate"', events_text)
            self.assertIn('"target_phase": "build"', events_text)
            self.assertIn('"event_type": "quality_gates_opened"', events_text)
        finally:
            api_main.set_current_workspace(previous_workspace)
            shutil.rmtree(workspace, ignore_errors=True)

    def test_chat_abort_phases_integration_converts_run_to_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[AbortPhasesIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Implement and decide if more validation is still needed",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertIn("build", payload.get("phase_task_ids", {}))
                self.assertNotIn("review", payload.get("phase_task_ids", {}))
                self.assertNotIn("qa", payload.get("phase_task_ids", {}))

                tasks_text = json.dumps(
                    _load_runtime_tasks(_runtime_dir_for(workspace)),
                    ensure_ascii=False,
                )
                self.assertIn("::build", tasks_text)
                self.assertNotIn("::review", tasks_text)
                self.assertNotIn("::qa", tasks_text)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"directive": "abort_phases"', events_text)
                self.assertIn('"source_phase": "lead_report_build"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_skip_mid_run_integration_removes_pending_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[SkipMidRunIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Implement and skip downstream validation if the lead agrees",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertIn("build", payload.get("phase_task_ids", {}))
                self.assertNotIn("review", payload.get("phase_task_ids", {}))
                self.assertNotIn("qa", payload.get("phase_task_ids", {}))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"directive": "skip_mid_run"', events_text)
                self.assertIn('"source_phase": "lead_report_build"', events_text)
                self.assertIn('"removed_phases": ["qa", "review"]', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_retry_route_integration_retries_target_with_alternate_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            shared_state = {"retry_emitted": False}

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[
                            RetryRouteIntegrationAdapter("primary_route", shared_state),
                            RetryRouteIntegrationAdapter("secondary_route", shared_state),
                        ],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Implement and retry build with another route if the lead requests it",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertIn("build", payload.get("phase_task_ids", {}))

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                build_task = by_id.get(payload.get("phase_task_ids", {}).get("build"))
                self.assertIsNotNone(build_task)
                metadata = dict(build_task.get("metadata", {}))
                self.assertEqual(metadata.get("last_adapter_name"), "secondary_route")
                self.assertIn("primary_route", list(metadata.get("excluded_adapters", [])))
                self.assertTrue(bool(metadata.get("retry_route_requested")))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"directive": "retry_route"', events_text)
                self.assertIn('"target_phase": "build"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_set_budget_mid_run_updates_round_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[SetBudgetIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Implement and then let the lead reduce the remaining budget",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(int(payload.get("round_budget", 0)), 3)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"directive": "set_budget_mid_run"', events_text)
                self.assertIn('"source_phase": "lead_report_build"', events_text)
                self.assertIn('"new_round_budget": 3', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_delegate_from_lead_failure_checkpoint_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[LeadFailureDelegateIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                            response = client.post(
                                "/api/aiteam/chat",
                                json={
                                    "message": "Trigger a failing build and let the lead delegate investigation",
                                    "mode": "sprint5",
                                    "max_rounds": 8,
                                    "allow_low_productivity_override": True,
                                    "auto_extend_weak_runs": False,
                                },
                            )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(isinstance(payload.get("delegate_batches", []), list))
                self.assertTrue(
                    any(
                        str(batch.get("source_phase", "")) == "lead_failure_build"
                        for batch in list(payload.get("delegate_batches", []) or [])
                    )
                )

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"source_phase": "lead_failure_build"', events_text)
                self.assertIn('"intent": "delegate_repo_scan"', events_text)

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                delegate_tasks = [
                    item
                    for item in tasks_data
                    if isinstance(item, dict)
                    and str(item.get("task_id", "")).startswith(
                        f"{payload.get('task_id')}::delegate_repo_scan_"
                    )
                ]
                self.assertTrue(delegate_tasks)
                delegate_metadata = dict((delegate_tasks[0].get("metadata", {}) or {}))
                delegate_contract = dict(delegate_metadata.get("phase_contract", {}) or {})
                self.assertEqual(str(delegate_metadata.get("delegation_from_role", "")), "team_lead")
                self.assertTrue(bool(str(delegate_metadata.get("delegation_brief", "")).strip()))
                self.assertTrue(bool(delegate_metadata.get("phase_contract_enforced")))
                self.assertEqual(
                    str(delegate_contract.get("phase_id", "")),
                    str(delegate_tasks[0].get("task_id", "")).split("::", 1)[-1],
                )
                self.assertTrue(bool(str(delegate_contract.get("objective", "")).strip()))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_delegate_from_lead_close_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[LeadCloseDelegateIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                            response = client.post(
                                "/api/aiteam/chat",
                                json={
                                    "message": "Complete a short run and let the lead delegate one final browser verification",
                                    "mode": "sprint5",
                                    "max_rounds": 8,
                                    "allow_low_productivity_override": True,
                                    "auto_extend_weak_runs": False,
                                },
                            )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertIn("lead_close", payload.get("phase_task_ids", {}))
                self.assertTrue(isinstance(payload.get("delegate_batches", []), list))
                self.assertTrue(
                    any(
                        str(batch.get("source_phase", "")) == "lead_close"
                        for batch in list(payload.get("delegate_batches", []) or [])
                    )
                )
                lead_close_batches = [
                    batch
                    for batch in list(payload.get("delegate_batches", []) or [])
                    if str(batch.get("source_phase", "")) == "lead_close"
                ]
                self.assertEqual(len(lead_close_batches), 1)
                self.assertIn("lead_close", str(payload.get("phase_task_ids", {})))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"source_phase": "lead_close"', events_text)
                self.assertIn('"intent": "delegate_browser_repro"', events_text)
                workflow_state = _read_runtime_workflow_state(
                    _runtime_dir_for(workspace)
                )
                chat_state = dict(workflow_state.get(str(payload.get("task_id")), {}) or {})
                self.assertTrue(
                    list(chat_state.get("consumed_delegate_request_signatures", []) or [])
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_delegate_request_signature_is_stable_for_repeat_detection(self) -> None:
        request = api_main._build_delegate_request(
            "delegate_repo_scan",
            query="Mapea el repo",
            wait_policy="best_effort",
            delegate_budget=2,
        )

        self.assertEqual(
            api_main._delegate_request_signature(request),
            ("delegate_repo_scan", "mapea el repo", "best_effort"),
        )
        self.assertEqual(
            api_main._delegate_request_signature(request, source_phase="lead_intake"),
            ("lead_intake", "delegate_repo_scan", "mapea el repo", "best_effort"),
        )

    def test_delegate_batch_has_successful_results_requires_completed_entry(self) -> None:
        self.assertFalse(
            api_main._delegate_batch_has_successful_results(
                {"entries": [{"state": "failed"}, {"state": "blocked"}]}
            )
        )
        self.assertTrue(
            api_main._delegate_batch_has_successful_results(
                {"entries": [{"state": "blocked"}, {"state": "completed"}]}
            )
        )

    def test_chat_advisory_mode_turns_policy_blocks_into_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[AdvisoryModeIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Implement and let the lead close in advisory mode if evidence is weak",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "strict_mode": True,
                                "allow_low_productivity_override": False,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("state", "")), "completed")
                self.assertTrue(bool(payload.get("advisory_mode")))
                self.assertIn("advisory", str(payload.get("response", "")).lower())
                self.assertIn(
                    "strict_mode_requires_more_evidence",
                    list(payload.get("policy_signals", [])),
                )
                self.assertIn(
                    "low_productivity_below_threshold",
                    list(payload.get("policy_signals", [])),
                )

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"directive": "advisory_mode"', events_text)
                self.assertIn('"event_type": "chat_policy_signal"', events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_skip_phase_marks_task_skipped_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[SkipPhaseIntegrationAdapter()],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Intenta implementar y si no es recuperable, acepta skip phase al cierre",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertIn("Skipped phases by Lead:", str(payload.get("response", "")))
                self.assertIn("build", list(payload.get("skipped_phase_ids", [])))
                self.assertEqual(
                    dict(payload.get("skipped_phase_reasons", {})).get("build"),
                    "gate rechazado repetidamente y output placeholder",
                )

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                build_task = by_id.get(payload.get("phase_task_ids", {}).get("build"))
                self.assertIsNotNone(build_task)
                self.assertEqual(str((build_task or {}).get("state", "")), "skipped")
                self.assertEqual(
                    str((((build_task or {}).get("metadata", {}) or {}).get("skipped_reason", ""))),
                    "gate rechazado repetidamente y output placeholder",
                )

                state_response = client.get("/api/aiteam/state")
                self.assertEqual(state_response.status_code, 200)
                state_payload = state_response.json()
                last_run = state_payload.get("last_chat_run", {})
                self.assertIn("build", list((last_run or {}).get("skipped_phase_ids", [])))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_degrade_partial_appears_in_chat_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[DegradeIntegrationAdapter("partial")],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Si build falla, cierra degradado parcial con diagnostico",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("degraded_delivery")))
                self.assertEqual(str(payload.get("degrade_scope", "")), "partial")
                self.assertIn("Degraded delivery (partial):", str(payload.get("response", "")))

                state_response = client.get("/api/aiteam/state")
                self.assertEqual(state_response.status_code, 200)
                last_run = (state_response.json().get("last_chat_run", {}) or {})
                self.assertTrue(bool(last_run.get("degraded_delivery")))
                self.assertEqual(str(last_run.get("degrade_scope", "")), "partial")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_degrade_minimal_appears_in_chat_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[DegradeIntegrationAdapter("minimal")],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Si todo sale mal, cierra degradado minimal con diagnostico",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("degraded_delivery")))
                self.assertEqual(str(payload.get("degrade_scope", "")), "minimal")
                self.assertIn("Degraded delivery (minimal):", str(payload.get("response", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_pause_for_user_transitions_to_waiting_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = PauseForUserIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        response = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Si el cierre queda bloqueado, pausa y pregunta al usuario",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("state", "")), "waiting_user")
                self.assertTrue(bool(payload.get("waiting_user")))
                self.assertIn(
                    "¿Quieres reintentar con otra ruta o ajustar el objetivo?",
                    str(payload.get("clarification_question", "")),
                )

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                lead_close = by_id.get(payload.get("phase_task_ids", {}).get("lead_close"))
                self.assertIsNotNone(lead_close)
                self.assertEqual(str((lead_close or {}).get("state", "")), "waiting_user")

                pending_file = _runtime_dir_for(workspace) / f"pending_clarification_{payload.get('task_id', '')}.json"
                self.assertTrue(pending_file.exists())
                pending_state = json.loads(pending_file.read_text(encoding="utf-8"))
                self.assertEqual(str(pending_state.get("type", "")), "mid_run")
                self.assertEqual(str(pending_state.get("waiting_phase", "")), "lead_close")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_resume_with_user_response_injects_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = PauseForUserIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        initial = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Si el cierre queda bloqueado, pausa y pregunta al usuario",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                        initial_payload = _parse_sse_result(initial)
                        resumed = client.post(
                            "/api/aiteam/chat/clarify",
                            json={
                                "chat_id": initial_payload.get("task_id"),
                                "clarification": "Reintenta con otra ruta antes de cerrar",
                            },
                        )
                self.assertEqual(resumed.status_code, 200)
                resumed_payload = _parse_sse_result(resumed)
                self.assertEqual(str(resumed_payload.get("state", "")), "completed")
                self.assertTrue(adapter.seen_resume_answer)
                self.assertIn("Reanudé el cierre", str(resumed_payload.get("response", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_resume_state_treats_blocked_after_lead_close_as_terminal(self) -> None:
        self.assertEqual(
            api_main._derive_resume_state_from_phase_states(
                {
                    "lead_intake": "completed",
                    "plan_engineering": "blocked",
                    "build": "blocked",
                    "review": "blocked",
                    "qa": "blocked",
                    "lead_close": "completed",
                }
            ),
            "failed",
        )
        self.assertEqual(
            api_main._derive_resume_state_from_phase_states(
                {"lead_intake": "completed", "lead_close": "waiting_user"}
            ),
            "waiting_user",
        )
        self.assertEqual(
            api_main._derive_resume_state_from_phase_states(
                {"lead_intake": "completed", "build": "ready", "lead_close": "blocked"}
            ),
            "in_progress",
        )
        self.assertEqual(
            api_main._derive_resume_state_from_phase_states(
                {"lead_intake": "completed", "build": "completed", "lead_close": "completed"}
            ),
            "completed",
        )

    def test_midrun_user_risk_acceptance_requires_explicit_risk_choice(self) -> None:
        accepted = api_main._classify_midrun_user_risk_acceptance(
            (
                "QA está bloqueada por routing. ¿Aprobar sin QA formal, "
                "esperar routing o replantear validación?"
            ),
            "aprobar sin QA formal",
        )
        self.assertEqual(accepted.get("kind"), "user_accepted_degraded_close")
        self.assertEqual(accepted.get("scope"), "partial")

        retry = api_main._classify_midrun_user_risk_acceptance(
            (
                "QA está bloqueada por routing. ¿Aprobar sin QA formal, "
                "esperar routing o replantear validación?"
            ),
            "Reintenta con otra ruta antes de cerrar",
        )
        self.assertEqual(retry, {})

    def test_phase_contract_prompt_exposes_scope_to_reviewer_as_visible_paths(self) -> None:
        block = api_main._phase_contract_prompt_block(
            PhaseSpec(
                phase_id="review_test_preparation",
                role="REVIEWER",
                objective="Review tests",
                depends_on=["engineer_prepare_tests"],
            ),
            all_contracts={
                "review_test_preparation": {
                    "phase_id": "review_test_preparation",
                    "role": "REVIEWER",
                    "objective": "Review tests",
                    "depends_on": ["engineer_prepare_tests"],
                    "allowed_module_path_hints": [
                        "src/md_report/",
                        "src/md_report/test_toc_generator.py",
                    ],
                },
                "engineer_prepare_tests": {
                    "objective": "Prepare executable evidence",
                },
            },
        )

        self.assertIn("visible_project_scope:", block)
        self.assertIn("src/md_report/test_toc_generator.py", block)
        self.assertIn("resuelvelo contra este scope", block)

    def test_phase_contract_prompt_marks_delegate_support_as_non_decision_authority(self) -> None:
        block = api_main._phase_contract_prompt_block(
            PhaseSpec(
                phase_id="delegate_review_test_runner_0",
                role="QA",
                objective="Run support smoke and report evidence.",
                depends_on=["build"],
            ),
            all_contracts={
                "delegate_review_test_runner_0": {
                    "phase_id": "delegate_review_test_runner_0",
                    "role": "QA",
                    "objective": "Run support smoke and report evidence.",
                    "depends_on": ["build"],
                    "contract_kind": "delegate_support_pre_phase",
                    "evidence_target_phase": "review",
                },
                "build": {
                    "objective": "Implement approved slice",
                },
            },
        )

        self.assertIn("support_role: true", block)
        self.assertIn("decision_authority: parent_phase_or_team_lead", block)
        self.assertIn("no declares la fase principal blocked/rejected", block)
        self.assertIn("observed_failure", block)

    def test_chat_pause_then_resume_completes_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = PauseForUserIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                original_policy_metadata = api_main.build_chat_task_policy_metadata
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    with patch.object(
                        api_main,
                        "build_chat_task_policy_metadata",
                        side_effect=lambda **kwargs: original_policy_metadata(
                            require_execution_plan=False
                        ),
                    ):
                        initial = client.post(
                            "/api/aiteam/chat",
                            json={
                                "message": "Si el cierre queda bloqueado, pausa y pregunta al usuario",
                                "mode": "sprint5",
                                "max_rounds": 8,
                                "allow_low_productivity_override": True,
                                "auto_extend_weak_runs": False,
                            },
                        )
                        initial_payload = _parse_sse_result(initial)
                        resumed = client.post(
                            "/api/aiteam/chat/clarify",
                            json={
                                "chat_id": initial_payload.get("task_id"),
                                "clarification": "Ajusta el objetivo y cierra con diagnóstico",
                            },
                        )
                self.assertEqual(resumed.status_code, 200)
                resumed_payload = _parse_sse_result(resumed)
                task_id = str(initial_payload.get("task_id", ""))
                progress_response = client.get(f"/api/aiteam/chat/progress/{task_id}")
                self.assertEqual(progress_response.status_code, 200)
                progress_payload = progress_response.json()
                self.assertEqual(str(progress_payload.get("state", "")), "completed")
                self.assertFalse(bool(progress_payload.get("waiting_user")))

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                lead_close = by_id.get(resumed_payload.get("phase_task_ids", {}).get("lead_close"))
                self.assertIsNotNone(lead_close)
                self.assertEqual(str((lead_close or {}).get("state", "")), "completed")

                pending_file = _runtime_dir_for(workspace) / f"pending_clarification_{task_id}.json"
                self.assertFalse(pending_file.exists())
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_intake_injects_project_instructions_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = AgentsMdIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                instructions_dir = workspace / ".aiteam"
                instructions_dir.mkdir(parents=True, exist_ok=True)
                (instructions_dir / "instructions.md").write_text(
                    "# Reglas del proyecto\nUsa commits pequenos y documenta decisiones.\n",
                    encoding="utf-8",
                )
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica la siguiente mejora de backend",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    "## Instrucciones del proyecto (.aiteam/instructions.md)",
                    adapter.intake_prompt,
                )
                self.assertIn("Usa commits pequenos y documenta decisiones.", adapter.intake_prompt)
                self.assertNotIn("# AI Team Hybrid Orchestrator", adapter.intake_prompt)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_intake_receives_capabilities_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = AgentsMdIntegrationAdapter()
            unavailable_api_adapter = MissingApiKeyCapabilitiesAdapter()
            mcp_rows = [
                {
                    "name": "filesystem",
                    "enabled": True,
                    "health_status": "healthy",
                    "health_reason": "",
                },
                {
                    "name": "browser_mcp",
                    "enabled": True,
                    "health_status": "unhealthy",
                    "health_reason": "timeout",
                },
            ]

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                orchestrator = AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter, unavailable_api_adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )
                orchestrator.mcp_manager = StaticMcpManager(mcp_rows)
                return orchestrator

            try:
                runtime_dir = _runtime_dir_for(workspace)
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "provider_doctor.json").write_text(
                    json.dumps(
                        {
                            "api_keys": {
                                "OPENAI_API_KEY": "missing",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica la siguiente mejora de backend",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertIn("== SYSTEM CAPABILITIES ==", adapter.intake_prompt)
                self.assertIn("OPENAI_API_KEY ausente", adapter.intake_prompt)
                self.assertIn("MCPs disponibles: filesystem", adapter.intake_prompt)
                self.assertIn("MCPs con error: browser_mcp (timeout)", adapter.intake_prompt)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_intake_receives_current_workspace_grounding_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = AgentsMdIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                (workspace / "src" / "sample_cli").mkdir(parents=True, exist_ok=True)
                (workspace / "tests").mkdir(parents=True, exist_ok=True)
                (workspace / "pyproject.toml").write_text("[project]\nname='sample-cli'\n", encoding="utf-8")
                (workspace / "src" / "sample_cli" / "__init__.py").write_text("", encoding="utf-8")
                (workspace / "src" / "sample_cli" / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica el siguiente slice minimo del proyecto",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertIn("== ESTADO ACTUAL CONFIRMADO (WORKSPACE REAL) ==", adapter.intake_prompt)
                self.assertIn("src/sample_cli/cli.py", adapter.intake_prompt)
                self.assertIn(
                    "Todo lo que venga del historial, lead memory o runs previas cuenta solo como contexto historico",
                    adapter.intake_prompt,
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_intake_can_continue_without_waiting_on_context_curator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = CuratorFailureButLeadContinuesAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                (workspace / "src" / "sample_cli").mkdir(parents=True, exist_ok=True)
                (workspace / "tests").mkdir(parents=True, exist_ok=True)
                (workspace / "pyproject.toml").write_text("[project]\nname='sample-cli'\n", encoding="utf-8")
                (workspace / "src" / "sample_cli" / "__init__.py").write_text("", encoding="utf-8")
                (workspace / "src" / "sample_cli" / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
                (workspace / "tests" / "test_cli.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica el siguiente slice minimo del proyecto",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_phase = {
                    str(item.get("metadata", {}).get("phase", "") or ""): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                self.assertIn(
                    str((by_phase.get("scout_context_curator") or {}).get("state", "")),
                    {"completed", "failed"},
                )
                self.assertEqual(str((by_phase.get("lead_intake") or {}).get("state", "")), "completed")
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_memory_created_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = LeadMemoryIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Implementa una mejora minima del backend",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                memory_path = _runtime_dir_for(workspace) / "lead_memory.md"
                self.assertTrue(memory_path.exists())
                memory_text = memory_path.read_text(encoding="utf-8")
                self.assertIn("## Historial de runs recientes", memory_text)
                self.assertIn("objetivo=Implementa una mejora minima del backend", memory_text)
                self.assertIn("resultado=", memory_text)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_memory_appends_run_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = LeadMemoryIntegrationAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    first = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Primera mejora",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                    second = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Segunda mejora",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(first.status_code, 200)
                self.assertEqual(second.status_code, 200)
                memory_text = (_runtime_dir_for(workspace) / "lead_memory.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("objetivo=Primera mejora", memory_text)
                self.assertIn("objetivo=Segunda mejora", memory_text)
                self.assertGreaterEqual(memory_text.count("- Run "), 2)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_lead_memory_injected_before_lead_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            adapter = LeadMemoryIntegrationAdapter()
            unavailable_api_adapter = MissingApiKeyCapabilitiesAdapter()

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter, unavailable_api_adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                runtime_dir = _runtime_dir_for(workspace)
                runtime_dir.mkdir(parents=True, exist_ok=True)
                (runtime_dir / "lead_memory.md").write_text(
                    "# Lead Memory - tmp\n\n## Historial de runs recientes\n- Run 2026-04-03 10:00 UTC | chat=CHAT-OLD | objetivo=run previa | resultado=parcial | fases=2/3 | duracion=12s | errores=ninguno | decisiones=ADVISORY_MODE\n",
                    encoding="utf-8",
                )
                (runtime_dir / "provider_doctor.json").write_text(
                    json.dumps({"api_keys": {"OPENAI_API_KEY": "missing"}}),
                    encoding="utf-8",
                )
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": "Planifica otra mejora",
                            "mode": "sprint5",
                            "max_rounds": 4,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                self.assertIn("== LEAD MEMORY ==", adapter.intake_prompt)
                self.assertIn("run previa", adapter.intake_prompt)
                self.assertIn("== SYSTEM CAPABILITIES ==", adapter.intake_prompt)
                self.assertLess(
                    adapter.intake_prompt.index("== LEAD MEMORY =="),
                    adapter.intake_prompt.index("== SYSTEM CAPABILITIES =="),
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_persists_lead_and_delegated_tasks_to_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Plan architecture for a new project and then implement core module",
                        "role": "engineer",
                        "complexity": "high",
                        "criticality": "high",
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                tasks_text = json.dumps(tasks_data, ensure_ascii=False)
                self.assertIn(payload.get("lead_task_id"), tasks_text)
                for phase_id in payload.get("phase_task_ids", {}).values():
                    self.assertIn(phase_id, tasks_text)
                for delegated_id in payload.get("delegated_task_ids", []):
                    self.assertIn(delegated_id, tasks_text)

                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                build_task = by_id.get(payload.get("phase_task_ids", {}).get("build"))
                review_task = by_id.get(payload.get("phase_task_ids", {}).get("review"))
                self.assertIsNotNone(build_task)
                self.assertIsNotNone(review_task)
                # La descripcion incluye el objetivo de la fase (puede ser como
                # "Delegation brief:" o directo desde spec.objective en modo dinamico)
                self.assertTrue(str((build_task or {}).get("description", "")).strip())
                self.assertTrue(
                    str(
                        ((build_task or {}).get("metadata", {}) or {}).get(
                            "delegation_brief", ""
                        )
                    )
                )
                self.assertEqual(
                    str(
                        ((review_task or {}).get("metadata", {}) or {}).get(
                            "delegation_from_role", ""
                        )
                    ),
                    "team_lead",
                )

                mailbox_file = _runtime_dir_for(workspace) / "mailbox.jsonl"
                self.assertTrue(mailbox_file.exists())
                mailbox_text = mailbox_file.read_text(encoding="utf-8")
                self.assertIn('"sender": "user"', mailbox_text)
                self.assertIn(
                    '"body": "Plan architecture for a new project and then implement core module"',
                    mailbox_text,
                )
                self.assertIn('"sender": "team_lead"', mailbox_text)
                self.assertIn('"recipient": "user"', mailbox_text)
                self.assertIn("Resumen del Team Lead para ti:", mailbox_text)

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                self.assertTrue(events_file.exists())
                events_text = events_file.read_text(encoding="utf-8")
                self.assertIn('"event_type": "user_input"', events_text)
                self.assertIn(
                    '"event_type": "chat_execution_mode_assessed"', events_text
                )
                self.assertIn('"event_type": "routing_decision"', events_text)
                event_rows = [
                    json.loads(line)
                    for line in events_text.splitlines()
                    if line.strip()
                ]
                routing_rows = [
                    row
                    for row in event_rows
                    if str(row.get("event_type", "")) == "routing_decision"
                ]
                self.assertTrue(routing_rows)
                self.assertTrue(
                    all(
                        isinstance(row.get("payload", {}), dict)
                        and str(row.get("payload", {}).get("task_id", "")).startswith(
                            "CHAT-"
                        )
                        for row in routing_rows
                    )
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_state_and_conversation_include_lead_user_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement a focused refactor and explain outcomes",
                        "mode": "sprint5",
                        "max_rounds": 4,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                task_id = str(payload.get("task_id", ""))

                state = client.get("/api/aiteam/state?environment=dev")
                self.assertEqual(state.status_code, 200)
                state_payload = state.json()
                lead_summary = state_payload.get("last_lead_user_summary", {})
                self.assertEqual(str(lead_summary.get("task_id", "")), task_id)
                self.assertIn(
                    "Resumen del Team Lead para ti", str(lead_summary.get("body", ""))
                )
                last_chat = state_payload.get("last_chat_run", {})
                self.assertIn(
                    str(last_chat.get("execution_mode", "")),
                    {"unknown", "text_only", "hybrid", "live"},
                )
                self.assertGreaterEqual(int(last_chat.get("placeholder_outputs", 0)), 0)
                self.assertTrue(
                    isinstance(last_chat.get("successful_check_count", 0), int)
                )
                self.assertTrue(
                    isinstance(last_chat.get("live_mode_required", False), bool)
                )
                self.assertTrue(
                    isinstance(last_chat.get("live_mode_rejected", False), bool)
                )

                conv = client.get("/api/aiteam/conversations?limit=120")
                self.assertEqual(conv.status_code, 200)
                conv_payload = conv.json()
                items = conv_payload.get("items", [])
                conv_last = conv_payload.get("last_chat_run", {})
                self.assertIn(
                    str(conv_last.get("execution_mode", "")),
                    {"unknown", "text_only", "hybrid", "live"},
                )
                self.assertGreaterEqual(int(conv_last.get("placeholder_outputs", 0)), 0)
                self.assertTrue(
                    isinstance(conv_last.get("successful_check_count", 0), int)
                )
                self.assertTrue(
                    isinstance(conv_last.get("live_mode_required", False), bool)
                )
                matching = [
                    row
                    for row in items
                    if str(row.get("task_id", "")) == task_id
                    and str(row.get("sender", "")).lower() == "team_lead"
                    and str(row.get("recipient", "")).lower() == "user"
                ]
                self.assertGreaterEqual(len(matching), 1)
                self.assertIn(
                    "Resumen del Team Lead para ti", str(matching[0].get("body", ""))
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_respects_explicit_round_budget_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create plan and execute first slice in bounded rounds",
                        "role": "engineer",
                        "complexity": "medium",
                        "criticality": "medium",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(payload.get("chat_mode"), "sprint5")
                self.assertEqual(int(payload.get("round_budget", 0)), 4)
                self.assertGreaterEqual(int(payload.get("rounds_used", 0)), 1)
                self.assertLessEqual(int(payload.get("rounds_used", 0)), 4)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_tasks_expose_explicit_validation_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a concise plan and execute the first step",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(str(payload.get("validation_owner", "")), "chat_policy")

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                build_task = by_id.get(payload.get("phase_task_ids", {}).get("build"))
                lead_close = by_id.get(payload.get("phase_task_ids", {}).get("lead_close"))
                self.assertIsNotNone(build_task)
                self.assertIsNotNone(lead_close)
                for task_row in [build_task, lead_close]:
                    metadata = ((task_row or {}).get("metadata", {}) or {})
                    self.assertEqual(str(metadata.get("validation_owner", "")), "chat_policy")
                    self.assertEqual(
                        str(metadata.get("final_validation_layer", "")),
                        "chat_policy",
                    )
                    self.assertEqual(
                        str(metadata.get("phase_quality_gate_mode", "")),
                        "delegated_to_chat_policy",
                    )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_response_progress_and_events_expose_phase_evidence_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement React login form with browser validation and tests",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                phase_evidence_plan = payload.get("phase_evidence_plan", {})
                self.assertTrue(isinstance(phase_evidence_plan, dict))
                self.assertIn("build", phase_evidence_plan)
                self.assertIn(
                    "delegate_test_run",
                    list(phase_evidence_plan["build"].get("delegate_intents", [])),
                )
                self.assertTrue(isinstance(payload.get("delegate_batches", []), list))
                self.assertTrue(isinstance(payload.get("delegate_economics", {}), dict))
                self.assertTrue(isinstance(payload.get("specialist_reports", []), list))
                self.assertTrue(isinstance(payload.get("specialist_report_summary", {}), dict))
                self.assertTrue(
                    isinstance(payload.get("peer_consultation_summary", {}), dict)
                )
                self.assertTrue(
                    isinstance(
                        payload.get("peer_consultation_summary", {}).get(
                            "consulted_roles", []
                        ),
                        list,
                    )
                )
                self.assertIn(
                    "estimated_net_tokens_saved",
                    payload.get("delegate_economics", {}),
                )

                task_root = str(payload.get("task_id", "")).strip()
                progress = client.get(f"/api/aiteam/chat/progress/{task_root}")
                self.assertEqual(progress.status_code, 200)
                progress_payload = progress.json()
                self.assertIn("build", progress_payload.get("phase_evidence_plan", {}))
                self.assertTrue(
                    isinstance(progress_payload.get("delegate_batches", []), list)
                )
                self.assertTrue(
                    isinstance(progress_payload.get("delegate_economics", {}), dict)
                )
                self.assertTrue(
                    isinstance(progress_payload.get("specialist_reports", []), list)
                )
                self.assertTrue(
                    isinstance(progress_payload.get("specialist_report_summary", {}), dict)
                )
                self.assertTrue(
                    isinstance(progress_payload.get("peer_consultation_summary", {}), dict)
                )
                self.assertTrue(
                    isinstance(
                        progress_payload.get("peer_consultation_summary", {}).get(
                            "consulted_providers", []
                        ),
                        list,
                    )
                )

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                event_rows = [
                    json.loads(line)
                    for line in events_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                plan_rows = [
                    row for row in event_rows
                    if str(row.get("event_type", "")) == "chat_plan_created"
                ]
                self.assertTrue(plan_rows)
                plan_payload = dict((plan_rows[-1].get("payload", {}) or {}))
                self.assertIn("build", plan_payload.get("phase_evidence_plan", {}))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_solo_lead_profile_does_not_create_delegate_evidence_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement the smallest useful CLI improvement",
                        "mode": "direct",
                        "run_profile": "solo_lead",
                        "max_rounds": 3,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertEqual(payload.get("run_profile"), "solo_lead")
                self.assertEqual(payload.get("phase_evidence_plan", {}), {})
                self.assertEqual(payload.get("delegated_task_ids", []), [])
                self.assertIn("build", payload.get("phase_task_ids", {}))
                self.assertIn("lead_close", payload.get("phase_task_ids", {}))

                task_root = str(payload.get("task_id", "")).strip()
                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                run_task_ids = [
                    str(item.get("task_id", ""))
                    for item in tasks_data
                    if isinstance(item, dict)
                    and str(item.get("task_id", "")).startswith(f"{task_root}::")
                ]
                self.assertFalse(
                    [task_id for task_id in run_task_ids if "::delegate_" in task_id],
                    "solo_lead should not create delegate evidence tasks",
                )

                build_task_rows = [
                    item
                    for item in tasks_data
                    if isinstance(item, dict)
                    and item.get("task_id") == f"{task_root}::build"
                ]
                self.assertTrue(build_task_rows, "build phase task should exist")
                build_task_meta = (build_task_rows[0].get("metadata", {}) or {})
                self.assertEqual(build_task_meta.get("run_profile"), "solo_lead")
                self.assertTrue(build_task_meta.get("direct_coding_executor"))
                self.assertIs(build_task_meta.get("require_peer_consultation"), False)
                self.assertIs(build_task_meta.get("skip_peer_consultation"), True)
                self.assertIs(build_task_meta.get("skip_specialist_prefetch"), True)
                self.assertEqual(
                    build_task_meta.get("required_capabilities"),
                    ["reasoning", "coding"],
                )
                self.assertEqual(build_task_meta.get("deferred_evidence_specs", []), [])
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_solo_lead_allows_bounded_retries_after_failed_post_build_validation(self) -> None:
        with _safe_tempdir("solo-lead-post-build-retry") as tmp:
            runtime_dir = tmp / "runtime"
            workspace = tmp / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            orch = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=workspace,
            )
            build = WorkTask(
                task_id="CHAT-POST-REPAIR::build",
                title="Build",
                description="Original build objective\n",
                role=Role.TEAM_LEAD,
                state=TaskState.COMPLETED,
                metadata={
                    "phase": "build",
                    "result": "previous syntax-only repair output",
                    "phase_contract": {
                        "phase_id": "build",
                        "role": "TEAM_LEAD",
                        "objective": "Repair syntax-only failure",
                    },
                },
            )
            lead_close = WorkTask(
                task_id="CHAT-POST-REPAIR::lead_close",
                title="Lead synthesis",
                description="Close after build.",
                role=Role.TEAM_LEAD,
                dependencies=[build.task_id],
                state=TaskState.COMPLETED,
                metadata={"phase": "lead_close"},
            )
            orch.taskboard.add_task(build)
            orch.taskboard.add_task(lead_close)

            target = api_main._prepare_direct_post_build_repair_retry(
                orch=orch,
                task_root="CHAT-POST-REPAIR",
                phase_task_ids={
                    "build": build.task_id,
                    "lead_close": lead_close.task_id,
                },
                result={
                    "success": False,
                    "target_task_id": build.task_id,
                    "command": "python syntax smoke imports tests/test_report_generator.py",
                    "exit_code": 1,
                    "stdout": "ImportError: cannot import name 'generate_report'",
                    "stderr": "",
                },
            )

            self.assertEqual(target, build.task_id)
            retried_build = orch.taskboard.get_task(build.task_id)
            retried_close = orch.taskboard.get_task(lead_close.task_id)
            assert retried_build is not None
            assert retried_close is not None
            self.assertEqual(retried_build.state, TaskState.READY)
            self.assertEqual(retried_close.state, TaskState.COMPLETED)
            self.assertTrue(retried_build.metadata.get("auto_post_build_repair_attempted"))
            self.assertEqual(
                retried_build.metadata.get("repair_first_origin"),
                "auto_post_build_validation",
            )
            self.assertEqual(
                retried_build.metadata.get("phase_contract", {}).get("contract_kind"),
                "solo_lead_post_build_repair",
            )
            self.assertIn("generate_report", retried_build.description)
            self.assertTrue(retried_close.metadata.get("post_build_repair_pending"))
            self.assertTrue(
                retried_close.metadata.get("post_build_repair_hold_until_validation")
            )

            retried_build.state = TaskState.COMPLETED
            retried_close.state = TaskState.COMPLETED
            second_target = api_main._prepare_direct_post_build_repair_retry(
                orch=orch,
                task_root="CHAT-POST-REPAIR",
                phase_task_ids={
                    "build": build.task_id,
                    "lead_close": lead_close.task_id,
                },
                result={
                    "success": False,
                    "target_task_id": build.task_id,
                    "command": "python syntax smoke imports src/md_report/toc_generator.py",
                    "exit_code": 1,
                    "stdout": "ImportError: cannot import name 'format_toc_as_markdown'",
                    "stderr": "",
                },
                max_attempts=2,
            )
            self.assertEqual(second_target, build.task_id)
            retried_build = orch.taskboard.get_task(build.task_id)
            assert retried_build is not None
            self.assertEqual(
                retried_build.metadata.get("auto_post_build_repair_attempt_count"),
                2,
            )
            self.assertIn("format_toc_as_markdown", retried_build.description)
            third_target = api_main._prepare_direct_post_build_repair_retry(
                orch=orch,
                task_root="CHAT-POST-REPAIR",
                phase_task_ids={
                    "build": build.task_id,
                    "lead_close": lead_close.task_id,
                },
                result={
                    "success": False,
                    "target_task_id": build.task_id,
                    "command": "python syntax smoke imports src/md_report/toc_generator.py",
                    "exit_code": 1,
                    "stdout": "ImportError: still failing",
                    "stderr": "",
                },
                max_attempts=2,
            )
            self.assertEqual(third_target, "")

    def test_solo_lead_post_build_repair_stops_on_stagnant_import_error(self) -> None:
        with _safe_tempdir("solo-lead-post-build-stagnation") as tmp:
            runtime_dir = tmp / "runtime"
            workspace = tmp / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            orch = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=workspace,
            )
            build = WorkTask(
                task_id="CHAT-POST-STAGNANT::build",
                title="Build",
                description="Original build objective\n",
                role=Role.TEAM_LEAD,
                state=TaskState.COMPLETED,
                metadata={"phase": "build"},
            )
            orch.taskboard.add_task(build)
            result = {
                "success": False,
                "target_task_id": build.task_id,
                "command": "python -m pytest -q --tb=short",
                "exit_code": 2,
                "stdout": (
                    "ImportError: cannot import name 'TOCGenerator' "
                    "from 'src.md_report.toc_generator'"
                ),
                "stderr": "",
            }
            with patch.dict(
                os.environ,
                {"AITEAM_DIRECT_POST_BUILD_STAGNATION_LIMIT": "2"},
                clear=False,
            ):
                first = api_main._prepare_direct_post_build_repair_retry(
                    orch=orch,
                    task_root="CHAT-POST-STAGNANT",
                    phase_task_ids={"build": build.task_id},
                    result=result,
                    max_attempts=10,
                )
                self.assertEqual(first, build.task_id)
                build.state = TaskState.COMPLETED
                second = api_main._prepare_direct_post_build_repair_retry(
                    orch=orch,
                    task_root="CHAT-POST-STAGNANT",
                    phase_task_ids={"build": build.task_id},
                    result=result,
                    max_attempts=10,
                )
                self.assertEqual(second, build.task_id)
                build.state = TaskState.COMPLETED
                third = api_main._prepare_direct_post_build_repair_retry(
                    orch=orch,
                    task_root="CHAT-POST-STAGNANT",
                    phase_task_ids={"build": build.task_id},
                    result=result,
                    max_attempts=10,
                )

            self.assertEqual(third, "")
            refreshed = orch.taskboard.get_task(build.task_id)
            assert refreshed is not None
            self.assertTrue(refreshed.metadata.get("auto_post_build_repair_stagnated"))

    def test_solo_lead_resumes_lead_close_only_after_post_build_success(self) -> None:
        with _safe_tempdir("solo-lead-post-build-close-resume") as tmp:
            runtime_dir = tmp / "runtime"
            workspace = tmp / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            workspace.mkdir(parents=True, exist_ok=True)
            orch = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=workspace,
            )
            build = WorkTask(
                task_id="CHAT-POST-CLOSE::build",
                title="Build",
                description="Build.",
                role=Role.TEAM_LEAD,
                state=TaskState.COMPLETED,
                metadata={"phase": "build"},
            )
            lead_close = WorkTask(
                task_id="CHAT-POST-CLOSE::lead_close",
                title="Lead synthesis",
                description="Close.",
                role=Role.TEAM_LEAD,
                dependencies=[build.task_id],
                state=TaskState.COMPLETED,
                metadata={
                    "phase": "lead_close",
                    "post_build_repair_pending": True,
                    "post_build_repair_hold_until_validation": True,
                },
            )
            orch.taskboard.add_task(build)
            orch.taskboard.add_task(lead_close)

            resumed = api_main._resume_direct_post_build_lead_close_after_success(
                orch=orch,
                task_root="CHAT-POST-CLOSE",
                phase_task_ids={"build": build.task_id, "lead_close": lead_close.task_id},
            )

            self.assertTrue(resumed)
            refreshed = orch.taskboard.get_task(lead_close.task_id)
            assert refreshed is not None
            self.assertEqual(refreshed.state, TaskState.READY)
            self.assertFalse(refreshed.metadata.get("post_build_repair_pending"))
            self.assertTrue(refreshed.metadata.get("post_build_repair_validation_passed"))

    def test_chat_browser_surface_delegates_to_browser_and_mcp_specialists_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Debug React browser flow with DOM checks, screenshots and MCP UI validation",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                phase_evidence_plan = payload.get("phase_evidence_plan", {})
                self.assertIn("build", phase_evidence_plan)
                self.assertIn(
                    "delegate_browser_repro",
                    list(phase_evidence_plan["build"].get("delegate_intents", [])),
                )

                task_root = str(payload.get("task_id", "")).strip()
                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                # C1: delegate evidence tasks are deferred — they're created lazily
                # when the parent phase is claimed, not at plan time. Verify that the
                # build task stores its deferred specs and that they have the expected
                # specialists and metadata.
                build_task_rows = [
                    item
                    for item in tasks_data
                    if isinstance(item, dict)
                    and item.get("task_id") == f"{task_root}::build"
                ]
                self.assertTrue(build_task_rows, "build phase task should exist")
                build_task_meta = (build_task_rows[0].get("metadata", {}) or {})
                deferred_specs = list(build_task_meta.get("deferred_evidence_specs", []) or [])
                self.assertTrue(deferred_specs, "build task should have deferred_evidence_specs (C1)")

                by_specialist = {
                    str((spec.get("metadata", {}) or {}).get("tool_specialist", "")): (spec.get("metadata", {}) or {})
                    for spec in deferred_specs
                }
                self.assertIn("browser_operator", by_specialist)
                self.assertTrue(
                    any(
                        name in by_specialist
                        for name in ("mcp_operator", "skill_worker", "test_runner")
                    )
                )
                self.assertEqual(
                    by_specialist["browser_operator"].get("skill_targets", []),
                    ["playwright_qa_skill"],
                )
                self.assertEqual(
                    by_specialist["browser_operator"].get("delegate_report_contract_version", ""),
                    "operator_report_v1",
                )
                self.assertIn(
                    "playwright_qa_skill",
                    list(by_specialist["browser_operator"].get("tool_specialist_skill_targets", [])),
                )

                browser_specs = [
                    spec for spec in deferred_specs
                    if str((spec.get("metadata", {}) or {}).get("tool_specialist", "") or "") == "browser_operator"
                ]
                self.assertTrue(browser_specs)
                browser_meta = dict(browser_specs[0].get("metadata", {}) or {})
                browser_contract = dict(browser_meta.get("phase_contract", {}) or {})
                self.assertTrue(bool(browser_meta.get("phase_contract_enforced")))
                self.assertEqual(
                    str(browser_contract.get("phase_id", "")),
                    str(browser_specs[0].get("task_id", "")).split("::", 1)[-1],
                )
                self.assertTrue(bool(str(browser_contract.get("objective", "")).strip()))
                self.assertIn(
                    "steps_reproduced",
                    str(browser_specs[0].get("description", "")),
                )
                self.assertTrue(isinstance(payload.get("specialist_reports", []), list))
                summary = dict(payload.get("specialist_report_summary", {}) or {})
                self.assertIn("count", summary)

                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                review_task = by_id.get(f"{task_root}::review")
                qa_task = by_id.get(f"{task_root}::qa")
                self.assertIsNotNone(review_task)
                self.assertIsNotNone(qa_task)
                review_delegate_ids = [
                    str(task_id)
                    for task_id in by_id
                    if str(task_id).startswith(f"{task_root}::delegate_review_")
                ]
                qa_delegate_ids = [
                    str(task_id)
                    for task_id in by_id
                    if str(task_id).startswith(f"{task_root}::delegate_qa_")
                ]
                self.assertTrue(review_delegate_ids)
                self.assertTrue(qa_delegate_ids)
                self.assertTrue(
                    any(
                        task_id in list((review_task or {}).get("dependencies", []) or [])
                        for task_id in review_delegate_ids
                    )
                )
                self.assertTrue(
                    any(
                        task_id in list((qa_task or {}).get("dependencies", []) or [])
                        for task_id in qa_delegate_ids
                    )
                )
                self.assertTrue(
                    all(
                        f"{task_root}::review"
                        not in list((by_id.get(task_id) or {}).get("dependencies", []) or [])
                        for task_id in review_delegate_ids
                    )
                )
                self.assertTrue(
                    all(
                        f"{task_root}::qa"
                        not in list((by_id.get(task_id) or {}).get("dependencies", []) or [])
                        for task_id in qa_delegate_ids
                    )
                )
                for task_id in review_delegate_ids:
                    delegate_contract = dict(
                        ((by_id.get(task_id) or {}).get("metadata", {}) or {}).get(
                            "phase_contract", {}
                        )
                        or {}
                    )
                    self.assertNotIn("review", list(delegate_contract.get("depends_on", []) or []))
                    self.assertEqual(
                        str(delegate_contract.get("evidence_target_phase", "")),
                        "review",
                    )
                    self.assertEqual(
                        str(delegate_contract.get("contract_kind", "")),
                        "delegate_support_pre_phase",
                    )
                for task_id in qa_delegate_ids:
                    delegate_contract = dict(
                        ((by_id.get(task_id) or {}).get("metadata", {}) or {}).get(
                            "phase_contract", {}
                        )
                        or {}
                    )
                    self.assertNotIn("qa", list(delegate_contract.get("depends_on", []) or []))
                    self.assertEqual(
                        str(delegate_contract.get("evidence_target_phase", "")),
                        "qa",
                    )
                    self.assertEqual(
                        str(delegate_contract.get("contract_kind", "")),
                        "delegate_support_pre_phase",
                    )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_scout_preflight_tasks_use_repo_scout_specialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Ponme al día del proyecto y prepara plan inicial",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                task_root = str(payload.get("task_id", "")).strip()

                tasks_data = _load_runtime_tasks(_runtime_dir_for(workspace))
                by_id = {
                    item.get("task_id"): item
                    for item in tasks_data
                    if isinstance(item, dict)
                }
                scout_rows = [
                    by_id.get(f"{task_root}::scout_project_state"),
                    by_id.get(f"{task_root}::scout_session_history"),
                ]
                scout_rows = [row for row in scout_rows if row is not None]
                self.assertGreaterEqual(len(scout_rows), 2)
                for row in scout_rows:
                    metadata = ((row or {}).get("metadata", {}) or {})
                    self.assertEqual(str(metadata.get("tool_specialist", "")), "repo_scout")
                    self.assertEqual(
                        str(metadata.get("tool_specialist_decision_scope", "")),
                        "operate_tools_and_report_only",
                    )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_auto_extends_round_budget_when_run_is_weak_only_if_runnable_work_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Analyze and propose architecture options for auth module",
                        "mode": "sprint5",
                        "max_rounds": 3,
                        "strict_mode": False,
                        "auto_extend_weak_runs": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                auto_extended_rounds = int(payload.get("auto_extended_rounds", 0))
                if auto_extended_rounds > 0:
                    self.assertGreaterEqual(int(payload.get("round_budget", 0)), 6)
                    self.assertGreaterEqual(auto_extended_rounds, 3)
                else:
                    self.assertEqual(int(payload.get("round_budget", 0)), 3)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_should_auto_extend_weak_run_skips_when_root_has_no_runnable_tasks(self) -> None:
        should_extend, reason = api_main._should_auto_extend_weak_run(
            artifact_created=0,
            execution_steps_so_far=0,
            planning_failure_detected=False,
            root_task_state_counts={
                "completed": 4,
                "blocked": 3,
                "pending": 2,
            },
        )

        self.assertFalse(should_extend)
        self.assertEqual(reason, "no_runnable_tasks_for_root")

    def test_chat_strict_mode_blocks_close_without_minimum_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a concise implementation proposal for logging improvements",
                        "mode": "sprint5",
                        "max_rounds": 5,
                        "strict_mode": True,
                        "auto_extend_weak_runs": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("strict_mode")))
                if (
                    int(payload.get("execution_steps", 0)) == 0
                    and int(payload.get("artifact_created", 0))
                    + int(payload.get("artifact_modified", 0))
                    == 0
                ):
                    self.assertNotEqual(str(payload.get("state", "")), "completed")
                self.assertIn("Strict mode", str(payload.get("response", "")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_low_productivity_gate_signals_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a conceptual architecture note for telemetry improvements",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": True,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertLess(
                    int(payload.get("productivity_score", 100)),
                    int(payload.get("productivity_threshold", 35)),
                )
                self.assertTrue(bool(payload.get("policy_review_required")))
                self.assertFalse(bool(payload.get("low_productivity_rejected")))
                self.assertIn(
                    "low_productivity_below_threshold",
                    list(payload.get("policy_signals", [])),
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_low_productivity_override_allows_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create a conceptual architecture note for telemetry improvements",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("low_productivity_override")))
                self.assertFalse(bool(payload.get("low_productivity_rejected")))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_evidence_gate_signals_placeholder_build_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Plan and implement a robust auth module",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": True,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("evidence_gate_applied")))
                self.assertTrue(bool(payload.get("policy_review_required")))
                self.assertIn(
                    "evidence_gate_failed",
                    list(payload.get("policy_signals", [])),
                )
                failures = payload.get("evidence_gate_failures", [])
                self.assertTrue(any("build" in str(item) for item in failures))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_requires_execution_plan_for_build_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement backend endpoint with tests",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": True,
                        "auto_extend_weak_runs": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("evidence_gate_applied")))
                self.assertTrue(bool(payload.get("policy_review_required")))
                self.assertIn(
                    "evidence_gate_failed",
                    list(payload.get("policy_signals", [])),
                )
                failures = [
                    str(item) for item in payload.get("evidence_gate_failures", [])
                ]
                self.assertTrue(any("build" in item for item in failures))

                events_file = _runtime_dir_for(workspace) / "events.jsonl"
                self.assertTrue(events_file.exists())
                events_rows = [
                    json.loads(line)
                    for line in events_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                compliance_rows = [
                    row
                    for row in events_rows
                    if str(row.get("event_type", "")) == "compliance_violation"
                ]
                self.assertTrue(compliance_rows)
                reasons = [
                    str((row.get("payload", {}) or {}).get("reason", ""))
                    for row in compliance_rows
                ]
                self.assertIn("missing_execution_plan_required", reasons)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_phase_execution_plan_requirement_is_narrowed_to_build_or_derived_steps(self) -> None:
        self.assertFalse(
            api_main._should_require_execution_plan_for_chat_phase(
                phase_id="validate_and_fix",
                role="ENGINEER",
                lead_run_mode="standard",
                require_build_execution_plan=True,
                derived_execution_plan=[],
            )
        )
        self.assertTrue(
            api_main._should_require_execution_plan_for_chat_phase(
                phase_id="validate_and_fix",
                role="ENGINEER",
                lead_run_mode="standard",
                require_build_execution_plan=True,
                derived_execution_plan=[{"type": "cmd", "command": "pytest tests/ -q"}],
            )
        )
        self.assertTrue(
            api_main._should_require_execution_plan_for_chat_phase(
                phase_id="build",
                role="ENGINEER",
                lead_run_mode="standard",
                require_build_execution_plan=True,
                derived_execution_plan=[],
            )
        )

    def test_workspace_allowed_module_scope_hints_follow_current_layout(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp:
            workspace = Path(tmp)
            (workspace / "src" / "acme_cli").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "acme_cli" / "__init__.py").write_text("", encoding="utf-8")
            (workspace / "src" / "acme_cli" / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
            (workspace / "src" / "acme_cli" / "render.py").write_text("def render():\n    return ''\n", encoding="utf-8")
            (workspace / "tests").mkdir(parents=True, exist_ok=True)
            (workspace / "tests" / "test_cli.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

            hints = api_main._workspace_allowed_module_scope_hints(workspace)

            self.assertIn("src/acme_cli/", hints)
            self.assertIn("src/acme_cli/cli.py", hints)
            self.assertIn("src/acme_cli/render.py", hints)
            self.assertNotIn("tests/test_cli.py", hints)

    def test_chat_can_signal_live_mode_via_env_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            previous_env = os.environ.get("AITEAM_REQUIRE_LIVE_MODE")
            # Force disabled live mode so chat is rejected explicitly.
            previous_live_api = os.environ.get("AITEAM_ENABLE_LIVE_API")
            try:
                os.environ["AITEAM_REQUIRE_LIVE_MODE"] = "1"
                os.environ["AITEAM_ENABLE_LIVE_API"] = "0"
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Implement endpoint and validate behavior",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "strict_mode": False,
                        "allow_low_productivity_override": True,
                    },
                )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                self.assertTrue(bool(payload.get("live_mode_required")))
                self.assertFalse(bool(payload.get("live_mode_rejected")))
                self.assertTrue(bool(payload.get("policy_review_required")))
                self.assertIn(
                    "live_mode_required_non_live",
                    list(payload.get("policy_signals", [])),
                )
            finally:
                if previous_env is None:
                    os.environ.pop("AITEAM_REQUIRE_LIVE_MODE", None)
                else:
                    os.environ["AITEAM_REQUIRE_LIVE_MODE"] = previous_env
                if previous_live_api is None:
                    os.environ.pop("AITEAM_ENABLE_LIVE_API", None)
                else:
                    os.environ["AITEAM_ENABLE_LIVE_API"] = previous_live_api
                api_main.set_current_workspace(previous_workspace)

    def test_operator_timeline_endpoint_returns_key_events_for_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                client_task_id = "CHAT-1122AABB"
                chat = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create an original arcade game and implement first playable version",
                        "mode": "sprint5",
                        "max_rounds": 5,
                        "client_task_id": client_task_id,
                    },
                )
                self.assertEqual(chat.status_code, 200)

                timeline = client.get(
                    f"/api/aiteam/operator/timeline?task_id={client_task_id}&limit=60&key_only=true"
                )
                self.assertEqual(timeline.status_code, 200)
                payload = timeline.json()
                self.assertEqual(
                    str(payload.get("selected_task_id", "")), client_task_id
                )
                self.assertGreaterEqual(int(payload.get("total", 0)), 1)
                self.assertTrue(isinstance(payload.get("items", []), list))
                self.assertTrue(isinstance(payload.get("available_runs", []), list))
                progress = payload.get("progress", {})
                self.assertEqual(str(progress.get("task_id", "")), client_task_id)
                if payload.get("items"):
                    item = payload["items"][0]
                    self.assertIn("execution_round", item)
                    self.assertIn("execution_sub_iteration", item)
                    self.assertIn("gate_iteration", item)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_continue_from_explicit_chat_root_keeps_continuity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                first = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create and design an original game",
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(first.status_code, 200)
                first_payload = _parse_sse_result(first)
                first_root = str(first_payload.get("task_id", ""))
                self.assertTrue(first_root.startswith("CHAT-"))

                second = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": f"Continue from {first_root}.",
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(second.status_code, 200)
                second_payload = _parse_sse_result(second)
                self.assertTrue(bool(second_payload.get("continuation_requested")))
                self.assertEqual(
                    str(second_payload.get("continuation_of", "")), first_root
                )
                self.assertIn(
                    f"continuation_of={first_root}",
                    str(second_payload.get("response", "")),
                )

                third = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Close pending phases first, then provide a compact final synthesis.",
                        "continuation_target": first_root,
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(third.status_code, 200)
                third_payload = _parse_sse_result(third)
                self.assertTrue(bool(third_payload.get("continuation_requested")))
                self.assertEqual(
                    str(third_payload.get("continuation_of", "")), first_root
                )

                state = client.get("/api/aiteam/state?environment=dev").json()
                last_run = state.get("last_chat_run", {})
                self.assertTrue(bool(last_run.get("continuation_requested")))
                self.assertEqual(str(last_run.get("continuation_of", "")), first_root)
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_implicit_continue_uses_latest_project_run_not_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                first = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Create and design an original game",
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(first.status_code, 200)
                first_payload = _parse_sse_result(first)
                first_root = str(first_payload.get("task_id", ""))
                self.assertTrue(first_root.startswith("CHAT-"))

                second = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": (
                            "continua completando el proyecto\n\n"
                            "[Respuesta del usuario a tu pregunta previa: "
                            "Indica `Continue from CHAT-XXXXXXXX`: no, continua el proyecto]"
                        ),
                        "mode": "sprint5",
                        "max_rounds": 5,
                    },
                )
                self.assertEqual(second.status_code, 200)
                second_payload = _parse_sse_result(second)
                self.assertTrue(bool(second_payload.get("continuation_requested")))
                self.assertTrue(bool(second_payload.get("continuation_effective")))
                self.assertEqual(
                    str(second_payload.get("continuation_of", "")), first_root
                )
                self.assertNotEqual(
                    str(second_payload.get("continuation_of", "")),
                    "CHAT-XXXXXXXX",
                )
                self.assertNotEqual(str(second_payload.get("state", "")), "waiting_user")
                self.assertNotIn(
                    "necesito el chat exacto",
                    str(second_payload.get("response", "")).lower(),
                )
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_progress_endpoint_tracks_client_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                client_task_id = "CHAT-ABCDEF12"

                initial_progress = client.get(
                    f"/api/aiteam/chat/progress/{client_task_id}"
                )
                self.assertEqual(initial_progress.status_code, 200)
                initial_payload = initial_progress.json()
                self.assertFalse(bool(initial_payload.get("exists")))

                chat_response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Build a small game prototype with real files",
                        "mode": "sprint5",
                        "max_rounds": 4,
                        "client_task_id": client_task_id,
                    },
                )
                self.assertEqual(chat_response.status_code, 200)
                chat_payload = _parse_sse_result(chat_response)
                self.assertEqual(str(chat_payload.get("task_id", "")), client_task_id)

                progress = client.get(f"/api/aiteam/chat/progress/{client_task_id}")
                self.assertEqual(progress.status_code, 200)
                payload = progress.json()
                self.assertTrue(bool(payload.get("exists")))
                self.assertEqual(str(payload.get("task_id", "")), client_task_id)
                self.assertGreaterEqual(int(payload.get("round_budget", 0)), 4)
                self.assertGreaterEqual(int(payload.get("rounds_used", 0)), 1)
                self.assertIn("lead_intake", payload.get("phase_states", {}))
                self.assertGreaterEqual(int(payload.get("execution_attempts", 0)), 1)
                self.assertGreaterEqual(
                    int(payload.get("execution_steps_success", 0)), 0
                )
                self.assertTrue(isinstance(payload.get("successful_checks", []), list))
            finally:
                api_main.set_current_workspace(previous_workspace)

    def test_chat_rejects_new_run_when_workspace_already_has_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            active_root = "CHAT-AC710E01"
            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                api_main._claim_workspace_active_run(workspace, active_root)

                response = client.post(
                    "/api/aiteam/chat",
                    json={
                        "message": "Start a brand new run while another one is still active",
                        "mode": "sprint5",
                        "max_rounds": 4,
                    },
                )

                self.assertEqual(response.status_code, 409)
                payload = response.json()
                detail = payload.get("detail", {})
                self.assertIn(active_root, str(detail.get("message", "")))
                active_run = detail.get("active_run", {}) or {}
                self.assertEqual(str(active_run.get("task_id", "")), active_root)
            finally:
                api_main._release_workspace_active_run(workspace, active_root)
                api_main.set_current_workspace(previous_workspace)

    def test_close_pending_plan_repair_recreates_failed_plan_engineering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            previous_workspace = api_main.get_current_workspace()
            previous_root = "CHAT-ZZZZ9999"
            runtime_dir = _runtime_dir_for(workspace)
            runtime_dir.mkdir(parents=True, exist_ok=True)
            adapter = ReplanIntegrationAdapter()

            SqliteStore(runtime_dir / "aiteam.db").save_workflow_state(
                {
                    previous_root: {
                        "phase_contracts": {
                            "plan_engineering": {
                                "phase_id": "plan_engineering",
                                "role": "ENGINEER",
                                "objective": "Definir corte de implementacion del slice pendiente",
                                "depends_on": [],
                            },
                            "build": {
                                "phase_id": "build",
                                "role": "ENGINEER",
                                "objective": "Ejecutar el slice aprobado en planning",
                                "depends_on": ["plan_engineering"],
                            },
                            "review": {
                                "phase_id": "review",
                                "role": "REVIEWER",
                                "objective": "Revisar el build contra el plan acordado",
                                "depends_on": ["build"],
                            },
                            "qa": {
                                "phase_id": "qa",
                                "role": "QA",
                                "objective": "Validar criterios de aceptacion para lo ejecutado",
                                "depends_on": ["build"],
                            },
                        },
                        "phase_verdicts": {
                            "plan_engineering": {"status": "failed"},
                            "build": {"status": "blocked"},
                            "review": {"status": "blocked"},
                            "qa": {"status": "blocked"},
                        },
                        "workflow_phase_keys": [
                            "lead_intake",
                            "plan_engineering",
                            "build",
                            "review",
                            "qa",
                            "lead_close",
                        ],
                        "run_verdict": {
                            "state": "failed",
                            "result": "fallido",
                            "reason_codes": [
                                "phase_failed:plan_engineering",
                                "build:not_completed",
                            ],
                            "failed_phases": ["plan_engineering"],
                            "pending_phases": ["build", "review", "qa", "lead_close"],
                        },
                    }
                }
            )

            def _factory(runtime_dir: Path, browser_mode: str = "basic", environment: str = "dev"):
                return AITeamOrchestrator(
                    router=HybridRouter(
                        adapters=[adapter],
                        policy=build_default_router_policy(),
                    ),
                    runtime_dir=runtime_dir,
                    project_root=workspace,
                    browser_mode=browser_mode,
                    environment=environment,
                )

            try:
                api_main.set_current_workspace(workspace)
                client = TestClient(api_main.app)
                with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                    response = client.post(
                        "/api/aiteam/chat",
                        json={
                            "message": (
                                f"Continue from {previous_root}. "
                                "Close pending phases first, then provide a compact final synthesis."
                            ),
                            "mode": "sprint5",
                            "max_rounds": 2,
                            "allow_low_productivity_override": True,
                            "auto_extend_weak_runs": False,
                        },
                    )
                self.assertEqual(response.status_code, 200)
                payload = _parse_sse_result(response)
                phase_task_ids = dict(payload.get("phase_task_ids", {}) or {})
                self.assertIn("plan_engineering", phase_task_ids)
                self.assertIn("build", phase_task_ids)
                self.assertIn("review", phase_task_ids)
                self.assertIn("qa", phase_task_ids)

                runtime_tasks = _load_runtime_tasks(runtime_dir)
                created_ids = {str(item.get("task_id", "")) for item in runtime_tasks}
                self.assertIn(phase_task_ids["plan_engineering"], created_ids)
                self.assertIn(phase_task_ids["build"], created_ids)

                events_text = (runtime_dir / "events.jsonl").read_text(encoding="utf-8")
                self.assertIn("chat_close_pending_plan_repaired", events_text)
            finally:
                api_main.set_current_workspace(previous_workspace)


if __name__ == "__main__":
    unittest.main()
