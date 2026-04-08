import os
import re
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from aiteam.phase_verdicts import derive_run_verdict_from_phase_verdicts
from aiteam.context_curator import ContextCuratorStore, project_key_from_runtime_dir
from aiteam.workflow_planner import PhaseSpec
from aiteam.phase_verdicts import is_missing_contract_objective

# Add PROJECT_ROOT here to avoid circular imports
# Resolved dynamically from the location of this file (api/utils.py → project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Global state for the IDE's current workspace directory
_CURRENT_WORKSPACE: Path = PROJECT_ROOT

_CONTINUATION_CLOSE_PENDING_MARKERS = (
    "close pending",
    "close pending phases",
    "cerrar pendientes",
    "cierra pendientes",
    "close remaining",
)


def get_current_workspace() -> Path:
    return _CURRENT_WORKSPACE


def set_current_workspace(path: Path) -> None:
    global _CURRENT_WORKSPACE
    _CURRENT_WORKSPACE = path


def _absorb_legacy_runtime(legacy: Path, dotdir: Path) -> None:
    dotdir.mkdir(parents=True, exist_ok=True)
    for candidate in sorted(
        list(legacy.rglob("*")),
        key=lambda path: (len(path.parts), str(path)),
    ):
        relative = candidate.relative_to(legacy)
        target = dotdir / relative
        if candidate.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            candidate.rename(target)
        except OSError:
            shutil.copy2(candidate, target)
            try:
                candidate.unlink()
            except OSError:
                pass
    for candidate in sorted(
        [path for path in legacy.rglob("*") if path.is_dir()],
        key=lambda path: (len(path.parts), str(path)),
        reverse=True,
    ):
        try:
            candidate.rmdir()
        except OSError:
            continue
    try:
        legacy.rmdir()
    except OSError:
        pass


def resolve_runtime_dir(workspace: Path, project_root: Path = PROJECT_ROOT) -> Path:
    workspace = Path(workspace).resolve()
    project_root = Path(project_root).resolve()
    if workspace == project_root:
        return workspace / "runtime"
    dotdir = workspace / ".aiteam"
    legacy = workspace / "runtime"
    if legacy.exists() and not dotdir.exists():
        last_error: OSError | None = None
        for delay in (0.0, 0.05, 0.1):
            if delay > 0:
                time.sleep(delay)
            try:
                legacy.rename(dotdir)
                return dotdir
            except PermissionError as exc:
                last_error = exc
                continue
            except OSError as exc:
                last_error = exc
                break
        if last_error is not None:
            dotdir.mkdir(parents=True, exist_ok=True)
            _absorb_legacy_runtime(legacy, dotdir)
        return dotdir
    if legacy.exists() and dotdir.exists():
        _absorb_legacy_runtime(legacy, dotdir)
    return dotdir


from pydantic import BaseModel
from fastapi import HTTPException, Request


def _truncate_text(value: object, limit: int = 360) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _read_json_payload(path: Path, fallback: object) -> object:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    return data


def _read_runtime_tasks_payload(runtime_dir: Path) -> list[dict]:
    db_path = runtime_dir / "aiteam.db"
    if db_path.exists():
        try:
            from aiteam.sqlite_store import SqliteStore

            payload = SqliteStore(db_path).load_all_tasks()
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
        except Exception:
            pass
    return []


def _read_runtime_workflow_state(runtime_dir: Path) -> dict[str, object]:
    db_path = runtime_dir / "aiteam.db"
    if db_path.exists():
        try:
            from aiteam.sqlite_store import SqliteStore

            payload = SqliteStore(db_path).load_workflow_state()
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {}


def _read_jsonl_records(path: Path, *, tail: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    try:
        from aiteam.persistence import AtomicFileWriter

        if tail is not None:
            records = AtomicFileWriter.read_jsonl_tail(path, tail)
        else:
            records = AtomicFileWriter.read_jsonl_with_dedup(path)
    except Exception:
        return []
    return [item for item in records if isinstance(item, dict)]


def _coerce_specialist_report_entry(
    payload: object,
    *,
    source_task_id: str,
    phase: str,
    source: str,
) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    specialist = str(payload.get("specialist", "") or "").strip().lower()
    summary = str(payload.get("summary", "") or "").strip()
    if not specialist and not summary:
        return None
    validation_status = str(payload.get("validation_status", "unknown") or "unknown").strip().lower() or "unknown"
    report = {
        "specialist": specialist,
        "summary": _truncate_text(summary, limit=220),
        "recommendation": _truncate_text(payload.get("recommendation", ""), limit=180),
        "provider": str(payload.get("provider", "") or "").strip(),
        "model": str(payload.get("model", "") or "").strip(),
        "validation_status": validation_status,
        "validation_errors": [
            str(item).strip()
            for item in list(payload.get("validation_errors", []) or [])
            if str(item).strip()
        ][:6],
        "report_version": str(payload.get("report_version", "") or "").strip(),
        "source_task_id": source_task_id,
        "phase": phase,
        "source": source,
    }
    return report


def _load_chat_specialist_insights(
    runtime_dir: Path,
    task_root: str,
    *,
    limit: int = 8,
) -> dict[str, object]:
    normalized_root = str(task_root or "").strip().upper()
    if not normalized_root:
        return {
            "specialist_reports": [],
            "specialist_report_summary": {
                "count": 0,
                "displayed_count": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "truncated": False,
                "by_specialist": {},
            },
        }

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    reports: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    by_specialist: dict[str, dict[str, int]] = {}
    valid_count = 0
    invalid_count = 0

    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "")
            if not task_id.upper().startswith(f"{normalized_root}::"):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            phase = task_id.split("::", 1)[1] if "::" in task_id else "root"
            for field_name, source_name in (
                ("specialist_prefetch_reports", "prefetch"),
                ("specialist_reports", "task"),
            ):
                raw_reports = list(metadata.get(field_name, []) or [])
                for raw in raw_reports:
                    report = _coerce_specialist_report_entry(
                        raw,
                        source_task_id=task_id,
                        phase=phase,
                        source=source_name,
                    )
                    if report is None:
                        continue
                    dedupe_key = (
                        str(report.get("specialist", "")),
                        str(report.get("summary", "")),
                        str(report.get("source_task_id", "")),
                        str(report.get("source", "")),
                    )
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    reports.append(report)
                    specialist = str(report.get("specialist", "") or "unknown").strip() or "unknown"
                    bucket = by_specialist.setdefault(
                        specialist,
                        {"count": 0, "valid": 0, "invalid": 0},
                    )
                    bucket["count"] += 1
                    if str(report.get("validation_status", "")) == "valid":
                        valid_count += 1
                        bucket["valid"] += 1
                    else:
                        invalid_count += 1
                        bucket["invalid"] += 1

    total_count = len(reports)
    reports = reports[: max(1, int(limit or 8))]
    return {
        "specialist_reports": reports,
        "specialist_report_summary": {
            "count": total_count,
            "displayed_count": len(reports),
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "truncated": total_count > len(reports),
            "by_specialist": by_specialist,
        },
    }


def _specialist_insight_fields(
    runtime_dir: Path,
    task_root: str,
    *,
    limit: int = 8,
) -> dict[str, object]:
    insights = _load_chat_specialist_insights(runtime_dir, task_root, limit=limit)
    return {
        "specialist_reports": list(insights.get("specialist_reports", []) or []),
        "specialist_report_summary": dict(
            insights.get("specialist_report_summary", {}) or {}
        ),
    }


def _peer_consultation_summary_fields(
    runtime_dir: Path,
    task_root: str,
) -> dict[str, object]:
    normalized_root = str(task_root or "").strip().upper()
    if not normalized_root:
        return {
            "peer_consultation_summary": {
                "consulted_roles": [],
                "consulted_providers": [],
                "unavailable_roles": [],
                "provider_count": 0,
                "diversity_observed": False,
            }
        }

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    consulted_roles: list[str] = []
    consulted_providers: list[str] = []
    unavailable_roles: list[str] = []
    diversity_observed = False

    def _extend_unique(target: list[str], values: object) -> None:
        for raw in list(values or []):
            item = str(raw or "").strip().lower()
            if item and item not in target:
                target.append(item)

    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "")
            if not task_id.upper().startswith(f"{normalized_root}::"):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            _extend_unique(consulted_roles, metadata.get("consulted_roles", []))
            _extend_unique(consulted_providers, metadata.get("consulted_providers", []))
            _extend_unique(
                unavailable_roles,
                metadata.get("unavailable_consultations", []),
            )
            diversity_observed = diversity_observed or bool(
                metadata.get("peer_diversity_observed", False)
            )

    return {
        "peer_consultation_summary": {
            "consulted_roles": consulted_roles,
            "consulted_providers": consulted_providers,
            "unavailable_roles": unavailable_roles,
            "provider_count": len(consulted_providers),
            "diversity_observed": diversity_observed or len(consulted_providers) >= 2,
        }
    }


def _load_chat_rewiring_insights(
    runtime_dir: Path,
    task_root: str,
) -> dict[str, object]:
    normalized_root = str(task_root or "").strip().upper()
    if not normalized_root:
        return {
            "tool_rewiring_summary": {
                "count": 0,
                "by_specialist": {},
                "replacements": {},
            }
        }

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    count = 0
    by_specialist: dict[str, int] = {}
    replacements: dict[str, int] = {}
    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "")
            if not task_id.upper().startswith(f"{normalized_root}::"):
                continue
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            if not bool(metadata.get("tool_rewiring_active", False)):
                continue
            count += 1
            specialist = str(metadata.get("tool_rewiring_preferred_specialist", "") or "").strip().lower() or "unknown"
            by_specialist[specialist] = int(by_specialist.get(specialist, 0)) + 1
            for name in list(metadata.get("tool_rewiring_replacement_for", []) or []):
                normalized = str(name or "").strip().lower()
                if not normalized:
                    continue
                replacements[normalized] = int(replacements.get(normalized, 0)) + 1
    return {
        "tool_rewiring_summary": {
            "count": count,
            "by_specialist": by_specialist,
            "replacements": replacements,
        }
    }


def _parse_iso_ts(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def _display_ts_local(value: object) -> str:
    ts = _parse_iso_ts(value)
    if ts is None:
        return str(value or "").strip()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone().isoformat()


def _layer_counts(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {
            "working_set": 0,
            "durable_facts": 0,
            "decisions": 0,
            "open_questions": 0,
            "invalidations": 0,
            "next_actions": 0,
        }
    return {
        "working_set": len(list(payload.get("working_set", []) or [])),
        "durable_facts": len(list(payload.get("durable_facts", []) or [])),
        "decisions": len(list(payload.get("decisions", []) or [])),
        "open_questions": len(list(payload.get("open_questions", []) or [])),
        "invalidations": len(list(payload.get("invalidations", []) or [])),
        "next_actions": len(list(payload.get("next_actions", []) or [])),
    }


def _freshness_status(updated_at: object) -> str:
    ts = _parse_iso_ts(updated_at)
    if ts is None:
        return "unknown"
    now = datetime.now().astimezone()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone()
    age_seconds = max(0, int((now - ts).total_seconds()))
    if age_seconds <= 3600:
        return "fresh"
    if age_seconds <= 21600:
        return "warm"
    return "stale"


def _load_chat_context_curator_insights(
    runtime_dir: Path,
    task_root: str,
) -> dict[str, object]:
    raw_root = str(task_root or "").strip()
    normalized_root = raw_root.upper()
    if not raw_root:
        return {
            "context_pressure": {},
            "context_curator_summary": {},
        }

    workflow_state = _read_runtime_workflow_state(runtime_dir)
    workflow_entry = {}
    if isinstance(workflow_state, dict):
        candidate = workflow_state.get(raw_root, {})
        if not isinstance(candidate, dict):
            candidate = workflow_state.get(normalized_root, {})
        if isinstance(candidate, dict):
            workflow_entry = candidate

    curator_store = ContextCuratorStore(runtime_dir)
    project_key = project_key_from_runtime_dir(runtime_dir)
    project_context = curator_store.load_project_context(project_key)
    chat_context = curator_store.load_chat_context(raw_root, project_key=project_key)

    project_updated_at = str(project_context.get("updated_at", "") or "")
    chat_updated_at = str(chat_context.get("updated_at", "") or "")
    context_pressure = dict(workflow_entry.get("context_pressure", {}) or {})
    phase_outputs = dict(workflow_entry.get("phase_outputs", {}) or {})
    phase_context_summaries = dict(workflow_entry.get("phase_context_summaries", {}) or {})
    project_summary = str(workflow_entry.get("project_context_summary", "") or "")
    chat_summary = str(workflow_entry.get("chat_context_summary", "") or "")
    raw_context_chars = sum(
        len(str(value or ""))
        for value in phase_outputs.values()
        if str(value or "").strip()
    )
    compact_context_chars = (
        len(project_summary)
        + len(chat_summary)
        + sum(
            len(str(value or ""))
            for value in phase_context_summaries.values()
            if str(value or "").strip()
        )
    )
    estimated_chars_saved = max(0, raw_context_chars - compact_context_chars)
    estimated_tokens_saved = max(0, estimated_chars_saved // 4)
    compression_ratio = round(
        (compact_context_chars / raw_context_chars),
        4,
    ) if raw_context_chars > 0 else 0.0

    return {
        "context_pressure": context_pressure,
        "context_curator_summary": {
            "project_updated_at": project_updated_at,
            "chat_updated_at": chat_updated_at,
            "freshness_status": _freshness_status(chat_updated_at or project_updated_at),
            "project_layer_counts": _layer_counts(project_context),
            "chat_layer_counts": _layer_counts(chat_context),
            "project_summary": project_summary,
            "chat_summary": chat_summary,
            "context_curator_recommended": bool(
                workflow_entry.get("context_curator_recommended", False)
            ),
            "invalidation_count": len(list(chat_context.get("invalidations", []) or [])),
            "open_question_count": len(list(chat_context.get("open_questions", []) or [])),
            "estimated_context_chars_saved": estimated_chars_saved,
            "estimated_context_tokens_saved": estimated_tokens_saved,
            "raw_context_chars": raw_context_chars,
            "compact_context_chars": compact_context_chars,
            "compression_ratio": compression_ratio,
        },
    }


def _event_summary(event_type: str, payload: dict) -> str:
    if event_type == "user_input":
        task_id = payload.get("task_id", "-")
        message = _truncate_text(payload.get("message", ""), limit=180)
        return f"user_input task_id={task_id} message={message}"
    if event_type == "chat_continuation_blocked":
        return (
            f"continuation_blocked root={payload.get('continuation_of', '-')} "
            f"reason={payload.get('reason', '-')} source={payload.get('source', '-')}"
        )
    if event_type == "chat_continuation_source_reconstructed":
        reason_codes = _truncate_text(payload.get("reason_codes", []), limit=120)
        return (
            f"continuation_source_reconstructed root={payload.get('continuation_of', '-')} "
            f"source={payload.get('source', '-')} reasons={reason_codes}"
        )
    if event_type == "routing_decision":
        provider = payload.get("provider", "-")
        model = payload.get("model", "-")
        channel = payload.get("channel", "-")
        success = payload.get("success", False)
        return f"routing success={success} provider={provider} model={model} channel={channel}"
    if event_type == "task_execution":
        role = payload.get("role", "-")
        assignee = payload.get("assignee", "-")
        success = payload.get("success", False)
        latency = payload.get("latency_ms", 0)
        round_id = payload.get("execution_round", 0)
        sub_id = payload.get("execution_sub_iteration", 0)
        gate_id = payload.get("gate_iteration", 0)
        return f"task_execution success={success} role={role} assignee={assignee} latency={latency}ms r={round_id} s={sub_id} g={gate_id}"
    if event_type == "task_started":
        return (
            f"task_started assignee={payload.get('assignee', '-')} "
            f"r={payload.get('execution_round', 0)} s={payload.get('execution_sub_iteration', 0)} "
            f"g={payload.get('gate_iteration', 0)}"
        )
    if event_type == "gate_iteration":
        return (
            f"gate_iteration iter={payload.get('iteration', 0)} failed={payload.get('failed_gates', [])} "
            f"r={payload.get('execution_round', 0)} s={payload.get('execution_sub_iteration', 0)}"
        )
    if event_type in {
        "agent_handoff",
        "conversation_mailbox_consumed",
        "conversation_mailbox_reply",
    }:
        return _truncate_text(json.dumps(payload, ensure_ascii=True), limit=220)
    if event_type in {
        "sync_meeting",
        "sync_meeting_skipped",
        "round_sub_iteration",
        "round_completed",
        "sub_iteration_barrier",
    }:
        return _truncate_text(json.dumps(payload, ensure_ascii=True), limit=220)
    if event_type == "execution_step":
        step_type = payload.get("step_type", "-")
        command = _truncate_text(payload.get("command", ""), limit=120)
        exit_code = payload.get("exit_code", "-")
        success = payload.get("success", False)
        return f"execution_step success={success} type={step_type} exit={exit_code} cmd={command}"
    if event_type in {"mail_dm", "mail_broadcast"}:
        sender = payload.get("sender", "-")
        recipient = payload.get("recipient", "broadcast")
        subject = _truncate_text(payload.get("subject", ""), limit=90)
        return f"mail sender={sender} recipient={recipient} subject={subject}"
    return _truncate_text(json.dumps(payload, ensure_ascii=True), limit=220)


def _auth_expected_key() -> str:
    return os.getenv("AITEAM_API_KEY", "").strip()


def _extract_auth_token(headers: dict[str, str]) -> str:
    auth = str(headers.get("authorization", "") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _is_authorized(headers: dict[str, str]) -> bool:
    expected = _auth_expected_key()
    if not expected:
        return True
    x_api_key = str(headers.get("x-api-key", "") or "").strip()
    bearer = _extract_auth_token(headers)
    return x_api_key == expected or bearer == expected


def _require_api_auth_request(request: Request) -> None:
    if _is_authorized({k.lower(): v for k, v in request.headers.items()}):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _normalize_workspace_path(raw_path: str, project_root: Path) -> Path:
    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = (project_root / candidate).resolve()
    return candidate.resolve()


def _workspace_from_header_map(
    headers: dict[str, str], current_workspace: Path, project_root: Path
) -> Path:
    raw = str(headers.get("x-workspace-path", "") or "").strip()
    if not raw:
        return current_workspace
    try:
        return _normalize_workspace_path(raw, project_root)
    except Exception:
        return current_workspace


def _workspace_from_request(
    request: Request, current_workspace: Path, project_root: Path
) -> Path:
    header_map = {k.lower(): v for k, v in request.headers.items()}
    return _workspace_from_header_map(header_map, current_workspace, project_root)


def _safe_workspace_target(workspace: Path, relative_path: str) -> Path | None:
    try:
        target = (workspace / relative_path).resolve()
        target.relative_to(workspace)
        return target
    except Exception:
        return None


def _extract_user_message_from_task_description(description: str) -> str:
    text = str(description or "")
    markers = (
        "Solicitud original:\n",
        "Solicitud original: ",
        "Solicitud del usuario: ",
        "Solicitud: ",
    )
    fragment = ""
    for marker in markers:
        if marker not in text:
            continue
        fragment = text.split(marker, 1)[1]
        break
    if not fragment:
        return ""

    boundaries = (
        "\nEntrega:",
        "\nEntrega en ",
        "\n[PREPLAN_SIGNALS]",
        "\nCONTINUATION TARGET PRIORITARIO:",
        "\n== LEAD MEMORY ==",
        "\nContinuidad de proyecto",
        "\n[PHASE_CONTRACT]",
        "\nGate iteration:",
    )
    for boundary in boundaries:
        if boundary in fragment:
            fragment = fragment.split(boundary, 1)[0]
    return fragment.strip()


def _group_chat_roots(tasks_payload: object) -> dict[str, dict[str, object]]:
    roots: dict[str, dict[str, object]] = {}
    if not isinstance(tasks_payload, list):
        return roots
    for item in tasks_payload:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id", "") or "")
        if not task_id.startswith("CHAT-"):
            continue
        root = task_id.split("::", 1)[0]
        row = roots.setdefault(
            root,
            {
                "root_id": root,
                "phase_states": {},
                "latest_ts": "",
                "lead_close_result": "",
                "user_message": "",
            },
        )
        phase = task_id.split("::", 1)[1] if "::" in task_id else "root"
        phase_states = row.get("phase_states", {})
        if isinstance(phase_states, dict):
            phase_states[phase] = str(item.get("state", ""))
            row["phase_states"] = phase_states

        metadata = item.get("metadata", {})
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if phase == "lead_close":
            row["lead_close_result"] = str(metadata_dict.get("result", "") or "")

        description = str(item.get("description", "") or "")
        extracted = _extract_user_message_from_task_description(description)
        if extracted and not str(row.get("user_message", "")).strip():
            row["user_message"] = extracted
    return roots


_SEMANTIC_CONTINUITY_MARKERS = (
    "slice_drift",
    "review_rejected",
    "review_failed",
    "qa_blocked",
    "qa_failed",
    "missing_upstream",
    "contract_violation",
    "semantic_gate_failed",
    "evidence_gate_failed",
    "rejected_decision",
    "blocked_status",
    "drift",
)

_INFRA_CONTINUITY_MARKERS = (
    "http 429",
    "http_error:429",
    "http 403",
    "http_error:403",
    "routing",
    "rate_limit",
    "quota",
    "no_eligible_adapter",
    "agotamiento de recursos",
    "resource exhaustion",
    "infraestructura transitoria",
    "systemic_block",
)


def _continuity_run_profile(
    *,
    root_id: str,
    workflow_entry: object,
    authoritative_result: str = "",
    lead_close_text: str = "",
) -> dict[str, object]:
    entry = workflow_entry if isinstance(workflow_entry, dict) else {}
    reconstructed_verdict = (
        derive_run_verdict_from_phase_verdicts(entry.get("phase_verdicts", {}))
        if entry
        else {}
    )
    reason_codes = [
        str(item).strip().lower()
        for item in list(reconstructed_verdict.get("reason_codes", []) or [])
        if str(item).strip()
    ]
    haystack = " ".join(
        [
            str(root_id or "").strip().lower(),
            str(authoritative_result or "").strip().lower(),
            str(lead_close_text or "").strip().lower(),
            " ".join(reason_codes),
        ]
    )
    semantic = any(marker in haystack for marker in _SEMANTIC_CONTINUITY_MARKERS)
    infra = any(marker in haystack for marker in _INFRA_CONTINUITY_MARKERS)
    return {
        "reconstructed_verdict": reconstructed_verdict,
        "reason_codes": reason_codes,
        "semantic": semantic,
        "infra_only": bool(infra and not semantic),
    }


def _build_project_continuity_context(runtime_dir: Path, max_chats: int = 4) -> str:
    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    events = _read_jsonl_records(runtime_dir / "events.jsonl")
    roots = _group_chat_roots(tasks_payload)
    workflow_state = _read_runtime_workflow_state(runtime_dir)
    curator_store = ContextCuratorStore(runtime_dir)
    project_context_summary = curator_store.build_summary(
        curator_store.load_project_context(project_key_from_runtime_dir(runtime_dir)),
        max_items_per_section=2,
    )
    if not roots and not project_context_summary:
        return ""

    task_started_ts: dict[str, str] = {}
    for event in events:
        if str(event.get("event_type", "")) != "task_started":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id", "") or "")
        if not task_id.startswith("CHAT-"):
            continue
        root = task_id.split("::", 1)[0]
        ts = str(event.get("ts", "") or "")
        current = task_started_ts.get(root, "")
        if ts > current:
            task_started_ts[root] = ts

    for root_id, item in roots.items():
        item["latest_ts"] = task_started_ts.get(root_id, "")

    ordered = sorted(
        roots.values(),
        key=lambda row: str(row.get("latest_ts", "")),
        reverse=True,
    )[: max(1, max_chats)]

    lines = [
        "Continuidad de proyecto (sesiones previas):",
        "Prioridad de contexto: usa primero las instrucciones actuales del proyecto, el estado actual del repo y la run mas reciente.",
        "Las runs antiguas bloqueadas solo por infraestructura/routing son historicas y no describen por si solas el estado actual del proyecto.",
    ]
    if project_context_summary:
        lines.append("Context curator:")
        for line in project_context_summary.splitlines():
            lines.append(f"  {line}")
    infra_history: list[str] = []
    detailed_rows: list[tuple[dict[str, object], dict[str, object]]] = []
    for index, row in enumerate(ordered):
        root_id = str(row.get("root_id", ""))
        workflow_entry = (
            workflow_state.get(root_id, {}) if isinstance(workflow_state, dict) else {}
        )
        profile = _continuity_run_profile(
            root_id=root_id,
            workflow_entry=workflow_entry,
            lead_close_text=str(row.get("lead_close_result", "") or ""),
        )
        if index > 0 and bool(profile.get("infra_only", False)):
            infra_history.append(root_id)
            continue
        detailed_rows.append((row, profile))

    if infra_history:
        lines.append(
            "Bloqueos historicos de infraestructura (colapsados, no tratarlos como estado actual): "
            + ", ".join(infra_history)
        )

    for row, profile in detailed_rows:
        root_id = str(row.get("root_id", ""))
        workflow_entry = (
            workflow_state.get(root_id, {}) if isinstance(workflow_state, dict) else {}
        )
        workflow_entry = workflow_entry if isinstance(workflow_entry, dict) else {}
        message = _truncate_text(
            workflow_entry.get("user_message", "") or row.get("user_message", ""),
            limit=220,
        )
        lead_close = _truncate_text(row.get("lead_close_result", ""), limit=220)
        phase_states = row.get("phase_states", {})
        reconstructed_verdict = dict(profile.get("reconstructed_verdict", {}) or {})
        state_view = ""
        if isinstance(phase_states, dict):
            state_view = ", ".join(f"{k}:{v}" for k, v in phase_states.items())
        lines.append(f"- {root_id} msg={message or '-'}")
        if reconstructed_verdict:
            lines.append(
                "  health="
                + str(reconstructed_verdict.get("state", "") or "unknown")
                + " via phase_verdicts"
            )
        if bool(profile.get("semantic", False)):
            lines.append("  relevance=semantica_del_run")
        elif bool(profile.get("infra_only", False)):
            lines.append("  relevance=infra_reciente_no_autoritativa")
        if state_view:
            lines.append(f"  states={state_view}")
        if lead_close:
            lines.append(f"  close={lead_close}")

    return "\n".join(lines)


def _effective_phase_state(task_state: str, verdict_status: str) -> str:
    normalized_task = str(task_state or "").strip().lower()
    normalized_verdict = str(verdict_status or "").strip().lower()
    if normalized_verdict in {"blocked", "rejected", "failed", "partial", "archived"}:
        return normalized_verdict
    if normalized_task in {"blocked", "rejected", "failed", "partial", "archived"}:
        return normalized_task
    if normalized_verdict in {"approved", "completed"}:
        return normalized_verdict
    if normalized_task:
        return normalized_task
    return normalized_verdict


def _message_requests_close_pending(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _CONTINUATION_CLOSE_PENDING_MARKERS)


def _fallback_phase_role(phase_id: str) -> str:
    normalized = str(phase_id or "").strip().lower()
    if normalized.startswith("review"):
        return "REVIEWER"
    if normalized.startswith("qa"):
        return "QA"
    if normalized.startswith(
        ("research", "discovery", "analysis", "investigate", "plan_research")
    ):
        return "RESEARCHER"
    return "ENGINEER"


def _collect_continuation_target_pending_details(
    runtime_dir: Path,
    continuation_of: str,
) -> list[dict[str, object]]:
    continuation_root = str(continuation_of or "").strip().upper()
    if not continuation_root:
        return []

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    roots = _group_chat_roots(tasks_payload)
    target_row = dict(roots.get(continuation_root, {}) or {})
    workflow_state = _read_runtime_workflow_state(runtime_dir)
    workflow_entry = (
        workflow_state.get(continuation_root, {})
        if isinstance(workflow_state, dict)
        else {}
    )
    workflow_entry = workflow_entry if isinstance(workflow_entry, dict) else {}
    if not target_row and not workflow_entry:
        return []

    phase_states = dict(target_row.get("phase_states", {}) or {})
    phase_verdicts = dict(workflow_entry.get("phase_verdicts", {}) or {})
    phase_contracts = dict(workflow_entry.get("phase_contracts", {}) or {})
    workflow_phase_keys = [
        str(item).strip()
        for item in list(workflow_entry.get("workflow_phase_keys", []) or [])
        if str(item).strip()
    ]

    ordered_phase_names: list[str] = []
    for phase_name in workflow_phase_keys:
        if phase_name not in ordered_phase_names:
            ordered_phase_names.append(phase_name)
    for source in (phase_states.keys(), phase_contracts.keys()):
        for phase_name in source:
            normalized = str(phase_name or "").strip()
            if (
                normalized
                and normalized not in ordered_phase_names
                and normalized not in {"lead_intake"}
                and not normalized.startswith("scout_")
                and not normalized.startswith("delegate_")
                and not normalized.startswith("lead_preflight_")
                and not normalized.startswith("lead_report_")
            ):
                ordered_phase_names.append(normalized)

    pending_details: list[dict[str, object]] = []
    for phase_name in ordered_phase_names:
        task_state = str(phase_states.get(phase_name, "") or "").strip().lower()
        verdict = (
            phase_verdicts.get(phase_name, {})
            if isinstance(phase_verdicts.get(phase_name, {}), dict)
            else {}
        )
        verdict_status = str(verdict.get("status", "") or "").strip().lower()
        effective_state = _effective_phase_state(task_state, verdict_status)
        if effective_state in {"completed", "approved"}:
            continue
        contract = (
            phase_contracts.get(phase_name, {})
            if isinstance(phase_contracts.get(phase_name, {}), dict)
            else {}
        )
        pending_details.append(
            {
                "phase_id": phase_name,
                "state": effective_state,
                "objective": str(contract.get("objective", "") or "").strip(),
                "depends_on": [
                    str(item).strip()
                    for item in list(contract.get("depends_on", []) or [])
                    if str(item).strip()
                ],
                "role": str(contract.get("role", "") or "").strip().upper()
                or _fallback_phase_role(phase_name),
            }
        )
    return pending_details


def _phase_specs_from_pending_details(
    pending_details: list[dict[str, object]],
) -> list[PhaseSpec]:
    pending_ids = {
        str(item.get("phase_id", "")).strip()
        for item in pending_details
        if str(item.get("phase_id", "")).strip()
        and str(item.get("phase_id", "")).strip() not in {"lead_close", "lead_intake"}
    }
    phases: list[PhaseSpec] = []
    for item in pending_details:
        phase_id = str(item.get("phase_id", "")).strip()
        if not phase_id or phase_id in {"lead_close", "lead_intake"}:
            continue
        role = str(item.get("role", "")).strip().upper() or _fallback_phase_role(phase_id)
        objective = str(item.get("objective", "") or "").strip()
        if is_missing_contract_objective(objective):
            objective = (
                f"Cerrar o replanificar la fase pendiente '{phase_id}' del continuation target "
                "con evidencia concreta y sin abrir un slice nuevo."
            )
        depends_on = [
            dep
            for dep in list(item.get("depends_on", []) or [])
            if str(dep).strip() in pending_ids and str(dep).strip() != phase_id
        ]
        phases.append(
            PhaseSpec(
                phase_id=phase_id,
                role=role,
                objective=objective,
                depends_on=depends_on,
            )
        )
    return phases


def _close_pending_plan_requires_repair(
    proposed_phases: list[PhaseSpec],
    pending_details: list[dict[str, object]],
) -> bool:
    if not pending_details:
        return False
    proposed_ids = {
        str(spec.phase_id or "").strip()
        for spec in proposed_phases
        if str(spec.phase_id or "").strip()
    }
    pending_ids = {
        str(item.get("phase_id", "")).strip()
        for item in pending_details
        if str(item.get("phase_id", "")).strip()
        and str(item.get("phase_id", "")).strip() not in {"lead_close", "lead_intake"}
    }
    if not pending_ids:
        return False
    return not pending_ids.issubset(proposed_ids)


def _build_continuation_target_context(
    runtime_dir: Path,
    continuation_of: str,
    *,
    current_message: str = "",
) -> str:
    continuation_root = str(continuation_of or "").strip().upper()
    if not continuation_root:
        return ""

    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    roots = _group_chat_roots(tasks_payload)
    target_row = dict(roots.get(continuation_root, {}) or {})
    workflow_state = _read_runtime_workflow_state(runtime_dir)
    workflow_entry = (
        workflow_state.get(continuation_root, {})
        if isinstance(workflow_state, dict)
        else {}
    )
    workflow_entry = workflow_entry if isinstance(workflow_entry, dict) else {}
    if not target_row and not workflow_entry:
        return ""

    pending_rows: list[str] = []
    for item in _collect_continuation_target_pending_details(runtime_dir, continuation_root):
        phase_name = str(item.get("phase_id", "")).strip()
        effective_state = str(item.get("state", "")).strip()
        objective = _truncate_text(item.get("objective", "") or "", limit=140)
        depends_on = [
            str(dep).strip()
            for dep in list(item.get("depends_on", []) or [])
            if str(dep).strip()
        ]
        phase_line = f"- {phase_name}"
        if effective_state:
            phase_line += f" [{effective_state}]"
        if objective:
            phase_line += f" objective={objective}"
        if depends_on:
            phase_line += f" deps={', '.join(depends_on)}"
        pending_rows.append(phase_line)

    requested_message = _truncate_text(current_message, limit=220)
    previous_message = _truncate_text(
        workflow_entry.get("user_message", "") or target_row.get("user_message", "") or "",
        limit=220,
    )
    run_verdict = (
        workflow_entry.get("run_verdict", {})
        if isinstance(workflow_entry.get("run_verdict", {}), dict)
        else {}
    )
    run_state = str(run_verdict.get("state", "") or "").strip().lower()

    lines = [
        "CONTINUATION TARGET PRIORITARIO:",
        f"- continuation_of={continuation_root}",
    ]
    if requested_message:
        lines.append(f"- pedido_actual={requested_message}")
    if previous_message:
        lines.append(f"- pedido_run_objetivo={previous_message}")
    if run_state:
        lines.append(f"- estado_autoritativo_previo={run_state}")
    lines.append(
        "- regla: prioriza primero el pedido actual del usuario y las fases pendientes de esta run objetivo."
    )
    lines.append(
        "- prohibido: sustituir este objetivo por un objetivo historico mas antiguo o abrir otro slice antes de cerrar lo pendiente."
    )
    if pending_rows:
        lines.append("- fases_pendientes_objetivo:")
        lines.extend(f"  {row}" for row in pending_rows[:8])
    else:
        lines.append("- fases_pendientes_objetivo: ninguna visible")
    return "\n".join(lines)


def _build_scout_project_state_context(workspace: Path) -> str:
    """Construye contexto crudo de estado del proyecto para el scout de estado.

    Recoge: git status, últimos 5 commits, archivos clave presentes.
    No llama a ningún LLM — solo shell/filesystem. El scout LLM resume esto.
    """
    import subprocess

    lines: list[str] = [
        "=== ESTADO DEL PROYECTO ===",
        "REGLA AUTORITATIVA: solo considera confirmados los archivos y rutas listados abajo.",
        "Si un archivo, clase o modulo no aparece en este snapshot del workspace, tratalo como NO CONFIRMADO y no lo presentes como hecho.",
    ]

    is_git_repo = False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if result.returncode == 0:
            repo_root = str(Path(result.stdout.strip()).resolve())
            workspace_root = str(workspace.resolve())
            is_git_repo = repo_root == workspace_root
        lines.append(f"git repository: {'yes' if is_git_repo else 'no'}")
    except Exception:
        lines.append("git repository: unknown")

    # Git status
    if is_git_repo:
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            git_status = result.stdout.strip()
            lines.append(f"git status:\n{git_status[:600] if git_status else '(limpio)'}")
        except Exception:
            lines.append("git status: no disponible")

        # Últimos 5 commits
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            git_log = result.stdout.strip()
            if git_log:
                lines.append(f"últimos commits:\n{git_log[:400]}")
        except Exception:
            pass
    else:
        lines.append("git status: no disponible (workspace sin repositorio git)")

    # Snapshot autoritativo de rutas reales para evitar invenciones del scout.
    # Incluye TODOS los directorios no excluidos (no solo un set hardcodeado),
    # mostrando subdirectorios y archivos hasta 2 niveles o hasta el cap.
    try:
        _excluded = {"venv", "node_modules", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".aiteam_snapshots"}
        _top_files = {"README.md", "PROJECT_PLAN.md", "pyproject.toml", "package.json", "requirements.txt", "AITEAM_TEST_LOG.md", "setup.py", "setup.cfg", "Makefile"}
        actual_paths: list[str] = []
        for child in sorted(workspace.iterdir(), key=lambda p: (p.is_dir(), p.name)):
            if child.name.startswith(".") and child.name != ".aiteam":
                continue
            if child.name in _excluded:
                continue
            if child.is_file():
                if child.name in _top_files:
                    actual_paths.append(child.name)
            elif child.is_dir():
                actual_paths.append(f"{child.name}/")
                for nested in sorted(child.rglob("*"), key=lambda p: str(p.relative_to(workspace)).lower()):
                    if any(part in _excluded for part in nested.parts):
                        continue
                    rel = str(nested.relative_to(workspace)).replace("\\", "/")
                    if nested.is_dir():
                        actual_paths.append(f"{rel}/")
                    else:
                        actual_paths.append(rel)
                    if len(actual_paths) >= 120:
                        break
                if len(actual_paths) >= 120:
                    actual_paths.append("... (truncado)")
                    break
        if actual_paths:
            lines.append("workspace snapshot autoritativo:")
            lines.extend(f"- {item}" for item in actual_paths)
    except Exception:
        pass

    return "\n".join(lines)


def _build_scout_session_history_context(
    runtime_dir: Path,
    max_chats: int = 3,
    *,
    continuation_of: str = "",
    current_message: str = "",
) -> str:
    """Construye contexto crudo del historial de sesiones para el scout de historial.

    Extrae las últimas N sesiones con mensaje del usuario y síntesis del lead_close.
    No llama a ningún LLM. El scout LLM resume lo relevante.
    """
    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    events = _read_jsonl_records(runtime_dir / "events.jsonl")
    roots = _group_chat_roots(tasks_payload)
    if not roots:
        return "Sin sesiones previas."

    task_started_ts: dict[str, str] = {}
    for event in events:
        if str(event.get("event_type", "")) != "task_started":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id", "") or "")
        if not task_id.startswith("CHAT-"):
            continue
        root = task_id.split("::", 1)[0]
        ts = str(event.get("ts", "") or "")
        if ts > task_started_ts.get(root, ""):
            task_started_ts[root] = ts

    for root_id, item in roots.items():
        item["latest_ts"] = task_started_ts.get(root_id, "")

    ordered = sorted(
        roots.values(),
        key=lambda row: str(row.get("latest_ts", "")),
        reverse=True,
    )[:max_chats]
    workflow_state = _read_runtime_workflow_state(runtime_dir)

    # Load authoritative outcomes from lead_memory.md (resultado field is ground truth)
    lead_memory_outcomes: dict[str, str] = {}
    lead_memory_path = runtime_dir / "lead_memory.md"
    if lead_memory_path.exists():
        try:
            memory_text = lead_memory_path.read_text(encoding="utf-8", errors="replace")
            for line in memory_text.splitlines():
                line = line.strip()
                if not line.startswith("- Run "):
                    continue
                # Parse: "- Run ... | chat=CHAT-XXXXXXXX | ... | resultado=X | ..."
                chat_match = re.search(r"chat=(CHAT-[A-Z0-9]+)", line)
                result_match = re.search(r"resultado=(\w+)", line)
                if chat_match and result_match:
                    lead_memory_outcomes[chat_match.group(1)] = result_match.group(1)
        except Exception:
            pass

    lines = [
        "=== HISTORIAL DE SESIONES ===",
        "Prioriza la run mas reciente y cualquier run con evidencia semantica del mismo objetivo.",
        "Colapsa como historicos los bloqueos viejos debidos solo a infraestructura/routing.",
    ]
    continuation_target_context = _build_continuation_target_context(
        runtime_dir,
        continuation_of,
        current_message=current_message,
    )
    if continuation_target_context:
        lines.append(continuation_target_context)
    infra_history: list[str] = []
    detailed_rows: list[tuple[dict[str, object], dict[str, object], str]] = []
    for index, row in enumerate(ordered):
        root_id = str(row.get("root_id", ""))
        workflow_entry = (
            workflow_state.get(root_id, {}) if isinstance(workflow_state, dict) else {}
        )
        authoritative = lead_memory_outcomes.get(root_id, "")
        profile = _continuity_run_profile(
            root_id=root_id,
            workflow_entry=workflow_entry,
            authoritative_result=authoritative,
            lead_close_text=str(row.get("lead_close_result", "") or ""),
        )
        if index > 0 and bool(profile.get("infra_only", False)):
            infra_history.append(root_id)
            continue
        detailed_rows.append((row, profile, authoritative))

    if infra_history:
        lines.append(
            "bloqueos_historicos_infraestructura: "
            + ", ".join(infra_history)
        )

    for row, profile, authoritative in detailed_rows:
        root_id = str(row.get("root_id", ""))
        message = _truncate_text(row.get("user_message", ""), limit=200)
        lead_close = _truncate_text(row.get("lead_close_result", ""), limit=300)
        phase_states = row.get("phase_states", {})
        failed = [k for k, v in (phase_states.items() if isinstance(phase_states, dict) else []) if v == "failed"]
        reconstructed_verdict = dict(profile.get("reconstructed_verdict", {}) or {})
        lines.append(f"\n[{root_id}]")
        # Authoritative outcome from lead_memory (overrides DB phase states)
        if authoritative:
            lines.append(f"  resultado_oficial: {authoritative}")
        elif reconstructed_verdict:
            lines.append(
                "  resultado_reconstruido: "
                + str(reconstructed_verdict.get("result", "") or "desconocido")
            )
        if bool(profile.get("semantic", False)):
            lines.append("  relevancia: semantica")
        elif bool(profile.get("infra_only", False)):
            lines.append("  relevancia: infra_reciente_no_autoritativa")
        if message:
            lines.append(f"  pedido: {message}")
        if lead_close:
            lines.append(f"  resultado: {lead_close}")
        if reconstructed_verdict:
            reasons = [
                str(item).strip()
                for item in list(reconstructed_verdict.get("reason_codes", []) or [])
                if str(item).strip()
            ]
            if reasons:
                lines.append(f"  señales_reconstruidas: {', '.join(reasons)}")
        if failed:
            lines.append(f"  fases fallidas: {', '.join(failed)}")

    return "\n".join(lines)


def _chat_round_budget(complexity, criticality) -> int:
    from aiteam.types import Complexity, Criticality

    override = os.getenv("AITEAM_CHAT_MAX_ROUNDS", "").strip()
    if override.isdigit():
        return max(12, int(override))

    default_budget = {
        (Complexity.LOW, Criticality.LOW): 18,
        (Complexity.MEDIUM, Criticality.MEDIUM): 28,
        (Complexity.HIGH, Criticality.HIGH): 44,
    }
    budget = default_budget.get((complexity, criticality), 32)
    if complexity == Complexity.HIGH or criticality == Criticality.HIGH:
        budget = max(budget, 36)
    return budget


def _sanitize_project_name(raw_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", raw_name.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    if not cleaned:
        return "New Project"
    return cleaned


def _allocate_project_path(projects_root: Path, preferred_name: str) -> Path:
    candidate = projects_root / preferred_name
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        trial = projects_root / f"{preferred_name}-{suffix}"
        if not trial.exists():
            return trial
        suffix += 1


def _detect_notebooklm_status(
    runtime_dir: Path, project_root: Path
) -> dict[str, str | bool]:
    adapters_path = runtime_dir / "adapters.json"
    sync_status_path = runtime_dir / "notebooklm_sync_status.json"
    notebooklm_adapters: list[dict[str, object]] = []

    if adapters_path.exists():
        try:
            data = json.loads(adapters_path.read_text(encoding="utf-8"))
            candidates = (
                data.get("external_adapters", []) if isinstance(data, dict) else []
            )
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).lower()
                provider = str(item.get("provider", "")).lower()
                model = str(item.get("model", "")).lower()
                if (
                    "notebooklm" in name
                    or "notebooklm" in provider
                    or "notebooklm" in model
                ):
                    notebooklm_adapters.append(item)
        except Exception:
            pass

    if sync_status_path.exists():
        try:
            sync_status = json.loads(sync_status_path.read_text(encoding="utf-8"))
            if isinstance(sync_status, dict):
                mode = str(sync_status.get("mode", "unknown"))
                success = bool(sync_status.get("success", False))
                details = str(sync_status.get("details", "")).strip()
                ts = str(sync_status.get("ts", "")).strip()
                return {
                    "connected": success and mode in {"endpoint", "command"},
                    "mode": mode,
                    "details": f"{details} (last_sync={ts or '-'})",
                }
        except Exception:
            pass

    enabled_adapters = [
        item for item in notebooklm_adapters if bool(item.get("enabled", False))
    ]
    if enabled_adapters:
        first = enabled_adapters[0]
        auto_configured = bool(
            os.getenv("NOTEBOOKLM_INGEST_ENDPOINT")
            or os.getenv("NOTEBOOKLM_INGEST_COMMAND")
        )
        return {
            "connected": auto_configured,
            "mode": "adapter",
            "details": (
                f"Adapter enabled: {first.get('name', 'notebooklm')}."
                + (
                    " Auto-sync transport configured."
                    if auto_configured
                    else " Waiting for first sync transport config."
                )
            ),
        }

    if notebooklm_adapters:
        first = notebooklm_adapters[0]
        return {
            "connected": False,
            "mode": "configured_disabled",
            "details": f"Adapter configured but disabled: {first.get('name', 'notebooklm')}.",
        }

    if os.getenv("NOTEBOOKLM_API_KEY") or os.getenv("GOOGLE_NOTEBOOKLM_API_KEY"):
        return {
            "connected": False,
            "mode": "credentials_only",
            "details": "NotebookLM credential detected, but no active adapter is configured.",
        }

    manual_ingest_script = project_root / "scripts" / "ingest_learnings.py"
    if manual_ingest_script.exists():
        return {
            "connected": False,
            "mode": "manual_export",
            "details": "Manual export workflow available via scripts/ingest_learnings.py.",
        }

    return {
        "connected": False,
        "mode": "not_configured",
        "details": "NotebookLM integration is not configured.",
    }
