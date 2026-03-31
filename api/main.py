import asyncio
import logging
import os
import json
import time
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import subprocess
import threading
import sys
import json as std_json

try:
    from dotenv import load_dotenv

    _root_env = Path(__file__).parent.parent / ".env"
    if _root_env.exists():
        # override=True: .env gana sobre vars vacías del shell (ej. ANTHROPIC_API_KEY="")
        load_dotenv(_root_env, override=True)
except ImportError:
    pass

# Import AI Team Dashboard requirements
from aiteam.dashboard import build_dashboard_payload
from aiteam.autotools import AutoToolIntegrator
from aiteam.cli import build_default_orchestrator, cmd_notebooklm_sync
from aiteam.chat_runtime import ChatRunState
from aiteam.context_curator import (
    ContextCuratorStore,
    estimate_context_compaction_value,
    estimate_context_pressure,
)
from aiteam.chat_policy import (
    CHAT_VALIDATION_OWNER,
    ChatPolicyInput,
    build_chat_task_policy_metadata,
    evaluate_chat_policy,
    resolve_run_type_policy,
)
from aiteam.lead_control import (
    extract_clarify_directive as _lead_control_extract_clarify_directive,
    extract_delegate_directive as _lead_control_extract_delegate_directive,
    extract_delegate_request as _lead_control_extract_delegate_request,
    extract_evidence_plan as _lead_control_extract_evidence_plan,
    extract_lcp_directives as _lead_control_extract_lcp_directives,
    iter_lead_checkpoint_directives as _lead_control_iter_lead_checkpoint_directives,
    resolve_lead_intake as _lead_control_resolve_lead_intake,
    strip_selected_lcp_directives as _lead_control_strip_selected_lcp_directives,
    strip_lcp_directives as _lead_control_strip_lcp_directives,
)
from aiteam.persistence import AtomicFileWriter
from aiteam.pilot import compute_pilot_metrics
from aiteam.tool_specialists import (
    build_tool_specialist_metadata,
    replacement_specialists_from_metadata,
)
from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask
from aiteam.workflow_planner import (
    PhaseSpec,
    default_phases,
    parse_workflow_plan,
)

# Rondas maximas para ejecutar SOLO lead_intake en el flujo de dos pasos.
# Lead_intake es una sola tarea; 5 rondas es mas que suficiente.
_LEAD_INTAKE_MAX_ROUNDS = 5

logger = logging.getLogger(__name__)


class SimplePTY:
    def __init__(self, cols, rows):
        self.proc = None

    def spawn(self, cmd, cwd=None):
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def write(self, data):
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def read(self, size):
        if self.proc and self.proc.stdout:
            return self.proc.stdout.read(1)
        return ""

    def set_size(self, cols, rows):
        pass

    def isalive(self):
        if not self.proc:
            return False
        return self.proc.poll() is None

    def close(self):
        if self.proc:
            self.proc.terminate()


try:
    from pywinpty import PTY
except ImportError:
    PTY = SimplePTY

app = FastAPI(title="AI Teams IDE Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9483",
        "http://127.0.0.1:9483",
        "http://localhost:9490",
        "http://127.0.0.1:9490",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

active_pty = None


class WorkspacePath(BaseModel):
    path: str


class NewProjectRequest(BaseModel):
    name: str


class TeamChatRequest(BaseModel):
    message: str
    role: str = "engineer"
    complexity: str = "medium"
    criticality: str = "medium"
    mode: str = "sprint5"
    max_rounds: int | None = None
    client_task_id: str = ""
    strict_mode: bool = False
    auto_extend_weak_runs: bool = True
    allow_low_productivity_override: bool = False


class TeamChatResponse(BaseModel):
    task_id: str
    role: str
    state: str
    response: str
    decision_justification: str
    elapsed_ms: int
    lead_task_id: str
    delegated_task_ids: list[str]
    phase_task_ids: dict[str, str]
    chat_mode: str = "sprint5"
    round_budget: int = 0
    rounds_used: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    continuation_requested: bool = False
    continuation_of: str = ""
    artifact_created: int = 0
    artifact_modified: int = 0
    artifact_files: list[str] = []
    productivity_score: int = 0
    reasoning_score: int = 0
    productivity_status: str = "weak"
    execution_attempts: int = 0
    execution_success: int = 0
    execution_steps: int = 0
    next_action_hint: str = ""
    strict_mode: bool = False
    strict_mode_applied: bool = False
    auto_extended_rounds: int = 0
    productivity_threshold: int = 35
    low_productivity_rejected: bool = False
    low_productivity_override: bool = False
    execution_mode: str = "unknown"
    placeholder_outputs: int = 0
    placeholder_output_ratio: float = 0.0
    evidence_gate_applied: bool = False
    evidence_gate_failures: list[str] = []
    execution_steps_success: int = 0
    successful_checks: list[str] = []
    successful_check_count: int = 0
    live_mode_required: bool = False
    live_mode_rejected: bool = False
    advisory_mode: bool = False
    advisory_reason: str = ""
    policy_review_required: bool = False
    validation_owner: str = ""
    policy_signals: list[str] = []
    phase_evidence_plan: dict[str, dict[str, object]] = {}
    delegate_batches: list[dict[str, object]] = []
    delegate_economics: dict[str, object] = {}
    specialist_reports: list[dict[str, object]] = []
    specialist_report_summary: dict[str, object] = {}
    specialist_reports: list[dict[str, object]] = []
    specialist_report_summary: dict[str, object] = {}
    waiting_user: bool = False
    clarification_question: str = ""


class TeamChatProgressResponse(BaseModel):
    task_id: str
    exists: bool = False
    state: str = "queued"
    round_budget: int = 0
    rounds_used: int = 0
    phase_states: dict[str, str] = {}
    completed_tasks: int = 0
    pending_tasks: int = 0
    failed_tasks: int = 0
    execution_attempts: int = 0
    execution_steps: int = 0
    execution_steps_success: int = 0
    execution_mode: str = "queued"
    placeholder_outputs: int = 0
    successful_checks: list[str] = []
    successful_check_count: int = 0
    live_mode_required: bool = False
    live_mode_rejected: bool = False
    evidence_gate_rejected: bool = False
    evidence_gate_failures: list[str] = []
    last_event: str = ""
    last_event_ts: str = ""
    dynamic_phases_ready: bool = False
    phase_task_ids: dict[str, str] = {}
    phase_evidence_plan: dict[str, dict[str, object]] = {}
    delegate_batches: list[dict[str, object]] = []
    delegate_economics: dict[str, object] = {}
    specialist_reports: list[dict[str, object]] = []
    specialist_report_summary: dict[str, object] = {}
    waiting_user: bool = False                # E7-D4: alguna tarea mid-run está pausada esperando al usuario
    clarification_question: str = ""          # pregunta emitida por el agente pausado


class OperatorTimelineItem(BaseModel):
    ts: str = ""
    event_type: str = ""
    task_id: str = ""
    level: str = "info"
    summary: str = ""
    assignee: str = ""
    execution_round: int = 0
    execution_sub_iteration: int = 0
    gate_iteration: int = 0
    blocked_reason: str = ""
    handoff_from: str = ""
    handoff_to: str = ""
    conversation_thread_id: str = ""
    meeting_kind: str = ""
    artifact_created: int = 0
    artifact_modified: int = 0
    artifact_files: list[str] = []
    productivity_score: int = 0
    reasoning_score: int = 0


class OperatorTimelineResponse(BaseModel):
    selected_task_id: str = ""
    latest_task_id: str = ""
    available_runs: list[str] = []
    total: int = 0
    items: list[OperatorTimelineItem] = []
    progress: TeamChatProgressResponse | None = None


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
    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
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


def _is_game_request(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    hints = ["juego", "game", "arcade", "platformer", "minijuego", "videojuego"]
    return any(token in normalized for token in hints)


def _is_game_followup_request(workspace: Path, message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    has_game_files = (
        (workspace / "game.js").exists()
        or (workspace / "index.html").exists()
        or (workspace / ".aiteam_game_progress.json").exists()
    )
    if not has_game_files:
        return False
    followup_hints = [
        "continue",
        "continua",
        "continúe",
        "sigue",
        "next slice",
        "next step",
        "highest-impact",
        "design",
        "diseno",
        "diseño",
        "gameplay",
        "iteracion",
        "iteración",
    ]
    return any(token in normalized for token in followup_hints)


def _extract_lcp_directives(text: str) -> dict:
    return _lead_control_extract_lcp_directives(text)


def _strip_lcp_directives(text: str) -> str:
    return _lead_control_strip_lcp_directives(text)


def _extract_clarify_directive(text: str) -> str | None:
    return _lead_control_extract_clarify_directive(text)


def _extract_delegate_directive(text: str) -> str | None:
    return _lead_control_extract_delegate_directive(text)


def _extract_delegate_request(text: str):
    return _lead_control_extract_delegate_request(text)


def _extract_evidence_plan(text: str) -> dict[str, dict[str, object]]:
    return _lead_control_extract_evidence_plan(text)


def _resolve_delegate_plan(delegate_request) -> dict[str, object]:
    intent = str(getattr(delegate_request, "intent", "delegate") or "delegate").strip().lower()
    plans: dict[str, dict[str, object]] = {
        "delegate": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "phase_prefix": "delegate_scout",
            "title_prefix": "Delegate scout",
            "instruction": "Inspecciona el repositorio y el contexto local para responder con hechos concretos.",
        },
        "delegate_repo_scan": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "phase_prefix": "delegate_repo_scan",
            "title_prefix": "Delegate repo scan",
            "instruction": "Recorre archivos, estructura, git y pistas locales; devuelve un mapa corto y factual.",
        },
        "delegate_browser_repro": {
            "role": Role.QA,
            "specialist": "browser_operator",
            "required_capabilities": ["browser_test", "browser_nav"],
            "phase_prefix": "delegate_browser_repro",
            "title_prefix": "Delegate browser repro",
            "instruction": "Reproduce el flujo en navegador, Playwright o MCP UI y devuelve solo pasos, resultado y evidencia compacta.",
        },
        "delegate_lsp_impact": {
            "role": Role.RESEARCHER,
            "specialist": "lsp_navigator",
            "required_capabilities": ["lsp_symbols", "lsp_references"],
            "phase_prefix": "delegate_lsp_impact",
            "title_prefix": "Delegate LSP impact",
            "instruction": "Usa navegacion semantica y referencias para resumir impacto, hotspots y dependencias.",
        },
        "delegate_test_run": {
            "role": Role.QA,
            "specialist": "test_runner",
            "required_capabilities": ["test_execute", "build_execute"],
            "phase_prefix": "delegate_test_run",
            "title_prefix": "Delegate test run",
            "instruction": "Ejecuta checks o tests relevantes y resume fallos, evidencia y cobertura util.",
        },
        "delegate_mcp_probe": {
            "role": Role.SCOUT,
            "specialist": "mcp_operator",
            "required_capabilities": ["external_mcp"],
            "phase_prefix": "delegate_mcp_probe",
            "title_prefix": "Delegate MCP probe",
            "instruction": "Usa el MCP o integracion externa asignada y devuelve resultados estructurados y compactos.",
        },
    }
    return dict(plans.get(intent, plans["delegate"]))


def _resolve_delegate_round_budget(delegate_request) -> int:
    requested = _safe_int_value(getattr(delegate_request, "delegate_budget", 3), 3)
    wait_policy = str(getattr(delegate_request, "wait_policy", "all") or "all").strip().lower()
    budget = max(1, min(requested, 6))
    if wait_policy == "best_effort":
        budget = max(1, min(budget, 2))
    elif wait_policy == "quorum":
        budget = max(2, min(budget, 4))
    return budget


def _is_delegate_phase_name(phase_name: str) -> bool:
    return str(phase_name or "").strip().lower().startswith("delegate_")


def _is_supporting_control_phase(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    return normalized == "lead_intake" or _is_delegate_phase_name(normalized)


def _delegate_specialist_plan(specialist: str) -> dict[str, object]:
    catalog: dict[str, dict[str, object]] = {
        "repo_scout": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "instruction": "Inspecciona repositorio, archivos, git y contexto local; devuelve hechos compactos.",
        },
        "browser_operator": {
            "role": Role.QA,
            "specialist": "browser_operator",
            "required_capabilities": ["browser_test", "browser_nav"],
            "instruction": "Opera navegador, Playwright o MCP UI y resume pasos reproducidos, resultado y evidencia compacta.",
        },
        "lsp_navigator": {
            "role": Role.RESEARCHER,
            "specialist": "lsp_navigator",
            "required_capabilities": ["lsp_symbols", "lsp_references"],
            "instruction": "Usa navegacion semantica para resumir impacto, referencias y hotspots.",
        },
        "test_runner": {
            "role": Role.QA,
            "specialist": "test_runner",
            "required_capabilities": ["test_execute", "build_execute"],
            "instruction": "Ejecuta checks o tests relevantes y resume fallos, evidencia y regresiones.",
        },
        "mcp_operator": {
            "role": Role.SCOUT,
            "specialist": "mcp_operator",
            "required_capabilities": ["external_mcp"],
            "instruction": "Usa MCPs o integraciones externas y devuelve resultados estructurados, compactos y sin transcripts crudos.",
        },
        "skill_worker": {
            "role": Role.SCOUT,
            "specialist": "skill_worker",
            "required_capabilities": ["skill_run"],
            "instruction": "Ejecuta la skill o playbook asignado y devuelve evidencia compacta, hallazgos y recomendacion operativa.",
        },
    }
    return dict(catalog.get(specialist, catalog["repo_scout"]))


def _delegate_specialist_targets(
    *,
    intent: str,
    specialist: str,
) -> tuple[list[str], list[str]]:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    skill_targets: list[str] = []
    lsp_targets: list[str] = []

    if normalized_specialist == "browser_operator" or normalized_intent == "delegate_browser_repro":
        skill_targets.append("playwright_qa_skill")
    if normalized_specialist == "lsp_navigator" or normalized_intent == "delegate_lsp_impact":
        lsp_targets.extend(["symbols", "references", "impact"])

    return list(dict.fromkeys(skill_targets)), list(dict.fromkeys(lsp_targets))


def _delegate_report_contract(
    *,
    intent: str,
    specialist: str,
) -> str:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    if normalized_specialist == "browser_operator" or normalized_intent == "delegate_browser_repro":
        return (
            "Formato obligatorio: summary, steps_reproduced, result, evidence, artifacts, risks, recommendation. "
            "Si usas navegador, Playwright o MCP UI, devuelve solo pasos reproducidos y evidencia compacta; no pegues transcripts crudos."
        )
    if normalized_specialist == "mcp_operator" or normalized_intent == "delegate_mcp_probe":
        return (
            "Formato obligatorio: summary, result, evidence, artifacts, risks, recommendation. "
            "Si operas un MCP de UI, incluye tambien steps_reproduced compactos; no pegues transcripts crudos."
        )
    return "Formato obligatorio: summary, evidence, artifacts, risks, recommendation compactos."


def _delegate_catalog_capabilities(
    *,
    intent: str,
    specialist: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    query: str = "",
) -> set[str]:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    normalized_query = str(query or "").strip().lower()
    caps: set[str] = set()

    for capability in list(required_capabilities or []):
        capability_name = str(capability or "").strip().lower()
        if capability_name in {"browser_nav", "browser_test"}:
            caps.update({"browser_testing", "e2e", "web_automation"})
        elif capability_name in {"test_execute", "build_execute"}:
            caps.update({"code_quality"})
        elif capability_name == "repo_read":
            caps.update({"research"})

    if normalized_intent == "delegate_browser_repro":
        caps.update({"browser_testing", "e2e", "web_automation"})
    if normalized_intent == "delegate_lsp_impact":
        caps.update({"research"})
    if normalized_intent == "delegate_test_run":
        caps.update({"code_quality"})
    if normalized_specialist == "mcp_operator" or normalized_intent == "delegate_mcp_probe":
        if any(token in normalized_query for token in ("semgrep", "sast", "security", "vulnerability")):
            caps.update({"security_scan", "sast", "code_quality"})
        if any(token in normalized_query for token in ("playwright", "browser", "ui", "e2e", "web")):
            caps.update({"browser_testing", "e2e", "web_automation"})
        if any(token in normalized_query for token in ("perplexity", "context7", "docs", "documentation", "research")):
            caps.update({"research", "documentation", "ground_truth"})
    return caps


def _resolve_delegate_rewiring(
    *,
    workspace: Path | None,
    intent: str,
    specialist: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    query: str = "",
) -> dict[str, object]:
    normalized_specialist = str(specialist or "").strip().lower()
    if normalized_specialist != "mcp_operator":
        return {}

    project_root = Path(workspace or PROJECT_ROOT)
    integrator = AutoToolIntegrator(
        runtime_dir=project_root / "runtime",
        project_root=project_root,
    )
    discovery_caps = _delegate_catalog_capabilities(
        intent=intent,
        specialist=specialist,
        required_capabilities=required_capabilities,
        query=query,
    )
    if not discovery_caps:
        return {}

    suggestions = integrator.suggest_requirements(discovery_caps, limit=3)
    replacements = [row for row in suggestions if str(row.get("replacement_for", "") or "").strip()]
    if not replacements:
        return {}

    candidate_names = [
        str(row.get("name", "") or "").strip().lower()
        for row in replacements
        if str(row.get("name", "") or "").strip()
    ]
    replacement_for = sorted(
        {
            str(row.get("replacement_for", "") or "").strip().lower()
            for row in replacements
            if str(row.get("replacement_for", "") or "").strip()
        }
    )
    replacement_specialists = replacement_specialists_from_metadata(
        {"tool_rewiring_candidates": candidate_names}
    )
    preferred_specialist = replacement_specialists[0] if replacement_specialists else ""
    skill_targets = [name for name in candidate_names if name.endswith("_skill")]
    lsp_targets: list[str] = []
    if not preferred_specialist:
        return {}

    return {
        "tool_rewiring_active": True,
        "tool_rewiring_candidates": candidate_names,
        "tool_rewiring_replacement_for": replacement_for,
        "tool_rewiring_preferred_specialist": preferred_specialist,
        "tool_rewiring_suppress_mcp_operator": True,
        "tool_rewiring_reason": "delegate_catalog_replacement_preferred_over_mcp_operator",
        "skill_targets": list(dict.fromkeys(skill_targets)),
        "lsp_targets": lsp_targets,
    }


def _build_delegate_request(intent: str, *, query: str, wait_policy: str, delegate_budget: int):
    class _DelegateRequest:
        def __init__(self, intent: str, query: str, wait_policy: str, delegate_budget: int) -> None:
            self.intent = intent
            self.query = query
            self.wait_policy = wait_policy
            self.delegate_budget = delegate_budget

    return _DelegateRequest(
        intent=str(intent or "").strip().lower(),
        query=str(query or "").strip(),
        wait_policy=str(wait_policy or "all").strip().lower(),
        delegate_budget=max(1, int(delegate_budget or 3)),
    )


def _resolve_delegate_assignments(
    delegate_request,
    *,
    workspace: Path | None = None,
) -> list[dict[str, object]]:
    primary = _resolve_delegate_plan(delegate_request)
    intent = str(getattr(delegate_request, "intent", "delegate") or "delegate").strip().lower()
    wait_policy = str(getattr(delegate_request, "wait_policy", "all") or "all").strip().lower()
    delegate_query = str(getattr(delegate_request, "query", "") or "").strip()
    roster_by_intent: dict[str, list[str]] = {
        "delegate": ["repo_scout", "lsp_navigator"],
        "delegate_repo_scan": ["repo_scout", "lsp_navigator"],
        "delegate_browser_repro": ["browser_operator", "mcp_operator", "test_runner", "repo_scout"],
        "delegate_lsp_impact": ["lsp_navigator", "repo_scout", "test_runner"],
        "delegate_test_run": ["test_runner", "repo_scout", "lsp_navigator"],
        "delegate_mcp_probe": ["mcp_operator", "repo_scout"],
    }
    roster = list(roster_by_intent.get(intent, [str(primary.get("specialist", "repo_scout"))]))
    if wait_policy == "best_effort":
        selected = roster[:1]
    elif wait_policy == "quorum":
        selected = roster[: min(len(roster), 3)]
    else:
        selected = roster

    assignments: list[dict[str, object]] = []
    for specialist_name in selected:
        specialist_plan = _delegate_specialist_plan(specialist_name)
        rewiring = _resolve_delegate_rewiring(
            workspace=workspace,
            intent=intent,
            specialist=specialist_name,
            required_capabilities=list(specialist_plan.get("required_capabilities", []) or []),
            query=delegate_query,
        )
        effective_specialist = str(
            rewiring.get("tool_rewiring_preferred_specialist", specialist_name) or specialist_name
        ).strip().lower()
        if effective_specialist != str(specialist_name or "").strip().lower():
            specialist_plan = _delegate_specialist_plan(effective_specialist)
        skill_targets, lsp_targets = _delegate_specialist_targets(
            intent=intent,
            specialist=effective_specialist,
        )
        skill_targets = list(
            dict.fromkeys(
                list(skill_targets)
                + [
                    str(item).strip()
                    for item in list(rewiring.get("skill_targets", []) or [])
                    if str(item).strip()
                ]
            )
        )
        lsp_targets = list(
            dict.fromkeys(
                list(lsp_targets)
                + [
                    str(item).strip()
                    for item in list(rewiring.get("lsp_targets", []) or [])
                    if str(item).strip()
                ]
            )
        )
        base_instruction = str(
            specialist_plan.get("instruction") or primary.get("instruction", "Responde con hechos concretos.")
        )
        if rewiring:
            base_instruction = (
                "El catalogo marca un replacement preferente sobre MCP para esta consulta. "
                "Opera la ruta de replacement asignada y devuelve evidencia compacta.\n"
                f"{base_instruction}"
            )
        assignments.append(
            {
                **specialist_plan,
                "phase_prefix": primary.get("phase_prefix", "delegate_scout"),
                "title_prefix": primary.get("title_prefix", "Delegate specialist"),
                "instruction": base_instruction,
                "skill_targets": skill_targets,
                "lsp_targets": lsp_targets,
                "rewired_from_specialist": (
                    str(specialist_name or "").strip().lower()
                    if rewiring and effective_specialist != str(specialist_name or "").strip().lower()
                    else ""
                ),
                **rewiring,
            }
        )
    return assignments


def _delegate_quorum_target(wait_policy: str, assignment_count: int) -> int:
    if assignment_count <= 0:
        return 0
    normalized = str(wait_policy or "all").strip().lower()
    if normalized == "best_effort":
        return 1
    if normalized == "all":
        return assignment_count
    if assignment_count == 1:
        return 1
    return max(2, (assignment_count // 2) + 1)


def _aggregate_delegate_results(
    entries: list[dict[str, object]],
    *,
    wait_policy: str,
) -> tuple[str, bool]:
    total = len(entries)
    quorum_target = _delegate_quorum_target(wait_policy, total)
    completed = sum(
        1
        for entry in entries
        if str(entry.get("state", "") or "").strip().lower() == "completed"
    )
    quorum_met = completed >= quorum_target if quorum_target > 0 else False
    header = (
        f"Delegacion especializada agregada "
        f"(wait_policy={wait_policy}, completed={completed}/{total}, quorum_target={quorum_target}, quorum_met={'yes' if quorum_met else 'no'})"
    )
    lines = [header]
    for entry in entries:
        specialist = str(entry.get("specialist", "") or "").strip()
        phase = str(entry.get("phase", "") or "").strip()
        state = str(entry.get("state", "") or "missing").strip()
        result = _compact_delegated_result(
            str(entry.get("result", "") or ""),
            state=state,
        )
        contract = str(entry.get("report_contract_version", "") or "").strip()
        contract_suffix = f" contract={contract}" if contract else ""
        lines.append(
            f"- {phase} [{specialist}] state={state}{contract_suffix} result={result}"
        )
    return "\n".join(lines), quorum_met


def _execute_delegate_request(
    *,
    orch,
    task_root: str,
    workspace: Path,
    runtime_dir: Path,
    delegate_request,
    source_task_id: str,
    source_phase: str,
    delegate_cycle: int,
    rerun_budget: int,
) -> dict[str, object]:
    delegate_plan = _resolve_delegate_plan(delegate_request)
    delegate_assignments = _resolve_delegate_assignments(delegate_request, workspace=workspace)
    delegate_round_budget = _resolve_delegate_round_budget(delegate_request)
    delegate_wait_policy = str(delegate_request.wait_policy or "all").strip().lower()
    delegate_query = str(delegate_request.query or "").strip()
    raw_context = (
        _build_scout_project_state_context(workspace)
        + "\n\n"
        + _build_scout_session_history_context(runtime_dir)
    )
    delegate_entries: list[dict[str, object]] = []

    for assignment in delegate_assignments:
        delegate_phase_prefix = str(
            assignment.get("phase_prefix", delegate_plan.get("phase_prefix", "delegate_scout"))
        )
        delegate_specialist = str(assignment.get("specialist", "repo_scout"))
        delegate_caps = list(assignment.get("required_capabilities", []) or [])
        delegate_skill_targets = [
            str(item).strip()
            for item in list(assignment.get("skill_targets", []) or [])
            if str(item).strip()
        ]
        delegate_lsp_targets = [
            str(item).strip()
            for item in list(assignment.get("lsp_targets", []) or [])
            if str(item).strip()
        ]
        if not delegate_skill_targets and not delegate_lsp_targets:
            delegate_skill_targets, delegate_lsp_targets = _delegate_specialist_targets(
                intent=str(delegate_request.intent or ""),
                specialist=delegate_specialist,
            )
        delegate_report_contract = _delegate_report_contract(
            intent=str(delegate_request.intent or ""),
            specialist=delegate_specialist,
        )
        delegate_phase = f"{delegate_phase_prefix}_{delegate_cycle}_{delegate_specialist}"
        delegate_task_id = f"{task_root}::{delegate_phase}"
        delegate_entries.append(
            {
                "task_id": delegate_task_id,
                "phase": delegate_phase,
                "specialist": delegate_specialist,
                "report_contract_version": "operator_report_v1",
            }
        )
        orch.submit_task(
            WorkTask(
                task_id=delegate_task_id,
                title=f"{assignment.get('title_prefix', 'Delegate specialist')}: {delegate_query[:60]}",
                description=(
                    f"{assignment.get('instruction', 'Responde con hechos concretos.')}\n\n"
                    f"Consulta del Team Lead (maximo 8 lineas de respuesta compacta):\n"
                    f"{delegate_query}\n\n"
                    "Contexto disponible:\n"
                    f"{raw_context}\n\n"
                    "Solo hechos, evidencia compacta y recomendacion operativa breve. "
                    "Sin arbitraje de producto.\n"
                    f"{delegate_report_contract}"
                ),
                role=assignment.get("role", Role.SCOUT),
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                metadata={
                    "is_scout": assignment.get("role", Role.SCOUT) == Role.SCOUT,
                    "scout_type": "on_demand_delegate",
                    "skip_quality_gates": True,
                    "phase": delegate_phase,
                    "chat_parent": task_root,
                    "required_capabilities": delegate_caps,
                    "delegated_by": "team_lead",
                    "delegate_intent": delegate_request.intent,
                    "delegate_wait_policy": delegate_wait_policy,
                    "delegate_budget_rounds": delegate_round_budget,
                    "delegate_source_phase": source_phase,
                    "delegate_report_contract_version": "operator_report_v1",
                    "delegate_original_specialist": str(
                        assignment.get("rewired_from_specialist", "") or ""
                    ).strip(),
                    "skill_targets": delegate_skill_targets,
                    "lsp_targets": delegate_lsp_targets,
                    "tool_rewiring_active": bool(assignment.get("tool_rewiring_active", False)),
                    "tool_rewiring_candidates": list(
                        assignment.get("tool_rewiring_candidates", []) or []
                    ),
                    "tool_rewiring_replacement_for": list(
                        assignment.get("tool_rewiring_replacement_for", []) or []
                    ),
                    "tool_rewiring_preferred_specialist": str(
                        assignment.get("tool_rewiring_preferred_specialist", "") or ""
                    ).strip(),
                    "tool_rewiring_reason": str(
                        assignment.get("tool_rewiring_reason", "") or ""
                    ).strip(),
                    **build_tool_specialist_metadata(
                        specialist=delegate_specialist,
                        required_capabilities=delegate_caps,
                        reason=(
                            f"delegacion del Team Lead via {delegate_request.intent}; "
                            f"source_phase={source_phase}; wait_policy={delegate_wait_policy}"
                        ),
                        skill_targets=delegate_skill_targets,
                        lsp_targets=delegate_lsp_targets,
                    ),
                },
            )
        )

    orch.run_until_idle(max_rounds=delegate_round_budget)

    delegate_ws = orch._get_workflow_state(task_root)
    for entry in delegate_entries:
        task_id = str(entry.get("task_id", "") or "")
        phase = str(entry.get("phase", "") or "")
        delegate_task = orch.taskboard.get_task(task_id)
        result = str(delegate_task.metadata.get("result", "") if delegate_task else "")
        state = str(delegate_task.state.value if delegate_task else "missing")
        if not result:
            result = delegate_ws.get("phase_outputs", {}).get(phase, "")
        if not result:
            result = "Sin datos disponibles para esta consulta."
        entry["result"] = result
        entry["state"] = state
        entry["result_len"] = len(result)

    aggregated_delegate_result, delegate_quorum_met = _aggregate_delegate_results(
        delegate_entries,
        wait_policy=delegate_wait_policy,
    )
    batch_economics = _estimate_delegate_batch_economics(delegate_entries)
    source_task = orch.taskboard.get_task(source_task_id)
    if source_task is not None:
        inject_block = (
            f"\n\n[Resultado de tu delegación"
            f" (ciclo {delegate_cycle}, source_phase='{source_phase}', "
            f"intent='{delegate_request.intent}', wait_policy='{delegate_wait_policy}', "
            f"quorum_met='{delegate_quorum_met}', query: '{delegate_query[:80]}'):\n"
            f"{aggregated_delegate_result[:900]}\n]"
        )
        source_task.description = source_task.description + inject_block
        orch.taskboard.retry_task(
            source_task_id,
            reason=f"delegate_result_injected ({source_phase}, cycle {delegate_cycle})",
        )
        orch.run_until_idle(max_rounds=rerun_budget)

    batch_payload = {
        "source_phase": source_phase,
        "source_task_id": source_task_id,
        "intent": delegate_request.intent,
        "query": delegate_query[:200],
        "wait_policy": delegate_wait_policy,
        "delegate_budget_rounds": delegate_round_budget,
        "delegate_cycle": delegate_cycle,
        "quorum_met": delegate_quorum_met,
        "specialists": [str(entry.get("specialist", "") or "") for entry in delegate_entries],
        "task_ids": [str(entry.get("task_id", "") or "") for entry in delegate_entries],
        "states": [str(entry.get("state", "") or "") for entry in delegate_entries],
        "result_lengths": [int(entry.get("result_len", 0) or 0) for entry in delegate_entries],
        "economics": batch_economics,
    }
    delegate_ws.setdefault("delegate_batches", []).append(batch_payload)
    delegate_ws["delegate_economics_summary"] = _summarize_delegate_economics(
        _coerce_delegate_batches(delegate_ws.get("delegate_batches", []))
    )
    orch._save_workflow_state()
    orch.event_logger.emit(
        "lcp_directive_applied",
        {
            "task_id": task_root,
            "directive": "delegate",
            "source_phase": source_phase,
            "intent": delegate_request.intent,
            "query": delegate_query[:120],
            "cycle": delegate_cycle,
            "wait_policy": delegate_wait_policy,
            "delegate_budget_rounds": delegate_round_budget,
            "specialists": batch_payload["specialists"],
            "quorum_met": delegate_quorum_met,
            "delegate_result_len": len(aggregated_delegate_result),
        },
    )
    orch.event_logger.emit(
        "delegate_economics_estimated",
        {
            "task_id": task_root,
            "source_phase": source_phase,
            "intent": delegate_request.intent,
            "wait_policy": delegate_wait_policy,
            "quorum_met": delegate_quorum_met,
            **batch_economics,
        },
    )
    return {
        "aggregated_result": aggregated_delegate_result,
        "quorum_met": delegate_quorum_met,
        "entries": delegate_entries,
        "delegate_budget_rounds": delegate_round_budget,
        "wait_policy": delegate_wait_policy,
        "economics": batch_economics,
    }


def _structured_evidence_specs_for_phase(
    phase_id: str,
    phase_evidence_plan: dict[str, dict[str, object]],
    *,
    workspace: Path | None = None,
) -> list[dict[str, object]]:
    entry = dict(phase_evidence_plan.get(phase_id) or {})
    intents = [
        str(item).strip().lower()
        for item in list(entry.get("delegate_intents", []) or [])
        if str(item).strip()
    ]
    wait_policy = str(entry.get("wait_policy", "all") or "all").strip().lower()
    delegate_budget = max(1, _safe_int_value(entry.get("delegate_budget", 3), 3))
    specs: list[dict[str, object]] = []
    for intent in intents:
        delegate_request = _build_delegate_request(
            intent,
            query=f"Genera evidencia especializada para la fase '{phase_id}'.",
            wait_policy=wait_policy,
            delegate_budget=delegate_budget,
        )
        assignments = _resolve_delegate_assignments(
            delegate_request,
            workspace=Path(workspace or PROJECT_ROOT),
        )
        for assign_index, assignment in enumerate(assignments):
            specialist = str(assignment.get("specialist", "repo_scout"))
            evidence_phase_id = f"delegate_{phase_id}_{specialist}_{assign_index}"
            skill_targets = [
                str(item).strip()
                for item in list(assignment.get("skill_targets", []) or [])
                if str(item).strip()
            ]
            lsp_targets = [
                str(item).strip()
                for item in list(assignment.get("lsp_targets", []) or [])
                if str(item).strip()
            ]
            if not skill_targets and not lsp_targets:
                skill_targets, lsp_targets = _delegate_specialist_targets(
                    intent=intent,
                    specialist=specialist,
                )
            specs.append(
                {
                    "phase_id": evidence_phase_id,
                    "intent": intent,
                    "specialist": specialist,
                    "role": assignment.get("role", Role.SCOUT),
                    "required_capabilities": list(
                        assignment.get("required_capabilities", []) or []
                    ),
                    "instruction": str(
                        assignment.get("instruction", "Recoge evidencia especializada.")
                    ),
                    "skill_targets": skill_targets,
                    "lsp_targets": lsp_targets,
                    "report_contract": _delegate_report_contract(
                        intent=intent,
                        specialist=specialist,
                    ),
                    "wait_policy": wait_policy,
                    "delegate_budget": delegate_budget,
                    "source_phase": phase_id,
                }
            )
    return specs


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


def _delegate_specialist_economics_profile(specialist: str) -> dict[str, int]:
    specialist_key = str(specialist or "").strip().lower()
    profiles: dict[str, dict[str, int]] = {
        "repo_scout": {
            "avoided_tokens": 600,
            "operator_tokens": 180,
            "cost_units_saved": 5,
        },
        "lsp_navigator": {
            "avoided_tokens": 700,
            "operator_tokens": 220,
            "cost_units_saved": 6,
        },
        "test_runner": {
            "avoided_tokens": 800,
            "operator_tokens": 260,
            "cost_units_saved": 7,
        },
        "browser_operator": {
            "avoided_tokens": 1400,
            "operator_tokens": 420,
            "cost_units_saved": 10,
        },
        "mcp_operator": {
            "avoided_tokens": 900,
            "operator_tokens": 300,
            "cost_units_saved": 7,
        },
        "skill_worker": {
            "avoided_tokens": 750,
            "operator_tokens": 240,
            "cost_units_saved": 6,
        },
    }
    return profiles.get(
        specialist_key,
        {
            "avoided_tokens": 650,
            "operator_tokens": 220,
            "cost_units_saved": 5,
        },
    )


def _delegate_state_multiplier(state: str) -> tuple[float, float]:
    normalized = str(state or "").strip().lower()
    if normalized == "completed":
        return 1.0, 1.0
    if normalized == "failed":
        return 0.5, 0.7
    if normalized in {"claimed", "ready", "pending", "blocked"}:
        return 0.4, 0.5
    return 0.3, 0.4


def _estimate_delegate_batch_economics(
    delegate_entries: list[dict[str, object]],
) -> dict[str, object]:
    specialist_breakdown: dict[str, dict[str, int]] = {}
    estimated_lead_tokens_avoided = 0
    estimated_operator_tokens_used = 0
    estimated_cost_units_saved = 0

    for entry in delegate_entries:
        specialist = str(entry.get("specialist", "") or "").strip().lower() or "unknown"
        state = str(entry.get("state", "") or "").strip().lower()
        avoided_factor, operator_factor = _delegate_state_multiplier(state)
        profile = _delegate_specialist_economics_profile(specialist)
        avoided_tokens = int(round(profile["avoided_tokens"] * avoided_factor))
        operator_tokens = int(round(profile["operator_tokens"] * operator_factor))
        cost_units_saved = max(
            0,
            int(round(profile["cost_units_saved"] * avoided_factor)),
        )
        estimated_lead_tokens_avoided += avoided_tokens
        estimated_operator_tokens_used += operator_tokens
        estimated_cost_units_saved += cost_units_saved
        specialist_row = specialist_breakdown.setdefault(
            specialist,
            {
                "count": 0,
                "completed": 0,
                "failed": 0,
                "estimated_lead_tokens_avoided": 0,
                "estimated_operator_tokens_used": 0,
                "estimated_net_tokens_saved": 0,
                "estimated_cost_units_saved": 0,
            },
        )
        specialist_row["count"] += 1
        if state == "completed":
            specialist_row["completed"] += 1
        elif state == "failed":
            specialist_row["failed"] += 1
        specialist_row["estimated_lead_tokens_avoided"] += avoided_tokens
        specialist_row["estimated_operator_tokens_used"] += operator_tokens
        specialist_row["estimated_net_tokens_saved"] += max(
            0, avoided_tokens - operator_tokens
        )
        specialist_row["estimated_cost_units_saved"] += cost_units_saved

    return {
        "economics_version": "delegate_economics_v1",
        "estimated": True,
        "specialist_tasks": len(delegate_entries),
        "estimated_lead_tokens_avoided": estimated_lead_tokens_avoided,
        "estimated_operator_tokens_used": estimated_operator_tokens_used,
        "estimated_net_tokens_saved": max(
            0, estimated_lead_tokens_avoided - estimated_operator_tokens_used
        ),
        "estimated_cost_units_saved": estimated_cost_units_saved,
        "specialist_breakdown": specialist_breakdown,
    }


def _summarize_delegate_economics(
    delegate_batches: list[dict[str, object]],
) -> dict[str, object]:
    summary = {
        "economics_version": "delegate_economics_v1",
        "estimated": True,
        "batch_count": 0,
        "specialist_task_count": 0,
        "estimated_lead_tokens_avoided": 0,
        "estimated_operator_tokens_used": 0,
        "estimated_net_tokens_saved": 0,
        "estimated_cost_units_saved": 0,
        "quorum_met_count": 0,
        "quorum_met_ratio": 0.0,
        "wait_policy_counts": {},
        "intent_counts": {},
        "specialist_breakdown": {},
    }
    if not delegate_batches:
        return summary

    wait_policy_counts: dict[str, int] = {}
    intent_counts: dict[str, int] = {}
    specialist_breakdown: dict[str, dict[str, int]] = {}

    for batch in delegate_batches:
        if not isinstance(batch, dict):
            continue
        summary["batch_count"] += 1
        economics = dict(batch.get("economics", {}) or {})
        summary["specialist_task_count"] += _safe_int_value(
            economics.get("specialist_tasks", 0), 0
        )
        summary["estimated_lead_tokens_avoided"] += _safe_int_value(
            economics.get("estimated_lead_tokens_avoided", 0), 0
        )
        summary["estimated_operator_tokens_used"] += _safe_int_value(
            economics.get("estimated_operator_tokens_used", 0), 0
        )
        summary["estimated_cost_units_saved"] += _safe_int_value(
            economics.get("estimated_cost_units_saved", 0), 0
        )
        if bool(batch.get("quorum_met", False)):
            summary["quorum_met_count"] += 1

        wait_policy = str(batch.get("wait_policy", "") or "").strip().lower()
        if wait_policy:
            wait_policy_counts[wait_policy] = wait_policy_counts.get(wait_policy, 0) + 1
        intent = str(batch.get("intent", "") or "").strip().lower()
        if intent:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

        for specialist, values in dict(economics.get("specialist_breakdown", {}) or {}).items():
            if not isinstance(values, dict):
                continue
            row = specialist_breakdown.setdefault(
                str(specialist),
                {
                    "count": 0,
                    "completed": 0,
                    "failed": 0,
                    "estimated_lead_tokens_avoided": 0,
                    "estimated_operator_tokens_used": 0,
                    "estimated_net_tokens_saved": 0,
                    "estimated_cost_units_saved": 0,
                },
            )
            row["count"] += _safe_int_value(values.get("count", 0), 0)
            row["completed"] += _safe_int_value(values.get("completed", 0), 0)
            row["failed"] += _safe_int_value(values.get("failed", 0), 0)
            row["estimated_lead_tokens_avoided"] += _safe_int_value(
                values.get("estimated_lead_tokens_avoided", 0), 0
            )
            row["estimated_operator_tokens_used"] += _safe_int_value(
                values.get("estimated_operator_tokens_used", 0), 0
            )
            row["estimated_net_tokens_saved"] += _safe_int_value(
                values.get("estimated_net_tokens_saved", 0), 0
            )
            row["estimated_cost_units_saved"] += _safe_int_value(
                values.get("estimated_cost_units_saved", 0), 0
            )

    summary["estimated_net_tokens_saved"] = max(
        0,
        int(summary["estimated_lead_tokens_avoided"])
        - int(summary["estimated_operator_tokens_used"]),
    )
    if int(summary["batch_count"]) > 0:
        summary["quorum_met_ratio"] = round(
            int(summary["quorum_met_count"]) / int(summary["batch_count"]),
            4,
        )
    summary["wait_policy_counts"] = wait_policy_counts
    summary["intent_counts"] = intent_counts
    summary["specialist_breakdown"] = specialist_breakdown
    return summary


def _message_suggests_browser_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    browser_terms = (
        "frontend",
        "ui",
        "ux",
        "browser",
        "playwright",
        "dom",
        "selector",
        "screenshot",
        "scrape",
        "scraping",
        "mcp",
        "page",
        "screen",
        "form",
        "react",
        "vite",
        "css",
        "html",
        "component",
        "web",
    )
    return any(term in normalized for term in browser_terms)


def _message_suggests_security_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    security_terms = (
        "security",
        "seguridad",
        "secure",
        "vulnerability",
        "vulnerabilidad",
        "semgrep",
        "sast",
        "audit",
        "auditoria",
        "compliance",
        "hardening",
        "auth",
        "authentication",
        "authorization",
        "secret",
        "credential",
        "token",
        "xss",
        "csrf",
        "sql injection",
        "owasp",
    )
    return any(term in normalized for term in security_terms)


def _message_suggests_research_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    research_terms = (
        "research",
        "investiga",
        "investigar",
        "documentacion",
        "documentation",
        "docs",
        "api reference",
        "best practices",
        "ground truth",
        "context7",
        "perplexity",
        "look up",
        "lookup",
        "manual",
        "spec",
        "specification",
        "integration guide",
    )
    return any(term in normalized for term in research_terms)


def _detect_preplan_surface_hints(message: str) -> dict[str, object]:
    surfaces: list[str] = []
    recommended_delegate_intents: list[str] = []
    recommended_specialists: list[str] = []

    if _message_suggests_browser_surface(message):
        surfaces.append("browser")
        recommended_delegate_intents.append("delegate_browser_repro")
        recommended_specialists.extend(["browser_operator", "skill_worker"])
    if _message_suggests_security_surface(message):
        surfaces.append("security")
        recommended_delegate_intents.append("delegate_mcp_probe")
        recommended_specialists.extend(["skill_worker", "repo_scout"])
    if _message_suggests_research_surface(message):
        surfaces.append("research")
        recommended_delegate_intents.append("delegate_mcp_probe")
        recommended_specialists.extend(["skill_worker", "repo_scout", "lsp_navigator"])

    if not surfaces:
        surfaces.append("general")
        recommended_delegate_intents.append("delegate_repo_scan")
        recommended_specialists.append("repo_scout")

    return {
        "surfaces": list(dict.fromkeys(surfaces)),
        "recommended_delegate_intents": list(dict.fromkeys(recommended_delegate_intents)),
        "recommended_specialists": list(dict.fromkeys(recommended_specialists)),
    }


def _estimate_preplan_context_pressure(
    *,
    runtime_dir: Path,
    continuation_requested: bool,
    continuation_of: str,
    continuation_snapshot: str,
) -> dict[str, object]:
    previous_delegate_batches = 0
    previous_phase_summaries = 0
    previous_specialist_reports = 0
    previous_invalidations = 0
    previous_open_questions = 0
    previous_phase_outputs: dict[str, object] = {}
    previous_phase_context_summaries: dict[str, object] = {}
    previous_project_context_summary = ""
    previous_chat_context_summary = ""

    if continuation_of:
        workflow_payload = _read_json_payload(
            runtime_dir / "workflow_state.json",
            fallback={},
        )
        if isinstance(workflow_payload, dict):
            previous_entry = workflow_payload.get(continuation_of, {})
            if isinstance(previous_entry, dict):
                previous_delegate_batches = len(
                    _coerce_delegate_batches(previous_entry.get("delegate_batches", []))
                )
                previous_phase_context_summaries = dict(
                    previous_entry.get("phase_context_summaries", {}) or {}
                )
                previous_phase_summaries = len(previous_phase_context_summaries)
                previous_phase_outputs = dict(previous_entry.get("phase_outputs", {}) or {})
                previous_project_context_summary = str(
                    previous_entry.get("project_context_summary", "") or ""
                )
                previous_chat_context_summary = str(
                    previous_entry.get("chat_context_summary", "") or ""
                )
        previous_specialist_reports = len(
            list(
                _load_chat_specialist_insights(runtime_dir, continuation_of).get(
                    "specialist_reports",
                    [],
                )
                or []
            )
        )
        curator_store = ContextCuratorStore(runtime_dir)
        chat_context = curator_store.load_chat_context(
            continuation_of,
            project_key=str(runtime_dir.parent.resolve()),
        )
        previous_invalidations = len(list(chat_context.get("invalidations", []) or []))
        previous_open_questions = len(list(chat_context.get("open_questions", []) or []))

    pressure = estimate_context_pressure(
        continuation_requested=continuation_requested,
        continuation_snapshot=continuation_snapshot,
        phase_summary_count=previous_phase_summaries,
        delegate_batch_count=previous_delegate_batches,
        specialist_report_count=previous_specialist_reports,
        invalidation_count=previous_invalidations,
        open_question_count=previous_open_questions,
    )
    compaction_value = estimate_context_compaction_value(
        phase_outputs=previous_phase_outputs,
        project_context_summary=previous_project_context_summary,
        chat_context_summary=previous_chat_context_summary,
        phase_context_summaries=previous_phase_context_summaries,
    )
    merged_signals = list(pressure.get("signals", []) or [])
    value_level = str(compaction_value.get("level", "") or "").strip().lower()
    if value_level in {"medium", "high"}:
        signal_name = f"context_compaction_value_{value_level}"
        if signal_name not in merged_signals:
            merged_signals.append(signal_name)
    merged = dict(pressure)
    merged["signals"] = merged_signals
    merged["recommend_context_curator"] = bool(
        pressure.get("recommend_context_curator", False)
        or compaction_value.get("priority_boost", False)
    )
    merged["context_compaction"] = dict(compaction_value)
    return merged


def _build_preplan_signal_block(surface_hints: dict[str, object]) -> str:
    surfaces = [
        str(item).strip().lower()
        for item in list(surface_hints.get("surfaces", []) or [])
        if str(item).strip()
    ]
    intents = [
        str(item).strip().lower()
        for item in list(surface_hints.get("recommended_delegate_intents", []) or [])
        if str(item).strip()
    ]
    specialists = [
        str(item).strip().lower()
        for item in list(surface_hints.get("recommended_specialists", []) or [])
        if str(item).strip()
    ]
    if not surfaces:
        return ""
    return (
        "\n\n[PREPLAN_SIGNALS]\n"
        f"surfaces={', '.join(surfaces)}\n"
        f"recommended_delegate_intents={', '.join(intents)}\n"
        f"recommended_specialists={', '.join(specialists)}\n"
        "Usa estas señales como pista previa al plan: si te ayudan, delega barato y "
        "evita cargar el contexto pesado en el Lead. No son obligatorias.\n"
        "[/PREPLAN_SIGNALS]"
    )


def _build_context_curator_prompt(
    *,
    message: str,
    surface_hints: dict[str, object],
    project_state_raw: str,
    session_history_raw: str,
) -> str:
    surfaces = ", ".join(
        [
            str(item).strip().lower()
            for item in list(surface_hints.get("surfaces", []) or [])
            if str(item).strip()
        ]
    ) or "general"
    return (
        "Compacta el contexto del proyecto para el Team Lead en maximo 8 lineas utiles.\n"
        f"Solicitud del usuario: {message[:180]}\n"
        f"Superficies detectadas: {surfaces}\n\n"
        "Prioriza solo lo relevante para decidir el plan inicial: hechos, archivos/areas "
        "probables, riesgos y senales utiles. Sin teoria y sin transcripts crudos.\n\n"
        "[PROJECT_STATE]\n"
        f"{project_state_raw}\n"
        "[/PROJECT_STATE]\n\n"
        "[SESSION_HISTORY]\n"
        f"{session_history_raw}\n"
        "[/SESSION_HISTORY]"
    )


def _context_project_key(workspace: Path) -> str:
    return str(workspace.resolve())


def _persist_preplan_context(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    user_message: str,
    surface_hints: dict[str, object],
    curator_summary: str,
    lead_summary: str,
    source_task_ids: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    store = ContextCuratorStore(runtime_dir)
    return store.remember_preplan(
        project_key=_context_project_key(workspace),
        chat_root=task_root,
        user_message=user_message,
        surface_hints=surface_hints,
        curator_summary=curator_summary,
        lead_summary=lead_summary,
        source_task_ids=source_task_ids,
    )


def _build_curated_context_block(
    *,
    runtime_dir: Path,
    workspace: Path,
    continuation_of: str = "",
) -> str:
    store = ContextCuratorStore(runtime_dir)
    parts: list[str] = []
    project_summary = store.build_summary(store.load_project_context(_context_project_key(workspace)))
    if project_summary:
        parts.append("Contexto curado del proyecto:")
        parts.extend(f"- {line}" for line in project_summary.splitlines())
    continuation_root = str(continuation_of or "").strip()
    if continuation_root:
        chat_summary = store.build_summary(
            store.load_chat_context(continuation_root, project_key=_context_project_key(workspace))
        )
        if chat_summary:
            parts.append(f"Contexto curado de {continuation_root}:")
            parts.extend(f"- {line}" for line in chat_summary.splitlines())
    return ("\n" + "\n".join(parts)) if parts else ""


def _record_context_invalidation(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    reason: str,
    affected_phases: list[str],
    source_task_ids: list[str],
) -> tuple[str, str]:
    store = ContextCuratorStore(runtime_dir)
    project_ctx, chat_ctx = store.remember_invalidation(
        project_key=_context_project_key(workspace),
        chat_root=task_root,
        reason=reason,
        affected_phases=affected_phases,
        source_task_ids=source_task_ids,
    )
    return store.build_summary(project_ctx), store.build_summary(chat_ctx)


def _synthesize_default_phase_evidence_plan(
    phases: list[PhaseSpec],
    *,
    message: str,
    run_mode: str,
) -> dict[str, dict[str, object]]:
    """Fallback conservador cuando el Lead no define EVIDENCE_PLAN.

    Prioriza evidencia barata y operativa para builds estandar sin cargar tokens
    del Team Lead con transcripts extensos de tools.
    """

    if run_mode != "standard":
        return {}

    browser_surface = _message_suggests_browser_surface(message)
    security_surface = _message_suggests_security_surface(message)
    research_surface = _message_suggests_research_surface(message)
    plan: dict[str, dict[str, object]] = {}
    for spec in phases:
        phase_id = str(spec.phase_id or "").strip()
        if not phase_id:
            continue
        intents: list[str] = []
        wait_policy = "best_effort"
        delegate_budget = 2

        if spec.role == "RESEARCHER" or phase_id in {"discovery", "research", "analysis", "investigate"}:
            intents.append("delegate_repo_scan")
            if research_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "ENGINEER" or phase_id == "build":
            intents.append("delegate_test_run")
            wait_policy = "quorum"
            delegate_budget = 4
            if browser_surface:
                intents.append("delegate_browser_repro")
            if security_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "REVIEWER" or phase_id == "review":
            intents.append("delegate_repo_scan")
            if security_surface or research_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "QA" or phase_id == "qa":
            intents.append("delegate_test_run")
            wait_policy = "quorum"
            delegate_budget = 3
            if browser_surface:
                intents.append("delegate_browser_repro")
            if security_surface:
                intents.append("delegate_mcp_probe")

        if intents:
            plan[phase_id] = {
                "delegate_intents": list(dict.fromkeys(intents)),
                "wait_policy": wait_policy,
                "delegate_budget": delegate_budget,
            }
    return plan


def _resolve_phase_evidence_plan(
    *,
    lead_output: str,
    phases: list[PhaseSpec],
    message: str,
    run_mode: str,
) -> tuple[dict[str, dict[str, object]], str]:
    explicit_plan = _coerce_phase_evidence_plan(_extract_evidence_plan(lead_output))
    if explicit_plan:
        return explicit_plan, "lead"
    return _synthesize_default_phase_evidence_plan(
        phases,
        message=message,
        run_mode=run_mode,
    ), "default"


def _sync_chat_runtime_state(
    orch,
    *,
    task_root: str,
    chat_run_state: ChatRunState,
    lead_run_mode: str,
    delegated_task_ids: list[str] | None = None,
    evidence_plan_source: str = "",
) -> None:
    ws = orch._get_workflow_state(task_root)
    ws["phase_evidence_plan"] = _coerce_phase_evidence_plan(
        chat_run_state.phase_evidence_plan
    )
    ws["lead_run_mode"] = str(lead_run_mode or "standard").strip() or "standard"
    ws["phase_task_ids"] = chat_run_state.phase_task_ids
    ws["workflow_phase_keys"] = chat_run_state.workflow_phase_keys
    if delegated_task_ids is not None:
        ws["delegated_task_ids"] = list(delegated_task_ids)
    ws.setdefault("delegate_batches", [])
    ws["delegate_economics_summary"] = _summarize_delegate_economics(
        _coerce_delegate_batches(ws.get("delegate_batches", []))
    )
    if evidence_plan_source:
        ws["evidence_plan_source"] = evidence_plan_source
    orch._save_workflow_state()


def _is_context_only_query(message: str) -> bool:
    """Detecta si el mensaje es una consulta de orientacion/contexto sin solicitud de desarrollo.

    Estos mensajes no deben penalizarse por falta de artefactos o ejecucion, ya que su
    objetivo es recuperar y sintetizar informacion, no producir codigo.
    """
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if not normalized or len(normalized) > 300:
        return False
    # Patrones de orientacion: preguntas sobre estado/contexto del proyecto
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
    import re as _re
    for pattern in orientation_patterns:
        if _re.search(pattern, normalized):
            return True
    # Consultas muy cortas que preguntan sobre el proyecto (heuristica de longitud)
    # Excluir mensajes que empiecen con verbos de accion imperativa
    _action_verbs = (
        "implementa", "añade", "agrega", "crea", "haz ", "modifica", "arregla",
        "reorganiza", "refactoriza", "migra", "actualiza", "genera", "construye",
        "diseña", "elimina", "borra", "configura", "despliega", "ejecuta", "fix",
        "add ", "create", "build", "run ", "deploy", "update", "remove", "delete",
    )
    if len(normalized) < 80 and any(
        kw in normalized
        for kw in ["proyecto", "project", "sabes", "sabe", "recuerdas", "recuerda"]
    ) and not any(normalized.startswith(v) for v in _action_verbs):
        return True
    return False



def _detect_run_type(
    message: str,
    phase_task_ids: dict[str, str],
    artifact_created: int,
    artifact_modified: int,
) -> str:
    """Clasifica el tipo de run para aplicar el threshold de scoring correcto.

    Returns:
        "context_recovery" — consulta de orientacion sin desarrollo
        "planning"         — solo fases de investigacion/diseno, sin engineer
        "build"            — tiene fase engineer y/o produce artefactos
        "mixed"            — combinacion de tipos
    """
    phase_names = set(phase_task_ids.keys()) - {"lead_intake", "lead_close"}
    has_build = any(
        k in phase_names for k in ("build", "implement", "develop", "code", "fix", "refactor")
    ) or any(
        # Detectar por presencia de fases de engineer aunque tengan otro nombre
        k.startswith("engineer") or k.startswith("eng_") for k in phase_names
    )
    has_artifacts = (artifact_created + artifact_modified) > 0

    if _is_context_only_query(message) and not has_build and not has_artifacts:
        return "context_recovery"

    if has_build or has_artifacts:
        return "build"

    # Solo fases de researcher/reviewer/qa sin engineer
    researcher_phases = {"discovery", "research", "plan_research", "analysis", "investigate"}
    review_phases = {"review", "plan_risks", "audit", "security"}
    qa_phases = {"qa", "test", "verify", "acceptance"}
    non_build = researcher_phases | review_phases | qa_phases
    if phase_names and phase_names.issubset(non_build):
        return "planning"

    return "mixed"


def _workspace_artifact_snapshot(workspace: Path) -> dict[str, tuple[int, int]]:
    skip_dirs = {
        "runtime",
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
    }
    snapshot: dict[str, tuple[int, int]] = {}
    if not workspace.exists():
        return snapshot

    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in skip_dirs for part in relative.parts):
            continue
        key = relative.as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[key] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def _workspace_artifact_diff(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    created = sorted(path for path in after.keys() if path not in before)
    modified = sorted(
        path for path in after.keys() if path in before and after[path] != before[path]
    )
    return created, modified


def _read_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_json_dict(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _materialize_game_iteration(workspace: Path, message: str) -> dict[str, object]:
    progress_path = workspace / ".aiteam_game_progress.json"
    is_initial_bootstrap = not progress_path.exists()
    should_apply = is_initial_bootstrap and (
        _is_game_request(message) or _is_game_followup_request(workspace, message)
    )
    if not should_apply:
        return {
            "applied": False,
            "iteration": 0,
            "files": [],
            "reason": "bootstrap_already_done"
            if not is_initial_bootstrap
            else "not_game_request",
        }

    iteration = 1

    index_html = workspace / "index.html"
    styles_css = workspace / "styles.css"
    game_js = workspace / "game.js"
    readme_md = workspace / "README.md"

    html_content = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Juego Test</title>
    <link rel=\"stylesheet\" href=\"styles.css\" />
  </head>
  <body>
    <main class=\"app\">
      <h1>Juego Test</h1>
      <p class=\"hint\">Move with arrow keys or WASD. Collect stars and avoid hazards.</p>
      <canvas id=\"game\" width=\"640\" height=\"400\"></canvas>
      <div class=\"hud\">
        <span id=\"score\">Score: 0</span>
        <span id=\"status\">Status: ready</span>
      </div>
    </main>
    <script src=\"game.js\"></script>
  </body>
</html>
"""

    css_content = """* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: \"Segoe UI\", Tahoma, sans-serif;
  background: radial-gradient(circle at 20% 10%, #1d2a3a, #0a1018 60%);
  color: #f3f7fb;
}
.app { width: min(94vw, 760px); text-align: center; }
h1 { margin: 0 0 8px; }
.hint { margin: 0 0 12px; color: #b9c6d6; font-size: 14px; }
canvas {
  width: 100%;
  border: 1px solid #2e435a;
  border-radius: 10px;
  background: linear-gradient(180deg, #102033, #0e1827);
}
.hud {
  margin-top: 10px;
  display: flex;
  justify-content: space-between;
  color: #d2dceb;
  font-size: 14px;
}
"""

    game_v1 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 1,
  running: true,
  player: { x: 320, y: 200, size: 16, speed: 3 },
  star: { x: 120, y: 100, size: 10 },
  keys: new Set(),
};

function randomPoint(padding = 20) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => {
  state.keys.add(event.key.toLowerCase());
});

window.addEventListener('keyup', (event) => {
  state.keys.delete(event.key.toLowerCase());
});

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    resetStar();
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  statusLabel.textContent = `Status: running · level ${state.level}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    game_v2 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 2,
  running: true,
  timeLeft: 60,
  player: { x: 320, y: 200, size: 16, speed: 3.2 },
  star: { x: 120, y: 100, size: 10 },
  hazard: { x: 200, y: 180, size: 12, vx: 2.1, vy: 1.7 },
  keys: new Set(),
};

function randomPoint(padding = 20) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => state.keys.add(event.key.toLowerCase()));
window.addEventListener('keyup', (event) => state.keys.delete(event.key.toLowerCase()));

setInterval(() => {
  if (!state.running) return;
  state.timeLeft -= 1;
  if (state.timeLeft <= 0) {
    state.running = false;
    statusLabel.textContent = `Status: finished · final score ${state.score}`;
  }
}, 1000);

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  state.hazard.x += state.hazard.vx;
  state.hazard.y += state.hazard.vy;
  if (state.hazard.x < 10 || state.hazard.x > canvas.width - 10) state.hazard.vx *= -1;
  if (state.hazard.y < 10 || state.hazard.y > canvas.height - 10) state.hazard.vy *= -1;

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    resetStar();
  }

  if (intersects(p, state.hazard, 14)) {
    state.score = Math.max(0, state.score - 15);
    scoreLabel.textContent = `Score: ${state.score}`;
    const next = randomPoint();
    state.hazard.x = next.x;
    state.hazard.y = next.y;
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  drawRect(state.hazard.x, state.hazard.y, state.hazard.size, '#fb7185');
  statusLabel.textContent = state.running
    ? `Status: running · level ${state.level} · ${state.timeLeft}s`
    : `Status: finished · final score ${state.score}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    game_v3 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 3,
  running: true,
  wave: 1,
  player: { x: 320, y: 200, size: 16, speed: 3.4 },
  star: { x: 120, y: 100, size: 10 },
  hazards: [
    { x: 180, y: 140, size: 11, vx: 1.8, vy: 1.2 },
    { x: 460, y: 240, size: 11, vx: -1.6, vy: 1.5 },
  ],
  keys: new Set(),
};

function randomPoint(padding = 24) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => state.keys.add(event.key.toLowerCase()));
window.addEventListener('keyup', (event) => state.keys.delete(event.key.toLowerCase()));

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  for (const hazard of state.hazards) {
    hazard.x += hazard.vx;
    hazard.y += hazard.vy;
    if (hazard.x < 10 || hazard.x > canvas.width - 10) hazard.vx *= -1;
    if (hazard.y < 10 || hazard.y > canvas.height - 10) hazard.vy *= -1;
    if (intersects(p, hazard, 14)) {
      state.running = false;
    }
  }

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    if (state.score % 50 === 0) {
      state.wave += 1;
      state.hazards.push({
        x: randomPoint().x,
        y: randomPoint().y,
        size: 10 + state.wave,
        vx: 1 + Math.random() * 2,
        vy: 1 + Math.random() * 2,
      });
    }
    resetStar();
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  for (const hazard of state.hazards) {
    drawRect(hazard.x, hazard.y, hazard.size, '#fb7185');
  }
  statusLabel.textContent = state.running
    ? `Status: running · level ${state.level} · wave ${state.wave}`
    : `Status: game over · score ${state.score}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    readme = f"""# Juego Test

Generated by AI Team artifact-first bootstrap.

## Run

Open `index.html` in your browser.

## Iteration

Current automatic game iteration: {iteration}

## Controls

- Arrow keys / WASD to move.
- Collect yellow stars.
- Avoid hazards.
"""

    if iteration <= 1:
        game_content = game_v1
    elif iteration == 2:
        game_content = game_v2
    else:
        game_content = game_v3

    index_html.write_text(html_content, encoding="utf-8")
    styles_css.write_text(css_content, encoding="utf-8")
    game_js.write_text(game_content, encoding="utf-8")
    readme_md.write_text(readme, encoding="utf-8")

    progress_payload = {
        "iteration": iteration,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "artifact_first_game_bootstrap",
        "last_message": str(message or "")[:300],
    }
    _write_json_dict(progress_path, progress_payload)

    return {
        "applied": True,
        "iteration": iteration,
        "files": [
            "index.html",
            "styles.css",
            "game.js",
            "README.md",
            ".aiteam_game_progress.json",
        ],
    }


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

    workflow_state_payload = _read_json_payload(
        runtime_dir / "workflow_state.json",
        fallback={},
    )
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
    specialist_insights = _load_chat_specialist_insights(runtime_dir, normalized_root)
    specialist_reports = list(specialist_insights.get("specialist_reports", []) or [])
    specialist_report_summary = dict(
        specialist_insights.get("specialist_report_summary", {}) or {}
    )

    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
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
    _active_states = {"pending", "ready", "claimed", "blocked", "waiting_user"}
    pending_tasks = sum(
        1 for state in phase_states.values() if state in _active_states
    )
    lead_state = phase_states.get("lead_close", "")

    # E7-D4: Detectar si el run está pausado esperando respuesta del usuario
    _waiting_user_progress = False
    _waiting_question_progress = ""
    _pending_clarify = runtime_dir / f"pending_clarification_{normalized_root}.json"
    if _pending_clarify.exists():
        try:
            _pcs = json.loads(_pending_clarify.read_text(encoding="utf-8"))
            if _pcs.get("type") in ("mid_run", "lead_intake"):
                _waiting_user_progress = True
                _waiting_question_progress = str(_pcs.get("question", ""))
        except Exception:
            pass
    if not _waiting_user_progress:
        _waiting_user_progress = any(
            s == "waiting_user" for s in phase_states.values()
        )

    if not exists:
        return TeamChatProgressResponse(
            task_id=normalized_root,
            exists=False,
            state="queued",
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
        )

    if evidence_gate_rejected:
        progress_state = "rejected"
    elif failed_tasks > 0 or lead_state == "failed":
        progress_state = "failed"
    elif _waiting_user_progress:
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

    # dynamic_phases_ready: True cuando el plan ya fue generado y las tareas
    # dinamicas estan en el taskboard (mas alla de lead_intake/lead_close).
    _progress_phase_task_ids = {
        name: f"{normalized_root}::{name}" for name in phase_states
    }
    _dynamic_phases_ready = any(
        name not in ("lead_intake", "lead_close") for name in phase_states
    )

    return TeamChatProgressResponse(
        task_id=normalized_root,
        exists=True,
        state=progress_state,
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
        dynamic_phases_ready=_dynamic_phases_ready,
        phase_task_ids=_progress_phase_task_ids,
        phase_evidence_plan=phase_evidence_plan,
        delegate_batches=delegate_batches,
        delegate_economics=delegate_economics,
        specialist_reports=specialist_reports,
        specialist_report_summary=specialist_report_summary,
        waiting_user=_waiting_user_progress,
        clarification_question=_waiting_question_progress,
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


def _evaluate_chat_quality(
    *,
    decision_text: str,
    justification_text: str,
    completed_tasks: int,
    total_tasks: int,
    pending_tasks: int,
    failed_tasks: int,
    execution_attempts: int,
    execution_success: int,
    execution_steps: int,
    successful_checks: list[str],
    artifact_created: int,
    artifact_modified: int,
) -> tuple[int, int, str, str]:
    total = max(1, total_tasks)
    completion_ratio = completed_tasks / total
    artifact_total = max(0, artifact_created) + max(0, artifact_modified)

    reasoning_score = 0
    decision_len = len(str(decision_text or "").strip())
    justification_len = len(str(justification_text or "").strip())
    if decision_len >= 160:
        reasoning_score += 30
    elif decision_len >= 80:
        reasoning_score += 20
    elif decision_len >= 30:
        reasoning_score += 12

    if justification_len >= 180:
        reasoning_score += 25
    elif justification_len >= 90:
        reasoning_score += 16
    elif justification_len >= 35:
        reasoning_score += 10

    if completion_ratio >= 0.75:
        reasoning_score += 20
    elif completion_ratio >= 0.4:
        reasoning_score += 12
    elif completed_tasks > 0:
        reasoning_score += 8

    if failed_tasks == 0:
        reasoning_score += 10
    if pending_tasks <= max(1, total // 3):
        reasoning_score += 15

    productivity_score = 0
    if execution_attempts > 0:
        productivity_score += 8
        if execution_attempts >= max(2, total // 2):
            productivity_score += 4
        success_ratio = execution_success / max(1, execution_attempts)
        productivity_score += int(success_ratio * 8)

    if execution_steps > 0:
        productivity_score += 30
        if execution_steps >= 3:
            productivity_score += 15

    checks_count = len(successful_checks)
    if checks_count > 0:
        productivity_score += 6
        if checks_count >= 2:
            productivity_score += 6
        if checks_count >= 3:
            productivity_score += 4

    if artifact_total > 0:
        productivity_score += 35
        if artifact_total >= 3:
            productivity_score += 10

    if completion_ratio >= 0.75:
        productivity_score += 6
    elif completion_ratio >= 0.4:
        productivity_score += 4

    if failed_tasks == 0:
        productivity_score += 4

    reasoning_score = max(0, min(100, reasoning_score))
    productivity_score = max(0, min(100, productivity_score))

    if productivity_score >= 75:
        productivity_status = "strong"
    elif productivity_score >= 45:
        productivity_status = "moderate"
    else:
        productivity_status = "weak"

    if execution_attempts == 0:
        hint = "No hubo ejecucion de tareas; fuerza un slice implementable y vuelve a correr."
    elif execution_steps == 0:
        hint = "Hubo routing, pero sin pasos de ejecucion; agrega comandos/pruebas minimas en build."
    elif artifact_total == 0:
        hint = "No se detectaron artefactos nuevos o modificados; prioriza cambios concretos en archivos."
    elif failed_tasks > 0:
        hint = "Resuelve fases fallidas antes de ampliar alcance."
    else:
        hint = (
            "Buen avance; toma el siguiente slice de impacto con pruebas de regresion."
        )

    return productivity_score, reasoning_score, productivity_status, hint


def _classify_check_from_command(command: str) -> str:
    text = str(command or "").strip().lower()
    if not text:
        return ""
    test_tokens = [
        "pytest",
        "npm test",
        "pnpm test",
        "bun test",
        "vitest",
        "jest",
        "go test",
        "cargo test",
    ]
    lint_tokens = [
        "eslint",
        "ruff",
        "flake8",
        "pylint",
        "npm run lint",
        "pnpm lint",
        "bun lint",
    ]
    build_tokens = [
        "npm run build",
        "pnpm build",
        "bun run build",
        "vite build",
        "tsc -b",
        "cargo build",
        "go build",
    ]
    if any(token in text for token in test_tokens):
        return "test"
    if any(token in text for token in lint_tokens):
        return "lint"
    if any(token in text for token in build_tokens):
        return "build"
    return ""


def _is_placeholder_output_text(value: str) -> bool:
    """
    Detecta si un texto es output de placeholder/mock generado por el sistema.
    Solo hace matching en el INICIO del string para evitar falsos positivos
    en texto real del LLM que pueda contener esas palabras.
    """
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    # Formato legacy: "[provider:model:channel] Processed prompt ..."
    if re.match(r"^\[[a-z0-9_\-]+:[a-z0-9_.\-]+:(subscription|api)\]", lower):
        return True
    # Formato actual: "[SIMULADO | ...]" o "[DEMO]" al inicio
    if re.match(r"^\[simulado\s*\|", lower):
        return True
    if lower.startswith("[demo]"):
        return True
    # Marcador explícito de respuesta mock al inicio
    if lower.startswith("respuesta mock"):
        return True
    return False


def _assess_execution_mode(
    *,
    task_rows: list[WorkTask],
    execution_steps: int,
    artifact_created: int,
    artifact_modified: int,
) -> tuple[str, int, float, int]:
    result_texts: list[str] = []
    for task in task_rows:
        result = str(
            task.metadata.get("result") or task.metadata.get("error") or ""
        ).strip()
        if result:
            result_texts.append(result)

    if not result_texts:
        mode = (
            "live"
            if (execution_steps > 0 or (artifact_created + artifact_modified) > 0)
            else "simulated"
        )
        return mode, 0, 0.0, 0

    placeholder_count = sum(
        1 for row in result_texts if _is_placeholder_output_text(row)
    )
    placeholder_ratio = float(placeholder_count) / float(len(result_texts))
    has_execution_evidence = (
        execution_steps > 0 or (artifact_created + artifact_modified) > 0
    )

    if not has_execution_evidence:
        return "simulated", placeholder_count, placeholder_ratio, len(result_texts)

    if placeholder_count == len(result_texts) and execution_steps == 0:
        return "simulated", placeholder_count, placeholder_ratio, len(result_texts)
    if placeholder_count > 0:
        return "hybrid", placeholder_count, placeholder_ratio, len(result_texts)
    return "live", placeholder_count, placeholder_ratio, len(result_texts)


def _evaluate_phase_evidence_gate(
    *,
    task_rows_by_phase: dict[str, WorkTask],
    execution_steps: int,
    execution_steps_success: int,
    successful_checks: list[str],
    artifact_created: int,
    artifact_modified: int,
    require_followup_artifact_delta: bool,
    require_test_or_build_check: bool,
) -> list[str]:
    failures: list[str] = []
    target_phases = ["build", "review", "qa"]
    for phase in target_phases:
        task = task_rows_by_phase.get(phase)
        if task is None:
            failures.append(f"{phase}:missing_task")
            continue
        if task.state.value != "completed":
            failures.append(f"{phase}:not_completed")
            continue
        result_text = str(
            task.metadata.get("result") or task.metadata.get("error") or ""
        ).strip()
        if not result_text:
            failures.append(f"{phase}:empty_result")
            continue
        if _is_placeholder_output_text(result_text):
            failures.append(f"{phase}:placeholder_output")

        if phase == "build" and bool(
            task.metadata.get("require_execution_plan", False)
        ):
            raw_plan = task.metadata.get("execution_plan", [])
            if not isinstance(raw_plan, list) or not raw_plan:
                failures.append("build:missing_execution_plan")

    build_has_output = all(not row.startswith("build:") for row in failures)
    if (
        build_has_output
        and execution_steps <= 0
        and (artifact_created + artifact_modified) <= 0
    ):
        failures.append("build:no_execution_evidence")
    if execution_steps_success <= 0:
        failures.append("build:no_successful_execution_steps")
    if execution_steps_success > 0 and not successful_checks:
        failures.append("build:no_successful_post_build_checks")
    if require_test_or_build_check and execution_steps_success > 0:
        if not any(check in {"test", "build"} for check in successful_checks):
            failures.append("build:missing_test_or_build_check")
    if require_followup_artifact_delta and (artifact_created + artifact_modified) <= 0:
        failures.append("build:no_followup_artifact_delta")
    return failures


def _compose_user_facing_run_summary(
    *,
    task_root: str,
    request_line: str,
    continuation_line: str,
    mode: str,
    rounds_used: int,
    round_budget: int,
    elapsed_ms: int,
    done_line: str,
    pending_line: str,
    failed_line: str,
    participants_line: str,
    decision_compact: str,
    artifact_created: int,
    artifact_modified: int,
    artifact_files: list[str],
    productivity_score: int,
    reasoning_score: int,
    productivity_status: str,
    next_action_hint: str,
    execution_mode: str,
    placeholder_outputs: int,
) -> str:
    execution_label = (
        "demo"
        if _env_bool("AITEAM_CHAT_DEMO_FAST", default=False)
        and execution_mode == "simulated"
        else execution_mode
    )
    placeholder_label = (
        "salidas demo"
        if _env_bool("AITEAM_CHAT_DEMO_FAST", default=False)
        else "salidas placeholder"
    )
    # Usar output real del LLM; solo caer a fallback si genuinamente no hay nada
    decision_text = _presentable_decision_text(str(decision_compact or "").strip())
    if not decision_text:
        if execution_mode == "simulated":
            decision_text = (
                "Sin output del Team Lead; modo simulado (sin pasos de ejecucion verificables)."
            )
        elif execution_mode == "hybrid" and placeholder_outputs > 0:
            decision_text = (
                "Coordinacion parcial completada; parte del output fue placeholder."
            )
        else:
            decision_text = "Sin sintesis del Team Lead en esta ronda."

    meta_parts = [f"fases: hecho={done_line} | pendiente={pending_line} | fallido={failed_line}"]
    if artifact_files:
        files_line = ", ".join(artifact_files[:8])
        meta_parts.append(f"archivos({artifact_created}+{artifact_modified}): {files_line}")
    if execution_mode != "live":
        meta_parts.append(f"modo={execution_label}")
    meta_parts.append(f"calidad={productivity_score}/100 | {elapsed_ms}ms | {task_root}")

    lines: list[str] = [
        "Resumen del Team Lead para ti:",
        decision_text,
        "",
        f"Continuity: {continuation_line}",
        "",
        "---",
        " | ".join(meta_parts),
    ]
    return "\n".join(lines)


def _presentable_decision_text(value: str) -> str:
    """Retorna el texto si es output real del LLM, vacío si es placeholder."""
    decision_text = str(value or "").strip()
    if not decision_text or _is_placeholder_output_text(decision_text):
        return ""
    return decision_text


def _compact_text_line(value: str, limit: int = 320) -> str:
    flat = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 3)] + "..."


def _is_placeholder_like_text(value: str) -> bool:
    """Alias de _is_placeholder_output_text para compatibilidad."""
    return _is_placeholder_output_text(value)


def _compact_delegated_result(value: str, *, state: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "sin resultado"
    if _is_placeholder_output_text(text):
        lower = text.lower()
        if lower.startswith("[demo]"):
            return "demo"
        if re.match(r"^\[simulado\s*\|", lower):
            return "placeholder/simulado"
        return "placeholder" if state == "completed" else f"placeholder/{state}"
    return _compact_text_line(_presentable_decision_text(text) or text, 220)


def _trim_at_boundary(text: str, limit: int) -> str:
    """Trunca al límite intentando respetar párrafos, luego oraciones, luego palabras."""
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    for sep in ("\n\n", ". ", "\n", " "):
        idx = chunk.rfind(sep)
        if idx > limit * 0.80:
            return chunk[:idx + len(sep)].rstrip()
    return chunk.rstrip()


def _limit_chat_response(text: str, *, limit: int = 12000) -> str:
    content = str(text or "")
    if len(content) <= limit:
        return content

    # Si el contenido tiene el marcador legacy de sección, preservar el cuerpo principal
    marker = "\nLead message for user:\n"
    if marker in content:
        prefix, suffix = content.split(marker, 1)
        suffix_budget = max(3500, int(limit * 0.60))
        prefix_budget = max(800, limit - suffix_budget - len(marker) - 20)
        compact_prefix = _trim_at_boundary(prefix, prefix_budget)
        compact_suffix = _trim_at_boundary(suffix, suffix_budget)
        content = compact_prefix + marker + compact_suffix
        if len(content) <= limit:
            return content

    return _trim_at_boundary(content, limit - 20) + "\n[... truncado]"


def _stream_display_chunk(task_id: str, chunk: str) -> str:
    text = str(chunk or "").strip()
    if not text:
        return ""
    if not _env_bool("AITEAM_CHAT_DEMO_FAST", default=False):
        return text
    if _is_placeholder_like_text(text):
        phase = str(task_id or "").split("::")[-1].strip().lower()
        phase_label_map = {
            "lead_intake": "Analizando solicitud",
            "plan_research": "Investigando contexto",
            "plan_engineering": "Definiendo implementacion",
            "plan_risks": "Evaluando riesgos",
            "build": "Preparando entrega",
            "review": "Revisando resultado",
            "qa": "Validando salida",
            "lead_close": "Cerrando sintesis",
        }
        phase_label = phase_label_map.get(phase, "Coordinando equipo")
        return f"{phase_label}...\n"
    return text


def _resolve_chat_decision_text(
    *,
    lead_response: str,
    intake_response: str,
    phase_states: dict[str, str],
    workflow_phase_keys: list[str],
    phase_results: dict[str, str],
) -> str:
    lead_text = str(lead_response or "").strip()
    if lead_text:
        return lead_text

    lead_close_state = str(phase_states.get("lead_close", "") or "").strip().lower()
    intake_text = str(intake_response or "").strip()
    if lead_close_state == "completed" and intake_text:
        return intake_text

    done_phases = [
        phase for phase in workflow_phase_keys if phase_states.get(phase) == "completed"
    ]
    blocked_phases = [
        phase for phase in workflow_phase_keys if phase_states.get(phase) == "blocked"
    ]
    failed_phases = [
        phase for phase in workflow_phase_keys if phase_states.get(phase) == "failed"
    ]
    pending_phases = [
        phase
        for phase in workflow_phase_keys
        if phase_states.get(phase) in {"pending", "ready", "claimed"}
    ]

    fragments: list[str] = []
    if done_phases:
        fragments.append(f"completado={', '.join(done_phases)}")

    if failed_phases:
        failed_with_context: list[str] = []
        for phase in failed_phases[:4]:
            detail = re.sub(
                r"\s+", " ", str(phase_results.get(phase, "") or "")
            ).strip()
            if detail:
                failed_with_context.append(f"{phase} ({detail[:120]})")
            else:
                failed_with_context.append(phase)
        fragments.append(f"fallido={', '.join(failed_with_context)}")

    if blocked_phases:
        fragments.append(f"bloqueado={', '.join(blocked_phases)}")

    if pending_phases:
        fragments.append(f"pendiente={', '.join(pending_phases)}")

    if lead_close_state and lead_close_state != "completed":
        fragments.append(f"lead_close={lead_close_state}")
    elif not lead_close_state:
        fragments.append("lead_close=missing")

    if not fragments:
        return "Corrida sin cierre final; aun no hay sintesis definitiva del Team Lead."

    return (
        "Corrida sin cierre final. "
        + "; ".join(fragment.rstrip(".") for fragment in fragments)
        + "."
    )


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
    return all(str(phase_states.get(phase, "") or "").strip().lower() in allowed_states for phase in dynamic_phases)


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
        request = _extract_delegate_request(output)
        if request is not None:
            return phase_name, request
    return None


def _phase_started_for_replan(task: WorkTask | None) -> bool:
    """Determina si una fase ya empezo y no debe ser reemplazada por REPLAN parcial."""

    if task is None:
        return False
    state = str(task.state.value if hasattr(task.state, "value") else task.state).strip().lower()
    if state in {"claimed", "completed", "failed", "waiting_user"}:
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
    """Elimina fases no iniciadas por instruccion mid-run del Lead.

    Devuelve: `new_phases, removed_phase_ids, preserved_started_phase_ids, skipped_started_targets`.
    """

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


class NotebookLMSyncRequest(BaseModel):
    title: str = "AI Team Sync"
    source: str = "api"
    content: str = ""
    export_format: str = "markdown"
    days: int = 7
    dry_run: bool = False
    notebook_id: str = ""


from api.utils import (
    _truncate_text,
    _read_json_payload,
    _read_jsonl_records,
    _load_chat_specialist_insights,
    _event_summary,
    _auth_expected_key,
    _extract_auth_token,
    _is_authorized,
    _require_api_auth_request,
    _normalize_workspace_path,
    _workspace_from_header_map,
    _workspace_from_request,
    _safe_workspace_target,
    _extract_user_message_from_task_description,
    _group_chat_roots,
    _build_project_continuity_context,
    _build_scout_project_state_context,
    _build_scout_session_history_context,
    _chat_round_budget,
    _sanitize_project_name,
    _allocate_project_path,
    _detect_notebooklm_status,
    PROJECT_ROOT,
    get_current_workspace,
    set_current_workspace,
)

from api.routers import workspace as workspace_router
from api.routers import aiteam as aiteam_router

app.include_router(workspace_router.router)
app.include_router(aiteam_router.router)


@app.post("/api/notebooklm/sync")
async def post_notebooklm_sync(payload: NotebookLMSyncRequest, request: Request):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(
            request, get_current_workspace(), PROJECT_ROOT
        )
        runtime_dir = workspace / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        def _sync():
            return cmd_notebooklm_sync(
                runtime_dir=runtime_dir,
                notebook_id=payload.notebook_id,
                title=payload.title,
                source=payload.source,
                content_file="",
                from_prompt=payload.content,
                export_format=payload.export_format,
                days=max(1, int(payload.days)),
                dry_run=bool(payload.dry_run),
                quiet=True,
            )

        return await asyncio.to_thread(_sync)
    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("Unhandled error in notebooklm sync")
        return {"error": str(e)}


@app.post("/api/aiteam/chat")
async def post_aiteam_chat(payload: TeamChatRequest, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    role_map = {
        "team_lead": Role.TEAM_LEAD,
        "lead": Role.TEAM_LEAD,
        "researcher": Role.RESEARCHER,
        "engineer": Role.ENGINEER,
        "reviewer": Role.REVIEWER,
        "qa": Role.QA,
    }
    complexity_map = {
        "low": Complexity.LOW,
        "medium": Complexity.MEDIUM,
        "high": Complexity.HIGH,
    }
    criticality_map = {
        "low": Criticality.LOW,
        "medium": Criticality.MEDIUM,
        "high": Criticality.HIGH,
    }

    preferred_role = role_map.get(payload.role.strip().lower(), Role.ENGINEER)
    complexity = complexity_map.get(
        payload.complexity.strip().lower(), Complexity.MEDIUM
    )
    criticality = criticality_map.get(
        payload.criticality.strip().lower(), Criticality.MEDIUM
    )

    def _task_result(task: WorkTask | None) -> str:
        if task is None:
            return ""
        return str(task.metadata.get("result") or task.metadata.get("error") or "")

    import queue as _queue_mod

    _token_queue: _queue_mod.Queue = _queue_mod.Queue()

    def _run_chat() -> TeamChatResponse:
        orch = build_default_orchestrator(
            runtime_dir=runtime_dir,
            browser_mode="basic",
            environment="dev",
        )

        def _on_chunk(task_id: str, chunk: str) -> None:
            display_chunk = _stream_display_chunk(task_id, chunk)
            if display_chunk:
                _token_queue.put(
                    ("token_chunk", {"task_id": task_id, "chunk": display_chunk})
                )

        orch.token_chunk_callback = _on_chunk

        def _on_agent_event(event: dict) -> None:
            _token_queue.put(("agent_event", event))

        orch.agent_event_callback = _on_agent_event
        previous_runs = _recent_chat_roots(runtime_dir, max_chats=3)
        previous_root = previous_runs[0] if previous_runs else {}
        previous_by_root: dict[str, dict[str, object]] = {
            str(item.get("root_id", "")).upper(): item
            for item in previous_runs
            if isinstance(item, dict)
            and str(item.get("root_id", "")).upper().startswith("CHAT-")
        }
        continuation_requested = _is_continuation_message(payload.message)
        continuation_target = _extract_chat_root_from_message(payload.message)
        continuation_of = ""
        continuation_snapshot = ""
        continuation_source: dict[str, object] = {}
        preplan_surface_hints = _detect_preplan_surface_hints(payload.message)
        preplan_signal_block = _build_preplan_signal_block(preplan_surface_hints)
        if continuation_requested:
            if continuation_target and continuation_target in previous_by_root:
                continuation_source = previous_by_root.get(continuation_target, {})
            elif previous_root:
                continuation_source = previous_root

        if continuation_requested and continuation_source:
            continuation_of = str(continuation_source.get("root_id", "") or "")
            previous_states = continuation_source.get("phase_states", {})
            unresolved: list[str] = []
            if isinstance(previous_states, dict):
                for phase_name, state in previous_states.items():
                    state_value = str(state or "")
                    if state_value != "completed":
                        unresolved.append(f"{phase_name}:{state_value}")
            continuation_snapshot = (
                ", ".join(unresolved[:8]) if unresolved else "all_completed"
            )
        elif continuation_requested and continuation_target:
            continuation_of = continuation_target
            continuation_snapshot = "target_not_found"

        task_root = _resolve_task_root(payload.client_task_id)
        chat_mode = _normalize_chat_mode(payload.mode)
        round_budget = _resolve_chat_round_budget(
            requested_rounds=payload.max_rounds,
            chat_mode=chat_mode,
            complexity=complexity,
            criticality=criticality,
        )
        preplan_context_pressure = _estimate_preplan_context_pressure(
            runtime_dir=runtime_dir,
            continuation_requested=continuation_requested,
            continuation_of=continuation_of,
            continuation_snapshot=continuation_snapshot,
        )
        require_build_execution_plan = not bool(continuation_requested)

        # ── Constantes de capabilities por rol ─────────────────────────────
        _ROLE_CAPABILITIES = {
            "RESEARCHER": ["analysis"],
            "ENGINEER": ["coding"],
            "REVIEWER": ["review"],
            "QA": ["analysis"],
        }

        # ── Instruccion de WORKFLOW_PLAN para el prompt del Lead ────────────
        _WORKFLOW_PLAN_INSTRUCTION = (
            "\n\nTRAS TU ANALISIS, incluye un bloque [WORKFLOW_PLAN] con las fases"
            " especificas que este pedido necesita. NO incluyas lead_intake ni"
            " lead_close (se agregan automaticamente). Usa solo:"
            " RESEARCHER, ENGINEER, REVIEWER, QA. Maximo 8 fases.\n"
            "[WORKFLOW_PLAN]\n"
            "- phase_id: <nombre_corto>\n"
            "  role: <RESEARCHER|ENGINEER|REVIEWER|QA>\n"
            "  objective: <objetivo concreto en una linea>\n"
            "  depends_on: [<phase_ids separados por coma, o vacio>]\n"
            "[/WORKFLOW_PLAN]"
        )

        lead_task_id = f"{task_root}::lead_intake"
        continuity_context = _build_project_continuity_context(runtime_dir)
        continuity_block = f"\n\n{continuity_context}\n" if continuity_context else ""
        curated_context_block = _build_curated_context_block(
            runtime_dir=runtime_dir,
            workspace=workspace,
            continuation_of=continuation_of,
        )

        orch.mailbox.send(
            sender="user",
            recipient="team_lead",
            subject=f"User input: {task_root}",
            body=payload.message,
            task_id=task_root,
        )
        orch.event_logger.emit(
            "user_input",
            {
                "task_id": task_root,
                "role": payload.role,
                "complexity": payload.complexity,
                "criticality": payload.criticality,
                "message": payload.message,
                "continuation_requested": continuation_requested,
                "continuation_of": continuation_of,
            },
        )
        orch.memory.remember(
            agent_id="lead-1",
            role=Role.TEAM_LEAD.value,
            kind="user_input",
            content=payload.message,
            task_id=task_root,
            tags=["chat", "user_input"],
        )

        # ── Descripcion del lead_intake segun modo ──────────────────────────
        if chat_mode == "classic":
            lead_intake_description = (
                "Eres Team Lead senior. Escucha al usuario, define alcance y estrategia de ejecucion.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Entrega: objetivos, supuestos, riesgos y orden de trabajo del equipo."
                f"{preplan_signal_block}"
                f"{curated_context_block}"
                f"{_WORKFLOW_PLAN_INSTRUCTION}"
                f"{continuity_block}"
            )
        else:
            lead_intake_description = (
                "Eres Team Lead senior. Convierte el input en plan de ejecucion de ventana corta.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Entrega en <=12 lineas: objetivo, backlog priorizado (P0/P1), riesgos y"
                " que se intentara completar en esta corrida."
                f"{preplan_signal_block}"
                f"{curated_context_block}"
                f"{_WORKFLOW_PLAN_INSTRUCTION}"
                f"{continuity_block}"
            )

        # ── PASO 0: Pre-flight scouts (modelos baratos en paralelo) ─────────
        # Los scouts pre-fetchen datos del proyecto sin LLM, luego los resumen
        # con un modelo barato. El lead_intake recibe briefings compactos, no raw context.
        scout_state_id = f"{task_root}::scout_project_state"
        scout_history_id = f"{task_root}::scout_session_history"
        scout_curator_id = f"{task_root}::scout_context_curator"

        _scout_state_raw = _build_scout_project_state_context(workspace)
        _scout_history_raw = _build_scout_session_history_context(runtime_dir)

        _scout_state_task = WorkTask(
            task_id=scout_state_id,
            title="Scout: estado del proyecto",
            description=(
                "Resume el siguiente contexto en maximo 6 lineas de hechos concretos "
                f"relevantes para la solicitud: '{payload.message[:120]}'\n\n"
                f"{_scout_state_raw}\n\n"
                "Formato: hechos breves. Sin teoria, sin recomendaciones."
            ),
            role=Role.SCOUT,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
            metadata={
                **build_tool_specialist_metadata(
                    specialist="repo_scout",
                    required_capabilities=["repo_read"],
                    reason="briefing barato del estado del proyecto para el Team Lead",
                ),
                "is_scout": True,
                "scout_type": "project_state",
                "skip_quality_gates": True,
                "phase": "scout_project_state",
                "chat_parent": task_root,
            },
        )
        _scout_history_task = WorkTask(
            task_id=scout_history_id,
            title="Scout: historial de sesiones",
            description=(
                "Extrae los 3 hechos mas relevantes del historial para la "
                f"solicitud: '{payload.message[:120]}'\n\n"
                f"{_scout_history_raw}\n\n"
                "Formato: 3 hechos concretos en maximo 6 lineas. "
                "Incluye nombre del proyecto, decisiones tecnicas clave, estado de fases."
            ),
            role=Role.SCOUT,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
            metadata={
                **build_tool_specialist_metadata(
                    specialist="repo_scout",
                    required_capabilities=["repo_read"],
                    reason="briefing barato del historial reciente para el Team Lead",
                ),
                "is_scout": True,
                "scout_type": "session_history",
                "skip_quality_gates": True,
                "phase": "scout_session_history",
                "chat_parent": task_root,
            },
        )
        _scout_curator_task = WorkTask(
            task_id=scout_curator_id,
            title="Scout: context curator",
            description=_build_context_curator_prompt(
                message=payload.message,
                surface_hints=preplan_surface_hints,
                project_state_raw=_scout_state_raw,
                session_history_raw=_scout_history_raw,
            ),
            role=Role.SCOUT,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
            metadata={
                **build_tool_specialist_metadata(
                    specialist="context_curator",
                    required_capabilities=["repo_read"],
                    reason="compactacion barata del contexto antes de lead_intake",
                ),
                "is_scout": True,
                "scout_type": "context_curator",
                "skip_quality_gates": True,
                "phase": "scout_context_curator",
                "chat_parent": task_root,
                "preplan_surface_hints": dict(preplan_surface_hints),
                "context_pressure_score": int(preplan_context_pressure.get("score", 0) or 0),
                "context_pressure_level": str(preplan_context_pressure.get("level", "") or "").strip(),
                "context_pressure_signals": list(preplan_context_pressure.get("signals", []) or []),
                "context_curator_recommended": bool(
                    preplan_context_pressure.get("recommend_context_curator", False)
                ),
            },
        )

        lead_intake_task = WorkTask(
            task_id=lead_task_id,
            title="Lead intake and planning",
            description=lead_intake_description,
            role=Role.TEAM_LEAD,
            complexity=complexity,
            criticality=criticality,
            dependencies=[scout_state_id, scout_history_id, scout_curator_id],
            metadata={
                **build_chat_task_policy_metadata(),
                "required_capabilities": ["reasoning"],
                "require_peer_consultation": True,
                "phase": "lead_intake",
                "chat_preferred_role": preferred_role.value,
                "preplan_surface_hints": dict(preplan_surface_hints),
                "preplan_signal_block": preplan_signal_block,
                "preplan_context_curator_task_id": scout_curator_id,
                "continuation_requested": continuation_requested,
                "continuation_of": continuation_of,
                "continuation_snapshot": continuation_snapshot,
                "context_pressure_score": int(preplan_context_pressure.get("score", 0) or 0),
                "context_pressure_level": str(preplan_context_pressure.get("level", "") or "").strip(),
                "context_pressure_signals": list(preplan_context_pressure.get("signals", []) or []),
                "context_curator_recommended": bool(
                    preplan_context_pressure.get("recommend_context_curator", False)
                ),
            },
        )

        _preplan_ws = orch._get_workflow_state(task_root)
        _preplan_ws["preplan_surface_hints"] = dict(preplan_surface_hints)
        _preplan_ws["preplan_signal_block"] = preplan_signal_block
        _preplan_ws["continuation_requested"] = continuation_requested
        _preplan_ws["continuation_of"] = continuation_of
        _preplan_ws["continuation_snapshot"] = continuation_snapshot
        _preplan_ws["context_pressure"] = dict(preplan_context_pressure)
        _preplan_ws["context_curator_recommended"] = bool(
            preplan_context_pressure.get("recommend_context_curator", False)
        )
        orch._save_workflow_state()
        orch.event_logger.emit(
            "lead_preplan_surface_hints",
            {
                "task_id": task_root,
                "surfaces": list(preplan_surface_hints.get("surfaces", []) or []),
                "recommended_delegate_intents": list(
                    preplan_surface_hints.get("recommended_delegate_intents", []) or []
                ),
                "context_pressure_score": int(preplan_context_pressure.get("score", 0) or 0),
                "context_pressure_level": str(preplan_context_pressure.get("level", "") or "").strip(),
            },
        )

        artifact_before = _workspace_artifact_snapshot(workspace)
        bootstrap_result = _materialize_game_iteration(workspace, payload.message)
        if bool(bootstrap_result.get("applied", False)):
            raw_bootstrap_files = bootstrap_result.get("files", [])
            _bfiles = (
                raw_bootstrap_files if isinstance(raw_bootstrap_files, list) else []
            )
            orch.event_logger.emit(
                "chat_artifact_bootstrap",
                {
                    "task_id": task_root,
                    "iteration": _safe_int_value(
                        bootstrap_result.get("iteration", 0), 0
                    ),
                    "files": [
                        str(item or "") for item in _bfiles if str(item or "").strip()
                    ],
                },
            )

        started = time.perf_counter()

        # ── PASO 1: scouts en paralelo → lead_intake ─────────────────────────
        # Los scouts (SCOUT role) corren en paralelo con modelos baratos.
        # lead_intake arranca solo cuando ambos scouts completan.
        orch.submit_task(_scout_state_task)
        orch.submit_task(_scout_history_task)
        orch.submit_task(_scout_curator_task)
        orch.submit_task(lead_intake_task)
        orch.run_until_idle(max_rounds=_LEAD_INTAKE_MAX_ROUNDS)

        # ── E7-C: Delegación bajo demanda [DELEGATE: "query"] ─────────────────
        # El Lead puede solicitar que un scout busque info adicional antes de
        # planificar. El scout responde con el contexto disponible y el Lead
        # replanifica con esa info. Máximo _MAX_DELEGATE_CYCLES ciclos.
        _MAX_DELEGATE_CYCLES = 2
        for _delegate_cycle in range(_MAX_DELEGATE_CYCLES):
            _tmp_ws = orch._get_workflow_state(task_root)
            _tmp_lead_out = _tmp_ws.get("phase_outputs", {}).get("lead_intake", "")
            _delegate_request = _extract_delegate_request(_tmp_lead_out)
            if _delegate_request is None:
                break
            _lead_delegate_result = _execute_delegate_request(
                orch=orch,
                task_root=task_root,
                workspace=workspace,
                runtime_dir=runtime_dir,
                delegate_request=_delegate_request,
                source_task_id=lead_task_id,
                source_phase="lead_intake",
                delegate_cycle=_delegate_cycle,
                rerun_budget=_LEAD_INTAKE_MAX_ROUNDS,
            )
            if not _lead_delegate_result:
                break

        # ── PASO 2: parsear WORKFLOW_PLAN del lead → fases dinamicas ────────
        _ws = orch._get_workflow_state(task_root)
        _lead_output = _ws.get("phase_outputs", {}).get("lead_intake", "")

        # ── Pausa conversacional: [CLARIFY] ──────────────────────────────────
        _clarify_question = _extract_clarify_directive(_lead_output)
        if _clarify_question:
            _pending_file = runtime_dir / f"pending_clarification_{task_root}.json"
            _pending_state = {
                "task_root": task_root,
                "question": _clarify_question,
                "original_message": payload.message,
                "original_payload": payload.model_dump(),
                "lead_output": _lead_output[:800],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            _pending_file.write_text(json.dumps(_pending_state, ensure_ascii=False, indent=2))
            orch.event_logger.emit(
                "chat_waiting_user",
                {"task_id": task_root, "question": _clarify_question},
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="waiting_user",
                response=_clarify_question,
                decision_justification="Lead necesita aclaración antes de planificar.",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=chat_mode,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                waiting_user=True,
                clarification_question=_clarify_question,
            )
        # ── LCP: Lead Control Protocol ───────────────────────────────────────
        # Parsear directivas del Lead e intervenir antes de crear fases.
        # Prioridad: REJECT > ABORT/DIRECT > ESCALATE > SKIP > EXTEND_BUDGET
        _lcp_resolution = _lead_control_resolve_lead_intake(
            lead_output=_lead_output,
            chat_mode=chat_mode,
            complexity=complexity,
            criticality=criticality,
            round_budget=round_budget,
        )
        _lcp = _lcp_resolution.directives
        _lead_output_clean = _lcp_resolution.cleaned_output
        _lead_run_mode = str(_lcp.get("run_mode", "") or "").strip() or "standard"
        _curator_output = str(_ws.get("phase_outputs", {}).get("scout_context_curator", "") or "")
        _project_context_payload, _chat_context_payload = _persist_preplan_context(
            runtime_dir=runtime_dir,
            workspace=workspace,
            task_root=task_root,
            user_message=payload.message,
            surface_hints=preplan_surface_hints,
            curator_summary=_curator_output,
            lead_summary=_lead_output_clean,
            source_task_ids=[scout_curator_id, lead_task_id],
        )
        _ws["project_context_summary"] = ContextCuratorStore(runtime_dir).build_summary(
            _project_context_payload
        )
        _ws["chat_context_summary"] = ContextCuratorStore(runtime_dir).build_summary(
            _chat_context_payload
        )
        orch._save_workflow_state()
        orch.event_logger.emit(
            "context_curator_persisted",
            {
                "task_id": task_root,
                "project_key": _context_project_key(workspace),
                "chat_root": task_root,
                "surfaces": list(preplan_surface_hints.get("surfaces", []) or []),
            },
        )

        def _lcp_base_response(**kwargs) -> "TeamChatResponse":
            """Response de retorno temprano compartida por REJECT/ABORT/DIRECT."""
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                response=_lead_output_clean,
                decision_justification=kwargs.get("justification", ""),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=chat_mode,
                round_budget=round_budget,
                state=kwargs.get("state", "completed"),
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
            )

        for _event in _lcp_resolution.events:
            orch.event_logger.emit(
                "lcp_directive_applied",
                {"task_id": task_root, **_event.to_event_payload()},
            )

        if _lcp_resolution.early_exit is not None:
            return _lcp_base_response(
                state=_lcp_resolution.early_exit.state,
                justification=_lcp_resolution.early_exit.justification,
            )

        _phase_evidence_plan, _evidence_plan_source = _resolve_phase_evidence_plan(
            lead_output=_lead_output,
            phases=_lcp_resolution.phases,
            message=payload.message,
            run_mode=_lead_run_mode,
        )
        _chat_run_state = ChatRunState(
            chat_root=task_root,
            lead_task_id=lead_task_id,
            preferred_role=preferred_role,
            chat_mode=chat_mode,
            complexity=_lcp_resolution.complexity,
            criticality=_lcp_resolution.criticality,
            round_budget=_lcp_resolution.round_budget,
            phases=_lcp_resolution.phases,
            phase_evidence_plan=_phase_evidence_plan,
        )
        _sync_chat_runtime_state(
            orch,
            task_root=task_root,
            chat_run_state=_chat_run_state,
            lead_run_mode=_lead_run_mode,
            evidence_plan_source=_evidence_plan_source,
        )
        _resolved_complexity = _chat_run_state.complexity
        _resolved_criticality = _chat_run_state.criticality
        round_budget = _chat_run_state.round_budget
        _chat_round_budget_cap = 80
        lead_advisory_mode = False
        lead_advisory_reason = ""
        policy_signals: list[str] = []
        phases: list[PhaseSpec] = _chat_run_state.phases
        def _submit_chat_plan(
            _state: ChatRunState,
        ) -> tuple[dict[str, str], list[str], list[str]]:
            local_phase_task_ids = _state.phase_task_ids
            local_workflow_phase_keys = _state.workflow_phase_keys
            local_delegated_task_ids = _state.delegated_task_ids
            local_require_execution_plan = (
                require_build_execution_plan and _lead_run_mode == "standard"
            )

            for _spec in _state.phases:
                _phase_task_exists = (
                    orch.taskboard.get_task(local_phase_task_ids[_spec.phase_id]) is not None
                )
                _role_enum = Role[_spec.role]
                _caps = _ROLE_CAPABILITIES.get(_spec.role, ["analysis"])
                _is_engineer = _spec.role == "ENGINEER"
                _deps = _state.dependency_ids_for(_spec)
                if not _phase_task_exists:
                    orch.submit_task(
                        WorkTask(
                            task_id=local_phase_task_ids[_spec.phase_id],
                            title=_spec.phase_id.replace("_", " ").title(),
                            description=(
                                f"{_spec.objective}\n"
                                f"Solicitud original: {payload.message}\n"
                                f"Entrega: resultado accionable con evidencia para la siguiente fase."
                                f"{continuity_block}"
                            ),
                            role=_role_enum,
                            complexity=_resolved_complexity,
                            criticality=_resolved_criticality,
                            dependencies=_deps,
                            metadata={
                                **build_chat_task_policy_metadata(
                                    require_execution_plan=(
                                        local_require_execution_plan if _is_engineer else False
                                    )
                                ),
                                "required_capabilities": _caps,
                                "require_peer_consultation": True,
                                "phase": _spec.phase_id,
                                "chat_parent": task_root,
                                "lead_run_mode": _lead_run_mode,
                                "delegated_by": "team_lead",
                                "delegation_brief": _spec.objective,
                                "delegation_from_role": "team_lead",
                            },
                        )
                    )
                _evidence_specs = _structured_evidence_specs_for_phase(
                    _spec.phase_id,
                    _state.phase_evidence_plan,
                )
                for _evidence_spec in _evidence_specs:
                    _evidence_task_id = f"{task_root}::{_evidence_spec['phase_id']}"
                    if orch.taskboard.get_task(_evidence_task_id) is not None:
                        local_delegated_task_ids.append(_evidence_task_id)
                        continue
                    orch.submit_task(
                        WorkTask(
                            task_id=_evidence_task_id,
                            title=f"Evidencia {str(_evidence_spec['source_phase']).replace('_', ' ')}",
                            description=(
                                f"{_evidence_spec['instruction']}\n\n"
                                f"Fase origen: {_spec.phase_id}\n"
                                f"Objetivo de la fase: {_spec.objective}\n"
                                f"Solicitud original: {payload.message}\n"
                                f"{_evidence_spec['report_contract']}"
                                f"{continuity_block}"
                            ),
                            role=_evidence_spec["role"],
                            complexity=Complexity.LOW,
                            criticality=_resolved_criticality,
                            dependencies=[local_phase_task_ids[_spec.phase_id]],
                            metadata={
                                **build_chat_task_policy_metadata(),
                                "required_capabilities": _evidence_spec["required_capabilities"],
                                "skip_quality_gates": True,
                                "skip_evidence_gate": True,
                                "phase": _evidence_spec["phase_id"],
                                "chat_parent": task_root,
                                "lead_run_mode": _lead_run_mode,
                                "delegated_by": "team_lead",
                                "delegation_brief": (
                                    f"Evidencia estructurada para {_spec.phase_id}: "
                                    f"{_evidence_spec['intent']}"
                                ),
                                "delegation_from_role": "team_lead",
                                "delegate_intent": _evidence_spec["intent"],
                                "delegate_wait_policy": _evidence_spec["wait_policy"],
                                "delegate_budget_rounds": _evidence_spec["delegate_budget"],
                                "evidence_source_phase": _spec.phase_id,
                                "structured_evidence_task": True,
                                "delegate_report_contract_version": "operator_report_v1",
                                "skill_targets": _evidence_spec["skill_targets"],
                                "lsp_targets": _evidence_spec["lsp_targets"],
                                **build_tool_specialist_metadata(
                                    specialist=str(_evidence_spec["specialist"]),
                                    required_capabilities=_evidence_spec["required_capabilities"],
                                    reason=(
                                        f"evidence_plan para la fase {_spec.phase_id}; "
                                        f"intent={_evidence_spec['intent']}"
                                    ),
                                    skill_targets=_evidence_spec["skill_targets"],
                                    lsp_targets=_evidence_spec["lsp_targets"],
                                ),
                            },
                        )
                    )
                    local_delegated_task_ids.append(_evidence_task_id)
            local_delegated_task_ids = list(dict.fromkeys(local_delegated_task_ids))

            _close_deps = local_delegated_task_ids if local_delegated_task_ids else [lead_task_id]
            if orch.taskboard.get_task(_state.lead_close_task_id) is None:
                orch.submit_task(
                    WorkTask(
                        task_id=_state.lead_close_task_id,
                        title="Lead synthesis and response",
                        description=(
                            "Como Team Lead senior, sintetiza el trabajo del equipo y responde al usuario.\n"
                            f"Solicitud original: {payload.message}\n"
                            "Entrega: resumen ejecutivo, decisiones tomadas y proximos pasos."
                            f"{continuity_block}"
                        ),
                        role=Role.TEAM_LEAD,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        dependencies=_close_deps,
                        metadata={
                            **build_chat_task_policy_metadata(),
                            "required_capabilities": ["reasoning"],
                            "require_peer_consultation": True,
                            "phase": "lead_close",
                            "chat_parent": task_root,
                            "lead_run_mode": _lead_run_mode,
                        },
                    )
                )
            return local_phase_task_ids, local_workflow_phase_keys, local_delegated_task_ids

        phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
            _chat_run_state
        )
        _sync_chat_runtime_state(
            orch,
            task_root=task_root,
            chat_run_state=_chat_run_state,
            lead_run_mode=_lead_run_mode,
            delegated_task_ids=delegated_task_ids,
            evidence_plan_source=_evidence_plan_source,
        )

        workflow_label = " -> ".join(workflow_phase_keys)
        orch.event_logger.emit(
            "chat_plan_created",
            {
                "task_id": task_root,
                "chat_mode": chat_mode,
                "round_budget": round_budget,
                "phase_count": len(workflow_phase_keys),
                "delegated_count": len(delegated_task_ids),
                "dynamic_phases": [s.phase_id for s in phases],
                "lead_run_mode": _lead_run_mode,
                "phase_evidence_plan": _chat_run_state.phase_evidence_plan,
                "evidence_plan_source": _evidence_plan_source,
                "continuation_requested": continuation_requested,
                "continuation_of": continuation_of,
                "continuation_snapshot": continuation_snapshot,
            },
        )

        orch.mailbox.send(
            sender="team_lead",
            recipient="broadcast",
            subject=f"Lead delegation created: {task_root}",
            body=(
                "Lead received user request and created phased workflow: "
                f"{workflow_label} (mode={chat_mode}, round_budget={round_budget})"
            ),
            task_id=task_root,
        )

        # ── PASO 3: ejecutar fases dinamicas + lead_close ───────────────────
        orch.run_until_idle(max_rounds=round_budget)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        _phase_states_for_replan: dict[str, str] = {}
        for _phase_name, _phase_id in phase_task_ids.items():
            _task_row = orch.taskboard.get_task(_phase_id)
            _phase_states_for_replan[_phase_name] = (
                _task_row.state.value if _task_row is not None else "missing"
            )

        _budget_adjustments = _extract_budget_adjustments_from_outputs(
            _ws.get("phase_outputs", {})
        )
        _budget_changed = False
        for _budget_source_phase, _budget_payload in _budget_adjustments:
            _source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_budget_source_phase, "") or ""
            )
            _strip_budget_directives: list[str] = []
            _new_round_budget = round_budget
            _escalate = _budget_payload.get("escalate")
            if isinstance(_escalate, dict):
                _valid_complexity = {"low", "medium", "high"}
                _valid_criticality = {"low", "medium", "high"}
                _complexity_value = str(_escalate.get("complexity", "") or "").strip()
                _criticality_value = str(_escalate.get("criticality", "") or "").strip()
                if _complexity_value in _valid_complexity:
                    _resolved_complexity = Complexity(_complexity_value)
                if _criticality_value in _valid_criticality:
                    _resolved_criticality = Criticality(_criticality_value)
                _boost = {"high": 1.5}.get(_resolved_complexity.value, 1.0)
                if _boost > 1.0:
                    _new_round_budget = min(
                        _chat_round_budget_cap,
                        max(round_budget, int(round_budget * _boost)),
                    )
                _strip_budget_directives.append("ESCALATE")
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "escalate_mid_run",
                        "source_phase": _budget_source_phase,
                        "payload": _escalate,
                        "new_round_budget": _new_round_budget,
                    },
                )
            if _budget_payload.get("extend_budget"):
                _extension = int(_budget_payload.get("extend_budget", 0))
                _new_round_budget = min(_chat_round_budget_cap, round_budget + _extension)
                _strip_budget_directives.append("EXTEND_BUDGET")
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "extend_budget_mid_run",
                        "source_phase": _budget_source_phase,
                        "extension": _extension,
                        "new_round_budget": _new_round_budget,
                    },
                )
            if _budget_payload.get("set_budget"):
                _requested_budget = int(_budget_payload.get("set_budget", round_budget))
                _new_round_budget = max(1, min(_requested_budget, _chat_round_budget_cap))
                _strip_budget_directives.append("SET_BUDGET")
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "set_budget_mid_run",
                        "source_phase": _budget_source_phase,
                        "new_round_budget": _new_round_budget,
                    },
                )
            if _strip_budget_directives:
                round_budget = _new_round_budget
                _budget_changed = True
                _ws.setdefault("phase_outputs", {})[_budget_source_phase] = (
                    _strip_selected_directives(_source_output, _strip_budget_directives)
                )
                orch._save_workflow_state()

        if _budget_changed:
            _chat_run_state = ChatRunState(
                chat_root=task_root,
                lead_task_id=lead_task_id,
                preferred_role=preferred_role,
                chat_mode=chat_mode,
                complexity=_resolved_complexity,
                criticality=_resolved_criticality,
                round_budget=round_budget,
                phases=phases,
                phase_evidence_plan=_chat_run_state.phase_evidence_plan,
            )
            _sync_chat_runtime_state(
                orch,
                task_root=task_root,
                chat_run_state=_chat_run_state,
                lead_run_mode=_lead_run_mode,
                delegated_task_ids=delegated_task_ids,
                evidence_plan_source=_evidence_plan_source,
            )

        _MAX_MIDRUN_DELEGATE_CYCLES = 2
        for _mid_delegate_cycle in range(_MAX_MIDRUN_DELEGATE_CYCLES):
            _mid_delegate_request = _extract_delegate_request_from_outputs(
                _ws.get("phase_outputs", {})
            )
            if _mid_delegate_request is None:
                break
            _delegate_source_phase, _delegate_request = _mid_delegate_request
            _delegate_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_delegate_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_delegate_source_phase] = _strip_selected_directives(
                _delegate_source_output,
                [
                    "DELEGATE",
                    "DELEGATE_REPO_SCAN",
                    "DELEGATE_BROWSER_REPRO",
                    "DELEGATE_LSP_IMPACT",
                    "DELEGATE_TEST_RUN",
                    "DELEGATE_MCP_PROBE",
                    "WAIT_POLICY",
                    "DELEGATE_BUDGET",
                ],
            )
            orch._save_workflow_state()
            _mid_source_task_id = f"{task_root}::{_delegate_source_phase}"
            _delegate_result = _execute_delegate_request(
                orch=orch,
                task_root=task_root,
                workspace=workspace,
                runtime_dir=runtime_dir,
                delegate_request=_delegate_request,
                source_task_id=_mid_source_task_id,
                source_phase=_delegate_source_phase,
                delegate_cycle=_mid_delegate_cycle,
                rerun_budget=round_budget,
            )
            if not _delegate_result:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "delegate",
                        "source_phase": _delegate_source_phase,
                        "reason": "missing_source_task",
                    },
                )
                break
            elapsed_ms = int((time.perf_counter() - started) * 1000)

        _advisory_request = _extract_advisory_request_from_outputs(
            _ws.get("phase_outputs", {})
        )
        if _advisory_request is not None:
            _advisory_source_phase, _advisory_reason = _advisory_request
            _advisory_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_advisory_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_advisory_source_phase] = _strip_selected_directives(
                _advisory_source_output,
                ["ADVISORY_MODE"],
            )
            orch._save_workflow_state()
            _lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            if not _phase_started_for_replan(_lead_close_task):
                _current_tasks_by_phase = {
                    _phase_name: orch.taskboard.get_task(_phase_id)
                    for _phase_name, _phase_id in phase_task_ids.items()
                    if _phase_name not in {"lead_intake", "lead_close"}
                }
                _pruned_phases, _removed_phase_ids, _preserved_started_phase_ids, _ = (
                    _prune_phases_for_mid_run_lead_action(
                        phases,
                        _current_tasks_by_phase,
                        abort_all_pending=True,
                    )
                )
                _preserved_task_ids = [
                    _task.task_id
                    for _phase_name, _task in _current_tasks_by_phase.items()
                    if _phase_name in set(_preserved_started_phase_ids)
                    and _task is not None
                ]
                _remove_ids = [
                    _task.task_id
                    for _task in orch.taskboard.list_tasks()
                    if str(_task.task_id).startswith(f"{task_root}::")
                    and _task.task_id != lead_task_id
                    and _task.task_id not in set(_preserved_task_ids)
                ]
                orch.taskboard.remove_tasks(_remove_ids)
                _keep_outputs: dict[str, str] = {}
                for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                    if _is_supporting_control_phase(_phase_key):
                        _keep_outputs[_phase_key] = _output
                    elif _phase_key in set(_preserved_started_phase_ids):
                        _keep_outputs[_phase_key] = _output
                _ws["phase_outputs"] = _keep_outputs
                orch._save_workflow_state()
                lead_advisory_mode = True
                lead_advisory_reason = _advisory_reason
                _chat_run_state = ChatRunState(
                    chat_root=task_root,
                    lead_task_id=lead_task_id,
                    preferred_role=preferred_role,
                    chat_mode=chat_mode,
                    complexity=_resolved_complexity,
                    criticality=_resolved_criticality,
                    round_budget=round_budget,
                    phases=_pruned_phases,
                    phase_evidence_plan=_chat_run_state.phase_evidence_plan,
                )
                phases = _chat_run_state.phases
                phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                    _chat_run_state
                )
                _sync_chat_runtime_state(
                    orch,
                    task_root=task_root,
                    chat_run_state=_chat_run_state,
                    lead_run_mode=_lead_run_mode,
                    delegated_task_ids=delegated_task_ids,
                    evidence_plan_source=_evidence_plan_source,
                )
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "advisory_mode",
                        "source_phase": _advisory_source_phase,
                        "reason": lead_advisory_reason,
                        "removed_phases": _removed_phase_ids,
                    },
                )
                orch.run_until_idle(max_rounds=round_budget)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "advisory_mode",
                        "source_phase": _advisory_source_phase,
                        "reason": "lead_close_started",
                    },
                )

        _replan_request = _extract_replan_phases_from_outputs(
            _ws.get("phase_outputs", {})
        )
        if _replan_request is not None:
            _replan_source_phase, _replan_phases = _replan_request
            _replan_skip = _replan_skip_reason(_replan_source_phase)
            if _replan_skip:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "replan",
                        "source_phase": _replan_source_phase,
                        "reason": _replan_skip,
                    },
                )
                _replan_request = None
        if _replan_request is not None:
            _replan_source_phase, _replan_phases = _replan_request
            _replan_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_replan_source_phase, "") or ""
            )
            _replan_evidence_plan = (
                _extract_evidence_plan(_replan_source_output)
                or _chat_run_state.phase_evidence_plan
            )
            if _replan_window_is_open(_phase_states_for_replan, workflow_phase_keys):
                _remove_ids = [
                    _task.task_id
                    for _task in orch.taskboard.list_tasks()
                    if str(_task.task_id).startswith(f"{task_root}::")
                    and _task.task_id != lead_task_id
                ]
                orch.taskboard.remove_tasks(_remove_ids)
                _keep_outputs: dict[str, str] = {}
                for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                    if _is_supporting_control_phase(_phase_key):
                        _keep_outputs[_phase_key] = _output
                _ws["phase_outputs"] = _keep_outputs
                _phase_context_summaries = dict(_ws.get("phase_context_summaries", {}) or {})
                for _phase_name in [spec.phase_id for spec in phases]:
                    _phase_context_summaries.pop(_phase_name, None)
                _ws["phase_context_summaries"] = _phase_context_summaries
                _project_summary, _chat_summary = _record_context_invalidation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root=task_root,
                    reason="replan_full",
                    affected_phases=[spec.phase_id for spec in phases],
                    source_task_ids=[lead_task_id, f"{task_root}::{_replan_source_phase}"],
                )
                _ws["project_context_summary"] = _project_summary
                _ws["chat_context_summary"] = _chat_summary
                orch._save_workflow_state()

                _chat_run_state = ChatRunState(
                    chat_root=task_root,
                    lead_task_id=lead_task_id,
                    preferred_role=preferred_role,
                    chat_mode=chat_mode,
                    complexity=_resolved_complexity,
                    criticality=_resolved_criticality,
                    round_budget=round_budget,
                    phases=_replan_phases,
                    phase_evidence_plan=_replan_evidence_plan,
                )
                phases = _chat_run_state.phases
                phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                    _chat_run_state
                )
                _sync_chat_runtime_state(
                    orch,
                    task_root=task_root,
                    chat_run_state=_chat_run_state,
                    lead_run_mode=_lead_run_mode,
                    delegated_task_ids=delegated_task_ids,
                    evidence_plan_source="replan",
                )
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "replan",
                        "source_phase": _replan_source_phase,
                        "phase_count": len(_replan_phases),
                    },
                )
                orch.run_until_idle(max_rounds=round_budget)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
            else:
                _current_tasks_by_phase = {
                    _phase_name: orch.taskboard.get_task(_phase_id)
                    for _phase_name, _phase_id in phase_task_ids.items()
                    if _phase_name not in {"lead_intake", "lead_close"}
                }
                _merged_phases, _preserved_phase_ids, _preserved_task_ids = _merge_replanned_phases(
                    phases,
                    _current_tasks_by_phase,
                    _replan_phases,
                )
                if _preserved_phase_ids and len(_merged_phases) >= len(_preserved_phase_ids):
                    _remove_ids = [
                        _task.task_id
                        for _task in orch.taskboard.list_tasks()
                        if str(_task.task_id).startswith(f"{task_root}::")
                        and _task.task_id != lead_task_id
                        and _task.task_id not in set(_preserved_task_ids)
                    ]
                    orch.taskboard.remove_tasks(_remove_ids)

                    _keep_outputs: dict[str, str] = {}
                    for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                        if _is_supporting_control_phase(_phase_key):
                            _keep_outputs[_phase_key] = _output
                        elif _phase_key in set(_preserved_phase_ids):
                            _keep_outputs[_phase_key] = _output
                    _ws["phase_outputs"] = _keep_outputs
                    _phase_context_summaries = dict(_ws.get("phase_context_summaries", {}) or {})
                    for _phase_name in _removed_phase_ids:
                        _phase_context_summaries.pop(_phase_name, None)
                    _ws["phase_context_summaries"] = _phase_context_summaries
                    _project_summary, _chat_summary = _record_context_invalidation(
                        runtime_dir=runtime_dir,
                        workspace=workspace,
                        task_root=task_root,
                        reason="replan_partial",
                        affected_phases=_removed_phase_ids,
                        source_task_ids=[lead_task_id, f"{task_root}::{_replan_source_phase}"],
                    )
                    _ws["project_context_summary"] = _project_summary
                    _ws["chat_context_summary"] = _chat_summary
                    orch._save_workflow_state()

                    _chat_run_state = ChatRunState(
                        chat_root=task_root,
                        lead_task_id=lead_task_id,
                        preferred_role=preferred_role,
                        chat_mode=chat_mode,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        round_budget=round_budget,
                        phases=_merged_phases,
                        phase_evidence_plan=_replan_evidence_plan,
                    )
                    phases = _chat_run_state.phases
                    phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                        _chat_run_state
                    )
                    _sync_chat_runtime_state(
                        orch,
                        task_root=task_root,
                        chat_run_state=_chat_run_state,
                        lead_run_mode=_lead_run_mode,
                        delegated_task_ids=delegated_task_ids,
                        evidence_plan_source="replan",
                    )
                    orch.event_logger.emit(
                        "lcp_directive_applied",
                        {
                            "task_id": task_root,
                            "directive": "replan_partial",
                            "source_phase": _replan_source_phase,
                            "phase_count": len(_merged_phases),
                            "preserved_phases": _preserved_phase_ids,
                        },
                    )
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                else:
                    orch.event_logger.emit(
                        "lcp_directive_skipped",
                        {
                            "task_id": task_root,
                            "directive": "replan",
                            "source_phase": _replan_source_phase,
                            "reason": "dynamic_phase_already_started",
                        },
                    )

        _abort_request = _extract_abort_request_from_outputs(_ws.get("phase_outputs", {}))
        if _abort_request is not None:
            _abort_source_phase, _abort_reason = _abort_request
            _abort_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_abort_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_abort_source_phase] = _strip_lcp_directives(
                _abort_source_output
            )
            orch._save_workflow_state()
            _lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            if not _phase_started_for_replan(_lead_close_task):
                _current_tasks_by_phase = {
                    _phase_name: orch.taskboard.get_task(_phase_id)
                    for _phase_name, _phase_id in phase_task_ids.items()
                    if _phase_name not in {"lead_intake", "lead_close"}
                }
                _pruned_phases, _removed_phase_ids, _preserved_started_phase_ids, _ = (
                    _prune_phases_for_mid_run_lead_action(
                        phases,
                        _current_tasks_by_phase,
                        abort_all_pending=True,
                    )
                )
                if _removed_phase_ids:
                    _preserved_task_ids = [
                        _task.task_id
                        for _phase_name, _task in _current_tasks_by_phase.items()
                        if _phase_name in set(_preserved_started_phase_ids)
                        and _task is not None
                    ]
                    _remove_ids = [
                        _task.task_id
                        for _task in orch.taskboard.list_tasks()
                        if str(_task.task_id).startswith(f"{task_root}::")
                        and _task.task_id != lead_task_id
                        and _task.task_id not in set(_preserved_task_ids)
                    ]
                    orch.taskboard.remove_tasks(_remove_ids)
                    _keep_outputs: dict[str, str] = {}
                    for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                        if _is_supporting_control_phase(_phase_key):
                            _keep_outputs[_phase_key] = _output
                        elif _phase_key in set(_preserved_started_phase_ids):
                            _keep_outputs[_phase_key] = _output
                    _ws["phase_outputs"] = _keep_outputs
                    orch._save_workflow_state()
                    _chat_run_state = ChatRunState(
                        chat_root=task_root,
                        lead_task_id=lead_task_id,
                        preferred_role=preferred_role,
                        chat_mode=chat_mode,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        round_budget=round_budget,
                        phases=_pruned_phases,
                        phase_evidence_plan=_chat_run_state.phase_evidence_plan,
                    )
                    phases = _chat_run_state.phases
                    phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                        _chat_run_state
                    )
                    _sync_chat_runtime_state(
                        orch,
                        task_root=task_root,
                        chat_run_state=_chat_run_state,
                        lead_run_mode=_lead_run_mode,
                        delegated_task_ids=delegated_task_ids,
                        evidence_plan_source=_evidence_plan_source,
                    )
                    orch.event_logger.emit(
                        "lcp_directive_applied",
                        {
                            "task_id": task_root,
                            "directive": "abort_phases",
                            "source_phase": _abort_source_phase,
                            "removed_phases": _removed_phase_ids,
                            "reason": _abort_reason,
                        },
                    )
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                else:
                    orch.event_logger.emit(
                        "lcp_directive_skipped",
                        {
                            "task_id": task_root,
                            "directive": "abort_phases",
                            "source_phase": _abort_source_phase,
                            "reason": "no_pending_phases_to_abort",
                        },
                    )
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "abort_phases",
                        "source_phase": _abort_source_phase,
                        "reason": "lead_close_started",
                    },
                )

        _skip_request = _extract_skip_request_from_outputs(_ws.get("phase_outputs", {}))
        if _skip_request is not None:
            _skip_source_phase, _skip_targets = _skip_request
            _skip_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_skip_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_skip_source_phase] = _strip_lcp_directives(
                _skip_source_output
            )
            orch._save_workflow_state()
            _lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            if not _phase_started_for_replan(_lead_close_task):
                _current_tasks_by_phase = {
                    _phase_name: orch.taskboard.get_task(_phase_id)
                    for _phase_name, _phase_id in phase_task_ids.items()
                    if _phase_name not in {"lead_intake", "lead_close"}
                }
                (
                    _pruned_phases,
                    _removed_phase_ids,
                    _preserved_started_phase_ids,
                    _skipped_started_targets,
                ) = _prune_phases_for_mid_run_lead_action(
                    phases,
                    _current_tasks_by_phase,
                    target_phase_ids=_skip_targets,
                )
                if _removed_phase_ids:
                    _preserved_task_ids = [
                        _task.task_id
                        for _phase_name, _task in _current_tasks_by_phase.items()
                        if _phase_name in set(_preserved_started_phase_ids)
                        and _task is not None
                    ]
                    _remove_ids = [
                        _task.task_id
                        for _task in orch.taskboard.list_tasks()
                        if str(_task.task_id).startswith(f"{task_root}::")
                        and _task.task_id != lead_task_id
                        and _task.task_id not in set(_preserved_task_ids)
                    ]
                    orch.taskboard.remove_tasks(_remove_ids)
                    _keep_outputs: dict[str, str] = {}
                    for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                        if _is_supporting_control_phase(_phase_key):
                            _keep_outputs[_phase_key] = _output
                        elif _phase_key in set(_preserved_started_phase_ids):
                            _keep_outputs[_phase_key] = _output
                    _ws["phase_outputs"] = _keep_outputs
                    orch._save_workflow_state()
                    _chat_run_state = ChatRunState(
                        chat_root=task_root,
                        lead_task_id=lead_task_id,
                        preferred_role=preferred_role,
                        chat_mode=chat_mode,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        round_budget=round_budget,
                        phases=_pruned_phases,
                        phase_evidence_plan=_chat_run_state.phase_evidence_plan,
                    )
                    phases = _chat_run_state.phases
                    phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                        _chat_run_state
                    )
                    _sync_chat_runtime_state(
                        orch,
                        task_root=task_root,
                        chat_run_state=_chat_run_state,
                        lead_run_mode=_lead_run_mode,
                        delegated_task_ids=delegated_task_ids,
                        evidence_plan_source=_evidence_plan_source,
                    )
                    orch.event_logger.emit(
                        "lcp_directive_applied",
                        {
                            "task_id": task_root,
                            "directive": "skip_mid_run",
                            "source_phase": _skip_source_phase,
                            "removed_phases": _removed_phase_ids,
                            "requested_phases": _skip_targets,
                            "skipped_started_targets": _skipped_started_targets,
                        },
                    )
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                else:
                    orch.event_logger.emit(
                        "lcp_directive_skipped",
                        {
                            "task_id": task_root,
                            "directive": "skip_mid_run",
                            "source_phase": _skip_source_phase,
                            "requested_phases": _skip_targets,
                            "reason": "no_pending_target_phases",
                            "skipped_started_targets": _skipped_started_targets,
                        },
                    )
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "skip_mid_run",
                        "source_phase": _skip_source_phase,
                        "requested_phases": _skip_targets,
                        "reason": "lead_close_started",
                    },
                )

        _retry_route_request = _extract_retry_route_request_from_outputs(
            _ws.get("phase_outputs", {})
        )
        if _retry_route_request is not None:
            _retry_source_phase, _retry_target_phase = _retry_route_request
            _retry_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_retry_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_retry_source_phase] = _strip_selected_directives(
                _retry_source_output,
                ["RETRY_ROUTE"],
            )
            orch._save_workflow_state()
            _lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            _target_task_id = phase_task_ids.get(_retry_target_phase, "")
            _target_task = orch.taskboard.get_task(_target_task_id) if _target_task_id else None
            _tasks_by_phase = {
                _phase_name: orch.taskboard.get_task(_phase_id)
                for _phase_name, _phase_id in phase_task_ids.items()
                if _phase_name not in {"lead_intake", "lead_close"}
            }
            _retry_removed_phase_ids = _retry_route_removal_phase_ids(
                phases,
                _retry_target_phase,
            )
            _downstream_started = any(
                _phase_name != _retry_target_phase
                and _phase_started_for_replan(_tasks_by_phase.get(_phase_name))
                for _phase_name in _retry_removed_phase_ids
            )
            if (
                _target_task is not None
                and _phase_started_for_replan(_target_task)
                and not _phase_started_for_replan(_lead_close_task)
                and _retry_removed_phase_ids
                and not _downstream_started
            ):
                _preserved_started_phase_ids = [
                    _phase_name
                    for _phase_name, _task in _tasks_by_phase.items()
                    if _phase_name not in set(_retry_removed_phase_ids)
                    and _phase_started_for_replan(_task)
                ]
                _preserved_task_ids = [
                    _task.task_id
                    for _phase_name, _task in _tasks_by_phase.items()
                    if _phase_name in set(_preserved_started_phase_ids)
                    and _task is not None
                ]
                _remove_ids = [
                    _task.task_id
                    for _task in orch.taskboard.list_tasks()
                    if str(_task.task_id).startswith(f"{task_root}::")
                    and _task.task_id != lead_task_id
                    and _task.task_id not in set(_preserved_task_ids)
                ]
                orch.taskboard.remove_tasks(_remove_ids)
                _keep_outputs: dict[str, str] = {}
                for _phase_key, _output in _ws.get("phase_outputs", {}).items():
                    if _is_supporting_control_phase(_phase_key):
                        _keep_outputs[_phase_key] = _output
                    elif _phase_key in set(_preserved_started_phase_ids):
                        _keep_outputs[_phase_key] = _output
                _ws["phase_outputs"] = _keep_outputs
                orch._save_workflow_state()
                _chat_run_state = ChatRunState(
                    chat_root=task_root,
                    lead_task_id=lead_task_id,
                    preferred_role=preferred_role,
                    chat_mode=chat_mode,
                    complexity=_resolved_complexity,
                    criticality=_resolved_criticality,
                    round_budget=round_budget,
                    phases=phases,
                    phase_evidence_plan=_chat_run_state.phase_evidence_plan,
                )
                phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                    _chat_run_state
                )
                _sync_chat_runtime_state(
                    orch,
                    task_root=task_root,
                    chat_run_state=_chat_run_state,
                    lead_run_mode=_lead_run_mode,
                    delegated_task_ids=delegated_task_ids,
                    evidence_plan_source=_evidence_plan_source,
                )
                _new_target_task_id = phase_task_ids.get(_retry_target_phase, "")
                _excluded_adapters = [
                    str(item).strip()
                    for item in list((_target_task.metadata.get("excluded_adapters", []) or []))
                    if str(item).strip()
                ]
                _last_adapter_name = str(_target_task.metadata.get("last_adapter_name", "") or "").strip()
                if _last_adapter_name and _last_adapter_name not in _excluded_adapters:
                    _excluded_adapters.append(_last_adapter_name)
                if _new_target_task_id:
                    orch.taskboard.update_metadata(
                        _new_target_task_id,
                        {
                            "excluded_adapters": _excluded_adapters,
                            "retry_route_requested": True,
                            "retry_route_requested_by": _retry_source_phase,
                            "retry_route_count": int(_target_task.metadata.get("retry_route_count", 0)) + 1,
                        },
                    )
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "retry_route",
                        "source_phase": _retry_source_phase,
                        "target_phase": _retry_target_phase,
                        "excluded_adapters": _excluded_adapters,
                    },
                )
                orch.run_until_idle(max_rounds=round_budget)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "retry_route",
                        "source_phase": _retry_source_phase,
                        "target_phase": _retry_target_phase,
                        "reason": (
                            "lead_close_started"
                            if _phase_started_for_replan(_lead_close_task)
                            else "target_not_started_or_downstream_already_started"
                        ),
                    },
                )

        _force_gate_request = _extract_force_gate_request_from_outputs(
            _ws.get("phase_outputs", {})
        )
        if _force_gate_request is not None:
            _force_gate_source_phase, _force_gate_target = _force_gate_request
            _force_gate_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_force_gate_source_phase, "") or ""
            )
            _clean_force_gate_output = _strip_lcp_directives(_force_gate_source_output)
            _ws.setdefault("phase_outputs", {})[_force_gate_source_phase] = (
                _clean_force_gate_output
            )
            _phase_context_summaries = dict(_ws.get("phase_context_summaries", {}) or {})
            _phase_context_summaries.pop(_force_gate_target, None)
            _ws["phase_context_summaries"] = _phase_context_summaries
            _project_summary, _chat_summary = _record_context_invalidation(
                runtime_dir=runtime_dir,
                workspace=workspace,
                task_root=task_root,
                reason="force_gate",
                affected_phases=[_force_gate_target],
                source_task_ids=[lead_task_id, f"{task_root}::{_force_gate_source_phase}"],
            )
            _ws["project_context_summary"] = _project_summary
            _ws["chat_context_summary"] = _chat_summary
            orch._save_workflow_state()
            _target_task_id = phase_task_ids.get(_force_gate_target, "")
            _target_task = orch.taskboard.get_task(_target_task_id) if _target_task_id else None
            _lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            _lead_close_started = _phase_started_for_replan(_lead_close_task)
            if (
                _target_task is not None
                and _target_task.state == TaskState.COMPLETED
                and not _target_task.metadata.get("quality_gate_spawned")
                and not _lead_close_started
            ):
                orch.taskboard.mark_blocked(
                    _target_task.task_id,
                    reason="waiting_quality_gates",
                )
                orch.taskboard.update_metadata(
                    _target_task.task_id,
                    {
                        "force_gate_requested_by": _force_gate_source_phase,
                        "force_gate_target_phase": _force_gate_target,
                        "force_gate_requested": True,
                    },
                )
                orch._spawn_quality_gates(_target_task)
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "force_gate",
                        "source_phase": _force_gate_source_phase,
                        "target_phase": _force_gate_target,
                    },
                )
                orch.run_until_idle(max_rounds=round_budget)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "force_gate",
                        "source_phase": _force_gate_source_phase,
                        "target_phase": _force_gate_target,
                        "reason": (
                            "lead_close_started"
                            if _lead_close_started
                            else "target_phase_not_completed_or_gate_already_open"
                        ),
                    },
                )
                if not _lead_close_started:
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)

        # ── E7-D4: Pausa mid-run — algún agente emitió [CLARIFY] ─────────────
        _mid_waiting = [
            t for t in orch.taskboard.list_tasks()
            if t.state == TaskState.WAITING_USER
        ]
        if _mid_waiting:
            _mwt = _mid_waiting[0]
            _mwq = _mwt.metadata.get("clarify_question", "")
            _mwphase = str(_mwt.task_id or "").split("::")[-1]
            _mid_pending_file = runtime_dir / f"pending_clarification_{task_root}.json"
            _mid_state = {
                "type": "mid_run",
                "task_root": task_root,
                "chat_run_state": _chat_run_state.to_dict(),
                "waiting_task_id": _mwt.task_id,
                "waiting_phase": _mwphase,
                "question": _mwq,
                "original_message": payload.message,
                "original_payload": payload.model_dump(),
                "phase_task_ids": phase_task_ids,
                "workflow_phase_keys": workflow_phase_keys,
                "chat_mode": chat_mode,
                "remaining_budget": round_budget,
                "preferred_role": preferred_role.value,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            _mid_pending_file.write_text(
                json.dumps(_mid_state, ensure_ascii=False, indent=2, default=str)
            )
            orch.event_logger.emit(
                "chat_waiting_user",
                {"task_id": task_root, "phase": _mwphase, "question": _mwq},
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="waiting_user",
                response=_mwq,
                decision_justification=(
                    f"El agente '{_mwphase}' necesita aclaración antes de continuar."
                ),
                elapsed_ms=elapsed_ms,
                lead_task_id=lead_task_id,
                delegated_task_ids=delegated_task_ids,
                phase_task_ids=phase_task_ids,
                chat_mode=chat_mode,
                phase_evidence_plan=_coerce_phase_evidence_plan(
                    _ws.get("phase_evidence_plan", _chat_run_state.phase_evidence_plan)
                ),
                delegate_batches=_coerce_delegate_batches(
                    _ws.get("delegate_batches", [])
                ),
                delegate_economics=dict(
                    _ws.get("delegate_economics_summary", {}) or {}
                ),
                specialist_reports=list(
                    _load_chat_specialist_insights(runtime_dir, task_root).get(
                        "specialist_reports", []
                    )
                    or []
                ),
                specialist_report_summary=dict(
                    _load_chat_specialist_insights(runtime_dir, task_root).get(
                        "specialist_report_summary", {}
                    )
                    or {}
                ),
                waiting_user=True,
                clarification_question=_mwq,
            )

        artifact_after = _workspace_artifact_snapshot(workspace)
        created_artifacts, modified_artifacts = _workspace_artifact_diff(
            artifact_before, artifact_after
        )
        artifact_created = len(created_artifacts)
        artifact_modified = len(modified_artifacts)
        artifact_files = sorted(set(created_artifacts + modified_artifacts))
        bootstrap_files = bootstrap_result.get("files", [])
        if isinstance(bootstrap_files, list):
            for item in bootstrap_files:
                name = str(item or "").strip()
                if name:
                    artifact_files.append(name)
        artifact_files = sorted(set(artifact_files))

        if artifact_files:
            orch.event_logger.emit(
                "chat_artifacts_detected",
                {
                    "task_id": task_root,
                    "created": artifact_created,
                    "modified": artifact_modified,
                    "files": artifact_files[:16],
                },
            )

        phase_task_set = set(phase_task_ids.values())
        game_followup_requested = _is_game_followup_request(workspace, payload.message)

        def _collect_phase_progress() -> tuple[
            WorkTask | None, dict[str, str], int, int, int
        ]:
            local_lead = orch.taskboard.get_task(phase_task_ids["lead_close"])
            local_phase_states: dict[str, str] = {}
            local_rounds_used = 0
            for phase_name, phase_id in phase_task_ids.items():
                task = orch.taskboard.get_task(phase_id)
                if task is None:
                    local_phase_states[phase_name] = "missing"
                    continue
                local_phase_states[phase_name] = task.state.value
                execution_round = _safe_int_value(
                    task.metadata.get("execution_round", 0), 0
                )
                local_rounds_used = max(local_rounds_used, execution_round)

            local_completed = sum(
                1 for state in local_phase_states.values() if state == "completed"
            )
            local_pending = sum(
                1
                for state in local_phase_states.values()
                if state in {"pending", "ready", "claimed", "blocked", "waiting_user"}
            )
            return (
                local_lead,
                local_phase_states,
                local_rounds_used,
                local_completed,
                local_pending,
            )

        auto_extended_rounds = 0
        if bool(payload.auto_extend_weak_runs) and round_budget < 80:
            execution_steps_so_far = 0
            for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
                if str(record.get("event_type", "") or "") != "execution_step":
                    continue
                payload_dict = record.get("payload", {})
                if not isinstance(payload_dict, dict):
                    continue
                route_task_id = str(payload_dict.get("task_id", "") or "")
                if route_task_id not in phase_task_set:
                    continue
                execution_steps_so_far += 1

            weak_without_evidence = (
                artifact_created == 0 and execution_steps_so_far == 0
            )
            if weak_without_evidence:
                next_round_budget = min(80, round_budget + 3)
                if next_round_budget > round_budget:
                    auto_extended_rounds = next_round_budget - round_budget
                    orch.event_logger.emit(
                        "chat_auto_rounds_extended",
                        {
                            "task_id": task_root,
                            "from_round_budget": round_budget,
                            "to_round_budget": next_round_budget,
                            "reason": "weak_run_without_artifacts_or_execution_steps",
                        },
                    )
                    round_budget = next_round_budget
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    artifact_after = _workspace_artifact_snapshot(workspace)
                    created_artifacts, modified_artifacts = _workspace_artifact_diff(
                        artifact_before, artifact_after
                    )
                    artifact_created = len(created_artifacts)
                    artifact_modified = len(modified_artifacts)
                    artifact_files = sorted(set(created_artifacts + modified_artifacts))
                    if isinstance(bootstrap_files, list):
                        for item in bootstrap_files:
                            name = str(item or "").strip()
                            if name:
                                artifact_files.append(name)
                    artifact_files = sorted(set(artifact_files))
                    if artifact_files:
                        orch.event_logger.emit(
                            "chat_artifacts_detected",
                            {
                                "task_id": task_root,
                                "created": artifact_created,
                                "modified": artifact_modified,
                                "files": artifact_files[:16],
                            },
                        )

        lead_result_task, phase_states, rounds_used, completed_tasks, pending_tasks = (
            _collect_phase_progress()
        )

        lead_completed = (
            lead_result_task is not None and lead_result_task.state.value == "completed"
        )
        lead_response = _task_result(lead_result_task)
        delegated_lines: list[str] = []
        delegated_placeholder_count = 0
        phase_name_by_task_id = {
            task_id: phase for phase, task_id in phase_task_ids.items()
        }
        if lead_result_task is None:
            final_state = "in_progress" if pending_tasks > 0 else "failed"
        elif lead_result_task.state.value == "completed":
            final_state = "completed"
        elif lead_result_task.state.value == "failed":
            final_state = "failed"
        else:
            final_state = "in_progress"
        for delegated_id in delegated_task_ids:
            delegated_task = orch.taskboard.get_task(delegated_id)
            if delegated_task is None:
                delegated_phase = phase_name_by_task_id.get(delegated_id, delegated_id)
                delegated_lines.append(f"- {delegated_phase}: missing")
                continue
            delegated_outcome = _task_result(delegated_task)
            delegated_phase = phase_name_by_task_id.get(delegated_id, delegated_id)
            compact_result = _compact_delegated_result(
                delegated_outcome, state=delegated_task.state.value
            )
            if compact_result == "placeholder/simulado":
                delegated_placeholder_count += 1
            delegated_lines.append(
                f"- {delegated_phase}: state={delegated_task.state.value} result={compact_result}"
            )
            if delegated_task.state.value == "failed":
                final_state = "failed"

        task_rows_by_phase: dict[str, WorkTask] = {}
        for phase_name, phase_id in phase_task_ids.items():
            task = orch.taskboard.get_task(phase_id)
            if task is not None:
                task_rows_by_phase[phase_name] = task

        role_participants = sorted(
            {task.role.value for task in task_rows_by_phase.values()}
        )
        assignee_participants = sorted(
            {
                str(task.assignee).strip()
                for task in task_rows_by_phase.values()
                if str(task.assignee or "").strip()
            }
        )

        done_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) == "completed"
        ]
        pending_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) in {"pending", "ready", "claimed", "blocked", "waiting_user"}
        ]
        failed_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) == "failed"
        ]

        intake_task = task_rows_by_phase.get("lead_intake")
        decision_source = _resolve_chat_decision_text(
            lead_response=lead_response,
            intake_response=_task_result(intake_task),
            phase_states=phase_states,
            workflow_phase_keys=workflow_phase_keys,
            phase_results={
                phase_name: _task_result(task)
                for phase_name, task in task_rows_by_phase.items()
            },
        )

        decision_compact = str(decision_source or "").strip()
        if len(decision_compact) > 4000:
            # Truncar en límite de párrafo/oración si es posible
            cutoff = decision_compact[:3980]
            last_para = cutoff.rfind("\n\n")
            last_sent = cutoff.rfind(". ")
            trim_at = last_para if last_para > 3000 else (last_sent if last_sent > 3000 else 3980)
            decision_compact = decision_compact[:trim_at].rstrip() + "\n\n[... respuesta parcial]"

        route_records: list[tuple[str, str, str, bool]] = []
        execution_steps = 0
        execution_steps_success = 0
        successful_checks_set: set[str] = set()
        for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
            event_type = str(record.get("event_type", "") or "")
            payload_dict = record.get("payload", {})
            if not isinstance(payload_dict, dict):
                continue
            route_task_id = str(payload_dict.get("task_id", "") or "")
            if route_task_id not in phase_task_set:
                continue
            if event_type == "task_execution":
                route_records.append(
                    (
                        str(payload_dict.get("provider", "-") or "-"),
                        str(payload_dict.get("model", "-") or "-"),
                        str(payload_dict.get("channel", "-") or "-"),
                        bool(payload_dict.get("success", False)),
                    )
                )
                continue
            if event_type == "execution_step":
                execution_steps += 1
                if bool(payload_dict.get("success", False)):
                    execution_steps_success += 1
                    check_type = _classify_check_from_command(
                        str(payload_dict.get("command", "") or "")
                    )
                    if check_type:
                        successful_checks_set.add(check_type)

        successful_checks = sorted(successful_checks_set)

        route_counts: dict[tuple[str, str, str], int] = {}
        successful_routes = 0
        for provider, model, channel, was_success in route_records:
            route_key = (provider, model, channel)
            route_counts[route_key] = int(route_counts.get(route_key, 0)) + 1
            if was_success:
                successful_routes += 1
        used_routes = sorted(
            [
                f"{provider}/{model} ({channel}) x{count}"
                for (provider, model, channel), count in route_counts.items()
            ]
        )
        execution_attempts = len(route_records)
        execution_success = successful_routes
        (
            execution_mode,
            placeholder_outputs,
            placeholder_output_ratio,
            output_result_count,
        ) = _assess_execution_mode(
            task_rows=list(task_rows_by_phase.values()),
            execution_steps=execution_steps,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
        )

        orch.event_logger.emit(
            "chat_execution_mode_assessed",
            {
                "task_id": task_root,
                "execution_mode": execution_mode,
                "placeholder_outputs": placeholder_outputs,
                "placeholder_output_ratio": round(placeholder_output_ratio, 4),
                "execution_steps": execution_steps,
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
                "live_mode_required": True,
            },
        )
        demo_fast_chat_active = _env_bool("AITEAM_CHAT_DEMO_FAST", default=False)
        decision_display = _presentable_decision_text(decision_compact) or decision_compact or "—"

        live_mode_required = _env_bool(
            "AITEAM_LIVE_MODE_REQUIRED",
            default=False,
        ) or _env_bool("AITEAM_REQUIRE_LIVE_MODE", default=False)
        evidence_gate_failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase=task_rows_by_phase,
            execution_steps=execution_steps,
            execution_steps_success=execution_steps_success,
            successful_checks=successful_checks,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            require_followup_artifact_delta=game_followup_requested,
            require_test_or_build_check=True,
        )
        if demo_fast_chat_active:
            evidence_gate_failures = []
        if continuation_requested:
            evidence_gate_failures = [
                f
                for f in evidence_gate_failures
                if not f.endswith("placeholder_output")
                and not f.endswith("no_execution_evidence")
                and not f.endswith("no_successful_execution_steps")
                and not f.endswith("missing_test_or_build_check")
            ]

        evidence_gate_applied = False
        if evidence_gate_failures:
            evidence_gate_applied = True
            # Emitir compliance_violation por cada fallo del evidence gate
            _FAILURE_REASON_MAP = {
                "missing_execution_plan": "missing_execution_plan_required",
                "no_execution_evidence": "no_execution_evidence",
                "no_successful_execution_steps": "no_successful_execution_steps",
                "missing_task": "build_phase_missing",
                "not_completed": "missing_execution_plan_required",
                "empty_result": "build_phase_empty_result",
                "placeholder_output": "build_phase_placeholder_output",
            }
            for failure in evidence_gate_failures:
                if not failure.startswith("build:"):
                    continue
                failure_code = failure.split(":", 1)[1] if ":" in failure else failure
                reason = _FAILURE_REASON_MAP.get(failure_code, failure_code)
                orch.event_logger.emit(
                    "compliance_violation",
                    {
                        "task_id": task_root,
                        "reason": reason,
                        "failure": failure,
                    },
                )

        lead_justification = ""
        if lead_result_task is not None:
            lead_justification = str(
                lead_result_task.metadata.get("decision_justification", "")
            )
        if not lead_justification:
            intake_task = orch.taskboard.get_task(phase_task_ids["lead_intake"])
            if intake_task is not None:
                lead_justification = str(
                    intake_task.metadata.get("decision_justification", "")
                )

        productivity_score, reasoning_score, productivity_status, next_action_hint = (
            _evaluate_chat_quality(
                decision_text=decision_source,
                justification_text=lead_justification,
                completed_tasks=completed_tasks,
                total_tasks=len(phase_task_ids),
                pending_tasks=pending_tasks,
                failed_tasks=len(failed_phases),
                execution_attempts=execution_attempts,
                execution_success=execution_success,
                execution_steps=execution_steps,
                successful_checks=successful_checks,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
            )
        )

        orch.event_logger.emit(
            "chat_quality_assessed",
            {
                "task_id": task_root,
                "productivity_score": productivity_score,
                "reasoning_score": reasoning_score,
                "productivity_status": productivity_status,
                "execution_attempts": execution_attempts,
                "execution_steps": execution_steps,
                "execution_steps_success": execution_steps_success,
                "execution_mode": execution_mode,
                "placeholder_outputs": placeholder_outputs,
                "successful_checks": successful_checks,
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
            },
        )

        participants_line = (
            ", ".join(role_participants) if role_participants else "none"
        )
        agents_line = (
            ", ".join(assignee_participants) if assignee_participants else "none"
        )
        used_line = ", ".join(used_routes[:5]) if used_routes else "none"
        done_line = ", ".join(done_phases) if done_phases else "none"
        pending_line = ", ".join(pending_phases) if pending_phases else "none"
        failed_line = ", ".join(failed_phases) if failed_phases else "none"
        request_line = _compact_text_line(payload.message, limit=180)
        if continuation_of and continuation_snapshot == "target_not_found":
            continuity_line = (
                f"requested target not found (continuation_of={continuation_of})"
            )
        elif continuation_of:
            continuity_line = f"yes (continuation_of={continuation_of}; carryover={continuation_snapshot or '-'})"
        elif continuation_requested:
            continuity_line = "requested, but no previous chat root found"
        elif previous_root:
            continuity_line = f"new run (latest_previous={str(previous_root.get('root_id', '')) or '-'})"
        else:
            continuity_line = "new run (no previous chat roots)"

        run_type = _detect_run_type(
            message=payload.message,
            phase_task_ids=phase_task_ids,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
        )
        orch.event_logger.emit(
            "chat_run_type_detected",
            {
                "task_id": task_root,
                "run_type": run_type,
                "productivity_score": productivity_score,
                "reasoning_score": reasoning_score,
                "phases": list(phase_task_ids.keys()),
            },
        )

        run_type_policy = resolve_run_type_policy(run_type, reasoning_score)
        productivity_threshold = run_type_policy.productivity_threshold
        _passes_by_reasoning = run_type_policy.passes_by_reasoning
        is_context_query = run_type_policy.is_context_query
        if is_context_query:
            orch.event_logger.emit(
                "chat_context_only_query",
                {
                    "task_id": task_root,
                    "run_type": run_type,
                    "message_preview": payload.message[:120],
                    "productivity_score": productivity_score,
                    "reasoning_score": reasoning_score,
                    "passes_by_reasoning": _passes_by_reasoning,
                },
            )
        _policy_outcome = evaluate_chat_policy(
            ChatPolicyInput(
                task_id=task_root,
                run_type=run_type,
                final_state=final_state,
                productivity_status=productivity_status,
                next_action_hint=next_action_hint,
                strict_mode=bool(payload.strict_mode),
                continuation_requested=bool(continuation_requested),
                allow_low_productivity_override=bool(payload.allow_low_productivity_override),
                demo_fast_chat_active=demo_fast_chat_active,
                lead_advisory_mode=lead_advisory_mode,
                live_mode_required=live_mode_required,
                execution_mode=execution_mode,
                execution_steps=execution_steps,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
                productivity_score=productivity_score,
                reasoning_score=reasoning_score,
                evidence_gate_failures=evidence_gate_failures,
            ),
            run_type_policy,
        )
        final_state = _policy_outcome.final_state
        productivity_status = _policy_outcome.productivity_status
        next_action_hint = _policy_outcome.next_action_hint
        live_mode_rejected = _policy_outcome.live_mode_rejected
        evidence_gate_applied = _policy_outcome.evidence_gate_applied
        strict_mode_applied = _policy_outcome.strict_mode_applied
        low_productivity_rejected = _policy_outcome.low_productivity_rejected
        low_productivity_override = _policy_outcome.low_productivity_override
        policy_review_required = _policy_outcome.policy_review_required
        policy_signals.extend(_policy_outcome.policy_signals)
        for _policy_event in _policy_outcome.events:
            orch.event_logger.emit(_policy_event.event_type, _policy_event.payload)

        if not lead_completed:
            orch.event_logger.emit(
                "chat_window_exhausted",
                {
                    "task_id": task_root,
                    "chat_mode": chat_mode,
                    "round_budget": round_budget,
                    "rounds_used": rounds_used,
                    "phase_states": phase_states,
                },
            )

        workflow_lines = "\n".join(f"- {phase}" for phase in workflow_phase_keys)
        user_facing_summary = _compose_user_facing_run_summary(
            task_root=task_root,
            request_line=request_line,
            continuation_line=continuity_line,
            mode=chat_mode,
            rounds_used=rounds_used,
            round_budget=round_budget,
            elapsed_ms=elapsed_ms,
            done_line=done_line,
            pending_line=pending_line,
            failed_line=failed_line,
            participants_line=participants_line,
            decision_compact=decision_compact,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            artifact_files=artifact_files,
            productivity_score=productivity_score,
            reasoning_score=reasoning_score,
            productivity_status=productivity_status,
            next_action_hint=next_action_hint,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
        )

        orch.mailbox.send(
            sender="team_lead",
            recipient="user",
            subject=f"Lead user summary: {task_root}",
            body=user_facing_summary,
            task_id=task_root,
        )
        orch.event_logger.emit(
            "chat_user_summary_published",
            {
                "task_id": task_root,
                "summary_chars": len(user_facing_summary),
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
            },
        )

        delegation_results_lines = (
            delegated_lines[:12] if delegated_lines else ["- none"]
        )
        if delegated_placeholder_count > 0:
            delegation_results_lines = [
                f"- respuestas demo detectadas: {delegated_placeholder_count}"
                if demo_fast_chat_active
                else f"- placeholders/simulados detectados: {delegated_placeholder_count}"
            ] + delegation_results_lines

        execution_mode_label = (
            "demo"
            if demo_fast_chat_active and execution_mode == "simulated"
            else execution_mode
        )
        output_count_label = (
            "demo_outputs" if demo_fast_chat_active else "placeholder_outputs"
        )

        # ── Formato de respuesta: usuario primero, debug después ──────────────
        # El mensaje al usuario va AL PRINCIPIO para que no quede enterrado.
        # El bloque técnico va al final, condensado en pocas líneas.
        verbose_lead = os.getenv("AITEAM_VERBOSE_LEAD_SUMMARY", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }

        # Bloque de estado técnico compacto (1-3 líneas)
        status_icon = "✓" if final_state == "completed" else ("✗" if final_state == "rejected" else "~")
        compact_status = (
            f"{status_icon} {final_state} | rounds={rounds_used}/{round_budget} | "
            f"elapsed={elapsed_ms}ms | routes={successful_routes}/{len(route_records)} ok"
        )
        if artifact_created or artifact_modified:
            compact_status += f" | artifacts +{artifact_created}/~{artifact_modified}"
        if failed_line and failed_line != "none":
            compact_status += f"\nFallido: {failed_line}"
        if next_action_hint and final_state != "completed":
            compact_status += f"\nSiguiente: {next_action_hint}"
        elif next_action_hint and policy_review_required:
            compact_status += f"\nPolicy review: {next_action_hint}"

        response_lines = [
            "Lead summary:",
            user_facing_summary,
            "",
            "Workflow phases:",
            workflow_lines or "- none",
        ]
        if lead_advisory_mode:
            response_lines.extend(
                [
                    "",
                    f"Advisory mode: {lead_advisory_reason or 'El Lead decidió cerrar en modo advisory.'}",
                ]
            )
        if (
            bool(payload.strict_mode)
            or live_mode_required
            or evidence_gate_applied
            or low_productivity_rejected
            or policy_review_required
            or policy_signals
        ):
            _strict_mode_label = (
                "blocked_close"
                if strict_mode_applied
                else (
                    "signaled"
                    if "strict_mode_requires_more_evidence" in policy_signals
                    else ("on" if payload.strict_mode else "off")
                )
            )
            _live_mode_label = (
                "rejected"
                if live_mode_rejected
                else (
                    "required_signal"
                    if "live_mode_required_non_live" in policy_signals
                    else ("required" if live_mode_required else "off")
                )
            )
            _evidence_gate_label = (
                "failed_signal"
                if evidence_gate_applied and "evidence_gate_failed" in policy_signals
                else ("rejected" if evidence_gate_applied else "pass")
            )
            response_lines.extend(
                [
                    "",
                    f"Strict mode: {_strict_mode_label}",
                    f"Live mode gate: {_live_mode_label}",
                    f"Evidence gate: {_evidence_gate_label}",
                ]
            )
            if policy_review_required:
                response_lines.append("Policy review required: yes")
            if policy_signals:
                response_lines.append(
                    f"Policy signals: {', '.join(policy_signals)}"
                )
        if artifact_files:
            response_lines.extend(
                [
                    "",
                    f"Archivos: {', '.join(artifact_files[:12])}",
                ]
            )
        # Delegation results solo cuando hay algo no trivial
        non_trivial_delegation = any(
            "sin resultado" not in ln and "blocked" not in ln
            for ln in delegation_results_lines
        )
        if non_trivial_delegation and delegation_results_lines != ["- none"]:
            response_lines.extend(
                [
                    "",
                    "Delegation results:",
                    "\n".join(delegation_results_lines),
                ]
            )
        # Estado técnico compacto al final
        response_lines.extend(["", "---", compact_status])

        _strict_mode_label = (
            "blocked_close"
            if strict_mode_applied
            else (
                "signaled"
                if "strict_mode_requires_more_evidence" in policy_signals
                else ("on" if payload.strict_mode else "off")
            )
        )
        _live_mode_label = (
            "rejected"
            if live_mode_rejected
            else (
                "required_signal"
                if "live_mode_required_non_live" in policy_signals
                else ("required" if live_mode_required else "off")
            )
        )
        _evidence_gate_label = (
            "failed_signal"
            if evidence_gate_applied and "evidence_gate_failed" in policy_signals
            else ("rejected" if evidence_gate_applied else "pass")
        )
        _low_productivity_label = (
            "rejected"
            if low_productivity_rejected
            else (
                "signaled"
                if "low_productivity_below_threshold" in policy_signals
                else (
                    "override"
                    if low_productivity_override and productivity_score < productivity_threshold
                    else "active"
                )
            )
        )

        # Bloque verbose completo solo cuando se pide explícitamente
        if verbose_lead:
            response_lines.extend([
                "",
                "=== DEBUG LEAD SUMMARY ===",
                f"Status={final_state} mode={chat_mode} rounds={rounds_used}/{round_budget} elapsed={elapsed_ms}ms",
                f"Request: {request_line}",
                f"Continuity: {continuity_line}",
                f"Participants (roles): {participants_line}",
                f"Participants (agents): {agents_line}",
                f"Decision: {decision_display}",
                f"Done: {done_line}",
                f"Pending: {pending_line}",
                f"Failed: {failed_line}",
                f"Used: {used_line}",
                f"Route attempts: {len(route_records)} (success={successful_routes})",
                f"Execution steps: {execution_steps} (success={execution_steps_success})",
                f"Execution mode: {execution_mode_label} ({output_count_label}={placeholder_outputs}/{max(1, output_result_count)})",
                f"Live mode gate: {_live_mode_label}",
                f"Checks passed: {', '.join(successful_checks) if successful_checks else 'none'}",
                f"Evidence gate: {_evidence_gate_label} ({', '.join(evidence_gate_failures) if evidence_gate_failures else 'ok'})",
                f"Artifacts: created={artifact_created} modified={artifact_modified}",
                f"Quality: productivity={productivity_score}/100 ({productivity_status}) reasoning={reasoning_score}/100",
                f"Action hint: {next_action_hint}",
                f"Strict mode: {_strict_mode_label}",
                f"Low productivity gate: {_low_productivity_label}",
                f"Advisory mode: {'on' if lead_advisory_mode else 'off'} ({lead_advisory_reason or '-'})",
                f"Policy review required: {'yes' if policy_review_required else 'no'}",
                f"Policy signals: {', '.join(policy_signals) if policy_signals else 'none'}",
                f"Auto-extended rounds: +{auto_extended_rounds}",
                "",
                "Workflow phases:",
                workflow_lines,
            ])

        merged_response = _limit_chat_response("\n".join(response_lines))
        _specialist_insights = _load_chat_specialist_insights(runtime_dir, task_root)

        _token_queue.put(("done", None))
        result = TeamChatResponse(
            task_id=task_root,
            role=Role.TEAM_LEAD.value,
            state=final_state,
            response=merged_response,
            decision_justification=lead_justification,
            elapsed_ms=elapsed_ms,
            lead_task_id=lead_task_id,
            delegated_task_ids=delegated_task_ids,
            phase_task_ids=phase_task_ids,
            chat_mode=chat_mode,
            round_budget=round_budget,
            rounds_used=rounds_used,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            continuation_requested=continuation_requested,
            continuation_of=continuation_of,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            artifact_files=artifact_files,
            productivity_score=productivity_score,
            reasoning_score=reasoning_score,
            productivity_status=productivity_status,
            execution_attempts=execution_attempts,
            execution_success=execution_success,
            execution_steps=execution_steps,
            execution_steps_success=execution_steps_success,
            successful_checks=successful_checks,
            successful_check_count=len(successful_checks),
            live_mode_required=live_mode_required,
            live_mode_rejected=live_mode_rejected,
            advisory_mode=lead_advisory_mode,
            advisory_reason=lead_advisory_reason,
            policy_review_required=policy_review_required,
            validation_owner=CHAT_VALIDATION_OWNER,
            policy_signals=policy_signals,
            phase_evidence_plan=_coerce_phase_evidence_plan(
                _ws.get("phase_evidence_plan", _chat_run_state.phase_evidence_plan)
            ),
            delegate_batches=_coerce_delegate_batches(
                _ws.get("delegate_batches", [])
            ),
            delegate_economics=dict(
                _ws.get("delegate_economics_summary", {}) or {}
            ),
            specialist_reports=list(
                _specialist_insights.get("specialist_reports", []) or []
            ),
            specialist_report_summary=dict(
                _specialist_insights.get("specialist_report_summary", {}) or {}
            ),
            next_action_hint=next_action_hint,
            strict_mode=bool(payload.strict_mode),
            strict_mode_applied=strict_mode_applied,
            auto_extended_rounds=auto_extended_rounds,
            productivity_threshold=productivity_threshold,
            low_productivity_rejected=low_productivity_rejected,
            low_productivity_override=low_productivity_override,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
            placeholder_output_ratio=round(placeholder_output_ratio, 4),
            evidence_gate_applied=evidence_gate_applied,
            evidence_gate_failures=evidence_gate_failures,
        )
        return result

    async def _event_stream():
        import asyncio as _asyncio

        _chat_fut = _asyncio.get_event_loop().run_in_executor(None, _run_chat)

        while True:
            try:
                item = await _asyncio.to_thread(lambda: _token_queue.get(timeout=2.0))
                event_type, data = item
                if event_type == "token_chunk":
                    yield f"event: token_chunk\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                elif event_type == "agent_event":
                    evt_name = (
                        data.get("type", "agent_event")
                        if isinstance(data, dict)
                        else "agent_event"
                    )
                    yield f"event: {evt_name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                elif event_type == "done":
                    # _run_chat already finished — await the future for the result
                    try:
                        result = await _asyncio.wait_for(
                            _asyncio.wrap_future(_chat_fut), timeout=5.0
                        )
                        result_dict = (
                            result.model_dump() if hasattr(result, "model_dump") else {}
                        )
                        # M5: truncar el campo "response" en el evento result para evitar
                        # truncacion SSE en respuestas largas. El frontend ya usa el stream
                        # acumulado (token_chunk) para el contenido principal; "response"
                        # solo se usa como fallback cuando accumulated < 80 chars.
                        _resp_full = result_dict.get("response", "")
                        if len(_resp_full) > 2000:
                            result_dict = dict(result_dict)
                            result_dict["response"] = _resp_full[:2000]
                            result_dict["response_truncated"] = True
                        yield f"event: result\ndata: {json.dumps(result_dict, ensure_ascii=False, default=str)}\n\n"
                    except Exception as exc:
                        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    break
            except Exception:
                # timeout in queue.get (queue.Empty) — send keepalive or recover if done
                if _chat_fut.done():
                    try:
                        result = _chat_fut.result()
                        result_dict = (
                            result.model_dump() if hasattr(result, "model_dump") else {}
                        )
                        _resp_full = result_dict.get("response", "")
                        if len(_resp_full) > 2000:
                            result_dict = dict(result_dict)
                            result_dict["response"] = _resp_full[:2000]
                            result_dict["response_truncated"] = True
                        yield f"event: result\ndata: {json.dumps(result_dict, ensure_ascii=False, default=str)}\n\n"
                    except Exception as exc:
                        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    break
                yield "event: keepalive\ndata: {}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


def _build_resume_stream(
    pending_state: dict,
    clarification: str,
    runtime_dir: Path,
) -> "StreamingResponse":
    """E7-D4: Reanuda un run pausado mid-run (type == 'mid_run') con la respuesta del usuario.

    Carga el orquestador desde disco (tiene el taskboard persistido con la tarea
    en WAITING_USER), inyecta la respuesta, y ejecuta las fases restantes con
    SSE streaming idéntico al flujo principal.
    """
    import queue as _qmod_resume

    _resume_queue: _qmod_resume.Queue = _qmod_resume.Queue()

    task_root = pending_state["task_root"]
    waiting_task_id = pending_state["waiting_task_id"]
    waiting_phase = pending_state.get("waiting_phase", "")
    question = pending_state.get("question", "")
    _chat_run_state_payload = pending_state.get("chat_run_state")
    _chat_run_state = None
    if isinstance(_chat_run_state_payload, dict):
        try:
            _chat_run_state = ChatRunState.from_dict(_chat_run_state_payload)
        except Exception:
            _chat_run_state = None

    phase_task_ids: dict[str, str] = (
        _chat_run_state.phase_task_ids
        if _chat_run_state is not None
        else pending_state.get("phase_task_ids", {})
    )
    remaining_budget = max(
        1,
        int(
            pending_state.get(
                "remaining_budget",
                _chat_run_state.round_budget if _chat_run_state is not None else 5,
            )
        ),
    )
    preferred_role_str = (
        _chat_run_state.preferred_role.value
        if _chat_run_state is not None
        else pending_state.get("preferred_role", "engineer")
    )
    original_message = pending_state.get("original_message", "")

    def _run_resume() -> "TeamChatResponse":
        orch_resume = build_default_orchestrator(
            runtime_dir=runtime_dir,
            browser_mode="basic",
            environment="dev",
        )

        def _on_chunk_r(task_id: str, chunk: str) -> None:
            disp = _stream_display_chunk(task_id, chunk)
            if disp:
                _resume_queue.put(
                    ("token_chunk", {"task_id": task_id, "chunk": disp})
                )

        orch_resume.token_chunk_callback = _on_chunk_r

        def _on_agent_event_r(event: dict) -> None:
            _resume_queue.put(("agent_event", event))

        orch_resume.agent_event_callback = _on_agent_event_r

        waiting_task = orch_resume.taskboard.get_task(waiting_task_id)
        if waiting_task is None or waiting_task.state != TaskState.WAITING_USER:
            # Estado inconsistente — devolver respuesta de error
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role_str,
                state="failed",
                response=(
                    f"No se encontró la tarea pausada '{waiting_task_id}'. "
                    "Es posible que el estado haya sido reiniciado."
                ),
                decision_justification="mid_run_resume_task_not_found",
                elapsed_ms=0,
                lead_task_id=phase_task_ids.get("lead_intake", ""),
                delegated_task_ids=[],
                phase_task_ids=phase_task_ids,
                phase_evidence_plan=(
                    _chat_run_state.phase_evidence_plan if _chat_run_state is not None else {}
                ),
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
            )

        # Inyectar respuesta del usuario en la descripción de la tarea pausada
        _inject = (
            f"\n\n[Respuesta del usuario a tu pregunta previa '{question}': "
            f"{clarification}]"
        )
        waiting_task.description = waiting_task.description + _inject
        orch_resume.taskboard.retry_task(
            waiting_task_id,
            reason=f"mid_run_clarification_injected (phase={waiting_phase})",
        )
        orch_resume.event_logger.emit(
            "lcp_directive_applied",
            {
                "task_id": task_root,
                "directive": "mid_run_resume",
                "phase": waiting_phase,
                "question_len": len(question),
                "answer_len": len(clarification),
            },
        )

        started_r = time.perf_counter()
        orch_resume.run_until_idle(max_rounds=remaining_budget)
        elapsed_r = int((time.perf_counter() - started_r) * 1000)

        # Recoger resultado: lead_close si completó, o el output de la fase que pausó
        _ws_r = orch_resume._get_workflow_state(task_root)
        _phase_outputs_r = _ws_r.get("phase_outputs", {})
        lead_close_output = _phase_outputs_r.get("lead_close", "")
        waiting_phase_output = _phase_outputs_r.get(waiting_phase, "")
        final_response = lead_close_output or waiting_phase_output or (
            f"Run reanudado desde fase '{waiting_phase}'. "
            f"Respuesta original: {original_message[:200]}"
        )
        final_response = _strip_lcp_directives(final_response)

        # Estado de fases para la respuesta
        _phase_states: dict[str, str] = {}
        for pname, pid in phase_task_ids.items():
            t = orch_resume.taskboard.get_task(pid)
            _phase_states[pname] = t.state.value if t else "missing"
        _resume_specialist_insights = _load_chat_specialist_insights(runtime_dir, task_root)

        return TeamChatResponse(
            task_id=task_root,
            role=preferred_role_str,
            state="completed",
            response=final_response,
            decision_justification=(
                f"Run reanudado tras aclaración de fase '{waiting_phase}'."
            ),
            elapsed_ms=elapsed_r,
            lead_task_id=(
                _chat_run_state.lead_task_id
                if _chat_run_state is not None
                else phase_task_ids.get("lead_intake", "")
            ),
            delegated_task_ids=(
                _chat_run_state.delegated_task_ids
                if _chat_run_state is not None
                else [
                    pid for pname, pid in phase_task_ids.items()
                    if pname not in ("lead_intake", "lead_close")
                ]
            ),
            phase_task_ids=phase_task_ids,
            phase_states=_phase_states,
            phase_evidence_plan=_coerce_phase_evidence_plan(
                _ws_r.get(
                    "phase_evidence_plan",
                    _chat_run_state.phase_evidence_plan if _chat_run_state is not None else {},
                )
            ),
            delegate_batches=_coerce_delegate_batches(
                _ws_r.get("delegate_batches", [])
            ),
            delegate_economics=dict(
                _ws_r.get("delegate_economics_summary", {}) or {}
            ),
            specialist_reports=list(
                _resume_specialist_insights.get("specialist_reports", []) or []
            ),
            specialist_report_summary=dict(
                _resume_specialist_insights.get("specialist_report_summary", {}) or {}
            ),
        )

    async def _resume_event_stream():
        import asyncio as _aio_r

        _fut_r = _aio_r.get_event_loop().run_in_executor(None, _run_resume)
        while True:
            try:
                item = await _aio_r.to_thread(
                    lambda: _resume_queue.get(timeout=2.0)
                )
                etype, data = item
                if etype == "token_chunk":
                    yield f"event: token_chunk\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                elif etype == "agent_event":
                    ename = (
                        data.get("type", "agent_event") if isinstance(data, dict)
                        else "agent_event"
                    )
                    yield f"event: {ename}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
            except Exception:
                if _fut_r.done():
                    try:
                        result_r = _fut_r.result()
                        result_dict_r = (
                            result_r.model_dump()
                            if hasattr(result_r, "model_dump") else {}
                        )
                        _resp_r = result_dict_r.get("response", "")
                        if len(_resp_r) > 2000:
                            result_dict_r = dict(result_dict_r)
                            result_dict_r["response"] = _resp_r[:2000]
                            result_dict_r["response_truncated"] = True
                        yield f"event: result\ndata: {json.dumps(result_dict_r, ensure_ascii=False, default=str)}\n\n"
                    except Exception as exc:
                        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    break
                yield "event: keepalive\ndata: {}\n\n"

    return StreamingResponse(_resume_event_stream(), media_type="text/event-stream")


class ClarifyRequest(BaseModel):
    chat_id: str
    clarification: str


@app.post("/api/aiteam/chat/clarify")
async def post_aiteam_chat_clarify(payload: ClarifyRequest, request: Request):
    """Reanuda un run pausado con [CLARIFY] inyectando la respuesta del usuario.

    Carga el estado pendiente, construye un nuevo TeamChatRequest con la
    clarificación añadida al mensaje original, y ejecuta el chat normalmente.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"

    task_root = _normalize_task_root(payload.chat_id)
    pending_file = runtime_dir / f"pending_clarification_{task_root}.json"

    if not pending_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No hay clarificación pendiente para el chat {payload.chat_id}",
        )

    try:
        pending_state = json.loads(pending_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error leyendo estado pendiente: {exc}")

    # ── E7-D4: Distinguir pausa de lead_intake vs pausa mid-run ──────────────
    pending_type = pending_state.get("type", "lead_intake")

    # Eliminar estado pendiente antes de reanudar (en ambos paths)
    pending_file.unlink(missing_ok=True)

    if pending_type == "mid_run":
        # Reanudar el run desde la tarea pausada sin reiniciar el workflow completo
        return _build_resume_stream(pending_state, payload.clarification, runtime_dir)

    # ── Path original: pausa de lead_intake ─────────────────────────────────
    original_payload_data = pending_state.get("original_payload", {})
    original_message = pending_state.get("original_message", original_payload_data.get("message", ""))
    question = pending_state.get("question", "")

    # Inyectar la respuesta del usuario en el mensaje original
    augmented_message = (
        f"{original_message}\n\n"
        f"[Respuesta del usuario a tu pregunta previa '{question}': "
        f"{payload.clarification}]"
    )

    # Construir nuevo request reutilizando los parámetros originales
    new_payload = TeamChatRequest(
        message=augmented_message,
        role=original_payload_data.get("role", "engineer"),
        complexity=original_payload_data.get("complexity", "medium"),
        criticality=original_payload_data.get("criticality", "medium"),
        mode=original_payload_data.get("mode", "sprint5"),
        max_rounds=original_payload_data.get("max_rounds"),
        client_task_id=original_payload_data.get("client_task_id", ""),
        strict_mode=original_payload_data.get("strict_mode", False),
        auto_extend_weak_runs=original_payload_data.get("auto_extend_weak_runs", True),
        allow_low_productivity_override=original_payload_data.get(
            "allow_low_productivity_override", False
        ),
    )

    # Reutilizar el endpoint principal (en hilo separado para no bloquear)
    return await post_aiteam_chat(new_payload, request)


@app.get("/api/aiteam/chat/progress/{task_id}", response_model=TeamChatProgressResponse)
async def get_aiteam_chat_progress(task_id: str, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    normalized_root = _normalize_task_root(task_id)
    if not normalized_root:
        return TeamChatProgressResponse(task_id="", exists=False)
    if not runtime_dir.exists():
        return TeamChatProgressResponse(task_id=normalized_root, exists=False)
    return await asyncio.to_thread(_build_chat_progress, runtime_dir, normalized_root)


@app.get("/api/aiteam/chat/load/{task_id}")
async def get_aiteam_chat_load(task_id: str, request: Request):
    """Devuelve los mensajes reconstruidos de un chat pasado (user + lead response)."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    normalized_root = _normalize_task_root(task_id)
    if not normalized_root or not runtime_dir.exists():
        return {"task_id": normalized_root or task_id, "messages": []}

    def _load():
        tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
        roots = _group_chat_roots(tasks_payload)
        row = roots.get(normalized_root)
        if not row:
            return {"task_id": normalized_root, "messages": []}
        messages = []
        user_msg = str(row.get("user_message", "") or "").strip()
        if user_msg:
            messages.append({"sender": "user", "text": user_msg})
        lead_resp = str(row.get("lead_close_result", "") or "").strip()
        if lead_resp:
            messages.append({"sender": "team", "text": lead_resp})
        phase_states = row.get("phase_states", {})
        state = "completed" if phase_states.get("lead_close") == "completed" else "partial"
        return {"task_id": normalized_root, "state": state, "messages": messages}

    return await asyncio.to_thread(_load)


# ── Background chat runs with SSE streaming ───────────────────

_background_runs: dict[str, dict] = {}  # task_root → {status, progress_queue, result}
_background_runs_lock = threading.Lock()


@app.post("/api/aiteam/chat/async")
async def post_aiteam_chat_async(payload: TeamChatRequest, request: Request):
    """Inicia un chat en background y retorna el task_id inmediatamente.

    Usar GET /api/aiteam/chat/stream/{task_id} para recibir progreso via SSE.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    import queue as queue_module

    task_root = f"CHAT-{uuid.uuid4().hex[:8].upper()}"
    progress_queue = queue_module.Queue()

    with _background_runs_lock:
        _background_runs[task_root] = {
            "status": "running",
            "progress_queue": progress_queue,
            "result": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def _run_bg():
        try:
            from aiteam.cli import build_default_orchestrator
            from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask

            role_map = {
                "team_lead": Role.TEAM_LEAD,
                "lead": Role.TEAM_LEAD,
                "researcher": Role.RESEARCHER,
                "engineer": Role.ENGINEER,
                "reviewer": Role.REVIEWER,
                "qa": Role.QA,
            }
            complexity_map = {
                "low": Complexity.LOW,
                "medium": Complexity.MEDIUM,
                "high": Complexity.HIGH,
            }
            criticality_map = {
                "low": Criticality.LOW,
                "medium": Criticality.MEDIUM,
                "high": Criticality.HIGH,
            }

            preferred_role = role_map.get(payload.role.strip().lower(), Role.ENGINEER)
            complexity = complexity_map.get(
                payload.complexity.strip().lower(), Complexity.MEDIUM
            )
            criticality = criticality_map.get(
                payload.criticality.strip().lower(), Criticality.MEDIUM
            )

            orch = build_default_orchestrator(runtime_dir=runtime_dir)

            # Wire token streaming callback → progress_queue
            def _on_token_chunk(task_id: str, chunk: str) -> None:
                display_chunk = _stream_display_chunk(task_id, chunk)
                if display_chunk:
                    progress_queue.put(
                        ("token_chunk", {"task_id": task_id, "chunk": display_chunk})
                    )

            orch.token_chunk_callback = _on_token_chunk

            round_budget = min(max(1, payload.max_rounds or 5), 20)

            # Submit tasks (simplified — lead_intake only for async)
            task = WorkTask(
                task_id=f"{task_root}::build",
                title=f"Build: {payload.message[:80]}",
                description=payload.message,
                role=preferred_role,
                complexity=complexity,
                criticality=criticality,
                metadata={"required_capabilities": ["coding", "analysis"]},
            )
            orch.submit_task(task)

            # Run with progress
            for progress in orch.run_until_idle_with_progress(max_rounds=round_budget):
                progress["task_root"] = task_root
                progress_queue.put(("progress", progress))

            # Collect result
            tasks = orch.taskboard.list_tasks()
            completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
            failed = sum(1 for t in tasks if t.state == TaskState.FAILED)
            results = []
            for t in tasks:
                result_text = str(t.metadata.get("result", ""))
                if result_text:
                    results.append(result_text[:500])

            final = {
                "task_root": task_root,
                "status": "completed",
                "tasks_total": len(tasks),
                "tasks_completed": completed,
                "tasks_failed": failed,
                "result_summary": "\n---\n".join(results)[:3000],
            }
            progress_queue.put(("done", final))

            with _background_runs_lock:
                if task_root in _background_runs:
                    _background_runs[task_root]["status"] = "completed"
                    _background_runs[task_root]["result"] = final

        except Exception as exc:
            error_result = {
                "task_root": task_root,
                "status": "failed",
                "error": str(exc)[:500],
            }
            progress_queue.put(("error", error_result))
            with _background_runs_lock:
                if task_root in _background_runs:
                    _background_runs[task_root]["status"] = "failed"
                    _background_runs[task_root]["result"] = error_result

    thread = threading.Thread(target=_run_bg, daemon=True)
    thread.start()

    return {
        "task_root": task_root,
        "status": "running",
        "stream_url": f"/api/aiteam/chat/stream/{task_root}",
    }


@app.get("/api/aiteam/chat/stream/{task_root}")
async def stream_chat_progress(task_root: str, request: Request):
    """SSE endpoint para recibir progreso de un chat en background."""
    _require_api_auth_request(request)

    with _background_runs_lock:
        run = _background_runs.get(task_root)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No background run for {task_root}"
        )

    progress_queue = run["progress_queue"]

    async def event_stream():
        import queue as queue_module

        while True:
            try:
                event_type, data = await asyncio.to_thread(
                    progress_queue.get, timeout=30
                )
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                if event_type in ("done", "error"):
                    break
            except Exception:
                # Timeout or queue empty — send keepalive
                yield f"event: keepalive\ndata: {{}}\n\n"
                # Check if run is still active
                with _background_runs_lock:
                    current = _background_runs.get(task_root, {})
                if current.get("status") in ("completed", "failed"):
                    break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/aiteam/chat/async/{task_root}")
async def get_async_chat_status(task_root: str, request: Request):
    """Consulta el estado de un chat async."""
    _require_api_auth_request(request)
    with _background_runs_lock:
        run = _background_runs.get(task_root)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No background run for {task_root}"
        )
    return {
        "task_root": task_root,
        "status": run["status"],
        "started_at": run.get("started_at", ""),
        "result": run.get("result"),
    }


@app.get("/api/aiteam/operator/timeline", response_model=OperatorTimelineResponse)
async def get_aiteam_operator_timeline(
    request: Request,
    task_id: str = "",
    limit: int = 120,
    key_only: bool = True,
):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    if not runtime_dir.exists():
        return OperatorTimelineResponse()
    return await asyncio.to_thread(
        _build_operator_timeline,
        runtime_dir,
        task_id=task_id,
        limit=limit,
        key_only=key_only,
    )


@app.get("/api/aiteam/mailbox/inbox")
async def get_mailbox_inbox(request: Request):
    """Query agent mailbox with optional filters."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = Path(workspace) / "runtime"
    mailbox_path = runtime_dir / "mailbox.jsonl"
    if not mailbox_path.exists():
        return {"messages": [], "total": 0, "unread": 0}

    from aiteam.mailbox import Mailbox

    mb = Mailbox(mailbox_path)
    recipient = request.query_params.get("recipient", "")
    sender_filter = request.query_params.get("sender", "")
    task_filter = request.query_params.get("task_id", "")
    unread_only = request.query_params.get("unread_only", "false").lower() in (
        "true",
        "1",
    )
    limit = min(int(request.query_params.get("limit", "50")), 200)

    messages = mb.inbox_query(
        recipient=recipient,
        unread_only=unread_only,
        sender=sender_filter or None,
        task_id=task_filter or None,
        limit=limit,
    )
    total = len(mb.list_messages(recipient=recipient or None))
    unread = mb.unread_count(recipient) if recipient else 0

    return {
        "messages": [
            {
                "message_id": m.message_id,
                "timestamp": m.timestamp,
                "sender": m.sender,
                "recipient": m.recipient,
                "subject": m.subject,
                "body": m.body[:500],
                "task_id": m.task_id,
            }
            for m in messages
        ],
        "total": total,
        "unread": unread,
    }


@app.get("/api/fs/tree")
async def get_fs_tree(request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)

    def build_tree(path: Path):
        name = path.name
        if name in [
            ".git",
            "__pycache__",
            "venv",
            ".pytest_cache",
            ".aiteam_snapshots",
            "node_modules",
        ]:
            return None
        try:
            if path.is_file():
                return {
                    "name": name,
                    "path": str(path.relative_to(workspace).as_posix()),
                    "type": "file",
                }
            elif path.is_dir():
                children = []
                for child in path.iterdir():
                    node = build_tree(child)
                    if node:
                        children.append(node)
                # Sort alphabetically, directories first
                children.sort(
                    key=lambda x: (
                        0 if x["type"] == "directory" else 1,
                        x["name"].lower(),
                    )
                )
                return {
                    "name": name,
                    "path": str(path.relative_to(workspace).as_posix()),
                    "type": "directory",
                    "children": children,
                }
        except Exception:
            return None

    return build_tree(workspace)


class FileContent(BaseModel):
    content: str


@app.get("/api/fs/file")
async def read_file(path: str, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    target = _safe_workspace_target(workspace, path)
    if target is None:
        raise HTTPException(status_code=400, detail="Path is outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"content": target.read_text(encoding="utf-8")}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except Exception as e:
        logger.exception("Error reading file: %s", path)
        raise HTTPException(status_code=500, detail="Error reading file")


@app.put("/api/fs/file")
async def write_file(path: str, payload: FileContent, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    target = _safe_workspace_target(workspace, path)
    if target is None:
        raise HTTPException(status_code=400, detail="Path is outside workspace")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")
        return {"success": True}
    except PermissionError:
        raise HTTPException(status_code=403, detail="File is read-only or in use")
    except Exception as e:
        logger.exception("Error writing file: %s", path)
        raise HTTPException(status_code=500, detail="Error writing file")


@app.websocket("/api/terminal")
async def terminal_endpoint(websocket: WebSocket):
    global active_pty
    header_map = {k.lower(): v for k, v in websocket.headers.items()}
    query_api_key = str(websocket.query_params.get("api_key", "") or "").strip()
    query_workspace_path = str(
        websocket.query_params.get("workspace_path", "") or ""
    ).strip()
    if query_api_key:
        header_map.setdefault("x-api-key", query_api_key)
    if query_workspace_path:
        header_map["x-workspace-path"] = query_workspace_path
    logger.debug(
        "WebSocket connection request to /api/terminal from %s",
        websocket.client.host if websocket.client else "unknown",
    )
    # Temporarily bypass auth for debugging if requested by localhost
    is_authorized = _is_authorized(header_map)
    if not is_authorized:
        logger.debug(
            "WebSocket auth failed for header_map keys: %s", list(header_map.keys())
        )
        # Bypass for local dev
        if websocket.client and websocket.client.host in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            logger.debug("Bypassing auth for local connection")
            is_authorized = True

    if not is_authorized:
        await websocket.close(code=1008)
        return
    workspace = _workspace_from_header_map(
        header_map, get_current_workspace(), PROJECT_ROOT
    )
    logger.debug("WebSocket accepted for workspace: %s", workspace)
    await websocket.accept()
    if PTY is None:
        await websocket.send_text(
            "Error: pywinpty is not installed on this system.\r\n"
        )
        await websocket.close()
        return

    pty = PTY(80, 24)
    active_pty = pty
    # Spawn shell inside the workspace (cross-platform)
    _shell = "powershell.exe" if sys.platform == "win32" else "bash"
    if sys.platform == "win32":
        # Check standard location as fallback if powershell.exe not in PATH
        std_ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        if not any(
            Path(p).joinpath("powershell.exe").exists()
            for p in os.environ.get("PATH", "").split(os.pathsep)
        ):
            if Path(std_ps).exists():
                _shell = std_ps

    try:
        pty.spawn(_shell, cwd=str(workspace))
    except Exception as e:
        logger.exception("Failed to spawn shell %s in %s", _shell, workspace)
        await websocket.send_text(f"\r\nError: Failed to spawn shell {_shell}. {e}\r\n")
        await websocket.close()
        return

    async def read_pty():
        try:
            while pty.isalive():
                # Read from PTY in a separate thread so it doesn't block the async event loop
                data = await asyncio.to_thread(pty.read, 4096)
                if data:
                    await websocket.send_text(data)
                else:
                    await asyncio.sleep(0.01)
        except Exception as e:
            logger.error("PTY read error: %s", e)

    task = asyncio.create_task(read_pty())
    try:
        while True:
            message = await websocket.receive_text()
            # If the user sends a resize payload like '{"type":"resize","cols":100,"rows":30}'
            if message.startswith('{"type":"resize"'):
                try:
                    payload = json.loads(message)
                    cols = payload.get("cols", 80)
                    rows = payload.get("rows", 24)
                    pty.set_size(cols, rows)
                except Exception:
                    pass
            else:
                pty.write(message)
    except WebSocketDisconnect:
        logger.info("Client disconnected from terminal")
    finally:
        if active_pty == pty:
            active_pty = None
        task.cancel()
        pty.close()
