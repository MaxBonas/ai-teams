import json
from pathlib import Path

from api.chat_logic import _normalize_task_root, _recent_chat_roots, _safe_int_value
from api.chat_models import (
    OperatorTimelineItem,
    OperatorTimelineResponse,
    TeamChatProgressResponse,
)
from api.utils import (
    _event_summary,
    _peer_consultation_summary_fields,
    _read_jsonl_records,
    _read_runtime_tasks_payload,
    _read_runtime_workflow_state,
    _specialist_insight_fields,
)


def _truncate_text(value: object, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_OPERATIONAL_REASON_LABELS: dict[str, str] = {
    "dependency_failed": "bloqueada por dependencia fallida",
    "specialist_quorum_not_met": "bloqueada por quorum de especialistas",
    "no_eligible_adapter": "bloqueada por falta de adapter elegible",
    "blocked_by_files": "bloqueada por conflicto de archivos",
    "waiting_quality_gates": "esperando quality gates",
    "waiting_user": "esperando aclaracion del usuario",
    "pending": "pendiente de ejecucion",
    "ready": "lista para ejecutarse",
    "claimed": "ya reclamada por un agente",
    "carryover": "arrastrada desde una run previa",
    "blocked": "bloqueada por motivo operativo",
}


def _operational_reason_label(code: str) -> str:
    normalized = str(code or "").strip().lower()
    return _OPERATIONAL_REASON_LABELS.get(normalized, normalized or "motivo no clasificado")


def _classify_operational_bucket(
    task: dict[str, object],
    *,
    task_root: str,
) -> tuple[str, str, str] | None:
    task_id = str(task.get("task_id", "") or "").strip()
    task_state = str(task.get("state", "pending") or "pending").strip().lower()
    if task_state not in {"pending", "ready", "claimed", "blocked", "waiting_user"}:
        return None

    root_id = _normalize_task_root(task_id)
    metadata = task.get("metadata", {})
    metadata_dict = metadata if isinstance(metadata, dict) else {}

    if root_id and root_id != task_root:
        return ("carried_over_from_previous_run", "carryover", root_id)
    if task_state == "waiting_user":
        return ("waiting_user", "waiting_user", root_id)
    if task_state == "blocked":
        if metadata_dict.get("blocked_by_files"):
            return ("blocked_by_file_lock", "blocked_by_files", root_id)
        blocked_reason = str(metadata_dict.get("blocked_reason", "") or "").strip().lower()
        if blocked_reason == "dependency_failed":
            return ("blocked_by_dependency", blocked_reason, root_id)
        if blocked_reason == "specialist_quorum_not_met":
            return ("blocked_by_quorum", blocked_reason, root_id)
        if blocked_reason == "no_eligible_adapter":
            return ("blocked_by_no_eligible_adapter", blocked_reason, root_id)
        if blocked_reason == "waiting_quality_gates":
            return ("blocked_waiting_quality_gates", blocked_reason, root_id)
        return ("blocked_other", blocked_reason or "blocked", root_id)
    return ("pending", task_state or "pending", root_id)


def _summarize_chat_tasks(
    tasks_payload: object,
    *,
    task_root: str,
) -> list[dict[str, object]]:
    if not isinstance(tasks_payload, list):
        return []

    task_root_upper = task_root.upper()
    summaries: list[dict[str, object]] = []
    for item in tasks_payload:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id", "") or "").strip()
        if not task_id.upper().startswith(f"{task_root_upper}::"):
            continue

        suffix = task_id.split("::", 1)[1] if "::" in task_id else task_id
        metadata = item.get("metadata", {})
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        state = str(item.get("state", "pending") or "pending").strip().lower()
        category = "phase"
        if suffix.startswith("scout_"):
            category = "scout"
        elif suffix.startswith("delegate_"):
            category = "delegate"
        elif suffix.startswith("checkpoint_"):
            category = "checkpoint"

        error = _truncate_text(metadata_dict.get("error", ""), limit=180)
        preview_source = (
            metadata_dict.get("result")
            or metadata_dict.get("summary")
            or metadata_dict.get("_last_agent_output")
            or item.get("description", "")
        )
        preview = _truncate_text(preview_source, limit=220)
        if error and not preview:
            preview = error

        summaries.append(
            {
                "task_id": task_id,
                "short_id": suffix,
                "title": str(item.get("title", "") or "").strip(),
                "role": str(item.get("role", "") or "").strip(),
                "state": state,
                "assignee": str(item.get("assignee", "") or "").strip(),
                "category": category,
                "phase": str(metadata_dict.get("phase", "") or "").strip(),
                "provider": str(
                    metadata_dict.get("last_provider")
                    or metadata_dict.get("provider")
                    or ""
                ).strip(),
                "model": str(
                    metadata_dict.get("last_model")
                    or metadata_dict.get("model")
                    or ""
                ).strip(),
                "channel": str(
                    metadata_dict.get("last_channel")
                    or metadata_dict.get("channel")
                    or ""
                ).strip(),
                "blocked_reason": str(metadata_dict.get("blocked_reason", "") or "").strip(),
                "blocked_dependencies": [
                    str(dep).strip()
                    for dep in list(metadata_dict.get("blocked_dependencies", []) or [])
                    if str(dep).strip()
                ],
                "preview": preview,
                "error": error,
            }
        )

    category_order = {"phase": 0, "scout": 1, "delegate": 2, "checkpoint": 3}
    state_order = {
        "failed": 0,
        "running": 1,
        "claimed": 1,
        "blocked": 2,
        "waiting_user": 2,
        "pending": 3,
        "ready": 3,
        "completed": 4,
    }
    summaries.sort(
        key=lambda row: (
            int(category_order.get(str(row.get("category", "")), 9)),
            int(state_order.get(str(row.get("state", "")), 9)),
            str(row.get("short_id", "")),
        )
    )
    return summaries


def _build_task_operational_summary(
    tasks_payload: object,
    *,
    task_root: str,
) -> dict[str, object]:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root or not isinstance(tasks_payload, list):
        return {
            "has_actionable_items": False,
            "active_total": 0,
            "counts": {},
            "blocked_reasons": [],
            "sample_items": [],
            "carryover_roots": [],
        }

    counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    carryover_roots: list[str] = []
    sample_items: list[dict[str, object]] = []

    for item in tasks_payload:
        if not isinstance(item, dict):
            continue
        classification = _classify_operational_bucket(item, task_root=normalized_root)
        if classification is None:
            continue
        operational_state, reason_code, source_root = classification
        counts[operational_state] = int(counts.get(operational_state, 0)) + 1
        if operational_state.startswith("blocked_"):
            reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1
        if operational_state == "carried_over_from_previous_run" and source_root:
            if source_root not in carryover_roots:
                carryover_roots.append(source_root)

        task_id = str(item.get("task_id", "") or "").strip()
        suffix = task_id.split("::", 1)[1] if "::" in task_id else task_id
        sample_items.append(
            {
                "task_id": task_id,
                "short_id": suffix,
                "title": str(item.get("title", "") or "").strip(),
                "role": str(item.get("role", "") or "").strip(),
                "state": str(item.get("state", "") or "").strip().lower(),
                "operational_state": operational_state,
                "reason_code": reason_code,
                "reason_label": _operational_reason_label(reason_code),
                "source_root": source_root,
            }
        )

    operational_order = {
        "blocked_by_no_eligible_adapter": 0,
        "blocked_by_quorum": 1,
        "blocked_by_dependency": 2,
        "blocked_by_file_lock": 3,
        "blocked_waiting_quality_gates": 4,
        "blocked_other": 5,
        "waiting_user": 6,
        "pending": 7,
        "carried_over_from_previous_run": 8,
    }
    sample_items.sort(
        key=lambda row: (
            int(operational_order.get(str(row.get("operational_state", "")), 99)),
            str(row.get("short_id", "")),
        )
    )

    blocked_reasons = [
        {
            "code": code,
            "label": _operational_reason_label(code),
            "count": count,
        }
        for code, count in sorted(
            reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    ]

    active_total = sum(int(value) for value in counts.values())
    return {
        "has_actionable_items": active_total > 0,
        "active_total": active_total,
        "counts": counts,
        "blocked_reasons": blocked_reasons,
        "sample_items": sample_items[:8],
        "carryover_roots": carryover_roots[:6],
    }


def _coerce_phase_evidence_plan(
    payload: object,
) -> dict[str, dict[str, object]]:
    plan: dict[str, dict[str, object]] = {}
    if not isinstance(payload, dict):
        return plan
    for raw_phase_id, raw_entry in payload.items():
        phase_id = str(raw_phase_id or "").strip()
        if not phase_id or not isinstance(raw_entry, dict):
            continue
        entry: dict[str, object] = {}
        intents = [
            str(item).strip().lower()
            for item in list(raw_entry.get("delegate_intents", []) or [])
            if str(item).strip()
        ]
        if intents:
            entry["delegate_intents"] = list(dict.fromkeys(intents))
        wait_policy = str(raw_entry.get("wait_policy", "") or "").strip().lower()
        if wait_policy in {"all", "best_effort", "quorum"}:
            entry["wait_policy"] = wait_policy
        if "delegate_budget" in raw_entry:
            entry["delegate_budget"] = max(
                1,
                _safe_int_value(raw_entry.get("delegate_budget", 3), 3),
            )
        if entry:
            plan[phase_id] = entry
    return plan


def _coerce_delegate_batches(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list):
        return []
    batches: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        batch = {str(key): value for key, value in item.items() if str(key).strip()}
        if batch:
            batches.append(batch)
    return batches


def _build_chat_progress(runtime_dir: Path, task_root: str) -> TeamChatProgressResponse:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return TeamChatProgressResponse(task_id="", exists=False)

    phase_states: dict[str, str] = {}
    rounds_used = 0
    round_budget = 0
    exists = False
    failed_tasks = 0
    execution_attempts = 0
    execution_steps = 0
    execution_steps_success = 0
    execution_mode = "queued"
    placeholder_outputs = 0
    successful_checks: list[str] = []
    evidence_gate_rejected = False
    evidence_gate_failures: list[str] = []
    live_mode_required = False
    live_mode_rejected = False
    phase_evidence_plan: dict[str, dict[str, object]] = {}
    delegate_batches: list[dict[str, object]] = []
    delegate_economics: dict[str, object] = {}

    workflow_state_payload = _read_runtime_workflow_state(runtime_dir)
    if isinstance(workflow_state_payload, dict):
        workflow_entry = workflow_state_payload.get(normalized_root, {})
        if isinstance(workflow_entry, dict):
            phase_evidence_plan = _coerce_phase_evidence_plan(
                workflow_entry.get("phase_evidence_plan", {})
            )
            delegate_batches = _coerce_delegate_batches(
                workflow_entry.get("delegate_batches", [])
            )
            delegate_economics = dict(
                workflow_entry.get("delegate_economics_summary", {}) or {}
            )
    specialist_insights = _specialist_insight_fields(runtime_dir, normalized_root)
    specialist_reports = list(specialist_insights.get("specialist_reports", []) or [])
    specialist_report_summary = dict(
        specialist_insights.get("specialist_report_summary", {}) or {}
    )
    peer_consultation_summary = dict(
        _peer_consultation_summary_fields(runtime_dir, normalized_root).get(
            "peer_consultation_summary", {}
        )
        or {}
    )

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    task_summaries = _summarize_chat_tasks(tasks_payload, task_root=normalized_root)
    task_operational_summary = _build_task_operational_summary(
        tasks_payload,
        task_root=normalized_root,
    )
    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "")
            task_id_upper = task_id.upper()
            if not task_id_upper.startswith(f"{normalized_root}::"):
                continue
            exists = True
            phase_name = task_id.split("::", 1)[1]
            state_value = str(item.get("state", "pending") or "pending")
            phase_states[phase_name] = state_value
            if state_value == "failed":
                failed_tasks += 1
            metadata = item.get("metadata", {})
            if isinstance(metadata, dict):
                rounds_used = max(
                    rounds_used, _safe_int_value(metadata.get("execution_round", 0), 0)
                )

    last_event = ""
    last_event_ts = ""
    exhausted = False
    root_event_seen = False
    for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
        event_type = str(record.get("event_type", "") or "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        event_task_id = str(payload.get("task_id", "") or "")
        event_task_id_upper = event_task_id.upper()
        is_root_related = (
            event_task_id_upper == normalized_root
            or event_task_id_upper.startswith(f"{normalized_root}::")
        )
        if not is_root_related:
            continue
        root_event_seen = True
        if event_type == "chat_plan_created" and event_task_id_upper == normalized_root:
            round_budget = max(
                round_budget, _safe_int_value(payload.get("round_budget", 0), 0)
            )
            if not phase_evidence_plan:
                phase_evidence_plan = _coerce_phase_evidence_plan(
                    payload.get("phase_evidence_plan", {})
                )
        if (
            event_type == "chat_auto_rounds_extended"
            and event_task_id_upper == normalized_root
        ):
            round_budget = max(
                round_budget, _safe_int_value(payload.get("to_round_budget", 0), 0)
            )
        if (
            event_type == "chat_execution_mode_assessed"
            and event_task_id_upper == normalized_root
        ):
            execution_mode = str(
                payload.get("execution_mode", execution_mode) or execution_mode
            )
            placeholder_outputs = max(
                placeholder_outputs,
                _safe_int_value(payload.get("placeholder_outputs", 0), 0),
            )
            live_mode_required = bool(
                payload.get("live_mode_required", live_mode_required)
            )
        if (
            event_type == "chat_quality_assessed"
            and event_task_id_upper == normalized_root
        ):
            raw_checks = payload.get("successful_checks", [])
            if isinstance(raw_checks, list):
                successful_checks = sorted(
                    {
                        str(item or "").strip()
                        for item in raw_checks
                        if str(item or "").strip()
                    }
                )
        if (
            event_type == "chat_evidence_gate_rejected"
            and event_task_id_upper == normalized_root
        ):
            evidence_gate_rejected = True
            raw_failures = payload.get("failures", [])
            if isinstance(raw_failures, list):
                evidence_gate_failures = [
                    str(item or "").strip()
                    for item in raw_failures
                    if str(item or "").strip()
                ][:12]
        if (
            event_type == "chat_live_mode_required_rejected"
            and event_task_id_upper == normalized_root
        ):
            live_mode_required = True
            live_mode_rejected = True
        if (
            event_type == "chat_window_exhausted"
            and event_task_id_upper == normalized_root
        ):
            exhausted = True
            rounds_used = max(
                rounds_used, _safe_int_value(payload.get("rounds_used", 0), 0)
            )
        if event_type == "task_execution":
            execution_attempts += 1
            rounds_used = max(
                rounds_used, _safe_int_value(payload.get("execution_round", 0), 0)
            )
        if event_type == "execution_step":
            execution_steps += 1
            if bool(payload.get("success", False)):
                execution_steps_success += 1
        last_event = _event_summary(event_type, payload)
        last_event_ts = str(record.get("ts", "") or "")

    exists = exists or root_event_seen
    completed_tasks = sum(1 for state in phase_states.values() if state == "completed")
    active_states = {"pending", "ready", "claimed", "blocked", "waiting_user"}
    pending_tasks = sum(1 for state in phase_states.values() if state in active_states)
    lead_state = phase_states.get("lead_close", "")

    waiting_user = False
    waiting_question = ""
    pending_clarify = runtime_dir / f"pending_clarification_{normalized_root}.json"
    if pending_clarify.exists():
        try:
            pending_clarify_state = json.loads(pending_clarify.read_text(encoding="utf-8"))
            if pending_clarify_state.get("type") in ("mid_run", "lead_intake"):
                waiting_user = True
                waiting_question = str(pending_clarify_state.get("question", ""))
        except Exception:
            pass
    if not waiting_user:
        waiting_user = any(state == "waiting_user" for state in phase_states.values())

    response_kwargs = dict(
        task_id=normalized_root,
        round_budget=round_budget,
        rounds_used=rounds_used,
        phase_states=phase_states,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        failed_tasks=failed_tasks,
        execution_attempts=execution_attempts,
        execution_steps=execution_steps,
        execution_steps_success=execution_steps_success,
        execution_mode=execution_mode,
        placeholder_outputs=placeholder_outputs,
        successful_checks=successful_checks,
        successful_check_count=len(successful_checks),
        live_mode_required=live_mode_required,
        live_mode_rejected=live_mode_rejected,
        evidence_gate_rejected=evidence_gate_rejected,
        evidence_gate_failures=evidence_gate_failures,
        last_event=last_event,
        last_event_ts=last_event_ts,
        phase_evidence_plan=phase_evidence_plan,
        delegate_batches=delegate_batches,
        delegate_economics=delegate_economics,
        specialist_reports=specialist_reports,
        specialist_report_summary=specialist_report_summary,
        peer_consultation_summary=peer_consultation_summary,
        task_summaries=task_summaries,
        task_operational_summary=task_operational_summary,
    )

    if not exists:
        return TeamChatProgressResponse(
            exists=False,
            state="queued",
            **response_kwargs,
        )

    if evidence_gate_rejected:
        progress_state = "rejected"
    elif failed_tasks > 0 or lead_state == "failed":
        progress_state = "failed"
    elif waiting_user:
        progress_state = "waiting_user"
    elif lead_state == "completed" and pending_tasks == 0:
        progress_state = "completed"
    elif exhausted:
        progress_state = "in_progress"
    elif pending_tasks > 0:
        progress_state = "running"
    elif completed_tasks > 0:
        progress_state = "completed"
    else:
        progress_state = "running"

    progress_phase_task_ids = {
        name: f"{normalized_root}::{name}" for name in phase_states
    }
    dynamic_phases_ready = any(
        name not in ("lead_intake", "lead_close") for name in phase_states
    )

    return TeamChatProgressResponse(
        exists=True,
        state=progress_state,
        dynamic_phases_ready=dynamic_phases_ready,
        phase_task_ids=progress_phase_task_ids,
        waiting_user=waiting_user,
        clarification_question=waiting_question,
        **response_kwargs,
    )


def _build_operator_timeline(
    runtime_dir: Path,
    *,
    task_id: str,
    limit: int,
    key_only: bool,
) -> OperatorTimelineResponse:
    recent_runs = _recent_chat_roots(runtime_dir, max_chats=24)
    available_runs: list[str] = []
    for item in recent_runs:
        if not isinstance(item, dict):
            continue
        root_id = _normalize_task_root(str(item.get("root_id", "") or ""))
        if root_id and root_id not in available_runs:
            available_runs.append(root_id)

    latest_task_id = available_runs[0] if available_runs else ""
    selected_task_id = _normalize_task_root(task_id) or latest_task_id

    if not selected_task_id:
        return OperatorTimelineResponse(
            selected_task_id="",
            latest_task_id="",
            available_runs=available_runs,
            total=0,
            items=[],
            progress=None,
        )

    key_events = {
        "chat_plan_created",
        "task_execution",
        "execution_step",
        "chat_artifact_bootstrap",
        "chat_artifacts_detected",
        "chat_auto_rounds_extended",
        "chat_quality_assessed",
        "chat_strict_mode_blocked_close",
        "chat_low_productivity_rejected",
        "chat_low_productivity_override",
        "chat_window_exhausted",
        "task_failed",
    }

    records = _read_jsonl_records(runtime_dir / "events.jsonl")
    timeline_items: list[OperatorTimelineItem] = []
    for record in records:
        event_type = str(record.get("event_type", "") or "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue

        event_task_id = str(payload.get("task_id", "") or "")
        event_task_root = _normalize_task_root(event_task_id)
        if not event_task_root and "::" in event_task_id:
            event_task_root = _normalize_task_root(event_task_id.split("::", 1)[0])
        if event_task_root != selected_task_id:
            continue
        if key_only and event_type not in key_events:
            continue

        level = "info"
        if event_type in {
            "task_failed",
            "chat_low_productivity_rejected",
            "chat_strict_mode_blocked_close",
        }:
            level = "error"
        elif event_type in {"chat_window_exhausted", "chat_auto_rounds_extended"}:
            level = "warn"
        elif event_type == "task_execution":
            level = "info" if bool(payload.get("success", False)) else "error"
        elif event_type == "execution_step":
            level = "info" if bool(payload.get("success", False)) else "warn"

        raw_files = payload.get("files", [])
        files = raw_files if isinstance(raw_files, list) else []
        timeline_items.append(
            OperatorTimelineItem(
                ts=str(record.get("ts", "") or ""),
                event_type=event_type,
                task_id=event_task_id,
                level=level,
                summary=_event_summary(event_type, payload),
                assignee=str(payload.get("assignee", "") or ""),
                execution_round=_safe_int_value(payload.get("execution_round", 0), 0),
                execution_sub_iteration=_safe_int_value(
                    payload.get(
                        "execution_sub_iteration", payload.get("sub_iteration", 0)
                    ),
                    0,
                ),
                gate_iteration=_safe_int_value(
                    payload.get("gate_iteration", payload.get("iteration", 0)), 0
                ),
                blocked_reason=str(payload.get("blocked_reason", "") or ""),
                handoff_from=str(payload.get("from", "") or ""),
                handoff_to=str(payload.get("to", "") or ""),
                conversation_thread_id=str(payload.get("thread_id", "") or ""),
                meeting_kind=str(payload.get("meeting_kind", "") or ""),
                artifact_created=_safe_int_value(payload.get("created", 0), 0),
                artifact_modified=_safe_int_value(payload.get("modified", 0), 0),
                artifact_files=[
                    str(item or "") for item in files if str(item or "").strip()
                ][:16],
                productivity_score=_safe_int_value(
                    payload.get("productivity_score", 0), 0
                ),
                reasoning_score=_safe_int_value(payload.get("reasoning_score", 0), 0),
            )
        )

    timeline_items.sort(key=lambda item: item.ts, reverse=True)
    effective_limit = max(20, min(limit, 300))
    limited_items = timeline_items[:effective_limit]
    progress = _build_chat_progress(runtime_dir, selected_task_id)

    return OperatorTimelineResponse(
        selected_task_id=selected_task_id,
        latest_task_id=latest_task_id,
        available_runs=available_runs,
        total=len(timeline_items),
        items=limited_items,
        progress=progress,
    )
