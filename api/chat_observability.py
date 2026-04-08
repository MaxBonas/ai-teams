import json
from pathlib import Path

from aiteam.sim_mode import sim_mode_enabled
from aiteam.lead_close_policy import derive_lead_close_policy
from aiteam.phase_verdicts import coerce_phase_verdicts, derive_run_verdict_from_phase_verdicts
from api.chat_logic import _normalize_task_root, _recent_chat_roots, _safe_int_value
from api.chat_models import (
    OperatorTimelineItem,
    OperatorTimelineResponse,
    TeamChatProgressResponse,
)
from api.utils import (
    _display_ts_local,
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
    "policy_review_required": "requiere revision de policy antes de continuar",
    "semantic_gate_failed": "bloqueada por semantic gate",
    "evidence_gate_failed": "bloqueada por evidence gate",
    "review_rejected": "review rechazado por veredicto autoritativo",
    "qa_blocked": "qa bloqueada por veredicto autoritativo",
    "slice_drift": "deriva de slice detectada por contrato",
    "run_rejected": "run rechazada por policy autoritativa",
    "run_failed": "run fallida por policy autoritativa",
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
    task_threads: dict[str, dict[str, object]] | None = None,
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
        thread_meta = (
            task_threads.get(task_id, {}) if isinstance(task_threads, dict) else {}
        )
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
        full_text = str(preview_source or "").strip()
        preview = _truncate_text(preview_source, limit=420)
        if error and not preview:
            preview = error
        if error and not full_text:
            full_text = error

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
                "thread_id": str(thread_meta.get("thread_id", "") or "").strip(),
                "thread_provider": str(
                    thread_meta.get("thread_provider", "")
                    or thread_meta.get("provider", "")
                    or ""
                ).strip(),
                "thread_channel": str(
                    thread_meta.get("thread_channel", "")
                    or thread_meta.get("channel", "")
                    or ""
                ).strip(),
                "thread_model_family": str(
                    thread_meta.get("thread_model_family", "")
                    or thread_meta.get("model_family", "")
                    or ""
                ).strip(),
                "thread_generation": _safe_int_value(
                    thread_meta.get("thread_generation", 0), 0
                ),
                "blocked_reason": str(metadata_dict.get("blocked_reason", "") or "").strip(),
                "blocked_dependencies": [
                    str(dep).strip()
                    for dep in list(metadata_dict.get("blocked_dependencies", []) or [])
                    if str(dep).strip()
                ],
                "preview": preview,
                "full_text": full_text,
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
        "skipped": 4,
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


def _collect_chat_thread_metadata(
    runtime_dir: Path,
    task_root: str,
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    normalized_root = _normalize_task_root(task_root)
    task_threads: dict[str, dict[str, object]] = {}
    latest_thread_summary: dict[str, object] = {}
    rebound_count = 0
    candidate_count = 0
    distinct_threads: set[str] = set()
    distinct_providers: set[str] = set()

    for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
        event_type = str(record.get("event_type", "") or "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue

        event_task_id = str(payload.get("task_id", "") or "").strip()
        event_root = _normalize_task_root(event_task_id)
        if event_root != normalized_root:
            continue

        if event_type in {
            "task_started",
            "task_execution",
            "conversation_messages_built",
        }:
            thread_id = str(payload.get("thread_id", "") or "").strip()
            provider = str(payload.get("thread_provider", "") or "").strip()
            channel = str(payload.get("thread_channel", "") or "").strip()
            model_family = str(payload.get("thread_model_family", "") or "").strip()
            generation = _safe_int_value(payload.get("thread_generation", 0), 0)
            if thread_id:
                distinct_threads.add(thread_id)
            if provider:
                distinct_providers.add(provider)
            task_threads[event_task_id] = {
                "thread_id": thread_id,
                "thread_provider": provider,
                "thread_channel": channel,
                "thread_model_family": model_family,
                "thread_generation": generation,
            }
            latest_thread_summary = {
                "thread_id": thread_id,
                "provider": provider,
                "channel": channel,
                "model_family": model_family,
                "generation": generation,
            }
        elif event_type == "conversation_thread_rebound":
            rebound_count += 1
        elif event_type == "conversation_thread_candidate_selected":
            candidate_count += 1

    latest_thread_summary = dict(latest_thread_summary or {})
    latest_thread_summary["rebound_count"] = rebound_count
    latest_thread_summary["candidate_count"] = candidate_count
    latest_thread_summary["distinct_thread_count"] = len(distinct_threads)
    latest_thread_summary["providers"] = sorted(distinct_providers)
    return task_threads, latest_thread_summary


def _build_task_operational_summary(
    tasks_payload: object,
    *,
    task_root: str,
    phase_verdicts: object = None,
    run_verdict: object = None,
) -> dict[str, object]:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return {
            "has_actionable_items": False,
            "active_total": 0,
            "counts": {},
            "blocked_reasons": [],
            "sample_items": [],
            "carryover_roots": [],
            "has_authoritative_blockers": False,
            "authoritative_blockers": [],
            "run_verdict_reconstructed": False,
            "health_signals": [],
            "lead_close_policy": {},
            "authoritative_close_state": "",
            "close_blocking_signals": [],
        }

    coerced_run_verdict = _coerce_run_verdict(run_verdict)
    coerced_phase_verdicts = _coerce_phase_verdicts(phase_verdicts)
    counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    carryover_roots: list[str] = []
    sample_items: list[dict[str, object]] = []
    phase_states_for_policy: dict[str, str] = {}

    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            classification = _classify_operational_bucket(item, task_root=normalized_root)
            if classification is None:
                continue
            operational_state, reason_code, source_root = classification
            task_id = str(item.get("task_id", "") or "").strip()
            if task_id.upper().startswith(f"{normalized_root}::"):
                phase_name = task_id.split("::", 1)[1].strip().lower()
                if phase_name:
                    phase_states_for_policy[phase_name] = str(
                        item.get("state", "") or ""
                    ).strip().lower()
            counts[operational_state] = int(counts.get(operational_state, 0)) + 1
            if operational_state.startswith("blocked_"):
                reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1
            if operational_state == "carried_over_from_previous_run" and source_root:
                if source_root not in carryover_roots:
                    carryover_roots.append(source_root)

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
                    "source": "task",
                }
            )

    authoritative_blockers: list[dict[str, object]] = []
    seen_authoritative_codes: set[str] = set()

    def _append_authoritative_blocker(
        operational_state: str,
        reason_code: str,
        *,
        title: str,
        detail: str = "",
        source: str,
    ) -> None:
        blocker_key = f"{operational_state}:{reason_code}"
        if blocker_key in seen_authoritative_codes:
            return
        seen_authoritative_codes.add(blocker_key)
        counts[operational_state] = int(counts.get(operational_state, 0)) + 1
        reason_counts[reason_code] = int(reason_counts.get(reason_code, 0)) + 1
        sample_items.append(
            {
                "task_id": normalized_root,
                "short_id": source,
                "title": title,
                "role": "team_lead",
                "state": str(coerced_run_verdict.get("state", "") or "").strip().lower(),
                "operational_state": operational_state,
                "reason_code": reason_code,
                "reason_label": _operational_reason_label(reason_code),
                "source_root": normalized_root,
                "source": source,
                "detail": detail,
            }
        )
        authoritative_blockers.append(
            {
                "operational_state": operational_state,
                "reason_code": reason_code,
                "reason_label": _operational_reason_label(reason_code),
                "detail": detail,
                "source": source,
            }
        )

    for phase_id, verdict in sorted(coerced_phase_verdicts.items()):
        verdict_status = str(verdict.get("status", "") or "").strip().lower()
        verdict_contract_status = str(verdict.get("contract_status", "") or "").strip().lower()
        verdict_reason_codes = [
            str(item).strip().lower()
            for item in list(verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ]
        if phase_id == "review" and (
            verdict_status == "rejected" or "review_rejected" in verdict_reason_codes
        ):
            _append_authoritative_blocker(
                "review_rejected",
                "review_rejected",
                title="Review rechazada",
                detail=phase_id,
                source="phase_verdict",
            )
        elif phase_id == "qa" and (
            verdict_status == "blocked" or "qa_blocked" in verdict_reason_codes
        ):
            _append_authoritative_blocker(
                "qa_blocked",
                "qa_blocked",
                title="QA bloqueada",
                detail=phase_id,
                source="phase_verdict",
            )
        elif phase_id == "build" and (
            verdict_contract_status == "drift" or "slice_drift" in verdict_reason_codes
        ):
            _append_authoritative_blocker(
                "slice_drift",
                "slice_drift",
                title="Deriva de slice",
                detail=str(verdict.get("slice_id", "") or phase_id),
                source="phase_verdict",
            )

    if coerced_run_verdict:
        semantic_failures = [
            str(item).strip()
            for item in list(coerced_run_verdict.get("semantic_gate_failures", []) or [])
            if str(item).strip()
        ]
        evidence_failures = [
            str(item).strip()
            for item in list(coerced_run_verdict.get("evidence_gate_failures", []) or [])
            if str(item).strip()
        ]
        verdict_state = str(coerced_run_verdict.get("state", "") or "").strip().lower()
        policy_signals = [
            str(item).strip()
            for item in list(coerced_run_verdict.get("policy_signals", []) or [])
            if str(item).strip()
        ]

        if bool(coerced_run_verdict.get("policy_review_required", False)):
            _append_authoritative_blocker(
                "blocked_by_policy",
                "policy_review_required",
                title="Policy review requerida",
                detail="El cierre requiere revision operativa antes de continuar.",
                source="run_verdict",
            )

        for failure in semantic_failures:
            failure_lower = failure.lower()
            if failure_lower.startswith("review:rejected"):
                _append_authoritative_blocker(
                    "review_rejected",
                    "review_rejected",
                    title="Review rechazada",
                    detail=failure,
                    source="run_verdict",
                )
            elif failure_lower.startswith("qa:blocked"):
                _append_authoritative_blocker(
                    "qa_blocked",
                    "qa_blocked",
                    title="QA bloqueada",
                    detail=failure,
                    source="run_verdict",
                )
            elif "slice_drift" in failure_lower:
                _append_authoritative_blocker(
                    "slice_drift",
                    "slice_drift",
                    title="Deriva de slice",
                    detail=failure,
                    source="run_verdict",
                )
            else:
                _append_authoritative_blocker(
                    "blocked_by_policy",
                    "semantic_gate_failed",
                    title="Semantic gate bloqueante",
                    detail=failure,
                    source="run_verdict",
                )

        for failure in evidence_failures:
            _append_authoritative_blocker(
                "blocked_by_policy",
                "evidence_gate_failed",
                title="Evidence gate bloqueante",
                detail=failure,
                source="run_verdict",
            )

        if not authoritative_blockers and verdict_state in {"rejected", "failed"}:
            _append_authoritative_blocker(
                "blocked_by_policy",
                "run_rejected" if verdict_state == "rejected" else "run_failed",
                title="Run cerrada con bloqueo autoritativo",
                detail=verdict_state,
                source="run_verdict",
            )

        if (
            not authoritative_blockers
            and any(signal == "semantic_gate_failed" for signal in policy_signals)
        ):
            _append_authoritative_blocker(
                "blocked_by_policy",
                "semantic_gate_failed",
                title="Semantic gate bloqueante",
                detail="semantic_gate_failed",
                source="run_verdict",
            )

    operational_order = {
        "review_rejected": 0,
        "qa_blocked": 1,
        "slice_drift": 2,
        "blocked_by_policy": 3,
        "blocked_by_no_eligible_adapter": 0,
        "blocked_by_quorum": 4,
        "blocked_by_dependency": 5,
        "blocked_by_file_lock": 6,
        "blocked_waiting_quality_gates": 7,
        "blocked_other": 8,
        "waiting_user": 9,
        "pending": 10,
        "carried_over_from_previous_run": 11,
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
    lead_close_policy = derive_lead_close_policy(
        phase_verdicts=coerced_phase_verdicts,
        phase_states=phase_states_for_policy,
        run_verdict=coerced_run_verdict,
    )
    return {
        "has_actionable_items": active_total > 0,
        "active_total": active_total,
        "counts": counts,
        "blocked_reasons": blocked_reasons,
        "sample_items": sample_items[:8],
        "carryover_roots": carryover_roots[:6],
        "has_authoritative_blockers": bool(authoritative_blockers),
        "authoritative_blockers": authoritative_blockers[:8],
        "authoritative_state": str(coerced_run_verdict.get("state", "") or "").strip().lower(),
        "authoritative_reason_codes": [
            str(item).strip()
            for item in list(coerced_run_verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ][:12],
        "run_verdict_reconstructed": bool(
            coerced_run_verdict.get("reconstructed_from_phase_verdicts", False)
        ),
        "lead_close_policy": lead_close_policy,
        "authoritative_close_state": str(
            lead_close_policy.get("authoritative_close_state", "") or ""
        ).strip().lower(),
        "close_blocking_signals": [
            str(item).strip()
            for item in list(lead_close_policy.get("blocking_signals", []) or [])
            if str(item).strip()
        ][:12],
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


def _coerce_phase_contracts(payload: object) -> dict[str, dict[str, object]]:
    contracts: dict[str, dict[str, object]] = {}
    if not isinstance(payload, dict):
        return contracts
    for raw_phase_id, raw_entry in payload.items():
        phase_id = str(raw_phase_id or "").strip()
        if not phase_id or not isinstance(raw_entry, dict):
            continue
        entry: dict[str, object] = {"phase_id": phase_id}
        role = str(raw_entry.get("role", "") or "").strip()
        objective = str(raw_entry.get("objective", "") or "").strip()
        depends_on = [
            str(item).strip()
            for item in list(raw_entry.get("depends_on", []) or [])
            if str(item).strip()
        ]
        if role:
            entry["role"] = role
        if objective:
            entry["objective"] = objective
        if depends_on:
            entry["depends_on"] = depends_on
        contracts[phase_id] = entry
    return contracts


def _coerce_phase_verdicts(payload: object) -> dict[str, dict[str, object]]:
    return coerce_phase_verdicts(payload)


def _coerce_run_verdict(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}

    verdict: dict[str, object] = {}
    state = str(payload.get("state", "") or "").strip().lower()
    if state:
        verdict["state"] = state

    result = str(payload.get("result", "") or "").strip().lower()
    if result:
        verdict["result"] = result

    reason_codes = [
        str(item).strip()
        for item in list(payload.get("reason_codes", []) or [])
        if str(item).strip()
    ]
    if reason_codes:
        verdict["reason_codes"] = reason_codes[:24]

    policy_signals = [
        str(item).strip()
        for item in list(payload.get("policy_signals", []) or [])
        if str(item).strip()
    ]
    if policy_signals:
        verdict["policy_signals"] = policy_signals[:24]

    semantic_gate_failures = [
        str(item).strip()
        for item in list(payload.get("semantic_gate_failures", []) or [])
        if str(item).strip()
    ]
    evidence_gate_failures = [
        str(item).strip()
        for item in list(payload.get("evidence_gate_failures", []) or [])
        if str(item).strip()
    ]
    failed_phases = [
        str(item).strip()
        for item in list(payload.get("failed_phases", []) or [])
        if str(item).strip()
    ]
    pending_phases = [
        str(item).strip()
        for item in list(payload.get("pending_phases", []) or [])
        if str(item).strip()
    ]

    verdict["semantic_gate_applied"] = bool(payload.get("semantic_gate_applied", False))
    verdict["semantic_gate_failures"] = semantic_gate_failures[:12]
    verdict["evidence_gate_applied"] = bool(payload.get("evidence_gate_applied", False))
    verdict["evidence_gate_failures"] = evidence_gate_failures[:12]
    verdict["policy_review_required"] = bool(payload.get("policy_review_required", False))
    verdict["advisory_mode"] = bool(payload.get("advisory_mode", False))
    verdict["degraded_delivery"] = bool(payload.get("degraded_delivery", False))
    verdict["reconstructed_from_phase_verdicts"] = bool(
        payload.get("reconstructed_from_phase_verdicts", False)
    )

    if failed_phases:
        verdict["failed_phases"] = failed_phases[:12]
    if pending_phases:
        verdict["pending_phases"] = pending_phases[:12]

    next_action_hint = str(payload.get("next_action_hint", "") or "").strip()
    if next_action_hint:
        verdict["next_action_hint"] = next_action_hint

    updated_at = str(payload.get("updated_at", "") or "").strip()
    if updated_at:
        verdict["updated_at"] = updated_at

    if not verdict.get("state") and not verdict.get("result") and not reason_codes:
        return {}
    return verdict


def _coerce_lead_close_policy(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    authoritative_close_state = str(
        payload.get("authoritative_close_state", "") or ""
    ).strip().lower()
    blocking_signals = [
        str(item).strip()
        for item in list(payload.get("blocking_signals", []) or [])
        if str(item).strip()
    ]
    policy: dict[str, object] = {}
    if authoritative_close_state:
        policy["authoritative_close_state"] = authoritative_close_state
    if blocking_signals:
        policy["blocking_signals"] = blocking_signals[:12]
    policy["can_declare_done"] = bool(payload.get("can_declare_done", False))
    policy["requires_close_rewrite"] = bool(
        payload.get("requires_close_rewrite", False)
    )
    return policy


def _build_health_signals(
    *,
    run_verdict: dict[str, object],
    continuation_requested: bool,
    continuation_effective: bool,
    continuation_block_reason: str,
) -> list[str]:
    signals: list[str] = []
    if continuation_requested and not continuation_effective and continuation_block_reason:
        signals.append(f"continuation_blocked:{continuation_block_reason}")
    if bool(run_verdict.get("reconstructed_from_phase_verdicts", False)):
        signals.append("run_verdict_reconstructed")
    verdict_state = str(run_verdict.get("state", "") or "").strip().lower()
    if verdict_state in {"rejected", "failed"}:
        signals.append(f"run_state:{verdict_state}")
    if bool(run_verdict.get("policy_review_required", False)):
        signals.append("policy_review_required")
    if bool(run_verdict.get("semantic_gate_applied", False)):
        signals.append("semantic_gate_applied")
    return signals[:8]


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
    phase_contracts: dict[str, dict[str, object]] = {}
    phase_verdicts: dict[str, dict[str, object]] = {}
    run_verdict: dict[str, object] = {}
    continuation_requested = False
    continuation_effective = False
    continuation_block_reason = ""
    semantic_gate_applied = False
    semantic_gate_failures: list[str] = []
    workflow_run_status = ""

    workflow_state_payload = _read_runtime_workflow_state(runtime_dir)
    if isinstance(workflow_state_payload, dict):
        workflow_entry = workflow_state_payload.get(normalized_root, {})
        if isinstance(workflow_entry, dict):
            workflow_run_status = str(
                workflow_entry.get("run_status", "") or ""
            ).strip().lower()
            phase_contracts = _coerce_phase_contracts(
                workflow_entry.get("phase_contracts", {})
            )
            phase_verdicts = _coerce_phase_verdicts(
                workflow_entry.get("phase_verdicts", {})
            )
            phase_evidence_plan = _coerce_phase_evidence_plan(
                workflow_entry.get("phase_evidence_plan", {})
            )
            delegate_batches = _coerce_delegate_batches(
                workflow_entry.get("delegate_batches", [])
            )
            delegate_economics = dict(
                workflow_entry.get("delegate_economics_summary", {}) or {}
            )
            run_verdict = _coerce_run_verdict(workflow_entry.get("run_verdict", {}))
            if not run_verdict and phase_verdicts:
                run_verdict = _coerce_run_verdict(
                    derive_run_verdict_from_phase_verdicts(phase_verdicts)
                )
            continuation_requested = bool(
                workflow_entry.get("continuation_requested", False)
            )
            continuation_effective = bool(
                workflow_entry.get("continuation_effective", False)
            )
            continuation_block_reason = str(
                workflow_entry.get("continuation_block_reason", "") or ""
            ).strip()
            semantic_gate_applied = bool(run_verdict.get("semantic_gate_applied", False))
            semantic_gate_failures = [
                str(item).strip()
                for item in list(run_verdict.get("semantic_gate_failures", []) or [])
                if str(item).strip()
            ][:12]
            evidence_gate_rejected = bool(run_verdict.get("evidence_gate_applied", False)) or evidence_gate_rejected
    health_signals = _build_health_signals(
        run_verdict=run_verdict,
        continuation_requested=continuation_requested,
        continuation_effective=continuation_effective,
        continuation_block_reason=continuation_block_reason,
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
    task_thread_meta, thread_summary = _collect_chat_thread_metadata(
        runtime_dir, normalized_root
    )
    task_summaries = _summarize_chat_tasks(
        tasks_payload,
        task_root=normalized_root,
        task_threads=task_thread_meta,
    )
    task_operational_summary = _build_task_operational_summary(
        tasks_payload,
        task_root=normalized_root,
        phase_verdicts=phase_verdicts,
        run_verdict=run_verdict,
    )
    lead_close_policy = _coerce_lead_close_policy(
        (
            task_operational_summary.get("lead_close_policy", {})
            if isinstance(task_operational_summary, dict)
            else {}
        )
    )
    if isinstance(task_operational_summary, dict):
        task_operational_summary["health_signals"] = list(health_signals)
        if continuation_requested and not continuation_effective and continuation_block_reason:
            task_operational_summary["continuation_blocked"] = True
            task_operational_summary["continuation_block_reason"] = continuation_block_reason
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
        last_event_ts = _display_ts_local(record.get("ts", ""))

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
        workflow_run_status=workflow_run_status,
        continuation_requested=continuation_requested,
        continuation_effective=continuation_effective,
        continuation_block_reason=continuation_block_reason,
        run_verdict_reconstructed=bool(
            run_verdict.get("reconstructed_from_phase_verdicts", False)
        ),
        health_signals=health_signals,
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
        semantic_gate_applied=semantic_gate_applied,
        semantic_gate_failures=semantic_gate_failures,
        evidence_gate_rejected=evidence_gate_rejected,
        evidence_gate_failures=evidence_gate_failures,
        run_verdict=run_verdict,
        lead_close_policy=lead_close_policy,
        phase_verdicts=phase_verdicts,
        phase_contracts=phase_contracts,
        last_event=last_event,
        last_event_ts=last_event_ts,
        phase_evidence_plan=phase_evidence_plan,
        delegate_batches=delegate_batches,
        delegate_economics=delegate_economics,
        specialist_reports=specialist_reports,
        specialist_report_summary=specialist_report_summary,
        peer_consultation_summary=peer_consultation_summary,
        task_summaries=task_summaries,
        thread_summary=thread_summary,
        task_operational_summary=task_operational_summary,
    )

    if not exists:
        return TeamChatProgressResponse(
            exists=False,
            state="queued",
            is_sim_mode=sim_mode_enabled(),
            **response_kwargs,
        )

    verdict_state = str(run_verdict.get("state", "") or "").strip().lower()
    task_summary_states = {
        str(getattr(item, "state", "") or "").strip().lower()
        for item in task_summaries
        if str(getattr(item, "state", "") or "").strip()
    }
    has_runnable_tasks = bool(task_summary_states.intersection({"ready", "claimed"}))
    has_non_terminal_task_states = bool(
        task_summary_states.intersection({"pending", "ready", "claimed", "blocked", "waiting_user"})
    )
    if workflow_run_status == "waiting_user" or waiting_user:
        progress_state = "waiting_user"
    elif verdict_state in {"rejected", "failed", "completed"} and not waiting_user:
        progress_state = verdict_state
    elif workflow_run_status in {"rejected", "failed", "completed"} and not waiting_user:
        progress_state = workflow_run_status
    elif evidence_gate_rejected:
        progress_state = "rejected"
    elif failed_tasks > 0 or lead_state == "failed":
        progress_state = "failed"
    elif lead_state == "completed" and pending_tasks == 0:
        progress_state = "completed"
    elif exhausted:
        progress_state = "in_progress"
    elif pending_tasks > 0:
        progress_state = "running"
    elif completed_tasks > 0 and not has_non_terminal_task_states:
        progress_state = "completed"
    else:
        progress_state = "running"

    if (
        progress_state in {"running", "in_progress"}
        and not waiting_user
        and not has_runnable_tasks
        and pending_tasks > 0
    ):
        if verdict_state == "rejected" or semantic_gate_applied:
            progress_state = "rejected"
        elif failed_tasks > 0 or evidence_gate_rejected:
            progress_state = "failed"

    if (
        workflow_run_status in {"running", "in_progress"}
        and progress_state == "completed"
        and not waiting_user
    ):
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
        is_sim_mode=sim_mode_enabled(),
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
        "chat_continuation_blocked",
        "chat_continuation_source_reconstructed",
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
                ts=_display_ts_local(record.get("ts", "")),
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
                thread_provider=str(payload.get("thread_provider", "") or ""),
                thread_channel=str(payload.get("thread_channel", "") or ""),
                thread_model_family=str(payload.get("thread_model_family", "") or ""),
                thread_generation=_safe_int_value(payload.get("thread_generation", 0), 0),
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

    progress = _build_chat_progress(runtime_dir, selected_task_id)
    if bool(progress.run_verdict_reconstructed):
        synthetic_ts = (
            str((progress.run_verdict or {}).get("updated_at", "") or "")
            or str(progress.last_event_ts or "")
        )
        timeline_items.append(
            OperatorTimelineItem(
                ts=_display_ts_local(synthetic_ts),
                event_type="run_verdict_reconstructed",
                task_id=selected_task_id,
                level="warn",
                summary="run_verdict reconstructed from phase_verdicts",
            )
        )

    timeline_items.sort(key=lambda item: item.ts, reverse=True)
    effective_limit = max(20, min(limit, 300))
    limited_items = timeline_items[:effective_limit]

    return OperatorTimelineResponse(
        selected_task_id=selected_task_id,
        latest_task_id=latest_task_id,
        available_runs=available_runs,
        total=len(timeline_items),
        items=limited_items,
        progress=progress,
    )
