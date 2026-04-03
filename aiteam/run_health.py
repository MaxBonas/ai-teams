from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

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


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def build_capabilities_briefing(*, router, mcp_status: object | None = None) -> str:
    runtime_dir_value = getattr(router, "runtime_dir", None)
    runtime_dir = Path(runtime_dir_value) if runtime_dir_value else None
    doctor_payload = (
        _read_json(runtime_dir / "provider_doctor.json")
        if runtime_dir is not None
        else {}
    )
    api_key_status = dict(doctor_payload.get("api_keys", {}) or {})

    available_rows: list[tuple[str, str, str]] = []
    unavailable_rows: list[tuple[str, str, str]] = []
    seen_available: set[tuple[str, str]] = set()
    seen_unavailable: set[tuple[str, str, str]] = set()

    for adapter in list(getattr(router, "adapters", []) or []):
        profile = router._profile_for(adapter) if hasattr(router, "_profile_for") else None
        tier = str(getattr(profile, "tier", "") or adapter.channel.value or "unknown").strip()
        model_name = str(getattr(adapter, "model", "") or getattr(adapter, "name", "") or "unknown").strip()
        provider_name = str(getattr(adapter, "provider", "") or "").strip().lower()
        channel_value = str(getattr(getattr(adapter, "channel", None), "value", "") or "").strip().lower()
        key_env = _PROVIDER_API_KEY_ENV.get(provider_name, "")

        try:
            available = bool(adapter.available())
        except Exception:
            available = False

        operational = True
        if hasattr(router, "_operational_ok"):
            try:
                operational = bool(router._operational_ok(adapter))
            except Exception:
                operational = available

        if (
            channel_value == "api"
            and key_env
            and str(api_key_status.get(key_env, "")).strip().lower() == "missing"
        ):
            row = (model_name, tier, f"{key_env} ausente")
            if row not in seen_unavailable:
                unavailable_rows.append(row)
                seen_unavailable.add(row)
            continue

        if not available:
            row = (model_name, tier, "adapter no disponible")
            if row not in seen_unavailable:
                unavailable_rows.append(row)
                seen_unavailable.add(row)
            continue

        if not operational:
            reason = "estado no operativo"
            if hasattr(router, "_cached_ops_status"):
                try:
                    ops_status = dict(router._cached_ops_status().get(adapter.name, {}) or {})
                except Exception:
                    ops_status = {}
                reason = (
                    str(ops_status.get("smoke_details", "") or "").strip()
                    or str(ops_status.get("doctor_details", "") or "").strip()
                    or reason
                )
            row = (model_name, tier, _compact(reason, 120))
            if row not in seen_unavailable:
                unavailable_rows.append(row)
                seen_unavailable.add(row)
            continue

        row = (model_name, tier, "disponible")
        if (model_name, tier) not in seen_available:
            available_rows.append(row)
            seen_available.add((model_name, tier))

    mcp_rows = list(mcp_status) if isinstance(mcp_status, list) else list(dict(mcp_status or {}).values()) if isinstance(mcp_status, dict) and "servers" not in dict(mcp_status or {}) else list(dict(mcp_status or {}).get("servers", []) or []) if isinstance(mcp_status, dict) else []
    working_mcps: list[str] = []
    broken_mcps: list[str] = []
    for item in mcp_rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or item.get("server", "") or "").strip()
        if not name or not bool(item.get("enabled", True)):
            continue
        health_status = str(item.get("health_status", "") or item.get("status", "") or "").strip().lower()
        health_reason = str(item.get("health_reason", "") or item.get("reason", "") or "").strip()
        if health_status in {"", "healthy", "ok", "running"}:
            working_mcps.append(name)
        else:
            broken_mcps.append(
                f"{name} ({_compact(health_reason or health_status, 80)})"
            )

    if not unavailable_rows and not broken_mcps:
        return ""

    lines = ["== SYSTEM CAPABILITIES =="]
    if available_rows:
        lines.append("Modelos disponibles:")
        for model_name, tier, _status in sorted(available_rows):
            lines.append(f"  - {model_name} ({tier}) - disponible")
    if unavailable_rows:
        lines.append("")
        lines.append("Modelos NO disponibles:")
        for model_name, tier, reason in sorted(unavailable_rows):
            lines.append(f"  - {model_name} ({tier}) - {reason}")
    if working_mcps:
        lines.append("")
        lines.append(f"MCPs disponibles: {', '.join(sorted(dict.fromkeys(working_mcps)))}")
    if broken_mcps:
        lines.append(f"MCPs con error: {', '.join(sorted(dict.fromkeys(broken_mcps)))}")
    lines.append("== FIN CAPABILITIES ==")
    return "\n".join(lines)


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
    execution_steps_total: int = 0
    execution_steps_success: int = 0

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

        if self.execution_steps_total > 0 or self.execution_steps_success > 0:
            lines.append("")
            lines.append("EJECUCION DE PASOS:")
            lines.append(f"  - Pasos totales: {self.execution_steps_total}")
            lines.append(f"  - Pasos exitosos: {self.execution_steps_success}")
            delivery = "code_block_extraction (archivos escritos)" if self.execution_steps_success > 0 else "sin pasos exitosos"
            lines.append(f"  - Tipo: {delivery}")

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
    execution_steps_total: int = 0,
    execution_steps_success: int = 0,
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
        skipped = task.state in {TaskState.ARCHIVED, TaskState.SKIPPED}
        skip_reason = str(
            task.metadata.get("archived_reason", "")
            or task.metadata.get("skipped_reason", "")
            or task.metadata.get("skip_reason", "")
            or ""
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
        execution_steps_total=max(0, int(execution_steps_total or 0)),
        execution_steps_success=max(0, int(execution_steps_success or 0)),
    )
