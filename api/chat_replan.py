from aiteam.lead_control import (
    extract_delegate_request as _lead_control_extract_delegate_request,
    iter_lead_checkpoint_directives as _lead_control_iter_lead_checkpoint_directives,
    strip_selected_lcp_directives as _lead_control_strip_selected_lcp_directives,
)
from aiteam.types import WorkTask
from aiteam.workflow_planner import PhaseSpec, parse_workflow_plan

from api.chat_logic import _safe_int_value


def _replan_window_is_open(phase_states: dict[str, str], workflow_phase_keys: list[str]) -> bool:
    """MVP E9-O6: solo permitir REPLAN si ninguna fase dinamica ha empezado."""

    dynamic_phases = [
        phase
        for phase in workflow_phase_keys
        if phase not in {"lead_intake", "lead_close"} and not phase.startswith("lead_")
    ]
    if not dynamic_phases:
        return False
    allowed_states = {"pending", "ready", "blocked"}
    return all(
        str(phase_states.get(phase, "") or "").strip().lower() in allowed_states
        for phase in dynamic_phases
    )


def _extract_replan_phases_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, list[PhaseSpec]] | None:
    """Busca un REPLAN emitido por algun checkpoint del Lead con WORKFLOW_PLAN valido."""

    for phase_name, output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=True,
        reverse=True,
    ):
        if not directives.get("replan"):
            continue
        parsed = parse_workflow_plan(output)
        if parsed:
            return phase_name, parsed
    return None


def _replan_skip_reason(source_phase: str) -> str:
    normalized = str(source_phase or "").strip()
    if normalized == "lead_close":
        return "lead_close_completed_plan"
    return ""


def _extract_force_gate_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, str] | None:
    """Busca un FORCE_GATE emitido por algun checkpoint del Lead."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=True,
        reverse=True,
    ):
        target = str(directives.get("force_gate", "") or "").strip()
        if target:
            return phase_name, target
    return None


def _extract_abort_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, str] | None:
    """Busca un ABORT_PHASES emitido por un checkpoint mid-run del Lead."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        reason = str(directives.get("abort_phases", "") or "").strip()
        if reason:
            return phase_name, reason
    return None


def _extract_skip_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, list[str]] | None:
    """Busca un SKIP emitido por un checkpoint mid-run del Lead."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        targets = [
            str(item).strip()
            for item in list(directives.get("skip") or [])
            if str(item).strip()
        ]
        if targets:
            return phase_name, targets
    return None


def _extract_retry_route_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, str] | None:
    """Busca un RETRY_ROUTE emitido por un checkpoint mid-run del Lead."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        target = str(directives.get("retry_route", "") or "").strip()
        if target:
            return phase_name, target
    return None


def _extract_advisory_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, str] | None:
    """Busca un ADVISORY_MODE emitido por un checkpoint mid-run del Lead."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        reason = str(directives.get("advisory_mode", "") or "").strip()
        if reason:
            return phase_name, reason
    return None


def _extract_pause_for_user_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, str] | None:
    """Busca un PAUSE_FOR_USER emitido por el Lead al cierre del run."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        question = str(directives.get("pause_for_user", "") or "").strip()
        if question:
            return phase_name, question
    return None


def _extract_skip_phase_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, dict[str, str]] | None:
    """Busca un SKIP_PHASE emitido por el Lead al cierre del run."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        payload = directives.get("skip_phase")
        if not isinstance(payload, dict):
            continue
        phase_id = str(payload.get("phase_id", "") or "").strip()
        if phase_id:
            return phase_name, {
                "phase_id": phase_id,
                "reason": str(payload.get("reason", "") or "").strip(),
            }
    return None


def _extract_degrade_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, dict[str, str]] | None:
    """Busca un DEGRADE emitido por el Lead al cierre del run."""

    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        payload = directives.get("degrade")
        if not isinstance(payload, dict):
            continue
        scope = str(payload.get("scope", "") or "").strip().lower()
        if scope in {"minimal", "partial"}:
            return phase_name, {
                "scope": scope,
                "reason": str(payload.get("reason", "") or "").strip(),
            }
    return None


def _extract_budget_adjustments_from_outputs(
    phase_outputs: dict[str, str],
) -> list[tuple[str, dict[str, object]]]:
    """Recoge ajustes de budget emitidos por checkpoints del Lead en orden temporal."""

    adjustments: list[tuple[str, dict[str, object]]] = []
    for phase_name, _output, directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=False,
    ):
        payload: dict[str, object] = {}
        if directives.get("escalate"):
            payload["escalate"] = directives["escalate"]
        if directives.get("extend_budget"):
            payload["extend_budget"] = directives["extend_budget"]
        if directives.get("set_budget"):
            payload["set_budget"] = directives["set_budget"]
        if payload:
            adjustments.append((phase_name, payload))
    return adjustments


def _extract_delegate_request_from_outputs(
    phase_outputs: dict[str, str],
) -> tuple[str, object] | None:
    """Busca una delegacion especializada emitida por un checkpoint mid-run del Lead."""

    for phase_name, output, _directives in _lead_control_iter_lead_checkpoint_directives(
        phase_outputs,
        include_lead_intake=False,
        reverse=True,
    ):
        request = _lead_control_extract_delegate_request(output)
        if request is not None:
            return phase_name, request
    return None


def _phase_started_for_replan(task: WorkTask | None) -> bool:
    """Determina si una fase ya empezo y no debe ser reemplazada por REPLAN parcial."""

    if task is None:
        return False
    state = str(task.state.value if hasattr(task.state, "value") else task.state).strip().lower()
    if state in {"claimed", "completed", "skipped", "failed", "waiting_user"}:
        return True
    if _safe_int_value(task.metadata.get("execution_round", 0), 0) > 0:
        return True
    if state == "blocked":
        if (
            task.metadata.get("result")
            or task.metadata.get("error")
            or task.metadata.get("waiting_since")
            or task.metadata.get("gate_opened_at")
            or task.metadata.get("quality_gate_tasks")
        ):
            return True
    return False


def _merge_replanned_phases(
    current_phases: list[PhaseSpec],
    tasks_by_phase: dict[str, WorkTask | None],
    replan_phases: list[PhaseSpec],
) -> tuple[list[PhaseSpec], list[str], list[str]]:
    """Fusiona un REPLAN con el estado actual preservando fases ya iniciadas."""

    preserved_specs: list[PhaseSpec] = []
    preserved_phase_ids: list[str] = []
    preserved_task_ids: list[str] = []
    for spec in current_phases:
        current_task = tasks_by_phase.get(spec.phase_id)
        if not _phase_started_for_replan(current_task):
            continue
        preserved_specs.append(spec)
        preserved_phase_ids.append(spec.phase_id)
        if current_task is not None:
            preserved_task_ids.append(current_task.task_id)

    preserved_set = set(preserved_phase_ids)
    merged = preserved_specs + [
        spec for spec in replan_phases if spec.phase_id not in preserved_set
    ]
    return merged, preserved_phase_ids, preserved_task_ids


def _prune_phases_for_mid_run_lead_action(
    current_phases: list[PhaseSpec],
    tasks_by_phase: dict[str, WorkTask | None],
    target_phase_ids: list[str] | None = None,
    abort_all_pending: bool = False,
) -> tuple[list[PhaseSpec], list[str], list[str], list[str]]:
    """Elimina fases no iniciadas por instruccion mid-run del Lead."""

    started_phase_ids = {
        spec.phase_id
        for spec in current_phases
        if _phase_started_for_replan(tasks_by_phase.get(spec.phase_id))
    }

    raw_targets = {
        str(item).strip()
        for item in list(target_phase_ids or [])
        if str(item).strip()
    }
    skipped_started_targets = sorted(raw_targets & started_phase_ids)

    if abort_all_pending:
        removed_phase_ids = {
            spec.phase_id
            for spec in current_phases
            if spec.phase_id not in started_phase_ids
        }
    else:
        removed_phase_ids = {
            phase_id
            for phase_id in raw_targets
            if phase_id in {spec.phase_id for spec in current_phases}
            and phase_id not in started_phase_ids
        }

    changed = True
    while changed:
        changed = False
        for spec in current_phases:
            if spec.phase_id in removed_phase_ids or spec.phase_id in started_phase_ids:
                continue
            if any(dep in removed_phase_ids for dep in spec.depends_on):
                removed_phase_ids.add(spec.phase_id)
                changed = True

    new_phases = [
        spec for spec in current_phases if spec.phase_id not in removed_phase_ids
    ]
    preserved_started_phase_ids = [
        spec.phase_id for spec in current_phases if spec.phase_id in started_phase_ids
    ]
    return (
        new_phases,
        sorted(removed_phase_ids),
        preserved_started_phase_ids,
        skipped_started_targets,
    )


def _retry_route_removal_phase_ids(
    current_phases: list[PhaseSpec],
    target_phase_id: str,
) -> list[str]:
    """Calcula la fase objetivo y todo su downstream transitivo para reintento."""

    existing_ids = {spec.phase_id for spec in current_phases}
    if target_phase_id not in existing_ids:
        return []

    removed = {target_phase_id}
    changed = True
    while changed:
        changed = False
        for spec in current_phases:
            if spec.phase_id in removed:
                continue
            if any(dep in removed for dep in spec.depends_on):
                removed.add(spec.phase_id)
                changed = True
    return [spec.phase_id for spec in current_phases if spec.phase_id in removed]


def _strip_selected_directives(text: str, directives: list[str]) -> str:
    """Elimina solo un subconjunto de directivas LCP del texto."""

    return _lead_control_strip_selected_lcp_directives(text, directives)
