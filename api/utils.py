import os
import re
import json
from datetime import datetime, timezone
from pathlib import Path

from aiteam.context_curator import ContextCuratorStore

# Add PROJECT_ROOT here to avoid circular imports
# Resolved dynamically from the location of this file (api/utils.py → project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Global state for the IDE's current workspace directory
_CURRENT_WORKSPACE: Path = PROJECT_ROOT


def get_current_workspace() -> Path:
    return _CURRENT_WORKSPACE


def set_current_workspace(path: Path) -> None:
    global _CURRENT_WORKSPACE
    _CURRENT_WORKSPACE = path


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


def _read_jsonl_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        from aiteam.persistence import AtomicFileWriter

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
                "valid_count": 0,
                "invalid_count": 0,
                "by_specialist": {},
            },
        }

    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
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

    reports = reports[: max(1, int(limit or 8))]
    return {
        "specialist_reports": reports,
        "specialist_report_summary": {
            "count": len(reports),
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "by_specialist": by_specialist,
        },
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

    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
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
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
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

    workflow_state = _read_json_payload(runtime_dir / "workflow_state.json", fallback={})
    workflow_entry = {}
    if isinstance(workflow_state, dict):
        candidate = workflow_state.get(raw_root, {})
        if not isinstance(candidate, dict):
            candidate = workflow_state.get(normalized_root, {})
        if isinstance(candidate, dict):
            workflow_entry = candidate

    curator_store = ContextCuratorStore(runtime_dir)
    project_key = str(runtime_dir.parent.resolve())
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
    marker = "Solicitud original:\n"
    if marker not in description:
        marker = "Solicitud: "
        if marker not in description:
            return ""
        fragment = description.split(marker, 1)[1]
        return fragment.split("\n", 1)[0].strip()

    fragment = description.split(marker, 1)[1]
    if "\nEntrega:" in fragment:
        fragment = fragment.split("\nEntrega:", 1)[0]
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


def _build_project_continuity_context(runtime_dir: Path, max_chats: int = 4) -> str:
    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
    events = _read_jsonl_records(runtime_dir / "events.jsonl")
    roots = _group_chat_roots(tasks_payload)
    curator_store = ContextCuratorStore(runtime_dir)
    project_context_summary = curator_store.build_summary(
        curator_store.load_project_context(str(runtime_dir.parent.resolve())),
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

    lines = ["Continuidad de proyecto (sesiones previas):"]
    if project_context_summary:
        lines.append("Context curator:")
        for line in project_context_summary.splitlines():
            lines.append(f"  {line}")
    for row in ordered:
        root_id = str(row.get("root_id", ""))
        message = _truncate_text(row.get("user_message", ""), limit=220)
        lead_close = _truncate_text(row.get("lead_close_result", ""), limit=220)
        phase_states = row.get("phase_states", {})
        state_view = ""
        if isinstance(phase_states, dict):
            state_view = ", ".join(f"{k}:{v}" for k, v in phase_states.items())
        lines.append(f"- {root_id} msg={message or '-'}")
        if state_view:
            lines.append(f"  states={state_view}")
        if lead_close:
            lines.append(f"  close={lead_close}")

    return "\n".join(lines)


def _build_scout_project_state_context(workspace: Path) -> str:
    """Construye contexto crudo de estado del proyecto para el scout de estado.

    Recoge: git status, últimos 5 commits, archivos clave presentes.
    No llama a ningún LLM — solo shell/filesystem. El scout LLM resume esto.
    """
    import subprocess

    lines: list[str] = ["=== ESTADO DEL PROYECTO ==="]

    # Git status
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=8,
        )
        git_status = result.stdout.strip()
        lines.append(f"git status:\n{git_status[:600] if git_status else '(limpio)'}")
    except Exception:
        lines.append("git status: no disponible")

    # Últimos 3 commits
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=8,
        )
        git_log = result.stdout.strip()
        if git_log:
            lines.append(f"últimos commits:\n{git_log[:400]}")
    except Exception:
        pass

    # Archivos de primer nivel relevantes
    try:
        entries = sorted(workspace.iterdir(), key=lambda p: p.name)
        visible = [
            e.name + ("/" if e.is_dir() else "")
            for e in entries
            if not e.name.startswith(".")
            and e.name not in {"venv", "node_modules", "__pycache__", ".git"}
        ]
        lines.append(f"estructura: {', '.join(visible[:30])}")
    except Exception:
        pass

    return "\n".join(lines)


def _build_scout_session_history_context(runtime_dir: Path, max_chats: int = 3) -> str:
    """Construye contexto crudo del historial de sesiones para el scout de historial.

    Extrae las últimas N sesiones con mensaje del usuario y síntesis del lead_close.
    No llama a ningún LLM. El scout LLM resume lo relevante.
    """
    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
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

    lines = ["=== HISTORIAL DE SESIONES ==="]
    for row in ordered:
        root_id = str(row.get("root_id", ""))
        message = _truncate_text(row.get("user_message", ""), limit=200)
        lead_close = _truncate_text(row.get("lead_close_result", ""), limit=300)
        phase_states = row.get("phase_states", {})
        failed = [k for k, v in (phase_states.items() if isinstance(phase_states, dict) else []) if v == "failed"]
        lines.append(f"\n[{root_id}]")
        if message:
            lines.append(f"  pedido: {message}")
        if lead_close:
            lines.append(f"  resultado: {lead_close}")
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
