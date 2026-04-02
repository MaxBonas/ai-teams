import os
import re
import uuid
from pathlib import Path

from aiteam.types import Complexity, Criticality

from api.utils import (
    _chat_round_budget,
    _group_chat_roots,
    _read_jsonl_records,
    _read_runtime_tasks_payload,
)


def _normalize_chat_mode(raw_mode: str) -> str:
    normalized = str(raw_mode or "").strip().lower()
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
    if chat_mode == "sprint5":
        return 5
    return _chat_round_budget(complexity=complexity, criticality=criticality)


def _recent_chat_roots(
    runtime_dir: Path, max_chats: int = 4
) -> list[dict[str, object]]:
    tasks_payload = _read_runtime_tasks_payload(runtime_dir)
    roots = _group_chat_roots(tasks_payload)
    if not roots:
        return []

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
    match = re.search(r"\bCHAT-([0-9a-fA-F]{8})\b", text)
    if not match:
        return ""
    return f"CHAT-{match.group(1).upper()}"


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


def _detect_run_type(
    message: str,
    phase_task_ids: dict[str, str],
    artifact_created: int,
    artifact_modified: int,
) -> str:
    """Clasifica el tipo de run para aplicar el threshold de scoring correcto."""
    phase_names = set(phase_task_ids.keys()) - {"lead_intake", "lead_close"}
    has_build = any(
        key in phase_names
        for key in ("build", "implement", "develop", "code", "fix", "refactor")
    ) or any(
        key.startswith("engineer") or key.startswith("eng_") for key in phase_names
    )
    has_artifacts = (artifact_created + artifact_modified) > 0

    if _is_context_only_query(message) and not has_build and not has_artifacts:
        return "context_recovery"

    if has_build or has_artifacts:
        return "build"

    researcher_phases = {"discovery", "research", "plan_research", "analysis", "investigate"}
    review_phases = {"review", "plan_risks", "audit", "security"}
    qa_phases = {"qa", "test", "verify", "acceptance"}
    non_build = researcher_phases | review_phases | qa_phases
    if phase_names and phase_names.issubset(non_build):
        return "planning"

    return "mixed"
