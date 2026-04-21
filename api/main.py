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

import subprocess
import threading
import sys
import json as std_json

from api.chat_logic import (
    _detect_run_type,
    _env_bool,
    _extract_chat_root_from_message,
    _is_advisory_context_phase_name,
    _is_advisory_planning_phase_name,
    _is_build_like_phase_name,
    _is_context_only_query,
    _is_continuation_message,
    _is_qa_like_phase_name,
    _is_review_like_phase_name,
    _normalize_chat_mode,
    _normalize_task_root,
    _default_implicit_continuation_source,
    _recent_chat_roots,
    _resolve_chat_round_budget,
    _resolve_task_root,
    _safe_int_value,
)
from api.chat_delegate import (
    _aggregate_delegate_results,
    _build_delegate_phase_contract,
    _build_delegate_request,
    _delegate_catalog_capabilities,
    _delegate_quorum_target,
    _delegate_report_contract,
    _delegate_specialist_plan,
    _delegate_specialist_targets,
    _estimate_delegate_batch_economics,
    _execute_delegate_request,
    _extract_delegate_request,
    _extract_evidence_plan,
    _is_delegate_phase_name,
    _is_supporting_control_phase,
    _resolve_delegate_assignments,
    _resolve_delegate_plan,
    _resolve_delegate_round_budget,
    _resolve_delegate_rewiring,
    _structured_evidence_specs_for_phase,
    _summarize_delegate_economics,
)
from api.chat_models import (
    ClarifyRequest,
    FileContent,
    NewProjectRequest,
    NotebookLMSyncRequest,
    OperatorTimelineItem,
    OperatorTimelineResponse,
    TeamChatProgressResponse,
    TeamChatRequest,
    TeamChatResponse,
    WorkspacePath,
)
from api.chat_observability import (
    _build_chat_progress,
    _build_operator_timeline,
    _coerce_lead_close_policy,
    _coerce_phase_contracts,
    _coerce_delegate_batches,
    _coerce_phase_evidence_plan,
)
from api.chat_preplan import (
    _build_context_curator_prompt,
    _build_curated_context_block,
    _build_preplan_signal_block,
    _context_project_key,
    _detect_preplan_surface_hints,
    _estimate_preplan_context_pressure,
    _message_suggests_browser_surface,
    _message_suggests_research_surface,
    _message_suggests_security_surface,
    _persist_preplan_context,
    _record_context_invalidation,
    _resolve_phase_evidence_plan,
    _sync_chat_runtime_state,
    _synthesize_default_phase_evidence_plan,
)
from api.chat_quality import (
    _assess_execution_mode,
    _classify_check_from_command,
    _compact_delegated_result,
    _compact_text_line,
    _compose_user_facing_run_summary,
    _evaluate_chat_quality,
    _evaluate_phase_evidence_gate,
    _limit_chat_response,
    _presentable_decision_text,
    _resolve_chat_decision_text,
    _stream_display_chunk,
)
from api.chat_replan import (
    _extract_abort_request_from_outputs,
    _extract_advisory_request_from_outputs,
    _extract_budget_adjustments_from_outputs,
    _extract_degrade_request_from_outputs,
    _extract_delegate_request_from_outputs,
    _extract_force_gate_request_from_outputs,
    _extract_pause_for_user_request_from_outputs,
    _extract_replan_phases_from_outputs,
    _extract_retry_route_request_from_outputs,
    _extract_skip_request_from_outputs,
    _extract_skip_phase_request_from_outputs,
    _merge_replanned_phases,
    _phase_started_for_replan,
    _prune_phases_for_mid_run_lead_action,
    _replan_skip_reason,
    _replan_window_is_open,
    _retry_route_removal_phase_ids,
    _strip_selected_directives,
)

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
from aiteam.context_curator import ContextCuratorStore
from aiteam.chat_policy import (
    CHAT_VALIDATION_OWNER,
    ChatPolicyInput,
    build_chat_task_policy_metadata,
    evaluate_chat_policy,
    resolve_run_type_policy,
)
from aiteam.lead_control import (
    LeadDirectiveEvent,
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
from aiteam.lead_memory import (
    build_memory_prompt_block,
    observe_capabilities_snapshot,
    update_lead_memory,
)
from aiteam.lead_close_policy import derive_lead_close_policy
from aiteam.persistence import AtomicFileWriter
from aiteam.phase_verdicts import (
    build_phase_verdict_prompt_block,
    coerce_phase_verdicts,
    extract_phase_verdict,
    is_missing_contract_objective,
)
from aiteam.pilot import compute_pilot_metrics
from aiteam.quorum import (
    PLANNING_QUORUM_RUN_MODES,
    QuorumResult,
    run_planning_quorum,
    should_apply_planning_quorum,
)
from aiteam.run_health import build_capabilities_briefing
from aiteam.sim_mode import sim_mode_enabled
from aiteam.time_utils import local_now, local_now_iso
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


def _extract_lcp_directives(text: str) -> dict:
    return _lead_control_extract_lcp_directives(text)


def _strip_lcp_directives(text: str) -> str:
    return _lead_control_strip_lcp_directives(text)


def _extract_clarify_directive(text: str) -> str | None:
    return _lead_control_extract_clarify_directive(text)


def _extract_delegate_directive(text: str) -> str | None:
    return _lead_control_extract_delegate_directive(text)


def _role_required_capabilities(role_name: str) -> list[str]:
    normalized = str(role_name or "").strip().upper()
    capabilities_by_role = {
        "TEAM_LEAD": ["reasoning", "coding", "repo_read"],
        "RESEARCHER": ["analysis", "repo_read", "reasoning"],
        "ENGINEER": ["coding", "repo_read"],
        "REVIEWER": ["review", "repo_read", "reasoning"],
        "QA": ["analysis", "test_execute", "build_execute"],
        "SCOUT": ["repo_read"],
    }
    return list(capabilities_by_role.get(normalized, ["analysis"]))


def _workspace_artifact_snapshot(workspace: Path) -> dict[str, tuple[int, int]]:
    skip_dirs = {
        "runtime",
        ".aiteam",
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
    runtime_dir_name = resolve_runtime_dir(workspace, PROJECT_ROOT).name

    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in skip_dirs or part == runtime_dir_name for part in relative.parts):
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


def _project_instructions_block(project_root: Path) -> str:
    instructions_path = Path(project_root) / ".aiteam" / "instructions.md"
    if not instructions_path.exists():
        return ""
    try:
        instructions_content = instructions_path.read_text(encoding="utf-8")[:4000].strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if not instructions_content:
        return ""
    return (
        "\n\n## Instrucciones del proyecto (.aiteam/instructions.md)\n"
        f"{instructions_content}"
    )


def _project_instruction_constraints(project_root: Path) -> dict[str, object]:
    instructions_path = Path(project_root) / ".aiteam" / "instructions.md"
    if not instructions_path.exists():
        return {}
    try:
        instructions_content = instructions_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    text = str(instructions_content or "")
    if not text.strip():
        return {}

    forbidden_path_hints: list[str] = []
    allowed_module_path_hints: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        code_refs = re.findall(r"`([^`\n]+)`", line)
        if re.search(r"(?i)\b(?:stop:\s*)?no\s+crear\b", line):
            forbidden_path_hints.extend(code_refs)
        if re.search(r"(?i)(?:logica|lógica)\s+en|escribirlos\s+contra", line):
            allowed_module_path_hints.extend(code_refs)

    return {
        "forbidden_path_hints": [
            str(item).strip().replace("\\", "/")
            for item in forbidden_path_hints
            if str(item).strip()
        ][:12],
        "allowed_module_path_hints": [
            str(item).strip().replace("\\", "/")
            for item in allowed_module_path_hints
            if str(item).strip()
        ][:12],
    }


def _workspace_allowed_module_scope_hints(project_root: Path) -> list[str]:
    root = Path(project_root)
    if not root.exists():
        return []

    candidates: list[str] = []
    package_dirs: list[str] = []
    src_dir = root / "src"
    if src_dir.is_dir():
        for path in sorted(src_dir.rglob("*.py")):
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if not rel or any(part.startswith(".") for part in path.parts):
                continue
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            candidates.append(rel)
            parent_rel = path.parent.relative_to(root).as_posix().strip()
            if (
                parent_rel
                and parent_rel != "src"
                and parent_rel.startswith("src/")
            ):
                package_dirs.append(parent_rel.rstrip("/") + "/")

    if not candidates:
        for path in sorted(root.glob("*.py")):
            name = path.name.strip()
            if name and name.lower() != "conftest.py":
                candidates.append(name)

    merged = [
        item
        for item in (package_dirs + candidates)
        if str(item).strip()
    ]
    return list(dict.fromkeys(merged))[:16]


def _workspace_reviewable_artifact_hints(project_root: Path, *, limit: int = 8) -> list[str]:
    snapshot = _workspace_artifact_snapshot(project_root)
    preferred_roots = ("src/", "tests/", "docs/", "api/")
    preferred = [
        path for path in snapshot.keys()
        if any(path.startswith(root) for root in preferred_roots)
    ]
    ordered = preferred or list(snapshot.keys())
    return list(dict.fromkeys(str(item).strip() for item in ordered if str(item).strip()))[:limit]


_PLANNING_RUN_MODES = set(PLANNING_QUORUM_RUN_MODES)


def _safe_plan_slug(value: str, *, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _resolve_project_plan_dir(workspace: Path) -> Path:
    docs_dir = workspace / "docs"
    if docs_dir.exists():
        return docs_dir / "aiteam"
    return workspace / "planning"


_REVIEW_REJECTED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict|estado|status|result|resultado)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:rechazad[oa]|rejected|changes_requested|cambios\s+solicitados?|solicita\s+cambios)\b|\b[\"']?(?:recomendaci[oó]n|recommendation|status|result|resultado|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:rechazad[oa]|rejected|changes_requested|cambios\s+solicitados?|solicita\s+cambios)\b)"
)
_REVIEW_BLOCKED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict|estado|status|result|resultado)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:bloquead[oa]|blocked)\b|\b[\"']?(?:recomendaci[oó]n|recommendation|status|result|resultado|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:bloquead[oa]|blocked)\b|\b(?:no\s+(?:puedo|puede|pude|se\s+puede)|cannot|can't|could\s+not)\s+(?:revisar|review|validar|validate|verificar|verify)\b|\b(?:insufficient|missing|lack(?:ing)?)\s+(?:review\s+)?evidence\b|\bevidencia\b.{0,40}\binsuficiente\b|\b(?:falta\s+evidencia|no\s+hay\s+evidencia)\b)"
)
_QA_BLOCKED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:summary|resumen|estado|status|result|resultado|decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b[\"']?(?:summary|resumen|estado|status|result|resultado|recomendaci[oó]n|recommendation|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b(?:summary|resumen|estado)\b.{0,240}\b(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b(?:no\s+(?:puedo|puede|pude|se\s+puede)|cannot|can't|could\s+not)\s+(?:validar|validate|verificar|verify|probar|test)\b|\b(?:insufficient|missing|lack(?:ing)?)\s+(?:qa\s+|validation\s+)?evidence\b|\b(?:evidencia\s+insuficiente|falta\s+evidencia|no\s+hay\s+evidencia|faltan?\s+(?:tests?|checks?|validaciones?|criterios\s+de\s+aceptaci[oó]n)|no\s+(?:hay|existen?)\s+(?:tests?|checks?|validaciones?|criterios\s+de\s+aceptaci[oó]n))\b)"
)
_SLICE_ID_RE = re.compile(r"(?i)\bslice\s+(\d+)\b")
_BUILD_SLICE_DRIFT_RE = re.compile(
    r"(?is)(?:\ba pesar de\b.{0,240}\b(?:directriz|directive)\b|\bslice de mayor impacto\b)"
)

# RC-H: per-run cancel flags.  Keyed by task_root (CHAT-XXXX).
# Set to True by POST /api/aiteam/chat/{task_root}/cancel.
# Checked in the streaming loop to close the SSE connection early.
_RUN_CANCEL_FLAGS: dict[str, bool] = {}
_WORKSPACE_ACTIVE_RUNS: dict[str, str] = {}
_WORKSPACE_ACTIVE_RUNS_LOCK = threading.RLock()


def _workspace_run_registry_key(workspace: Path | str) -> str:
    try:
        return str(Path(workspace).resolve()).strip().lower()
    except Exception:
        return str(workspace or "").strip().lower()


def _claim_workspace_active_run(workspace: Path | str, task_root: str) -> str:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return ""
    key = _workspace_run_registry_key(workspace)
    with _WORKSPACE_ACTIVE_RUNS_LOCK:
        active_root = _normalize_task_root(_WORKSPACE_ACTIVE_RUNS.get(key, ""))
        if active_root and active_root != normalized_root:
            return active_root
        _WORKSPACE_ACTIVE_RUNS[key] = normalized_root
    return ""


def _release_workspace_active_run(workspace: Path | str, task_root: str) -> None:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return
    key = _workspace_run_registry_key(workspace)
    with _WORKSPACE_ACTIVE_RUNS_LOCK:
        current_root = _normalize_task_root(_WORKSPACE_ACTIVE_RUNS.get(key, ""))
        if current_root == normalized_root:
            _WORKSPACE_ACTIVE_RUNS.pop(key, None)


def _workspace_active_run_detail(runtime_dir: Path, task_root: str) -> dict[str, object]:
    normalized_root = _normalize_task_root(task_root)
    progress = _build_chat_progress(runtime_dir, normalized_root) if normalized_root else None
    if progress is None:
        return {"task_id": normalized_root}
    return {
        "task_id": normalized_root,
        "state": str(progress.state or ""),
        "waiting_user": bool(progress.waiting_user),
        "next_action_hint": str((progress.run_verdict or {}).get("next_action_hint", "") or ""),
        "pending_tasks": int(progress.pending_tasks),
        "failed_tasks": int(progress.failed_tasks),
    }


def _task_result_text(task: WorkTask | None) -> str:
    if task is None:
        return ""
    return str(task.metadata.get("result") or task.metadata.get("error") or "")


def _first_slice_id(*texts: str) -> str:
    for text in texts:
        match = _SLICE_ID_RE.search(str(text or ""))
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _first_verdict_slice_id(*verdicts: dict[str, object]) -> str:
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            continue
        slice_id = str(verdict.get("slice_id", "") or "").strip()
        if slice_id:
            return slice_id
    return ""


def _sync_phase_verdict_in_workflow_state(
    workflow_state: dict[str, object],
    *,
    phase_id: str,
    output: str,
) -> None:
    normalized_phase = str(phase_id or "").strip().lower()
    if not normalized_phase or not isinstance(workflow_state, dict):
        return
    verdicts = workflow_state.setdefault("phase_verdicts", {})
    if not isinstance(verdicts, dict):
        verdicts = {}
        workflow_state["phase_verdicts"] = verdicts
    verdict = extract_phase_verdict(output, phase_id=normalized_phase)
    if verdict:
        verdicts[normalized_phase] = verdict
    else:
        verdicts.pop(normalized_phase, None)


def _prune_phase_verdicts(
    workflow_state: dict[str, object],
    *,
    keep_phase_ids: set[str],
) -> None:
    if not isinstance(workflow_state, dict):
        return
    existing = workflow_state.get("phase_verdicts", {})
    if not isinstance(existing, dict):
        workflow_state["phase_verdicts"] = {}
        return
    normalized_keep = {
        str(item).strip().lower()
        for item in keep_phase_ids
        if str(item).strip()
    }
    workflow_state["phase_verdicts"] = {
        str(phase_id).strip().lower(): dict(entry)
        for phase_id, entry in existing.items()
        if str(phase_id).strip().lower() in normalized_keep and isinstance(entry, dict)
    }


def _evaluate_phase_semantic_gate(
    *,
    task_rows_by_phase: dict[str, WorkTask],
    phase_verdicts: dict[str, dict[str, object]] | None = None,
) -> list[str]:
    def _is_slice_source_phase(phase_id: str) -> bool:
        normalized_phase = str(phase_id or "").strip().lower()
        return normalized_phase == "lead_intake" or normalized_phase.startswith("plan_")

    def _select_approved_slice_from_verdicts(
        verdicts_by_phase: dict[str, dict[str, object]],
    ) -> str:
        explicit = dict(verdicts_by_phase.get("lead_intake", {}) or {})
        explicit_slice = str(explicit.get("slice_id", "") or "").strip()
        if explicit_slice:
            return explicit_slice
        for phase_id, entry in verdicts_by_phase.items():
            if not _is_slice_source_phase(phase_id):
                continue
            if not isinstance(entry, dict):
                continue
            slice_id = str(entry.get("slice_id", "") or "").strip()
            if slice_id:
                return slice_id
        return ""

    def _select_approved_slice_from_outputs() -> str:
        lead_intake_text = _task_result_text(task_rows_by_phase.get("lead_intake"))
        explicit_slice = _first_slice_id(lead_intake_text)
        if explicit_slice:
            return explicit_slice
        for phase_id, candidate in task_rows_by_phase.items():
            if not _is_slice_source_phase(phase_id):
                continue
            slice_id = _first_slice_id(_task_result_text(candidate))
            if slice_id:
                return slice_id
        return ""

    def _select_gate_verdict(
        verdicts_by_phase: dict[str, dict[str, object]],
        gate_kind: str,
    ) -> dict[str, object]:
        def _gate_kind_for_entry(phase_name: str, entry: dict[str, object]) -> str:
            normalized_phase = str(phase_name or "").strip().lower()
            role_hint = str(entry.get("role_hint", "") or "").strip().lower()
            if normalized_phase.startswith(("lead_", "delegate_", "plan_")):
                return ""
            if normalized_phase == "build":
                return "build"
            if normalized_phase == "review" or "review" in normalized_phase:
                return "review"
            if normalized_phase == "qa" or "qa" in normalized_phase or "validation" in normalized_phase or normalized_phase.startswith("validate"):
                return "qa"
            if role_hint == "engineer":
                return "build"
            if role_hint == "reviewer":
                return "review"
            if role_hint == "qa":
                return "qa"
            return ""

        normalized_gate = str(gate_kind or "").strip().lower()
        explicit = dict(verdicts_by_phase.get(normalized_gate, {}) or {})
        if explicit and _gate_kind_for_entry(str(explicit.get("phase_id", "") or normalized_gate), explicit) == normalized_gate:
            return explicit
        for phase_id, entry in verdicts_by_phase.items():
            if not isinstance(entry, dict):
                continue
            if _gate_kind_for_entry(phase_id, entry) == normalized_gate:
                return dict(entry)
        return {}

    def _select_gate_task(gate_kind: str) -> WorkTask | None:
        normalized_gate = str(gate_kind or "").strip().lower()
        explicit = task_rows_by_phase.get(normalized_gate)
        if explicit is not None:
            return explicit
        for phase_id, candidate in task_rows_by_phase.items():
            if candidate is None:
                continue
            normalized_phase = str(phase_id or "").strip().lower()
            if normalized_phase.startswith(("lead_", "delegate_", "plan_")):
                continue
            if normalized_gate == "build" and candidate.role.value == "engineer":
                return candidate
            if normalized_gate == "review" and candidate.role.value == "reviewer":
                return candidate
            if normalized_gate == "qa" and candidate.role.value == "qa":
                return candidate
        return None

    failures: list[str] = []
    verdicts = coerce_phase_verdicts(phase_verdicts or {})

    review_verdict = _select_gate_verdict(verdicts, "review")
    if (
        str(review_verdict.get("status", "") or "").strip().lower() == "rejected"
        or "review_rejected"
        in [
            str(item).strip().lower()
            for item in list(review_verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ]
    ):
        failures.append("review:rejected_decision")
    elif (
        str(review_verdict.get("status", "") or "").strip().lower() == "blocked"
        or "review_blocked"
        in [
            str(item).strip().lower()
            for item in list(review_verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ]
    ):
        failures.append("review:blocked_status")

    review_text = _task_result_text(_select_gate_task("review"))
    if (
        "review:rejected_decision" not in failures
        and review_text
        and _REVIEW_REJECTED_RE.search(review_text)
    ):
        failures.append("review:rejected_decision")
    if (
        "review:rejected_decision" not in failures
        and "review:blocked_status" not in failures
        and review_text
        and _REVIEW_BLOCKED_RE.search(review_text)
    ):
        failures.append("review:blocked_status")

    qa_verdict = _select_gate_verdict(verdicts, "qa")
    if (
        str(qa_verdict.get("status", "") or "").strip().lower() == "blocked"
        or "qa_blocked"
        in [
            str(item).strip().lower()
            for item in list(qa_verdict.get("reason_codes", []) or [])
            if str(item).strip()
        ]
    ):
        failures.append("qa:blocked_status")

    qa_text = _task_result_text(_select_gate_task("qa"))
    if "qa:blocked_status" not in failures and qa_text and _QA_BLOCKED_RE.search(qa_text):
        failures.append("qa:blocked_status")

    build_verdict = _select_gate_verdict(verdicts, "build")
    build_text = _task_result_text(_select_gate_task("build"))
    approved_slice = _select_approved_slice_from_verdicts(verdicts) or _select_approved_slice_from_outputs()
    build_slice = str(build_verdict.get("slice_id", "") or "").strip() or _first_slice_id(build_text)
    build_contract_status = str(build_verdict.get("contract_status", "") or "").strip().lower()
    build_reason_codes = [
        str(item).strip().lower()
        for item in list(build_verdict.get("reason_codes", []) or [])
        if str(item).strip()
    ]
    if approved_slice and build_slice and approved_slice != build_slice:
        failures.append(f"build:slice_drift:{approved_slice}->{build_slice}")
    elif approved_slice and (
        build_contract_status == "drift"
        or "slice_drift" in build_reason_codes
    ):
        failures.append(f"build:slice_drift:{approved_slice}->{build_slice or 'unknown'}")
    elif approved_slice and build_text and _BUILD_SLICE_DRIFT_RE.search(build_text):
        failures.append(f"build:slice_drift:{approved_slice}->unknown")

    return list(dict.fromkeys(failures))


def _format_pending_phase_summary(
    pending_phases: list[str],
    planning_failed_phases: list[str],
) -> str:
    normalized_pending = [
        str(item).strip()
        for item in list(pending_phases or [])
        if str(item).strip()
    ]
    normalized_planning_failed = [
        str(item).strip()
        for item in list(planning_failed_phases or [])
        if str(item).strip()
    ]
    if not normalized_pending:
        return "none"
    if not normalized_planning_failed:
        return ", ".join(normalized_pending)

    downstream_pending = [
        phase
        for phase in normalized_pending
        if not str(phase).strip().lower().startswith("plan_")
    ]
    planning_pending = [
        phase
        for phase in normalized_pending
        if str(phase).strip().lower().startswith("plan_")
    ]

    segments: list[str] = []
    if planning_pending:
        segments.append(", ".join(planning_pending))
    if downstream_pending:
        segments.append(
            "downstream bloqueado por planning: " + ", ".join(downstream_pending)
        )
    return " | ".join(segments) if segments else "none"


def _should_auto_extend_weak_run(
    *,
    artifact_created: int,
    execution_steps_so_far: int,
    planning_failure_detected: bool,
    root_task_state_counts: dict[str, int] | None,
) -> tuple[bool, str]:
    if planning_failure_detected:
        return False, "planning_phase_failed"
    if artifact_created != 0 or execution_steps_so_far != 0:
        return False, "run_has_execution_or_artifacts"

    counts = dict(root_task_state_counts or {})
    runnable_count = int(counts.get("ready", 0)) + int(counts.get("claimed", 0))
    waiting_user_count = int(counts.get("waiting_user", 0))
    if waiting_user_count > 0:
        return False, "waiting_user"
    if runnable_count <= 0:
        return False, "no_runnable_tasks_for_root"
    return True, "weak_run_without_artifacts_or_execution_steps"


def _detect_preplanning_support_failure(
    *,
    phase_states: dict[str, str],
    task_rows_by_phase: dict[str, WorkTask],
) -> tuple[bool, list[str], list[str]]:
    lead_state = str(phase_states.get("lead_intake", "") or "").strip().lower()
    support_failed = [
        phase_name
        for phase_name, state in phase_states.items()
        if phase_name != "lead_intake"
        and _is_supporting_control_phase(phase_name)
        and str(state or "").strip().lower() == "failed"
    ]
    if lead_state not in {"blocked", "failed"} or not support_failed:
        return False, support_failed, []

    non_support_started = False
    for phase_name, task_row in task_rows_by_phase.items():
        if phase_name in {"lead_intake", "lead_close"}:
            continue
        if _is_supporting_control_phase(phase_name):
            continue
        if task_row is None:
            continue
        if task_row.state not in {TaskState.PENDING, TaskState.READY}:
            non_support_started = True
            break
    if non_support_started:
        return False, support_failed, []

    reason_codes = [f"phase_failed:{phase_name}" for phase_name in support_failed]
    reason_codes.append("lead_intake:blocked_by_support_context")
    return True, support_failed, reason_codes


def _resolve_run_failed_phases(
    *,
    failed_phases: list[str],
    preplanning_support_failure_detected: bool,
    preplanning_support_failed_phases: list[str],
) -> list[str]:
    if not preplanning_support_failure_detected:
        return list(failed_phases or [])
    resolved = [
        str(item).strip()
        for item in list(preplanning_support_failed_phases or [])
        if str(item).strip()
    ]
    if resolved:
        return list(dict.fromkeys(resolved))
    return list(failed_phases or [])


def _should_skip_pause_for_user_due_to_authoritative_policy(
    question: str,
    lead_close_policy: object,
) -> bool:
    """Reject stale routing pauses when the policy has a stronger semantic cause."""

    if not isinstance(lead_close_policy, dict):
        return False
    normalized_question = str(question or "").strip().lower()
    if not normalized_question:
        return False
    routing_markers = (
        "routing",
        "adapter",
        "adaptador",
        "adaptadores",
        "modelo",
        "modelos",
        "no_eligible_adapter",
        "429",
        "quota",
        "cuota",
        "capacidad",
        "fallback",
    )
    if not any(marker in normalized_question for marker in routing_markers):
        return False
    signals = {
        str(item or "").strip().lower()
        for key in ("primary_blocking_signals", "blocking_signals")
        for item in list(lead_close_policy.get(key, []) or [])
        if str(item or "").strip()
    }
    return "review_rejected" in signals


def _safe_phase_suffix(value: str) -> str:
    raw = str(value or "").strip().lower()
    chars: list[str] = []
    for ch in raw:
        chars.append(ch if ch.isalnum() or ch == "_" else "_")
    suffix = "".join(chars).strip("_")
    return suffix[:48] or "phase"


def _normalize_run_profile(raw_profile: str, *, chat_mode: str = "") -> str:
    normalized = str(raw_profile or "").strip().lower()
    normalized_mode = str(chat_mode or "").strip().lower()
    if normalized in {"solo_lead", "direct", "basic", "single", "single_agent", "opencode"}:
        return "solo_lead"
    if normalized_mode == "direct":
        return "solo_lead"
    # lead_quorum: solo_lead + quorum deliberativo sobre el plan antes de ejecutar
    if normalized in {"lead_quorum", "quorum"}:
        return "lead_quorum"
    # ai_team_basic: Lead planifica + Engineer ejecuta + QA valida (sin Reviewer)
    if normalized in {"ai_team_basic", "team_basic", "basic_team"}:
        return "ai_team_basic"
    # ai_teams_full: pipeline completo Lead+Engineer+Reviewer+QA con quorum en plan
    if normalized in {"ai_teams_full", "teams_full", "full_team", "full"}:
        return "ai_teams_full"
    if normalized in {"team_advanced", "advanced", "team", "multi_agent", "multiagent"}:
        return "team_advanced"
    return "team_advanced"


def _direct_profile_phase_specs(
    phases: list[PhaseSpec],
    *,
    user_message: str,
) -> list[PhaseSpec]:
    """Reduce any Lead plan to one direct Team Lead coding phase."""

    def _direct_objective(raw_objective: str) -> str:
        objective = _compact_text_line(str(raw_objective or ""), limit=900)
        if not objective:
            objective = (
                "Implementa directamente el cambio minimo solicitado en el workspace, "
                "respetando instrucciones del proyecto, rutas reales y checks disponibles."
            )
        return (
            "Perfil solo_lead/direct: actua como agente de coding directo tipo Codex/OpenCode. "
            "Define y ejecuta el menor slice coherente con el objetivo actual si no existe un "
            "slice aprobado previo; eso no es drift. Solo marca contract_status=drift si cambias "
            "el objetivo del usuario, sales del scope del workspace o escribes en rutas no "
            "relacionadas. No emitas directivas [DELEGATE_*], [WAIT_POLICY] ni "
            "[DELEGATE_BUDGET]; ejecuta y valida directamente. Tienes autoridad para "
            "inspeccionar el workspace y modificar todos los archivos minimos necesarios "
            "para resolver el fallo material actual, aunque sean varios archivos "
            "relacionados; no cierres solo con diagnostico si una reparacion segura es "
            f"posible. Objetivo: {objective}"
        )

    for spec in list(phases or []):
        role = str(spec.role or "").strip().upper()
        phase_id = str(spec.phase_id or "").strip()
        if role != "ENGINEER" or phase_id.lower().startswith("plan_"):
            continue
        objective = str(spec.objective or "").strip()
        return [
            PhaseSpec(
                phase_id="build",
                role="TEAM_LEAD",
                objective=_direct_objective(objective),
                depends_on=[],
            )
        ]
    fallback = default_phases("direct")[0]
    message = _compact_text_line(str(user_message or ""), limit=180)
    if message:
        fallback = PhaseSpec(
            phase_id=fallback.phase_id,
            role="TEAM_LEAD",
            objective=_direct_objective(f"{fallback.objective} Solicitud actual: {message}"),
            depends_on=[],
        )
    else:
        fallback = PhaseSpec(
            phase_id=fallback.phase_id,
            role="TEAM_LEAD",
            objective=_direct_objective(fallback.objective),
            depends_on=[],
        )
    return [fallback]


def _unique_phase_id(base: str, existing_ids: set[str]) -> str:
    candidate = _safe_phase_suffix(base)
    if candidate not in existing_ids:
        existing_ids.add(candidate)
        return candidate
    counter = 2
    while True:
        next_candidate = f"{candidate}_{counter}"
        if next_candidate not in existing_ids:
            existing_ids.add(next_candidate)
            return next_candidate
        counter += 1


def _review_rework_phase_specs(
    phases: list[PhaseSpec],
    *,
    rejected_review_phase: str,
    review_feedback: str,
) -> list[PhaseSpec]:
    """Replace a rejected review tail with a generic repair -> review -> qa loop."""

    normalized_target = str(rejected_review_phase or "").strip()
    if not normalized_target:
        return list(phases or [])
    existing_ids = {
        str(spec.phase_id or "").strip()
        for spec in list(phases or [])
        if str(spec.phase_id or "").strip()
    }
    if any(phase_id.startswith("repair_after_") for phase_id in existing_ids):
        return list(phases or [])

    target_index = -1
    target_spec: PhaseSpec | None = None
    for idx, spec in enumerate(list(phases or [])):
        if str(spec.phase_id or "").strip() == normalized_target:
            target_index = idx
            target_spec = spec
            break
    if target_index < 0 or target_spec is None:
        return list(phases or [])

    preserved = list(phases[:target_index])
    repair_deps = [
        str(dep).strip()
        for dep in list(target_spec.depends_on or [])
        if str(dep).strip() and str(dep).strip() in existing_ids
    ]
    if not repair_deps:
        for candidate in reversed(preserved):
            candidate_id = str(candidate.phase_id or "").strip()
            candidate_role = str(candidate.role or "").strip().upper()
            if candidate_id and (
                candidate_role == "ENGINEER"
                or _is_build_like_phase_name(candidate_id)
            ):
                repair_deps = [candidate_id]
                break

    existing_after_prune = {
        str(spec.phase_id or "").strip()
        for spec in preserved
        if str(spec.phase_id or "").strip()
    }
    repair_id = _unique_phase_id(
        f"repair_after_{normalized_target}",
        existing_after_prune,
    )
    review_id = _unique_phase_id(
        f"review_after_{repair_id}",
        existing_after_prune,
    )
    qa_id = _unique_phase_id(
        f"qa_after_{repair_id}",
        existing_after_prune,
    )
    feedback = _compact_text_line(str(review_feedback or ""), limit=900)
    feedback_line = (
        f" Review feedback compacto: {feedback}"
        if feedback
        else " Usa el resultado de la review rechazada como lista de hallazgos."
    )
    repair_objective = (
        f"Reparar de forma minima y generica los hallazgos de la review rechazada "
        f"`{normalized_target}` antes de volver a validar.{feedback_line}"
    )
    review_objective = (
        f"Revisar solo la reparacion de `{repair_id}` contra los hallazgos previos; "
        "si quedan issues, emitir findings accionables sin culpar routing."
    )
    qa_objective = (
        f"Validar la reparacion de `{repair_id}` con checks disponibles y evidencia concreta."
    )
    return preserved + [
        PhaseSpec(
            phase_id=repair_id,
            role="ENGINEER",
            objective=repair_objective,
            depends_on=repair_deps,
        ),
        PhaseSpec(
            phase_id=review_id,
            role="REVIEWER",
            objective=review_objective,
            depends_on=[repair_id],
        ),
        PhaseSpec(
            phase_id=qa_id,
            role="QA",
            objective=qa_objective,
            depends_on=[review_id],
        ),
    ]


def _normalize_advisory_context_phase_specs(phases: list[PhaseSpec]) -> list[PhaseSpec]:
    copied = [
        PhaseSpec(
            phase_id=str(spec.phase_id or "").strip(),
            role=(
                "SCOUT"
                if _is_advisory_context_phase_name(
                    str(spec.phase_id or "").strip(),
                    str(spec.role or "").strip(),
                )
                and str(spec.role or "").strip().upper() == "RESEARCHER"
                else str(spec.role or "").strip()
            ),
            objective=str(spec.objective or "").strip(),
            depends_on=[
                str(dep).strip()
                for dep in list(spec.depends_on or [])
                if str(dep).strip()
            ],
        )
        for spec in list(phases or [])
        if str(spec.phase_id or "").strip()
    ]
    advisory_ids = {
        spec.phase_id
        for spec in copied
        if _is_advisory_context_phase_name(spec.phase_id, spec.role)
    }
    has_implementation_phase = any(
        str(spec.role or "").strip().upper() == "ENGINEER"
        or _is_build_like_phase_name(spec.phase_id)
        for spec in copied
    )
    advisory_planning_ids = {
        spec.phase_id
        for spec in copied
        if has_implementation_phase
        and _is_advisory_planning_phase_name(spec.phase_id, spec.role)
    }
    if not advisory_ids and not advisory_planning_ids:
        return copied

    normalized: list[PhaseSpec] = []
    for spec in copied:
        deps = list(spec.depends_on or [])
        if str(spec.role or "").strip().upper() in {"ENGINEER", "REVIEWER", "QA"}:
            deps = [
                dep
                for dep in deps
                if dep not in advisory_ids and dep not in advisory_planning_ids
            ]
        normalized.append(
            PhaseSpec(
                phase_id=spec.phase_id,
                role=spec.role,
                objective=spec.objective,
                depends_on=deps,
            )
        )
    return normalized


def _phase_defaults_to_skip_peer_consultation(
    phase_id: str,
    role: str,
    *,
    advisory_context_phase: bool = False,
    advisory_planning_phase: bool = False,
) -> bool:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role or "").strip().upper()
    if normalized_phase == "lead_close":
        return True
    if advisory_context_phase or advisory_planning_phase:
        return True
    if normalized_phase.startswith("plan_"):
        return True
    if normalized_role == "SCOUT":
        return True
    if normalized_role in {"ENGINEER", "REVIEWER", "QA"}:
        return True
    return False


def _phase_defaults_to_skip_specialist_prefetch(
    phase_id: str,
    role: str,
    *,
    advisory_context_phase: bool = False,
    advisory_planning_phase: bool = False,
) -> bool:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role or "").strip().upper()
    if normalized_phase == "lead_close":
        return True
    if advisory_context_phase or advisory_planning_phase:
        return True
    if normalized_phase.startswith("plan_"):
        return True
    if normalized_role == "SCOUT":
        return True
    if normalized_role in {"ENGINEER", "REVIEWER", "QA"}:
        return True
    return False


def _is_actionable_failed_phase(
    phase_name: str,
    task_rows_by_phase: dict[str, WorkTask],
) -> bool:
    normalized_phase = str(phase_name or "").strip().lower()
    if normalized_phase.startswith("delegate_"):
        return False
    task_row = task_rows_by_phase.get(str(phase_name or "").strip())
    role_hint = ""
    if task_row is not None:
        role_hint = getattr(task_row.role, "value", str(task_row.role or ""))
    if _is_advisory_context_phase_name(phase_name, role_hint):
        return False
    if _is_advisory_planning_phase_name(phase_name, role_hint):
        has_implementation_backbone = any(
            (
                str(other_phase or "").strip().lower() not in {"lead_intake", "lead_close"}
                and not _is_advisory_context_phase_name(other_phase, getattr(other_task.role, "value", str(other_task.role or "")))
                and not _is_advisory_planning_phase_name(other_phase, getattr(other_task.role, "value", str(other_task.role or "")))
                and (
                    getattr(other_task.role, "value", "").strip().lower() in {"engineer", "reviewer", "qa"}
                    or _is_build_like_phase_name(other_phase)
                    or _is_review_like_phase_name(other_phase)
                    or _is_qa_like_phase_name(other_phase)
                )
            )
            for other_phase, other_task in task_rows_by_phase.items()
            if other_task is not None
        )
        if has_implementation_backbone:
            return False
    return True


def _determine_run_failure_origin(
    *,
    preplanning_support_failure_detected: bool,
    planning_failed_phases: list[str],
    failed_phases: list[str],
    blocked_phases: list[str] | None = None,
    semantic_gate_failures: list[str] | None = None,
    evidence_gate_failures: list[str] | None = None,
) -> str:
    if preplanning_support_failure_detected:
        return "preplanning_support"
    if list(planning_failed_phases or []):
        return "planning"
    if list(failed_phases or []):
        return "execution"
    if list(blocked_phases or []):
        return "execution"
    if list(semantic_gate_failures or []) or list(evidence_gate_failures or []):
        return "execution"
    return "none"


def _filter_continuation_evidence_gate_failures(
    failures: list[str],
    *,
    continuation_requested: bool,
    artifact_created: int,
    artifact_modified: int,
) -> list[str]:
    normalized_failures = [
        str(item).strip()
        for item in list(failures or [])
        if str(item).strip()
    ]
    if not continuation_requested:
        return normalized_failures

    artifact_total = max(0, int(artifact_created or 0)) + max(0, int(artifact_modified or 0))
    if artifact_total > 0:
        # A continuation that changed files is a delivery run, not a pure
        # context/revalidation run. It still needs real test/build/import evidence.
        return normalized_failures

    suppressed_suffixes = (
        "placeholder_output",
        "no_execution_evidence",
        "no_successful_execution_steps",
        "missing_test_or_build_check",
    )
    return [
        failure
        for failure in normalized_failures
        if not failure.endswith(suppressed_suffixes)
    ]


def _cascade_blocked_phases(
    blocked_phases: list[str],
    task_rows_by_phase: dict[str, WorkTask],
) -> list[str]:
    tasks_by_id = {
        str(task.task_id): task
        for task in task_rows_by_phase.values()
        if task is not None and str(task.task_id).strip()
    }
    cascade: list[str] = []
    for phase in list(blocked_phases or []):
        task = task_rows_by_phase.get(str(phase or "").strip())
        if task is None or task.state != TaskState.BLOCKED:
            continue
        if str(task.metadata.get("blocked_reason", "") or "").strip() != "dependency_failed":
            continue
        dependency_ids = list(task.metadata.get("blocked_dependencies", []) or []) or list(
            task.dependencies or []
        )
        for dep_id in dependency_ids:
            dep_task = tasks_by_id.get(str(dep_id))
            if dep_task is None:
                continue
            if dep_task.state in {TaskState.FAILED, TaskState.BLOCKED}:
                cascade.append(str(phase))
                break
    return list(dict.fromkeys(cascade))


def _filter_cascade_blocked_evidence_failures(
    failures: list[str],
    *,
    cascade_blocked_phases: list[str],
) -> list[str]:
    cascade = {str(phase).strip().lower() for phase in cascade_blocked_phases if str(phase).strip()}
    filtered: list[str] = []
    for failure in list(failures or []):
        text = str(failure or "").strip()
        if not text:
            continue
        phase, _, reason = text.partition(":")
        if phase.strip().lower() in cascade and reason.strip().lower() == "blocked":
            continue
        filtered.append(text)
    return filtered


def _merge_auto_post_validation_failure(
    failures: list[str],
    auto_post_build_validation_result: dict[str, object],
) -> list[str]:
    normalized = list(failures or [])
    failed_validation = (
        isinstance(auto_post_build_validation_result, dict)
        and bool(auto_post_build_validation_result)
        and not bool(auto_post_build_validation_result.get("skipped", False))
        and not bool(auto_post_build_validation_result.get("success", False))
    )
    if not failed_validation:
        return normalized
    normalized = [
        failure
        for failure in normalized
        if failure != "build:missing_test_or_build_check"
    ]
    if "build:auto_post_build_validation_failed" not in normalized:
        normalized.append("build:auto_post_build_validation_failed")
    return normalized


def _auto_validation_command_for_workspace(
    workspace: Path,
    artifact_files: list[str],
    *,
    import_related_modules: bool = False,
) -> tuple[list[str], str]:
    root = Path(workspace)
    python_executable = _python_executable_for_workspace(root)
    syntax_smoke_code = (
        "import ast, importlib, importlib.util, pathlib, sys\n"
        "root = pathlib.Path.cwd()\n"
        "for item in (root, root / 'src'):\n"
        "    if item.exists():\n"
        "        sys.path.insert(0, str(item))\n"
        "import_modules = '--import-modules' in sys.argv[1:]\n"
        "def module_name_for(path):\n"
        "    parts = list(path.with_suffix('').parts)\n"
        "    if parts and parts[0] == 'src':\n"
        "        parts = parts[1:]\n"
        "    if not parts or parts[0] in {'tests', 'test'}:\n"
        "        return ''\n"
        "    if not all(part.isidentifier() for part in parts):\n"
        "        return ''\n"
        "    return '.'.join(parts)\n"
        "failed = []\n"
        "targets = [item for item in sys.argv[1:] if item != '--import-modules'] or ['src']\n"
        "paths = []\n"
        "for raw in targets:\n"
        "    path = pathlib.Path(raw)\n"
        "    if path.is_dir():\n"
        "        paths.extend(sorted(path.rglob('*.py')))\n"
        "    elif path.suffix in {'.py', '.pyi'}:\n"
        "        paths.append(path)\n"
        "for path in paths:\n"
        "    try:\n"
        "        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))\n"
        "        if path.name.startswith('test') and path.suffix == '.py':\n"
        "            module_name = 'aiteam_artifact_smoke_' + '_'.join(path.with_suffix('').parts)\n"
        "            spec = importlib.util.spec_from_file_location(module_name, path)\n"
        "            if spec is None or spec.loader is None:\n"
        "                raise RuntimeError('cannot load module spec')\n"
        "            module = importlib.util.module_from_spec(spec)\n"
        "            spec.loader.exec_module(module)\n"
        "        else:\n"
        "            if import_modules:\n"
        "                module_name = module_name_for(path)\n"
        "                if module_name:\n"
        "                    importlib.import_module(module_name)\n"
        "    except BaseException as exc:\n"
        "        failed.append((str(path), type(exc).__name__, str(exc)))\n"
        "for path, exc_type, message in failed:\n"
        "    print(f'{path}: {exc_type}: {message}')\n"
        "print(f'python_syntax_smoke: {len(paths)} files, {len(failed)} failed')\n"
        "sys.exit(1 if failed else 0)\n"
    )
    normalized_artifacts = [
        str(item or "").strip().replace("\\", "/")
        for item in list(artifact_files or [])
        if str(item or "").strip()
    ]
    has_artifacts = bool(normalized_artifacts)
    python_artifact_targets: list[str] = []
    for path in normalized_artifacts:
        normalized_path = path.strip().lstrip("./")
        if (
            not normalized_path
            or normalized_path.startswith("../")
            or Path(normalized_path).is_absolute()
            or not normalized_path.lower().endswith((".py", ".pyi"))
        ):
            continue
        try:
            if (root / normalized_path).is_file():
                python_artifact_targets.append(normalized_path)
        except OSError:
            continue
    if import_related_modules:
        related_targets: list[str] = []
        for target in list(python_artifact_targets):
            target_path = root / target
            if not target_path.name.startswith("test"):
                continue
            parts = target_path.parts
            if "src" not in parts:
                continue
            try:
                siblings = sorted(target_path.parent.glob("*.py"))
            except OSError:
                siblings = []
            for sibling in siblings:
                if sibling.name == "__init__.py" or sibling.name.startswith("test"):
                    continue
                try:
                    related_targets.append(sibling.relative_to(root).as_posix())
                except ValueError:
                    continue
        python_artifact_targets.extend(related_targets)
    python_artifact_targets = list(dict.fromkeys(python_artifact_targets))[:24]
    if python_artifact_targets:
        command_label = (
            f"python syntax smoke imports {' '.join(python_artifact_targets)}"
            if import_related_modules
            else f"python syntax smoke {' '.join(python_artifact_targets)}"
        )
        return [
            python_executable,
            "-c",
            syntax_smoke_code,
            *(["--import-modules"] if import_related_modules else []),
            *python_artifact_targets,
        ], command_label

    has_python_artifact = any(path.lower().endswith((".py", ".pyi")) for path in normalized_artifacts)
    if has_artifacts and (has_python_artifact or (root / "src").is_dir()):
        compile_target = "src" if (root / "src").is_dir() else "."
        return [
            python_executable,
            "-c",
            syntax_smoke_code,
            compile_target,
        ], f"python syntax smoke {compile_target}"

    package_json = root / "package.json"
    if package_json.exists():
        try:
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            package_data = {}
        scripts = package_data.get("scripts", {}) if isinstance(package_data, dict) else {}
        if isinstance(scripts, dict):
            if has_artifacts and "build" in scripts:
                return ["npm", "run", "build"], "npm run build"

    if has_artifacts:
        return [], ""

    tests_dir = root / "tests"
    has_python_tests = False
    try:
        has_python_tests = tests_dir.is_dir() and any(tests_dir.rglob("test*.py"))
    except OSError:
        has_python_tests = False

    if has_python_tests:
        return [
            python_executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
        ], "python -m pytest -q --tb=short"

    if has_python_artifact or (root / "src").is_dir():
        compile_target = "src" if (root / "src").is_dir() else "."
        return [
            python_executable,
            "-c",
            syntax_smoke_code,
            compile_target,
        ], f"python syntax smoke {compile_target}"

    if package_json.exists():
        try:
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            package_data = {}
        scripts = package_data.get("scripts", {}) if isinstance(package_data, dict) else {}
        if isinstance(scripts, dict):
            if "test" in scripts:
                return ["npm", "test"], "npm test"
            if "build" in scripts:
                return ["npm", "run", "build"], "npm run build"

    return [], ""


def _python_executable_for_workspace(workspace: Path) -> str:
    root = Path(workspace)
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return sys.executable


def _auto_pre_validation_command_for_workspace(workspace: Path) -> tuple[list[str], str]:
    root = Path(workspace)
    python_executable = _python_executable_for_workspace(root)
    tests_dir = root / "tests"
    has_python_tests = False
    try:
        has_python_tests = tests_dir.is_dir() and any(tests_dir.rglob("test*.py"))
    except OSError:
        has_python_tests = False

    if has_python_tests:
        code = (
            "import importlib.util, pathlib, sys\n"
            "root = pathlib.Path.cwd()\n"
            "for item in (root, root / 'src'):\n"
            "    if item.exists():\n"
            "        sys.path.insert(0, str(item))\n"
            "failed = []\n"
            "for path in sorted(pathlib.Path('tests').rglob('test*.py')):\n"
            "    module_name = 'aiteam_precheck_' + '_'.join(path.with_suffix('').parts)\n"
            "    try:\n"
            "        spec = importlib.util.spec_from_file_location(module_name, path)\n"
            "        if spec is None or spec.loader is None:\n"
            "            raise RuntimeError('cannot load module spec')\n"
            "        module = importlib.util.module_from_spec(spec)\n"
            "        spec.loader.exec_module(module)\n"
            "    except BaseException as exc:\n"
            "        failed.append((str(path), type(exc).__name__, str(exc)))\n"
            "for path, exc_type, message in failed:\n"
            "    print(f'{path}: {exc_type}: {message}')\n"
            "print(f'test_import_smoke: {len(failed)} failed')\n"
            "sys.exit(1 if failed else 0)\n"
        )
        return [python_executable, "-c", code], "python test import smoke"

    return _auto_validation_command_for_workspace(root, [])


def _auto_validation_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    blocked_markers = (
        "API_KEY",
        "APIKEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "ANTHROPIC_",
        "OPENAI_",
        "GOOGLE_API",
        "GROQ_",
    )
    for key in list(env.keys()):
        upper = key.upper()
        if any(marker in upper for marker in blocked_markers):
            env.pop(key, None)
    return env


def _safe_replay_auto_validation_command(
    workspace: Path,
    command_label: str,
) -> tuple[list[str], str]:
    """Replay only commands that were produced by our own validation layer."""
    root = Path(workspace)
    label = str(command_label or "").strip()
    normalized = label.lower()
    if not normalized:
        return [], ""
    python_executable = _python_executable_for_workspace(root)
    if normalized == "python -m pytest -q --tb=short":
        return [python_executable, "-m", "pytest", "-q", "--tb=short"], label
    if normalized == "npm test":
        return ["npm", "test"], label
    if normalized == "npm run build":
        return ["npm", "run", "build"], label
    syntax_import_prefix = "python syntax smoke imports "
    syntax_prefix = "python syntax smoke "
    if normalized.startswith(syntax_import_prefix):
        targets = [
            item
            for item in label[len(syntax_import_prefix):].split()
            if item.strip()
        ]
        return _auto_validation_command_for_workspace(
            root,
            targets,
            import_related_modules=True,
        )
    if normalized.startswith(syntax_prefix):
        targets = [
            item
            for item in label[len(syntax_prefix):].split()
            if item.strip()
        ]
        return _auto_validation_command_for_workspace(root, targets)
    return [], ""


def _run_auto_post_build_validation(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    phase_task_set: set[str],
    artifact_files: list[str],
    event_logger,
    run_profile: str = "",
    failed_validation_result: dict[str, object] | None = None,
) -> dict[str, object]:
    if not _env_bool("AITEAM_AUTO_POST_BUILD_VALIDATION", default=True):
        return {}
    direct_profile = str(run_profile or "").strip().lower() in {"solo_lead", "direct"}
    failed_validation = dict(failed_validation_result or {})
    if (
        not artifact_files
        and (
            not direct_profile
            or not failed_validation
            or bool(failed_validation.get("success", False))
        )
    ):
        return {}
    existing_real_check = False
    for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
        if str(record.get("event_type", "") or "") != "execution_step":
            continue
        payload_dict = record.get("payload", {})
        if not isinstance(payload_dict, dict):
            continue
        if str(payload_dict.get("reason", "") or "") == "auto_pre_phase_validation":
            continue
        route_task_id = str(payload_dict.get("task_id", "") or "")
        if route_task_id not in phase_task_set:
            continue
        if not bool(payload_dict.get("success", False)):
            continue
        check_type = _classify_check_from_command(str(payload_dict.get("command", "") or ""))
        if check_type in {"test", "build", "import"}:
            existing_real_check = True
            break
    if existing_real_check:
        return {}

    args: list[str] = []
    command_label = ""
    replaying_failed_command = False
    if (
        direct_profile
        and failed_validation
        and not bool(failed_validation.get("success", False))
    ):
        previous_command = str(failed_validation.get("command", "") or "").strip()
        previous_check_type = _classify_check_from_command(previous_command)
        if previous_check_type in {"test", "build", "import"}:
            args, command_label = _safe_replay_auto_validation_command(
                workspace,
                previous_command,
            )
            replaying_failed_command = bool(args and command_label)
    if not args or not command_label:
        args, command_label = _auto_validation_command_for_workspace(
            workspace,
            artifact_files,
            import_related_modules=direct_profile,
        )
    if not args or not command_label:
        event_logger.emit(
            "chat_auto_validation_skipped",
            {
                "task_id": task_root,
                "reason": "no_safe_validation_command",
                "artifact_files": list(artifact_files[:16]),
            },
        )
        return {
            "success": False,
            "skipped": True,
            "reason": "no_safe_validation_command",
            "artifact_files": list(artifact_files[:16]),
        }

    target_task_id = ""
    for candidate in sorted(phase_task_set):
        phase = candidate.split("::")[-1].lower() if "::" in candidate else candidate.lower()
        if any(marker in phase for marker in ("build", "engineer", "implement", "repair")):
            target_task_id = candidate
            break
    if not target_task_id:
        target_task_id = sorted(phase_task_set)[0] if phase_task_set else task_root

    started_at = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(workspace),
            env=_auto_validation_env(),
            capture_output=True,
            text=True,
            timeout=_safe_int_value(os.getenv("AITEAM_AUTO_VALIDATION_TIMEOUT", "90"), 90),
            check=False,
        )
        stdout = str(proc.stdout or "")[-4000:]
        stderr = str(proc.stderr or "")[-4000:]
        event_logger.emit(
            "execution_step",
            {
                "task_id": target_task_id,
                "success": proc.returncode == 0,
                "step_type": "auto_validation",
                "command": command_label,
                "exit_code": int(proc.returncode),
                "reason": "auto_post_build_validation",
                "replayed_failed_command": replaying_failed_command,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "stdout": stdout,
                "stderr": stderr,
            },
        )
        result = {
            "success": proc.returncode == 0,
            "skipped": False,
            "task_id": task_root,
            "target_task_id": target_task_id,
            "command": command_label,
            "exit_code": int(proc.returncode),
            "reason": "auto_post_build_validation",
            "replayed_failed_command": replaying_failed_command,
            "stdout": stdout,
            "stderr": stderr,
            "artifact_files": list(artifact_files[:16]),
        }
        event_logger.emit(
            "chat_auto_validation_completed",
            {
                "task_id": task_root,
                "target_task_id": target_task_id,
                "success": proc.returncode == 0,
                "command": command_label,
                "exit_code": int(proc.returncode),
                "replayed_failed_command": replaying_failed_command,
            },
        )
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        stderr = str(exc)[:4000]
        event_logger.emit(
            "execution_step",
            {
                "task_id": target_task_id,
                "success": False,
                "step_type": "auto_validation",
                "command": command_label,
                "exit_code": 124,
                "reason": "auto_post_build_validation_error",
                "stderr": stderr,
            },
        )
        return {
            "success": False,
            "skipped": False,
            "task_id": task_root,
            "target_task_id": target_task_id,
            "command": command_label,
            "exit_code": 124,
            "reason": "auto_post_build_validation_error",
            "stdout": "",
            "stderr": stderr,
            "artifact_files": list(artifact_files[:16]),
        }


_VALIDATION_REQUEST_RE = re.compile(
    r"(?is)\b(pytest|tests?|test\s+suite|suite\s+de\s+tests|validar|validate|qa|check(?:s)?)\b"
)
_IMPLEMENTATION_PHASE_RE = re.compile(
    r"(?is)\b(build|implement|repair|fix|develop|code|refactor)\b"
)


def _phase_requests_validation_execution(spec: PhaseSpec) -> bool:
    phase_id = str(getattr(spec, "phase_id", "") or "").strip().lower()
    role = str(getattr(spec, "role", "") or "").strip().upper()
    objective = str(getattr(spec, "objective", "") or "").strip().lower()
    haystack = f"{phase_id}\n{objective}"
    if phase_id.startswith("plan_"):
        return False
    if role == "ENGINEER" or _is_build_like_phase_name(phase_id):
        return False
    if role == "QA":
        return True
    if any(marker in phase_id for marker in ("qa", "test", "validate", "validation", "verify")):
        return True
    return bool(_VALIDATION_REQUEST_RE.search(haystack))


def _plan_allows_pre_phase_validation(phases: list[PhaseSpec]) -> bool:
    phase_list = list(phases or [])
    if not any(_phase_requests_validation_execution(spec) for spec in phase_list):
        return False
    for spec in phase_list:
        phase_id = str(getattr(spec, "phase_id", "") or "").strip().lower()
        role = str(getattr(spec, "role", "") or "").strip().upper()
        objective = str(getattr(spec, "objective", "") or "").strip().lower()
        if role == "ENGINEER" or _is_build_like_phase_name(phase_id):
            return False
        if phase_id.startswith("plan_") or _phase_requests_validation_execution(spec):
            continue
        if _IMPLEMENTATION_PHASE_RE.search(f"{phase_id}\n{objective}"):
            return False
    return True


def _format_auto_validation_context(
    result: dict[str, object],
    *,
    direct_profile: bool = False,
    repair_first_mode: bool = False,
) -> str:
    if not result:
        return ""
    command = str(result.get("command", "") or "").strip()
    if not command:
        return ""
    stdout = str(result.get("stdout", "") or "")[-3000:]
    stderr = str(result.get("stderr", "") or "")[-3000:]
    success = bool(result.get("success", False))
    exit_code = int(result.get("exit_code", 0) or 0)
    if direct_profile and repair_first_mode and not success:
        instruction = (
            "La capa 1 ya ejecuto esta validacion antes de la fase. En perfil solo_lead/direct, "
            "este fallo es la tarea inmediata: repara primero el error material mas temprano con "
            "el cambio minimo razonable. No abras un slice nuevo mientras esta validacion falle. "
            "Si puedes reparar, hazlo: puedes tocar todos los archivos minimos relacionados "
            "con la causa raiz material (por ejemplo test, import y modulo publico) en la "
            "misma fase. Si el mismo check lista varios blockers de collection/import del "
            "mismo scope, repara el conjunto minimo coherente para que el check pueda "
            "avanzar, no solo la primera linea. Emite bloques completos para cada archivo modificado usando "
            "preferentemente fences ` ```python path=... `; tambien se acepta una linea "
            "`path=...` justo antes del fence. Explica el check real que debe volver a "
            "ejecutarse. Solo marca bloqueo si falta una decision humana o el workspace "
            "impide una reparacion segura."
        )
    else:
        instruction = (
            "La capa 1 ya ejecuto esta validacion antes de la fase. Usala como evidencia real; "
            "no digas que necesitas ejecutar el comando si este bloque esta presente. "
            "No inventes archivos, salidas ni rutas: si success=false, informa solo los errores "
            "observados y marca la fase como no validada."
        )
    return (
        "\n\n[AUTO_VALIDATION_RESULT]\n"
        f"{instruction}\n"
        f"command: {command}\n"
        f"success: {str(success).lower()}\n"
        f"exit_code: {exit_code}\n"
        "stdout:\n"
        "```text\n"
        f"{stdout}\n"
        "```\n"
        "stderr:\n"
        "```text\n"
        f"{stderr}\n"
        "```\n"
        "[/AUTO_VALIDATION_RESULT]\n"
    )


def _format_direct_repair_first_directive(result: dict[str, object]) -> str:
    if not result or bool(result.get("success", False)):
        return ""
    command = str(result.get("command", "") or "").strip()
    if not command:
        return ""
    exit_code = int(result.get("exit_code", 0) or 0)
    stdout = _compact_text_line(str(result.get("stdout", "") or ""), limit=1200)
    stderr = _compact_text_line(str(result.get("stderr", "") or ""), limit=800)
    details = stdout or stderr or "sin salida capturada"
    missing_apis = _format_missing_public_api_directive(result)
    missing_api_line = f"{missing_apis}\n" if missing_apis else ""
    return (
        "[REPAIR_FIRST_DIRECTIVE]\n"
        "Esta fase build es una tarea de reparacion inmediata, no un slice nuevo.\n"
        f"Validacion fallida: {command} (exit_code={exit_code}).\n"
        f"Primeros detalles: {details}\n"
        f"{missing_api_line}"
        "Accion obligatoria: inspecciona los archivos reales afectados y aplica la reparacion "
        "minima segura. Tienes autonomia para modificar todos los archivos relacionados con "
        "el fallo material actual, no solo la primera linea mencionada. Si la salida del "
        "check muestra varios blockers de collection/import relacionados, repara el conjunto "
        "minimo coherente para que el check avance. Si aparece `ImportError: cannot import "
        "name X from Y`, preserva o restaura esa API publica X en el modulo Y salvo que el "
        "usuario haya pedido explicitamente eliminarla. Emite bloques completos "
        "preferentemente como ```python path=...; tambien se acepta una linea `path=...` justo "
        "antes del fence. Deja indicado el check exacto a reejecutar.\n"
        "Prohibido: cerrar solo con diagnostico, abrir funcionalidad nueva o delegar en otros roles.\n"
        "[/REPAIR_FIRST_DIRECTIVE]\n\n"
    )


_IMPORT_ERROR_PUBLIC_API_RE = re.compile(
    r"ImportError:\s+cannot\s+import\s+name\s+'([^']+)'\s+from\s+'([^']+)'",
    re.IGNORECASE,
)


def _missing_public_apis_from_validation_result(
    result: dict[str, object],
) -> list[dict[str, str]]:
    blob = "\n".join(
        [
            str(result.get("stdout", "") or ""),
            str(result.get("stderr", "") or ""),
        ]
    )
    missing: dict[tuple[str, str], dict[str, str]] = {}
    for match in _IMPORT_ERROR_PUBLIC_API_RE.finditer(blob):
        symbol = match.group(1).strip()
        module = match.group(2).strip()
        if not symbol or not module:
            continue
        missing[(module, symbol)] = {"module": module, "symbol": symbol}
    return [missing[key] for key in sorted(missing.keys())]


def _format_missing_public_api_directive(result: dict[str, object]) -> str:
    missing = _missing_public_apis_from_validation_result(result)
    if not missing:
        return ""
    items = [
        f"{item['module']}.{item['symbol']}"
        for item in missing[:8]
    ]
    return (
        "APIs publicas faltantes detectadas: "
        + ", ".join(items)
        + ". Restaura exactamente esos simbolos salvo instruccion explicita contraria."
    )


def _auto_validation_failure_signature(result: dict[str, object]) -> str:
    missing = _missing_public_apis_from_validation_result(result)
    if missing:
        return "import:" + "|".join(
            f"{item['module']}:{item['symbol']}" for item in missing
        )
    blob = "\n".join(
        [
            str(result.get("stdout", "") or ""),
            str(result.get("stderr", "") or ""),
        ]
    )
    interesting: list[str] = []
    for line in blob.splitlines():
        text = line.strip()
        if not text:
            continue
        if (
            "SyntaxError" in text
            or "AssertionError" in text
            or text.startswith("E   ")
            or text.startswith("FAILED ")
        ):
            interesting.append(text)
        if len(interesting) >= 4:
            break
    return " | ".join(interesting)[:800]


def _apply_direct_repair_first_to_phase_task(
    phase_task: WorkTask,
    result: dict[str, object],
) -> bool:
    directive = _format_direct_repair_first_directive(result)
    if not directive:
        return False
    if "[REPAIR_FIRST_DIRECTIVE]" not in phase_task.description:
        phase_task.description = directive + phase_task.description
    if not phase_task.title.lower().startswith("repair "):
        phase_task.title = "Repair " + phase_task.title
    phase_task.metadata["repair_first_required"] = True
    phase_task.metadata["repair_first_origin"] = "auto_pre_phase_validation"
    phase_task.metadata["repair_first_command"] = str(result.get("command", "") or "").strip()
    phase_contract = dict(phase_task.metadata.get("phase_contract", {}) or {})
    original_objective = str(phase_contract.get("objective", "") or "").strip()
    if original_objective and "repair_first_original_objective" not in phase_contract:
        phase_contract["repair_first_original_objective"] = original_objective
    phase_contract["objective"] = (
        "Repair the failed pre-build validation before opening any new slice. "
        f"Command: {str(result.get('command', '') or '').strip()}; "
        f"exit_code={int(result.get('exit_code', 0) or 0)}."
    )
    phase_contract["contract_kind"] = "solo_lead_repair_first"
    phase_task.metadata["phase_contract"] = phase_contract
    return True


def _apply_direct_post_build_repair_to_phase_task(
    phase_task: WorkTask,
    result: dict[str, object],
) -> bool:
    directive = _format_direct_repair_first_directive(result)
    if not directive:
        return False
    phase_task.description = directive + phase_task.description
    if not phase_task.title.lower().startswith("repair "):
        phase_task.title = "Repair " + phase_task.title
    command = str(result.get("command", "") or "").strip()
    phase_task.metadata["repair_first_required"] = True
    phase_task.metadata["repair_first_origin"] = "auto_post_build_validation"
    phase_task.metadata["repair_first_command"] = command
    phase_task.metadata["auto_post_build_repair_attempted"] = True
    phase_task.metadata["auto_post_build_repair_attempt_count"] = int(
        phase_task.metadata.get("auto_post_build_repair_attempt_count", 0) or 0
    ) + 1
    phase_task.metadata["auto_post_validation_result"] = {
        key: value
        for key, value in dict(result).items()
        if key not in {"stdout", "stderr"}
    }
    phase_task.metadata["review_feedback"] = (
        "Repair-first post-build: la validacion automatica fallo tras aplicar cambios. "
        "Usa stdout/stderr adjuntos en la descripcion como fuente autoritativa, repara "
        "el fallo material y vuelve a entregar bloques path=... completos."
    )
    phase_task.metadata["gate_iteration"] = int(
        phase_task.metadata.get("gate_iteration", 0) or 0
    ) + 1
    phase_contract = dict(phase_task.metadata.get("phase_contract", {}) or {})
    original_objective = str(phase_contract.get("objective", "") or "").strip()
    if original_objective and "repair_first_original_objective" not in phase_contract:
        phase_contract["repair_first_original_objective"] = original_objective
    phase_contract["objective"] = (
        "Repair the failed post-build validation before closing the run. "
        f"Command: {command}; exit_code={int(result.get('exit_code', 0) or 0)}. "
        "You may modify all minimal related files needed to fix the material failure."
    )
    phase_contract["contract_kind"] = "solo_lead_post_build_repair"
    phase_task.metadata["phase_contract"] = phase_contract
    return True


def _prepare_direct_post_build_repair_retry(
    *,
    orch,
    task_root: str,
    phase_task_ids: dict[str, str],
    result: dict[str, object],
    max_attempts: int = 1,
) -> str:
    target_task_id = str(result.get("target_task_id", "") or "").strip()
    if not target_task_id:
        target_task_id = str(phase_task_ids.get("build", "") or "").strip()
    if not target_task_id:
        return ""

    target_task = orch.taskboard.get_task(target_task_id)
    if target_task is None:
        return ""
    attempt_count = int(
        target_task.metadata.get("auto_post_build_repair_attempt_count", 0) or 0
    )
    if attempt_count >= max(1, int(max_attempts or 1)):
        return ""
    failure_signature = _auto_validation_failure_signature(result)
    if failure_signature:
        previous_signature = str(
            target_task.metadata.get("auto_post_build_failure_signature", "") or ""
        )
        stagnation_count = (
            int(target_task.metadata.get("auto_post_build_failure_stagnation_count", 0) or 0) + 1
            if previous_signature == failure_signature
            else 1
        )
        target_task.metadata["auto_post_build_failure_signature"] = failure_signature
        target_task.metadata["auto_post_build_failure_stagnation_count"] = stagnation_count
        target_task.metadata["auto_post_build_missing_public_apis"] = (
            _missing_public_apis_from_validation_result(result)
        )
        stagnation_limit = max(
            1,
            _safe_int_value(
                os.getenv("AITEAM_DIRECT_POST_BUILD_STAGNATION_LIMIT", "3"),
                3,
            ),
        )
        if stagnation_count > stagnation_limit:
            target_task.metadata["auto_post_build_repair_stagnated"] = True
            target_task.metadata["auto_post_build_repair_stagnation_reason"] = (
                failure_signature
            )
            orch.taskboard.persist_tasks([target_task_id])
            orch.event_logger.emit(
                "chat_repair_first_post_build_stagnated",
                {
                    "task_id": task_root,
                    "phase_task_id": target_task_id,
                    "signature": failure_signature,
                    "stagnation_count": stagnation_count,
                    "stagnation_limit": stagnation_limit,
                    "attempt": attempt_count,
                },
            )
            return ""
    if not _apply_direct_post_build_repair_to_phase_task(target_task, result):
        return ""

    lead_close_id = str(phase_task_ids.get("lead_close", "") or "").strip()
    lead_close_task = orch.taskboard.get_task(lead_close_id) if lead_close_id else None
    if lead_close_task is not None:
        if target_task_id not in lead_close_task.dependencies:
            lead_close_task.dependencies.append(target_task_id)
        lead_close_task.metadata["post_build_repair_pending"] = True
        lead_close_task.metadata["review_feedback"] = (
            "Post-build validation failed and build is being retried. "
            "Close only after the repaired build and the real validation result."
        )
        lead_close_task.metadata["post_build_repair_hold_until_validation"] = True
        lead_close_task.metadata["gate_iteration"] = int(
            lead_close_task.metadata.get("gate_iteration", 0) or 0
        ) + 1
        orch.taskboard.persist_tasks([lead_close_id])

    orch.taskboard.retry_task(
        target_task_id,
        reason="auto_post_build_validation_repair_first",
    )
    orch.event_logger.emit(
        "chat_repair_first_post_build_retry_started",
        {
            "task_id": task_root,
            "phase_task_id": target_task_id,
            "lead_close_task_id": lead_close_id,
            "command": str(result.get("command", "") or "").strip(),
            "exit_code": int(result.get("exit_code", 0) or 0),
            "attempt": int(target_task.metadata.get("auto_post_build_repair_attempt_count", 0) or 0),
            "max_attempts": max(1, int(max_attempts or 1)),
        },
    )
    return target_task_id


def _resume_direct_post_build_lead_close_after_success(
    *,
    orch,
    task_root: str,
    phase_task_ids: dict[str, str],
) -> bool:
    lead_close_id = str(phase_task_ids.get("lead_close", "") or "").strip()
    if not lead_close_id:
        return False
    lead_close_task = orch.taskboard.get_task(lead_close_id)
    if lead_close_task is None:
        return False
    if not bool(lead_close_task.metadata.get("post_build_repair_pending", False)):
        return False
    if bool(lead_close_task.metadata.get("post_build_repair_close_resumed", False)):
        return False
    lead_close_task.metadata["post_build_repair_pending"] = False
    lead_close_task.metadata["post_build_repair_hold_until_validation"] = False
    lead_close_task.metadata["post_build_repair_validation_passed"] = True
    lead_close_task.metadata["post_build_repair_close_resumed"] = True
    orch.taskboard.persist_tasks([lead_close_id])
    orch.taskboard.retry_task(
        lead_close_id,
        reason="auto_post_build_validation_passed",
    )
    orch.event_logger.emit(
        "chat_repair_first_lead_close_resumed",
        {
            "task_id": task_root,
            "lead_close_task_id": lead_close_id,
            "reason": "auto_post_build_validation_passed",
        },
    )
    return True


def _run_auto_pre_phase_validation(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    phases: list[PhaseSpec],
    phase_task_ids: dict[str, str],
    event_logger,
    run_profile: str = "",
) -> dict[str, object]:
    if not _env_bool("AITEAM_AUTO_PRE_PHASE_VALIDATION", default=True):
        return {}
    direct_profile = str(run_profile or "").strip().lower() in {"solo_lead", "direct"}
    if not direct_profile and not _plan_allows_pre_phase_validation(phases):
        return {}

    if direct_profile and "build" in phase_task_ids:
        validation_phases = ["build"]
    else:
        validation_phases = [
            str(spec.phase_id or "").strip()
            for spec in list(phases or [])
            if _phase_requests_validation_execution(spec)
            and str(spec.phase_id or "").strip() in phase_task_ids
        ]
    if not validation_phases:
        return {}

    for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
        if str(record.get("event_type", "") or "") != "execution_step":
            continue
        payload_dict = record.get("payload", {})
        if not isinstance(payload_dict, dict):
            continue
        if str(payload_dict.get("task_id", "") or "") not in set(phase_task_ids.values()):
            continue
        check_type = _classify_check_from_command(str(payload_dict.get("command", "") or ""))
        if check_type in {"test", "build", "import"}:
            return {}

    args, command_label = (
        _auto_validation_command_for_workspace(workspace, [])
        if direct_profile
        else _auto_pre_validation_command_for_workspace(workspace)
    )
    if not args or not command_label:
        event_logger.emit(
            "chat_auto_validation_skipped",
            {
                "task_id": task_root,
                "reason": "no_safe_pre_phase_validation_command",
                "validation_phases": validation_phases[:8],
            },
        )
        return {}

    target_phase = validation_phases[0]
    target_task_id = phase_task_ids.get(target_phase, task_root)
    started_at = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(workspace),
            env=_auto_validation_env(),
            capture_output=True,
            text=True,
            timeout=_safe_int_value(os.getenv("AITEAM_AUTO_PRE_VALIDATION_TIMEOUT", "25"), 25),
            check=False,
        )
        result = {
            "task_id": target_task_id,
            "target_phase": target_phase,
            "success": proc.returncode == 0,
            "command": command_label,
            "exit_code": int(proc.returncode),
            "reason": "auto_pre_phase_validation",
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "stdout": str(proc.stdout or "")[-4000:],
            "stderr": str(proc.stderr or "")[-4000:],
        }
        event_logger.emit(
            "execution_step",
            {
                "task_id": target_task_id,
                "success": bool(result["success"]),
                "step_type": "auto_validation",
                "command": command_label,
                "exit_code": int(result["exit_code"]),
                "reason": "auto_pre_phase_validation",
                "duration_ms": int(result["duration_ms"]),
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            },
        )
        event_logger.emit(
            "chat_auto_validation_completed",
            {
                "task_id": task_root,
                "target_task_id": target_task_id,
                "target_phase": target_phase,
                "success": bool(result["success"]),
                "command": command_label,
                "exit_code": int(result["exit_code"]),
                "reason": "auto_pre_phase_validation",
            },
        )
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = {
            "task_id": target_task_id,
            "target_phase": target_phase,
            "success": False,
            "command": command_label,
            "exit_code": 124,
            "reason": "auto_pre_phase_validation_error",
            "stdout": "",
            "stderr": str(exc)[:4000],
        }
        event_logger.emit(
            "execution_step",
            {
                "task_id": target_task_id,
                "success": False,
                "step_type": "auto_validation",
                "command": command_label,
                "exit_code": 124,
                "reason": "auto_pre_phase_validation_error",
                "stderr": result["stderr"],
            },
        )
        return result


def _build_preplanning_run_verdict(
    *,
    lead_state: str,
    preplanning_support_failure_detected: bool,
    preplanning_support_failed_phases: list[str],
    preplanning_support_reason_codes: list[str],
) -> dict[str, object]:
    normalized_lead_state = str(lead_state or "").strip().lower()
    base_failed_phases = ["lead_intake"] if normalized_lead_state == "failed" else []
    run_failed_phases = _resolve_run_failed_phases(
        failed_phases=base_failed_phases,
        preplanning_support_failure_detected=preplanning_support_failure_detected,
        preplanning_support_failed_phases=preplanning_support_failed_phases,
    )
    failure_origin = _determine_run_failure_origin(
        preplanning_support_failure_detected=preplanning_support_failure_detected,
        planning_failed_phases=[],
        failed_phases=run_failed_phases,
        semantic_gate_failures=[],
        evidence_gate_failures=[],
    )
    if preplanning_support_failure_detected:
        next_action_hint = (
            "Falló el contexto de soporte previo a planning ("
            + ", ".join(run_failed_phases[:3])
            + "). Reintenta con scouts básicos o contexto degradado antes de abrir workflow."
        )
        reason_codes = list(preplanning_support_reason_codes or [])
    elif normalized_lead_state == "blocked":
        next_action_hint = (
            "lead_intake quedó bloqueado antes de planificar. "
            "Revisar dependencias, directivas del Lead o contexto mínimo requerido."
        )
        reason_codes = ["lead_intake:blocked_before_workflow"]
    else:
        next_action_hint = (
            "lead_intake falló antes de materializar workflow. "
            "Revisa el output del Lead y el contexto operativo actual."
        )
        reason_codes = ["phase_failed:lead_intake"]
    return {
        "state": "failed",
        "result": "fallido",
        "failure_origin": failure_origin or "preplanning",
        "reason_codes": list(dict.fromkeys(reason_codes))[:24],
        "policy_signals": [],
        "policy_review_required": False,
        "semantic_gate_applied": False,
        "semantic_gate_failures": [],
        "evidence_gate_applied": False,
        "evidence_gate_failures": [],
        "failed_phases": list(run_failed_phases[:12]),
        "pending_phases": [],
        "advisory_mode": False,
        "degraded_delivery": False,
        "next_action_hint": next_action_hint,
        "updated_at": local_now_iso(),
    }


def _failed_phase_root_cause_reason_codes(
    task_rows_by_phase: dict[str, WorkTask],
    failed_phases: list[str],
) -> list[str]:
    reason_codes: list[str] = []
    for phase_name in list(failed_phases or []):
        task_row = task_rows_by_phase.get(str(phase_name or "").strip())
        if task_row is None:
            continue
        error_text = str((task_row.metadata or {}).get("error", "") or "").strip().lower()
        if not error_text:
            continue
        if "ungrounded_phase_block_detected" in error_text:
            reason_codes.append(f"phase_failed:{phase_name}:ungrounded_phase_block")
        elif "ungrounded_evidence_output_detected" in error_text:
            reason_codes.append(f"phase_failed:{phase_name}:ungrounded_evidence")
        elif "missing_dependency_artifacts" in error_text:
            reason_codes.append(f"phase_failed:{phase_name}:missing_dependency_artifacts")
        elif "specialist_quorum_not_met" in error_text:
            reason_codes.append(f"phase_failed:{phase_name}:specialist_quorum_not_met")
    return list(dict.fromkeys(reason_codes))


_GENERIC_PROJECT_RETRY_MARKERS = (
    "start the next highest-impact slice for the same project objective",
    "same project objective",
    "clean retry from the current validated project state",
    "close pending phases first",
    "next highest-impact slice",
    "siguiente slice de mayor impacto",
)

_PRODUCT_SCOPE_CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sql",
    ".svelte",
    ".swift",
    ".ts",
    ".tsx",
    ".vue",
}

_ROOT_LEVEL_SUPPORT_FILES = {
    "aiteam_test_log.md",
    "project_plan.md",
    "pyproject.toml",
    "readme.md",
    "requirements.txt",
    "run_validation.py",
    "setup.cfg",
    "setup.py",
    "uv.lock",
}


def _workspace_has_concrete_product_scope(snapshot: dict[str, tuple[int, int]]) -> bool:
    for raw_path in list(snapshot.keys()):
        normalized = str(raw_path or "").strip().replace("\\", "/").lower()
        if not normalized:
            continue
        if normalized.startswith(
            ("src/", "app/", "api/", "lib/", "packages/", "services/", "tests/")
        ):
            return True
        if "/" not in normalized:
            suffix = Path(normalized).suffix.lower()
            if suffix in _PRODUCT_SCOPE_CODE_SUFFIXES and normalized not in _ROOT_LEVEL_SUPPORT_FILES:
                return True
    return False


def _requires_explicit_project_objective_clarification(
    message: str,
    *,
    workspace_snapshot: dict[str, tuple[int, int]],
    continuation_requested: bool = False,
) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    if not normalized:
        return False
    if continuation_requested:
        return False
    if not any(marker in normalized for marker in _GENERIC_PROJECT_RETRY_MARKERS):
        return False
    return not _workspace_has_concrete_product_scope(workspace_snapshot)


def _delegate_request_signature(
    delegate_request,
    *,
    source_phase: str = "",
) -> tuple[str, ...]:
    signature = [
        str(getattr(delegate_request, "intent", "") or "").strip().lower(),
        str(getattr(delegate_request, "query", "") or "").strip().lower(),
        str(getattr(delegate_request, "wait_policy", "") or "").strip().lower(),
    ]
    normalized_source_phase = str(source_phase or "").strip().lower()
    if normalized_source_phase:
        return (normalized_source_phase, *signature)
    return tuple(signature)


def _delegate_batch_has_successful_results(delegate_result: dict[str, object] | None) -> bool:
    entries = list((delegate_result or {}).get("entries", []) or [])
    if not entries:
        return False
    successful_states = {"completed", "approved"}
    for entry in entries:
        state = str((entry or {}).get("state", "") or "").strip().lower()
        if state in successful_states:
            return True
    return False


def _should_require_execution_plan_for_chat_phase(
    *,
    phase_id: str,
    role: str,
    lead_run_mode: str,
    require_build_execution_plan: bool,
    derived_execution_plan: list[dict[str, object]] | None,
) -> bool:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role or "").strip().upper()
    if not require_build_execution_plan or str(lead_run_mode or "").strip().lower() != "standard":
        return False
    if normalized_role != "ENGINEER":
        return False
    if normalized_phase.startswith("plan_"):
        return False
    if normalized_phase == "build":
        return True
    return bool(list(derived_execution_plan or []))


def _infer_lead_run_mode_from_message(message: str) -> str:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return ""
    if (
        ("arquitectura" in normalized or "architecture" in normalized)
        and ("adr" in normalized or "decision record" in normalized)
    ):
        return "architecture_review"
    if "roadmap" in normalized or "hoja de ruta" in normalized:
        return "roadmap"
    return ""


def _phase_contract_prompt_block(
    spec: PhaseSpec,
    *,
    all_contracts: dict[str, dict[str, object]],
) -> str:
    phase_id = str(spec.phase_id or "").strip()
    if not phase_id:
        return ""

    contract = dict(all_contracts.get(phase_id, {}) or {})
    objective = str(contract.get("objective", spec.objective) or "").strip()
    depends_on = [
        str(dep).strip()
        for dep in list(contract.get("depends_on", spec.depends_on) or [])
        if str(dep).strip()
    ]
    upstream_lines: list[str] = []
    for dep in depends_on[:4]:
        dep_contract = dict(all_contracts.get(dep, {}) or {})
        dep_objective = str(dep_contract.get("objective", "") or "").strip()
        if dep_objective:
            upstream_lines.append(f"- {dep}: {dep_objective}")

    role_upper = str(spec.role or "").strip().upper()
    objective_missing = is_missing_contract_objective(objective)
    if objective_missing and role_upper == "TEAM_LEAD":
        objective_display = "Planificar y coordinar la corrida actual."
    else:
        objective_display = (
            f"[CONTRATO INVALIDO: objective ausente para '{phase_id}']"
            if objective_missing
            else objective
        )

    if objective_missing and role_upper != "TEAM_LEAD":
        role_guidance = (
            "Contrato invalido: objective ausente. No infieras el objetivo por nombre de fase, "
            "historial o contexto lateral. Declara bloqueo contractual y solicita replanificacion del Lead."
        )
    else:
        validation_guidance = (
            "Valida estrictamente si lo ejecutado respeta este contrato. Si detectas deriva, "
            "decláralo explícitamente. No afirmes tests, cobertura, rutas o artefactos que no "
            "aparezcan en evidencia upstream o en archivos visibles; si faltan, bloquea o rechaza "
            "con el faltante concreto. No conviertas entregables planeados ni criterios de "
            "aceptación en evidencia existente."
        )
        if role_upper == "REVIEWER":
            validation_guidance += (
                " Si tu recomendación es CHANGES_REQUESTED, el [PHASE_VERDICT] debe usar "
                "status: rejected y reason_codes: review_rejected."
            )
        elif role_upper == "QA":
            validation_guidance += (
                " Si el contrato pide un test/reporte que no existe o no se ejecutó, no lo des por "
                "pasado: usa status: blocked o failed y nombra el archivo/check ausente."
            )
        role_guidance = (
            "No cambies de slice, no cambies de objetivo y no sustituyas esta fase por otra "
            "de 'mayor impacto' sin una nueva directiva del Lead."
            if role_upper == "ENGINEER"
            else (
                validation_guidance
                if role_upper in {"REVIEWER", "QA"}
                else "Usa este contrato como restricción autoritativa de la fase."
            )
        )

    lines = [
        "[PHASE_CONTRACT]",
        f"phase_id: {phase_id}",
        f"role: {role_upper}",
        f"objective: {objective_display}",
        f"depends_on: [{', '.join(depends_on)}]" if depends_on else "depends_on: []",
        "contract_rule: obligatorio",
        role_guidance,
    ]
    contract_kind = str(contract.get("contract_kind", "") or "").strip().lower()
    if contract_kind.startswith("delegate_support"):
        lines.append("support_role: true")
        lines.append("decision_authority: parent_phase_or_team_lead")
        lines.append(
            "support_rule: informa evidencia, huecos y fallos observados; no declares "
            "la fase principal blocked/rejected. Si no puedes inspeccionar, usa degraded "
            "u observed_failure como resultado de soporte."
        )
    forbidden_path_hints = [
        str(item).strip()
        for item in list(contract.get("forbidden_path_hints", []) or [])
        if str(item).strip()
    ]
    allowed_module_path_hints = [
        str(item).strip()
        for item in list(contract.get("allowed_module_path_hints", []) or [])
        if str(item).strip()
    ]
    if forbidden_path_hints:
        lines.append(
            f"forbidden_write_paths: [{', '.join(forbidden_path_hints[:6])}]"
            "  # prohibe CREAR o MODIFICAR estos paths — leer/inspeccionar está siempre permitido"
        )
    if allowed_module_path_hints:
        if role_upper == "ENGINEER":
            lines.append(
                f"allowed_module_scope: [{', '.join(allowed_module_path_hints[:6])}, __init__.py]"
            )
        else:
            lines.append(
                f"visible_project_scope: [{', '.join(allowed_module_path_hints[:8])}]"
            )
            lines.append(
                "scope_rule: trata estos paths como evidencia visible del workspace actual; "
                "si el objetivo menciona solo un basename, resuelvelo contra este scope antes de bloquear."
            )
    if upstream_lines:
        lines.append("upstream_context:")
        lines.extend(upstream_lines)
    lines.append("[/PHASE_CONTRACT]")
    return "\n".join(lines)


def _extract_authoritative_lead_objective(
    *,
    lead_output: str,
    user_message: str,
) -> str:
    text = str(lead_output or "").strip()
    candidates: list[str] = []
    objective_heading = re.search(
        r"(?ims)^\s{0,3}#{1,6}\s*(?:\[[^\]]+\]\s*)?(?:objective|objetivo)\b[:\s-]*(.*?)(?=^\s{0,3}#{1,6}\s|\Z)",
        text,
    )
    if objective_heading:
        candidates.append(objective_heading.group(1))
    inline_objective = re.search(
        r"(?im)^\s*(?:\*\*)?(?:slice_)?(?:objective|objetivo)(?:\*\*)?\s*[:=-]\s*(.+)$",
        text,
    )
    if inline_objective:
        candidates.append(inline_objective.group(1))
    bracket_objective = re.search(
        r"(?is)\[(?:OBJECTIVE|OBJETIVO)\]\s*(.*?)(?=\n\s*\[[A-Z_]+\]|\Z)",
        text,
    )
    if bracket_objective:
        candidates.append(bracket_objective.group(1))
    candidates.append(str(user_message or ""))

    for candidate in candidates:
        normalized = re.sub(r"[*_`#>-]+", " ", str(candidate or ""))
        normalized = re.sub(r"\s+", " ", normalized).strip(" :-\"'")
        if normalized and normalized not in {"|", ">"} and len(normalized) >= 8:
            return normalized[:700]
    return ""


def _bind_default_phases_to_lead_objective(
    phases: list[PhaseSpec],
    *,
    plan_source: str,
    lead_output: str,
    user_message: str,
) -> list[PhaseSpec]:
    if str(plan_source or "").strip() != "default":
        return list(phases or [])
    objective = _extract_authoritative_lead_objective(
        lead_output=lead_output,
        user_message=user_message,
    )
    if not objective:
        return list(phases or [])

    bound: list[PhaseSpec] = []
    for spec in list(phases or []):
        phase_id = str(spec.phase_id or "").strip()
        role = str(spec.role or "").strip()
        current = str(spec.objective or "").strip()
        if phase_id.startswith("plan_"):
            prefix = (
                f"Objetivo autoritativo del Lead/usuario: {objective}. "
                "No sustituyas este objetivo por otro de historial o memoria. "
            )
        elif role.upper() == "ENGINEER" or _is_build_like_phase_name(phase_id):
            prefix = (
                f"Ejecuta el slice aprobado para este objetivo autoritativo: {objective}. "
                "Usa plan_engineering como contrato operativo; si falta detalle, inspecciona "
                "el workspace y aplica el menor cambio coherente con el objetivo, sin cambiar de slice. "
            )
        else:
            prefix = (
                f"Valida contra este objetivo autoritativo: {objective}. "
                "No aceptes ni rechaces por objetivos heredados distintos. "
            )
        if current and objective.lower() in current.lower():
            new_objective = current
        else:
            new_objective = f"{prefix}{current}".strip()
        bound.append(
            PhaseSpec(
                phase_id=phase_id,
                role=role,
                objective=new_objective,
                depends_on=list(spec.depends_on or []),
            )
        )
    return bound


def _persist_planning_markdown(
    *,
    workspace: Path,
    task_root: str,
    run_mode: str,
    message: str,
    lead_output: str,
    planned_phases: list[dict[str, object]],
    quorum_result: QuorumResult | None = None,
) -> Path | None:
    normalized_mode = str(run_mode or "").strip().lower()
    if normalized_mode not in _PLANNING_RUN_MODES:
        return None

    output_dir = _resolve_project_plan_dir(workspace)
    output_dir.mkdir(parents=True, exist_ok=True)

    title_source = next(
        (line.strip() for line in str(message or "").splitlines() if line.strip()),
        "",
    )
    title = title_source[:120] if title_source else f"Plan {task_root}"
    timestamp = local_now()
    file_name = (
        f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}"
        f"_{_safe_plan_slug(normalized_mode)}"
        f"_{_safe_plan_slug(task_root, fallback='chat')[:48]}.md"
    )
    plan_path = output_dir / file_name

    phase_lines = []
    for item in planned_phases:
        phase_id = str(item.get("phase_id", "") or "").strip()
        role = str(item.get("role", "") or "").strip()
        objective = str(item.get("objective", "") or "").strip()
        depends_on = [str(dep).strip() for dep in list(item.get("depends_on", []) or []) if str(dep).strip()]
        line = f"- `{phase_id}` · {role} · {objective or 'sin objetivo'}"
        if depends_on:
            line += f" · depends_on: {', '.join(depends_on)}"
        phase_lines.append(line)

    plan_sections = [
        f"# Plan: {title}",
        "",
        f"**Modo**: `{normalized_mode}`",
        f"**Fecha**: `{timestamp.isoformat()}`",
        f"**Task ID**: `{task_root}`",
        "",
        "## Solicitud",
        str(message or "").strip() or "_sin mensaje_",
        "",
        "## Fases planificadas",
        "\n".join(phase_lines) if phase_lines else "_sin fases dinamicas_",
        "",
        "## Salida del Lead",
        str(lead_output or "").strip() or "_sin salida_",
        "",
    ]
    if quorum_result is not None and (
        quorum_result.applied or list(quorum_result.consultant_plans or [])
    ):
        consultant_lines = [
            (
                f"- `{item.adapter or 'unknown'}`"
                f" · {item.provider or 'unknown'}"
                f" · {item.model or 'unknown'}"
                f" · status={item.status or 'consulted'}"
            )
            for item in list(quorum_result.consultant_plans or [])
        ]
        plan_sections.extend(
            [
                "## Quorum del Lead",
                f"- origen: `{'lead_quorum' if quorum_result.applied else 'lead_only_fallback'}`",
                (
                    f"- lead_inicial: `{quorum_result.lead_adapter or 'unknown'}`"
                    f" · {quorum_result.lead_provider or 'unknown'}"
                    f" · {quorum_result.lead_model or 'unknown'}"
                ),
                (
                    f"- lead_final: `{quorum_result.final_adapter or quorum_result.lead_adapter or 'unknown'}`"
                    f" · {quorum_result.final_provider or quorum_result.lead_provider or 'unknown'}"
                    f" · {quorum_result.final_model or quorum_result.lead_model or 'unknown'}"
                ),
                (
                    f"- skipped_reason: `{quorum_result.skipped_reason}`"
                    if quorum_result.skipped_reason
                    else "- skipped_reason: `_none_`"
                ),
                "### Consultores",
                "\n".join(consultant_lines) if consultant_lines else "_sin consultores efectivos_",
                "",
            ]
        )
    plan_path.write_text("\n".join(plan_sections), encoding="utf-8")
    return plan_path




from api.utils import (
    _truncate_text,
    _read_json_payload,
    _read_jsonl_records,
    _read_runtime_tasks_payload,
    _read_runtime_workflow_state,
    _display_ts_local,
    _peer_consultation_summary_fields,
    _specialist_insight_fields,
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
    _message_requests_close_pending,
    _collect_continuation_target_pending_details,
    _phase_specs_from_pending_details,
    _close_pending_plan_requires_repair,
    _build_project_continuity_context,
    _build_continuation_target_context,
    _build_current_workspace_grounding_context,
    _build_scout_project_state_context,
    _build_scout_session_history_context,
    _chat_round_budget,
    _sanitize_project_name,
    _allocate_project_path,
    _detect_notebooklm_status,
    resolve_runtime_dir,
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
        runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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


def _maybe_deposit_minimal_output(
    workspace: Path,
    lead_output: str,
    chat_id: str,
    run_mode: str = "",
) -> "str | None":
    """C3: Deposita PROJECT_PLAN.md en el workspace si esta vacio de artefactos de producto.

    Condiciones de activacion (TODAS deben cumplirse):
    1. Existe workspace (proyecto externo, no el propio repo AI Teams)
    2. El workspace no tiene archivos de producto fuera de .aiteam/
    3. lead_intake completo con output valido (lead_output no vacio)
    4. La run NO es de modo probe (probe ya devuelve el plan via API)

    Retorna el path del archivo creado, o None si no se deposito nada.
    """
    if not workspace or not workspace.exists():
        return None
    if not lead_output or not lead_output.strip():
        return None
    if str(run_mode or "").strip().lower() == "probe":
        return None
    # Skip if workspace IS the project root (ai teams itself, not an external project)
    try:
        workspace_resolved = workspace.resolve()
        project_resolved = Path(PROJECT_ROOT).resolve()
        if workspace_resolved == project_resolved:
            return None
    except Exception:
        return None

    def _workspace_has_product_files(root: Path) -> bool:
        aiteam_dir = root / ".aiteam"
        ignored_names = {".gitignore", ".gitkeep"}
        try:
            aiteam_resolved = aiteam_dir.resolve()
        except Exception:
            aiteam_resolved = aiteam_dir
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                entries = list(current.iterdir())
            except PermissionError:
                continue
            except Exception:
                continue
            for entry in entries:
                try:
                    entry_resolved = entry.resolve()
                except Exception:
                    entry_resolved = entry
                if str(entry_resolved).startswith(str(aiteam_resolved)):
                    continue
                if entry.name in ignored_names:
                    continue
                try:
                    if entry.is_file():
                        return True
                    if entry.is_dir():
                        pending.append(entry)
                except PermissionError:
                    continue
                except Exception:
                    continue
        return False

    if _workspace_has_product_files(workspace):
        return None  # Already has product artifacts
    # Deposit minimal plan
    plan_path = workspace / "PROJECT_PLAN.md"
    plan_content = (
        "# Plan del Proyecto\n\n"
        f"> Generado automáticamente por AI Teams · Run `{chat_id}`\n"
        ">\n"
        "> La run planificó correctamente pero no alcanzó la fase de ejecución.\n"
        "> Este archivo es el punto de partida para la siguiente run.\n\n"
        "---\n\n"
        f"{lead_output.strip()}\n"
    )
    try:
        plan_path.write_text(plan_content, encoding="utf-8")
        return str(plan_path)
    except Exception:
        return None


def _archive_incomplete_tasks_for_root(
    runtime_dir: Path,
    *,
    task_root: str,
    reason: str,
) -> int:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return 0
    archived_count = 0
    try:
        import sqlite3 as _sqlite3

        db_path = runtime_dir / "aiteam.db"
        if not db_path.exists():
            return 0
        with _sqlite3.connect(str(db_path)) as _conn:
            _rows = _conn.execute(
                "SELECT task_id, payload FROM tasks WHERE task_id LIKE ?",
                (f"{normalized_root}::%",),
            ).fetchall()
            _to_archive = []
            for _tid, _raw in _rows:
                try:
                    _payload = json.loads(_raw)
                except Exception:
                    continue
                if _payload.get("state") in (
                    "completed",
                    "failed",
                    "archived",
                    "cancelled",
                ):
                    continue
                _payload["state"] = "archived"
                _payload.setdefault("metadata", {})["archived_reason"] = reason
                _to_archive.append((json.dumps(_payload), _tid))
            if _to_archive:
                _conn.executemany(
                    "UPDATE tasks SET payload = ? WHERE task_id = ?",
                    _to_archive,
                )
                _conn.commit()
                archived_count = len(_to_archive)
    except Exception:
        return 0
    return archived_count


def _classify_clarification_continuation_policy(
    question: str,
    clarification: str,
) -> str:
    answer = re.sub(r"\s+", " ", str(clarification or "")).strip().lower()
    if not answer:
        return "auto"
    if any(
        token in answer
        for token in (
            "force continue",
            "force_continue",
            "forzar continu",
            "seguir a la fuerza",
            "continua igualmente",
            "continúa igualmente",
            "continua igual",
            "continúa igual",
        )
    ):
        return "force_continue"
    if any(
        token in answer
        for token in (
            "clean retry",
            "clean_retry",
            "retry limpio",
            "reintento limpio",
            "nuevo objetivo",
            "nueva run limpia",
        )
    ):
        return "clean_retry"
    if "clean retry" in str(question or "").lower():
        if "clean" in answer or "retry" in answer:
            return "clean_retry"
    return "auto"


def _sanitize_message_for_clean_retry(message: str, continuation_of: str) -> str:
    text = str(message or "")
    if continuation_of:
        text = re.sub(
            rf"\bcontinue\s+from\s+{re.escape(continuation_of)}\.?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(
        r"\bcontinue\s+from\s+CHAT-[0-9A-Za-z]{8}\.?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*[\r\n]+", "", text)
    return text.strip()


def _canonicalize_clarification_for_prompt(
    clarification: str,
    selected_policy: str,
) -> str:
    raw = re.sub(r"\s+", " ", str(clarification or "")).strip()
    if selected_policy == "clean_retry":
        return "clean retry"
    if selected_policy == "force_continue":
        return "force continue"
    return raw


@app.post("/api/aiteam/chat")
async def post_aiteam_chat(payload: TeamChatRequest, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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

        # C2: apply continuation_policy before the run starts
        _continuation_policy = str(getattr(payload, "continuation_policy", "auto") or "auto").strip().lower()
        if _continuation_policy == "clean_retry":
            _archived_ids = orch.taskboard.archive_incomplete_tasks(reason="clean_retry_requested")
            if _archived_ids:
                orch.event_logger.emit(
                    "clean_retry_archived",
                    {"archived_count": len(_archived_ids), "archived_task_ids": _archived_ids},
                )

        previous_runs = _recent_chat_roots(runtime_dir, max_chats=3)
        previous_root = previous_runs[0] if previous_runs else {}
        implicit_continuation_source = _default_implicit_continuation_source(previous_runs)
        previous_by_root: dict[str, dict[str, object]] = {
            str(item.get("root_id", "")).upper(): item
            for item in previous_runs
            if isinstance(item, dict)
            and str(item.get("root_id", "")).upper().startswith("CHAT-")
        }
        explicit_continuation_target = _normalize_task_root(
            str(getattr(payload, "continuation_target", "") or "")
        )
        continuation_requested = bool(explicit_continuation_target) or _is_continuation_message(payload.message)
        continuation_target = explicit_continuation_target or _extract_chat_root_from_message(payload.message)
        continuation_effective = bool(continuation_requested)
        continuation_of = ""
        continuation_snapshot = ""
        continuation_source: dict[str, object] = {}
        continuation_block_reason = ""
        implicit_continuation_defaulted = False
        preplan_surface_hints = _detect_preplan_surface_hints(payload.message)
        preplan_signal_block = _build_preplan_signal_block(preplan_surface_hints)
        if continuation_requested:
            if continuation_target and continuation_target in previous_by_root:
                continuation_source = previous_by_root.get(continuation_target, {})
            elif continuation_target:
                # RC-G: The user named a specific CHAT-XXXXXXX that does NOT exist in
                # this project's runtime.  Set the block_reason for observability but
                # keep continuation_effective=True so close-pending repair can still run
                # via _collect_continuation_target_pending_details (which reads SQLite
                # workflow state, not just the tasks list).
                continuation_source = {}
                continuation_block_reason = "target_not_found_in_current_project"
            else:
                # The common operator intent is "continue this project", not
                # "continue an arbitrary historical chat".  Default to the latest
                # actionable run in the current project and reserve explicit CHAT
                # selection for the exceptional case where the user wants history.
                if implicit_continuation_source:
                    continuation_source = implicit_continuation_source
                    implicit_continuation_defaulted = True
                    orch.event_logger.emit(
                        "chat_continuation_defaulted_to_latest_project_run",
                        {
                            "task_id": str(
                                implicit_continuation_source.get("root_id", "")
                                or ""
                            ),
                            "source": "implicit_project_continuation",
                        },
                    )
                else:
                    continuation_source = {}
                    continuation_effective = False
                    continuation_snapshot = "project_context_fallback"

        if continuation_requested and continuation_source:
            continuation_of = str(continuation_source.get("root_id", "") or "")
            previous_verdict = continuation_source.get("run_verdict", {})
            previous_verdict_dict = (
                previous_verdict if isinstance(previous_verdict, dict) else {}
            )
            previous_verdict_state = str(
                previous_verdict_dict.get("state", "") or ""
            ).strip().lower()
            # RC-F: detect meta-system / infrastructure failures in prior run.
            # These reason codes indicate that the SYSTEM failed, not the project
            # work.  A prior run blocked only by these should not prevent continuation
            # — blocking it creates a deadlock where require_execution_plan=True fires
            # on build but no structured plan is ever injected into metadata.
            _INFRA_META_MARKERS: tuple[str, ...] = (
                "missing_execution_plan_required",
                "build_phase_missing",
                "no_implementation_phase",
                "no_execution_evidence",
                "no_successful_execution_steps",
                "build_phase_empty_result",
                "build_phase_placeholder_output",
                "phase_failed:",       # build task failed before running (system error)
                ":not_completed",      # phase did not complete (cascade from blocked build)
                "routing_failure",
                "no_eligible_adapter",
                "infrastructure_routing_failure",
            )
            _prior_reason_codes = [
                str(r) for r in (previous_verdict_dict.get("reason_codes", []) or [])
                if str(r).strip()
            ]
            _prior_all_infra = bool(_prior_reason_codes) and all(
                any(m in code for m in _INFRA_META_MARKERS)
                for code in _prior_reason_codes
            )
            if (
                continuation_of
                and _continuation_policy != "force_continue"
                and not implicit_continuation_defaulted
                and previous_verdict_state in {"failed", "rejected"}
                and not _prior_all_infra
            ):
                continuation_effective = False
                continuation_block_reason = (
                    f"prior_run_{previous_verdict_state}"
                )
                continuation_snapshot = (
                    f"blocked:{previous_verdict_state}:use_force_continue_or_clean_retry"
                )
            else:
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
        requested_mode = str(payload.mode or "").strip().lower()
        probe_mode = requested_mode == "probe"
        plan_only_mode = requested_mode in {"plan", "planning", "planning_only", "plan_only"}
        chat_mode = _normalize_chat_mode(payload.mode)
        run_profile = _normalize_run_profile(
            str(getattr(payload, "run_profile", "") or ""),
            chat_mode=chat_mode,
        )
        direct_profile_mode = run_profile in {"solo_lead", "lead_quorum"}
        # lead_quorum ejecuta como solo_lead (el Lead escribe codigo directamente)
        # pero activa el quorum deliberativo sobre el plan antes de ejecutar.
        # lead_quorum y ai_teams_full activan quorum deliberativo en el plan
        _profile_forces_quorum = run_profile in {"lead_quorum", "ai_teams_full"}
        # ai_team_basic usa team mode pero con un solo ciclo de delegacion
        _profile_basic_team = run_profile == "ai_team_basic"
        response_mode = "probe" if probe_mode else ("plan" if plan_only_mode else chat_mode)
        round_budget = _resolve_chat_round_budget(
            requested_rounds=payload.max_rounds,
            chat_mode=chat_mode,
            complexity=complexity,
            criticality=criticality,
        )
        preplan_context_pressure = _estimate_preplan_context_pressure(
            runtime_dir=runtime_dir,
            continuation_requested=continuation_effective,
            continuation_of=(continuation_of if continuation_effective else ""),
            continuation_snapshot=continuation_snapshot,
        )
        require_build_execution_plan = not bool(continuation_effective)
        lead_task_id = f"{task_root}::lead_intake"
        _run_ws = orch._get_workflow_state(task_root)
        _run_ws["run_status"] = "running"
        _run_ws.setdefault("run_started_at", local_now_iso())
        orch._save_workflow_state(task_root)

        if continuation_requested and not continuation_target and continuation_block_reason == "ambiguous_target_required":
            clarification_question = (
                "He detectado una continuacion, pero necesito el chat exacto. "
                "Indica `Continue from CHAT-XXXXXXXX` o usa el boton Continue sobre la run que quieres retomar."
            )
            lead_intake_task = WorkTask(
                task_id=lead_task_id,
                title="Lead intake and planning",
                description=(
                    "Aclaracion requerida antes de continuar la run.\n"
                    f"Solicitud original:\n{payload.message}\n"
                    "Entrega: identificar el chat exacto a retomar."
                ),
                role=Role.TEAM_LEAD,
                complexity=complexity,
                criticality=criticality,
                metadata={
                    **build_chat_task_policy_metadata(),
                    "phase": "lead_intake",
                    "chat_preferred_role": preferred_role.value,
                    "continuation_requested": True,
                    "continuation_effective": False,
                    "continuation_of": "",
                    "continuation_snapshot": continuation_snapshot,
                    "continuation_block_reason": continuation_block_reason,
                },
            )
            orch.submit_task(lead_intake_task)
            orch.taskboard.mark_waiting_user(lead_task_id, question=clarification_question)
            orch.event_logger.emit(
                "chat_continuation_blocked",
                {
                    "task_id": task_root,
                    "continuation_of": "",
                    "reason": continuation_block_reason,
                    "snapshot": continuation_snapshot,
                    "source": "user_input",
                },
            )
            pending_file = runtime_dir / f"pending_clarification_{task_root}.json"
            pending_file.write_text(
                json.dumps(
                    {
                        "type": "lead_intake",
                        "task_root": task_root,
                        "question": clarification_question,
                        "original_message": payload.message,
                        "original_payload": payload.model_dump(),
                        "created_at": local_now_iso(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            ws = orch._get_workflow_state(task_root)
            ws["continuation_requested"] = True
            ws["continuation_effective"] = False
            ws["continuation_of"] = ""
            ws["continuation_snapshot"] = continuation_snapshot
            ws["continuation_block_reason"] = continuation_block_reason
            ws["user_message"] = payload.message
            ws["run_status"] = "waiting_user"
            orch._save_workflow_state()
            orch.event_logger.emit(
                "chat_waiting_user",
                {"task_id": task_root, "question": clarification_question},
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="waiting_user",
                response=clarification_question,
                decision_justification="Se requiere un chat explicito para continuar sin ambiguedad.",
                elapsed_ms=0,
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=response_mode,
                round_budget=round_budget,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                continuation_requested=True,
                continuation_effective=False,
                continuation_of="",
                waiting_user=True,
                clarification_question=clarification_question,
                is_sim_mode=sim_mode_enabled(),
            )
        if (
            continuation_requested
            and not continuation_effective
            and continuation_block_reason
            and continuation_block_reason != "ambiguous_target_required"
        ):
            if continuation_block_reason == "prior_run_rejected":
                clarification_question = (
                    f"La run {continuation_of or continuation_target or 'objetivo'} terminó rechazada. "
                    "Confirma si quieres `force continue` sobre esa run o prefieres un clean retry con nuevo objetivo."
                )
            elif continuation_block_reason == "prior_run_failed":
                clarification_question = (
                    f"La run {continuation_of or continuation_target or 'objetivo'} terminó fallida. "
                    "Confirma si quieres retomar exactamente esa run con `force continue` o abrir un clean retry. "
                    f"(continuation_of={continuation_of or continuation_target or ''})"
                )
            elif continuation_block_reason == "target_not_found_in_current_project":
                clarification_question = (
                    "El chat indicado no existe en este proyecto. "
                    "Indica un `CHAT-XXXXXXXX` válido de este workspace o abre un clean retry."
                )
            else:
                clarification_question = (
                    "No pude aplicar la continuation solicitada. "
                    "Confirma si quieres un clean retry o especifica el target exacto a retomar."
                )

            lead_intake_task = WorkTask(
                task_id=lead_task_id,
                title="Lead intake and planning",
                description=(
                    "Continuation bloqueada antes de planificar la run.\n"
                    f"Solicitud original:\n{payload.message}\n"
                    "Entrega: decidir entre force continue, clean retry o target alternativo."
                ),
                role=Role.TEAM_LEAD,
                complexity=complexity,
                criticality=criticality,
                metadata={
                    **build_chat_task_policy_metadata(),
                    "phase": "lead_intake",
                    "chat_preferred_role": preferred_role.value,
                    "continuation_requested": True,
                    "continuation_effective": False,
                    "continuation_of": continuation_of,
                    "continuation_snapshot": continuation_snapshot,
                    "continuation_block_reason": continuation_block_reason,
                },
            )
            orch.submit_task(lead_intake_task)
            orch.taskboard.mark_waiting_user(lead_task_id, question=clarification_question)
            orch.event_logger.emit(
                "chat_continuation_blocked",
                {
                    "task_id": task_root,
                    "continuation_of": continuation_of,
                    "reason": continuation_block_reason,
                    "snapshot": continuation_snapshot,
                    "source": "run_verdict",
                },
            )
            pending_file = runtime_dir / f"pending_clarification_{task_root}.json"
            pending_file.write_text(
                json.dumps(
                    {
                        "type": "lead_intake",
                        "task_root": task_root,
                        "question": clarification_question,
                        "original_message": payload.message,
                        "original_payload": payload.model_dump(),
                        "created_at": local_now_iso(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            ws = orch._get_workflow_state(task_root)
            ws["continuation_requested"] = True
            ws["continuation_effective"] = False
            ws["continuation_of"] = continuation_of
            ws["continuation_snapshot"] = continuation_snapshot
            ws["continuation_block_reason"] = continuation_block_reason
            ws["user_message"] = payload.message
            ws["run_status"] = "waiting_user"
            orch._save_workflow_state()
            orch.event_logger.emit(
                "chat_waiting_user",
                {"task_id": task_root, "question": clarification_question},
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="waiting_user",
                response=clarification_question,
                decision_justification=(
                    "La continuation solicitada no puede aplicarse automáticamente; "
                    "se requiere decisión explícita del usuario."
                ),
                elapsed_ms=0,
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=response_mode,
                round_budget=round_budget,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                continuation_requested=True,
                continuation_effective=False,
                continuation_of=continuation_of,
                waiting_user=True,
                clarification_question=clarification_question,
                is_sim_mode=sim_mode_enabled(),
            )

        _objective_clarification_snapshot = _workspace_artifact_snapshot(workspace)
        if _requires_explicit_project_objective_clarification(
            payload.message,
            workspace_snapshot=_objective_clarification_snapshot,
            continuation_requested=continuation_requested,
        ):
            clarification_question = (
                "¿Cuál es el objetivo específico del proyecto que debo planificar? "
                "Ejemplos válidos: `Implementar CLI de generación de reportes Markdown`, "
                "`Crear API REST para gestión de usuarios`, `Corregir autenticación y cobertura de tests`. "
                "Sin ese objetivo concreto, un clean retry genérico tenderá a fallar de nuevo."
            )
            lead_intake_task = WorkTask(
                task_id=lead_task_id,
                title="Lead intake and planning",
                description=(
                    "Objetivo del proyecto todavía demasiado genérico para planificar de forma segura.\n"
                    f"Solicitud original:\n{payload.message}\n"
                    "Entrega: pedir al usuario el objetivo concreto del proyecto o slice antes de abrir workflow."
                ),
                role=Role.TEAM_LEAD,
                complexity=complexity,
                criticality=criticality,
                metadata={
                    **build_chat_task_policy_metadata(),
                    "phase": "lead_intake",
                    "chat_preferred_role": preferred_role.value,
                    "objective_clarification_required": True,
                    "workspace_product_scope_detected": False,
                    "continuation_requested": continuation_requested,
                    "continuation_effective": continuation_effective,
                    "continuation_of": continuation_of,
                    "continuation_snapshot": continuation_snapshot,
                },
            )
            orch.submit_task(lead_intake_task)
            orch.taskboard.mark_waiting_user(lead_task_id, question=clarification_question)
            pending_file = runtime_dir / f"pending_clarification_{task_root}.json"
            pending_file.write_text(
                json.dumps(
                    {
                        "type": "lead_intake",
                        "task_root": task_root,
                        "question": clarification_question,
                        "original_message": payload.message,
                        "original_payload": payload.model_dump(),
                        "created_at": local_now_iso(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            ws = orch._get_workflow_state(task_root)
            ws["user_message"] = payload.message
            ws["run_status"] = "waiting_user"
            ws["objective_clarification_required"] = True
            ws["workspace_product_scope_detected"] = False
            orch._save_workflow_state()
            orch.event_logger.emit(
                "chat_waiting_user",
                {
                    "task_id": task_root,
                    "question": clarification_question,
                    "reason": "generic_project_objective_requires_clarification",
                },
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="waiting_user",
                response=clarification_question,
                decision_justification=(
                    "El objetivo del proyecto sigue siendo demasiado genérico para abrir planning sin deriva."
                ),
                elapsed_ms=0,
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=response_mode,
                round_budget=round_budget,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                continuation_requested=continuation_requested,
                continuation_effective=continuation_effective,
                continuation_of=continuation_of,
                waiting_user=True,
                clarification_question=clarification_question,
                is_sim_mode=sim_mode_enabled(),
            )

        # ── Instruccion de WORKFLOW_PLAN para el prompt del Lead ────────────
        _IMPL_KEYWORDS = (
            "start", "next slice", "implement", "build", "create", "write", "generate",
            "siguiente", "implementa", "comienza", "construye", "escribe", "genera",
            "crea", "prueba", "siguiente slice", "proximo", "proxima", "deploy",
        )
        _message_lower = payload.message.lower()
        _user_wants_implementation = any(kw in _message_lower for kw in _IMPL_KEYWORDS)

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
            "[/WORKFLOW_PLAN]\n"
            "REGLAS CRITICAS DEL WORKFLOW_PLAN:\n"
            "1. El campo `objective` es OBLIGATORIO y debe ser especifico para cada fase.\n"
            "   INVALIDO: 'Ejecutar fase: engineer_toc_implementation'\n"
            "   VALIDO:   'Implementar tabla de contenidos con anclas y nivel de profundidad configurable'\n"
            "2. El campo `depends_on` es OBLIGATORIO para fases ENGINEER, REVIEWER y QA.\n"
            "   Si la fase no depende de ninguna otra, escribe: depends_on: []\n"
            "3. En runs de CONTINUACION: si retomas una fase de una run anterior, COPIA su\n"
            "   objetivo real desde el contexto del historial. Un ENGINEER sin objective\n"
            "   especifico reportara BLOQUEADA sin producir codigo.\n"
            "4. CONTINUACION + IMPLEMENTACION: si el mensaje del usuario contiene palabras\n"
            "   como 'start', 'next slice', 'implement', 'siguiente', 'implementa', 'crea',\n"
            "   'construye' o 'escribe', el plan DEBE incluir al menos una fase ENGINEER.\n"
            "   Un plan con solo fases RESEARCHER en este contexto es invalido — el\n"
            "   diagnostico previo ya existe en el historial; no lo repitas.\n"
            "5. BLOQUEOS DE INFRAESTRUCTURA: si el lead_memory o el historial de sesiones\n"
            "   muestra runs anteriores marcadas como 'BLOQUEADO IRRECUPERABLEMENTE' cuya\n"
            "   causa fue HTTP 429, HTTP 403, routing failure o agotamiento de recursos,\n"
            "   NO las trates como estado actual del proyecto. Escribe en tu output:\n"
            "   'Bloqueos anteriores: infraestructura transitoria (runs X, Y) — proyecto sano.'\n"
            "   Luego planifica el slice pendiente normalmente. Los workers leen tu output\n"
            "   para determinar el estado del proyecto — si no lo aclaras, se auto-bloquean.\n"
            "6. Las fases RESEARCHER son de apoyo: sirven para compactar restricciones, riesgos\n"
            "   y supuestos para ahorrar contexto al Lead, pero no deben ser el cuello de botella\n"
            "   de una run incremental normal. Si el workspace actual y el Lead ya permiten decidir\n"
            "   el slice, usa RESEARCHER como advisory y no serialices todo el workflow detras de esa fase.\n"
            "7. Si incluyes cualquier fase ENGINEER que pueda crear o modificar codigo, el plan debe\n"
            "   incluir al menos una fase REVIEWER y una fase QA posteriores, dependientes de esa fase\n"
            "   ENGINEER. Excepcion: si el run_mode es planning_only, no incluyas ENGINEER."
        )

        # Mandate block injected when this is a continuation run that asks for
        # implementation work.  Prevents the Lead from generating a research-only plan
        # when prior diagnostic context already exists in the history.
        _continuation_impl_mandate = ""
        if continuation_effective and _user_wants_implementation:
            _mandate_source = f" (continua desde {continuation_of})" if continuation_of else ""
            _continuation_impl_mandate = (
                "\n\n== DIRECTRIZ DE CONTINUACION-IMPLEMENTACION =="
                f"\nEsta es una run de continuacion{_mandate_source}."
                "\nEl historial ya contiene investigacion y diagnostico previos."
                "\nPROHIBIDO: generar un plan con solo fases RESEARCHER."
                "\nOBLIGATORIO: tu [WORKFLOW_PLAN] debe incluir al menos una fase ENGINEER"
                " con un objetivo especifico y concreto extraido del historial."
                "\nSi esa fase ENGINEER puede modificar codigo, tambien debes incluir REVIEWER y QA posteriores."
                "\nSi el ultimo engineer reporto BLOQUEADA por objective generico,"
                " asigna ahora el objetivo real (no 'Ejecutar fase: X')."
                "\n== FIN DIRECTRIZ =="
            )

        _close_pending_requested = bool(
            continuation_effective and _message_requests_close_pending(payload.message)
        )
        _continuation_close_pending_mandate = ""
        if _close_pending_requested:
            _close_source = f" {continuation_of}" if continuation_of else ""
            _continuation_close_pending_mandate = (
                "\n\n== DIRECTRIZ DE CONTINUACION-CIERRE =="
                f"\nEsta run continua desde{_close_source} y el usuario pidio cerrar fases pendientes primero."
                "\nOBLIGATORIO: prioriza las fases pendientes/no resueltas del continuation target antes de abrir un slice nuevo."
                "\nPROHIBIDO: sustituir este pedido por 'next highest-impact slice' u otro objetivo historico mas antiguo mientras sigan pendientes visibles."
                "\nSi hace falta replantear, el [WORKFLOW_PLAN] debe representar el cierre o la replanificacion minima de esas fases pendientes."
                "\n== FIN DIRECTRIZ =="
            )

        continuation_target_context = _build_continuation_target_context(
            runtime_dir,
            continuation_of,
            current_message=payload.message,
        )
        continuity_context = _build_project_continuity_context(runtime_dir)
        current_workspace_grounding = _build_current_workspace_grounding_context(workspace)
        continuity_block = f"\n\n{continuity_context}\n" if continuity_context else ""
        continuation_target_block = (
            f"\n\n{continuation_target_context}\n"
            if continuation_target_context
            else ""
        )
        current_workspace_grounding_block = (
            f"\n\n{current_workspace_grounding}\n"
            if current_workspace_grounding
            else ""
        )
        curated_context_block = _build_curated_context_block(
            runtime_dir=runtime_dir,
            workspace=workspace,
            continuation_of=(continuation_of if continuation_effective else ""),
        )
        lead_memory_block = build_memory_prompt_block(
            runtime_dir=runtime_dir,
            project_root=workspace,
            direct_profile=direct_profile_mode,
        )
        mcp_status_rows = (
            orch.mcp_manager.server_status()
            if getattr(orch, "mcp_manager", None) is not None
            else []
        )
        lead_memory_prompt_block = (
            f"\n\n{lead_memory_block}\n" if lead_memory_block else ""
        )
        capabilities_briefing = build_capabilities_briefing(
            router=orch.router,
            mcp_status=mcp_status_rows,
        )
        capabilities_briefing_block = (
            f"\n\n{capabilities_briefing}\n" if capabilities_briefing else ""
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
                "run_profile": run_profile,
                "message": payload.message,
                "continuation_requested": continuation_requested,
                "continuation_effective": continuation_effective,
                "continuation_of": continuation_of,
                "continuation_block_reason": continuation_block_reason,
            },
        )
        if continuation_requested and continuation_source:
            previous_source_verdict = continuation_source.get("run_verdict", {})
            if isinstance(previous_source_verdict, dict) and bool(
                previous_source_verdict.get("reconstructed_from_phase_verdicts", False)
            ):
                orch.event_logger.emit(
                    "chat_continuation_source_reconstructed",
                    {
                        "task_id": task_root,
                        "continuation_of": continuation_of,
                        "reason_codes": list(
                            previous_source_verdict.get("reason_codes", []) or []
                        )[:12],
                        "source": "phase_verdicts",
                    },
                )
        if continuation_requested and not continuation_effective and continuation_block_reason:
            orch.event_logger.emit(
                "chat_continuation_blocked",
                {
                    "task_id": task_root,
                    "continuation_of": continuation_of,
                    "reason": continuation_block_reason,
                    "snapshot": continuation_snapshot,
                    "source": (
                        "phase_verdicts"
                        if isinstance(continuation_source.get("run_verdict", {}), dict)
                        and bool(
                            continuation_source.get("run_verdict", {}).get(
                                "reconstructed_from_phase_verdicts", False
                            )
                        )
                        else "run_verdict"
                    ),
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

        _repair_first_mode = bool(getattr(payload, "repair_first_mode", False)) or bool(
            direct_profile_mode
        )
        _repair_first_prompt_block = (
            "\n[REPAIR_FIRST_MODE]\n"
            "- Los gates materiales (syntax/import/test/build/path-scope) son sensores, no sustituyen el juicio del Lead.\n"
            "- Si aparece un fallo material reparable, prioriza una reparacion minima antes de abrir un slice nuevo.\n"
            "- Dentro del round_budget disponible, puedes adaptar la run: reintentar build, ampliar el cambio minimo relacionado y volver a validar sin pedir permiso.\n"
            "- Si una validacion real falla, el siguiente paso por defecto es reparar y reejecutar ese mismo check, no cerrar con diagnostico.\n"
            "- Los especialistas deben recibir briefs concisos; el Lead conserva la decision y la orquestacion.\n"
            "- No cierres como exito una entrega que no compile o contradiga paths/contratos reales.\n"
            if _repair_first_mode
            else ""
        )

        # ── Descripcion del lead_intake segun modo ──────────────────────────
        if plan_only_mode:
            lead_intake_description = (
                "Eres Team Lead senior en MODO PLAN. Tu salida NO debe ejecutar cambios, "
                "NO debe pedir build, NO debe crear archivos ni exigir entregables de producto.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Entrega: plan accionable, supuestos, riesgos, criterios de aceptacion, "
                "orden recomendado y decision del Lead. Incluye [RUN_MODE: planning_only]. "
                "Si incluyes [WORKFLOW_PLAN], usa solo fases de analisis/planificacion y "
                "no incluyas ENGINEER ni build.\n"
                f"{lead_memory_prompt_block}"
                f"{capabilities_briefing_block}"
                f"{preplan_signal_block}"
                f"{current_workspace_grounding_block}"
                f"{continuation_target_block}"
                f"{curated_context_block}"
                f"{continuity_block}"
                f"{_repair_first_prompt_block}"
            )
        elif chat_mode == "classic":
            lead_intake_description = (
                "Eres Team Lead senior. Escucha al usuario, define alcance y estrategia de ejecucion.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Entrega: objetivos, supuestos, riesgos y orden de trabajo del equipo."
                f"{lead_memory_prompt_block}"
                f"{capabilities_briefing_block}"
                f"{preplan_signal_block}"
                f"{current_workspace_grounding_block}"
                f"{continuation_target_block}"
                f"{curated_context_block}"
                f"{_WORKFLOW_PLAN_INSTRUCTION}"
                f"{_continuation_impl_mandate}"
                f"{_continuation_close_pending_mandate}"
                f"{continuity_block}"
                f"{_repair_first_prompt_block}"
            )
        elif direct_profile_mode:
            lead_intake_description = (
                "Eres Team Lead senior en MODO DIRECT. Este perfil debe comportarse como un agente de coding directo tipo Codex/OpenCode.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Reglas del perfil DIRECT:\n"
                "- No uses scouts, researcher, reviewer, QA, quorum ni delegates salvo que el usuario lo pida explicitamente.\n"
                "- Si la solicitud requiere codigo, emite un [WORKFLOW_PLAN] de una sola fase build; el sistema la convertira a ejecucion directa del Team Lead.\n"
                "- En la fase ejecutora tocaras el workspace directamente, respetando instrucciones del proyecto, rutas reales y checks disponibles.\n"
                "- Los gates son sensores materiales (syntax/import/test/build/path drift), no burocracia ni comite.\n"
                "- Puedes adaptar el slice durante la run si aparece un fallo relacionado: corrige el conjunto minimo coherente y revalida dentro del presupuesto de rondas.\n"
                "- Si el usuario pide que pytest pase, el cierre exitoso requiere reejecutar pytest o el check equivalente que fallo, no solo un smoke parcial.\n"
                "- Si la solicitud no requiere cambios de archivos, puedes responder con [DIRECT_ANSWER].\n"
                f"{lead_memory_prompt_block}"
                f"{capabilities_briefing_block}"
                f"{preplan_signal_block}"
                f"{current_workspace_grounding_block}"
                f"{continuation_target_block}"
                f"{curated_context_block}"
                "\n\n[WORKFLOW_PLAN]\n"
                "- phase_id: build\n"
                "  role: ENGINEER\n"
                "  objective: <cambio concreto a implementar directamente>\n"
                "  depends_on: []\n"
                "[/WORKFLOW_PLAN]\n"
                f"{_continuation_impl_mandate}"
                f"{_continuation_close_pending_mandate}"
                f"{continuity_block}"
                f"{_repair_first_prompt_block}"
            )
        else:
            lead_intake_description = (
                "Eres Team Lead senior. Convierte el input en plan de ejecucion de ventana corta.\n"
                f"Solicitud original:\n{payload.message}\n"
                "Entrega en <=12 lineas: objetivo, backlog priorizado (P0/P1), riesgos y"
                " que se intentara completar en esta corrida."
                f"{lead_memory_prompt_block}"
                f"{capabilities_briefing_block}"
                f"{preplan_signal_block}"
                f"{current_workspace_grounding_block}"
                f"{continuation_target_block}"
                f"{curated_context_block}"
                f"{_WORKFLOW_PLAN_INSTRUCTION}"
                f"{_continuation_impl_mandate}"
                f"{_continuation_close_pending_mandate}"
                f"{continuity_block}"
                f"{_repair_first_prompt_block}"
            )

        # ── PASO 0: Pre-flight scouts (modelos baratos en paralelo) ─────────
        # Los scouts pre-fetchen datos del proyecto sin LLM, luego los resumen
        # con un modelo barato. El lead_intake recibe briefings compactos, no raw context.
        scout_state_id = f"{task_root}::scout_project_state"
        scout_history_id = f"{task_root}::scout_session_history"
        scout_curator_id = f"{task_root}::scout_context_curator"

        _scout_state_raw = _build_scout_project_state_context(workspace)
        _scout_history_raw = _build_scout_session_history_context(
            runtime_dir,
            continuation_of=continuation_of,
            current_message=payload.message,
        )

        _scout_state_task = WorkTask(
            task_id=scout_state_id,
            title="Scout: estado del proyecto",
            description=(
                "Procesa el siguiente contexto del workspace y devuelve:\n"
                "1. La seccion 'workspace snapshot autoritativo' COMPLETA y VERBATIM "
                "(cada linea '- ...' debe aparecer en tu respuesta sin modificar).\n"
                "2. Maximo 2 lineas de contexto git relevante para: "
                f"'{payload.message[:120]}'\n\n"
                f"{_scout_state_raw}\n\n"
                "CRITICO: no comprimas ni omitas ninguna entrada del snapshot de archivos. "
                "El Lead necesita la lista completa para planificar correctamente."
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
                "phase_contract_enforced": True,
                "phase_contract": {
                    "phase_id": "scout_project_state",
                    "role": "SCOUT",
                    "objective": (
                        "Resumir hechos reales y confirmados del workspace actual "
                        "relevantes para la solicitud del usuario."
                    ),
                    "depends_on": [],
                },
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
                "phase_contract_enforced": True,
                "phase_contract": {
                    "phase_id": "scout_session_history",
                    "role": "SCOUT",
                    "objective": (
                        "Extraer hechos recientes del historial del proyecto que sean "
                        "relevantes para la solicitud actual."
                    ),
                    "depends_on": [],
                },
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
                continuation_target_context=continuation_target_context,
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
                "lead_memory_present": bool(lead_memory_block),
                "capabilities_briefing_present": bool(capabilities_briefing),
                "phase_contract_enforced": True,
                "phase_contract": {
                    "phase_id": "scout_context_curator",
                    "role": "SCOUT",
                    "objective": (
                        "Compactar el contexto operativo vigente del proyecto para que "
                        "lead_intake pueda planificar sin arrastrar continuidad vieja."
                    ),
                    "depends_on": ["scout_project_state", "scout_session_history"],
                },
            },
        )

        lead_intake_task = WorkTask(
            task_id=lead_task_id,
            title="Lead intake and planning",
            description=lead_intake_description,
            role=Role.TEAM_LEAD,
            complexity=complexity,
            criticality=criticality,
            dependencies=[] if direct_profile_mode else [scout_state_id, scout_history_id],
            metadata={
                **build_chat_task_policy_metadata(),
                "required_capabilities": ["reasoning"],
                "require_peer_consultation": not direct_profile_mode,
                "skip_peer_consultation": bool(direct_profile_mode),
                "skip_specialist_prefetch": bool(direct_profile_mode),
                "tool_specialist_economic_routing": bool(direct_profile_mode),
                "tool_specialist_default_tier": "advanced_api" if direct_profile_mode else "",
                "preferred_adapters": ["openai_codex_mini"] if direct_profile_mode else [],
                "phase": "lead_intake",
                "chat_preferred_role": preferred_role.value,
                "preplan_surface_hints": dict(preplan_surface_hints),
                "preplan_signal_block": preplan_signal_block,
                "preplan_context_curator_task_id": scout_curator_id,
                "continuation_requested": continuation_requested,
                "continuation_effective": continuation_effective,
                "continuation_of": continuation_of,
                "continuation_snapshot": continuation_snapshot,
                "repair_first_mode": _repair_first_mode,
                "run_profile": run_profile,
                "execution_profile": "direct" if direct_profile_mode else "team",
                "context_pressure_score": int(preplan_context_pressure.get("score", 0) or 0),
                "context_pressure_level": str(preplan_context_pressure.get("level", "") or "").strip(),
                "context_pressure_signals": list(preplan_context_pressure.get("signals", []) or []),
                "context_curator_recommended": bool(
                    preplan_context_pressure.get("recommend_context_curator", False)
                ),
                "optional_support_dependencies": [] if direct_profile_mode else [scout_curator_id],
            },
        )

        _preplan_ws = orch._get_workflow_state(task_root)
        _preplan_ws["preplan_surface_hints"] = dict(preplan_surface_hints)
        _preplan_ws["preplan_signal_block"] = preplan_signal_block
        _preplan_ws["continuation_requested"] = continuation_requested
        _preplan_ws["continuation_effective"] = continuation_effective
        _preplan_ws["continuation_of"] = continuation_of
        _preplan_ws["continuation_snapshot"] = continuation_snapshot
        _preplan_ws["continuation_block_reason"] = continuation_block_reason
        _preplan_ws["repair_first_mode"] = _repair_first_mode
        _preplan_ws["run_profile"] = run_profile
        _preplan_ws["execution_profile"] = "direct" if direct_profile_mode else "team"
        _preplan_ws["user_message"] = payload.message
        _preplan_ws["context_pressure"] = dict(preplan_context_pressure)
        _preplan_ws["context_curator_recommended"] = bool(
            preplan_context_pressure.get("recommend_context_curator", False)
        )
        _preplan_ws["lead_memory"] = lead_memory_block
        _preplan_ws["capabilities_briefing"] = capabilities_briefing
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
                "lead_memory_present": bool(lead_memory_block),
                "capabilities_briefing_present": bool(capabilities_briefing),
            },
        )

        artifact_before = _workspace_artifact_snapshot(workspace)

        started = time.perf_counter()

        # ── PASO 1: scouts en paralelo → lead_intake ─────────────────────────
        # Los scouts (SCOUT role) corren en paralelo con modelos baratos.
        # lead_intake arranca solo cuando ambos scouts completan.
        if not direct_profile_mode:
            orch.submit_task(_scout_state_task)
            orch.submit_task(_scout_history_task)
            orch.submit_task(_scout_curator_task)
        orch.submit_task(lead_intake_task)
        orch.run_until_idle(max_rounds=_LEAD_INTAKE_MAX_ROUNDS)

        # ── E7-C: Delegación bajo demanda [DELEGATE: "query"] ─────────────────
        # El Lead puede solicitar que un scout busque info adicional antes de
        # planificar. El scout responde con el contexto disponible y el Lead
        # replanifica con esa info. Máximo _MAX_DELEGATE_CYCLES ciclos.
        # ai_team_basic usa 1 ciclo (más ligero); full team usa 2.
        _MAX_DELEGATE_CYCLES = 1 if _profile_basic_team else 2
        _delegate_directive_names = [
            "DELEGATE",
            "DELEGATE_REPO_SCAN",
            "DELEGATE_BROWSER_REPRO",
            "DELEGATE_LSP_IMPACT",
            "DELEGATE_TEST_RUN",
            "DELEGATE_MCP_PROBE",
            "WAIT_POLICY",
            "DELEGATE_BUDGET",
        ]
        _seen_lead_delegate_signatures: set[tuple[str, str, str]] = set()
        for _delegate_cycle in range(0 if direct_profile_mode else _MAX_DELEGATE_CYCLES):
            _tmp_ws = orch._get_workflow_state(task_root)
            _tmp_lead_out = _tmp_ws.get("phase_outputs", {}).get("lead_intake", "")
            _delegate_request = _extract_delegate_request(_tmp_lead_out)
            if _delegate_request is None:
                break
            _delegate_signature = _delegate_request_signature(_delegate_request)
            if _delegate_signature in _seen_lead_delegate_signatures:
                _tmp_ws.setdefault("phase_outputs", {})["lead_intake"] = _strip_selected_directives(
                    _tmp_lead_out,
                    _delegate_directive_names,
                )
                orch._save_workflow_state(task_root)
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "delegate",
                        "source_phase": "lead_intake",
                        "reason": "repeated_delegate_request",
                        "intent": _delegate_signature[0],
                    },
                )
                break
            _seen_lead_delegate_signatures.add(_delegate_signature)
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
            if not _delegate_batch_has_successful_results(_lead_delegate_result):
                _delegate_entry_states = {
                    str(entry.get("state", "") or "").strip().lower()
                    for entry in list(_lead_delegate_result.get("entries", []) or [])
                    if str(entry.get("state", "") or "").strip()
                }
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "delegate",
                        "source_phase": "lead_intake",
                        "reason": "delegate_batch_without_successful_results",
                        "states": sorted(_delegate_entry_states),
                    },
                )
                break

        # ── PASO 2: parsear WORKFLOW_PLAN del lead → fases dinamicas ────────
        _ws = orch._get_workflow_state(task_root)
        _lead_output = _ws.get("phase_outputs", {}).get("lead_intake", "")
        _scout_state_row = orch.taskboard.get_task(scout_state_id)
        _scout_history_row = orch.taskboard.get_task(scout_history_id)
        _scout_curator_row = orch.taskboard.get_task(scout_curator_id)
        _lead_task_row = orch.taskboard.get_task(lead_task_id)
        _preplan_phase_states = {
            "lead_intake": str(_lead_task_row.state.value if _lead_task_row is not None else "missing"),
        }
        if not direct_profile_mode:
            _preplan_phase_states = {
                "scout_project_state": str(_scout_state_row.state.value if _scout_state_row is not None else "missing"),
                "scout_session_history": str(_scout_history_row.state.value if _scout_history_row is not None else "missing"),
                "scout_context_curator": str(_scout_curator_row.state.value if _scout_curator_row is not None else "missing"),
                **_preplan_phase_states,
            }
        (
            _preplanning_support_detected,
            _preplanning_support_failed_phases,
            _preplanning_support_reason_codes,
        ) = _detect_preplanning_support_failure(
            phase_states=_preplan_phase_states,
            task_rows_by_phase={
                phase_name: task_row
                for phase_name, task_row in (
                    {
                        "scout_project_state": _scout_state_row,
                        "scout_session_history": _scout_history_row,
                        "scout_context_curator": _scout_curator_row,
                        "lead_intake": _lead_task_row,
                    }
                    if not direct_profile_mode
                    else {"lead_intake": _lead_task_row}
                ).items()
                if task_row is not None
            },
        )

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
                "created_at": local_now_iso(),
            }
            _pending_file.write_text(
                json.dumps(_pending_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _ws["run_status"] = "waiting_user"
            orch._save_workflow_state(task_root)
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
                chat_mode=response_mode,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                waiting_user=True,
                clarification_question=_clarify_question,
                is_sim_mode=sim_mode_enabled(),
            )
        _lead_task_row = orch.taskboard.get_task(lead_task_id)
        _lead_state = str(_lead_task_row.state.value if _lead_task_row is not None else "").strip().lower()
        if _preplanning_support_detected and _lead_state in {"blocked", "failed"}:
            _preplanning_verdict = _build_preplanning_run_verdict(
                lead_state=_lead_state,
                preplanning_support_failure_detected=_preplanning_support_detected,
                preplanning_support_failed_phases=_preplanning_support_failed_phases,
                preplanning_support_reason_codes=_preplanning_support_reason_codes,
            )
            _ws["run_status"] = str(_preplanning_verdict.get("state", "failed") or "failed")
            _ws["run_verdict"] = dict(_preplanning_verdict)
            orch._save_workflow_state(task_root)
            orch.event_logger.emit(
                "chat_preplanning_terminal_state",
                {
                    "task_id": task_root,
                    "lead_state": _lead_state,
                    "failure_origin": _preplanning_verdict.get("failure_origin", ""),
                    "failed_phases": list(_preplanning_verdict.get("failed_phases", []) or []),
                },
            )
            orch.event_logger.emit(
                "chat_run_verdict_persisted",
                {
                    "task_id": task_root,
                    **_preplanning_verdict,
                },
            )
            _preplanning_policy = _coerce_lead_close_policy(
                derive_lead_close_policy(
                    phase_verdicts=_ws.get("phase_verdicts", {}),
                    phase_states=_preplan_phase_states,
                    run_verdict=_preplanning_verdict,
                )
            )
            return TeamChatResponse(
                task_id=task_root,
                role=Role.TEAM_LEAD.value,
                state=str(_preplanning_verdict.get("state", "failed") or "failed"),
                response=(
                    _lead_output
                    or "La corrida terminó antes de materializar workflow: lead_intake no completó."
                ),
                decision_justification=(
                    "La corrida terminó en pre-planning: lead_intake no completó, "
                    "así que no se materializaron fases dinámicas."
                ),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids=(
                    {"lead_intake": lead_task_id}
                    if direct_profile_mode
                    else {
                        "scout_project_state": scout_state_id,
                        "scout_session_history": scout_history_id,
                        "scout_context_curator": scout_curator_id,
                        "lead_intake": lead_task_id,
                    }
                ),
                chat_mode=response_mode,
                round_budget=round_budget,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                run_verdict=dict(_preplanning_verdict),
                lead_close_policy=_preplanning_policy,
                phase_states=dict(_preplan_phase_states),
                failed_tasks=len(list(_preplanning_verdict.get("failed_phases", []) or [])),
                next_action_hint=str(_preplanning_verdict.get("next_action_hint", "") or ""),
                probe_mode=probe_mode,
                lead_run_mode="preplanning",
                is_sim_mode=sim_mode_enabled(),
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
            forbid_direct_answer=bool(
                continuation_effective and _continuation_close_pending_mandate
            ),
        )
        _lcp = _lcp_resolution.directives
        _lead_output_clean = _lcp_resolution.cleaned_output
        _lead_run_mode = str(_lcp.get("run_mode", "") or "").strip() or "standard"
        if plan_only_mode and _lead_run_mode == "standard":
            _lead_run_mode = "planning_only"
        if _lead_run_mode == "standard":
            _inferred_run_mode = _infer_lead_run_mode_from_message(payload.message)
            if _inferred_run_mode:
                _lead_run_mode = _inferred_run_mode
        _quorum_result: QuorumResult | None = None
        _auto_quorum = os.getenv("AITEAM_AUTO_QUORUM", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        _quorum_requested = bool(payload.quorum) or _profile_forces_quorum
        _apply_planning_quorum = should_apply_planning_quorum(
            requested=_quorum_requested,
            run_mode=_lead_run_mode,
        )
        # lead_quorum activa el quorum aunque sea direct_profile_mode
        if direct_profile_mode and not _profile_forces_quorum:
            _apply_planning_quorum = False
        if not _quorum_requested and not _auto_quorum:
            _apply_planning_quorum = False
        if (
            not _quorum_requested
            and probe_mode
        ):
            # Probe mode should stay cheap and predictable unless quorum was explicitly requested.
            _apply_planning_quorum = False
        if _apply_planning_quorum and _lcp_resolution.early_exit is None:
            _lead_task = orch.taskboard.get_task(lead_task_id)
            _lead_metadata = dict(_lead_task.metadata if _lead_task is not None else {})
            _quorum_result = run_planning_quorum(
                router=orch.router,
                task_root=task_root,
                message=payload.message,
                base_prompt=lead_intake_description,
                lead_output=_lead_output_clean,
                lead_adapter=str(_lead_metadata.get("last_adapter_name", "") or "").strip(),
                lead_provider=str(_lead_metadata.get("last_provider", "") or "").strip(),
                lead_model=str(_lead_metadata.get("last_model", "") or "").strip(),
                complexity=complexity,
                criticality=criticality,
                environment="dev",
            )
            _ws["lead_quorum"] = _quorum_result.to_metadata()
            if _quorum_result.applied:
                _lead_output = _quorum_result.final_plan
                _ws.setdefault("phase_outputs", {})["lead_intake"] = _lead_output
                _sync_phase_verdict_in_workflow_state(
                    _ws,
                    phase_id="lead_intake",
                    output=_lead_output,
                )
                _lcp_resolution = _lead_control_resolve_lead_intake(
                    lead_output=_lead_output,
                    chat_mode=chat_mode,
                    complexity=complexity,
                    criticality=criticality,
                    round_budget=round_budget,
                    forbid_direct_answer=bool(
                        continuation_effective and _continuation_close_pending_mandate
                    ),
                )
                _lcp = _lcp_resolution.directives
                _lead_output_clean = _lcp_resolution.cleaned_output
                _lead_run_mode = str(_lcp.get("run_mode", "") or "").strip() or "standard"
                if plan_only_mode and _lead_run_mode == "standard":
                    _lead_run_mode = "planning_only"
                if _lead_run_mode == "standard":
                    _inferred_run_mode = _infer_lead_run_mode_from_message(payload.message)
                    if _inferred_run_mode:
                        _lead_run_mode = _inferred_run_mode
                orch.taskboard.update_metadata(
                    lead_task_id,
                    {
                        "quorum_requested": True,
                        "quorum_applied": True,
                        "quorum_summary": _quorum_result.to_metadata(),
                        "result": _lead_output_clean,
                    },
                )
                orch.event_logger.emit(
                    "chat_quorum_applied",
                    {
                        "task_id": task_root,
                        "lead_adapter": _quorum_result.lead_adapter,
                        "consultant_count": len(_quorum_result.consultant_plans),
                        "final_adapter": _quorum_result.final_adapter,
                    },
                )
            else:
                orch.taskboard.update_metadata(
                    lead_task_id,
                    {
                        "quorum_requested": True,
                        "quorum_applied": False,
                        "quorum_summary": _quorum_result.to_metadata(),
                    },
                )
                orch.event_logger.emit(
                    "chat_quorum_skipped",
                    {
                        "task_id": task_root,
                        "reason": _quorum_result.skipped_reason or "not_applied",
                    },
                )
            orch._save_workflow_state()
        elif payload.quorum:
            orch.event_logger.emit(
                "chat_quorum_skipped",
                {
                    "task_id": task_root,
                    "reason": (
                        "lead_early_exit"
                        if _lcp_resolution.early_exit is not None
                        else f"run_mode_{_lead_run_mode}_not_supported"
                    ),
                },
            )

        if continuation_effective and _close_pending_requested:
            _pending_details = _collect_continuation_target_pending_details(
                runtime_dir,
                continuation_of,
            )
            if _close_pending_plan_requires_repair(_lcp_resolution.phases, _pending_details):
                _repaired_phases = _phase_specs_from_pending_details(_pending_details)
                if _repaired_phases:
                    _lcp_resolution = type(_lcp_resolution)(
                        cleaned_output=_lcp_resolution.cleaned_output,
                        directives=dict(_lcp_resolution.directives),
                        phases=_repaired_phases,
                        complexity=_lcp_resolution.complexity,
                        criticality=_lcp_resolution.criticality,
                        round_budget=_lcp_resolution.round_budget,
                        early_exit=_lcp_resolution.early_exit,
                        events=[
                            *_lcp_resolution.events,
                            LeadDirectiveEvent(
                                directive="close_pending_plan_repaired",
                                payload=[spec.phase_id for spec in _repaired_phases],
                            ),
                        ],
                        plan_source="close_pending_plan_repaired",
                    )
                    _lcp = _lcp_resolution.directives
                    orch.event_logger.emit(
                        "chat_close_pending_plan_repaired",
                        {
                            "task_id": task_root,
                            "continuation_of": continuation_of,
                            "phase_ids": [spec.phase_id for spec in _repaired_phases],
                        },
                    )
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
                chat_mode=response_mode,
                round_budget=round_budget,
                state=kwargs.get("state", "completed"),
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                lead_run_mode=_lead_run_mode,
                is_sim_mode=sim_mode_enabled(),
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

        _normalized_phases = _normalize_advisory_context_phase_specs(
            _lcp_resolution.phases
        )
        _plan_source = str(getattr(_lcp_resolution, "plan_source", "") or "").strip()
        _normalized_phases = _bind_default_phases_to_lead_objective(
            _normalized_phases,
            plan_source=_plan_source,
            lead_output=_lead_output_clean,
            user_message=payload.message,
        )
        if direct_profile_mode:
            _normalized_phases = _direct_profile_phase_specs(
                _normalized_phases,
                user_message=payload.message,
            )
            _plan_source = "solo_lead_profile"
        if direct_profile_mode:
            _phase_evidence_plan, _evidence_plan_source = {}, "solo_lead_profile"
        else:
            _phase_evidence_plan, _evidence_plan_source = _resolve_phase_evidence_plan(
                lead_output=_lead_output,
                phases=_normalized_phases,
                message=payload.message,
                run_mode=_lead_run_mode,
                close_pending_mode=_close_pending_requested,
            )
        _planned_phases = [
            {
                "phase_id": spec.phase_id,
                "role": spec.role,
                "objective": spec.objective,
                "depends_on": list(spec.depends_on or []),
            }
            for spec in _normalized_phases
        ]
        _persisted_plan_path = (
            None
            if plan_only_mode
            else _persist_planning_markdown(
                workspace=workspace,
                task_root=task_root,
                run_mode=_lead_run_mode,
                message=payload.message,
                lead_output=_lead_output_clean,
                planned_phases=_planned_phases,
                quorum_result=_quorum_result,
            )
        )
        if _persisted_plan_path is not None:
            orch.event_logger.emit(
                "chat_plan_persisted",
                {
                    "task_id": task_root,
                    "path": str(_persisted_plan_path),
                    "run_mode": _lead_run_mode,
                    "phase_count": len(_planned_phases),
                },
            )
        if probe_mode or plan_only_mode:
            artifact_after = _workspace_artifact_snapshot(workspace)
            created_artifacts, modified_artifacts = _workspace_artifact_diff(
                artifact_before, artifact_after
            )
            artifact_created = 0 if plan_only_mode else len(created_artifacts)
            artifact_modified = 0 if plan_only_mode else len(modified_artifacts)
            artifact_files = [] if plan_only_mode else sorted(set(created_artifacts + modified_artifacts))
            _lead_only_ws = orch._get_workflow_state(task_root)
            _lead_only_ws["run_status"] = "completed"
            _lead_only_ws["run_verdict"] = {
                "state": "completed",
                "result": "planificado" if plan_only_mode else "exitoso",
                "failure_origin": "none",
                "reason_codes": [],
                "policy_signals": [],
                "policy_review_required": False,
                "semantic_gate_applied": False,
                "semantic_gate_failures": [],
                "evidence_gate_applied": False,
                "evidence_gate_failures": [],
                "failed_phases": [],
                "pending_phases": [],
                "advisory_mode": False,
                "degraded_delivery": False,
                "next_action_hint": (
                    "El plan esta listo; lanza Sprint cuando quieras ejecutar."
                    if plan_only_mode
                    else "Probe completado; revisa el plan antes de ejecutar."
                ),
                "updated_at": local_now_iso(),
            }
            orch._save_workflow_state()
            orch.event_logger.emit(
                "chat_plan_mode_completed" if plan_only_mode else "chat_probe_completed",
                {
                    "task_id": task_root,
                    "chat_mode": response_mode,
                    "lead_run_mode": _lead_run_mode,
                    "planned_phases": [item["phase_id"] for item in _planned_phases],
                    "round_budget": _lcp_resolution.round_budget,
                    "artifact_created": artifact_created,
                    "artifact_modified": artifact_modified,
                    "artifact_file_count": len(artifact_files),
                    "artifact_files_truncated": len(artifact_files) > 16,
                    "artifact_files": artifact_files[:16],
                },
            )
            return TeamChatResponse(
                task_id=task_root,
                role=preferred_role.value,
                state="completed",
                response=_lead_output_clean
                or (
                    "Plan completado. El Lead devolvio una planificacion sin ejecutar fases."
                    if plan_only_mode
                    else "Probe completado. El Lead devolvio un plan sin ejecutar fases."
                ),
                decision_justification=(
                    "Modo plan: se ejecuto solo lead_intake; no se crean entregables ni fases dinamicas."
                    if plan_only_mode
                    else "Modo probe: se ejecuto solo lead_intake y se devolvio el plan sin "
                    "crear ni ejecutar fases dinamicas."
                ),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=response_mode,
                round_budget=_lcp_resolution.round_budget,
                phase_contracts=_coerce_phase_contracts(
                    orch._get_workflow_state(task_root).get("phase_contracts", {})
                ),
                phase_verdicts=coerce_phase_verdicts(
                    orch._get_workflow_state(task_root).get("phase_verdicts", {})
                ),
                phase_evidence_plan=_phase_evidence_plan,
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
                lead_run_mode=_lead_run_mode,
                planned_phases=_planned_phases,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
                artifact_files=artifact_files,
                is_sim_mode=sim_mode_enabled(),
            )
        _chat_run_state = ChatRunState(
            chat_root=task_root,
            lead_task_id=lead_task_id,
            preferred_role=preferred_role,
            chat_mode=chat_mode,
            complexity=_lcp_resolution.complexity,
            criticality=_lcp_resolution.criticality,
            round_budget=_lcp_resolution.round_budget,
            phases=_normalized_phases,
            phase_evidence_plan=_phase_evidence_plan,
        )
        if _plan_source:
            _ws = orch._get_workflow_state(task_root)
            _ws["plan_source"] = _plan_source
            orch._save_workflow_state(task_root)
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
        lead_degraded_delivery = False
        lead_degrade_scope = ""
        lead_degrade_reason = ""
        skipped_phase_ids: list[str] = []
        skipped_phase_reasons: dict[str, str] = {}
        policy_signals: list[str] = []
        phases: list[PhaseSpec] = _chat_run_state.phases
        _phase_contracts = {
            spec.phase_id: {
                "phase_id": spec.phase_id,
                "role": spec.role,
                "objective": spec.objective,
                "depends_on": list(spec.depends_on or []),
            }
            for spec in phases
            if str(spec.phase_id or "").strip()
        }
        _instruction_constraints = _project_instruction_constraints(workspace)
        _workspace_scope_hints = _workspace_allowed_module_scope_hints(workspace)
        _merged_allowed_scope = list(
            dict.fromkeys(
                [
                    str(item).strip()
                    for item in list(
                        _instruction_constraints.get("allowed_module_path_hints", []) or []
                    )
                    if str(item).strip()
                ]
                + [
                    str(item).strip()
                    for item in list(_workspace_scope_hints or [])
                    if str(item).strip()
                ]
            )
        )[:16]
        if _instruction_constraints or _merged_allowed_scope:
            for _phase_contract in _phase_contracts.values():
                if not isinstance(_phase_contract, dict):
                    continue
                _phase_contract.update(
                    {
                        "forbidden_path_hints": list(
                            _instruction_constraints.get("forbidden_path_hints", []) or []
                        ),
                        "allowed_module_path_hints": list(
                            _merged_allowed_scope
                        ),
                    }
                )
        _planned_phase_names = [
            str(spec.phase_id or "").strip().lower()
            for spec in phases
            if str(spec.phase_id or "").strip()
        ]
        _review_revalidation_flow = (
            bool(_planned_phase_names)
            and not any(_is_build_like_phase_name(name) for name in _planned_phase_names)
            and any(
                _is_review_like_phase_name(name) or _is_qa_like_phase_name(name)
                for name in _planned_phase_names
            )
        )
        _workspace_review_hints = (
            _workspace_reviewable_artifact_hints(workspace)
            if _review_revalidation_flow
            else []
        )
        def _submit_chat_plan(
            _state: ChatRunState,
        ) -> tuple[dict[str, str], list[str], list[str]]:
            local_phase_task_ids = _state.phase_task_ids
            local_workflow_phase_keys = _state.workflow_phase_keys
            local_phase_dependency_ids = [
                local_phase_task_ids[_spec.phase_id]
                for _spec in _state.phases
                if _spec.phase_id in local_phase_task_ids
            ]
            local_delegated_task_ids = [] if direct_profile_mode else _state.delegated_task_ids

            for _spec in _state.phases:
                _phase_task_exists = (
                    orch.taskboard.get_task(local_phase_task_ids[_spec.phase_id]) is not None
                )
                _role_enum = Role[_spec.role]
                _caps = _role_required_capabilities(_spec.role)
                _is_engineer = _spec.role == "ENGINEER"
                _is_direct_lead_executor = bool(
                    direct_profile_mode
                    and _role_enum == Role.TEAM_LEAD
                    and str(_spec.phase_id or "").strip().lower() == "build"
                )
                if _is_direct_lead_executor:
                    _caps = ["reasoning", "coding"]
                _is_planning_phase = str(_spec.phase_id or "").strip().lower().startswith("plan_")
                _is_advisory_context_phase = _is_advisory_context_phase_name(
                    _spec.phase_id,
                    _spec.role,
                )
                _is_advisory_planning_phase = _is_advisory_planning_phase_name(
                    _spec.phase_id,
                    _spec.role,
                ) and any(
                    str(other.role or "").strip().upper() == "ENGINEER"
                    or _is_build_like_phase_name(other.phase_id)
                    for other in _state.phases
                )
                _is_review_validation_phase = _spec.role in {"REVIEWER", "QA"} and (
                    _is_review_like_phase_name(_spec.phase_id)
                    or _is_qa_like_phase_name(_spec.phase_id)
                )
                _deps = _state.dependency_ids_for(_spec)
                _sanitized_objective = str(
                    ((_phase_contracts.get(_spec.phase_id, {}) or {}).get("objective", "") or "")
                ).strip()
                _planning_guardrail = ""
                if _is_planning_phase:
                    _planning_guardrail = (
                        "Fase de planning puro: entrega solo corte, tareas secuenciadas, "
                        "riesgos, criterios de aceptacion y definition of done. "
                        "PROHIBIDO incluir bloques de codigo, path=..., comandos de escritura "
                        "o proponer archivos/modulos nuevos.\n"
                    )
                    if _spec.phase_id == "plan_engineering":
                        _planning_guardrail += (
                            "OBLIGATORIO: incluye exactamente un bloque [PLANNING_ARTIFACT]...[/PLANNING_ARTIFACT] "
                            "con objective, al menos 2 steps y al menos 1 acceptance_criteria verificable.\n"
                        )
                    elif _spec.phase_id == "plan_risks":
                        _planning_guardrail += (
                            "OBLIGATORIO: mantente en riesgos, quality gates y pruebas minimas; "
                            "no emitas veredicto de aprobacion/rechazo del build ni implementacion concreta.\n"
                        )
                _phase_contract_block = _phase_contract_prompt_block(
                    _spec,
                    all_contracts=_phase_contracts,
                )
                _phase_verdict_block = build_phase_verdict_prompt_block(
                    phase_id=_spec.phase_id,
                    role=_spec.role,
                )
                _skip_peer_consultation = _phase_defaults_to_skip_peer_consultation(
                    _spec.phase_id,
                    _spec.role,
                    advisory_context_phase=bool(_is_advisory_context_phase),
                    advisory_planning_phase=bool(_is_advisory_planning_phase),
                )
                _skip_specialist_prefetch = _phase_defaults_to_skip_specialist_prefetch(
                    _spec.phase_id,
                    _spec.role,
                    advisory_context_phase=bool(_is_advisory_context_phase),
                    advisory_planning_phase=bool(_is_advisory_planning_phase),
                )
                if _is_direct_lead_executor:
                    _skip_peer_consultation = True
                    _skip_specialist_prefetch = True
                if not _phase_task_exists:
                    _task_metadata = {
                        **build_chat_task_policy_metadata(require_execution_plan=False),
                        "required_capabilities": _caps,
                        "require_peer_consultation": False
                        if _is_direct_lead_executor
                        else not _skip_peer_consultation,
                        "skip_peer_consultation": True
                        if _is_direct_lead_executor
                        else _skip_peer_consultation,
                        "skip_specialist_prefetch": _skip_specialist_prefetch,
                        "phase": _spec.phase_id,
                        "chat_parent": task_root,
                        "run_mode": _lead_run_mode,
                        "lead_run_mode": _lead_run_mode,
                        "run_profile": run_profile,
                        "direct_coding_executor": _is_direct_lead_executor,
                        # For solo_lead fresh tasks there is no prior continuation slice
                        # to drift from — disable the drift detector to avoid false positives
                        # (e.g. objective mentions "ejecuta pytest" → detector infers test paths).
                        "skip_continuation_drift": bool(_is_direct_lead_executor) and not bool(continuation_effective),
                        "skip_quality_gates": bool(_is_direct_lead_executor),
                        "tool_specialist_economic_routing": bool(_is_direct_lead_executor),
                        "tool_specialist_default_tier": "advanced_api" if _is_direct_lead_executor else "",
                        "preferred_adapters": ["openai_codex_mini"] if _is_direct_lead_executor else [],
                        "delegated_by": "" if _is_direct_lead_executor else "team_lead",
                        "delegation_brief": _sanitized_objective,
                        "delegation_from_role": "" if _is_direct_lead_executor else "team_lead",
                        "continuation_requested": continuation_requested,
                        "continuation_effective": continuation_effective,
                        "continuation_of": continuation_of,
                        "continuation_snapshot": continuation_snapshot,
                        "phase_contract": dict(
                            _phase_contracts.get(_spec.phase_id, {}) or {}
                        ),
                        "phase_contract_enforced": True,
                        "advisory_context_phase": bool(_is_advisory_context_phase),
                        "advisory_planning_phase": bool(_is_advisory_planning_phase),
                    }
                    _workspace_hint_block = ""
                    if _is_advisory_context_phase:
                        _workspace_hint_block += (
                            "\nFase advisory de contexto: orienta al equipo con hechos del workspace actual, "
                            "pero no bloquea por si sola la implementacion si el Engineer puede inspeccionar "
                            "el repo directamente.\n"
                        )
                    if _is_advisory_planning_phase:
                        _workspace_hint_block += (
                            "\nFase advisory de planning: resume restricciones, riesgos y supuestos para ahorrar "
                            "contexto al Lead y al Engineer, pero no decide por si sola el slice ni debe bloquear "
                            "la implementacion incremental si el plan del Lead y los facts del workspace ya son suficientes.\n"
                        )
                    if _review_revalidation_flow and _is_review_validation_phase and _workspace_review_hints:
                        _task_metadata["workspace_artifact_hints"] = list(_workspace_review_hints)
                        _task_metadata["review_revalidation_flow"] = True
                        _workspace_hint_block = (
                            "\nArtefactos visibles del workspace para esta revision/validacion: "
                            + ", ".join(_workspace_review_hints[:6])
                            + ". Si no hubo build en esta misma run, usa estos archivos como base autoritativa.\n"
                        )
                    _phase_task = WorkTask(
                        task_id=local_phase_task_ids[_spec.phase_id],
                        title=_spec.phase_id.replace("_", " ").title(),
                        description=(
                            _planning_guardrail
                            + (
                                f"{_sanitized_objective}\n"
                                if _sanitized_objective
                                else ""
                            )
                            + _workspace_hint_block
                            + (
                                "Entrega: implementacion directa con bloques completos `path=...`, "
                                "sin delegar a otros roles; incluye validacion real o el check minimo recomendado."
                                if _is_direct_lead_executor
                                else "Entrega: resultado accionable con evidencia para la siguiente fase."
                            )
                            + f"\n\n{_phase_contract_block}"
                            + f"{_phase_verdict_block}"
                            + f"{continuity_block}"
                        ),
                        role=_role_enum,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        dependencies=_deps,
                        metadata=_task_metadata,
                    )
                    _derived_execution_plan = (
                        orch._derive_execution_plan_from_task(_phase_task)
                        if (_is_engineer and not _is_planning_phase)
                        else []
                    )
                    if _derived_execution_plan:
                        _phase_task.metadata["execution_plan"] = [
                            dict(step) for step in _derived_execution_plan
                        ]
                        _phase_task.metadata["execution_plan_source"] = "derived_from_contract"
                    _phase_task.metadata["require_execution_plan"] = _should_require_execution_plan_for_chat_phase(
                        phase_id=_spec.phase_id,
                        role=_spec.role,
                        lead_run_mode=_lead_run_mode,
                        require_build_execution_plan=require_build_execution_plan,
                        derived_execution_plan=_derived_execution_plan,
                    )
                    orch.submit_task(
                        _phase_task
                    )
                _evidence_specs = (
                    []
                    if direct_profile_mode
                    else _structured_evidence_specs_for_phase(
                        _spec.phase_id,
                        _state.phase_evidence_plan,
                        workspace=workspace,
                    )
                )
                _phase_deferred_specs: list[dict] = []
                for _evidence_spec in _evidence_specs:
                    _evidence_task_id = f"{task_root}::{_evidence_spec['phase_id']}"
                    _evidence_position = str(
                        _evidence_spec.get("evidence_position", "post_phase") or "post_phase"
                    ).strip().lower()
                    _parent_phase_task_id = local_phase_task_ids[_spec.phase_id]
                    if orch.taskboard.get_task(_evidence_task_id) is not None:
                        local_delegated_task_ids.append(_evidence_task_id)
                        if _evidence_position == "pre_phase":
                            _parent_task = orch.taskboard.get_task(_parent_phase_task_id)
                            if _parent_task is not None and _evidence_task_id not in list(
                                _parent_task.dependencies or []
                            ):
                                _parent_task.dependencies = list(
                                    dict.fromkeys(
                                        list(_parent_task.dependencies or []) + [_evidence_task_id]
                                    )
                                )
                                orch.taskboard.persist_tasks([_parent_phase_task_id])
                        continue
                    # C1: delegate evidence tasks are created lazily when the parent
                    # phase starts (CLAIMED) for post-phase checks. Review/QA
                    # delegates are pre-phase evidence: they must complete before the
                    # gate role judges, so we create them eagerly and add them as deps.
                    _specialist_meta = build_tool_specialist_metadata(
                        specialist=str(_evidence_spec["specialist"]),
                        required_capabilities=_evidence_spec["required_capabilities"],
                        reason=(
                            f"evidence_plan para la fase {_spec.phase_id}; "
                            f"intent={_evidence_spec['intent']}"
                        ),
                        skill_targets=_evidence_spec["skill_targets"],
                        lsp_targets=_evidence_spec["lsp_targets"],
                    )
                    _deferred_spec = {
                        "task_id": _evidence_task_id,
                        "title": f"Evidencia {str(_evidence_spec['source_phase']).replace('_', ' ')}",
                        "description": (
                            f"{_evidence_spec['instruction']}\n\n"
                            f"Fase origen: {_spec.phase_id}\n"
                            f"Objetivo de la fase: {_sanitized_objective or '[CONTRATO INVALIDO: objective ausente]'}\n"
                            f"{_phase_contract_block}\n\n"
                            f"{_evidence_spec['report_contract']}"
                            f"{continuity_block}"
                        ),
                        "role": (
                            _evidence_spec["role"].value
                            if hasattr(_evidence_spec["role"], "value")
                            else str(_evidence_spec["role"])
                        ),
                        "criticality": _resolved_criticality.value,
                        "metadata": {
                            **build_chat_task_policy_metadata(),
                            "required_capabilities": _evidence_spec["required_capabilities"],
                            "skip_quality_gates": True,
                            "skip_evidence_gate": True,
                            "phase": _evidence_spec["phase_id"],
                            "chat_parent": task_root,
                            "phase_contract_enforced": True,
                            "run_mode": _lead_run_mode,
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
                            "evidence_position": _evidence_position,
                            "skill_targets": _evidence_spec["skill_targets"],
                            "lsp_targets": _evidence_spec["lsp_targets"],
                            "phase_contract": _build_delegate_phase_contract(
                                phase_id=_evidence_spec["phase_id"],
                                source_phase=_spec.phase_id,
                                delegate_query=(
                                    f"Evidencia estructurada para {_spec.phase_id}: "
                                    f"{_evidence_spec['intent']}"
                                ),
                                assignment={
                                    "instruction": _evidence_spec["instruction"],
                                    "role": _evidence_spec["role"],
                                },
                            ),
                            **_specialist_meta,
                        },
                    }
                    if _evidence_position == "pre_phase":
                        _pre_phase_dependencies = [
                            str(dep).strip()
                            for dep in list(_deps or [])
                            if str(dep).strip()
                            and str(dep).strip() not in {_parent_phase_task_id, _evidence_task_id}
                        ]
                        if not _pre_phase_dependencies:
                            _pre_phase_dependencies = [lead_task_id]
                        _pre_phase_dependency_names: list[str] = []
                        for _pre_dep_id in _pre_phase_dependencies:
                            if _pre_dep_id == lead_task_id:
                                _pre_phase_dependency_names.append("lead_intake")
                                continue
                            if str(_pre_dep_id).startswith(f"{task_root}::"):
                                _pre_phase_dependency_names.append(
                                    str(_pre_dep_id).split("::", 1)[-1]
                                )
                            else:
                                _pre_phase_dependency_names.append(str(_pre_dep_id))
                        _pre_phase_contract = dict(
                            (_deferred_spec.get("metadata", {}) or {}).get("phase_contract", {})
                            or {}
                        )
                        _pre_phase_contract["depends_on"] = list(
                            dict.fromkeys(
                                phase
                                for phase in _pre_phase_dependency_names
                                if str(phase).strip()
                            )
                        )
                        _pre_phase_contract["contract_kind"] = "delegate_support_pre_phase"
                        _pre_phase_contract["evidence_target_phase"] = _spec.phase_id
                        _deferred_spec["metadata"]["phase_contract"] = _pre_phase_contract
                        try:
                            _evidence_role = _evidence_spec["role"]
                            _evidence_role_enum = (
                                _evidence_role
                                if isinstance(_evidence_role, Role)
                                else Role(str(_evidence_role).strip().lower())
                            )
                        except Exception:
                            _evidence_role_enum = Role.SCOUT
                        _evidence_task = WorkTask(
                            task_id=_evidence_task_id,
                            title=str(_deferred_spec["title"]),
                            description=str(_deferred_spec["description"]),
                            role=_evidence_role_enum,
                            complexity=Complexity.LOW,
                            criticality=_resolved_criticality,
                            dependencies=_pre_phase_dependencies,
                            metadata=dict(_deferred_spec["metadata"]),
                        )
                        orch.submit_task(_evidence_task)
                        _parent_task = orch.taskboard.get_task(_parent_phase_task_id)
                        if _parent_task is not None and _evidence_task_id not in list(
                            _parent_task.dependencies or []
                        ):
                            _parent_task.dependencies = list(
                                dict.fromkeys(
                                    list(_parent_task.dependencies or []) + [_evidence_task_id]
                                )
                            )
                            orch.taskboard.persist_tasks([_parent_phase_task_id])
                    else:
                        _deferred_spec["dependencies"] = [_parent_phase_task_id]
                        _phase_deferred_specs.append(_deferred_spec)
                    local_delegated_task_ids.append(_evidence_task_id)
                # Attach deferred specs to the parent phase task metadata so the
                # orchestrator can spawn them lazily when the phase is claimed.
                if _phase_deferred_specs:
                    _phase_task = orch.taskboard.get_task(local_phase_task_ids[_spec.phase_id])
                    if _phase_task is not None and not _phase_task.metadata.get("delegates_spawned"):
                        _existing = list(
                            _phase_task.metadata.get("deferred_evidence_specs", []) or []
                        )
                        _merged = {s["task_id"]: s for s in _existing}
                        _merged.update({s["task_id"]: s for s in _phase_deferred_specs})
                        orch.taskboard.update_metadata(
                            local_phase_task_ids[_spec.phase_id],
                            {"deferred_evidence_specs": list(_merged.values())},
                        )
            local_delegated_task_ids = list(dict.fromkeys(local_delegated_task_ids))

            _close_deps = (
                local_phase_dependency_ids
                if direct_profile_mode and local_phase_dependency_ids
                else (local_delegated_task_ids if local_delegated_task_ids else [lead_task_id])
            )
            if orch.taskboard.get_task(_state.lead_close_task_id) is None:
                orch.submit_task(
                    WorkTask(
                        task_id=_state.lead_close_task_id,
                        title="Lead synthesis and response",
                        description=(
                            (
                                "Cierre directo solo_lead.\n"
                                f"Solicitud original: {payload.message}\n"
                                "Entrega en <= 4 lineas:\n"
                                "1. Que archivos modificaste y por que.\n"
                                "2. Resultado de pytest (OK o primer fallo con nombre de test).\n"
                                "3. Siguiente paso concreto o 'ninguno'.\n"
                                "Sin secciones, sin riesgos, sin definition of done."
                            )
                            if direct_profile_mode
                            else (
                                "Como Team Lead senior, sintetiza el trabajo del equipo y responde al usuario.\n"
                                f"Solicitud original: {payload.message}\n"
                                "Entrega: resumen ejecutivo, decisiones tomadas y proximos pasos.\n"
                                "Causa raiz: usa primero run_verdict, phase_verdicts y phase_states autoritativos."
                                " Si hubo un fallo semantico, contractual, de planning o grounding, no lo sustituyas"
                                " por una narrativa de routing/429/capacidad salvo que no exista otro bloqueo autoritativo."
                                f"{continuity_block}"
                            )
                        ),
                        role=Role.TEAM_LEAD,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        dependencies=_close_deps,
                        metadata={
                            **build_chat_task_policy_metadata(),
                            "required_capabilities": ["reasoning"],
                            "require_peer_consultation": not direct_profile_mode,
                            "skip_peer_consultation": bool(direct_profile_mode),
                            "skip_specialist_prefetch": bool(direct_profile_mode),
                            "tool_specialist_economic_routing": bool(direct_profile_mode),
                            "tool_specialist_default_tier": "advanced_api" if direct_profile_mode else "",
                            "preferred_adapters": ["openai_codex_mini"] if direct_profile_mode else [],
                            "phase": "lead_close",
                            "chat_parent": task_root,
                            "run_mode": _lead_run_mode,
                            "lead_run_mode": _lead_run_mode,
                            "run_profile": run_profile,
                            "phase_contract": {
                                "phase_id": "lead_close",
                                "role": "TEAM_LEAD",
                                "objective": (
                                    "Sintetiza la run directa solo_lead contra el objetivo del usuario "
                                    "y los checks reales ejecutados; no exijas review/QA/delegates en "
                                    "este perfil ni emitas directivas [DELEGATE_*]."
                                    if direct_profile_mode
                                    else "Sintetiza el cierre de la run multi-rol."
                                ),
                                "depends_on": [
                                    key
                                    for key, value in local_phase_task_ids.items()
                                    if value in set(_close_deps) and key != "lead_close"
                                ],
                            },
                            "phase_contracts": dict(_phase_contracts),
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
        _auto_pre_validation_result = _run_auto_pre_phase_validation(
            runtime_dir=runtime_dir,
            workspace=workspace,
            task_root=task_root,
            phases=phases,
            phase_task_ids=phase_task_ids,
            event_logger=orch.event_logger,
            run_profile=run_profile,
        )
        if _auto_pre_validation_result:
            _auto_pre_validation_block = _format_auto_validation_context(
                _auto_pre_validation_result,
                direct_profile=direct_profile_mode,
                repair_first_mode=_repair_first_mode,
            )
            _updated_validation_task_ids: list[str] = []
            for _spec in phases:
                _is_direct_precheck_target = bool(
                    direct_profile_mode
                    and str(_spec.phase_id or "").strip().lower() == "build"
                )
                if not _is_direct_precheck_target and not _phase_requests_validation_execution(_spec):
                    continue
                _phase_task_id = phase_task_ids.get(_spec.phase_id, "")
                _phase_task = orch.taskboard.get_task(_phase_task_id)
                if _phase_task is None:
                    continue
                _repair_task_rewritten = False
                if (
                    _is_direct_precheck_target
                    and _repair_first_mode
                    and not bool(_auto_pre_validation_result.get("success", False))
                ):
                    _repair_task_rewritten = _apply_direct_repair_first_to_phase_task(
                        _phase_task,
                        _auto_pre_validation_result,
                    )
                if _auto_pre_validation_block and "[AUTO_VALIDATION_RESULT]" not in _phase_task.description:
                    _phase_task.description += _auto_pre_validation_block
                _phase_task.metadata["auto_pre_validation_result"] = {
                    key: value
                    for key, value in dict(_auto_pre_validation_result).items()
                    if key not in {"stdout", "stderr"}
                }
                _phase_task.metadata["auto_pre_validation_attached"] = True
                if _repair_task_rewritten:
                    orch.event_logger.emit(
                        "chat_repair_first_build_task_rewritten",
                        {
                            "task_id": task_root,
                            "phase_task_id": _phase_task.task_id,
                            "command": str(
                                _auto_pre_validation_result.get("command", "") or ""
                            ).strip(),
                            "exit_code": int(
                                _auto_pre_validation_result.get("exit_code", 0) or 0
                            ),
                        },
                    )
                _updated_validation_task_ids.append(_phase_task.task_id)
            if _updated_validation_task_ids:
                orch.taskboard.persist_tasks(_updated_validation_task_ids)

        workflow_label = " -> ".join(workflow_phase_keys)
        orch.event_logger.emit(
            "chat_plan_created",
            {
                "task_id": task_root,
                "chat_mode": chat_mode,
                "run_profile": run_profile,
                "round_budget": round_budget,
                "phase_count": len(workflow_phase_keys),
                "delegated_count": len(delegated_task_ids),
                "dynamic_phases": [s.phase_id for s in phases],
                "lead_run_mode": _lead_run_mode,
                "plan_source": _plan_source,
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
        # RC-H: archive zombie tasks from ALL previous runs before executing
        # this run. Prevents tasks from old chat_roots being picked up by
        # run_until_idle() (which calls ready_tasks() with no chat_root filter).
        _zombie_ids = orch.taskboard.archive_incomplete_tasks(
            reason=f"zombie_archived_at_run_start::{task_root}",
            exclude_chat_root=task_root,
        )
        if _zombie_ids:
            orch.event_logger.emit("zombie_tasks_archived", {
                "task_id": task_root,
                "archived_count": len(_zombie_ids),
                "sample_ids": _zombie_ids[:8],
                "note": "incomplete tasks from previous runs cleared before executing new run",
            })
        orch.run_until_idle(max_rounds=round_budget)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        def _attempt_terminal_lead_close_window() -> bool:
            lead_close_id = str(phase_task_ids.get("lead_close", "") or "").strip()
            if not lead_close_id:
                return False
            lead_intake_task = orch.taskboard.get_task(phase_task_ids.get("lead_intake", ""))
            if lead_intake_task is None or lead_intake_task.state != TaskState.COMPLETED:
                return False
            lead_close_task = orch.taskboard.get_task(lead_close_id)
            if lead_close_task is None:
                return False
            lead_close_state = lead_close_task.state
            if lead_close_state not in {TaskState.BLOCKED, TaskState.PENDING}:
                return False
            blocked_reason = str(
                lead_close_task.metadata.get("blocked_reason", "") or ""
            ).strip()
            if (
                lead_close_state == TaskState.BLOCKED
                and blocked_reason not in {"dependency_failed", "specialist_quorum_not_met"}
            ):
                return False
            non_lead_active = False
            for phase_name, phase_id in phase_task_ids.items():
                if phase_name in {"lead_intake", "lead_close"}:
                    continue
                task_row = orch.taskboard.get_task(phase_id)
                if task_row is None:
                    continue
                if task_row.state in {
                    TaskState.READY,
                    TaskState.CLAIMED,
                    TaskState.WAITING_USER,
                }:
                    non_lead_active = True
                    break
            if non_lead_active:
                return False
            workflow_started = False
            for phase_name, phase_id in phase_task_ids.items():
                if phase_name in {"lead_intake", "lead_close"}:
                    continue
                if _is_supporting_control_phase(phase_name):
                    continue
                task_row = orch.taskboard.get_task(phase_id)
                if task_row is None:
                    continue
                if task_row.state not in {TaskState.PENDING, TaskState.READY}:
                    workflow_started = True
                    break
            if not workflow_started:
                return False
            preserved_dependencies = list(lead_close_task.dependencies or [])
            orch.taskboard.retry_task(
                lead_close_id,
                reason="terminal_control_window",
            )
            retried = orch.taskboard.get_task(lead_close_id)
            if retried is None:
                return False
            retried.dependencies = []
            retried.metadata["terminal_control_window"] = True
            retried.metadata["terminal_control_source_reason"] = (
                blocked_reason or "no_runnable_tasks_for_root"
            )
            retried.metadata["terminal_control_original_dependencies"] = preserved_dependencies
            orch.taskboard.persist_tasks([lead_close_id])
            orch.event_logger.emit(
                "lead_close_terminal_window_opened",
                {
                    "task_id": task_root,
                    "lead_close_task_id": lead_close_id,
                    "blocked_reason": blocked_reason,
                    "lead_close_state": lead_close_state.value,
                    "preserved_dependencies": preserved_dependencies,
                },
            )
            orch.run_until_idle(max_rounds=1)
            return True

        def _apply_auto_review_rework_if_needed() -> bool:
            nonlocal phases, phase_task_ids, workflow_phase_keys, delegated_task_ids
            nonlocal _chat_run_state, _phase_contracts, elapsed_ms

            if any(
                str(spec.phase_id or "").strip().startswith("repair_after_")
                for spec in phases
            ):
                return False
            lead_close_task = orch.taskboard.get_task(phase_task_ids.get("lead_close", ""))
            if _phase_started_for_replan(lead_close_task):
                return False

            verdicts = coerce_phase_verdicts(_ws.get("phase_verdicts", {}))
            rejected_review_phase = ""
            review_feedback = ""
            for spec in phases:
                phase_id = str(spec.phase_id or "").strip()
                if not phase_id:
                    continue
                if not (
                    str(spec.role or "").strip().upper() == "REVIEWER"
                    or _is_review_like_phase_name(phase_id)
                ):
                    continue
                task_id = phase_task_ids.get(phase_id, "")
                task_row = orch.taskboard.get_task(task_id) if task_id else None
                if task_row is None or task_row.state != TaskState.FAILED:
                    continue
                verdict = dict(verdicts.get(phase_id, {}) or {})
                verdict_reasons = {
                    str(item).strip().lower()
                    for item in list(verdict.get("reason_codes", []) or [])
                    if str(item).strip()
                }
                task_text = str(
                    task_row.metadata.get("result")
                    or task_row.metadata.get("error")
                    or ""
                )
                task_error = str(task_row.metadata.get("error") or "").strip().lower()
                is_rejected = (
                    str(verdict.get("status", "") or "").strip().lower() == "rejected"
                    or "review_rejected" in verdict_reasons
                    or task_error == "rejected"
                    or bool(_REVIEW_REJECTED_RE.search(task_text))
                )
                if is_rejected:
                    rejected_review_phase = phase_id
                    review_feedback = task_text
                    break

            if not rejected_review_phase:
                return False

            repaired_phases = _review_rework_phase_specs(
                phases,
                rejected_review_phase=rejected_review_phase,
                review_feedback=review_feedback,
            )
            repaired_ids = {
                str(spec.phase_id or "").strip()
                for spec in repaired_phases
                if str(spec.phase_id or "").strip()
            }
            current_ids = {
                str(spec.phase_id or "").strip()
                for spec in phases
                if str(spec.phase_id or "").strip()
            }
            removed_phase_ids = sorted(current_ids - repaired_ids)
            if not removed_phase_ids:
                return False

            remove_ids: list[str] = []
            removed_set = set(removed_phase_ids) | {"lead_close"}
            for task_row in orch.taskboard.list_tasks():
                task_id = str(task_row.task_id or "")
                if not task_id.startswith(f"{task_root}::"):
                    continue
                phase_name = str(task_row.metadata.get("phase", "") or "").strip()
                evidence_source = str(
                    task_row.metadata.get("evidence_source_phase", "") or ""
                ).strip()
                if (
                    task_id == lead_task_id
                    or phase_name in {"lead_intake"}
                    or task_id == lead_task_id
                ):
                    continue
                if (
                    phase_name in removed_set
                    or evidence_source in removed_set
                    or task_id == phase_task_ids.get("lead_close", "")
                ):
                    remove_ids.append(task_id)
            if remove_ids:
                orch.taskboard.remove_tasks(list(dict.fromkeys(remove_ids)))

            keep_ids = repaired_ids | {"lead_intake"}
            keep_outputs: dict[str, str] = {}
            for phase_key, output in dict(_ws.get("phase_outputs", {}) or {}).items():
                if phase_key in keep_ids or _is_supporting_control_phase(phase_key):
                    keep_outputs[phase_key] = output
            _ws["phase_outputs"] = keep_outputs
            _prune_phase_verdicts(_ws, keep_phase_ids=keep_ids)
            _phase_context_summaries = dict(_ws.get("phase_context_summaries", {}) or {})
            for phase_name in removed_phase_ids:
                _phase_context_summaries.pop(phase_name, None)
            _ws["phase_context_summaries"] = _phase_context_summaries

            _project_summary, _chat_summary = _record_context_invalidation(
                runtime_dir=runtime_dir,
                workspace=workspace,
                task_root=task_root,
                reason="review_rework",
                affected_phases=removed_phase_ids,
                source_task_ids=[f"{task_root}::{rejected_review_phase}"],
            )
            _ws["project_context_summary"] = _project_summary
            _ws["chat_context_summary"] = _chat_summary

            _phase_contracts = {
                spec.phase_id: {
                    "phase_id": spec.phase_id,
                    "role": spec.role,
                    "objective": spec.objective,
                    "depends_on": list(spec.depends_on or []),
                }
                for spec in repaired_phases
                if str(spec.phase_id or "").strip()
            }
            if _instruction_constraints or _merged_allowed_scope:
                for _phase_contract in _phase_contracts.values():
                    _phase_contract.update(
                        {
                            "forbidden_path_hints": list(
                                _instruction_constraints.get("forbidden_path_hints", []) or []
                            ),
                            "allowed_module_path_hints": list(_merged_allowed_scope),
                        }
                    )
            _chat_run_state = ChatRunState(
                chat_root=task_root,
                lead_task_id=lead_task_id,
                preferred_role=preferred_role,
                chat_mode=chat_mode,
                complexity=_resolved_complexity,
                criticality=_resolved_criticality,
                round_budget=round_budget,
                phases=_normalize_advisory_context_phase_specs(repaired_phases),
                phase_evidence_plan=_chat_run_state.phase_evidence_plan,
            )
            phases = _chat_run_state.phases
            orch._save_workflow_state()
            phase_task_ids, workflow_phase_keys, delegated_task_ids = _submit_chat_plan(
                _chat_run_state
            )
            _sync_chat_runtime_state(
                orch,
                task_root=task_root,
                chat_run_state=_chat_run_state,
                lead_run_mode=_lead_run_mode,
                delegated_task_ids=delegated_task_ids,
                evidence_plan_source="review_rework",
            )
            orch.event_logger.emit(
                "chat_review_rework_planned",
                {
                    "task_id": task_root,
                    "rejected_review_phase": rejected_review_phase,
                    "removed_phase_ids": removed_phase_ids,
                    "phase_ids": [spec.phase_id for spec in phases],
                },
            )
            orch.run_until_idle(max_rounds=round_budget)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return True

        if not _apply_auto_review_rework_if_needed():
            _attempt_terminal_lead_close_window()

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
                phases=_normalize_advisory_context_phase_specs(phases),
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

        def _consume_midrun_delegate_cycles() -> None:
            nonlocal elapsed_ms

            def _lead_checkpoint_output_history() -> dict[str, list[str]]:
                history: dict[str, list[str]] = {}
                for _phase_name, _phase_id in phase_task_ids.items():
                    normalized_phase = str(_phase_name or "").strip()
                    if normalized_phase != "lead_close" and not normalized_phase.startswith("lead_"):
                        continue
                    _task_row = orch.taskboard.get_task(_phase_id)
                    if _task_row is None:
                        continue
                    _history_items = [
                        str(item).strip()
                        for item in list(_task_row.metadata.get("_agent_output_history", []) or [])
                        if str(item).strip()
                    ]
                    if _history_items:
                        history[normalized_phase] = _history_items
                return history

            _MAX_MIDRUN_DELEGATE_CYCLES = 2
            _seen_midrun_delegate_signatures: set[tuple[str, str, str, str]] = set()
            _consumed_delegate_signatures: set[tuple[str, str, str, str]] = {
                (
                    str(item[0]).strip(),
                    str(item[1]).strip(),
                    str(item[2]).strip(),
                    str(item[3]).strip(),
                )
                for item in list(_ws.get("consumed_delegate_request_signatures", []) or [])
                if isinstance(item, (list, tuple)) and len(item) == 4
            }
            for _mid_delegate_cycle in range(_MAX_MIDRUN_DELEGATE_CYCLES):
                _mid_delegate_request = _extract_delegate_request_from_outputs(
                    _ws.get("phase_outputs", {}),
                    phase_output_history=_lead_checkpoint_output_history(),
                )
                if _mid_delegate_request is None:
                    break
                _delegate_source_phase, _delegate_request = _mid_delegate_request
                _delegate_signature = _delegate_request_signature(
                    _delegate_request,
                    source_phase=_delegate_source_phase,
                )
                if (
                    _delegate_signature in _seen_midrun_delegate_signatures
                    or _delegate_signature in _consumed_delegate_signatures
                ):
                    orch.event_logger.emit(
                        "lcp_directive_skipped",
                        {
                            "task_id": task_root,
                            "directive": "delegate",
                            "source_phase": _delegate_source_phase,
                            "reason": (
                                "consumed_delegate_request"
                                if _delegate_signature in _consumed_delegate_signatures
                                else "repeated_delegate_request"
                            ),
                            "intent": _delegate_signature[1],
                        },
                    )
                    _source_output = str(
                        (_ws.get("phase_outputs", {}) or {}).get(_delegate_source_phase, "") or ""
                    )
                    _ws.setdefault("phase_outputs", {})[_delegate_source_phase] = _strip_selected_directives(
                        _source_output,
                        _delegate_directive_names,
                    )
                    orch._save_workflow_state()
                    break
                _seen_midrun_delegate_signatures.add(_delegate_signature)
                _consumed_delegate_signatures.add(_delegate_signature)
                _delegate_source_output = str(
                    (_ws.get("phase_outputs", {}) or {}).get(_delegate_source_phase, "") or ""
                )
                _ws.setdefault("phase_outputs", {})[_delegate_source_phase] = _strip_selected_directives(
                    _delegate_source_output,
                    _delegate_directive_names,
                )
                _ws["consumed_delegate_request_signatures"] = [
                    list(item) for item in sorted(_consumed_delegate_signatures)
                ]
                orch._save_workflow_state()
                _mid_source_task_id = f"{task_root}::{_delegate_source_phase}"
                _existing_delegate_cycles = sum(
                    1
                    for _batch in _coerce_delegate_batches(
                        _ws.get("delegate_batches", [])
                    )
                    if str(_batch.get("source_phase", "") or "").strip()
                    == _delegate_source_phase
                )
                _delegate_result = _execute_delegate_request(
                    orch=orch,
                    task_root=task_root,
                    workspace=workspace,
                    runtime_dir=runtime_dir,
                    delegate_request=_delegate_request,
                    source_task_id=_mid_source_task_id,
                    source_phase=_delegate_source_phase,
                    delegate_cycle=_existing_delegate_cycles,
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
                if not _delegate_batch_has_successful_results(_delegate_result):
                    _delegate_entry_states = {
                        str(entry.get("state", "") or "").strip().lower()
                        for entry in list(_delegate_result.get("entries", []) or [])
                        if str(entry.get("state", "") or "").strip()
                    }
                    orch.event_logger.emit(
                        "lcp_directive_skipped",
                        {
                            "task_id": task_root,
                            "directive": "delegate",
                            "source_phase": _delegate_source_phase,
                            "reason": "delegate_batch_without_successful_results",
                            "states": sorted(_delegate_entry_states),
                        },
                    )
                    break
                elapsed_ms = int((time.perf_counter() - started) * 1000)

        if not direct_profile_mode:
            _consume_midrun_delegate_cycles()

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
                _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
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
                    phases=_normalize_advisory_context_phase_specs(_pruned_phases),
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
                _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
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
                    phases=_normalize_advisory_context_phase_specs(_replan_phases),
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
                    _preserved_phase_set = set(_preserved_phase_ids)
                    _removed_phase_ids = [
                        _spec.phase_id
                        for _spec in phases
                        if _spec.phase_id not in _preserved_phase_set
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
                        elif _phase_key in set(_preserved_phase_ids):
                            _keep_outputs[_phase_key] = _output
                    _ws["phase_outputs"] = _keep_outputs
                    _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
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
                        phases=_normalize_advisory_context_phase_specs(_merged_phases),
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
                    _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
                    orch._save_workflow_state()
                    _chat_run_state = ChatRunState(
                        chat_root=task_root,
                        lead_task_id=lead_task_id,
                        preferred_role=preferred_role,
                        chat_mode=chat_mode,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        round_budget=round_budget,
                        phases=_normalize_advisory_context_phase_specs(_pruned_phases),
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
                    _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
                    orch._save_workflow_state()
                    _chat_run_state = ChatRunState(
                        chat_root=task_root,
                        lead_task_id=lead_task_id,
                        preferred_role=preferred_role,
                        chat_mode=chat_mode,
                        complexity=_resolved_complexity,
                        criticality=_resolved_criticality,
                        round_budget=round_budget,
                        phases=_normalize_advisory_context_phase_specs(_pruned_phases),
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
                _prune_phase_verdicts(_ws, keep_phase_ids=set(_keep_outputs.keys()))
                orch._save_workflow_state()
                _chat_run_state = ChatRunState(
                    chat_root=task_root,
                    lead_task_id=lead_task_id,
                    preferred_role=preferred_role,
                    chat_mode=chat_mode,
                    complexity=_resolved_complexity,
                    criticality=_resolved_criticality,
                    round_budget=round_budget,
                    phases=_normalize_advisory_context_phase_specs(phases),
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

        _pause_for_user_request = _extract_pause_for_user_request_from_outputs(
            _ws.get("phase_outputs", {})
        )
        _pause_for_user_applied = False
        if _pause_for_user_request is not None:
            _pause_source_phase, _pause_question = _pause_for_user_request
            _pause_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_pause_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_pause_source_phase] = _strip_selected_directives(
                _pause_source_output,
                ["PAUSE_FOR_USER"],
            )
            orch._save_workflow_state()
            _pause_task_id = phase_task_ids.get(_pause_source_phase, "")
            _pause_task = orch.taskboard.get_task(_pause_task_id) if _pause_task_id else None
            _pause_policy = {}
            if _pause_source_phase == "lead_close":
                _pause_phase_states = _collect_phase_progress()[1]
                _pause_policy = derive_lead_close_policy(
                    phase_verdicts=_ws.get("phase_verdicts", {}),
                    phase_states=_pause_phase_states,
                    run_verdict=_ws.get("run_verdict", {}),
                    phase_outputs=_ws.get("phase_outputs", {}),
                )
            if _should_skip_pause_for_user_due_to_authoritative_policy(
                _pause_question,
                _pause_policy,
            ):
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "pause_for_user",
                        "source_phase": _pause_source_phase,
                        "reason": "stale_routing_pause_contradicts_authoritative_policy",
                    },
                )
            elif _pause_source_phase == "lead_close" and _pause_task is not None:
                orch.taskboard.mark_waiting_user(_pause_task.task_id, question=_pause_question)
                _pause_for_user_applied = True
                _ws["run_status"] = "waiting_user"
                orch._save_workflow_state(task_root)
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "pause_for_user",
                        "source_phase": _pause_source_phase,
                        "question": _pause_question,
                    },
                )
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "pause_for_user",
                        "source_phase": _pause_source_phase,
                        "reason": "target_phase_missing_or_not_from_lead_close",
                    },
                )

        _skip_phase_request = None
        if not _pause_for_user_applied:
            _skip_phase_request = _extract_skip_phase_request_from_outputs(
                _ws.get("phase_outputs", {})
            )
        if _skip_phase_request is not None:
            _skip_phase_source, _skip_phase_payload = _skip_phase_request
            _skip_phase_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_skip_phase_source, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_skip_phase_source] = _strip_selected_directives(
                _skip_phase_source_output,
                ["SKIP_PHASE"],
            )
            orch._save_workflow_state()
            _skip_phase_target = str(_skip_phase_payload.get("phase_id", "") or "").strip()
            _skip_phase_reason = str(_skip_phase_payload.get("reason", "") or "").strip()
            _target_task_id = phase_task_ids.get(_skip_phase_target, "")
            _target_task = orch.taskboard.get_task(_target_task_id) if _target_task_id else None
            if _skip_phase_source == "lead_close" and _target_task is not None:
                orch.taskboard.skip_task(
                    _target_task.task_id,
                    _skip_phase_reason or f"Lead skipped phase {_skip_phase_target}",
                )
                skipped_phase_ids.append(_skip_phase_target)
                skipped_phase_reasons[_skip_phase_target] = _skip_phase_reason
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "skip_phase",
                        "source_phase": _skip_phase_source,
                        "target_phase": _skip_phase_target,
                        "reason": _skip_phase_reason,
                    },
                )
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "skip_phase",
                        "source_phase": _skip_phase_source,
                        "target_phase": _skip_phase_target,
                        "reason": "target_phase_missing_or_not_from_lead_close",
                    },
                )

        _degrade_request = None
        if not _pause_for_user_applied:
            _degrade_request = _extract_degrade_request_from_outputs(
                _ws.get("phase_outputs", {})
            )
        if _degrade_request is not None:
            _degrade_source_phase, _degrade_payload = _degrade_request
            _degrade_source_output = str(
                (_ws.get("phase_outputs", {}) or {}).get(_degrade_source_phase, "") or ""
            )
            _ws.setdefault("phase_outputs", {})[_degrade_source_phase] = _strip_selected_directives(
                _degrade_source_output,
                ["DEGRADE"],
            )
            orch._save_workflow_state()
            if str(_degrade_source_phase or "").strip().startswith("lead_"):
                lead_degraded_delivery = True
                lead_degrade_scope = str(_degrade_payload.get("scope", "") or "").strip().lower()
                lead_degrade_reason = str(_degrade_payload.get("reason", "") or "").strip()
                orch.event_logger.emit(
                    "lcp_directive_applied",
                    {
                        "task_id": task_root,
                        "directive": "degrade",
                        "source_phase": _degrade_source_phase,
                        "scope": lead_degrade_scope,
                        "reason": lead_degrade_reason,
                    },
                )
            else:
                orch.event_logger.emit(
                    "lcp_directive_skipped",
                    {
                        "task_id": task_root,
                        "directive": "degrade",
                        "source_phase": _degrade_source_phase,
                        "reason": "not_from_lead_checkpoint",
                    },
                )

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
                "created_at": local_now_iso(),
            }
            _mid_pending_file.write_text(
                json.dumps(_mid_state, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
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
                chat_mode=response_mode,
                phase_contracts=_coerce_phase_contracts(
                    _ws.get("phase_contracts", {})
                ),
                phase_verdicts=coerce_phase_verdicts(
                    _ws.get("phase_verdicts", {})
                ),
                phase_evidence_plan=_coerce_phase_evidence_plan(
                    _ws.get("phase_evidence_plan", _chat_run_state.phase_evidence_plan)
                ),
                delegate_batches=_coerce_delegate_batches(
                    _ws.get("delegate_batches", [])
                ),
                delegate_economics=dict(
                    _ws.get("delegate_economics_summary", {}) or {}
                ),
                **_specialist_insight_fields(runtime_dir, task_root),
                **_peer_consultation_summary_fields(runtime_dir, task_root),
                probe_mode=probe_mode,
                lead_run_mode=_lead_run_mode,
                waiting_user=True,
                clarification_question=_mwq,
                is_sim_mode=sim_mode_enabled(),
            )

        artifact_after = _workspace_artifact_snapshot(workspace)
        created_artifacts, modified_artifacts = _workspace_artifact_diff(
            artifact_before, artifact_after
        )
        artifact_created = len(created_artifacts)
        artifact_modified = len(modified_artifacts)
        artifact_files = sorted(set(created_artifacts + modified_artifacts))

        if artifact_files:
            orch.event_logger.emit(
                "chat_artifacts_detected",
                {
                    "task_id": task_root,
                    "created": artifact_created,
                    "modified": artifact_modified,
                    "file_count": len(artifact_files),
                    "files_truncated": len(artifact_files) > 16,
                    "files": artifact_files[:16],
                },
            )

        phase_task_set = set(phase_task_ids.values())
        _auto_post_build_validation_result = _run_auto_post_build_validation(
            runtime_dir=runtime_dir,
            workspace=workspace,
            task_root=task_root,
            phase_task_set=phase_task_set,
            artifact_files=artifact_files,
            event_logger=orch.event_logger,
            run_profile=run_profile,
            failed_validation_result=_auto_pre_validation_result,
        )
        _direct_post_build_repair_result = (
            dict(_auto_post_build_validation_result)
            if isinstance(_auto_post_build_validation_result, dict)
            else {}
        )
        _direct_post_build_repair_attempt_default = max(1, int(round_budget or 1))
        _direct_post_build_repair_max_attempts = max(
            1,
            min(
                _direct_post_build_repair_attempt_default,
                _safe_int_value(
                    os.getenv(
                        "AITEAM_DIRECT_POST_BUILD_REPAIR_RETRIES",
                        str(_direct_post_build_repair_attempt_default),
                    ),
                    _direct_post_build_repair_attempt_default,
                ),
            ),
        )
        while (
            direct_profile_mode
            and _repair_first_mode
            and _direct_post_build_repair_result
            and not bool(_direct_post_build_repair_result.get("success", False))
            and not bool(_direct_post_build_repair_result.get("skipped", False))
        ):
            _post_build_repair_target_id = _prepare_direct_post_build_repair_retry(
                orch=orch,
                task_root=task_root,
                phase_task_ids=phase_task_ids,
                result=_direct_post_build_repair_result,
                max_attempts=_direct_post_build_repair_max_attempts,
            )
            if _post_build_repair_target_id:
                orch.run_until_idle(max_rounds=max(1, int(round_budget or 1)))
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                artifact_after = _workspace_artifact_snapshot(workspace)
                created_artifacts, modified_artifacts = _workspace_artifact_diff(
                    artifact_before,
                    artifact_after,
                )
                artifact_created = len(created_artifacts)
                artifact_modified = len(modified_artifacts)
                artifact_files = sorted(set(created_artifacts + modified_artifacts))
                if artifact_files:
                    orch.event_logger.emit(
                        "chat_artifacts_detected",
                        {
                            "task_id": task_root,
                            "created": artifact_created,
                            "modified": artifact_modified,
                            "file_count": len(artifact_files),
                            "files_truncated": len(artifact_files) > 16,
                            "files": artifact_files[:16],
                            "reason": "post_build_repair_retry",
                        },
                    )
                _auto_post_build_validation_result = _run_auto_post_build_validation(
                    runtime_dir=runtime_dir,
                    workspace=workspace,
                    task_root=task_root,
                    phase_task_set=set(phase_task_ids.values()),
                    artifact_files=artifact_files,
                    event_logger=orch.event_logger,
                    run_profile=run_profile,
                    failed_validation_result=_direct_post_build_repair_result,
                )
                _post_build_repair_task = orch.taskboard.get_task(
                    _post_build_repair_target_id
                )
                _post_build_repair_attempt_count = int(
                    (
                        _post_build_repair_task.metadata
                        if _post_build_repair_task is not None
                        else {}
                    ).get("auto_post_build_repair_attempt_count", 0)
                    or 0
                )
                orch.event_logger.emit(
                    "chat_repair_first_post_build_retry_completed",
                    {
                        "task_id": task_root,
                        "phase_task_id": _post_build_repair_target_id,
                        "attempt": _post_build_repair_attempt_count,
                        "max_attempts": _direct_post_build_repair_max_attempts,
                        "success": bool(
                            isinstance(_auto_post_build_validation_result, dict)
                            and _auto_post_build_validation_result.get("success", False)
                        ),
                        "validation_reason": (
                            str(_auto_post_build_validation_result.get("reason", "") or "")
                            if isinstance(_auto_post_build_validation_result, dict)
                            else ""
                        ),
                    },
                )
                _direct_post_build_repair_result = (
                    dict(_auto_post_build_validation_result)
                    if isinstance(_auto_post_build_validation_result, dict)
                    else {}
                )
                continue
            break
        if (
            direct_profile_mode
            and _repair_first_mode
            and isinstance(_direct_post_build_repair_result, dict)
            and bool(_direct_post_build_repair_result.get("success", False))
            and _resume_direct_post_build_lead_close_after_success(
                orch=orch,
                task_root=task_root,
                phase_task_ids=phase_task_ids,
            )
        ):
            orch.run_until_idle(max_rounds=1)
            elapsed_ms = int((time.perf_counter() - started) * 1000)

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

        def _collect_root_task_state_counts() -> dict[str, int]:
            counts: dict[str, int] = {}
            prefix = f"{task_root}::"
            for task in orch.taskboard.list_tasks():
                task_id = str(getattr(task, "task_id", "") or "")
                if not task_id.startswith(prefix):
                    continue
                state_value = str(getattr(task.state, "value", task.state) or "").strip().lower()
                if not state_value:
                    continue
                counts[state_value] = int(counts.get(state_value, 0)) + 1
            return counts

        auto_extended_rounds = 0
        if bool(payload.auto_extend_weak_runs) and round_budget < 80:
            _phase_states_snapshot = _collect_phase_progress()[1]
            planning_failure_detected = any(
                str(phase_name).startswith("plan_") and state == "failed"
                for phase_name, state in _phase_states_snapshot.items()
            )
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

            _root_task_state_counts_for_extend = _collect_root_task_state_counts()
            should_auto_extend, auto_extend_reason = _should_auto_extend_weak_run(
                artifact_created=artifact_created,
                execution_steps_so_far=execution_steps_so_far,
                planning_failure_detected=planning_failure_detected,
                root_task_state_counts=_root_task_state_counts_for_extend,
            )
            if should_auto_extend:
                next_round_budget = min(80, round_budget + 3)
                if next_round_budget > round_budget:
                    auto_extended_rounds = next_round_budget - round_budget
                    _ready_count = int(
                        _root_task_state_counts_for_extend.get("ready", 0)
                    )
                    _claimed_count = int(
                        _root_task_state_counts_for_extend.get("claimed", 0)
                    )
                    orch.event_logger.emit(
                        "chat_auto_rounds_extended",
                        {
                            "task_id": task_root,
                            "from_round_budget": round_budget,
                            "to_round_budget": next_round_budget,
                            "reason": auto_extend_reason,
                            "taskboard_ready_count": _ready_count,
                            "taskboard_claimed_count": _claimed_count,
                        },
                    )
                    round_budget = next_round_budget
                    if (_ready_count + _claimed_count) > 0:
                        orch.run_until_idle(max_rounds=round_budget)
                        elapsed_ms = int((time.perf_counter() - started) * 1000)
                        artifact_after = _workspace_artifact_snapshot(workspace)
                        created_artifacts, modified_artifacts = _workspace_artifact_diff(
                            artifact_before, artifact_after
                        )
                        artifact_created = len(created_artifacts)
                        artifact_modified = len(modified_artifacts)
                        artifact_files = sorted(set(created_artifacts + modified_artifacts))
                        if artifact_files:
                            orch.event_logger.emit(
                                "chat_artifacts_detected",
                                {
                                    "task_id": task_root,
                                    "created": artifact_created,
                                    "modified": artifact_modified,
                                    "file_count": len(artifact_files),
                                    "files_truncated": len(artifact_files) > 16,
                                    "files": artifact_files[:16],
                                },
                            )
            elif auto_extend_reason == "planning_phase_failed":
                orch.event_logger.emit(
                    "chat_auto_extend_skipped",
                    {
                        "task_id": task_root,
                        "reason": auto_extend_reason,
                    },
                )
            elif auto_extend_reason == "no_runnable_tasks_for_root":
                orch.event_logger.emit(
                    "auto_extend_taskboard_empty",
                    {
                        "task_id": task_root,
                        "round_budget": round_budget,
                        "task_state_counts": dict(_root_task_state_counts_for_extend),
                        "note": "auto-extend skipped because this run has no runnable tasks",
                    },
                )
                orch.event_logger.emit(
                    "chat_auto_extend_skipped",
                    {
                        "task_id": task_root,
                        "reason": auto_extend_reason,
                        "task_state_counts": dict(_root_task_state_counts_for_extend),
                    },
                )
            else:
                orch.event_logger.emit(
                    "chat_auto_extend_skipped",
                    {
                        "task_id": task_root,
                        "reason": auto_extend_reason,
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
            final_state = "completed" if pending_tasks == 0 else "in_progress"
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

        _attempt_terminal_lead_close_window()
        _consume_midrun_delegate_cycles()

        task_rows_by_phase: dict[str, WorkTask] = {}
        for phase_name, phase_id in phase_task_ids.items():
            task = orch.taskboard.get_task(phase_id)
            if task is not None:
                task_rows_by_phase[phase_name] = task

        # RC-H: evidence gate expects exact keys "build", "review", "qa".
        # The Lead may use descriptive phase_ids like "implement_p0_artifacts",
        # "review_p0_artifacts", "qa_p0_val" etc.  Add canonical aliases so the
        # gate always finds an entry regardless of the Lead's naming choice.
        _CANONICAL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
            ("build",  ("build", "implement", "engineer")),
            ("review", ("review",)),
            ("qa",     ("qa", "test", "valid")),
        ]
        for _canon, _keywords in _CANONICAL_KEYWORDS:
            if _canon not in task_rows_by_phase:
                for _pn in phase_task_ids:
                    _pn_lo = _pn.lower()
                    if any(kw in _pn_lo for kw in _keywords) and _pn in task_rows_by_phase:
                        task_rows_by_phase[_canon] = task_rows_by_phase[_pn]
                        break

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

        actionable_phase_keys = [
            phase
            for phase in workflow_phase_keys
            if _is_actionable_failed_phase(phase, task_rows_by_phase)
        ]
        advisory_context_failed_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) == "failed"
            and phase not in actionable_phase_keys
        ]
        done_phases = [
            phase
            for phase in actionable_phase_keys
            if phase_states.get(phase) == "completed"
        ]
        pending_phases = [
            phase
            for phase in actionable_phase_keys
            if phase_states.get(phase) in {"pending", "ready", "claimed", "blocked", "waiting_user"}
        ]
        blocked_phases = [
            phase
            for phase in actionable_phase_keys
            if phase_states.get(phase) == "blocked"
        ]
        cascade_blocked_phases = _cascade_blocked_phases(
            blocked_phases,
            task_rows_by_phase,
        )
        root_blocked_phases = [
            phase for phase in blocked_phases if phase not in set(cascade_blocked_phases)
        ]
        failed_phases = [
            phase
            for phase in actionable_phase_keys
            if phase_states.get(phase) == "failed"
        ]
        planning_failed_phases = [
            phase for phase in failed_phases if str(phase).strip().lower().startswith("plan_")
        ]
        (
            preplanning_support_failure_detected,
            preplanning_support_failed_phases,
            preplanning_support_reason_codes,
        ) = _detect_preplanning_support_failure(
            phase_states=phase_states,
            task_rows_by_phase=task_rows_by_phase,
        )
        run_failed_phases = _resolve_run_failed_phases(
            failed_phases=failed_phases,
            preplanning_support_failure_detected=preplanning_support_failure_detected,
            preplanning_support_failed_phases=preplanning_support_failed_phases,
        )

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
        decision_display = _presentable_decision_text(decision_compact) or decision_compact or "—"

        live_mode_required = _env_bool(
            "AITEAM_LIVE_MODE_REQUIRED",
            default=False,
        ) or _env_bool("AITEAM_REQUIRE_LIVE_MODE", default=False)
        if preplanning_support_failure_detected:
            evidence_gate_failures = []
        else:
            evidence_gate_failures = _evaluate_phase_evidence_gate(
                task_rows_by_phase=task_rows_by_phase,
                execution_steps=execution_steps,
                execution_steps_success=execution_steps_success,
                successful_checks=successful_checks,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
                require_test_or_build_check=True,
                require_review_qa=not direct_profile_mode,
            )
        if planning_failed_phases:
            evidence_gate_failures = []
            live_mode_required = False
        if preplanning_support_failure_detected:
            evidence_gate_failures = []
            live_mode_required = False
        evidence_gate_failures = _filter_continuation_evidence_gate_failures(
            evidence_gate_failures,
            continuation_requested=bool(continuation_requested),
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
        )
        evidence_gate_failures = _filter_cascade_blocked_evidence_failures(
            evidence_gate_failures,
            cascade_blocked_phases=cascade_blocked_phases,
        )
        evidence_gate_failures = _merge_auto_post_validation_failure(
            evidence_gate_failures,
            _auto_post_build_validation_result,
        )

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
                "blocked": "missing_execution_plan_required",
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

        _phase_verdicts = coerce_phase_verdicts(_ws.get("phase_verdicts", {}))
        for _phase_name, _task_row in task_rows_by_phase.items():
            _phase_output = _task_result_text(_task_row) or str(
                (_ws.get("phase_outputs", {}) or {}).get(_phase_name, "") or ""
            )
            if _phase_output:
                _sync_phase_verdict_in_workflow_state(
                    _ws,
                    phase_id=_phase_name,
                    output=_phase_output,
                )
        _phase_verdicts = coerce_phase_verdicts(_ws.get("phase_verdicts", {}))

        semantic_gate_failures = _evaluate_phase_semantic_gate(
            task_rows_by_phase=task_rows_by_phase,
            phase_verdicts=_phase_verdicts,
        )
        semantic_gate_applied = bool(semantic_gate_failures)
        orch.event_logger.emit(
            "chat_semantic_gate_assessed",
            {
                "task_id": task_root,
                "semantic_gate_applied": semantic_gate_applied,
                "semantic_gate_failures": list(semantic_gate_failures),
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
                failed_tasks=len(run_failed_phases),
                execution_attempts=execution_attempts,
                execution_success=execution_success,
                execution_steps=execution_steps,
                successful_checks=successful_checks,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
            )
        )
        material_evidence_failures = {
            "build:no_successful_execution_steps",
            "build:no_successful_post_build_checks",
            "build:missing_test_or_build_check",
            "build:auto_post_build_validation_failed",
        }
        hard_evidence_failures = [
            failure
            for failure in list(evidence_gate_failures)
            if failure in material_evidence_failures
        ]
        if hard_evidence_failures:
            productivity_score = min(int(productivity_score), 49)
            productivity_status = "weak"
            orch.event_logger.emit(
                "chat_quality_hard_failure_override",
                {
                    "task_id": task_root,
                    "failures": hard_evidence_failures[:8],
                    "productivity_score": productivity_score,
                    "productivity_status": productivity_status,
                },
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
        pending_line = _format_pending_phase_summary(
            pending_phases,
            planning_failed_phases,
        )
        failed_line = ", ".join(run_failed_phases) if run_failed_phases else "none"
        request_line = _compact_text_line(payload.message, limit=180)
        if continuation_of and continuation_snapshot == "target_not_found":
            continuity_line = (
                f"requested target not found (continuation_of={continuation_of})"
            )
        elif continuation_of and continuation_block_reason:
            continuity_line = (
                f"requested but not applied (continuation_of={continuation_of}; "
                f"reason={continuation_block_reason}; snapshot={continuation_snapshot or '-'})"
            )
        elif continuation_of:
            continuity_line = (
                f"yes (continuation_of={continuation_of}; "
                f"carryover={continuation_snapshot or '-'})"
            )
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
                continuation_requested=bool(continuation_effective),
                allow_low_productivity_override=bool(payload.allow_low_productivity_override),
                lead_advisory_mode=lead_advisory_mode,
                lead_degraded_delivery=lead_degraded_delivery,
                live_mode_required=live_mode_required,
                execution_mode=execution_mode,
                execution_steps=execution_steps,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
                productivity_score=productivity_score,
                reasoning_score=reasoning_score,
                evidence_gate_failures=evidence_gate_failures,
                semantic_gate_failures=semantic_gate_failures,
            ),
            run_type_policy,
        )
        final_state = _policy_outcome.final_state
        productivity_status = _policy_outcome.productivity_status
        next_action_hint = _policy_outcome.next_action_hint
        live_mode_rejected = _policy_outcome.live_mode_rejected
        semantic_gate_applied = _policy_outcome.semantic_gate_applied
        evidence_gate_applied = _policy_outcome.evidence_gate_applied
        strict_mode_applied = _policy_outcome.strict_mode_applied
        low_productivity_rejected = _policy_outcome.low_productivity_rejected
        low_productivity_override = _policy_outcome.low_productivity_override
        policy_review_required = _policy_outcome.policy_review_required
        policy_signals.extend(_policy_outcome.policy_signals)
        if planning_failed_phases:
            policy_signals = [
                signal for signal in policy_signals
                if signal not in {"evidence_gate_failed", "live_mode_required_non_live"}
            ]
            if final_state == "failed":
                next_action_hint = (
                    "Fallo en planning crítico: replanificar la fase "
                    + ", ".join(planning_failed_phases[:3])
                    + " antes de volver a abrir build/review/qa."
                )
        if preplanning_support_failure_detected:
            policy_signals = [
                signal
                for signal in policy_signals
                if signal
                not in {
                    "evidence_gate_failed",
                    "live_mode_required_non_live",
                    "low_productivity_below_threshold",
                }
            ]
            if final_state in {"failed", "rejected", "in_progress"}:
                final_state = "failed"
                next_action_hint = (
                    "Falló el contexto de soporte previo a planning ("
                    + ", ".join(preplanning_support_failed_phases[:3])
                    + "). El Lead debe reintentar con scout básico o contexto degradado, sin abrir build/review/qa todavía."
                )
        _repair_first_failures: list[str] = []
        _repair_first_material_evidence = {
            "build:no_successful_execution_steps",
            "build:no_successful_post_build_checks",
            "build:missing_test_or_build_check",
            "build:auto_post_build_validation_failed",
        }
        if _repair_first_mode:
            _repair_first_failures = list(
                dict.fromkeys(
                    [
                        failure
                        for failure in list(evidence_gate_failures)
                        if failure in _repair_first_material_evidence
                    ]
                    + [
                        failure
                        for failure in list(semantic_gate_failures)
                        if failure.startswith(("review:", "qa:"))
                    ]
                    + [f"phase_failed:{phase_name}" for phase_name in run_failed_phases]
                )
            )
            if _repair_first_failures:
                if "repair_first_required" not in policy_signals:
                    policy_signals.append("repair_first_required")
                policy_review_required = True
                repair_target = (
                    str(_auto_post_build_validation_result.get("target_task_id", "") or "")
                    if isinstance(_auto_post_build_validation_result, dict)
                    else ""
                )
                repair_detail = ", ".join(_repair_first_failures[:4])
                target_detail = f" Target: {repair_target}." if repair_target else ""
                next_action_hint = (
                    "Repair-first activo: repara primero el fallo material mas temprano "
                    f"({repair_detail}) antes de abrir un slice nuevo.{target_detail} "
                    "Usa un brief minimo, conserva el alcance original y vuelve a ejecutar el check real."
                )
                orch.event_logger.emit(
                    "chat_repair_first_required",
                    {
                        "task_id": task_root,
                        "failures": list(_repair_first_failures[:12]),
                        "auto_post_build_validation": {
                            key: value
                            for key, value in dict(
                                _auto_post_build_validation_result or {}
                            ).items()
                            if key not in {"stdout", "stderr"}
                        }
                        if isinstance(_auto_post_build_validation_result, dict)
                        else {},
                    },
                )
        _root_task_state_counts = _collect_root_task_state_counts()
        _runnable_task_count = int(_root_task_state_counts.get("ready", 0)) + int(
            _root_task_state_counts.get("claimed", 0)
        )
        _waiting_user_count = int(_root_task_state_counts.get("waiting_user", 0))
        _stalled_without_runnable = (
            final_state == "in_progress"
            and pending_tasks > 0
            and _runnable_task_count == 0
            and _waiting_user_count == 0
        )
        if _stalled_without_runnable:
            final_state = "rejected" if semantic_gate_applied else "failed"
            policy_review_required = True
            if "run_stalled_without_runnable_tasks" not in policy_signals:
                policy_signals.append("run_stalled_without_runnable_tasks")
            if not next_action_hint:
                next_action_hint = (
                    "La corrida se agotó sin tareas ejecutables restantes. "
                    "Replanifica desde la primera fase fallida o bloqueada."
                )
            orch.event_logger.emit(
                "chat_forced_terminal_state",
                {
                    "task_id": task_root,
                    "final_state": final_state,
                    "reason": "no_runnable_tasks_after_policy",
                    "pending_tasks": pending_tasks,
                    "task_state_counts": dict(_root_task_state_counts),
                },
            )
        for _policy_event in _policy_outcome.events:
            orch.event_logger.emit(_policy_event.event_type, _policy_event.payload)
        orch.event_logger.emit(
            "chat_policy_assessed",
            {
                "task_id": task_root,
                "final_state": final_state,
                "productivity_status": productivity_status,
                "semantic_gate_applied": semantic_gate_applied,
                "evidence_gate_applied": evidence_gate_applied,
                "policy_review_required": policy_review_required,
                "policy_signals": list(policy_signals),
            },
        )

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
            final_state=final_state,
            policy_review_required=policy_review_required,
            semantic_gate_failures=semantic_gate_failures,
            evidence_gate_failures=evidence_gate_failures,
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
                f"- placeholders/simulados detectados: {delegated_placeholder_count}"
            ] + delegation_results_lines

        execution_mode_label = execution_mode
        output_count_label = "placeholder_outputs"

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
        if lead_degraded_delivery:
            degrade_label = lead_degrade_scope or "partial"
            degrade_reason_text = (
                lead_degrade_reason
                or "El Lead decidió cerrar con entrega degradada y diagnóstico explícito."
            )
            response_lines.extend(
                [
                    "",
                    f"Degraded delivery ({degrade_label}): {degrade_reason_text}",
                ]
            )
        if skipped_phase_ids:
            skipped_lines = [
                f"- {phase_id}: {skipped_phase_reasons.get(phase_id, '') or 'sin razon explicitada'}"
                for phase_id in skipped_phase_ids
            ]
            response_lines.extend(
                [
                    "",
                    "Skipped phases by Lead:",
                    "\n".join(skipped_lines),
                ]
            )
        if (
            bool(payload.strict_mode)
            or live_mode_required
            or semantic_gate_applied
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
            _semantic_gate_label = (
                "failed_signal"
                if semantic_gate_applied and "semantic_gate_failed" in policy_signals
                else ("rejected" if semantic_gate_applied else "pass")
            )
            response_lines.extend(
                [
                    "",
                    f"Strict mode: {_strict_mode_label}",
                    f"Live mode gate: {_live_mode_label}",
                    f"Semantic gate: {_semantic_gate_label}",
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
        _semantic_gate_label = (
            "failed_signal"
            if semantic_gate_applied and "semantic_gate_failed" in policy_signals
            else ("rejected" if semantic_gate_applied else "pass")
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
                f"Semantic gate: {_semantic_gate_label} ({', '.join(semantic_gate_failures) if semantic_gate_failures else 'ok'})",
                f"Checks passed: {', '.join(successful_checks) if successful_checks else 'none'}",
                f"Evidence gate: {_evidence_gate_label} ({', '.join(evidence_gate_failures) if evidence_gate_failures else 'ok'})",
                f"Artifacts: created={artifact_created} modified={artifact_modified}",
                f"Quality: productivity={productivity_score}/100 ({productivity_status}) reasoning={reasoning_score}/100",
                f"Action hint: {next_action_hint}",
                f"Strict mode: {_strict_mode_label}",
                f"Low productivity gate: {_low_productivity_label}",
                f"Advisory mode: {'on' if lead_advisory_mode else 'off'} ({lead_advisory_reason or '-'})",
                f"Degraded delivery: {'on' if lead_degraded_delivery else 'off'} ({lead_degrade_scope or '-'} | {lead_degrade_reason or '-'})",
                f"Skipped phases: {', '.join(skipped_phase_ids) if skipped_phase_ids else 'none'}",
                f"Policy review required: {'yes' if policy_review_required else 'no'}",
                f"Policy signals: {', '.join(policy_signals) if policy_signals else 'none'}",
                f"Auto-extended rounds: +{auto_extended_rounds}",
                "",
                "Workflow phases:",
                workflow_lines,
            ])

        merged_response = _limit_chat_response("\n".join(response_lines))
        _specialist_insights = _specialist_insight_fields(runtime_dir, task_root)
        _peer_consultation_insights = _peer_consultation_summary_fields(
            runtime_dir, task_root
        )
        _memory_result = "parcial"
        if final_state == "completed":
            if (
                not lead_advisory_mode
                and not lead_degraded_delivery
                and not run_failed_phases
                and not root_blocked_phases
                and not semantic_gate_failures
                and not evidence_gate_failures
            ):
                _memory_result = "exitoso"
        elif final_state in {"failed", "rejected"}:
            _memory_result = "fallido"

        _failure_origin = _determine_run_failure_origin(
            preplanning_support_failure_detected=preplanning_support_failure_detected,
            planning_failed_phases=planning_failed_phases,
            failed_phases=run_failed_phases,
            blocked_phases=root_blocked_phases,
            semantic_gate_failures=semantic_gate_failures,
            evidence_gate_failures=evidence_gate_failures,
        )
        _failed_phase_root_causes = _failed_phase_root_cause_reason_codes(
            task_rows_by_phase,
            run_failed_phases,
        )
        _run_verdict_reason_codes = list(
            dict.fromkeys(
                list(preplanning_support_reason_codes)
                + list(_failed_phase_root_causes)
                + list(semantic_gate_failures)
                + list(evidence_gate_failures)
                + (["repair_first_required"] if _repair_first_failures else [])
                + [f"phase_failed:{phase_name}" for phase_name in run_failed_phases]
                + [f"phase_blocked:{phase_name}" for phase_name in root_blocked_phases]
            )
        )
        _run_verdict = {
            "state": final_state,
            "result": _memory_result,
            "failure_origin": _failure_origin,
            "reason_codes": _run_verdict_reason_codes[:24],
            "policy_signals": list(policy_signals[:24]),
            "policy_review_required": bool(policy_review_required),
            "semantic_gate_applied": bool(semantic_gate_applied),
            "semantic_gate_failures": list(semantic_gate_failures[:12]),
            "evidence_gate_applied": bool(evidence_gate_applied),
            "evidence_gate_failures": list(evidence_gate_failures[:12]),
            "failed_phases": list(run_failed_phases[:12]),
            "blocked_phases": list(root_blocked_phases[:12]),
            "cascade_blocked_phases": list(cascade_blocked_phases[:12]),
            "advisory_context_failed_phases": list(advisory_context_failed_phases[:12]),
            "pending_phases": list(pending_phases[:12]),
            "advisory_mode": bool(lead_advisory_mode),
            "degraded_delivery": bool(lead_degraded_delivery),
            "repair_first_mode": bool(_repair_first_mode),
            "run_profile": run_profile,
            "repair_first_required": bool(_repair_first_failures),
            "repair_first_failures": list(_repair_first_failures[:12]),
            "next_action_hint": next_action_hint,
            "updated_at": local_now_iso(),
        }
        if isinstance(_auto_post_build_validation_result, dict) and _auto_post_build_validation_result:
            _run_verdict["auto_post_build_validation"] = {
                key: value
                for key, value in dict(_auto_post_build_validation_result).items()
                if key not in {"stdout", "stderr"}
            }
        _lead_close_policy = _coerce_lead_close_policy(
            derive_lead_close_policy(
                phase_verdicts=_ws.get("phase_verdicts", {}),
                phase_states=phase_states,
                run_verdict=_run_verdict,
            )
        )
        _lead_close_state = str(
            _lead_close_policy.get("authoritative_close_state", "") or ""
        ).strip().lower()
        _lead_close_blocking_signals = [
            str(item or "").strip()
            for item in list(_lead_close_policy.get("blocking_signals", []) or [])
            if str(item or "").strip()
        ]
        if final_state == "completed" and _lead_close_state == "rejected":
            final_state = "rejected"
            _run_verdict["state"] = "rejected"
            _run_verdict["result"] = "fallido"
            _run_verdict["reason_codes"] = list(
                dict.fromkeys(
                    list(_run_verdict.get("reason_codes", []) or [])
                    + [f"lead_close_policy:{signal}" for signal in _lead_close_blocking_signals]
                )
            )[:24]
            _run_verdict["policy_signals"] = list(
                dict.fromkeys(
                    list(_run_verdict.get("policy_signals", []) or [])
                    + ["lead_close_policy_rejected"]
                )
            )[:24]
            _run_verdict["next_action_hint"] = (
                "El cierre autoritativo detecto bloqueo: "
                + ", ".join(_lead_close_blocking_signals[:4])
            )
            next_action_hint = str(_run_verdict["next_action_hint"])
            _lead_close_policy = _coerce_lead_close_policy(
                derive_lead_close_policy(
                    phase_verdicts=_ws.get("phase_verdicts", {}),
                    phase_states=phase_states,
                    run_verdict=_run_verdict,
                )
            )
        _ws["run_status"] = final_state
        _ws["run_verdict"] = dict(_run_verdict)
        orch._save_workflow_state()
        orch.event_logger.emit(
            "chat_run_verdict_persisted",
            {
                "task_id": task_root,
                **_run_verdict,
            },
        )
        _response_delegated_task_ids = [] if direct_profile_mode else delegated_task_ids

        _token_queue.put(("done", None))
        _progress_snapshot = _build_chat_progress(runtime_dir, task_root)
        result = TeamChatResponse(
            task_id=task_root,
            role=Role.TEAM_LEAD.value,
            state=final_state,
            response=merged_response,
            decision_justification=lead_justification,
            elapsed_ms=elapsed_ms,
            lead_task_id=lead_task_id,
            delegated_task_ids=_response_delegated_task_ids,
            phase_task_ids=phase_task_ids,
            chat_mode=response_mode,
            run_profile=run_profile,
            round_budget=round_budget,
            rounds_used=rounds_used,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            continuation_requested=continuation_requested,
            continuation_effective=continuation_effective,
            continuation_of=continuation_of,
            continuation_block_reason=continuation_block_reason,
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
            degraded_delivery=lead_degraded_delivery,
            degrade_scope=lead_degrade_scope,
            degrade_reason=lead_degrade_reason,
            skipped_phase_ids=skipped_phase_ids,
            skipped_phase_reasons=skipped_phase_reasons,
            policy_review_required=policy_review_required,
            validation_owner=CHAT_VALIDATION_OWNER,
            policy_signals=policy_signals,
            run_verdict=dict(_run_verdict),
            lead_close_policy=_lead_close_policy,
            phase_contracts=_coerce_phase_contracts(
                _ws.get("phase_contracts", {})
            ),
            phase_verdicts=coerce_phase_verdicts(
                _ws.get("phase_verdicts", {})
            ),
            phase_evidence_plan=_coerce_phase_evidence_plan(
                _ws.get("phase_evidence_plan", _chat_run_state.phase_evidence_plan)
            ),
            delegate_batches=_coerce_delegate_batches(
                _ws.get("delegate_batches", [])
            ),
            delegate_economics=dict(
                _ws.get("delegate_economics_summary", {}) or {}
            ),
            **_specialist_insights,
            **_peer_consultation_insights,
            phase_states=dict(_progress_snapshot.phase_states),
            failed_tasks=int(_progress_snapshot.failed_tasks),
            task_summaries=list(_progress_snapshot.task_summaries),
            thread_summary=dict(_progress_snapshot.thread_summary),
            next_action_hint=next_action_hint,
            strict_mode=bool(payload.strict_mode),
            strict_mode_applied=strict_mode_applied,
            repair_first_mode=bool(_repair_first_mode),
            auto_extended_rounds=auto_extended_rounds,
            productivity_threshold=productivity_threshold,
            low_productivity_rejected=low_productivity_rejected,
            low_productivity_override=low_productivity_override,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
            placeholder_output_ratio=round(placeholder_output_ratio, 4),
            evidence_gate_applied=evidence_gate_applied,
            evidence_gate_failures=evidence_gate_failures,
            probe_mode=probe_mode,
            lead_run_mode=_lead_run_mode,
            planned_phases=_planned_phases,
            is_sim_mode=sim_mode_enabled(),
        )
        _memory_phases = [
            phase_name
            for phase_name in workflow_phase_keys
            if phase_name != "lead_intake"
        ]
        _memory_completed = sum(
            1
            for phase_name in _memory_phases
            if phase_states.get(phase_name) == "completed"
        )
        _memory_errors = [
            f"phase_failed:{phase_name}" for phase_name in failed_phases
        ] + list(semantic_gate_failures[:4]) + list(evidence_gate_failures[:4])
        _memory_errors.extend(
            _compact_text_line(
                f"routing:{str(item.get('phase', '') or '-')}:"
                f"{str(item.get('error', '') or item.get('reason', '') or 'unknown')}",
                limit=120,
            )
            for item in orch.router.get_recent_routing_failures(task_root)[:4]
        )
        _memory_decisions: list[str] = []
        if lead_advisory_mode:
            _memory_decisions.append(
                f"ADVISORY_MODE:{_compact_text_line(lead_advisory_reason or 'active', limit=80)}"
            )
        if lead_degraded_delivery:
            _memory_decisions.append(
                f"DEGRADE:{lead_degrade_scope or 'partial'}"
            )
        _memory_decisions.extend(
            f"SKIP_PHASE:{phase_id}" for phase_id in skipped_phase_ids
        )
        try:
            update_lead_memory(
                runtime_dir=runtime_dir,
                project_root=workspace,
                chat_id=task_root,
                objective=payload.message,
                result=_memory_result,
                phases_completed=_memory_completed,
                phases_total=len(_memory_phases),
                significant_errors=_memory_errors,
                lead_decisions=_memory_decisions,
                duration_seconds=max(1, elapsed_ms // 1000),
                capabilities=observe_capabilities_snapshot(
                    runtime_dir=runtime_dir,
                    mcp_status=mcp_status_rows,
                    subscription_providers=sorted({
                        str(adapter.provider).strip()
                        for adapter in orch.router.adapters
                        if str(getattr(adapter, "channel", "") or "").lower() == "subscription"
                        and str(getattr(adapter, "provider", "") or "").strip()
                    }) or None,
                ),
            )
            orch.event_logger.emit(
                "lead_memory_updated",
                {
                    "task_id": task_root,
                    "result": _memory_result,
                    "phases_completed": _memory_completed,
                    "phases_total": len(_memory_phases),
                },
            )
        except Exception as exc:
            orch.event_logger.emit(
                "lead_memory_update_failed",
                {"task_id": task_root, "error": str(exc)[:200]},
            )
        # C3: if workspace has no product artifacts and lead_intake completed,
        # deposit a minimal PROJECT_PLAN.md so the user sees tangible output.
        _c3_deposited = _maybe_deposit_minimal_output(
            workspace=workspace,
            lead_output=_lead_output_clean,
            chat_id=task_root,
            run_mode=_lead_run_mode,
        )
        if _c3_deposited:
            orch.event_logger.emit(
                "minimal_output_deposited",
                {"path": _c3_deposited, "chat_id": task_root},
            )
        return result

    _stream_task_root = _resolve_task_root(payload.client_task_id)
    _active_conflict_root = _claim_workspace_active_run(workspace, _stream_task_root)
    if _active_conflict_root:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Ya hay una run activa en este workspace ({_active_conflict_root}). "
                    "Espera a que termine o reanúdala desde Continue/clarify antes de abrir otra."
                ),
                "active_run": _workspace_active_run_detail(runtime_dir, _active_conflict_root),
            },
        )

    async def _event_stream():
        import asyncio as _asyncio

        _chat_fut = _asyncio.get_event_loop().run_in_executor(None, _run_chat)
        try:
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
                            if len(_resp_full) > 6000:
                                result_dict = dict(result_dict)
                                result_dict["response"] = _resp_full[:6000]
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
                            if len(_resp_full) > 6000:
                                result_dict = dict(result_dict)
                                result_dict["response"] = _resp_full[:6000]
                                result_dict["response_truncated"] = True
                            yield f"event: result\ndata: {json.dumps(result_dict, ensure_ascii=False, default=str)}\n\n"
                        except Exception as exc:
                            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                        break
                    # RC-H: cancel flag — close stream if the run was cancelled
                    if _RUN_CANCEL_FLAGS.pop(_stream_task_root, False):
                        yield f"event: cancelled\ndata: {json.dumps({'task_id': _stream_task_root})}\n\n"
                        break
                    yield "event: keepalive\ndata: {}\n\n"
        finally:
            _release_workspace_active_run(workspace, _stream_task_root)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


def _derive_resume_state_from_phase_states(phase_states: dict[str, str]) -> str:
    states = {
        str(state or "").strip().lower()
        for state in phase_states.values()
        if str(state or "").strip()
    }
    lead_close_state = str(phase_states.get("lead_close", "") or "").strip().lower()
    if "waiting_user" in states:
        return "waiting_user"
    if states.intersection({"failed", "rejected"}):
        return "failed"
    if states.intersection({"pending", "ready", "claimed"}):
        return "in_progress"
    if "blocked" in states:
        return "failed" if lead_close_state == "completed" else "in_progress"
    if states and states.issubset({"completed", "skipped", "archived"}):
        return "completed"
    return "completed" if lead_close_state == "completed" else "in_progress"


def _classify_midrun_user_risk_acceptance(question: str, clarification: str) -> dict[str, str]:
    """Detect a user-approved degraded close from a Lead pause question.

    This is intentionally conservative: it only triggers when the Lead's question
    offered a risk-bearing path (for example proceeding without formal QA) and
    the answer accepts that path instead of asking for retry/wait/replan.
    """
    q = re.sub(r"\s+", " ", str(question or "")).strip().lower()
    a = re.sub(r"\s+", " ", str(clarification or "")).strip().lower()
    if not q or not a:
        return {}

    question_offers_risk_close = (
        any(token in q for token in ("aprobar", "approve", "acept", "accept", "asum"))
        and any(
            token in q
            for token in (
                "sin qa",
                "sin valid",
                "without qa",
                "without validation",
                "formal",
                "bloque",
                "blocked",
                "gate",
                "routing",
                "degrad",
                "riesgo",
                "risk",
            )
        )
    )
    answer_accepts_risk = any(
        token in a
        for token in (
            "aprobar",
            "aprueba",
            "aprobado",
            "approve",
            "acepto",
            "aceptar",
            "accept",
            "asumo",
            "assume",
            "sin qa",
            "sin valid",
            "without qa",
            "without validation",
            "adelante",
            "proceed",
            "continua",
            "continuar",
        )
    ) or bool(re.fullmatch(r"(si|sí|yes|ok|vale|dale|go)", a))
    answer_prefers_retry_or_wait = any(
        token in a
        for token in (
            "espera",
            "esperar",
            "wait",
            "reintenta",
            "retry",
            "otra ruta",
            "reroute",
            "replan",
            "replantea",
            "no apruebo",
            "no aprobar",
            "no accept",
        )
    )
    if not question_offers_risk_close or not answer_accepts_risk or answer_prefers_retry_or_wait:
        return {}
    return {
        "kind": "user_accepted_degraded_close",
        "scope": "partial",
        "reason": "user_accepted_risk_after_mid_run_pause",
    }


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
                phase_contracts=_coerce_phase_contracts(
                    (
                        orch_resume._get_workflow_state(task_root).get("phase_contracts", {})
                        if waiting_task is None
                        else {}
                    )
                ),
                phase_verdicts=coerce_phase_verdicts(
                    (
                        orch_resume._get_workflow_state(task_root).get("phase_verdicts", {})
                        if waiting_task is None
                        else {}
                    )
                ),
                phase_evidence_plan=(
                    _chat_run_state.phase_evidence_plan if _chat_run_state is not None else {}
                ),
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                is_sim_mode=sim_mode_enabled(),
            )

        _risk_acceptance = _classify_midrun_user_risk_acceptance(question, clarification)
        # Inyectar respuesta del usuario en la descripción de la tarea pausada
        _inject = (
            f"\n\n[Respuesta del usuario a tu pregunta previa '{question}': "
            f"{clarification}]"
        )
        if _risk_acceptance:
            _inject += (
                "\n\n[Instruccion de control: el usuario acepto continuar con riesgo "
                "tras una pregunta explicita del Lead. Si cierras sin resolver la "
                "validacion bloqueada, emite una directiva "
                '[DEGRADE: scope="partial" reason="user accepted risk after mid-run pause"] '
                "y declara el riesgo pendiente sin ocultarlo.]"
            )
            waiting_task.metadata["mid_run_user_decision"] = dict(_risk_acceptance)
            waiting_task.metadata["lead_degraded_delivery"] = True
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
                "user_risk_acceptance": bool(_risk_acceptance),
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

        _resume_state = _derive_resume_state_from_phase_states(_phase_states)
        _lead_close_completed = (
            str(_phase_states.get("lead_close", "") or "").strip().lower() == "completed"
        )
        if _risk_acceptance and _lead_close_completed:
            _resume_state = "completed"
            _ws_r["run_verdict"] = {
                "state": "completed",
                "result": "parcial",
                "failure_origin": "none",
                "reason_codes": ["user_accepted_degraded_close"],
                "policy_signals": ["user_accepted_degraded_close"],
                "policy_review_required": False,
                "advisory_mode": False,
                "degraded_delivery": True,
                "degrade_scope": str(_risk_acceptance.get("scope", "partial") or "partial"),
                "degrade_reason": str(_risk_acceptance.get("reason", "") or ""),
                "user_risk_acceptance": True,
                "blocked_phases": [
                    phase
                    for phase, state in sorted(_phase_states.items())
                    if str(state or "").strip().lower() == "blocked"
                ][:12],
                "failed_phases": [
                    phase
                    for phase, state in sorted(_phase_states.items())
                    if str(state or "").strip().lower() in {"failed", "rejected"}
                ][:12],
                "next_action_hint": (
                    "El usuario acepto un cierre degradado; documenta los riesgos "
                    "pendientes y reintenta validacion formal en una run posterior."
                ),
                "updated_at": local_now_iso(),
            }
        _ws_r["run_status"] = _resume_state
        orch_resume._save_workflow_state(task_root)
        orch_resume.event_logger.emit(
            "chat_run_resumed",
            {
                "task_id": task_root,
                "phase": waiting_phase,
                "final_state": _resume_state,
                "user_risk_acceptance": bool(_risk_acceptance),
            },
        )
        _resume_specialist_insights = _specialist_insight_fields(runtime_dir, task_root)
        _resume_peer_consultation = _peer_consultation_summary_fields(
            runtime_dir, task_root
        )

        return TeamChatResponse(
            task_id=task_root,
            role=preferred_role_str,
            state=_resume_state,
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
            phase_contracts=_coerce_phase_contracts(
                _ws_r.get("phase_contracts", {})
            ),
            phase_verdicts=coerce_phase_verdicts(
                _ws_r.get("phase_verdicts", {})
            ),
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
            degraded_delivery=bool(_risk_acceptance),
            degrade_scope=(
                str(_risk_acceptance.get("scope", "") or "") if _risk_acceptance else ""
            ),
            degrade_reason=(
                str(_risk_acceptance.get("reason", "") or "") if _risk_acceptance else ""
            ),
            run_verdict=dict(_ws_r.get("run_verdict", {}) or {}),
            lead_close_policy=_coerce_lead_close_policy(
                derive_lead_close_policy(
                    phase_verdicts=_ws_r.get("phase_verdicts", {}),
                    phase_states=_phase_states,
                    run_verdict=_ws_r.get("run_verdict", {}),
                )
            ),
            **_resume_specialist_insights,
            **_resume_peer_consultation,
            is_sim_mode=sim_mode_enabled(),
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
                        if len(_resp_r) > 6000:
                            result_dict_r = dict(result_dict_r)
                            result_dict_r["response"] = _resp_r[:6000]
                            result_dict_r["response_truncated"] = True
                        yield f"event: result\ndata: {json.dumps(result_dict_r, ensure_ascii=False, default=str)}\n\n"
                    except Exception as exc:
                        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    break
                yield "event: keepalive\ndata: {}\n\n"

    return StreamingResponse(_resume_event_stream(), media_type="text/event-stream")
@app.post("/api/aiteam/chat/clarify")
async def post_aiteam_chat_clarify(payload: ClarifyRequest, request: Request):
    """Reanuda un run pausado con [CLARIFY] inyectando la respuesta del usuario.

    Carga el estado pendiente, construye un nuevo TeamChatRequest con la
    clarificación añadida al mensaje original, y ejecuta el chat normalmente.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

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

    if pending_type == "mid_run":
        # Reanudar el run desde la tarea pausada sin reiniciar el workflow completo
        try:
            result = _build_resume_stream(
                pending_state, payload.clarification, runtime_dir
            )
        except Exception:
            raise
        pending_file.unlink(missing_ok=True)
        return result

    # ── Path original: pausa de lead_intake ─────────────────────────────────
    original_payload_data = pending_state.get("original_payload", {})
    original_message = pending_state.get("original_message", original_payload_data.get("message", ""))
    question = pending_state.get("question", "")
    continuation_of = _normalize_task_root(
        str(
            pending_state.get("continuation_of")
            or original_payload_data.get("continuation_of")
            or ""
        )
    )
    selected_policy = _classify_clarification_continuation_policy(
        question,
        payload.clarification,
    )
    base_message = original_message
    if selected_policy == "clean_retry":
        base_message = _sanitize_message_for_clean_retry(
            original_message,
            continuation_of,
        )
        if not base_message:
            base_message = "Start the next highest-impact slice for the same project objective."
    elif not base_message.strip():
        base_message = original_payload_data.get("message", "")

    clarification_for_prompt = _canonicalize_clarification_for_prompt(
        payload.clarification,
        selected_policy,
    )

    # Inyectar la respuesta del usuario en el mensaje original
    augmented_message = (
        f"{base_message}\n\n"
        f"[Respuesta del usuario a tu pregunta previa '{question}': "
        f"{clarification_for_prompt}]"
    )

    # Construir nuevo request reutilizando los parámetros originales, pero con
    # un task_root nuevo para no colisionar con la pausa waiting_user original.
    new_payload = TeamChatRequest(
        message=augmented_message,
        role=original_payload_data.get("role", "engineer"),
        complexity=original_payload_data.get("complexity", "medium"),
        criticality=original_payload_data.get("criticality", "medium"),
        mode=original_payload_data.get("mode", "sprint5"),
        run_profile=original_payload_data.get("run_profile", "team_advanced"),
        max_rounds=original_payload_data.get("max_rounds"),
        client_task_id="",
        strict_mode=original_payload_data.get("strict_mode", False),
        auto_extend_weak_runs=original_payload_data.get("auto_extend_weak_runs", False),
        repair_first_mode=original_payload_data.get("repair_first_mode", False),
        allow_low_productivity_override=original_payload_data.get(
            "allow_low_productivity_override", True
        ),
        continuation_policy=selected_policy,
    )

    try:
        result = await post_aiteam_chat(new_payload, request)
    except Exception:
        raise

    archived_count = _archive_incomplete_tasks_for_root(
        runtime_dir,
        task_root=task_root,
        reason=f"clarification_resolved::{selected_policy}",
    )
    pending_file.unlink(missing_ok=True)
    if archived_count:
        try:
            orch = build_default_orchestrator(
                runtime_dir=runtime_dir,
                browser_mode="basic",
                environment="dev",
            )
            orch.event_logger.emit(
                "chat_waiting_user_archived_after_clarify",
                {
                    "task_id": task_root,
                    "archived_tasks": archived_count,
                    "selected_policy": selected_policy,
                },
            )
        except Exception:
            pass
    return result


@app.post("/api/aiteam/chat/{task_root}/cancel")
async def post_aiteam_chat_cancel(task_root: str, request: Request):
    """RC-H: Cancela un run activo cerrando su SSE stream y archivando sus tareas.

    1. Establece el flag de cancelacion — el streaming loop lo detecta en el
       proximo keepalive (≤ 2 s) y cierra la conexion.
    2. Archiva en el SQLite todas las tareas incomplete de ese chat_root para que
       run_until_idle() no siga procesandolas en el hilo de fondo.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)

    normalized = _normalize_task_root(task_root)
    # 1. Signal the streaming loop to close
    _RUN_CANCEL_FLAGS[normalized] = True

    # 2. Archive incomplete tasks in the DB so run_until_idle drains quickly
    archived_count = 0
    try:
        import sqlite3 as _sqlite3, json as _json_cancel
        db_path = runtime_dir / "aiteam.db"
        if db_path.exists():
            with _sqlite3.connect(str(db_path)) as _conn:
                _rows = _conn.execute("SELECT task_id, payload FROM tasks").fetchall()
                _to_archive = []
                for _tid, _raw in _rows:
                    if not str(_tid).startswith(f"{normalized}::"):
                        continue
                    try:
                        _p = _json_cancel.loads(_raw)
                    except Exception:
                        continue
                    if _p.get("state") not in ("completed", "failed", "archived", "cancelled"):
                        _p["state"] = "archived"
                        _p.setdefault("metadata", {})["archived_reason"] = f"cancelled_by_user::{normalized}"
                        _to_archive.append((_json_cancel.dumps(_p), _tid))
                if _to_archive:
                    _conn.executemany("UPDATE tasks SET payload = ? WHERE task_id = ?", _to_archive)
                    _conn.commit()
                    archived_count = len(_to_archive)
    except Exception as _ex:
        pass  # best-effort — the cancel flag is still set

    return {
        "cancelled": True,
        "task_root": normalized,
        "archived_tasks": archived_count,
        "note": "SSE stream will close within 2 seconds; background thread drains then terminates.",
    }


@app.get("/api/aiteam/chat/progress/{task_id}", response_model=TeamChatProgressResponse)
async def get_aiteam_chat_progress(task_id: str, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    normalized_root = _normalize_task_root(task_id)
    if not normalized_root or not runtime_dir.exists():
        return {"task_id": normalized_root or task_id, "messages": []}

    def _load():
        tasks_payload = _read_runtime_tasks_payload(runtime_dir)
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
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    import queue as queue_module

    task_root = f"CHAT-{uuid.uuid4().hex[:8].upper()}"
    progress_queue = queue_module.Queue()

    with _background_runs_lock:
        _background_runs[task_root] = {
            "status": "running",
            "progress_queue": progress_queue,
            "result": None,
            "started_at": local_now_iso(),
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
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
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
                "timestamp": _display_ts_local(m.timestamp),
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


_FS_TREE_EXCLUDED_NAMES = {
    ".git",
    "__pycache__",
    "venv",
    ".pytest_cache",
    ".aiteam_snapshots",
    "node_modules",
}
_FS_TREE_EXCLUDED_PREFIXES = (".tmp",)
_FS_TREE_RUNTIME_TMP_PARENTS = {"runtime", ".aiteam"}


def _should_exclude_fs_tree_path(path: Path, workspace: Path) -> bool:
    if path == workspace:
        return False
    name = path.name
    if name in _FS_TREE_EXCLUDED_NAMES:
        return True
    if any(name.startswith(prefix) for prefix in _FS_TREE_EXCLUDED_PREFIXES):
        return True
    if name == "tmp" and path.parent.name in _FS_TREE_RUNTIME_TMP_PARENTS:
        return True
    return False


@app.get("/api/fs/tree")
async def get_fs_tree(request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)

    def build_tree(path: Path):
        name = path.name
        if _should_exclude_fs_tree_path(path, workspace):
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

    return await asyncio.to_thread(build_tree, workspace)
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
