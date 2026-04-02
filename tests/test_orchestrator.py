import tempfile
import unittest
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from aiteam.adapters import (
    ApiAdapter as RealApiAdapter,
    FakeSuccessAdapter,
    SubscriptionAdapter as RealSubscriptionAdapter,
)
from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.router import HybridRouter
from aiteam.types import (
    AdapterResponse,
    ChannelType,
    Complexity,
    Criticality,
    Role,
    RoutingDecision,
    RoutingRequest,
    StreamChunk,
    TaskState,
    WorkTask,
)


class SubscriptionAdapter(FakeSuccessAdapter):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("channel", ChannelType.SUBSCRIPTION)
        super().__init__(*args, **kwargs)


class ApiAdapter(FakeSuccessAdapter):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("channel", ChannelType.API)
        super().__init__(*args, **kwargs)


class FailureThenLeadClarifyAdapter(ModelAdapter):
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
            text_parts.extend(str(item.get("content", "")) for item in messages if isinstance(item, dict))
        joined = "\n".join(text_parts)
        if "Como Team Lead, interviene tras un fallo de fase" in joined:
            return AdapterResponse(
                success=True,
                content='[CLARIFY: "¿Quieres que reoriente la corrida o solo documente el fallo?"]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "FORCE FAIL CHECKPOINT" in joined:
            return AdapterResponse(
                success=False,
                content="",
                error="forced_checkpoint_failure",
                latency_ms=1,
                input_tokens=10,
                output_tokens=0,
            )
        return AdapterResponse(
            success=True,
            content="Respuesta de prueba",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class DeliberativeReportCheckpointAdapter(ModelAdapter):
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
                content='[CLARIFY: "¿Prefieres una recomendación conservadora o agresiva?"]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        if "Lead synthesis and response" in joined:
            return AdapterResponse(
                success=True,
                content="Cierre del Team Lead.",
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Informe delegado con opciones y tradeoffs.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SensitivePreflightCheckpointAdapter(ModelAdapter):
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
                content='[CLARIFY: "¿Autorizas ejecutar esta fase sensible ahora mismo?"]',
                latency_ms=1,
                input_tokens=10,
                output_tokens=20,
            )
        return AdapterResponse(
            success=True,
            content="Implementacion o revision completada.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SpecialistJsonAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_api",
            provider="openai",
            model="gpt-cheap",
            channel=ChannelType.API,
            capabilities={"analysis", "reasoning", "browser_test"},
        )

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        return AdapterResponse(
            success=True,
            content=json.dumps(
                {
                    "summary": "UI reproducida con error de selector",
                    "evidence": ["selector .cta no existe"],
                    "artifacts": ["runtime/screenshots/home.png"],
                    "risks": ["flaky test"],
                    "recommendation": "usar data-testid",
                    "confidence": 0.77,
                }
            ),
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SpecialistPrefetchAdapter(ModelAdapter):
    def __init__(self) -> None:
        super().__init__(
            name="openai_api",
            provider="openai",
            model="gpt-cheap",
            channel=ChannelType.API,
            capabilities={"analysis", "reasoning", "coding", "test_execute"},
        )
        self.calls: list[dict[str, object]] = []

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        prompt_text = str(prompt or "")
        joined_messages = "\n".join(
            str(item.get("content", "")) for item in (messages or []) if isinstance(item, dict)
        )
        self.calls.append(
            {
                "prompt": prompt_text,
                "messages": joined_messages,
            }
        )
        if "Specialist precheck:" in prompt_text:
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": "Los tests relevantes apuntan a un caso borde de validacion.",
                        "evidence": ["tests/test_router.py::test_x"],
                        "artifacts": [],
                        "risks": ["posible regresion de budget"],
                        "recommendation": "mantener cobertura y ejecutar smoke",
                        "confidence": 0.71,
                    }
                ),
                latency_ms=1,
                input_tokens=8,
                output_tokens=12,
            )
        return AdapterResponse(
            success=True,
            content="Respuesta principal con contexto de especialista.",
            latency_ms=1,
            input_tokens=11,
            output_tokens=21,
        )


class ContextCuratorAvailabilityAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        available_after: int = 1,
        role_targets: set[str] | None = None,
    ) -> None:
        super().__init__(
            name="context_curator_api",
            provider="openai",
            model="gpt-cheap",
            channel=ChannelType.API,
            capabilities={"analysis", "reasoning", "repo_read"},
            role_targets=role_targets,
        )
        self.available_after = max(0, int(available_after))
        self.available_calls = 0
        self.invoke_calls = 0

    def available(self) -> bool:
        self.available_calls += 1
        return self.available_calls > self.available_after

    def invoke(self, prompt, messages=None, tools=None):
        self.invoke_calls += 1
        prompt_text = str(prompt or "")
        if "Specialist precheck:" in prompt_text:
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": "Contexto compacto listo para continuar la fase.",
                        "evidence": ["workflow_state compactado"],
                        "artifacts": [],
                        "risks": [],
                        "recommendation": "usar el resumen y continuar",
                        "confidence": 0.74,
                    }
                ),
                latency_ms=1,
                input_tokens=8,
                output_tokens=12,
            )
        return AdapterResponse(
            success=True,
            content="Respuesta principal tras prefetch de contexto.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class PeerDiversityCaptureAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        capabilities: set[str],
        record: list[dict[str, str]],
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=ChannelType.SUBSCRIPTION,
            capabilities=capabilities,
        )
        self.record = record

    def available(self) -> bool:
        return True

    def invoke(self, prompt, messages=None, tools=None):
        text_parts = [str(prompt or "")]
        if isinstance(messages, list):
            text_parts.extend(
                str(item.get("content", "")) for item in messages if isinstance(item, dict)
            )
        joined = "\n".join(text_parts)
        if "Consulta para" in joined:
            round_label = "round2" if "Modo: round2" in joined else "round1"
            self.record.append(
                {
                    "provider": self.provider,
                    "round": round_label,
                }
            )
            content = f"{self.provider} peer input"
        else:
            content = f"{self.provider} main response"
        return AdapterResponse(
            success=True,
            content=content,
            latency_ms=1,
            input_tokens=10,
            output_tokens=20,
        )


class SpecialistQuorumAdapter(ModelAdapter):
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

    @staticmethod
    def _extract_specialist_name(messages) -> str:
        joined_messages = "\n".join(
            str(item.get("content", "")) for item in (messages or []) if isinstance(item, dict)
        )
        marker = "Especializacion activa:"
        if marker not in joined_messages:
            return ""
        suffix = joined_messages.split(marker, 1)[1]
        if "(" not in suffix or ")" not in suffix:
            return ""
        return suffix.split("(", 1)[1].split(")", 1)[0].strip().lower()

    def invoke(self, prompt, messages=None, tools=None):
        prompt_text = str(prompt or "")
        if "Specialist precheck:" in prompt_text:
            specialist_name = self._extract_specialist_name(messages)
            if specialist_name in self.failing_specialists:
                return AdapterResponse(
                    success=False,
                    content="",
                    latency_ms=1,
                    input_tokens=8,
                    output_tokens=0,
                    error=f"{specialist_name}_failed",
                )
            return AdapterResponse(
                success=True,
                content=json.dumps(
                    {
                        "summary": f"Informe de {specialist_name or 'specialist'}",
                        "evidence": [f"evidence:{specialist_name or 'unknown'}"],
                        "artifacts": [],
                        "risks": [],
                        "recommendation": "continuar",
                        "confidence": 0.72,
                    }
                ),
                latency_ms=1,
                input_tokens=8,
                output_tokens=12,
            )
        self.main_calls += 1
        return AdapterResponse(
            success=True,
            content="Respuesta principal tras quorum de especialistas.",
            latency_ms=1,
            input_tokens=10,
            output_tokens=18,
        )


class WeakSpecialistReportAdapter(SpecialistQuorumAdapter):
    def __init__(self, weak_specialists: set[str] | None = None) -> None:
        super().__init__(failing_specialists=set())
        self.weak_specialists = {
            str(item).strip().lower() for item in (weak_specialists or set()) if str(item).strip()
        }

    def invoke(self, prompt, messages=None, tools=None):
        prompt_text = str(prompt or "")
        if "Specialist precheck:" in prompt_text:
            specialist_name = self._extract_specialist_name(messages)
            if specialist_name in self.weak_specialists:
                return AdapterResponse(
                    success=True,
                    content=json.dumps(
                        {
                            "summary": f"Informe debil de {specialist_name}",
                            "evidence": [],
                            "artifacts": [],
                            "risks": [],
                            "recommendation": "",
                            "confidence": 0.41,
                        }
                    ),
                    latency_ms=1,
                    input_tokens=8,
                    output_tokens=10,
                )
        return super().invoke(prompt, messages=messages, tools=tools)


class RecordingRetryMCPManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_healthy(
        self,
        *,
        retry_unhealthy: bool = True,
        retry_after_seconds: int = 900,
        timeout: int = 10,
    ) -> list[str]:
        self.calls.append(
            {
                "retry_unhealthy": retry_unhealthy,
                "retry_after_seconds": retry_after_seconds,
                "timeout": timeout,
            }
        )
        return ["filesystem_mcp"] if retry_unhealthy else []


class OrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_tempdir = tempfile.tempdir
        self._previous_temporary_directory = tempfile.TemporaryDirectory
        self._local_temp_root = Path.cwd() / ".tmp_test_orchestrator"
        self._local_temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(self._local_temp_root)

        class _WorkspaceTemporaryDirectory:
            def __init__(
                inner_self,
                suffix: str | None = None,
                prefix: str | None = None,
                dir: str | Path | None = None,
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

    def test_infers_tool_specialist_metadata_for_tool_heavy_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-cheap",
                        capabilities={"browser_test", "reasoning"},
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="chat_root::qa",
                title="Reproducir bug de UI",
                description="Usa browser y resume pasos.",
                role=Role.QA,
                metadata={"required_capabilities": ["browser_testing"]},
            )

            orchestrator._ensure_tool_specialist_metadata(task)

            self.assertEqual(task.metadata["tool_specialist"], "browser_operator")
            self.assertEqual(
                task.metadata["tool_specialist_decision_scope"],
                "operate_tools_and_report_only",
            )
            self.assertTrue(task.metadata["tool_specialist_economic_routing"])
            self.assertTrue(task.metadata["tool_specialist_inferred"])

    def test_applies_tool_rewiring_hints_from_catalog_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-cheap",
                        capabilities={"reasoning", "analysis"},
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="chat_root::review",
                title="Analizar seguridad",
                description="Necesita herramienta de seguridad pero con fallback.",
                role=Role.REVIEWER,
                metadata={"required_capabilities": ["security_scan", "external_mcp"]},
            )

            with patch.object(
                orchestrator.tool_integrator,
                "suggest_requirements",
                return_value=[
                    {
                        "name": "semgrep_security_skill",
                        "category": "skill",
                        "replacement_for": "semgrep_mcp",
                    }
                ],
            ):
                orchestrator._ensure_tool_specialist_metadata(task)

            self.assertTrue(task.metadata["tool_rewiring_active"])
            self.assertEqual(task.metadata["tool_rewiring_preferred_specialist"], "skill_worker")
            self.assertEqual(task.metadata["tool_specialist"], "skill_worker")
            self.assertTrue(task.metadata["tool_rewiring_suppress_mcp_operator"])
            self.assertIn("semgrep_security_skill", list(task.metadata.get("tool_rewiring_candidates", []) or []))

    def test_specialist_prefetch_uses_mcp_health_retry_for_mcp_operator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api",
                        provider="openai",
                        model="gpt-cheap",
                        capabilities={"reasoning", "analysis"},
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            mcp_manager = RecordingRetryMCPManager()
            orchestrator.mcp_manager = mcp_manager
            task = WorkTask(
                task_id="chat_root::review_mcp",
                title="Auditar integracion MCP",
                description="Necesita comprobar servidores externos MCP.",
                role=Role.REVIEWER,
                metadata={"required_capabilities": ["external_mcp"]},
            )
            captured_requests: list[RoutingRequest] = []

            def _capture_route(request, prompt, task_id="", messages=None, tools=None, on_chunk=None):
                if isinstance(request, RoutingRequest):
                    captured_requests.append(request)
                return RoutingDecision(
                    success=True,
                    provider="openai",
                    model="gpt-cheap",
                    channel=ChannelType.API,
                    reason="captured",
                    response=AdapterResponse(
                        success=True,
                        content=json.dumps(
                            {
                                "summary": "MCP recuperado y disponible para operar.",
                                "evidence": ["filesystem_mcp healthy tras retry"],
                                "artifacts": [],
                                "risks": [],
                                "recommendation": "continuar",
                                "confidence": 0.81,
                            }
                        ),
                        latency_ms=1,
                        input_tokens=8,
                        output_tokens=12,
                    ),
                )

            with patch.object(router, "route_and_invoke", side_effect=_capture_route):
                context = orchestrator._collect_specialist_prefetch_context(task)

            self.assertTrue(mcp_manager.calls)
            self.assertTrue(bool(mcp_manager.calls[-1].get("retry_unhealthy")))
            applied = dict(task.metadata.get("specialist_roster_applied", {}) or {})
            self.assertIn("mcp_operator", list(applied.get("specialist_roster", []) or []))
            mcp_requests = [
                request
                for request in captured_requests
                if isinstance(request, RoutingRequest) and request.tool_specialist == "mcp_operator"
            ]
            self.assertTrue(mcp_requests)
            self.assertIn("mcp_operator", context)

    def test_persists_structured_specialist_report_in_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[SpecialistJsonAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="chat_root::qa",
                title="Reproducir bug de UI",
                description="Usa browser y resume pasos.",
                role=Role.QA,
                metadata={
                    "required_capabilities": ["browser_testing"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("chat_root::qa")
            assert stored is not None
            reports = list(stored.metadata.get("specialist_reports", []) or [])
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].get("specialist"), "browser_operator")
            self.assertEqual(reports[0].get("provider"), "openai")
            self.assertEqual(reports[0].get("model"), "gpt-cheap")
            self.assertEqual(reports[0].get("recommendation"), "usar data-testid")
            self.assertEqual(int(reports[0].get("tokens_used", 0)), 30)
            self.assertEqual(reports[0].get("report_version"), "specialist_report_v1")
            self.assertEqual(reports[0].get("validation_status"), "valid")
            self.assertEqual(reports[0].get("validation_errors"), [])

            events_file = runtime_dir / "events.jsonl"
            self.assertTrue(events_file.exists())
            events_text = events_file.read_text(encoding="utf-8")
            self.assertIn('"event_type": "specialist_report_parsed"', events_text)
            self.assertIn('"validation_status": "valid"', events_text)

    def test_select_specialists_prefetches_reports_before_main_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = SpecialistPrefetchAdapter()
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::build",
                title="Endurecer validacion",
                description="Implementa ajuste y valida con tests.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["test_execute"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("root::build")
            assert stored is not None
            self.assertGreaterEqual(len(adapter.calls), 2)
            precheck_indices = [
                idx
                for idx, call in enumerate(adapter.calls)
                if "Specialist precheck:" in str(call.get("prompt", ""))
            ]
            self.assertTrue(precheck_indices)
            self.assertLess(precheck_indices[0], len(adapter.calls) - 1)
            self.assertIn(
                "Informes compactos de especialistas delegados",
                str(adapter.calls[-1].get("messages", "")),
            )
            applied = dict(stored.metadata.get("specialist_roster_applied", {}) or {})
            self.assertTrue(applied)
            self.assertIn("test_runner", list(applied.get("specialist_roster", []) or []))
            prefetch_reports = list(stored.metadata.get("specialist_prefetch_reports", []) or [])
            self.assertEqual(len(prefetch_reports), 1)
            self.assertEqual(prefetch_reports[0].get("specialist"), "test_runner")

    def test_specialist_prefetch_retries_on_transient_unavailability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = ContextCuratorAvailabilityAdapter(available_after=1)
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::plan_research",
                title="Planificar investigacion",
                description="Compacta el contexto antes de seguir.",
                role=Role.SCOUT,
                metadata={"context_curator_requested": True},
            )

            context = orchestrator._collect_specialist_prefetch_context(task)

            self.assertGreaterEqual(adapter.available_calls, 2)
            self.assertEqual(adapter.invoke_calls, 1)
            self.assertIn("context_curator", context)
            quorum_result = dict(task.metadata.get("specialist_quorum_result", {}) or {})
            self.assertTrue(quorum_result.get("quorum_met"))
            self.assertEqual(
                list(quorum_result.get("received_specialists", []) or []),
                ["context_curator"],
            )

    def test_context_curator_prefetch_degrades_gracefully_when_no_adapter_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            unavailable_curator = ContextCuratorAvailabilityAdapter(
                available_after=99,
                role_targets={"scout"},
            )
            engineer_adapter = ApiAdapter(
                name="engineer_api",
                provider="openai",
                model="gpt-engineer",
                capabilities={"coding", "reasoning", "analysis"},
                role_targets={"engineer"},
                response_content="Implementacion principal completada.",
            )
            router = HybridRouter(
                adapters=[unavailable_curator, engineer_adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::build_with_context_pressure",
                title="Implementar ajuste principal",
                description="Necesita continuar aunque el compactado de contexto no este disponible.",
                role=Role.ENGINEER,
                metadata={
                    "context_curator_recommended": True,
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("root::build_with_context_pressure")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            degraded = list(stored.metadata.get("specialist_prefetch_degraded", []) or [])
            self.assertEqual(len(degraded), 1)
            self.assertEqual(degraded[0].get("specialist"), "context_curator")
            self.assertEqual(degraded[0].get("reason"), "no_eligible_adapter")
            quorum_result = dict(stored.metadata.get("specialist_quorum_result", {}) or {})
            self.assertTrue(quorum_result.get("quorum_met"))
            self.assertEqual(int(quorum_result.get("responses_required", 0)), 0)
            self.assertEqual(list(quorum_result.get("missing_specialists", []) or []), [])

    def test_specialist_quorum_all_blocks_main_task_when_missing_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = SpecialistQuorumAdapter(failing_specialists={"test_runner"})
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::critical_build",
                title="Ejecutar validacion critica",
                description="Necesita doble evidencia antes de avanzar.",
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

            stored = orchestrator.taskboard.get_task("root::critical_build")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.BLOCKED)
            self.assertEqual(stored.metadata.get("blocked_reason"), "specialist_quorum_not_met")
            quorum_result = dict(stored.metadata.get("specialist_quorum_result", {}) or {})
            self.assertFalse(quorum_result.get("quorum_met"))
            self.assertEqual(int(quorum_result.get("responses_received", 0)), 1)
            self.assertEqual(int(quorum_result.get("responses_required", 0)), 2)
            self.assertIn("test_runner", list(quorum_result.get("missing_specialists", []) or []))
            self.assertEqual(adapter.main_calls, 0)
            events = orchestrator.event_logger.recent_events(hours=1)
            quorum_events = [
                item for item in events if item.get("event_type") == "specialist_quorum_result"
            ]
            self.assertTrue(quorum_events)
            payload = quorum_events[-1].get("payload", {}) or {}
            self.assertFalse(payload.get("quorum_met"))
            self.assertEqual(int(payload.get("responses_received", 0)), 1)
            self.assertEqual(int(payload.get("responses_required", 0)), 2)

    def test_specialist_quorum_majority_allows_main_task_with_partial_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = SpecialistQuorumAdapter(failing_specialists={"browser_operator"})
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::build_with_majority",
                title="Endurecer pipeline",
                description="Cruza repo, tests y superficie browser.",
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

            stored = orchestrator.taskboard.get_task("root::build_with_majority")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            quorum_result = dict(stored.metadata.get("specialist_quorum_result", {}) or {})
            self.assertTrue(quorum_result.get("quorum_met"))
            self.assertEqual(quorum_result.get("quorum_mode"), "majority")
            self.assertEqual(int(quorum_result.get("responses_received", 0)), 2)
            self.assertEqual(int(quorum_result.get("responses_required", 0)), 2)
            self.assertIn("browser_operator", list(quorum_result.get("missing_specialists", []) or []))
            self.assertEqual(
                stored.metadata.get("specialist_quorum_warning"),
                "quorum_met_with_partial_specialist_coverage",
            )
            self.assertGreaterEqual(adapter.main_calls, 1)

    def test_specialist_quorum_zero_does_not_block_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = SpecialistQuorumAdapter()
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::light_task",
                title="Responder con recomendacion",
                description="No requiere herramientas especializadas.",
                role=Role.RESEARCHER,
                metadata={
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("root::light_task")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            quorum_result = dict(stored.metadata.get("specialist_quorum_result", {}) or {})
            self.assertTrue(quorum_result.get("quorum_met"))
            self.assertEqual(int(quorum_result.get("responses_required", 0)), 0)
            self.assertEqual(int(quorum_result.get("responses_received", 0)), 0)
            self.assertEqual(
                list(stored.metadata.get("specialist_prefetch_reports", []) or []),
                [],
            )

    def test_specialist_quorum_ignores_reports_without_operational_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            adapter = WeakSpecialistReportAdapter(weak_specialists={"test_runner"})
            router = HybridRouter(
                adapters=[adapter],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="root::critical_signal_check",
                title="Validacion critica con evidencia de especialistas",
                description="No debe avanzar si uno de los informes no trae evidencia operativa.",
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

            stored = orchestrator.taskboard.get_task("root::critical_signal_check")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.BLOCKED)
            self.assertEqual(stored.metadata.get("blocked_reason"), "specialist_quorum_not_met")
            quorum_result = dict(stored.metadata.get("specialist_quorum_result", {}) or {})
            self.assertFalse(quorum_result.get("quorum_met"))
            self.assertEqual(int(quorum_result.get("responses_received", 0)), 1)
            self.assertEqual(int(quorum_result.get("responses_required", 0)), 2)
            self.assertIn("test_runner", list(quorum_result.get("invalid_specialists", []) or []))
            self.assertIn("test_runner", list(quorum_result.get("missing_specialists", []) or []))
            self.assertEqual(adapter.main_calls, 0)

    def test_specialist_roster_preferred_tool_tier_drives_main_routing_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    ApiAdapter(
                        name="openai_api_budget",
                        provider="openai",
                        model="gpt-budget",
                        capabilities={"test_execute"},
                        cost_tier=1,
                        response_content="budget",
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            captured_requests: list[dict[str, object]] = []

            def _capture_route(request, prompt, task_id="", messages=None, tools=None, on_chunk=None):
                captured_requests.append(
                    {
                        "request": request,
                        "prompt": str(prompt or ""),
                        "task_id": str(task_id or ""),
                    }
                )
                return RoutingDecision(
                    success=True,
                    provider="openai",
                    model="gpt-budget",
                    channel=ChannelType.API,
                    reason="captured",
                    response=AdapterResponse(
                        success=True,
                        content="Respuesta principal con tier del roster.",
                        latency_ms=1,
                        input_tokens=8,
                        output_tokens=12,
                    ),
                )

            task = WorkTask(
                task_id="root::specialist_roster_routing",
                title="Ejecutar validacion con roster",
                description="La tarea principal debe heredar el tier preferido del roster.",
                role=Role.ENGINEER,
                metadata={
                    "required_capabilities": ["test_execute"],
                    "tool_specialist": "test_runner",
                    "tool_specialist_default_tier": "budget_api",
                    "tool_specialist_economic_routing": True,
                    "_specialist_prefetch_done": True,
                    "specialist_roster_applied": {
                        "specialist_roster": ["test_runner", "repo_scout"],
                        "specialist_roster_preferred_tool_tier": "advanced_api",
                    },
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                },
            )

            with patch.object(router, "route_and_invoke", side_effect=_capture_route):
                orchestrator.submit_task(task)
                orchestrator.run_until_idle(max_rounds=2)

            stored = orchestrator.taskboard.get_task("root::specialist_roster_routing")
            assert stored is not None
            self.assertEqual(stored.state, TaskState.COMPLETED)
            main_requests = [
                item["request"]
                for item in captured_requests
                if isinstance(item.get("request"), RoutingRequest)
                and item["request"].role == Role.ENGINEER
                and item["request"].tool_specialist == "test_runner"
            ]
            self.assertTrue(main_requests)
            self.assertEqual(main_requests[-1].preferred_tool_tier, "advanced_api")

    def test_sensitive_chat_phase_spawns_lead_preflight_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[SensitivePreflightCheckpointAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            sensitive_task = WorkTask(
                task_id="CHAT-SENSITIVE::build",
                title="Sensitive build",
                description="Implementa un cambio con comandos sensibles.",
                role=Role.ENGINEER,
                complexity=Complexity.HIGH,
                criticality=Criticality.HIGH,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "build",
                    "chat_parent": "CHAT-SENSITIVE",
                    "lead_run_mode": "standard",
                    "require_execution_plan": True,
                },
            )

            orchestrator.submit_task(sensitive_task)
            orchestrator.run_until_idle(max_rounds=4)

            checkpoint = orchestrator.taskboard.get_task(
                "CHAT-SENSITIVE::lead_preflight_build"
            )
            build_task = orchestrator.taskboard.get_task("CHAT-SENSITIVE::build")
            assert checkpoint is not None
            assert build_task is not None
            self.assertEqual(checkpoint.role, Role.TEAM_LEAD)
            self.assertEqual(checkpoint.state, TaskState.WAITING_USER)
            self.assertEqual(
                checkpoint.metadata.get("clarify_question"),
                "¿Autorizas ejecutar esta fase sensible ahora mismo?",
            )
            self.assertIn(checkpoint.task_id, build_task.dependencies)
            self.assertEqual(build_task.state, TaskState.PENDING)
            self.assertEqual(
                build_task.metadata.get("lead_preflight_checkpoint_id"),
                checkpoint.task_id,
            )
            self.assertIn(
                "require_execution_plan",
                build_task.metadata.get("lead_preflight_sensitive_reasons", []),
            )

    def test_sim_mode_skips_missing_execution_plan_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            router = HybridRouter(
                adapters=[SensitivePreflightCheckpointAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
            )

            task = WorkTask(
                task_id="SIM-PLAN-1",
                title="Sim mode task",
                description="Resume el siguiente paso sin ejecutar comandos.",
                role=Role.TEAM_LEAD,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "require_execution_plan": True,
                },
            )

            with patch.dict("os.environ", {"AITEAM_SIM_MODE": "1"}, clear=False):
                orchestrator.submit_task(task)
                orchestrator.run_until_idle(max_rounds=4)

            stored = orchestrator.taskboard.get_task("SIM-PLAN-1")
            assert stored is not None
            self.assertNotEqual(stored.state, TaskState.FAILED)
            self.assertNotIn(
                "missing_execution_plan_required",
                str(stored.metadata.get("error", "")),
            )

    def test_non_sensitive_chat_phase_does_not_spawn_lead_preflight_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[SensitivePreflightCheckpointAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            normal_task = WorkTask(
                task_id="CHAT-NONSENSITIVE::review",
                title="Normal review",
                description="Revision ligera.",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["review"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "review",
                    "chat_parent": "CHAT-NONSENSITIVE",
                    "lead_run_mode": "standard",
                },
            )

            orchestrator.submit_task(normal_task)
            orchestrator.run_until_idle(max_rounds=3)

            self.assertIsNone(
                orchestrator.taskboard.get_task("CHAT-NONSENSITIVE::lead_preflight_review")
            )

    def test_deliberative_run_spawns_lead_report_checkpoint_and_blocks_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[DeliberativeReportCheckpointAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            delegated = WorkTask(
                task_id="CHAT-TEAM-DECISION::review_options",
                title="Review options",
                description="Evaluar opciones del equipo.",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["review"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "review_options",
                    "chat_parent": "CHAT-TEAM-DECISION",
                    "lead_run_mode": "team_decision",
                },
            )
            lead_close = WorkTask(
                task_id="CHAT-TEAM-DECISION::lead_close",
                title="Lead synthesis and response",
                description="Lead synthesis and response",
                role=Role.TEAM_LEAD,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                dependencies=["CHAT-TEAM-DECISION::review_options"],
                metadata={
                    "required_capabilities": ["reasoning"],
                    "interactive_chat": True,
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "phase": "lead_close",
                    "chat_parent": "CHAT-TEAM-DECISION",
                    "lead_run_mode": "team_decision",
                },
            )

            orchestrator.submit_task(delegated)
            orchestrator.submit_task(lead_close)
            orchestrator.run_until_idle(max_rounds=4)

            checkpoint = orchestrator.taskboard.get_task(
                "CHAT-TEAM-DECISION::lead_report_review_options"
            )
            close_task = orchestrator.taskboard.get_task("CHAT-TEAM-DECISION::lead_close")
            assert checkpoint is not None
            assert close_task is not None
            self.assertEqual(checkpoint.role, Role.TEAM_LEAD)
            self.assertEqual(checkpoint.state, TaskState.WAITING_USER)
            self.assertEqual(
                checkpoint.metadata.get("clarify_question"),
                "¿Prefieres una recomendación conservadora o agresiva?",
            )
            self.assertIn(checkpoint.task_id, close_task.dependencies)
            self.assertIn(
                checkpoint.task_id,
                close_task.metadata.get("lead_report_checkpoint_dependencies", []),
            )
            self.assertEqual(close_task.state, TaskState.PENDING)

    def test_standard_run_does_not_spawn_lead_report_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[DeliberativeReportCheckpointAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            delegated = WorkTask(
                task_id="CHAT-STANDARD::review",
                title="Review",
                description="Revision normal.",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["review"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "review",
                    "chat_parent": "CHAT-STANDARD",
                    "lead_run_mode": "standard",
                },
            )

            orchestrator.submit_task(delegated)
            orchestrator.run_until_idle(max_rounds=3)

            self.assertIsNone(
                orchestrator.taskboard.get_task("CHAT-STANDARD::lead_report_review")
            )

    def test_chat_phase_failure_spawns_lead_checkpoint_that_can_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[FailureThenLeadClarifyAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            failed_task = WorkTask(
                task_id="CHAT-FAIL-CHECKPOINT::build",
                title="Build checkpoint test",
                description="FORCE FAIL CHECKPOINT",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["coding"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "build",
                    "chat_parent": "CHAT-FAIL-CHECKPOINT",
                },
            )

            orchestrator.submit_task(failed_task)
            orchestrator.run_until_idle(max_rounds=4)

            failed = orchestrator.taskboard.get_task("CHAT-FAIL-CHECKPOINT::build")
            checkpoint = orchestrator.taskboard.get_task(
                "CHAT-FAIL-CHECKPOINT::lead_failure_build"
            )
            assert failed is not None
            assert checkpoint is not None
            self.assertEqual(failed.state, TaskState.FAILED)
            self.assertEqual(
                failed.metadata.get("lead_failure_checkpoint_id"),
                "CHAT-FAIL-CHECKPOINT::lead_failure_build",
            )
            self.assertEqual(checkpoint.role, Role.TEAM_LEAD)
            self.assertEqual(checkpoint.state, TaskState.WAITING_USER)
            self.assertEqual(
                checkpoint.metadata.get("clarify_question"),
                "¿Quieres que reoriente la corrida o solo documente el fallo?",
            )

    def test_non_chat_failure_does_not_spawn_lead_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[FailureThenLeadClarifyAdapter()],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            failed_task = WorkTask(
                task_id="NONCHAT-FAIL-1",
                title="Non chat failing task",
                description="FORCE FAIL CHECKPOINT",
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

            orchestrator.submit_task(failed_task)
            orchestrator.run_until_idle(max_rounds=4)

            failed = orchestrator.taskboard.get_task("NONCHAT-FAIL-1")
            assert failed is not None
            self.assertEqual(failed.state, TaskState.FAILED)
            self.assertIsNone(
                orchestrator.taskboard.get_task("NONCHAT-FAIL-1::lead_failure_engineer")
            )

    def test_workflow_state_updates_phase_context_summaries_for_chat_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            orchestrator._update_workflow_state(
                "CHAT-CTX-1",
                "build",
                "Implementado login flow con cambios en auth.py y evidencia compacta para browser.",
            )

            ws = orchestrator._get_workflow_state("CHAT-CTX-1")
            self.assertIn("build", ws.get("phase_context_summaries", {}))
            self.assertTrue(bool(str(ws.get("chat_context_summary", "") or "").strip()))
            self.assertTrue(
                bool(
                    orchestrator.context_curator.load_chat_context(
                        "CHAT-CTX-1",
                        project_key=str(project_root.resolve()),
                    ).get("working_set", [])
                )
            )

    def test_context_pressure_updates_from_delegate_batches_and_invalidations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            ws = orchestrator._get_workflow_state("CHAT-CTX-PRESSURE")
            ws["continuation_requested"] = True
            ws["continuation_snapshot"] = "build:failed, qa:pending"
            ws["delegate_batches"] = [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}]
            ws["phase_context_summaries"] = {
                "discovery": "resumen 1",
                "build": "resumen 2",
                "review": "resumen 3",
                "qa": "resumen 4",
            }
            orchestrator.context_curator.remember_invalidation(
                project_key=str(project_root.resolve()),
                chat_root="CHAT-CTX-PRESSURE",
                reason="replan_partial",
                affected_phases=["build"],
                source_task_ids=["CHAT-CTX-PRESSURE::lead_failure_build"],
            )

            metadata = {"required_capabilities": ["review"]}
            pressure = orchestrator._refresh_context_pressure(
                "CHAT-CTX-PRESSURE",
                metadata=metadata,
            )

            self.assertEqual(pressure["level"], "high")
            self.assertTrue(metadata.get("context_curator_recommended"))
            self.assertTrue(bool(ws.get("context_pressure", {})))
            self.assertEqual(ws.get("context_pressure", {}).get("level"), "high")

    def test_context_compaction_priority_boost_promotes_curator_even_with_low_base_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            ws = orchestrator._get_workflow_state("CHAT-CTX-ROI")
            ws["phase_outputs"] = {
                "discovery": "D" * 1100,
                "build": "B" * 1500,
            }
            ws["project_context_summary"] = "Proyecto corto"
            ws["chat_context_summary"] = "Chat corto"
            ws["phase_context_summaries"] = {
                "discovery": "Resumen discovery",
                "build": "Resumen build",
            }

            metadata = {"required_capabilities": ["review"]}
            pressure = orchestrator._refresh_context_pressure(
                "CHAT-CTX-ROI",
                metadata=metadata,
            )

            self.assertEqual(pressure.get("level"), "low")
            self.assertTrue(metadata.get("context_curator_recommended"))
            self.assertTrue(metadata.get("context_compaction_priority_boost"))
            self.assertGreater(int(metadata.get("estimated_context_tokens_saved", 0)), 300)
            self.assertEqual(
                str((pressure.get("context_compaction", {}) or {}).get("level", "")),
                "high",
            )

    def test_dependency_output_context_prefers_compacted_phase_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            orchestrator = AITeamOrchestrator(
                router=HybridRouter(adapters=[], policy=build_default_router_policy()),
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            dep = WorkTask(
                task_id="CHAT-CTX-2::build",
                title="Build",
                description="Implementa login",
                role=Role.ENGINEER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={"phase": "build", "result": "RAW OUTPUT MUY LARGO " * 80},
            )
            dep.state = TaskState.COMPLETED
            orchestrator.taskboard.add_task(dep)
            orchestrator._update_workflow_state(
                "CHAT-CTX-2",
                "build",
                "Resumen build: auth.py actualizado, flujo login reparado, falta smoke browser.",
            )

            task = WorkTask(
                task_id="CHAT-CTX-2::review",
                title="Review",
                description="Revisa",
                role=Role.REVIEWER,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                dependencies=["CHAT-CTX-2::build"],
                metadata={"phase": "review"},
            )

            context = orchestrator._build_dependency_output_context(task)

            self.assertIn("Resumen build:", context)
            self.assertNotIn("RAW OUTPUT MUY LARGO RAW OUTPUT MUY LARGO RAW OUTPUT MUY LARGO", context)

    def test_lead_close_can_pause_run_with_clarify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro",
                        provider="openai",
                        model="gpt-pro",
                        capabilities={"reasoning", "analysis"},
                        response_content='[CLARIFY: "¿Debo priorizar velocidad o calidad?"]',
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="CHAT-LEAD-CLOSE::lead_close",
                title="Lead synthesis and response",
                description="Sintetiza y decide el siguiente paso.",
                role=Role.TEAM_LEAD,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["reasoning"],
                    "interactive_chat": True,
                    "skip_quality_gates": True,
                    "phase": "lead_close",
                    "chat_parent": "CHAT-LEAD-CLOSE",
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=3)

            paused = orchestrator.taskboard.get_task("CHAT-LEAD-CLOSE::lead_close")
            assert paused is not None
            self.assertEqual(paused.state, TaskState.WAITING_USER)
            self.assertEqual(
                paused.metadata.get("clarify_question"),
                "¿Debo priorizar velocidad o calidad?",
            )

    def test_lead_close_messages_include_run_health_report(self) -> None:
        captured: dict[str, object] = {}

        class CaptureLeadCloseAdapter(SubscriptionAdapter):
            def invoke(
                self,
                prompt: str,
                messages: list[dict[str, str]] | None = None,
            ) -> AdapterResponse:
                captured["messages"] = messages
                return AdapterResponse(
                    success=True,
                    content="Cierre del Team Lead.",
                    latency_ms=1,
                    input_tokens=10,
                    output_tokens=20,
                )

        class FailingApiAdapter(ModelAdapter):
            def __init__(self) -> None:
                super().__init__(
                    name="openai_api",
                    provider="openai",
                    model="gpt-cheap",
                    channel=ChannelType.API,
                    capabilities={"coding", "reasoning", "analysis"},
                )

            def available(self) -> bool:
                return True

            def invoke(self, prompt, messages=None, tools=None):
                return AdapterResponse(
                    success=False,
                    content="",
                    error="rate_limit",
                    latency_ms=1,
                    input_tokens=5,
                    output_tokens=0,
                )

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    CaptureLeadCloseAdapter(
                        name="openai_pro",
                        provider="openai",
                        model="gpt-pro",
                        capabilities={"reasoning", "analysis"},
                    ),
                    FailingApiAdapter(),
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            router.route_and_invoke(
                RoutingRequest(
                    role=Role.ENGINEER,
                    complexity=Complexity.MEDIUM,
                    criticality=Criticality.MEDIUM,
                    required_capabilities={"coding"},
                    excluded_adapters={"openai_pro"},
                ),
                "Fase build previa con fallo de routing.",
                task_id="CHAT-RUN-HEALTH::build",
            )

            build_task = WorkTask(
                task_id="CHAT-RUN-HEALTH::build",
                title="Build",
                description="Implementa el cambio principal.",
                role=Role.ENGINEER,
                metadata={
                    "phase": "build",
                    "gate_iteration": 2,
                    "max_gate_iterations": 3,
                    "review_feedback": "Placeholder detectado en el output final.",
                    "quality_gate_tasks": ["CHAT-RUN-HEALTH::build_review"],
                    "execution_round": 2,
                },
            )
            orchestrator.taskboard.add_task(build_task)
            orchestrator.taskboard.mark_blocked(
                "CHAT-RUN-HEALTH::build",
                "gate_rejected",
            )

            gate_task = WorkTask(
                task_id="CHAT-RUN-HEALTH::build_review",
                title="Build review gate",
                description="Valida la evidencia de build.",
                role=Role.REVIEWER,
                metadata={"phase": "build", "is_gate": True},
            )
            orchestrator.taskboard.add_task(gate_task)
            orchestrator.taskboard.claim_task(
                "CHAT-RUN-HEALTH::build_review",
                "rev-1",
            )
            orchestrator.taskboard.mark_completed(
                "CHAT-RUN-HEALTH::build_review",
                "Gate completado.",
            )

            workflow_state = orchestrator._get_workflow_state("CHAT-RUN-HEALTH")
            workflow_state["phase_task_ids"] = {
                "build": "CHAT-RUN-HEALTH::build",
                "lead_close": "CHAT-RUN-HEALTH::lead_close",
            }
            orchestrator.event_logger.emit(
                "chat_plan_created",
                {
                    "task_id": "CHAT-RUN-HEALTH",
                    "round_budget": 4,
                },
            )

            lead_close = WorkTask(
                task_id="CHAT-RUN-HEALTH::lead_close",
                title="Lead synthesis and response",
                description="Sintetiza el run y decide el siguiente paso.",
                role=Role.TEAM_LEAD,
                metadata={
                    "required_capabilities": ["reasoning"],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                    "skip_placeholder_check": True,
                    "phase": "lead_close",
                    "chat_parent": "CHAT-RUN-HEALTH",
                },
            )
            orchestrator.submit_task(lead_close)
            orchestrator.run_until_idle(max_rounds=3)

            messages = list(captured.get("messages") or [])
            self.assertTrue(messages)
            final_user = str(messages[-1].get("content", ""))
            self.assertIn("== RUN HEALTH REPORT ==", final_user)
            self.assertIn("GATE REJECTIONS:", final_user)
            self.assertIn("phase=build, iterations=2/3", final_user)
            self.assertIn("Placeholder detectado", final_user)
            self.assertIn("ROUTING ERRORS:", final_user)
            self.assertIn("error=rate_limit", final_user)
            self.assertIn("PRESUPUESTO:", final_user)
            self.assertIn("Rondas usadas: 2 / 4", final_user)

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(
                    item.get("event_type") == "run_health_report_built"
                    for item in events
                )
            )

    def test_lead_intake_still_does_not_pause_inside_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(
                adapters=[
                    SubscriptionAdapter(
                        name="openai_pro",
                        provider="openai",
                        model="gpt-pro",
                        capabilities={"reasoning", "analysis"},
                        response_content='[CLARIFY: "Necesito más contexto inicial."]',
                    )
                ],
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="CHAT-LEAD-INTAKE::lead_intake",
                title="Lead intake",
                description="Analiza la petición y decide el flujo.",
                role=Role.TEAM_LEAD,
                complexity=Complexity.MEDIUM,
                criticality=Criticality.MEDIUM,
                metadata={
                    "required_capabilities": ["reasoning"],
                    "interactive_chat": True,
                    "skip_quality_gates": True,
                    "phase": "lead_intake",
                    "chat_parent": "CHAT-LEAD-INTAKE",
                },
            )

            orchestrator.submit_task(task)
            orchestrator.run_until_idle(max_rounds=3)

            result = orchestrator.taskboard.get_task("CHAT-LEAD-INTAKE::lead_intake")
            assert result is not None
            self.assertNotEqual(result.state, TaskState.WAITING_USER)

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
                    response_content="[SIMULADO | openai:gpt-pro] Respuesta mock para build.",
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
                    response_content="[SIMULADO | openai:gpt-pro] Respuesta mock para build.",
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
                    response_content="[SIMULADO | openai:gpt-pro] Respuesta mock para build.",
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

    def test_completed_task_can_be_reopened_with_forced_quality_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "review", "analysis", "reasoning"},
                    response_content="Gate passed with explicit review notes.",
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
                task_id="ENG-FORCE-1",
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

            parent = orchestrator.taskboard.get_task("ENG-FORCE-1")
            assert parent is not None
            self.assertEqual(parent.state.value, "completed")

            parent.metadata["skip_quality_gates"] = False
            parent.metadata["force_gate_requested"] = True
            orchestrator.taskboard.mark_blocked("ENG-FORCE-1", reason="waiting_quality_gates")
            orchestrator._spawn_quality_gates(parent)
            orchestrator.run_until_idle(max_rounds=6)

            reopened = orchestrator.taskboard.get_task("ENG-FORCE-1")
            review_gate = orchestrator.taskboard.get_task("ENG-FORCE-1::review")
            qa_gate = orchestrator.taskboard.get_task("ENG-FORCE-1::qa")

            assert reopened is not None
            assert review_gate is not None
            assert qa_gate is not None
            self.assertEqual(reopened.state.value, "completed")
            self.assertEqual(review_gate.state.value, "completed")
            self.assertEqual(qa_gate.state.value, "completed")

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
                RealSubscriptionAdapter(
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

    def test_peer_consultation_prefers_distinct_provider_families_when_available(self) -> None:
        peer_records: list[dict[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                PeerDiversityCaptureAdapter(
                    name="openai_engineer",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding"},
                    record=peer_records,
                ),
                PeerDiversityCaptureAdapter(
                    name="google_research",
                    provider="google",
                    model="gemini-pro",
                    capabilities={"analysis"},
                    record=peer_records,
                ),
                PeerDiversityCaptureAdapter(
                    name="anthropic_review",
                    provider="anthropic",
                    model="claude-sonnet",
                    capabilities={"review"},
                    record=peer_records,
                ),
                PeerDiversityCaptureAdapter(
                    name="groq_qa",
                    provider="groq",
                    model="llama-3.3",
                    capabilities={"analysis"},
                    record=peer_records,
                ),
            ]
            router = HybridRouter(
                adapters=adapters,
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="PEER-DIVERSITY-1",
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

            round1_providers = [
                item["provider"] for item in peer_records if item.get("round") == "round1"
            ]
            self.assertGreaterEqual(len(round1_providers), 3)
            self.assertGreaterEqual(len(set(round1_providers)), 3)
            stored = orchestrator.taskboard.get_task("PEER-DIVERSITY-1")
            assert stored is not None
            self.assertEqual(
                set(stored.metadata.get("consulted_providers", [])),
                set(round1_providers),
            )
            self.assertTrue(bool(stored.metadata.get("peer_diversity_observed")))

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertFalse(
                any(item.get("event_type") == "peer_diversity_fallback" for item in events)
            )

    def test_peer_consultation_emits_diversity_fallback_when_only_one_provider_is_available(
        self,
    ) -> None:
        peer_records: list[dict[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                PeerDiversityCaptureAdapter(
                    name="openai_all",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "analysis", "review"},
                    record=peer_records,
                )
            ]
            router = HybridRouter(
                adapters=adapters,
                policy=build_default_router_policy(),
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="PEER-DIVERSITY-2",
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

            round1_providers = [
                item["provider"] for item in peer_records if item.get("round") == "round1"
            ]
            self.assertGreaterEqual(len(round1_providers), 2)
            self.assertEqual(set(round1_providers), {"openai"})
            stored = orchestrator.taskboard.get_task("PEER-DIVERSITY-2")
            assert stored is not None
            self.assertEqual(stored.metadata.get("consulted_providers", []), ["openai"])
            self.assertFalse(bool(stored.metadata.get("peer_diversity_observed")))

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertTrue(
                any(item.get("event_type") == "peer_diversity_fallback" for item in events)
            )

    def test_peer_consultation_diversity_policy_can_be_disabled(self) -> None:
        peer_records: list[dict[str, str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            policy = build_default_router_policy()
            policy.peer_consultation_diversity_required = False
            adapters: list[ModelAdapter] = [
                PeerDiversityCaptureAdapter(
                    name="openai_all",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"coding", "analysis", "review"},
                    record=peer_records,
                )
            ]
            router = HybridRouter(
                adapters=adapters,
                policy=policy,
            )
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path.cwd(),
            )

            task = WorkTask(
                task_id="PEER-DIVERSITY-3",
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

            events = orchestrator.event_logger.recent_events(hours=1)
            self.assertFalse(
                any(item.get("event_type") == "peer_diversity_fallback" for item in events)
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

    def test_team_lead_gets_compact_targeted_skill_mcp_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters: list[ModelAdapter] = [
                SubscriptionAdapter(
                    name="lead_tool",
                    provider="openai",
                    model="gpt-pro",
                    capabilities={"analysis", "reasoning", "documentation"},
                    role_targets={"team_lead"},
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
                task_id="GUIDE-LEAD-1",
                title="Coordinar inspeccion browser",
                description="Delegar skill browser y usar LSP para estimar impacto",
                role=Role.TEAM_LEAD,
                metadata={
                    "skill_targets": ["playwright_qa_skill"],
                    "lsp_targets": ["impact"],
                    "required_capabilities": [],
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                },
            )
            assignee = orchestrator._assignee_for_role(Role.TEAM_LEAD)
            context = orchestrator._build_skill_mcp_context(task, assignee)

            self.assertIn("Coordina mediante especialistas", context)
            self.assertIn("playwright_qa_skill", context)
            self.assertIn("impact", context)
            self.assertNotIn("Skills aplicables:", context)

            entries = orchestrator.memory.recent(assignee, limit=10)
            guidance_entries = [
                item for item in entries if item.kind == "skill_mcp_guidance"
            ]
            self.assertTrue(guidance_entries)
            self.assertIn("playwright_qa_skill", guidance_entries[0].content)

            events_path = runtime_dir / "events.jsonl"
            self.assertTrue(events_path.exists())
            records = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            matching = [
                record
                for record in records
                if record.get("event_type") == "skill_mcp_guidance"
            ]
            self.assertTrue(matching)
            payload = matching[-1]["payload"]
            self.assertEqual(payload.get("guidance_mode"), "coordinator")
            self.assertEqual(payload.get("preferred_skills"), ["playwright_qa_skill"])

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
                RealSubscriptionAdapter(
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


    def test_gate_retry_injects_own_previous_output_into_context(self) -> None:
        """M3.3: En gate retry (gate_iteration > 0), el agente ve su propio output anterior."""
        import tempfile
        from pathlib import Path
        from aiteam.adapters.subscription import SubscriptionAdapter
        from aiteam.router import HybridRouter
        from aiteam.config import build_default_router_policy

        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            adapters = [
                SubscriptionAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-4o",
                    capabilities={"coding", "review", "reasoning"},
                )
            ]
            router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
            orch = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=Path(tmp),
            )

            previous_output = "Implementé el endpoint POST /users con validación básica."
            task = WorkTask(
                task_id="M3-TEST::build",
                title="Build endpoint",
                description="Implementar endpoint POST /users.",
                role=Role.ENGINEER,
                metadata={
                    "gate_iteration": 1,
                    "result": previous_output,
                    "phase": "build",
                },
            )
            orch.taskboard.add_task(task)

            context = orch._build_collaboration_context(task=task, assignee="engineer-1")

            self.assertIn(previous_output[:40], context,
                          "El output anterior del agente debe aparecer en el contexto del retry")
            self.assertIn("iteracion 0", context.lower() if "iteracion" in context.lower() else context,
                          "El contexto debe mencionar la iteracion anterior")

    def test_placeholder_gate_does_not_fail_for_generic_placeholder_word(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(adapters=[], policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            task = WorkTask(
                task_id="LEAD-PLACEHOLDER-1",
                title="Analizar briefing",
                description="Preparar plan realista.",
                role=Role.RESEARCHER,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                metadata={
                    "skip_peer_consultation": True,
                    "skip_quality_gates": True,
                    "skip_evidence_gate": True,
                },
            )
            orchestrator.submit_task(task)

            def _route(request, prompt, task_id="", messages=None, tools=None, on_chunk=None):
                return RoutingDecision(
                    success=True,
                    provider="anthropic",
                    model="claude-sonnet-4-5",
                    channel=ChannelType.SUBSCRIPTION,
                    reason="selected_by_policy",
                    response=AdapterResponse(
                        success=True,
                        content=(
                            "El documento evita respuestas genericas. "
                            "La palabra placeholder aparece aqui como ejemplo de un falso positivo, "
                            "pero no hay marcadores TODO ni texto simulado."
                        ),
                        latency_ms=5,
                        input_tokens=10,
                        output_tokens=30,
                    ),
                )

            with patch.object(router, "route_and_invoke", side_effect=_route):
                orchestrator.run_until_idle(max_rounds=3)

            final_task = orchestrator.taskboard.get_task("LEAD-PLACEHOLDER-1")
            assert final_task is not None
            self.assertEqual(final_task.state.value, "completed")
            self.assertNotIn("error", final_task.metadata)

    def test_streaming_thinking_chunks_only_hit_agent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            project_root = Path(tmp) / "workspace"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            project_root.mkdir(parents=True, exist_ok=True)
            router = HybridRouter(adapters=[], policy=build_default_router_policy())
            orchestrator = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )

            token_chunks: list[tuple[str, str]] = []
            agent_events: list[dict] = []
            orchestrator.token_chunk_callback = (
                lambda task_id, chunk: token_chunks.append((task_id, chunk))
            )
            orchestrator.agent_event_callback = lambda event: agent_events.append(event)

            task = WorkTask(
                task_id="THINK-1",
                title="Analizar arquitectura",
                description="Revisar el diseno y devolver una recomendacion.",
                role=Role.RESEARCHER,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                metadata={
                    "skip_peer_consultation": True,
                },
            )
            orchestrator.submit_task(task)

            def _route(request, prompt, task_id="", messages=None, tools=None, on_chunk=None):
                if on_chunk is not None:
                    on_chunk(StreamChunk(text="Analizando...", chunk_type="thinking"))
                    on_chunk("Respuesta final")
                return RoutingDecision(
                    success=True,
                    provider="anthropic",
                    model="claude-3-7-sonnet",
                    channel=ChannelType.API,
                    reason="selected_by_policy",
                    response=AdapterResponse(
                        success=True,
                        content="Respuesta final",
                        latency_ms=5,
                        input_tokens=10,
                        output_tokens=4,
                    ),
                )

            with patch.object(router, "route_and_invoke", side_effect=_route):
                orchestrator.run_until_idle(max_rounds=3)

            self.assertEqual(token_chunks, [("THINK-1", "Respuesta final")])
            chunk_events = [
                item for item in agent_events if item.get("type") == "agent_chunk"
            ]
            routed_events = [
                item for item in agent_events if item.get("type") == "agent_routed"
            ]
            completed_events = [
                item for item in agent_events if item.get("type") == "agent_completed"
            ]
            self.assertEqual(len(chunk_events), 2)
            self.assertEqual(len(routed_events), 1)
            self.assertEqual(routed_events[0].get("provider"), "anthropic")
            self.assertEqual(routed_events[0].get("model"), "claude-3-7-sonnet")
            self.assertEqual(len(completed_events), 1)
            self.assertEqual(completed_events[0].get("provider"), "anthropic")
            self.assertEqual(completed_events[0].get("model"), "claude-3-7-sonnet")
            self.assertEqual(chunk_events[0].get("chunk_type"), "thinking")
            self.assertEqual(chunk_events[0].get("chunk"), "Analizando...")
            self.assertEqual(chunk_events[1].get("chunk_type"), "output")
            self.assertEqual(chunk_events[1].get("chunk"), "Respuesta final")


class MinimalOutputDepositTests(unittest.TestCase):
    """C3: _maybe_deposit_minimal_output — deposit PROJECT_PLAN.md for empty workspaces."""

    def _get_fn(self):
        import api.main as api_main
        return api_main._maybe_deposit_minimal_output

    def test_minimal_output_deposited_when_workspace_empty_and_build_blocked(self) -> None:
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="# Plan\n\nAnálisis completo. Propuesta técnica lista.",
                chat_id="CHAT-TEST123",
                run_mode="standard",
            )
            self.assertIsNotNone(result, "Should deposit PROJECT_PLAN.md in empty workspace")
            plan_path = workspace / "PROJECT_PLAN.md"
            self.assertTrue(plan_path.exists())
            content = plan_path.read_text(encoding="utf-8")
            self.assertIn("CHAT-TEST123", content)
            self.assertIn("Análisis completo", content)

    def test_minimal_output_not_deposited_when_workspace_has_files(self) -> None:
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "main.py").write_text("# code", encoding="utf-8")
            result = fn(
                workspace=workspace,
                lead_output="# Plan",
                chat_id="CHAT-TEST456",
                run_mode="standard",
            )
            self.assertIsNone(result, "Should not deposit when workspace has product files")
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_when_lead_intake_failed(self) -> None:
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="",
                chat_id="CHAT-TEST789",
                run_mode="standard",
            )
            self.assertIsNone(result, "Should not deposit when lead_output is empty")
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_in_probe_mode(self) -> None:
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="# Plan detallado con análisis completo",
                chat_id="CHAT-PROBE",
                run_mode="probe",
            )
            self.assertIsNone(result, "Should not deposit in probe mode")
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_when_workspace_is_project_root(self) -> None:
        from api.utils import PROJECT_ROOT
        fn = self._get_fn()
        result = fn(
            workspace=Path(PROJECT_ROOT),
            lead_output="# Plan",
            chat_id="CHAT-SELF",
            run_mode="standard",
        )
        self.assertIsNone(result, "Should not deposit when workspace is the project root itself")

    def test_minimal_output_skips_aiteam_dir_files(self) -> None:
        fn = self._get_fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            aiteam_dir = workspace / ".aiteam"
            aiteam_dir.mkdir()
            (aiteam_dir / "instructions.md").write_text("instructions", encoding="utf-8")
            # Only .aiteam/ contents — workspace is otherwise empty
            result = fn(
                workspace=workspace,
                lead_output="# Plan",
                chat_id="CHAT-AITEAM",
                run_mode="standard",
            )
            self.assertIsNotNone(result, "Should deposit when only .aiteam/ exists")


class ContinuationPolicyTests(unittest.TestCase):
    """C2: continuation_policy — archive incomplete tasks for clean_retry."""

    def _make_taskboard(self, tmp_dir: Path):
        from aiteam.taskboard import TaskBoard
        return TaskBoard.from_runtime_dir(tmp_dir)

    def test_clean_retry_archives_previous_incomplete_tasks(self) -> None:
        """C2: archive_incomplete_tasks marks PENDING/BLOCKED/WAITING_USER as ARCHIVED."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tb = self._make_taskboard(tmp_path)

            tb.add_task(WorkTask(task_id="r1::phase_a", title="Phase A", description="", role=Role.ENGINEER))
            tb.add_task(WorkTask(task_id="r1::phase_b", title="Phase B", description="", role=Role.RESEARCHER))
            tb.mark_blocked("r1::phase_b", "no_eligible_adapter")

            archived = tb.archive_incomplete_tasks("clean_retry_requested")

            self.assertIn("r1::phase_a", archived)
            self.assertIn("r1::phase_b", archived)
            self.assertEqual(tb.get_task("r1::phase_a").state, TaskState.ARCHIVED)
            self.assertEqual(tb.get_task("r1::phase_b").state, TaskState.ARCHIVED)
            self.assertEqual(
                tb.get_task("r1::phase_a").metadata.get("archived_reason"),
                "clean_retry_requested",
            )

    def test_clean_retry_does_not_archive_completed_or_failed(self) -> None:
        """C2: COMPLETED and FAILED tasks are not archived."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tb = self._make_taskboard(tmp_path)

            tb.add_task(WorkTask(task_id="r2::done", title="Done", description="", role=Role.ENGINEER))
            tb.claim_task("r2::done", "agent1")
            tb.mark_completed("r2::done", "ok")

            tb.add_task(WorkTask(task_id="r2::failed", title="Failed", description="", role=Role.ENGINEER))
            tb.claim_task("r2::failed", "agent1")
            tb.mark_failed("r2::failed", "error")

            archived = tb.archive_incomplete_tasks("clean_retry_requested")

            self.assertNotIn("r2::done", archived)
            self.assertNotIn("r2::failed", archived)
            self.assertEqual(tb.get_task("r2::done").state, TaskState.COMPLETED)
            self.assertEqual(tb.get_task("r2::failed").state, TaskState.FAILED)

    def test_clean_retry_starts_fresh_workflow(self) -> None:
        """C2: After clean_retry, ARCHIVED tasks don't block new tasks."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tb = self._make_taskboard(tmp_path)

            # Simulate prior run with a blocked task
            tb.add_task(WorkTask(task_id="r3::old_phase", title="Old", description="", role=Role.ENGINEER))
            tb.mark_blocked("r3::old_phase", "no_eligible_adapter")

            # Archive incomplete tasks (clean_retry)
            tb.archive_incomplete_tasks("clean_retry_requested")

            # Add a new task with no dependencies — should be READY
            tb.add_task(WorkTask(task_id="r4::new_phase", title="New", description="", role=Role.ENGINEER))

            ready = tb.ready_tasks()
            ready_ids = [t.task_id for t in ready]
            self.assertIn("r4::new_phase", ready_ids)

    def test_auto_policy_preserves_existing_behavior(self) -> None:
        """C2: 'auto' policy (default) does not archive any tasks."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tb = self._make_taskboard(tmp_path)

            tb.add_task(WorkTask(task_id="r5::phase_a", title="Phase A", description="", role=Role.ENGINEER))
            tb.mark_blocked("r5::phase_a", "some_reason")

            # No archiving happens with auto policy (no call to archive_incomplete_tasks)
            self.assertEqual(tb.get_task("r5::phase_a").state, TaskState.BLOCKED)
            ready = tb.ready_tasks()
            self.assertNotIn("r5::phase_a", [t.task_id for t in ready])


class DeferredDelegateTests(unittest.TestCase):
    """C1: Delegate evidence tasks created lazily when parent phase is claimed."""

    def _make_orch(self, tmp_dir: Path) -> AITeamOrchestrator:
        return AITeamOrchestrator(
            router=HybridRouter(
                adapters=[FakeSuccessAdapter()],
                policy=build_default_router_policy(),
            ),
            runtime_dir=tmp_dir,
        )

    def test_delegate_tasks_not_created_until_phase_starts(self) -> None:
        """C1: Deferred specs stored in task metadata, no delegate tasks in board at plan time."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orch = self._make_orch(tmp_path)

            parent_id = "run1::build"
            parent_task = WorkTask(
                task_id=parent_id,
                title="Build",
                description="Build phase",
                role=Role.ENGINEER,
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                metadata={
                    "deferred_evidence_specs": [
                        {
                            "task_id": "run1::delegate_build_test_runner_0",
                            "title": "Evidencia build",
                            "description": "Ejecuta tests",
                            "role": "qa",
                            "criticality": "low",
                            "metadata": {
                                "required_capabilities": ["test_execute"],
                                "skip_quality_gates": True,
                                "skip_evidence_gate": True,
                                "structured_evidence_task": True,
                            },
                        }
                    ]
                },
            )
            orch.submit_task(parent_task)

            # Before phase starts: no delegate tasks
            delegate = orch.taskboard.get_task("run1::delegate_build_test_runner_0")
            self.assertIsNone(delegate, "Delegate task must not exist before phase starts")

            # Claim parent task → should trigger lazy spawn
            orch.taskboard.claim_task(parent_id, "agent1")
            orch._maybe_spawn_deferred_delegates(parent_id)

            # After claim: delegate task now exists
            delegate = orch.taskboard.get_task("run1::delegate_build_test_runner_0")
            self.assertIsNotNone(delegate, "Delegate task should exist after parent phase starts")
            self.assertEqual(delegate.dependencies, [parent_id])

            # Flag set to prevent re-spawn
            refreshed_parent = orch.taskboard.get_task(parent_id)
            self.assertTrue(refreshed_parent.metadata.get("delegates_spawned"))

    def test_blocked_phase_does_not_create_delegates(self) -> None:
        """C1: If parent phase is blocked (dependency failed), delegates are never created."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orch = self._make_orch(tmp_path)

            dep_id = "run2::plan_research"
            dep_task = WorkTask(
                task_id=dep_id,
                title="Plan research",
                description="Research phase",
                role=Role.RESEARCHER,
            )
            orch.submit_task(dep_task)
            # Mark dependency as blocked
            orch.taskboard.mark_blocked(dep_id, "no_eligible_adapter")

            parent_id = "run2::build"
            parent_task = WorkTask(
                task_id=parent_id,
                title="Build",
                description="Build phase",
                role=Role.ENGINEER,
                dependencies=[dep_id],
                metadata={
                    "deferred_evidence_specs": [
                        {
                            "task_id": "run2::delegate_build_test_runner_0",
                            "title": "Evidencia build",
                            "description": "Ejecuta tests",
                            "role": "qa",
                            "criticality": "low",
                            "metadata": {"required_capabilities": ["test_execute"]},
                        }
                    ]
                },
            )
            orch.submit_task(parent_task)

            # Run the orchestrator — parent should end up BLOCKED (dep failed)
            orch.run_until_idle(max_rounds=3)

            # Verify parent is blocked
            parent_current = orch.taskboard.get_task(parent_id)
            self.assertEqual(parent_current.state, TaskState.BLOCKED)

            # Verify no delegate was ever created
            delegate = orch.taskboard.get_task("run2::delegate_build_test_runner_0")
            self.assertIsNone(delegate, "Delegates must not be created when parent is blocked")

    # ── C1 spec-named tests ──────────────────────────────────────────────────

    def test_delegate_tasks_not_in_taskboard_before_phase_starts(self) -> None:
        """C1 spec: After plan creation, delegate tasks do not exist in taskboard yet."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orch = self._make_orch(tmp_path)

            parent_id = "c1s::build"
            parent_task = WorkTask(
                task_id=parent_id,
                title="Build",
                description="Build phase",
                role=Role.ENGINEER,
                metadata={
                    "deferred_evidence_specs": [
                        {
                            "task_id": "c1s::delegate_build_scout_0",
                            "title": "Evidence build",
                            "description": "Run tests",
                            "role": "qa",
                            "criticality": "low",
                            "metadata": {"skip_evidence_gate": True},
                        }
                    ]
                },
            )
            orch.submit_task(parent_task)

            # Delegate must not exist before phase is claimed
            self.assertIsNone(
                orch.taskboard.get_task("c1s::delegate_build_scout_0"),
                "Delegate task must not exist in taskboard before the parent phase starts",
            )

    def test_delegate_tasks_created_when_phase_is_claimed(self) -> None:
        """C1 spec: Claiming the parent phase spawns its deferred delegate tasks."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orch = self._make_orch(tmp_path)

            parent_id = "c1c::build"
            delegate_id = "c1c::delegate_build_scout_0"
            parent_task = WorkTask(
                task_id=parent_id,
                title="Build",
                description="Build phase",
                role=Role.ENGINEER,
                metadata={
                    "deferred_evidence_specs": [
                        {
                            "task_id": delegate_id,
                            "title": "Evidence build",
                            "description": "Run tests",
                            "role": "qa",
                            "criticality": "low",
                            "metadata": {"skip_evidence_gate": True},
                        }
                    ]
                },
            )
            orch.submit_task(parent_task)

            # Claim parent then spawn deferred delegates
            orch.taskboard.claim_task(parent_id, "agent1")
            orch._maybe_spawn_deferred_delegates(parent_id)

            delegate = orch.taskboard.get_task(delegate_id)
            self.assertIsNotNone(delegate, "Delegate task should exist after parent phase is claimed")
            self.assertEqual(delegate.dependencies, [parent_id])
            refreshed = orch.taskboard.get_task(parent_id)
            self.assertTrue(refreshed.metadata.get("delegates_spawned"))

    def test_blocked_phase_never_creates_delegates(self) -> None:
        """C1 spec: A blocked phase never creates its delegate tasks."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orch = self._make_orch(tmp_path)

            dep_id = "c1b::plan"
            orch.submit_task(WorkTask(
                task_id=dep_id,
                title="Plan",
                description="",
                role=Role.RESEARCHER,
            ))
            orch.taskboard.mark_blocked(dep_id, "no_eligible_adapter")

            parent_id = "c1b::build"
            delegate_id = "c1b::delegate_build_scout_0"
            orch.submit_task(WorkTask(
                task_id=parent_id,
                title="Build",
                description="",
                role=Role.ENGINEER,
                dependencies=[dep_id],
                metadata={
                    "deferred_evidence_specs": [
                        {
                            "task_id": delegate_id,
                            "title": "Evidence",
                            "description": "",
                            "role": "qa",
                            "criticality": "low",
                            "metadata": {},
                        }
                    ]
                },
            ))

            orch.run_until_idle(max_rounds=3)

            parent_current = orch.taskboard.get_task(parent_id)
            self.assertEqual(parent_current.state, TaskState.BLOCKED)
            self.assertIsNone(
                orch.taskboard.get_task(delegate_id),
                "Delegates must not be created when parent phase is blocked",
            )


class ContinuationPolicySpecTests(unittest.TestCase):
    """C2 spec-named tests: continuation_policy clean_retry and auto behaviour."""

    def _make_taskboard(self, tmp_dir: Path):
        from aiteam.taskboard import TaskBoard
        return TaskBoard.from_runtime_dir(tmp_dir)

    def test_archive_incomplete_tasks_marks_pending_as_archived(self) -> None:
        """C2 spec: archive_incomplete_tasks transitions PENDING tasks to ARCHIVED."""
        with tempfile.TemporaryDirectory() as tmp:
            tb = self._make_taskboard(Path(tmp))
            tb.add_task(WorkTask(task_id="cs::pending", title="P", description="", role=Role.ENGINEER))

            archived = tb.archive_incomplete_tasks("test_reason")

            self.assertIn("cs::pending", archived)
            self.assertEqual(tb.get_task("cs::pending").state, TaskState.ARCHIVED)
            self.assertEqual(tb.get_task("cs::pending").metadata.get("archived_reason"), "test_reason")

    def test_archive_incomplete_tasks_does_not_touch_completed(self) -> None:
        """C2 spec: archive_incomplete_tasks leaves COMPLETED and FAILED tasks untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            tb = self._make_taskboard(Path(tmp))

            tb.add_task(WorkTask(task_id="cs::done", title="Done", description="", role=Role.ENGINEER))
            tb.claim_task("cs::done", "agent1")
            tb.mark_completed("cs::done", "ok")

            tb.add_task(WorkTask(task_id="cs::fail", title="Fail", description="", role=Role.ENGINEER))
            tb.claim_task("cs::fail", "agent1")
            tb.mark_failed("cs::fail", "err")

            archived = tb.archive_incomplete_tasks("test_reason")

            self.assertNotIn("cs::done", archived)
            self.assertNotIn("cs::fail", archived)
            self.assertEqual(tb.get_task("cs::done").state, TaskState.COMPLETED)
            self.assertEqual(tb.get_task("cs::fail").state, TaskState.FAILED)

    def test_clean_retry_archives_previous_tasks_before_run(self) -> None:
        """C2 spec: clean_retry policy archives all incomplete tasks before the new run starts."""
        with tempfile.TemporaryDirectory() as tmp:
            tb = self._make_taskboard(Path(tmp))

            tb.add_task(WorkTask(task_id="cr::old_a", title="A", description="", role=Role.ENGINEER))
            tb.add_task(WorkTask(task_id="cr::old_b", title="B", description="", role=Role.RESEARCHER))
            tb.mark_blocked("cr::old_b", "dependency_failed")

            archived = tb.archive_incomplete_tasks("clean_retry_requested")

            self.assertIn("cr::old_a", archived)
            self.assertIn("cr::old_b", archived)
            for tid in ("cr::old_a", "cr::old_b"):
                self.assertEqual(tb.get_task(tid).state, TaskState.ARCHIVED)

            # New task added after archiving is unaffected
            tb.add_task(WorkTask(task_id="cr::new_phase", title="New", description="", role=Role.ENGINEER))
            ready_ids = [t.task_id for t in tb.ready_tasks()]
            self.assertIn("cr::new_phase", ready_ids)

    def test_auto_policy_preserves_existing_tasks(self) -> None:
        """C2 spec: 'auto' policy does not archive any existing tasks."""
        with tempfile.TemporaryDirectory() as tmp:
            tb = self._make_taskboard(Path(tmp))

            tb.add_task(WorkTask(task_id="auto::blocked", title="B", description="", role=Role.ENGINEER))
            tb.mark_blocked("auto::blocked", "some_reason")

            # auto policy: no archiving call is made
            self.assertEqual(tb.get_task("auto::blocked").state, TaskState.BLOCKED)
            self.assertNotIn("auto::blocked", [t.task_id for t in tb.ready_tasks()])


class MinimalOutputSpecTests(unittest.TestCase):
    """C3 spec-named tests: _maybe_deposit_minimal_output behaviour."""

    def _fn(self):
        import api.main as api_main
        return api_main._maybe_deposit_minimal_output

    def test_minimal_output_deposited_when_workspace_empty(self) -> None:
        """C3 spec: deposits PROJECT_PLAN.md when workspace has no product files."""
        fn = self._fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="# Plan\n\nAnalysis complete.",
                chat_id="SPEC-001",
                run_mode="standard",
            )
            self.assertIsNotNone(result)
            plan = workspace / "PROJECT_PLAN.md"
            self.assertTrue(plan.exists())
            self.assertIn("SPEC-001", plan.read_text(encoding="utf-8"))

    def test_minimal_output_not_deposited_when_files_exist(self) -> None:
        """C3 spec: does not deposit when workspace already contains product files."""
        fn = self._fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "main.py").write_text("# code", encoding="utf-8")
            result = fn(
                workspace=workspace,
                lead_output="# Plan",
                chat_id="SPEC-002",
                run_mode="standard",
            )
            self.assertIsNone(result)
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_when_no_lead_output(self) -> None:
        """C3 spec: does not deposit when lead_output is empty or blank."""
        fn = self._fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="",
                chat_id="SPEC-003",
                run_mode="standard",
            )
            self.assertIsNone(result)
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_in_probe_mode(self) -> None:
        """C3 spec: does not deposit in probe mode."""
        fn = self._fn()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            result = fn(
                workspace=workspace,
                lead_output="# Full plan with analysis",
                chat_id="SPEC-004",
                run_mode="probe",
            )
            self.assertIsNone(result)
            self.assertFalse((workspace / "PROJECT_PLAN.md").exists())

    def test_minimal_output_not_deposited_in_aiteams_repo(self) -> None:
        """C3 spec: does not deposit when the workspace is the AI Teams project root."""
        from api.utils import PROJECT_ROOT
        fn = self._fn()
        result = fn(
            workspace=Path(PROJECT_ROOT),
            lead_output="# Plan",
            chat_id="SPEC-005",
            run_mode="standard",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
