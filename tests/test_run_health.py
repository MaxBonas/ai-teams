import json
import tempfile
from pathlib import Path

from aiteam.adapters.base import ModelAdapter
from aiteam.config import build_default_router_policy
from aiteam.router import HybridRouter
from aiteam.run_health import build_capabilities_briefing, build_run_health_report
from aiteam.types import AdapterResponse, ChannelType, Role, TaskState, WorkTask


def _phase_task(
    phase_id: str,
    *,
    state: TaskState,
    metadata: dict | None = None,
) -> WorkTask:
    return WorkTask(
        task_id=f"CHAT-RUN::{phase_id}",
        title=phase_id,
        description=f"fase {phase_id}",
        role=Role.ENGINEER,
        state=state,
        metadata={"phase": phase_id, **(metadata or {})},
    )


class _StaticAdapter(ModelAdapter):
    def __init__(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        channel: ChannelType,
        available: bool,
    ) -> None:
        super().__init__(
            name=name,
            provider=provider,
            model=model,
            channel=channel,
            capabilities={"reasoning"},
        )
        self._available = available

    def available(self) -> bool:
        return self._available

    def invoke(self, prompt, messages=None, tools=None):
        return AdapterResponse(success=True, content="ok")


def test_build_run_health_report_renders_gate_routing_resources_and_budget() -> None:
    report = build_run_health_report(
        phase_tasks={
            "build": _phase_task(
                "build",
                state=TaskState.BLOCKED,
                metadata={
                    "gate_iteration": 2,
                    "max_gate_iterations": 3,
                    "review_feedback": "Placeholder detectado en el output final.",
                    "quality_gate_tasks": ["CHAT-RUN::gate-review"],
                },
            ),
            "qa": _phase_task("qa", state=TaskState.COMPLETED),
            "review": _phase_task(
                "review",
                state=TaskState.ARCHIVED,
                metadata={"skip_reason": "lead_decision"},
            ),
        },
        gate_tasks={
            "CHAT-RUN::gate-review": WorkTask(
                task_id="CHAT-RUN::gate-review",
                title="gate review",
                description="gate",
                role=Role.REVIEWER,
                state=TaskState.COMPLETED,
                metadata={"is_gate": True},
            )
        },
        routing_failures=[
            {
                "phase": "build",
                "error": "rate_limit",
                "role": "engineer",
                "model": "gpt-cheap",
            }
        ],
        missing_api_keys=["OPENAI_API_KEY", "OPENAI_API_KEY"],
        unavailable_models=["gpt-cheap", "claude-sonnet", "gpt-cheap"],
        rounds_used=2,
        round_budget=4,
        auto_extensions=1,
    )

    prompt = report.to_prompt_block()

    assert "== RUN HEALTH REPORT ==" in prompt
    assert "Fases completadas: 1 / 3" in prompt
    assert "GATE REJECTIONS:" in prompt
    assert "phase=build, iterations=2/3" in prompt
    assert "Placeholder detectado" in prompt
    assert "ROUTING ERRORS:" in prompt
    assert "error=rate_limit" in prompt
    assert "FASES SALTADAS:" in prompt
    assert "phase=review, razon: lead_decision" in prompt
    assert prompt.count("API key ausente: OPENAI_API_KEY") == 1
    assert "Modelo no disponible: claude-sonnet" in prompt
    assert "Modelo no disponible: gpt-cheap" in prompt
    assert "Rondas usadas: 2 / 4" in prompt
    assert "Extensiones automaticas: 1" in prompt


def test_build_run_health_report_requires_completed_gates_for_evidence_acceptance() -> None:
    report = build_run_health_report(
        phase_tasks={
            "build": _phase_task(
                "build",
                state=TaskState.COMPLETED,
                metadata={"quality_gate_tasks": ["CHAT-RUN::gate-review"]},
            )
        },
        gate_tasks={
            "CHAT-RUN::gate-review": WorkTask(
                task_id="CHAT-RUN::gate-review",
                title="gate review",
                description="gate",
                role=Role.REVIEWER,
                state=TaskState.BLOCKED,
                metadata={"is_gate": True},
            )
        },
    )

    assert len(report.phases) == 1
    assert report.phases[0].completed is True
    assert report.phases[0].evidence_accepted is False
    assert "Fases con evidencia aceptada: 0 / 1" in report.to_prompt_block()


def test_build_run_health_report_counts_skipped_task_state_as_skipped() -> None:
    report = build_run_health_report(
        phase_tasks={
            "build": _phase_task(
                "build",
                state=TaskState.SKIPPED,
                metadata={"skipped_reason": "lead_close_skip_phase"},
            )
        }
    )

    prompt = report.to_prompt_block()
    assert "FASES SALTADAS:" in prompt
    assert "phase=build, razon: lead_close_skip_phase" in prompt


def test_capabilities_briefing_omitted_when_all_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp)
        (runtime_dir / "provider_doctor.json").write_text(
            json.dumps(
                {
                    "api_keys": {
                        "OPENAI_API_KEY": "set",
                        "ANTHROPIC_API_KEY": "set",
                    }
                }
            ),
            encoding="utf-8",
        )
        router = HybridRouter(
            adapters=[
                _StaticAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-4o",
                    channel=ChannelType.API,
                    available=True,
                )
            ],
            policy=build_default_router_policy(),
        )
        router.runtime_dir = runtime_dir

        briefing = build_capabilities_briefing(
            router=router,
            mcp_status=[{"name": "filesystem", "enabled": True, "health_status": "healthy"}],
        )

        assert briefing == ""


def test_capabilities_briefing_ignores_missing_api_key_for_subscription_adapters() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp)
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
        router = HybridRouter(
            adapters=[
                _StaticAdapter(
                    name="openai_pro",
                    provider="openai",
                    model="gpt-pro",
                    channel=ChannelType.SUBSCRIPTION,
                    available=True,
                )
            ],
            policy=build_default_router_policy(),
        )
        router.runtime_dir = runtime_dir

        briefing = build_capabilities_briefing(router=router, mcp_status=[])

        assert briefing == ""


def test_capabilities_briefing_includes_missing_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp)
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
        router = HybridRouter(
            adapters=[
                _StaticAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-4o",
                    channel=ChannelType.API,
                    available=False,
                )
            ],
            policy=build_default_router_policy(),
        )
        router.runtime_dir = runtime_dir

        briefing = build_capabilities_briefing(router=router, mcp_status=[])

        assert "== SYSTEM CAPABILITIES ==" in briefing
        assert "Modelos NO disponibles:" in briefing
        assert "gpt-4o (advanced_api) - OPENAI_API_KEY ausente" in briefing


def test_capabilities_briefing_includes_broken_mcps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp)
        (runtime_dir / "provider_doctor.json").write_text(
            json.dumps({"api_keys": {"OPENAI_API_KEY": "set"}}),
            encoding="utf-8",
        )
        router = HybridRouter(
            adapters=[
                _StaticAdapter(
                    name="openai_api",
                    provider="openai",
                    model="gpt-4o",
                    channel=ChannelType.API,
                    available=True,
                )
            ],
            policy=build_default_router_policy(),
        )
        router.runtime_dir = runtime_dir

        briefing = build_capabilities_briefing(
            router=router,
            mcp_status=[
                {"name": "filesystem", "enabled": True, "health_status": "healthy"},
                {
                    "name": "browser_mcp",
                    "enabled": True,
                    "health_status": "unhealthy",
                    "health_reason": "timeout",
                },
            ],
        )

        assert "MCPs disponibles: filesystem" in briefing
        assert "MCPs con error: browser_mcp (timeout)" in briefing


def test_build_run_health_report_handles_empty_inputs() -> None:
    report = build_run_health_report(phase_tasks={})
    prompt = report.to_prompt_block()

    assert "Fases completadas: 0 / 0" in prompt
    assert "Fases con evidencia aceptada: 0 / 1" in prompt
    assert "PRESUPUESTO:" in prompt
    assert "Rondas usadas: 0 / 0" in prompt
    assert "== FIN REPORT ==" in prompt
