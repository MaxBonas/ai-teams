from __future__ import annotations

from dataclasses import dataclass, field

from aiteam.types import TaskState, WorkTask


def _compact(value: object, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _unique_sorted(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in values:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return sorted(output)


@dataclass
class PhaseHealthEntry:
    phase_id: str
    gate_iterations: int = 0
    gate_max: int = 0
    last_gate_reason: str = ""
    routing_errors: list[dict[str, str]] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    completed: bool = False
    evidence_accepted: bool = False


@dataclass
class RunHealthReport:
    phases: list[PhaseHealthEntry] = field(default_factory=list)
    missing_api_keys: list[str] = field(default_factory=list)
    unavailable_models: list[str] = field(default_factory=list)
    rounds_used: int = 0
    round_budget: int = 0
    auto_extensions: int = 0

    def to_prompt_block(self) -> str:
        lines = ["== RUN HEALTH REPORT =="]
        completed = sum(1 for item in self.phases if item.completed)
        accepted = sum(1 for item in self.phases if item.evidence_accepted)
        lines.append(f"Fases completadas: {completed} / {len(self.phases)}")
        lines.append(f"Fases con evidencia aceptada: {accepted} / {max(completed, 1)}")

        gate_issues = [
            item
            for item in self.phases
            if item.gate_iterations > 0 or item.last_gate_reason
        ]
        if gate_issues:
            lines.append("")
            lines.append("GATE REJECTIONS:")
            for item in gate_issues:
                gate_max = item.gate_max if item.gate_max > 0 else max(item.gate_iterations, 1)
                lines.append(
                    f"  - phase={item.phase_id}, iterations={item.gate_iterations}/{gate_max}, "
                    f"ultima razon: {_compact(item.last_gate_reason or 'desconocida', 180)}"
                )

        routing_issues = [item for item in self.phases if item.routing_errors]
        if routing_issues:
            lines.append("")
            lines.append("ROUTING ERRORS:")
            for item in routing_issues:
                for error in item.routing_errors:
                    role = _compact(error.get("role", "?"), 40)
                    model = _compact(error.get("model", "?"), 60)
                    reason = _compact(error.get("error", "unknown"), 140)
                    lines.append(
                        f"  - phase={item.phase_id}, error={reason}, role={role}, model={model}"
                    )

        skipped = [item for item in self.phases if item.skipped]
        if skipped:
            lines.append("")
            lines.append("FASES SALTADAS:")
            for item in skipped:
                lines.append(
                    f"  - phase={item.phase_id}, razon: {_compact(item.skip_reason or 'lead_decision', 180)}"
                )

        if self.missing_api_keys or self.unavailable_models:
            lines.append("")
            lines.append("RECURSOS NO DISPONIBLES:")
            for key_name in _unique_sorted(list(self.missing_api_keys)):
                lines.append(f"  - API key ausente: {key_name}")
            for model_name in _unique_sorted(list(self.unavailable_models)):
                lines.append(f"  - Modelo no disponible: {model_name}")

        lines.append("")
        lines.append("PRESUPUESTO:")
        lines.append(f"  - Rondas usadas: {max(0, int(self.rounds_used or 0))} / {max(0, int(self.round_budget or 0))}")
        if int(self.auto_extensions or 0) > 0:
            lines.append(f"  - Extensiones automaticas: {int(self.auto_extensions)}")

        lines.append("== FIN REPORT ==")
        return "\n".join(lines)


def build_run_health_report(
    *,
    phase_tasks: dict[str, WorkTask],
    gate_tasks: dict[str, WorkTask] | None = None,
    routing_failures: list[dict[str, str]] | None = None,
    missing_api_keys: list[str] | None = None,
    unavailable_models: list[str] | None = None,
    rounds_used: int = 0,
    round_budget: int = 0,
    auto_extensions: int = 0,
) -> RunHealthReport:
    gate_lookup = dict(gate_tasks or {})
    routing_lookup: dict[str, list[dict[str, str]]] = {}
    for item in list(routing_failures or []):
        phase_id = str(item.get("phase", "") or "").strip()
        if not phase_id:
            continue
        routing_lookup.setdefault(phase_id, []).append(
            {
                "error": _compact(item.get("error", "") or item.get("reason", "") or "unknown", 160),
                "role": _compact(item.get("role", "") or "?", 40),
                "model": _compact(item.get("model", "") or "?", 80),
            }
        )

    phase_entries: list[PhaseHealthEntry] = []
    for phase_id, task in phase_tasks.items():
        gate_iterations = int(task.metadata.get("gate_iteration", 0) or 0)
        gate_max = int(task.metadata.get("max_gate_iterations", 0) or 0)
        last_gate_reason = str(task.metadata.get("review_feedback", "") or "").strip()
        quality_gate_ids = list(task.metadata.get("quality_gate_tasks", []) or [])
        if not gate_max and quality_gate_ids:
            gate_max = 2
        evidence_accepted = task.state == TaskState.COMPLETED
        if quality_gate_ids:
            evidence_accepted = evidence_accepted and all(
                gate_lookup.get(gate_id) is not None
                and gate_lookup[gate_id].state == TaskState.COMPLETED
                for gate_id in quality_gate_ids
            )
        skipped = task.state == TaskState.ARCHIVED
        skip_reason = str(
            task.metadata.get("archived_reason", "") or task.metadata.get("skip_reason", "") or ""
        ).strip()
        phase_entries.append(
            PhaseHealthEntry(
                phase_id=phase_id,
                gate_iterations=gate_iterations,
                gate_max=gate_max,
                last_gate_reason=last_gate_reason,
                routing_errors=list(routing_lookup.get(phase_id, [])),
                skipped=skipped,
                skip_reason=skip_reason,
                completed=task.state == TaskState.COMPLETED,
                evidence_accepted=evidence_accepted,
            )
        )

    return RunHealthReport(
        phases=sorted(phase_entries, key=lambda item: item.phase_id),
        missing_api_keys=_unique_sorted(list(missing_api_keys or [])),
        unavailable_models=_unique_sorted(list(unavailable_models or [])),
        rounds_used=max(0, int(rounds_used or 0)),
        round_budget=max(0, int(round_budget or 0)),
        auto_extensions=max(0, int(auto_extensions or 0)),
    )
