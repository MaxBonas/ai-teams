import os
import re
import uuid
from pathlib import Path

from aiteam.phase_verdicts import derive_run_verdict_from_phase_verdicts
from aiteam.types import Complexity, Criticality

from api.utils import (
    _chat_round_budget,
    _group_chat_roots,
    _read_jsonl_records,
    _read_runtime_tasks_payload,
    _read_runtime_workflow_state,
)


def _normalize_chat_mode(raw_mode: str) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"plan", "planning", "planning_only", "plan_only"}:
        return "plan"
    if normalized in {"direct", "basic", "single", "single_agent", "opencode"}:
        return "direct"
    if normalized in {"classic", "legacy", "pipeline", "phased"}:
        return "classic"
    return "sprint5"


def _resolve_chat_round_budget(
    requested_rounds: int | None,
    chat_mode: str,
    complexity: Complexity,
    criticality: Criticality,
) -> int:
    if isinstance(requested_rounds, int):
        return max(3, min(requested_rounds, 80))
    if chat_mode == "plan":
        return 4
    if chat_mode in {"sprint5", "direct"}:
        return 5
    return _chat_round_budget(complexity=complexity, criticality=criticality)


def _recent_chat_roots(
    runtime_dir: Path, max_chats: int = 4
) -> list[dict[str, object]]:
    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    roots = _group_chat_roots(tasks_payload)
    if not roots:
        return []
    workflow_state = _read_runtime_workflow_state(runtime_dir)
    if isinstance(workflow_state, dict):
        for root_id, item in roots.items():
            workflow_entry = workflow_state.get(root_id, {})
            if isinstance(workflow_entry, dict):
                item["run_status"] = str(workflow_entry.get("run_status", "") or "").strip().lower()
                item["continuation_requested"] = bool(workflow_entry.get("continuation_requested", False))
                item["continuation_effective"] = bool(workflow_entry.get("continuation_effective", False))
                item["continuation_of"] = str(workflow_entry.get("continuation_of", "") or "").strip()
                item["continuation_block_reason"] = str(
                    workflow_entry.get("continuation_block_reason", "") or ""
                ).strip().lower()
                run_verdict = workflow_entry.get("run_verdict", {})
                if isinstance(run_verdict, dict):
                    if run_verdict:
                        item["run_verdict"] = dict(run_verdict)
                    else:
                        reconstructed = derive_run_verdict_from_phase_verdicts(
                            workflow_entry.get("phase_verdicts", {})
                        )
                        if reconstructed:
                            item["run_verdict"] = reconstructed
                else:
                    reconstructed = derive_run_verdict_from_phase_verdicts(
                        workflow_entry.get("phase_verdicts", {})
                    )
                    if reconstructed:
                        item["run_verdict"] = reconstructed
                phase_verdicts = workflow_entry.get("phase_verdicts", {})
                if isinstance(phase_verdicts, dict) and phase_verdicts:
                    item["phase_verdicts"] = dict(phase_verdicts)

    events = _read_jsonl_records(runtime_dir / "events.jsonl")
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
    )
    return ordered[: max(1, max_chats)]


def _is_actionable_continuation_source(item: dict[str, object]) -> bool:
    root_id = str(item.get("root_id", "") or "").strip().upper()
    if not re.match(r"^CHAT-[0-9A-F]{8}$", root_id):
        return False

    run_status = str(item.get("run_status", "") or "").strip().lower()
    block_reason = str(item.get("continuation_block_reason", "") or "").strip().lower()
    continuation_requested = bool(item.get("continuation_requested", False))
    continuation_effective = bool(item.get("continuation_effective", False))
    if (
        continuation_requested
        and not continuation_effective
        and block_reason
        in {
            "ambiguous_target_required",
            "target_not_found_in_current_project",
        }
    ):
        return False
    if run_status == "waiting_user" and block_reason in {
        "ambiguous_target_required",
        "target_not_found_in_current_project",
    }:
        return False
    return True


def _default_implicit_continuation_source(
    previous_runs: list[dict[str, object]],
) -> dict[str, object]:
    for item in previous_runs:
        if isinstance(item, dict) and _is_actionable_continuation_source(item):
            return item
    return {}


def _is_continuation_message(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    normalized = normalized.strip(".!? ")
    if not normalized:
        return False

    direct = {
        "continue",
        "continue please",
        "continua",
        "continuad",
        "continua por favor",
        "continúe",
        "continúen",
        "proceed",
        "go on",
        "carry on",
        "sigue",
        "seguir",
    }
    if normalized in direct:
        return True

    return bool(
        re.match(
            r"^(continue|continua|continuad|continúe|continúen|proceed|go on|carry on|sigue|seguir)(\b|$)",
            normalized,
        )
    )


def _extract_chat_root_from_message(message: str) -> str:
    text = str(message or "")
    match = re.search(r"\bCHAT-([0-9A-Za-z]{8})\b", text)
    if not match:
        return ""
    candidate = match.group(1)
    # Exclude placeholder patterns where all characters are the same (e.g. XXXXXXXX)
    if len(set(candidate)) == 1:
        return ""
    return f"CHAT-{candidate.upper()}"


def _resolve_task_root(client_task_id: str) -> str:
    candidate = str(client_task_id or "").strip().upper()
    if re.match(r"^CHAT-[0-9A-F]{8}$", candidate):
        return candidate
    return f"CHAT-{uuid.uuid4().hex[:8].upper()}"


def _safe_int_value(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except Exception:
        return default


def _normalize_task_root(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "::" in text:
        text = text.split("::", 1)[0]
    candidate = text.upper()
    if re.match(r"^CHAT-[0-9A-F]{8}$", candidate):
        return candidate
    return ""


def _env_bool(key: str, default: bool = False) -> bool:
    raw = str(os.getenv(key, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_context_only_query(message: str) -> bool:
    """Detecta si el mensaje es una consulta de orientacion/contexto sin solicitud de desarrollo."""
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if not normalized or len(normalized) > 300:
        return False
    orientation_patterns = [
        r"\bde qu[eé]\s+(va|iba|trata|trataba)\b",
        r"\bqu[eé]\s+(es|era|hay|hemos|tenemos|tiene)\b",
        r"\b(resumen|resume|resumir|sintetiza|sintetizar)\b",
        r"\b(estado|status)\s+(del|de)\s+(proyecto|trabajo|tarea)\b",
        r"\b(qu[eé]|como)\s+(llevamos|vamos|estamos)\b",
        r"\b(recuerda[sm]?|recuerdo|recordar)\b",
        r"\b(contexto|context)\s+(del|de)\b",
        r"\bqu[eé]\s+(hicimos|hemos\s+hecho|habiamos\s+hecho)\b",
        r"\b(orientaci[oó]n|orientame|orient[aá]me)\b",
        r"\bponte\s+al\s+(d[ií]a|corriente)\b",
        r"\b(cuales?|qu[eé])\s+(son|eran)\s+(los|las)\s+(siguiente[s]?\s+paso[s]?|pendiente[s]?)\b",
    ]
    for pattern in orientation_patterns:
        if re.search(pattern, normalized):
            return True
    action_verbs = (
        "implementa",
        "añade",
        "agrega",
        "crea",
        "haz ",
        "modifica",
        "arregla",
        "reorganiza",
        "refactoriza",
        "migra",
        "actualiza",
        "genera",
        "construye",
        "diseña",
        "elimina",
        "borra",
        "configura",
        "despliega",
        "ejecuta",
        "fix",
        "add ",
        "create",
        "build",
        "run ",
        "deploy",
        "update",
        "remove",
        "delete",
    )
    if len(normalized) < 80 and any(
        kw in normalized
        for kw in ["proyecto", "project", "sabes", "sabe", "recuerdas", "recuerda"]
    ) and not any(normalized.startswith(verb) for verb in action_verbs):
        return True
    return False


def _is_review_like_phase_name(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized == "review"
        or normalized.startswith("review_")
        or "review_" in normalized
        or "revalidation" in normalized
        or normalized.startswith("audit")
        or "_audit" in normalized
    )


def _is_qa_like_phase_name(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized == "qa"
        or normalized.startswith("qa_")
        or normalized.endswith("_qa")
        or "_qa_" in normalized
        or "validation" in normalized
        or normalized.startswith("verify")
        or "_verify" in normalized
        or "acceptance" in normalized
    )


def _is_build_like_phase_name(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("lead_", "delegate_", "plan_", "scout_")):
        return False
    if _is_review_like_phase_name(normalized) or _is_qa_like_phase_name(normalized):
        return False
    if normalized in {"build", "implement", "develop", "code", "fix", "refactor"}:
        return True
    return (
        normalized.startswith("engineer")
        or normalized.startswith("eng_")
        or normalized.startswith("build_")
        or normalized.startswith("implement_")
        or normalized.startswith("fix_")
        or normalized.startswith("refactor_")
    )


def _is_context_audit_phase_name(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("lead_", "delegate_", "plan_")):
        return False
    if normalized in {
        "current_state",
        "existing_state",
        "repo_state",
        "workspace_state",
        "codebase_state",
        "scout_current_state",
        "research_current_state",
    }:
        return True

    has_current_marker = any(
        marker in normalized for marker in ("current", "existing", "baseline", "snapshot")
    )
    has_state_marker = any(
        marker in normalized
        for marker in (
            "state",
            "workspace",
            "repo",
            "codebase",
            "layout",
            "tree",
            "inventory",
            "structure",
        )
    )
    return has_current_marker and has_state_marker


def _is_advisory_context_phase_name(phase_name: str, role_hint: str = "") -> bool:
    normalized_role = str(role_hint or "").strip().lower()
    if normalized_role and normalized_role not in {"researcher", "scout"}:
        return False
    return _is_context_audit_phase_name(phase_name)


def _is_advisory_planning_phase_name(phase_name: str, role_hint: str = "") -> bool:
    normalized_phase = str(phase_name or "").strip().lower()
    normalized_role = str(role_hint or "").strip().lower()
    if not normalized_phase:
        return False
    if normalized_phase.startswith(("lead_", "delegate_")):
        return False
    if normalized_role and normalized_role != "researcher":
        return False
    if not normalized_phase.startswith("plan_"):
        return False
    return any(
        marker in normalized_phase
        for marker in ("research", "discovery", "analysis", "constraints", "context")
    )


def _detect_run_type(
    message: str,
    phase_task_ids: dict[str, str],
    artifact_created: int,
    artifact_modified: int,
) -> str:
    """Clasifica el tipo de run para aplicar el threshold de scoring correcto."""
    phase_names = set(phase_task_ids.keys()) - {"lead_intake", "lead_close"}
    has_build = any(_is_build_like_phase_name(key) for key in phase_names)
    has_artifacts = (artifact_created + artifact_modified) > 0
    has_review_validation = any(
        _is_review_like_phase_name(key) or _is_qa_like_phase_name(key)
        for key in phase_names
    )

    if (
        _is_context_only_query(message)
        and not has_build
        and not has_artifacts
        and not has_review_validation
    ):
        return "context_recovery"

    if has_build or has_artifacts:
        return "build"

    researcher_phases = {"discovery", "research", "plan_research", "analysis", "investigate"}
    review_phases = {"review", "plan_risks", "audit", "security"}
    qa_phases = {"qa", "test", "verify", "acceptance"}
    non_build = researcher_phases | review_phases | qa_phases
    if phase_names and phase_names.issubset(non_build):
        return "planning"

    support_phases = {
        "scout_current_state",
        "scout_project_state",
        "scout_session_history",
        "scout_context_curator",
        "current_state",
        "discovery",
        "research",
        "analysis",
        "investigate",
    }
    if phase_names and has_review_validation:
        if all(
            name in support_phases
            or _is_review_like_phase_name(name)
            or _is_qa_like_phase_name(name)
            for name in phase_names
        ):
            return "review_revalidation"

    return "mixed"
