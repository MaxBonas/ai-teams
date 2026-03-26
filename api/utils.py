import os
import re
import json
from pathlib import Path

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
        return f"task_execution success={success} role={role} assignee={assignee} latency={latency}ms"
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

def _workspace_from_header_map(headers: dict[str, str], current_workspace: Path, project_root: Path) -> Path:
    raw = str(headers.get("x-workspace-path", "") or "").strip()
    if not raw:
        return current_workspace
    try:
        return _normalize_workspace_path(raw, project_root)
    except Exception:
        return current_workspace

def _workspace_from_request(request: Request, current_workspace: Path, project_root: Path) -> Path:
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
    if not roots:
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
    ]
    for row in ordered:
        root_id = str(row.get("root_id", ""))
        message = _truncate_text(row.get("user_message", ""), limit=220)
        lead_close = _truncate_text(row.get("lead_close_result", ""), limit=220)
        phase_states = row.get("phase_states", {})
        state_view = ""
        if isinstance(phase_states, dict):
            state_view = ", ".join(
                f"{k}:{v}"
                for k, v in phase_states.items()
            )
        lines.append(f"- {root_id} msg={message or '-'}")
        if state_view:
            lines.append(f"  states={state_view}")
        if lead_close:
            lines.append(f"  close={lead_close}")

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

def _detect_notebooklm_status(runtime_dir: Path, project_root: Path) -> dict[str, str | bool]:
    adapters_path = runtime_dir / "adapters.json"
    sync_status_path = runtime_dir / "notebooklm_sync_status.json"
    notebooklm_adapters: list[dict[str, object]] = []

    if adapters_path.exists():
        try:
            data = json.loads(adapters_path.read_text(encoding="utf-8"))
            candidates = data.get("external_adapters", []) if isinstance(data, dict) else []
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).lower()
                provider = str(item.get("provider", "")).lower()
                model = str(item.get("model", "")).lower()
                if "notebooklm" in name or "notebooklm" in provider or "notebooklm" in model:
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

    enabled_adapters = [item for item in notebooklm_adapters if bool(item.get("enabled", False))]
    if enabled_adapters:
        first = enabled_adapters[0]
        auto_configured = bool(os.getenv("NOTEBOOKLM_INGEST_ENDPOINT") or os.getenv("NOTEBOOKLM_INGEST_COMMAND"))
        return {
            "connected": auto_configured,
            "mode": "adapter",
            "details": (
                f"Adapter enabled: {first.get('name', 'notebooklm')}."
                + (" Auto-sync transport configured." if auto_configured else " Waiting for first sync transport config.")
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
