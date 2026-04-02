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
    _is_context_only_query,
    _is_continuation_message,
    _normalize_chat_mode,
    _normalize_task_root,
    _recent_chat_roots,
    _resolve_chat_round_budget,
    _resolve_task_root,
    _safe_int_value,
)
from api.chat_delegate import (
    _aggregate_delegate_results,
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
    _extract_delegate_request_from_outputs,
    _extract_force_gate_request_from_outputs,
    _extract_replan_phases_from_outputs,
    _extract_retry_route_request_from_outputs,
    _extract_skip_request_from_outputs,
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
from aiteam.quorum import (
    PLANNING_QUORUM_RUN_MODES,
    QuorumResult,
    run_planning_quorum,
    should_apply_planning_quorum,
)
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


_PLANNING_RUN_MODES = set(PLANNING_QUORUM_RUN_MODES)


def _safe_plan_slug(value: str, *, fallback: str = "plan") -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized or fallback


def _resolve_project_plan_dir(workspace: Path) -> Path:
    docs_dir = workspace / "docs"
    if docs_dir.exists():
        return docs_dir / "aiteam"
    return workspace / "planning"


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
    timestamp = datetime.now(timezone.utc)
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
    _build_project_continuity_context,
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
    # Check if there are product files outside .aiteam/
    aiteam_dir = workspace / ".aiteam"
    try:
        product_files = [
            f for f in workspace.rglob("*")
            if f.is_file()
            and not str(f.resolve()).startswith(str(aiteam_dir.resolve()))
            and f.name not in {".gitignore", ".gitkeep"}
        ]
    except Exception:
        return None
    if product_files:
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
        requested_mode = str(payload.mode or "").strip().lower()
        probe_mode = requested_mode == "probe"
        chat_mode = _normalize_chat_mode(payload.mode)
        response_mode = "probe" if probe_mode else chat_mode
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
        project_instructions_block = _project_instructions_block(workspace)

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
                f"{project_instructions_block}"
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
                f"{project_instructions_block}"
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
                chat_mode=response_mode,
                phase_evidence_plan={},
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=probe_mode,
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
        _quorum_result: QuorumResult | None = None
        if should_apply_planning_quorum(
            requested=bool(payload.quorum),
            run_mode=_lead_run_mode,
        ) and _lcp_resolution.early_exit is None:
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
        _planned_phases = [
            {
                "phase_id": spec.phase_id,
                "role": spec.role,
                "objective": spec.objective,
                "depends_on": list(spec.depends_on or []),
            }
            for spec in _lcp_resolution.phases
        ]
        _persisted_plan_path = _persist_planning_markdown(
            workspace=workspace,
            task_root=task_root,
            run_mode=_lead_run_mode,
            message=payload.message,
            lead_output=_lead_output_clean,
            planned_phases=_planned_phases,
            quorum_result=_quorum_result,
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
        if probe_mode:
            artifact_after = _workspace_artifact_snapshot(workspace)
            created_artifacts, modified_artifacts = _workspace_artifact_diff(
                artifact_before, artifact_after
            )
            artifact_created = len(created_artifacts)
            artifact_modified = len(modified_artifacts)
            artifact_files = sorted(set(created_artifacts + modified_artifacts))
            orch.event_logger.emit(
                "chat_probe_completed",
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
                or "Probe completado. El Lead devolvio un plan sin ejecutar fases.",
                decision_justification=(
                    "Modo probe: se ejecuto solo lead_intake y se devolvio el plan sin "
                    "crear ni ejecutar fases dinamicas."
                ),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                lead_task_id=lead_task_id,
                delegated_task_ids=[],
                phase_task_ids={"lead_intake": lead_task_id},
                chat_mode=response_mode,
                round_budget=_lcp_resolution.round_budget,
                phase_evidence_plan=_phase_evidence_plan,
                delegate_batches=[],
                delegate_economics={},
                specialist_reports=[],
                specialist_report_summary={},
                probe_mode=True,
                lead_run_mode=_lead_run_mode,
                planned_phases=_planned_phases,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
                artifact_files=artifact_files,
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
                                        local_require_execution_plan 
                                        if (_is_engineer and _spec.phase_id not in ("plan_engineering", "plan_engineer")) 
                                        else False
                                    )
                                ),
                                "required_capabilities": _caps,
                                "require_peer_consultation": True,
                                "phase": _spec.phase_id,
                                "chat_parent": task_root,
                                "run_mode": _lead_run_mode,
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
                _phase_deferred_specs: list[dict] = []
                for _evidence_spec in _evidence_specs:
                    _evidence_task_id = f"{task_root}::{_evidence_spec['phase_id']}"
                    if orch.taskboard.get_task(_evidence_task_id) is not None:
                        local_delegated_task_ids.append(_evidence_task_id)
                        continue
                    # C1: delegate evidence tasks are created lazily when the parent
                    # phase starts (CLAIMED). We pre-compute the full task spec and
                    # store it in the parent phase task's metadata so the orchestrator
                    # can spawn it at execution time without needing api/ context.
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
                            f"Objetivo de la fase: {_spec.objective}\n"
                            f"Solicitud original: {payload.message}\n"
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
                            "skill_targets": _evidence_spec["skill_targets"],
                            "lsp_targets": _evidence_spec["lsp_targets"],
                            **_specialist_meta,
                        },
                    }
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
                            "run_mode": _lead_run_mode,
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
                chat_mode=response_mode,
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
            require_test_or_build_check=True,
        )
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
        _specialist_insights = _specialist_insight_fields(runtime_dir, task_root)
        _peer_consultation_insights = _peer_consultation_summary_fields(
            runtime_dir, task_root
        )

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
            chat_mode=response_mode,
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
            **_specialist_insights,
            **_peer_consultation_insights,
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
            probe_mode=probe_mode,
            lead_run_mode=_lead_run_mode,
            planned_phases=_planned_phases,
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
        _resume_specialist_insights = _specialist_insight_fields(runtime_dir, task_root)
        _resume_peer_consultation = _peer_consultation_summary_fields(
            runtime_dir, task_root
        )

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
            **_resume_specialist_insights,
            **_resume_peer_consultation,
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
