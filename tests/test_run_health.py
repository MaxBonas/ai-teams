from aiteam.run_health import build_run_health_report
from aiteam.types import Role, TaskState, WorkTask


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


def test_build_run_health_report_handles_empty_inputs() -> None:
    report = build_run_health_report(phase_tasks={})
    prompt = report.to_prompt_block()

    assert "Fases completadas: 0 / 0" in prompt
    assert "Fases con evidencia aceptada: 0 / 1" in prompt
    assert "PRESUPUESTO:" in prompt
    assert "Rondas usadas: 0 / 0" in prompt
    assert "== FIN REPORT ==" in prompt
