import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import api.main as api_main
from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.sqlite_store import SqliteStore
from aiteam.types import (
    AdapterResponse,
    ChannelType,
    Complexity,
    Criticality,
    Role,
    TaskState,
    WorkTask,
)


def _parse_sse_result(response) -> dict:
    text = response.text
    current_event = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: ") and current_event == "result":
            return json.loads(line[6:])
    try:
        return response.json()
    except Exception:
        return {}


class LeadDelegateLspIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "lsp_symbols"},
        )
        self.delegate_emitted = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        is_lead_intake = "Eres Team Lead." in joined and (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        )
        if is_lead_intake and not self.delegate_emitted:
            self.delegate_emitted = True
            return AdapterResponse(
                success=True,
                content=(
                    '[DELEGATE_LSP_IMPACT: "identifica simbolos afectados por el cambio de auth"]\n'
                    "[WAIT_POLICY: best_effort]\n"
                    "[DELEGATE_BUDGET: 2]"
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=24,
            )
        if is_lead_intake and "impacto_lsp_auth" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: aplicar cambios guiados por impacto LSP\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan actualizado con impacto LSP."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=32,
            )
        if "Eres Team Lead." in joined and "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe aplico el impacto LSP antes de construir.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=16,
            )
        if "delegate_lsp_impact" in joined or "lsp_navigator" in joined:
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": "impacto_lsp_auth detectado en auth/service.py y auth/schema.py",
                        "evidence": ["auth/service.py", "auth/schema.py"],
                        "artifacts": [],
                        "risks": ["romper firma de validate_session"],
                        "recommendation": "actualizar simbolos dependientes antes del build",
                        "confidence": 0.82,
                    }
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=24,
            )
        return AdapterResponse(
            success=True,
            content="Build completado usando el informe LSP.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class LeadDelegateBrowserIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "browser_test"},
        )
        self.delegate_emitted = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        is_lead_intake = "Eres Team Lead." in joined and (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        )
        if is_lead_intake and not self.delegate_emitted:
            self.delegate_emitted = True
            return AdapterResponse(
                success=True,
                content=(
                    '[DELEGATE_BROWSER_REPRO: "reproduce el bug visual y resume evidencia"]\n'
                    "[WAIT_POLICY: quorum]\n"
                    "[DELEGATE_BUDGET: 3]"
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=24,
            )
        if is_lead_intake and "captura_browser_home" in joined:
            return AdapterResponse(
                success=True,
                content='[DIRECT_ANSWER: "La verificacion browser confirma el bug visual con captura_browser_home."]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "delegate_browser_repro" in joined or "browser_operator" in joined:
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": "captura_browser_home muestra CTA desplazado",
                        "evidence": ["captura_browser_home", "selector .cta roto"],
                        "artifacts": ["runtime/screenshots/home.png"],
                        "risks": ["regresion responsive"],
                        "recommendation": "ajustar layout antes de cerrar",
                        "confidence": 0.79,
                    }
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=22,
            )
        return AdapterResponse(
            success=True,
            content="Resultado generico.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class LeadUsesRepoReportIntegrationAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_pro",
            provider="openai",
            model="gpt-pro",
            channel=ChannelType.SUBSCRIPTION,
            capabilities={"coding", "reasoning", "analysis", "review", "repo_read"},
        )
        self.delegate_emitted = False

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        is_lead_intake = "Eres Team Lead." in joined and (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        )
        if is_lead_intake and not self.delegate_emitted:
            self.delegate_emitted = True
            return AdapterResponse(
                success=True,
                content=(
                    '[DELEGATE_REPO_SCAN: "encuentra el archivo responsable del bug"]\n'
                    "[WAIT_POLICY: best_effort]\n"
                    "[DELEGATE_BUDGET: 2]"
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=24,
            )
        if is_lead_intake and "modulo_auth.py" in joined:
            return AdapterResponse(
                success=True,
                content='[DIRECT_ANSWER: "El informe delegado apunta a modulo_auth.py como fuente principal del bug."]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=18,
            )
        if "delegate_repo_scan" in joined or "repo_scout" in joined:
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": "modulo_auth.py contiene la validacion inconsistente",
                        "evidence": ["src/modulo_auth.py:88", "tests/test_auth_flow.py:12"],
                        "artifacts": [],
                        "risks": ["romper login heredado"],
                        "recommendation": "ajustar guard clause en modulo_auth.py",
                        "confidence": 0.83,
                    }
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Resultado generico.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=10,
        )


class ReplanAfterDiscoveryIntegrationAdapter(ModelAdapter):
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
            "Eres Team Lead." in joined
            and "Como Team Lead, revisa este informe delegado antes del cierre." in joined
            and "Fase origen: discovery" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[REPLAN]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: inspeccionar dependencias reales\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar con contexto validado\n"
                    "depends_on: [discovery]\n"
                    "phase_id: review\n"
                    "role: REVIEWER\n"
                    "objective: revisar tras discovery preservado\n"
                    "depends_on: [build]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Replan tras discovery."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=58,
            )
        if "Eres Team Lead." in joined and (
            "Lead intake and planning" in joined
            or "TRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN]" in joined
            or "Eres Team Lead senior. Convierte el input" in joined
        ):
            return AdapterResponse(
                success=True,
                content=(
                    "[RUN_MODE: team_decision]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: inspeccionar dependencias reales\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: implementar ajuste inicial\n"
                    "depends_on: [discovery]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan inicial preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=46,
            )
        if "Eres Team Lead." in joined and "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nReplan tras discovery aplicado.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=14,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class ReplanAfterCompletionIgnoredAdapter(ModelAdapter):
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
        if "Eres Team Lead." in joined and (
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
                    "objective: implementar slice minimo\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Plan minimo preparado."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=24,
            )
        if "Eres Team Lead." in joined and "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content=(
                    "[REPLAN]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: no deberia aplicarse ya\n"
                    "phase_id: build\n"
                    "role: ENGINEER\n"
                    "objective: reabrir build\n"
                    "depends_on: [discovery]\n"
                    "[/WORKFLOW_PLAN]\n"
                    "Lead summary:\nEl cierre mantiene el plan original."
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=40,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class ForceGateE2EAdapter(ModelAdapter):
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
            "Eres Team Lead." in joined
            and "Como Team Lead, revisa este informe delegado antes del cierre." in joined
            and "Fase origen: review" in joined
        ):
            return AdapterResponse(
                success=True,
                content='[FORCE_GATE: "build"]\nLa build debe regatearse de nuevo.',
                latency_ms=1,
                input_tokens=10,
                output_tokens=18,
            )
        if "Eres Team Lead." in joined and (
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
        if "Eres Team Lead." in joined and "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Lead summary:\nSe forzo gate adicional sobre build.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=16,
            )
        return AdapterResponse(
            success=True,
            content="Resultado de fase con evidencia textual suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class QuorumE2EAdapter(ModelAdapter):
    def __init__(self, failing_specialists: set[str] | None = None) -> None:
        super().__init__(
            name="openai_api",
            provider="openai",
            model="gpt-cheap",
            channel=ChannelType.API,
            capabilities={
                "analysis",
                "reasoning",
                "coding",
                "test_execute",
                "browser_test",
                "repo_read",
            },
        )
        self.failing_specialists = {
            str(item).strip().lower() for item in (failing_specialists or set()) if str(item).strip()
        }
        self.main_calls = 0

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        prompt_text = str(prompt or "")
        joined = "\n".join(
            str(item.get("content", "")) for item in (messages or []) if isinstance(item, dict)
        )
        for specialist in ("repo_scout", "test_runner", "browser_operator"):
            if specialist in prompt_text.lower() or specialist in joined.lower():
                if specialist in self.failing_specialists:
                    return AdapterResponse(
                        success=False,
                        content="",
                        latency_ms=1,
                        input_tokens=10,
                        output_tokens=0,
                        error=f"{specialist}_failed",
                    )
                return AdapterResponse(
                    success=True,
                    content=json.dumps(
                        {
                            "summary": f"Informe {specialist}",
                            "evidence": [f"{specialist}.ok"],
                            "artifacts": [],
                            "risks": [],
                            "recommendation": "continuar",
                            "confidence": 0.74,
                        }
                    ),
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )
        self.main_calls += 1
        return AdapterResponse(
            success=True,
            content="Respuesta principal con quorum suficiente.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class DirectDelegateIntegrationAdapter(ModelAdapter):
    def __init__(self, *, specialist: str, summary: str, evidence: list[str]) -> None:
        super().__init__(
            name="openai_api",
            provider="openai",
            model="gpt-cheap",
            channel=ChannelType.API,
            capabilities={
                "reasoning",
                "analysis",
                "coding",
                "repo_read",
                "browser_test",
                "lsp_symbols",
                "test_execute",
            },
        )
        self.specialist = specialist
        self.summary = summary
        self.evidence = evidence

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        prompt_text = str(prompt or "")
        joined = "\n".join(
            str(item.get("content", "")) for item in (messages or []) if isinstance(item, dict)
        )
        combined = f"{prompt_text}\n{joined}"
        if self.specialist in combined.lower():
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": self.summary,
                        "evidence": self.evidence,
                        "artifacts": [],
                        "risks": [],
                        "recommendation": "continuar con el informe delegado",
                        "confidence": 0.81,
                    }
                ),
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "Resultado de tu delegación" in combined:
            return AdapterResponse(
                success=True,
                content=f"Lead summary:\nUsando informe delegado: {self.summary}",
                latency_ms=1,
                input_tokens=10,
                output_tokens=18,
            )
        return AdapterResponse(
            success=True,
            content="Respuesta principal.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=12,
        )


class MultiagentE2ETests(unittest.TestCase):
    def _run_chat_case(self, adapter: ModelAdapter, message: str, max_rounds: int = 8) -> tuple[dict, str, str]:
        temp_root = Path.cwd() / ".tmp_e2e_multiagent"
        workspace = temp_root / f"case_{uuid4().hex}"
        previous_workspace = api_main.get_current_workspace()

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
            workspace.mkdir(parents=True, exist_ok=True)
            api_main.set_current_workspace(workspace)
            client = TestClient(api_main.app)
            original_policy_metadata = api_main.build_chat_task_policy_metadata
            with patch.object(api_main, "build_default_orchestrator", side_effect=_factory):
                with patch.object(
                    api_main,
                    "build_chat_task_policy_metadata",
                    side_effect=lambda **kwargs: {
                        **original_policy_metadata(require_execution_plan=False),
                        "require_peer_consultation": False,
                    },
                ):
                    with patch.object(api_main, "_evaluate_phase_evidence_gate", return_value=[]):
                        with patch.object(api_main, "_LEAD_INTAKE_MAX_ROUNDS", 1):
                            response = client.post(
                                "/api/aiteam/chat",
                                json={
                                    "message": message,
                                    "mode": "sprint5",
                                    "max_rounds": max_rounds,
                                    "allow_low_productivity_override": True,
                                    "auto_extend_weak_runs": False,
                                },
                            )
            self.assertEqual(response.status_code, 200)
            payload = _parse_sse_result(response)
            runtime_dir = workspace / "runtime"
            events_text = (runtime_dir / "events.jsonl").read_text(encoding="utf-8")
            tasks_text = json.dumps(
                SqliteStore(runtime_dir / "aiteam.db").load_all_tasks(),
                ensure_ascii=False,
            )
            return payload, events_text, tasks_text
        finally:
            api_main.set_current_workspace(previous_workspace)
            shutil.rmtree(workspace, ignore_errors=True)

    def _run_direct_delegate_case(
        self,
        *,
        intent: str,
        source_phase: str,
        specialist: str,
        summary: str,
        evidence: list[str],
    ) -> tuple[WorkTask, str]:
        temp_root = Path.cwd() / ".tmp_e2e_multiagent"
        workspace_root = temp_root / f"delegate_{uuid4().hex}"
        try:
            runtime_dir = workspace_root / "runtime"
            project_root = workspace_root / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = DirectDelegateIntegrationAdapter(
                specialist=specialist,
                summary=summary,
                evidence=evidence,
            )
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[adapter],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task_root = f"CHAT-{uuid4().hex[:8].upper()}"
            source_task_id = f"{task_root}::{source_phase}"
            source_task = WorkTask(
                task_id=source_task_id,
                title=f"Lead {source_phase}",
                description="Tarea del Team Lead que espera informe delegado.",
                role=Role.TEAM_LEAD,
                state=TaskState.BLOCKED,
                metadata={
                    "interactive_chat": True,
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "required_capabilities": ["reasoning"],
                    "phase": source_phase,
                    "chat_parent": task_root,
                },
            )
            orchestrator.taskboard.add_task(source_task)
            delegate_request = api_main._build_delegate_request(
                intent,
                query="inspecciona y resume",
                wait_policy="best_effort",
                delegate_budget=2,
            )
            api_main._execute_delegate_request(
                orch=orchestrator,
                task_root=task_root,
                workspace=project_root,
                runtime_dir=runtime_dir,
                delegate_request=delegate_request,
                source_task_id=source_task_id,
                source_phase=source_phase,
                delegate_cycle=0,
                rerun_budget=2,
            )
            stored = orchestrator.taskboard.get_task(source_task_id)
            assert stored is not None
            events_text = (runtime_dir / "events.jsonl").read_text(encoding="utf-8")
            return stored, events_text
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    def test_lead_delegates_lsp_navigator_and_uses_report(self) -> None:
        stored, events_text = self._run_direct_delegate_case(
            intent="delegate_lsp_impact",
            source_phase="lead_intake",
            specialist="lsp_navigator",
            summary="impacto_lsp_auth detectado en auth/service.py y auth/schema.py",
            evidence=["auth/service.py", "auth/schema.py"],
        )
        self.assertEqual(stored.state, TaskState.COMPLETED)
        self.assertIn("impacto_lsp_auth", str(stored.metadata.get("result", "")))
        self.assertIn('"intent": "delegate_lsp_impact"', events_text)
        self.assertIn('"specialists": ["lsp_navigator"]', events_text)

    def test_lead_delegates_browser_operator_for_ui_check(self) -> None:
        stored, events_text = self._run_direct_delegate_case(
            intent="delegate_browser_repro",
            source_phase="lead_close",
            specialist="browser_operator",
            summary="captura_browser_home muestra CTA desplazado",
            evidence=["captura_browser_home", "selector .cta roto"],
        )
        self.assertEqual(stored.state, TaskState.COMPLETED)
        self.assertIn("captura_browser_home", str(stored.metadata.get("result", "")))
        self.assertIn('"intent": "delegate_browser_repro"', events_text)

    def test_specialist_report_injected_into_lead_context(self) -> None:
        stored, events_text = self._run_direct_delegate_case(
            intent="delegate_repo_scan",
            source_phase="lead_failure_build",
            specialist="repo_scout",
            summary="modulo_auth.py contiene la validacion inconsistente",
            evidence=["src/modulo_auth.py:88", "tests/test_auth_flow.py:12"],
        )
        self.assertIn("Resultado de tu delegación", stored.description)
        self.assertIn("modulo_auth.py", str(stored.metadata.get("result", "")))
        self.assertIn('"intent": "delegate_repo_scan"', events_text)

    def test_high_criticality_task_blocked_until_quorum(self) -> None:
        temp_root = Path.cwd() / ".tmp_e2e_multiagent"
        workspace_root = temp_root / f"orchestrator_{uuid4().hex}"
        try:
            runtime_dir = workspace_root / "runtime"
            project_root = workspace_root / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = QuorumE2EAdapter(failing_specialists={"test_runner"})
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[adapter],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="E2E-QUORUM-1",
                title="Validacion critica",
                description="Necesita dos informes antes de avanzar.",
                role=Role.ENGINEER,
                criticality=Criticality.HIGH,
                metadata={
                    "required_capabilities": ["test_execute"],
                    "specialist_roster": ["test_runner", "repo_scout"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("E2E-QUORUM-1")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.BLOCKED)
            self.assertEqual(stored.metadata.get("blocked_reason"), "specialist_quorum_not_met")
            self.assertEqual(adapter.main_calls, 0)
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    def test_majority_quorum_met_with_one_failure(self) -> None:
        temp_root = Path.cwd() / ".tmp_e2e_multiagent"
        workspace_root = temp_root / f"orchestrator_{uuid4().hex}"
        try:
            runtime_dir = workspace_root / "runtime"
            project_root = workspace_root / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = QuorumE2EAdapter(failing_specialists={"browser_operator"})
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[adapter],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="E2E-QUORUM-2",
                title="Validacion con mayoria",
                description="Cruza repo, tests y browser.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["repo_read", "test_execute", "browser_testing"],
                    "specialist_roster": ["repo_scout", "test_runner", "browser_operator"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("E2E-QUORUM-2")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            self.assertEqual(
                stored.metadata.get("specialist_quorum_warning"),
                "quorum_met_with_partial_specialist_coverage",
            )
            self.assertGreaterEqual(adapter.main_calls, 1)
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    def test_replan_after_discovery_preserves_discovery(self) -> None:
        payload, events_text, tasks_text = self._run_chat_case(
            ReplanAfterDiscoveryIntegrationAdapter(),
            "Haz discovery y luego replanifica antes de build",
        )
        self.assertIn("discovery", payload.get("phase_task_ids", {}))
        self.assertIn("build", payload.get("phase_task_ids", {}))
        self.assertIn("review", payload.get("phase_task_ids", {}))
        self.assertIn('"source_phase": "lead_report_discovery"', events_text)
        self.assertIn("::discovery", tasks_text)

    def test_replan_after_all_completed_is_ignored(self) -> None:
        replan = api_main._extract_replan_phases_from_outputs(
            {
                "lead_close": (
                    "[REPLAN]\n"
                    "[WORKFLOW_PLAN]\n"
                    "phase_id: discovery\n"
                    "role: RESEARCHER\n"
                    "objective: no deberia aplicarse ya\n"
                    "[/WORKFLOW_PLAN]"
                )
            }
        )
        self.assertIsNotNone(replan)
        assert replan is not None
        source_phase, _phases = replan
        self.assertEqual(api_main._replan_skip_reason(source_phase), "lead_close_completed_plan")

    def test_force_gate_on_completed_phase_reruns_review(self) -> None:
        temp_root = Path.cwd() / ".tmp_e2e_multiagent"
        workspace_root = temp_root / f"forcegate_{uuid4().hex}"
        try:
            runtime_dir = workspace_root / "runtime"
            project_root = workspace_root / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(
                    adapters=[ForceGateE2EAdapter()],
                    policy=build_default_router_policy(),
                ),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="E2E-FORCE-1",
                title="Completed build",
                description="Implementacion ya completada.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )
            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            parent = orchestrator.taskboard.get_task("E2E-FORCE-1")
            assert parent is not None
            parent.metadata["skip_quality_gates"] = False
            parent.metadata["force_gate_requested"] = True
            orchestrator.taskboard.mark_blocked("E2E-FORCE-1", reason="waiting_quality_gates")
            orchestrator._spawn_quality_gates(parent)
            orchestrator.run_until_idle(max_rounds=6)

            review_gate = orchestrator.taskboard.get_task("E2E-FORCE-1::review")
            qa_gate = orchestrator.taskboard.get_task("E2E-FORCE-1::qa")
            assert review_gate is not None
            assert qa_gate is not None
            self.assertEqual(review_gate.state, TaskState.COMPLETED)
            self.assertEqual(qa_gate.state, TaskState.COMPLETED)
            events_text = (runtime_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event_type": "quality_gates_opened"', events_text)
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
