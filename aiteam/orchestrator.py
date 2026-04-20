from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
import threading
from dataclasses import dataclass
from pathlib import Path

# Timeout para quality gates: si Reviewer/QA no completa en este tiempo,
# se escala automáticamente al Team Lead. Configurable por env var.
_GATE_TIMEOUT_SECONDS: int = int(os.getenv("AITEAM_GATE_TIMEOUT_SECONDS", "7200"))

from aiteam.adapters.registry import load_external_adapters
from aiteam.agent_session import ConversationThread, SessionStore, ThreadStore
from aiteam.autotools import AutoToolIntegrator, ToolIntegrationReport
from aiteam.communication import MeetingParticipant, TeamCommunicator
from aiteam.compliance import ComplianceGuard, CompliancePolicy
from aiteam.execution import (
    BrowserController,
    CommandPolicy,
    ExecutionEngine,
    LocalCommandExecutor,
    PlaywrightBrowserController,
)
from aiteam.chat_policy import uses_chat_policy
from aiteam.context_curator import (
    ContextCuratorStore,
    estimate_context_compaction_value,
    estimate_context_pressure,
)
from aiteam.mailbox import Mailbox
from aiteam.lead_control import extract_lcp_directives
from aiteam.lead_close_policy import (
    build_lead_close_policy_prompt_block,
    derive_lead_close_policy,
)
from aiteam.memory import AgentMemoryStore
from aiteam.observability import EventLogger
from aiteam.phase_verdicts import (
    _looks_like_noise_path_hint,
    detect_contract_path_drift,
    detect_continuation_drift,
    extract_path_candidates,
    extract_phase_verdict,
    is_missing_contract_objective,
)
from aiteam.profiles import build_prompt, build_system_prompt, role_charter_for
from aiteam.run_health import RunHealthReport, build_run_health_report
from aiteam.router import HybridRouter
from aiteam.runtime import SandboxManager
from aiteam.sim_mode import sim_mode_enabled
from aiteam.sqlite_store import SqliteStore
from aiteam.taskboard import TaskBoard
from aiteam.tool_specialists import (
    build_tool_specialist_metadata,
    infer_tool_specialist,
    parse_specialist_report,
    select_specialists_for_task,
    specialist_profile,
)
from aiteam.tool_inventory import (
    derive_target_capabilities,
    normalize_lsp_targets,
    normalize_skill_targets,
    normalize_tool_capabilities,
)
from aiteam.types import (
    Complexity,
    Criticality,
    Role,
    RoutingDecision,
    RoutingRequest,
    StreamChunk,
    TaskState,
    WorkTask,
)
from aiteam.evidence_gate import (
    assess_output_quality as _assess_output_quality_fn,
    build_gate_evidence_context as _build_gate_evidence_context_fn,
    detect_conversational_task as _detect_conversational_task_fn,
    summarize_git_diff as _summarize_git_diff_fn,
    verify_task_evidence as _verify_task_evidence_fn,
    _CONVERSATIONAL_KEYWORDS as _EVIDENCE_GATE_KEYWORDS,
)


@dataclass
class PeerConsultationReport:
    text: str
    consulted_roles: list[str]
    unavailable_roles: list[str]
    consulted_providers: list[str] | None = None


_PLACEHOLDER_OUTPUT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("todo:", re.compile(r"todo:", re.IGNORECASE)),
    ("fixme:", re.compile(r"fixme:", re.IGNORECASE)),
    ("simulated output", re.compile(r"simulated output", re.IGNORECASE)),
    ("insert code here", re.compile(r"insert code here", re.IGNORECASE)),
    (
        "placeholder marker",
        re.compile(
            r"(\[placeholder[^\]]*\]|<placeholder[^>]*>|\bplaceholder(?: text| content| here| output)\b)",
            re.IGNORECASE,
        ),
    ),
]
_SOFT_PLACEHOLDER_LABELS = frozenset({"todo:", "fixme:"})

_PLANNING_ARTIFACT_BLOCK_RE = re.compile(
    r"(?is)\[PLANNING_ARTIFACT\](.*?)\[/PLANNING_ARTIFACT\]"
)
_PLANNING_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")
_EXECUTION_INLINE_CODE_RE = re.compile(r"`([^`\n]{2,240})`")
_EXECUTION_LINE_COMMAND_RE = re.compile(
    r"(?im)^\s*(pytest|python|py|pip|npm|node|pnpm|yarn|uv|npx|tox|coverage|playwright|git|cargo|go|dotnet|mvn|gradle|make|pwsh|powershell|bash|sh|cmd)\b.+$"
)
_NON_COMMAND_INLINE_PREFIXES = (
    "from ",
    "import ",
    "def ",
    "class ",
    "phase_id:",
    "status:",
    "objective:",
    "summary:",
)
_POWERSHELL_COMMAND_STARTERS = {
    "powershell",
    "pwsh",
    "get-childitem",
    "get-content",
    "write-output",
    "set-location",
    "copy-item",
    "move-item",
    "remove-item",
    "new-item",
}


class AITeamOrchestrator:
    def __init__(
        self,
        router: HybridRouter,
        runtime_dir: Path,
        project_root: Path | None = None,
        additional_workspace_roots: list[Path] | None = None,
        browser_mode: str = "basic",
        environment: str = "dev",
    ) -> None:
        self.router = router
        self.runtime_dir = runtime_dir
        self.router.runtime_dir = runtime_dir
        self._sqlite_store = SqliteStore(runtime_dir / "aiteam.db")
        self.taskboard = TaskBoard.from_runtime_dir(runtime_dir)
        self.mailbox = Mailbox(runtime_dir / "mailbox.jsonl")
        self.sandboxes = SandboxManager(runtime_dir / "sandboxes")
        self.event_logger = EventLogger(runtime_dir)
        self.project_root = (project_root or runtime_dir.parent).resolve()
        self.context_curator = ContextCuratorStore(runtime_dir)
        self.additional_workspace_roots = [
            path.resolve() for path in (additional_workspace_roots or [])
        ]
        self.environment = environment
        self.compliance = ComplianceGuard(
            policy=CompliancePolicy(environment=environment)
        )
        self.memory = AgentMemoryStore(runtime_dir / "memory")
        self.thread_store = ThreadStore(runtime_dir)
        self.communicator = TeamCommunicator(
            mailbox=self.mailbox,
            memory=self.memory,
            event_logger=self.event_logger,
            runtime_dir=self.runtime_dir,
        )
        self.execution = ExecutionEngine(
            executor=LocalCommandExecutor(
                workspace_root=self.project_root,
                policy=CommandPolicy(),
                additional_roots=self.additional_workspace_roots,
            ),
            browser=(
                PlaywrightBrowserController()
                if browser_mode.strip().lower() == "playwright"
                else BrowserController()
            ),
            event_logger=self.event_logger,
        )
        self.tool_integrator = AutoToolIntegrator(
            runtime_dir=self.runtime_dir,
            project_root=self.project_root,
        )
        self.tool_integrator.sync_skill_library(force=False)
        self._round = 0
        self._last_event_meeting_round: dict[str, int] = {}
        self.agent_pools = self._build_agent_pools()
        self._role_assignment_cursor: dict[Role, int] = {}
        self._assignment_lock = threading.RLock()
        self._execution_order_lock = threading.RLock()
        self._execution_order = 0
        self._agent_latency_ewma_ms: dict[str, float] = {}
        self._agent_failure_penalty: dict[str, int] = {}
        # Specialization routing: (agent_id, task_type) -> (successes, total)
        self._agent_specialization: dict[tuple[str, str], tuple[int, int]] = {}
        self.max_parallel_tasks = self._resolve_max_parallel_tasks()
        self._parallel_min_tasks = self._resolve_parallel_min_tasks()
        self._parallel_autotune_enabled = self._resolve_parallel_autotune_enabled()
        self._parallel_target_latency_ms = self._resolve_parallel_target_latency_ms()
        self._parallel_max_failure_rate = self._resolve_parallel_max_failure_rate()
        self._dynamic_parallel_tasks = self.max_parallel_tasks
        self.workflow_state: dict[str, dict] = {}
        self._load_workflow_state()
        self.session_store = SessionStore(runtime_dir)
        self.token_chunk_callback: "Callable[[str, str], None] | None" = None
        self.agent_event_callback: "Callable[[dict], None] | None" = None
        self._init_tool_dispatcher()

    def _ensure_tool_specialist_metadata(self, task: WorkTask) -> None:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        self._apply_tool_rewiring_hints(task)
        explicit_name = str(metadata.get("tool_specialist", "") or "").strip().lower()
        required_caps = metadata.get("required_capabilities", [])
        specialist_name = infer_tool_specialist(
            role=task.role,
            required_capabilities=required_caps,
            metadata=metadata,
        )
        if not specialist_name:
            return
        profile = specialist_profile(specialist_name)
        if profile is None:
            return
        metadata.setdefault("tool_specialist", profile.name)
        metadata.setdefault("tool_specialist_label", profile.label)
        metadata.setdefault("tool_specialist_contract_version", "tool_specialist_v1")
        metadata.setdefault("tool_specialist_tool_families", list(profile.tool_families))
        metadata.setdefault(
            "tool_specialist_preferred_capabilities",
            sorted(
                {
                    *normalize_tool_capabilities(required_caps or profile.preferred_capabilities),
                    *derive_target_capabilities(
                        skill_targets=metadata.get("skill_targets", []),
                        lsp_targets=metadata.get("lsp_targets", []),
                    ),
                }
            ),
        )
        metadata.setdefault("tool_specialist_default_tier", profile.default_tier)
        metadata.setdefault("tool_specialist_decision_scope", "operate_tools_and_report_only")
        metadata.setdefault("tool_specialist_economic_routing", True)
        metadata.setdefault(
            "tool_specialist_skill_targets",
            normalize_skill_targets(metadata.get("skill_targets", [])),
        )
        metadata.setdefault(
            "tool_specialist_lsp_targets",
            normalize_lsp_targets(metadata.get("lsp_targets", [])),
        )
        if "tool_specialist_reason" not in metadata:
            metadata["tool_specialist_reason"] = (
                "especializacion inferida por capacidades de tools requeridas"
            )
        if "tool_specialist_inferred" not in metadata:
            metadata["tool_specialist_inferred"] = not bool(explicit_name)

    def _apply_tool_rewiring_hints(self, task: WorkTask) -> None:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if self._to_bool(metadata.get("_tool_rewiring_evaluated", False)):
            return
        metadata["_tool_rewiring_evaluated"] = True
        required_caps = {
            str(item or "").strip().lower()
            for item in list(metadata.get("required_capabilities", []) or [])
            if str(item or "").strip()
        }
        if not required_caps:
            return
        try:
            suggestions = self.tool_integrator.suggest_requirements(required_caps, limit=3)
        except Exception:
            return
        if not suggestions:
            return
        rewired = [item for item in suggestions if str(item.get("replacement_for", "") or "").strip()]
        if not rewired:
            return
        candidate_names = [
            str(item.get("name", "") or "").strip().lower()
            for item in rewired
            if str(item.get("name", "") or "").strip()
        ]
        replaced_names = [
            str(item.get("replacement_for", "") or "").strip().lower()
            for item in rewired
            if str(item.get("replacement_for", "") or "").strip()
        ]
        if not candidate_names:
            return
        metadata["tool_rewiring_candidates"] = candidate_names
        metadata["tool_rewiring_replacement_for"] = replaced_names
        metadata["tool_rewiring_active"] = True
        metadata["tool_rewiring_suppress_mcp_operator"] = True
        metadata["tool_rewiring_preferred_specialist"] = self._preferred_specialist_for_replacements(candidate_names)
        metadata["tool_rewiring_reason"] = (
            "catalog_replacement_candidates_preferred_over_replaceable_mcp"
        )
        self.event_logger.emit(
            "tool_rewiring_applied",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "replacement_for": replaced_names,
                "candidates": candidate_names,
                "preferred_specialist": str(
                    metadata.get("tool_rewiring_preferred_specialist", "") or ""
                ).strip(),
                "reason": str(metadata.get("tool_rewiring_reason", "") or "").strip(),
            },
        )

    @staticmethod
    def _resolve_task_preferred_tool_tier(task: WorkTask) -> str:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        roster_meta = metadata.get("specialist_roster_applied", {})
        roster_tier = ""
        if isinstance(roster_meta, dict):
            roster_tier = str(
                roster_meta.get("specialist_roster_preferred_tool_tier", "") or ""
            ).strip()
        if not roster_tier:
            roster_tier = str(
                metadata.get("specialist_roster_preferred_tool_tier", "") or ""
            ).strip()
        if roster_tier:
            return roster_tier
        return str(metadata.get("tool_specialist_default_tier", "") or "").strip()

    @staticmethod
    def _preferred_specialist_for_replacements(candidates: list[str]) -> str:
        normalized = [str(item or "").strip().lower() for item in candidates if str(item or "").strip()]
        if any(name.endswith("_skill") for name in normalized):
            return "skill_worker"
        if any(any(token in name for token in ("playwright", "browser", "puppeteer")) for name in normalized):
            return "browser_operator"
        if any(any(token in name for token in ("test", "pytest", "jest", "vitest")) for name in normalized):
            return "test_runner"
        if any(any(token in name for token in ("lsp", "symbol", "reference")) for name in normalized):
            return "lsp_navigator"
        if any(any(token in name for token in ("repo", "git")) for name in normalized):
            return "repo_scout"
        return ""

    @staticmethod
    def _is_best_effort_specialist(specialist_name: str) -> bool:
        normalized = str(specialist_name or "").strip().lower()
        return normalized == "context_curator"

    def _invoke_specialist_prefetch(
        self,
        *,
        request: RoutingRequest,
        prompt: str,
        task_id: str,
        messages: list[dict[str, str]],
        specialist_name: str,
    ) -> tuple[RoutingDecision, int]:
        max_attempts = 2
        last_decision: RoutingDecision | None = None
        for attempt in range(1, max_attempts + 1):
            decision = self.router.route_and_invoke(
                request=request,
                prompt=prompt,
                task_id=task_id,
                messages=messages,
                tools=None,
            )
            last_decision = decision
            if decision.success:
                return decision, attempt
            if decision.reason != "no_eligible_adapter" or attempt >= max_attempts:
                return decision, attempt
            self.event_logger.emit(
                "specialist_prefetch_retry",
                {
                    "task_id": task_id.split("::prefetch::", 1)[0],
                    "specialist": specialist_name,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "reason": decision.reason,
                },
            )
        assert last_decision is not None
        return last_decision, max_attempts

    def _is_specialist_prefetch_delegate_task(self, task: WorkTask) -> bool:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if "::prefetch::" in str(task.task_id or ""):
            return True
        if self._to_bool(metadata.get("structured_evidence_task", False)):
            return True
        phase = str(metadata.get("phase", "") or "").strip().lower()
        if phase.startswith("delegate_") or phase.startswith("delegated_"):
            return True
        return False

    def _collect_specialist_prefetch_context(self, task: WorkTask) -> str:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if self._to_bool(metadata.get("skip_specialist_prefetch", False)):
            return ""
        if self._to_bool(metadata.get("_specialist_prefetch_done", False)):
            stored = metadata.get("specialist_prefetch_context", "")
            return str(stored or "")
        if self._is_specialist_prefetch_delegate_task(task):
            metadata["_specialist_prefetch_done"] = True
            return ""

        required_caps = normalize_tool_capabilities(metadata.get("required_capabilities", []))
        skill_targets = normalize_skill_targets(metadata.get("skill_targets", []))
        lsp_targets = normalize_lsp_targets(metadata.get("lsp_targets", []))
        explicit_specialist_roster = bool(
            [
                item
                for item in list(metadata.get("specialist_roster", []) or [])
                if str(item).strip()
            ]
        )
        phase_name = self._phase_name_for_task(task).lower()
        specialist_capabilities = {
            "test_execute",
            "build_execute",
            "browser_nav",
            "browser_test",
            "external_mcp",
            "skill_run",
            "lsp_symbols",
            "lsp_references",
        }
        specialist_prefetch_implied = bool(
            specialist_capabilities & set(required_caps)
            or skill_targets
            or lsp_targets
            or explicit_specialist_roster
            or self._to_bool(metadata.get("context_curator_requested", False))
            or self._to_bool(metadata.get("context_curator_recommended", False))
            or self._to_bool(metadata.get("context_pressure_high", False))
            or (
                self._to_bool(metadata.get("continuation_requested", False))
                and task.role == Role.TEAM_LEAD
            )
        )
        if (
            task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}
            and not phase_name.startswith("plan_")
            and not self._to_bool(metadata.get("require_specialist_prefetch", False))
            and not specialist_prefetch_implied
        ):
            metadata["_specialist_prefetch_done"] = True
            self._record_specialist_quorum_result(
                task=task,
                specialists=[],
                quorum_required=0,
                quorum_mode="any",
                reports=[],
            )
            return ""
        task_root = self._task_root(task.task_id)
        pressure = self._refresh_context_pressure(task_root, metadata=metadata)
        wants_context_curator = (
            self._to_bool(metadata.get("context_curator_requested", False))
            or self._to_bool(metadata.get("context_curator_recommended", False))
            or self._to_bool(metadata.get("context_pressure_high", False))
            or (
                self._to_bool(metadata.get("continuation_requested", False))
                and task.role == Role.TEAM_LEAD
            )
        )
        if (
            phase_name.startswith("plan_")
            and not self._to_bool(metadata.get("require_specialist_prefetch", False))
            and not skill_targets
            and not lsp_targets
            and not explicit_specialist_roster
            and not wants_context_curator
        ):
            metadata["_specialist_prefetch_done"] = True
            self._record_specialist_quorum_result(
                task=task,
                specialists=[],
                quorum_required=0,
                quorum_mode="any",
                reports=[],
            )
            return ""
        if not required_caps and not skill_targets and not lsp_targets and not wants_context_curator:
            metadata["_specialist_prefetch_done"] = True
            self._record_specialist_quorum_result(
                task=task,
                specialists=[],
                quorum_required=0,
                quorum_mode="any",
                reports=[],
            )
            return ""

        available_mcp = None
        if self.mcp_manager is not None:
            try:
                available_mcp = self.mcp_manager.list_healthy()
            except Exception:
                available_mcp = None

        roster = select_specialists_for_task(
            role=task.role,
            required_capabilities=required_caps,
            complexity=task.complexity,
            criticality=task.criticality,
            skill_targets=skill_targets,
            lsp_targets=lsp_targets,
            available_mcp_servers=available_mcp,
            metadata=metadata,
        )
        metadata["context_pressure"] = pressure
        if roster.is_empty():
            metadata["_specialist_prefetch_done"] = True
            self._record_specialist_quorum_result(
                task=task,
                specialists=[],
                quorum_required=0,
                quorum_mode="any",
                reports=[],
            )
            return ""

        metadata["specialist_roster_applied"] = roster.to_metadata()
        reports: list[dict[str, Any]] = []
        effective_specialists = list(roster.specialists)
        degraded_specialists: list[dict[str, Any]] = []
        briefing_lines = [
            "Informes compactos de especialistas delegados antes de ejecutar la tarea principal:",
        ]

        for specialist_name in roster.specialists:
            profile = specialist_profile(specialist_name)
            if profile is None:
                continue
            specialist_metadata = build_tool_specialist_metadata(
                specialist=specialist_name,
                required_capabilities=required_caps,
                reason=f"prefetch para {task.task_id}",
                skill_targets=skill_targets,
                lsp_targets=lsp_targets,
            )
            # Best-effort specialists (e.g. context_curator) must not be
            # restricted to API-channel economy adapters: when all API adapters
            # are down (429/unavailable), the prefetch silently degrades and the
            # main task loses its context briefing.  For best-effort specialists
            # we drop the economic routing preference so the router can fall
            # through to subscription-channel adapters.
            _prefetch_best_effort = self._is_best_effort_specialist(specialist_name)
            specialist_request = RoutingRequest(
                role=profile.owner_role,
                complexity=task.complexity,
                criticality=task.criticality,
                required_capabilities=set(required_caps),
                tool_specialist=specialist_name,
                tool_rewiring_preferred_specialist=str(
                    metadata.get("tool_rewiring_preferred_specialist", "") or ""
                ).strip(),
                prefer_economic_routing=not _prefetch_best_effort,
                preferred_tool_tier="" if _prefetch_best_effort else profile.default_tier,
                skill_targets=set(skill_targets),
                lsp_targets=set(lsp_targets),
                approved_adapters=self.compliance.approved_adapters(metadata),
                excluded_adapters=set(),
                sensitive_approval=self.compliance.evaluate_sensitive_approval(task.metadata)[0],
                environment=self.environment,
            )
            specialist_prompt = (
                f"Specialist precheck: {profile.label} ({specialist_name}).\n"
                f"Especializacion activa: {profile.label} ({specialist_name}).\n"
                f"Tarea principal: {task.title}\n"
                f"Descripcion: {task.description}\n"
                "Devuelve solo un informe operativo compacto y grounded para apoyar a la "
                "tarea principal. No hables por otros specialists ni cambies de especialidad."
            )
            specialist_messages = [
                {
                    "role": "system",
                    "content": (
                        f"Eres {profile.label}. Especialista activo: {specialist_name}.\n"
                        f"Especializacion activa: {profile.label} ({specialist_name}).\n"
                        f"Familias: {', '.join(list(specialist_metadata.get('tool_specialist_tool_families', []) or [])) or 'unknown'}.\n"
                        f"Capacidades preferentes: {', '.join(list(specialist_metadata.get('tool_specialist_preferred_capabilities', []) or [])) or 'unknown'}.\n"
                        "Devuelve preferentemente JSON con schema "
                        '{"summary":"","evidence":[],"artifacts":[],"risks":[],"recommendation":"","confidence":0.0}.\n'
                        "No menciones ni asumas otros specialists salvo que aparezcan como evidencia real."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Specialist objetivo: {specialist_name}.\n"
                        f"Analiza la tarea principal '{task.title}'.\n"
                        f"Descripcion:\n{task.description}\n\n"
                        "Entrega un informe compacto con summary, evidence, artifacts, risks y recommendation."
                    ),
                },
            ]
            specialist_task_id = f"{task.task_id}::prefetch::{specialist_name}"
            decision, attempts_used = self._invoke_specialist_prefetch(
                request=specialist_request,
                prompt=specialist_prompt,
                task_id=specialist_task_id,
                messages=specialist_messages,
                specialist_name=specialist_name,
            )
            if not decision.success:
                if (
                    self._is_best_effort_specialist(specialist_name)
                    and decision.reason == "no_eligible_adapter"
                ):
                    effective_specialists = [
                        item for item in effective_specialists if item != specialist_name
                    ]
                    degraded_specialists.append(
                        {
                            "specialist": specialist_name,
                            "reason": decision.reason,
                            "attempts": attempts_used,
                        }
                    )
                    self.event_logger.emit(
                        "specialist_prefetch_degraded",
                        {
                            "task_id": task.task_id,
                            "specialist": specialist_name,
                            "reason": decision.reason,
                            "attempts": attempts_used,
                        },
                    )
                    continue
                self.event_logger.emit(
                    "specialist_prefetch_failed",
                    {
                        "task_id": task.task_id,
                        "specialist": specialist_name,
                        "provider": decision.provider,
                        "model": decision.model,
                        "reason": decision.reason,
                    },
                )
                briefing_lines.append(
                    f"- {specialist_name}: fallo al recopilar informe ({decision.reason or 'unknown'})."
                )
                continue

            parsed = parse_specialist_report(
                decision.response.content,
                specialist=specialist_name,
                provider=decision.provider,
                model=decision.model,
                toolset_used=list(specialist_metadata.get("tool_specialist_tool_families", []) or []),
                tokens_used=decision.response.input_tokens + decision.response.output_tokens,
            )
            report_meta = parsed.to_metadata()
            report_meta["source"] = "prefetch"
            reports.append(report_meta)
            briefing_lines.append(
                (
                    f"- {specialist_name} ({decision.provider}/{decision.model}): "
                    f"{self._compact_text(parsed.summary, 220)}"
                )
            )
            if parsed.recommendation:
                briefing_lines.append(
                    f"  recomendacion: {self._compact_text(parsed.recommendation, 180)}"
                )
            self.event_logger.emit(
                "specialist_prefetch_completed",
                {
                    "task_id": task.task_id,
                    "specialist": specialist_name,
                    "provider": decision.provider,
                    "model": decision.model,
                    "summary": self._compact_text(parsed.summary, 180),
                },
            )

        metadata["_specialist_prefetch_done"] = True
        metadata["specialist_prefetch_reports"] = reports
        if degraded_specialists:
            metadata["specialist_prefetch_degraded"] = degraded_specialists
        else:
            metadata.pop("specialist_prefetch_degraded", None)
        existing_reports = list(metadata.get("specialist_reports", []) or [])
        for report in reports:
            if not isinstance(report, dict):
                continue
            existing_reports.append(report)
        metadata["specialist_reports"] = existing_reports
        self._record_specialist_quorum_result(
            task=task,
            specialists=effective_specialists,
            quorum_required=min(int(roster.quorum_required), len(effective_specialists)),
            quorum_mode=str(roster.quorum_mode or "any"),
            reports=reports,
        )
        context = "\n".join(briefing_lines) if len(briefing_lines) > 1 else ""
        metadata["specialist_prefetch_context"] = context
        return context

    def _record_specialist_quorum_result(
        self,
        task: WorkTask,
        *,
        specialists: list[str],
        quorum_required: int,
        quorum_mode: str,
        reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        normalized_specialists = [
            str(item).strip().lower() for item in specialists if str(item).strip()
        ]
        valid_received_set: set[str] = set()
        invalid_specialists: list[str] = []
        for item in list(reports or []):
            if not isinstance(item, dict):
                continue
            specialist_name = str(item.get("specialist", "") or "").strip().lower()
            if not specialist_name:
                continue
            if self._report_counts_for_specialist_quorum(item):
                valid_received_set.add(specialist_name)
            else:
                invalid_specialists.append(specialist_name)
        received_specialists = [
            specialist for specialist in normalized_specialists if specialist in valid_received_set
        ]
        missing_specialists = [
            specialist for specialist in normalized_specialists if specialist not in valid_received_set
        ]
        responses_required = max(0, int(quorum_required))
        responses_received = len(received_specialists)
        quorum_met = responses_received >= responses_required
        result = {
            "specialists": normalized_specialists,
            "quorum_mode": str(quorum_mode or "any").strip().lower() or "any",
            "responses_required": responses_required,
            "responses_received": responses_received,
            "received_specialists": received_specialists,
            "missing_specialists": missing_specialists,
            "invalid_specialists": list(dict.fromkeys(invalid_specialists)),
            "quorum_met": quorum_met,
        }
        metadata["specialist_quorum_result"] = result
        if missing_specialists and quorum_met:
            metadata["specialist_quorum_warning"] = (
                "quorum_met_with_partial_specialist_coverage"
            )
        else:
            metadata.pop("specialist_quorum_warning", None)
        self.event_logger.emit(
            "specialist_quorum_result",
            {
                "task_id": task.task_id,
                "quorum_mode": result["quorum_mode"],
                "quorum_met": quorum_met,
                "responses_received": responses_received,
                "responses_required": responses_required,
                "received_specialists": received_specialists,
                "missing_specialists": missing_specialists,
                "invalid_specialists": result["invalid_specialists"],
            },
        )
        return result

    @staticmethod
    def _report_counts_for_specialist_quorum(report: dict[str, Any]) -> bool:
        validation_status = str(report.get("validation_status", "valid") or "valid").strip().lower()
        if validation_status != "valid":
            return False
        summary = str(report.get("summary", "") or "").strip()
        evidence = [
            str(item).strip()
            for item in list(report.get("evidence", []) or [])
            if str(item).strip()
        ]
        artifacts = [
            str(item).strip()
            for item in list(report.get("artifacts", []) or [])
            if str(item).strip()
        ]
        risks = [
            str(item).strip()
            for item in list(report.get("risks", []) or [])
            if str(item).strip()
        ]
        recommendation = str(report.get("recommendation", "") or "").strip()
        if evidence or artifacts or risks or recommendation:
            return True
        return len(summary) >= 40

    def _route_and_invoke_with_compat(self, **kwargs) -> RoutingDecision:
        """Allow older tests/mocks that do not yet accept `messages_resolver`."""
        try:
            return self.router.route_and_invoke(**kwargs)
        except TypeError as exc:
            if "messages_resolver" not in str(exc or ""):
                raise
            fallback_kwargs = dict(kwargs)
            fallback_kwargs.pop("messages_resolver", None)
            return self.router.route_and_invoke(**fallback_kwargs)

    @staticmethod
    def _routing_capabilities_for_task(task: WorkTask) -> set[str]:
        raw_capabilities = {
            str(item).strip()
            for item in list(task.metadata.get("required_capabilities", []) or [])
            if str(item).strip()
        }
        run_profile = str(task.metadata.get("run_profile", "") or "").strip().lower()
        if run_profile not in {"solo_lead", "direct"} and not bool(
            task.metadata.get("direct_coding_executor", False)
        ):
            return raw_capabilities
        # En perfil directo, repo/test/build access son capacidades del runtime
        # del workspace, no del modelo LLM. Filtrarlas aqui evita no_eligible_adapter
        # durante reparaciones antiguas que arrastran metadata de runtime.
        model_level_capabilities = {
            "analysis",
            "coding",
            "multimodal",
            "reasoning",
            "review",
            "summarization",
            "thinking",
            "tool_calling",
            "vision",
        }
        return {
            capability
            for capability in raw_capabilities
            if capability.lower() in model_level_capabilities
        }

    @staticmethod
    def _direct_profile_should_suppress_midrun_clarify(
        task: WorkTask,
        question: str = "",
    ) -> bool:
        run_profile = str(task.metadata.get("run_profile", "") or "").strip().lower()
        if run_profile not in {"solo_lead", "direct"} and not bool(
            task.metadata.get("direct_coding_executor", False)
        ):
            return False
        phase_name = str(task.metadata.get("phase", "") or "").strip().lower()
        normalized_question = str(question or "").strip().lower()
        safety_markers = (
            "api key",
            "borrar",
            "credential",
            "delete",
            "destruct",
            "eliminar",
            "extern",
            "network",
            "pago",
            "payment",
            "permiso",
            "prod",
            "production",
            "secret",
        )
        if phase_name in {"build", "lead_close"} and not any(
            marker in normalized_question for marker in safety_markers
        ):
            return True
        if phase_name == "build" and (
            bool(task.metadata.get("repair_first_required", False))
            or bool(task.metadata.get("direct_coding_executor", False))
        ):
            return True
        if phase_name == "lead_close" and bool(
            task.metadata.get("post_build_repair_pending", False)
        ):
            return True
        return False

    @staticmethod
    def _specialist_report_fingerprint(report: dict[str, Any]) -> tuple[object, ...]:
        return (
            str(report.get("specialist", "") or "").strip().lower(),
            str(report.get("summary", "") or "").strip(),
            tuple(str(item).strip() for item in list(report.get("evidence", []) or [])),
            tuple(str(item).strip() for item in list(report.get("artifacts", []) or [])),
            tuple(str(item).strip() for item in list(report.get("risks", []) or [])),
            str(report.get("recommendation", "") or "").strip(),
        )

    def _append_specialist_report_once(
        self,
        *,
        task: WorkTask,
        report: dict[str, Any],
    ) -> bool:
        reports = list(task.metadata.get("specialist_reports", []) or [])
        fingerprint = self._specialist_report_fingerprint(report)
        for existing in reports:
            if not isinstance(existing, dict):
                continue
            if self._specialist_report_fingerprint(existing) == fingerprint:
                return False
        reports.append(report)
        task.metadata["specialist_reports"] = reports
        return True

    def _init_tool_dispatcher(self) -> None:
        catalog_path = self.project_root / "config" / "tool_sources.catalog.json"
        try:
            from aiteam.tool_dispatch import ToolDispatcher

            self.tool_dispatcher = ToolDispatcher(
                catalog_path=catalog_path,
                runtime_dir=self.runtime_dir,
                environment=self.environment,
            )
        except Exception:
            self.tool_dispatcher = None

        # Initialize MCP server manager: sync catalog → mcp_servers.json
        try:
            from aiteam.mcp_manager import MCPServerManager

            self.mcp_manager = MCPServerManager(
                runtime_dir=self.runtime_dir,
                catalog_path=catalog_path,
                environment=self.environment,
            )
            synced = self.mcp_manager.sync_from_catalog()
            if synced > 0:
                self.event_logger.emit("mcp_catalog_synced", {"new_servers": synced})
            bootstrapped = self.mcp_manager.bootstrap_from_opencode()
            if bootstrapped > 0:
                self.event_logger.emit(
                    "mcp_opencode_bootstrapped",
                    {"new_servers": bootstrapped},
                )
        except Exception:
            self.mcp_manager = None

        # Compartir la misma instancia MCPServerManager con tool_dispatcher para
        # que build_tool_context_for_agent vea los mismos servidores arrancados
        # sin crear un segundo proceso independiente.
        if self.tool_dispatcher is not None and self.mcp_manager is not None:
            self.tool_dispatcher._mcp_manager = self.mcp_manager

        # Inyectar workspace en filesystem_mcp fuera del try exterior para que
        # un fallo aqui no anule mcp_manager. El arranque del servidor se
        # difiere al momento en que el Engineer construye su contexto de herramientas.
        self._inject_filesystem_mcp_workspace()

    def _inject_filesystem_mcp_workspace(self) -> None:
        """Inyecta el workspace del proyecto en los args de filesystem_mcp.

        @modelcontextprotocol/server-filesystem requiere rutas permitidas como
        args adicionales:
          npx -y @modelcontextprotocol/server-filesystem /ruta/workspace

        Sin este argumento el servidor arranca sin acceso a ningun directorio.
        El arranque real del servidor se hace de forma lazy en
        build_tool_context_for_agent (justo antes de que el Engineer necesite
        las herramientas), evitando bloquear el init del orchestrator y
        spawning de procesos multiples por request.
        """
        if self.mcp_manager is None:
            return
        config = self.mcp_manager._configs.get("filesystem_mcp")
        if config is None or not config.enabled:
            return

        workspace = str(self.runtime_dir.parent.resolve())

        def _norm(p: str) -> str:
            return str(p).replace("\\", "/").rstrip("/").lower()

        existing = [_norm(a) for a in config.args]
        if _norm(workspace) in existing:
            return  # ya configurado, nada que hacer

        config.args = list(config.args) + [workspace]
        try:
            self.mcp_manager._save_configs()
            self.event_logger.emit(
                "filesystem_mcp_workspace_injected",
                {"workspace": workspace, "args": config.args},
            )
        except Exception as exc:
            self.event_logger.emit(
                "filesystem_mcp_workspace_inject_error", {"error": str(exc)}
            )

    # ── Fix B: extraccion de bloques de codigo con path anotado ────

    _CODE_BLOCK_RE = re.compile(
        # Acepta:  ```python path=foo   ```lang path=foo   ``` path=foo   ```path=foo
        # (?:\w+\s+|\s*) = (language + space) OR (optional space, no language)
        # Esto evita que (?:\w+)? consuma "path" como si fuera el lenguaje.
        r"^\s*```(?:\w+\s+|\s*)path=[\"']?([^\"'\n\s`]+)[\"']?[^\n]*$",
        re.MULTILINE,
    )
    _STANDALONE_PATH_RE = re.compile(
        r"^\s*(?:[-*]\s*)?path=[\"']?([^\"'\n\s`]+)[\"']?\s*$",
        re.IGNORECASE,
    )
    _GENERIC_CODE_BLOCK_OPEN_RE = re.compile(r"^\s*```[^\n`]*$")
    _CODE_BLOCK_CLOSE_RE = re.compile(r"^\s*```\s*$")
    _MAX_CODE_FILES_PER_TASK = 10
    _MAX_CODE_FILE_BYTES = 512 * 1024  # 512 KB por archivo

    def _iter_path_code_blocks(self, content: str) -> list[tuple[str, str]]:
        lines = str(content or "").splitlines(keepends=True)
        blocks: list[tuple[str, str]] = []
        index = 0
        pending_path: str | None = None
        while index < len(lines):
            line = lines[index].rstrip("\r\n")
            standalone_path = self._STANDALONE_PATH_RE.match(line)
            if standalone_path is not None:
                pending_path = standalone_path.group(1).strip()
                index += 1
                continue

            opener = self._CODE_BLOCK_RE.match(line)
            if opener is None and pending_path:
                opener = self._GENERIC_CODE_BLOCK_OPEN_RE.match(line)
            if opener is None:
                if line.strip():
                    pending_path = None
                index += 1
                continue
            raw_path = (
                opener.group(1).strip()
                if self._CODE_BLOCK_RE.match(line)
                else str(pending_path or "").strip()
            )
            pending_path = None
            if not raw_path:
                index += 1
                continue
            index += 1
            block_lines: list[str] = []
            while index < len(lines):
                line = lines[index]
                if self._CODE_BLOCK_CLOSE_RE.match(line.rstrip("\r\n")):
                    break
                block_lines.append(line)
                index += 1
            if index < len(lines):
                blocks.append((raw_path, "".join(block_lines)))
                index += 1
            else:
                self.event_logger.emit(
                    "code_block_write_skipped",
                    {"path": raw_path, "reason": "unterminated_code_block"},
                )
        return blocks

    def _extract_and_write_code_blocks(self, task: "WorkTask", content: str) -> int:
        """Extrae bloques ```lang path=archivo y los escribe al workspace.

        Fallback de escritura cuando filesystem_mcp no esta disponible.
        El Engineer incluye el contenido completo de cada archivo usando
        la anotacion path= en el fence del bloque de codigo.

        Devuelve el numero de archivos escritos correctamente.
        """
        blocks = self._iter_path_code_blocks(content)
        if not blocks:
            return 0

        workspace = self.execution.executor.workspace_root
        written = 0

        for raw_path, file_content in blocks[: self._MAX_CODE_FILES_PER_TASK]:
            # Seguridad: solo paths relativos sin traversal
            try:
                rel = Path(raw_path)
                if rel.is_absolute() or ".." in rel.parts:
                    self.event_logger.emit(
                        "code_block_write_skipped",
                        {"task_id": task.task_id, "path": raw_path, "reason": "unsafe_path"},
                    )
                    continue
                target = (workspace / rel).resolve()
                # Asegurar que el target esta dentro del workspace
                target.relative_to(workspace.resolve())
            except (ValueError, TypeError):
                continue

            if len(file_content.encode("utf-8")) > self._MAX_CODE_FILE_BYTES:
                self.event_logger.emit(
                    "code_block_write_skipped",
                    {"task_id": task.task_id, "path": raw_path, "reason": "file_too_large"},
                )
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                existed = target.exists()
                target.write_text(file_content, encoding="utf-8")
                artifact_paths = [
                    str(item).strip()
                    for item in list(task.metadata.get("artifact_paths", []) or [])
                    if str(item).strip()
                ]
                rel_str = str(rel).replace("\\", "/")
                if rel_str not in artifact_paths:
                    artifact_paths.append(rel_str)
                task.metadata["artifact_paths"] = artifact_paths
                if existed:
                    task.metadata["artifact_modified_count"] = int(
                        task.metadata.get("artifact_modified_count", 0) or 0
                    ) + 1
                else:
                    task.metadata["artifact_created_count"] = int(
                        task.metadata.get("artifact_created_count", 0) or 0
                    ) + 1

                event_name = "artifact_modified" if existed else "artifact_created"
                self.event_logger.emit(
                    event_name,
                    {
                        "task_id": task.task_id,
                        "path": str(rel),
                        "bytes": len(file_content.encode("utf-8")),
                        "created": 0 if existed else 1,
                        "modified": 1 if existed else 0,
                    },
                )
                self.event_logger.emit(
                    "execution_step",
                    {
                        "task_id": task.task_id,
                        "success": True,
                        "step_type": "write_file",
                        "command": f"write:{rel}",
                        "exit_code": 0,
                        "reason": "code_block_extraction",
                    },
                )
                written += 1
            except Exception as exc:
                self.event_logger.emit(
                    "code_block_write_error",
                    {"task_id": task.task_id, "path": raw_path, "error": str(exc)},
                )

        if written > 0:
            self.event_logger.emit(
                "code_blocks_written",
                {"task_id": task.task_id, "files_written": written, "workspace": str(workspace)},
            )

        return written

    # ── Post-write validation (solo_lead / direct_coding_executor) ──

    def _solo_lead_post_write_validation(self, task: "WorkTask") -> str:
        """Valida sintaxis de los .py escritos por _extract_and_write_code_blocks.

        Usa ast.parse() — no crea .pyc ni toca el filesystem.
        Devuelve el primer error encontrado como string, o "" si todo es valido.
        Solo se invoca cuando direct_coding_executor=True (perfil solo_lead).
        """
        import ast

        artifact_paths = [
            str(p).strip()
            for p in list(task.metadata.get("artifact_paths", []) or [])
            if str(p).strip().endswith(".py")
        ]
        if not artifact_paths:
            return ""

        # _extract_and_write_code_blocks siempre escribe en execution.executor.workspace_root.
        ws_root = self.execution.executor.workspace_root.resolve()

        for rel_path in artifact_paths:
            abs_path = (ws_root / rel_path).resolve()
            if not abs_path.exists():
                continue
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
                ast.parse(source, filename=rel_path)
            except SyntaxError as exc:
                short = f"line {exc.lineno}: {exc.msg}"
                self.event_logger.emit(
                    "solo_lead_post_write_syntax_error",
                    {"task_id": task.task_id, "file": rel_path, "error": short},
                )
                return f"SyntaxError in {rel_path}: {short}"
            except Exception as exc:
                self.event_logger.emit(
                    "solo_lead_post_write_compile_check_failed",
                    {"task_id": task.task_id, "file": rel_path, "error": str(exc)[:200]},
                )
        return ""

    def _build_solo_lead_workspace_context(self, task: "WorkTask") -> str:
        """Lee archivos relevantes del workspace para inyectarlos en el prompt del build.

        Solo se invoca para la fase build en perfil solo_lead (direct_coding_executor=True).
        Lee hasta _MAX_WORKSPACE_FILES archivos de allowed_module_path_hints, limitando
        cada uno a _MAX_FILE_LINES lineas para no saturar el contexto.
        """
        _MAX_WORKSPACE_FILES = 6
        _MAX_FILE_LINES = 120

        phase_contract = dict(task.metadata.get("phase_contract") or {})
        hints_raw = phase_contract.get("allowed_module_path_hints") or task.metadata.get(
            "allowed_module_path_hints"
        )
        if not hints_raw:
            return ""
        hints = [str(h).strip() for h in (hints_raw if isinstance(hints_raw, list) else [hints_raw]) if str(h).strip()]
        if not hints:
            return ""

        ws_root = self.execution.executor.workspace_root.resolve()
        blocks: list[str] = []
        for hint in hints[:_MAX_WORKSPACE_FILES]:
            abs_path = (ws_root / hint).resolve()
            if not abs_path.is_file():
                continue
            try:
                lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
                truncated = lines[:_MAX_FILE_LINES]
                suffix = f"\n... ({len(lines) - _MAX_FILE_LINES} lineas omitidas)" if len(lines) > _MAX_FILE_LINES else ""
                blocks.append(f"# {hint}\n```\n" + "\n".join(truncated) + "\n```" + suffix)
            except Exception:
                pass
        if not blocks:
            return ""
        return "== CONTENIDO ACTUAL DEL WORKSPACE ==\n" + "\n\n".join(blocks)

    def _run_solo_lead_pytest(self, task: "WorkTask", task_root: str) -> str:
        """Corre pytest en el workspace tras escribir archivos en perfil solo_lead.

        Captura stdout/stderr, limita a 60 lineas para no saturar el contexto.
        Guarda el resultado en workflow_state para que lead_close lo consuma.
        Devuelve el resultado resumido como string.
        """
        import subprocess

        _MAX_LINES = 60
        ws_root = self.execution.executor.workspace_root.resolve()
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", "--tb=short", "-q", "--no-header"],
                cwd=str(ws_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            combined = (proc.stdout or "") + (proc.stderr or "")
            lines = combined.splitlines()
            if len(lines) > _MAX_LINES:
                lines = lines[:_MAX_LINES] + [f"... ({len(lines) - _MAX_LINES} lineas omitidas)"]
            result = "\n".join(lines).strip()
            status = "passed" if proc.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            result = "pytest: timeout (>120s)"
            status = "timeout"
        except FileNotFoundError:
            result = "pytest: python no encontrado en PATH"
            status = "not_found"
        except Exception as exc:
            result = f"pytest: error al ejecutar — {str(exc)[:120]}"
            status = "error"

        summary = f"pytest_status={status}\n{result}"
        ws = self._get_workflow_state(task_root)
        ws["solo_lead_pytest_result"] = summary
        ws["solo_lead_pytest_status"] = status
        self._save_workflow_state(task_root)
        self.event_logger.emit(
            "solo_lead_pytest_run",
            {"task_id": task.task_id, "status": status, "lines": len(result.splitlines())},
        )
        return summary

    # ── Workflow State (shared blackboard) ──────────────────────────

    def _load_workflow_state(self) -> None:
        try:
            self.workflow_state = self._sqlite_store.load_workflow_state()
        except Exception:
            self.workflow_state = {}

    def _save_workflow_state(self, task_root: str | None = None) -> None:
        try:
            if task_root:
                payload = self.workflow_state.get(task_root, {})
                if isinstance(payload, dict):
                    self._sqlite_store.save_workflow_entry(task_root, payload)
            else:
                self._sqlite_store.save_workflow_state(self.workflow_state)
        except Exception as e:
            self.event_logger.emit("workflow_state_save_error", {"error": str(e)})

    @staticmethod
    def _task_root(task_id: str) -> str:
        """Extrae el root de un task_id (ej. 'ROOT::build' → 'ROOT')."""
        return task_id.split("::")[0] if "::" in task_id else task_id

    def _get_workflow_state(self, task_root: str) -> dict:
        if task_root not in self.workflow_state:
            self.workflow_state[task_root] = {
                "phase_outputs": {},
                "phase_verdicts": {},
                "facts": [],
                "ledger": [],
                "review_feedback": [],
            }
        return self.workflow_state[task_root]

    def _update_workflow_state(
        self,
        task_root: str,
        phase: str,
        output: str,
        facts: list[str] | None = None,
    ) -> None:
        ws = self._get_workflow_state(task_root)
        ws["phase_outputs"][phase] = output[:2000]
        phase_key = str(phase or "").strip().lower()
        phase_verdicts = ws.setdefault("phase_verdicts", {})
        verdict = extract_phase_verdict(output, phase_id=phase_key)
        if verdict:
            phase_verdicts[phase_key] = verdict
        else:
            phase_verdicts.pop(phase_key, None)
        if facts:
            ws["facts"].extend(facts[-5:])
            ws["facts"] = ws["facts"][-20:]
        self._update_context_curator_for_phase(
            task_root=task_root,
            phase=phase,
            output=output,
        )
        self._save_workflow_state(task_root)
        self.event_logger.emit(
            "workflow_state_updated",
            {"task_root": task_root, "phase": phase, "facts_count": len(ws["facts"])},
        )

    @staticmethod
    def _append_agent_output_history(
        task: WorkTask,
        output: str,
        *,
        limit: int = 5,
    ) -> None:
        text = str(output or "").strip()
        if not text:
            return
        history = [
            str(item).strip()
            for item in list(task.metadata.get("_agent_output_history", []) or [])
            if str(item).strip()
        ]
        if history and history[-1] == text:
            task.metadata["_agent_output_history"] = history[-limit:]
            return
        history.append(text)
        task.metadata["_agent_output_history"] = history[-limit:]

    def _update_context_curator_for_phase(
        self,
        *,
        task_root: str,
        phase: str,
        output: str,
    ) -> None:
        normalized_phase = str(phase or "").strip()
        text = str(output or "").strip()
        if not task_root.startswith("CHAT-") or not normalized_phase or not text:
            return
        ws = self._get_workflow_state(task_root)
        project_ctx, chat_ctx, summary = self.context_curator.remember_phase_summary(
            project_key=self._project_thread_key(),
            chat_root=task_root,
            phase=normalized_phase,
            output=text,
            source_task_ids=[f"{task_root}::{normalized_phase}"],
        )
        phase_summaries = dict(ws.get("phase_context_summaries", {}) or {})
        phase_summaries[normalized_phase] = summary
        ws["phase_context_summaries"] = phase_summaries
        ws["project_context_summary"] = self.context_curator.build_summary(project_ctx)
        ws["chat_context_summary"] = self.context_curator.build_summary(chat_ctx)
        self._refresh_context_pressure(task_root)
        self.event_logger.emit(
            "context_curator_phase_updated",
            {
                "task_root": task_root,
                "phase": normalized_phase,
                "summary_len": len(summary),
            },
        )

    def _compute_context_pressure(
        self,
        task_root: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ws = self._get_workflow_state(task_root)
        meta = metadata or {}
        continuation_requested = self._to_bool(
            meta.get("continuation_requested", ws.get("continuation_requested", False))
        )
        continuation_snapshot = str(
            meta.get("continuation_snapshot", ws.get("continuation_snapshot", "")) or ""
        ).strip()
        phase_summary_count = len(dict(ws.get("phase_context_summaries", {}) or {}))
        delegate_batch_count = len(list(ws.get("delegate_batches", []) or []))

        specialist_report_count = 0
        for entry in self.event_logger.recent_events(hours=24):
            event_type = str(entry.get("event_type", "") or "").strip().lower()
            if event_type not in {"specialist_report_parsed", "specialist_prefetch_completed"}:
                continue
            payload = entry.get("payload", {}) or {}
            event_task_id = str(payload.get("task_id", "") or "").strip()
            if event_task_id.startswith(task_root):
                specialist_report_count += 1

        chat_ctx = self.context_curator.load_chat_context(
            task_root,
            project_key=self._project_thread_key(),
        )
        invalidation_count = len(list(chat_ctx.get("invalidations", []) or []))
        open_question_count = len(list(chat_ctx.get("open_questions", []) or []))

        pressure = estimate_context_pressure(
            continuation_requested=continuation_requested,
            continuation_snapshot=continuation_snapshot,
            phase_summary_count=phase_summary_count,
            delegate_batch_count=delegate_batch_count,
            specialist_report_count=specialist_report_count,
            invalidation_count=invalidation_count,
            open_question_count=open_question_count,
        )
        compaction_value = estimate_context_compaction_value(
            phase_outputs=dict(ws.get("phase_outputs", {}) or {}),
            project_context_summary=str(ws.get("project_context_summary", "") or ""),
            chat_context_summary=str(ws.get("chat_context_summary", "") or ""),
            phase_context_summaries=dict(ws.get("phase_context_summaries", {}) or {}),
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

    def _refresh_context_pressure(
        self,
        task_root: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ws = self._get_workflow_state(task_root)
        pressure = self._compute_context_pressure(task_root, metadata=metadata)
        previous = dict(ws.get("context_pressure", {}) or {})
        ws["context_pressure"] = dict(pressure)
        ws["context_curator_recommended"] = bool(
            pressure.get("recommend_context_curator", False)
        )
        compaction_value = dict(pressure.get("context_compaction", {}) or {})
        if metadata is not None:
            metadata["context_pressure_score"] = int(pressure.get("score", 0) or 0)
            metadata["context_pressure_level"] = str(
                pressure.get("level", "") or ""
            ).strip()
            metadata["context_pressure_signals"] = list(
                pressure.get("signals", []) or []
            )
            metadata["context_pressure_high"] = (
                str(pressure.get("level", "") or "").strip().lower() == "high"
            )
            metadata["context_compaction_value_level"] = str(
                compaction_value.get("level", "") or ""
            ).strip()
            metadata["context_compaction_signals"] = list(
                compaction_value.get("signals", []) or []
            )
            metadata["estimated_context_chars_saved"] = int(
                compaction_value.get("estimated_context_chars_saved", 0) or 0
            )
            metadata["estimated_context_tokens_saved"] = int(
                compaction_value.get("estimated_context_tokens_saved", 0) or 0
            )
            metadata["context_compaction_priority_boost"] = bool(
                compaction_value.get("priority_boost", False)
            )
            if bool(pressure.get("recommend_context_curator", False)):
                metadata["context_curator_recommended"] = True
        if previous != pressure:
            self.event_logger.emit(
                "context_pressure_updated",
                {
                    "task_root": task_root,
                    "score": int(pressure.get("score", 0) or 0),
                    "level": str(pressure.get("level", "") or "").strip(),
                    "signals": list(pressure.get("signals", []) or []),
                    "estimated_context_tokens_saved": int(
                        compaction_value.get("estimated_context_tokens_saved", 0) or 0
                    ),
                    "context_compaction_level": str(
                        compaction_value.get("level", "") or ""
                    ).strip(),
                },
            )
        return pressure

    def _maybe_spawn_lead_failure_checkpoint(
        self,
        task: WorkTask,
        reason: str,
    ) -> str | None:
        """Crea un checkpoint explicito del Lead tras un fallo de fase de chat.

        MVP de E9-O5: no replantea el grafo todavia, pero materializa una tarea del
        Team Lead que puede decidir, pedir aclaracion o preparar la futura
        replanificacion sobre la corrida viva.
        """
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent or task.role == Role.TEAM_LEAD:
            return None

        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if not phase_name:
            phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        if phase_name in {"lead_intake", "lead_close"}:
            return None

        checkpoint_id = f"{chat_parent}::lead_failure_{phase_name}"
        if self.taskboard.get_task(checkpoint_id) is not None:
            task.metadata["lead_failure_checkpoint_id"] = checkpoint_id
            self._attach_checkpoint_as_downstream_gate(
                chat_parent=chat_parent,
                checkpoint_id=checkpoint_id,
                origin_task_id=task.task_id,
                metadata_key="lead_failure_gate_dependencies",
            )
            return checkpoint_id

        checkpoint_task = WorkTask(
            task_id=checkpoint_id,
            title=f"[Checkpoint] Revisar fallo en {phase_name}",
            description=(
                "Como Team Lead, interviene tras un fallo de fase durante una corrida de chat.\n"
                f"Fase fallida: {phase_name}\n"
                f"Tarea fallida: {task.task_id}\n"
                f"Motivo: {reason[:500]}\n"
                f"Contexto previo: {task.description[:1200]}\n"
                "Objetivo: decidir el siguiente paso inmediato. Puedes pedir aclaracion "
                "al usuario si falta contexto o preparar la futura replanificacion."
            ),
            role=Role.TEAM_LEAD,
            complexity=task.complexity,
            criticality=task.criticality,
            metadata={
                "required_capabilities": ["reasoning"],
                "interactive_chat": True,
                "skip_quality_gates": True,
                "skip_evidence_gate": True,
                "skip_peer_consultation": True,
                "phase": f"lead_failure_{phase_name}",
                "chat_parent": chat_parent,
                "failure_checkpoint_for": task.task_id,
                "failure_phase": phase_name,
                "failure_reason": reason[:500],
                "checkpoint_kind": "post_failure",
            },
        )
        try:
            self.taskboard.add_task(checkpoint_task)
        except ValueError:
            pass

        self._attach_checkpoint_as_downstream_gate(
            chat_parent=chat_parent,
            checkpoint_id=checkpoint_id,
            origin_task_id=task.task_id,
            metadata_key="lead_failure_gate_dependencies",
        )

        task.metadata["lead_failure_checkpoint_id"] = checkpoint_id
        self.mailbox.send(
            sender="system",
            recipient="team_lead",
            subject=f"Lead checkpoint requested: {task.task_id}",
            body=(
                f"Se abrio un checkpoint del Lead tras el fallo de {task.task_id}. "
                f"reason={reason[:240]}"
            ),
            task_id=task.task_id,
        )
        self.event_logger.emit(
            "lead_failure_checkpoint_spawned",
            {
                "task_id": task.task_id,
                "chat_parent": chat_parent,
                "checkpoint_task_id": checkpoint_id,
                "phase": phase_name,
            },
        )
        return checkpoint_id

    def _attach_checkpoint_as_downstream_gate(
        self,
        *,
        chat_parent: str,
        checkpoint_id: str,
        origin_task_id: str,
        metadata_key: str,
    ) -> None:
        if not chat_parent or not checkpoint_id:
            return

        lead_close_id = f"{chat_parent}::lead_close"
        lead_close_task = self.taskboard.get_task(lead_close_id)
        if (
            lead_close_task is not None
            and lead_close_task.state not in (TaskState.CLAIMED, TaskState.COMPLETED)
            and checkpoint_id not in lead_close_task.dependencies
        ):
            lead_close_task.dependencies.append(checkpoint_id)
            lead_close_task.metadata[metadata_key] = sorted(
                set(list(lead_close_task.metadata.get(metadata_key, [])) + [checkpoint_id])
            )
            self.taskboard.persist_tasks([lead_close_task.task_id])

        for downstream_task in self.taskboard.list_tasks():
            if downstream_task.task_id in {origin_task_id, checkpoint_id}:
                continue
            if str(downstream_task.metadata.get("chat_parent", "") or "").strip() != chat_parent:
                continue
            if downstream_task.state in (
                TaskState.CLAIMED,
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.SKIPPED,
                TaskState.ARCHIVED,
            ):
                continue
            phase_name = self._phase_name_for_task(downstream_task)
            if phase_name == "lead_intake" or phase_name.startswith(("lead_failure_", "lead_report_")):
                continue
            if checkpoint_id in downstream_task.dependencies:
                continue
            downstream_task.dependencies.append(checkpoint_id)
            downstream_task.metadata[metadata_key] = sorted(
                set(list(downstream_task.metadata.get(metadata_key, [])) + [checkpoint_id])
            )
            self.taskboard.persist_tasks([downstream_task.task_id])

    def _maybe_spawn_lead_report_checkpoint(
        self,
        task: WorkTask,
        output: str,
    ) -> str | None:
        """Crea un checkpoint del Lead tras recibir un informe delegado.

        MVP deliberativo: solo aplica a corridas de planning sin build.
        La tarea del Lead puede pedir aclaracion al usuario y, si el `lead_close`
        aun no ha empezado, se encadena como dependencia adicional para que el
        cierre espere a este punto de control.
        """
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent or task.role == Role.TEAM_LEAD:
            return None

        lead_run_mode = str(task.metadata.get("lead_run_mode", "") or "").strip().lower()
        if lead_run_mode not in {
            "planning_only",
            "team_decision",
            "architecture_review",
            "roadmap",
        }:
            return None

        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if not phase_name:
            phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        if phase_name.startswith("lead_") or phase_name in {"lead_intake", "lead_close"}:
            return None

        checkpoint_id = f"{chat_parent}::lead_report_{phase_name}"
        if self.taskboard.get_task(checkpoint_id) is not None:
            task.metadata["lead_report_checkpoint_id"] = checkpoint_id
            return checkpoint_id

        checkpoint_task = WorkTask(
            task_id=checkpoint_id,
            title=f"[Checkpoint] Revisar informe de {phase_name}",
            description=(
                "Como Team Lead, revisa este informe delegado antes del cierre.\n"
                f"Modo de corrida: {lead_run_mode}\n"
                f"Fase origen: {phase_name}\n"
                f"Tarea origen: {task.task_id}\n"
                f"Resumen del informe: {output[:1200]}\n"
                "Objetivo: sintetizar implicaciones, decidir si basta para continuar "
                "o pedir aclaracion al usuario si la decision sigue ambigua."
            ),
            role=Role.TEAM_LEAD,
            complexity=task.complexity,
            criticality=task.criticality,
            metadata={
                "required_capabilities": ["reasoning"],
                "interactive_chat": True,
                "skip_quality_gates": True,
                "skip_evidence_gate": True,
                "skip_peer_consultation": True,
                "skip_specialist_prefetch": True,
                "phase": f"lead_report_{phase_name}",
                "chat_parent": chat_parent,
                "lead_run_mode": lead_run_mode,
                "report_checkpoint_for": task.task_id,
                "report_phase": phase_name,
                "checkpoint_kind": "post_delegate_report",
            },
        )
        try:
            self.taskboard.add_task(checkpoint_task)
        except ValueError:
            pass

        lead_close_id = f"{chat_parent}::lead_close"
        lead_close_task = self.taskboard.get_task(lead_close_id)
        if (
            lead_close_task is not None
            and lead_close_task.state not in (TaskState.CLAIMED, TaskState.COMPLETED)
            and checkpoint_id not in lead_close_task.dependencies
        ):
            lead_close_task.dependencies.append(checkpoint_id)
            lead_close_task.metadata["lead_report_checkpoint_dependencies"] = sorted(
                set(
                    list(lead_close_task.metadata.get("lead_report_checkpoint_dependencies", []))
                    + [checkpoint_id]
                )
            )
            self.taskboard.persist_tasks([lead_close_task.task_id])

        for downstream_task in self.taskboard.list_tasks():
            if downstream_task.task_id == task.task_id:
                continue
            if str(downstream_task.metadata.get("chat_parent", "") or "").strip() != chat_parent:
                continue
            if downstream_task.state in (TaskState.CLAIMED, TaskState.COMPLETED, TaskState.FAILED):
                continue
            if task.task_id not in list(downstream_task.dependencies or []):
                continue
            if checkpoint_id in downstream_task.dependencies:
                continue
            downstream_task.dependencies.append(checkpoint_id)
            downstream_task.metadata["lead_report_gate_dependencies"] = sorted(
                set(
                    list(downstream_task.metadata.get("lead_report_gate_dependencies", []))
                    + [checkpoint_id]
                )
            )
            self.taskboard.persist_tasks([downstream_task.task_id])

        task.metadata["lead_report_checkpoint_id"] = checkpoint_id
        self.mailbox.send(
            sender=task.role.value,
            recipient="team_lead",
            subject=f"Lead report checkpoint: {task.task_id}",
            body=(
                f"Se abrio un checkpoint del Lead tras el informe de {task.task_id}. "
                f"run_mode={lead_run_mode}"
            ),
            task_id=task.task_id,
        )
        self.event_logger.emit(
            "lead_report_checkpoint_spawned",
            {
                "task_id": task.task_id,
                "chat_parent": chat_parent,
                "checkpoint_task_id": checkpoint_id,
                "phase": phase_name,
                "lead_run_mode": lead_run_mode,
            },
        )
        return checkpoint_id

    def _sensitive_reasons_for_preflight(self, task: WorkTask) -> list[str]:
        reasons: list[str] = []
        if bool(task.metadata.get("require_execution_plan")):
            reasons.append("require_execution_plan")
        execution_plan = task.metadata.get("execution_plan", [])
        if isinstance(execution_plan, list) and execution_plan:
            reasons.append("execution_plan_present")
        if bool(task.metadata.get("require_security_gate")):
            reasons.append("require_security_gate")
        if self._should_open_security_gate(task):
            reasons.append("security_gate_candidate")
        return sorted(set(reasons))

    def _maybe_spawn_lead_preflight_checkpoint(
        self,
        task: WorkTask,
    ) -> str | None:
        """Crea un checkpoint previo del Lead antes de una fase sensible.

        Se usa para tareas de chat que aun no han empezado y que implican
        ejecucion sensible o gates de seguridad. El checkpoint puede pausar la
        corrida via `[CLARIFY]` antes de entrar en la fase delicada.
        """
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent or task.role in (Role.TEAM_LEAD, Role.SCOUT):
            return None
        if task.metadata.get("lead_preflight_approved"):
            return None

        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if not phase_name:
            phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        if phase_name.startswith("lead_") or phase_name in {"lead_intake", "lead_close"}:
            return None

        sensitive_reasons = self._sensitive_reasons_for_preflight(task)
        if not sensitive_reasons:
            return None

        preflight_execution_plan, preflight_plan_diagnostics = (
            self._derive_execution_plan_with_diagnostics(task)
        )
        preflight_plan_lines: list[str] = []
        if preflight_execution_plan:
            preflight_plan_lines.append("Preview del execution plan efectivo:")
            for step in list(preflight_execution_plan)[:4]:
                command = str((step or {}).get("command", "") or "").strip()
                step_type = str((step or {}).get("type", "") or "").strip()
                if command:
                    preflight_plan_lines.append(f"- [{step_type or 'cmd'}] {command}")
        elif "require_execution_plan" in sensitive_reasons:
            checked_sources = list(
                (preflight_plan_diagnostics.get("checked_sources", []) or [])
            )[:4]
            preview_sources = ", ".join(
                str(item.get("source", "") or "").strip()
                for item in checked_sources
                if str(item.get("source", "") or "").strip()
            )
            if preview_sources:
                preflight_plan_lines.append(
                    "No se pudo derivar execution_plan automaticamente. "
                    f"Fuentes revisadas: {preview_sources}."
                )
            else:
                preflight_plan_lines.append(
                    "No se pudo derivar execution_plan automaticamente a partir del contrato "
                    "y dependencias completadas."
                )

        checkpoint_id = f"{chat_parent}::lead_preflight_{phase_name}"
        existing = self.taskboard.get_task(checkpoint_id)
        if existing is not None:
            task.metadata["lead_preflight_checkpoint_id"] = checkpoint_id
            if checkpoint_id not in task.dependencies:
                task.dependencies.append(checkpoint_id)
                self.taskboard.persist_tasks([task.task_id])
            if existing.state == TaskState.COMPLETED:
                checkpoint_output = str(
                    existing.metadata.get("result")
                    or existing.metadata.get("_last_agent_output")
                    or ""
                ).strip()
                checkpoint_directives = (
                    extract_lcp_directives(checkpoint_output)
                    if checkpoint_output
                    else {}
                )
                if checkpoint_directives.get("replan"):
                    task.metadata["lead_preflight_replan_requested"] = True
                    self.taskboard.persist_tasks([task.task_id])
                    return checkpoint_id
                task.metadata["lead_preflight_approved"] = True
                self.taskboard.persist_tasks([task.task_id])
                return None
            return checkpoint_id

        checkpoint_task = WorkTask(
            task_id=checkpoint_id,
            title=f"[Checkpoint] Autorizar fase sensible {phase_name}",
            description=(
                "Como Team Lead, valida si esta fase sensible debe ejecutarse ahora.\n"
                f"Fase candidata: {phase_name}\n"
                f"Tarea candidata: {task.task_id}\n"
                f"Motivos de sensibilidad: {', '.join(sensitive_reasons)}\n"
                f"Contexto de la tarea: {task.description[:1200]}\n"
                + (("\n".join(preflight_plan_lines) + "\n") if preflight_plan_lines else "")
                + (
                "Objetivo: decidir si se puede continuar, si conviene pedir aclaracion "
                "al usuario o si hay que replantear el enfoque antes de ejecutar."
                )
            ),
            role=Role.TEAM_LEAD,
            complexity=task.complexity,
            criticality=task.criticality,
            metadata={
                "required_capabilities": ["reasoning"],
                "interactive_chat": True,
                "skip_quality_gates": True,
                "skip_evidence_gate": True,
                "skip_peer_consultation": True,
                "skip_specialist_prefetch": True,
                "phase": f"lead_preflight_{phase_name}",
                "chat_parent": chat_parent,
                "lead_run_mode": str(task.metadata.get("lead_run_mode", "") or "").strip() or "standard",
                "preflight_for": task.task_id,
                "preflight_phase": phase_name,
                "preflight_sensitive_reasons": sensitive_reasons,
                "preflight_execution_plan_preview": [
                    dict(step) for step in list(preflight_execution_plan or [])[:4]
                ],
                "preflight_execution_plan_diagnostics": dict(
                    preflight_plan_diagnostics
                ),
                "checkpoint_kind": "pre_sensitive_phase",
            },
        )
        try:
            self.taskboard.add_task(checkpoint_task)
        except ValueError:
            pass

        if checkpoint_id not in task.dependencies:
            task.dependencies.append(checkpoint_id)
        task.metadata["lead_preflight_checkpoint_id"] = checkpoint_id
        task.metadata["lead_preflight_sensitive_reasons"] = sensitive_reasons
        if preflight_execution_plan:
            task.metadata["lead_preflight_execution_plan_preview"] = [
                dict(step) for step in list(preflight_execution_plan or [])[:4]
            ]
        task.metadata["lead_preflight_execution_plan_diagnostics"] = dict(
            preflight_plan_diagnostics
        )
        self.taskboard.persist_tasks([task.task_id])
        self.mailbox.send(
            sender="system",
            recipient="team_lead",
            subject=f"Lead preflight checkpoint: {task.task_id}",
            body=(
                f"Se abrio un checkpoint previo para {task.task_id}. "
                f"sensitive_reasons={sensitive_reasons}"
            ),
            task_id=task.task_id,
        )
        self.event_logger.emit(
            "lead_preflight_checkpoint_spawned",
            {
                "task_id": task.task_id,
                "chat_parent": chat_parent,
                "checkpoint_task_id": checkpoint_id,
                "phase": phase_name,
                "sensitive_reasons": sensitive_reasons,
            },
        )
        return checkpoint_id

    @staticmethod
    def _missing_execution_plan_waiver_reason(task: WorkTask) -> str:
        """Devuelve el motivo de waiver si la fase puede continuar sin plan estructurado."""
        if not bool(task.metadata.get("require_execution_plan", False)):
            return ""
        if not bool(task.metadata.get("interactive_chat", False)):
            return ""
        if task.role != Role.ENGINEER:
            return ""
        if not bool(task.metadata.get("lead_preflight_approved", False)):
            return ""

        if bool(task.metadata.get("continuation_requested", False)) and not bool(
            task.metadata.get("continuation_effective", False)
        ):
            return "lead_preflight_continuation"

        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        objective = str(
            phase_contract.get("objective")
            or task.metadata.get("delegation_brief")
            or ""
        ).strip()
        if not is_missing_contract_objective(objective):
            return "lead_preflight_approved"
        return ""

    def _has_pending_chat_directive_checkpoint(self, task: WorkTask) -> bool:
        """Evita avanzar mientras exista una directiva del Lead pendiente de consumo.

        Hoy `REPLAN` y `FORCE_GATE` se aplican en `api/main.py` despues de
        `run_until_idle()`. Este helper evita que la corrida siga hasta
        `lead_close` o la fase sensible si un checkpoint del Lead ya emitio una
        de esas directivas y aun no fue consumida por la capa de chat.
        """
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent:
            return False
        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if phase_name.startswith(("lead_failure_", "lead_report_", "lead_preflight_")):
            return False
        delegate_source_phase = str(task.metadata.get("delegate_source_phase", "") or "").strip()
        if delegate_source_phase.startswith("lead_failure_"):
            return False

        phase_outputs = self._get_workflow_state(chat_parent).get("phase_outputs", {})
        if not isinstance(phase_outputs, dict):
            return False

        dependency_phases: list[str] = []
        prefix = f"{chat_parent}::"
        for dependency in list(task.dependencies or []):
            dep_id = str(dependency or "").strip()
            if not dep_id.startswith(prefix):
                continue
            phase_name = dep_id[len(prefix):]
            if phase_name.startswith(("lead_preflight_", "lead_report_", "lead_failure_")):
                dependency_phases.append(phase_name)

        if not dependency_phases:
            if phase_name == "lead_close":
                dependency_phases = [
                    name
                    for name in phase_outputs.keys()
                    if isinstance(name, str) and name.startswith(("lead_report_", "lead_failure_"))
                ]
            else:
                dependency_phases = [
                    name
                    for name in phase_outputs.keys()
                    if isinstance(name, str) and name.startswith("lead_preflight_")
                ]
                dependency_phases.extend(self._active_failure_checkpoint_phase_names(chat_parent))

        dependency_phases = list(dict.fromkeys(dependency_phases))
        active_failure_phases = set(self._active_failure_checkpoint_phase_names(chat_parent))

        for checkpoint_phase in dependency_phases:
            if checkpoint_phase in active_failure_phases:
                return True
            output = phase_outputs.get(checkpoint_phase, "")
            if checkpoint_phase not in phase_outputs:
                continue
            directives = extract_lcp_directives(str(output or ""))
            if (
                directives.get("replan")
                or directives.get("force_gate")
                or directives.get("abort_phases")
                or directives.get("advisory_mode")
                or directives.get("skip")
                or directives.get("retry_route")
            ):
                return True
        return False

    def _active_failure_checkpoint_phase_names(self, chat_parent: str) -> list[str]:
        if not chat_parent:
            return []
        active: list[str] = []
        for candidate in self.taskboard.list_tasks():
            if str(candidate.metadata.get("chat_parent", "") or "").strip() != chat_parent:
                continue
            phase_name = self._phase_name_for_task(candidate)
            if not phase_name.startswith("lead_failure_"):
                continue
            if candidate.state in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.SKIPPED,
                TaskState.ARCHIVED,
            ):
                continue
            active.append(phase_name)
        return active

    # ── Team Ledger ─────────────────────────────────────────────────

    def _update_team_ledger(
        self,
        task: WorkTask,
        assignee: str,
        output: str,
        success: bool,
    ) -> None:
        task_root = self._task_root(task.task_id)
        ws = self._get_workflow_state(task_root)
        entry = {
            "round": self._round,
            "phase": task.role.value,
            "task_id": task.task_id,
            "assignee": assignee,
            "status": "completed" if success else "failed",
            "output_summary": output[:300],
            "iteration": int(task.metadata.get("gate_iteration", 0)),
        }
        ws.setdefault("ledger", []).append(entry)
        ws["ledger"] = ws["ledger"][-30:]
        recent = ws["ledger"][-5:]
        failed_count = sum(1 for e in recent if e.get("status") == "failed")
        if failed_count >= 3:
            ws["stall_detected"] = True
            self.event_logger.emit(
                "stall_detected",
                {"task_root": task_root, "recent_failures": failed_count},
            )
        self._save_workflow_state(task_root)

    # ── Dependency output context ───────────────────────────────────

    def _format_dependency_planning_artifact(
        self,
        artifact: object,
        *,
        limit: int,
    ) -> str:
        if not isinstance(artifact, dict):
            return ""
        objective = str(artifact.get("objective", "") or "").strip()
        steps = [
            str(item).strip()
            for item in list(artifact.get("steps", []) or [])
            if str(item).strip()
        ]
        acceptance = [
            str(item).strip()
            for item in list(artifact.get("acceptance_criteria", []) or [])
            if str(item).strip()
        ]
        constraints = [
            str(item).strip()
            for item in list(artifact.get("constraints", []) or [])
            if str(item).strip()
        ]
        if not objective and not steps and not acceptance and not constraints:
            return ""
        sections: list[str] = []
        if objective:
            sections.append(f"objective={objective}")
        if steps:
            sections.append(
                "steps=" + "; ".join(f"{idx}. {step}" for idx, step in enumerate(steps[:3], start=1))
            )
        if acceptance:
            sections.append("acceptance=" + "; ".join(acceptance[:2]))
        if constraints:
            sections.append("constraints=" + "; ".join(constraints[:2]))
        return self._compact_text("planning_artifact: " + " | ".join(sections), limit)

    def _completed_dependency_planning_artifacts(
        self,
        task: WorkTask,
    ) -> list[tuple[str, dict[str, Any]]]:
        task_root = self._task_root(task.task_id)
        ws = self.workflow_state.get(task_root, {})
        planning_artifacts = dict(ws.get("planning_artifacts", {}) or {})
        completed: list[tuple[str, dict[str, Any]]] = []
        for dep_id in list(task.dependencies or []):
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                continue
            dep_phase = str(dep_task.metadata.get("phase", "") or "").strip()
            if not dep_phase:
                continue
            artifact = dict(
                dep_task.metadata.get("planning_artifact", {})
                or planning_artifacts.get(dep_phase, {})
                or {}
            )
            if artifact:
                completed.append((dep_phase, artifact))
        return completed

    def _repair_plan_risks_upstream_relitigation(
        self,
        *,
        task: WorkTask,
        safe_content: str,
        assignee: str,
    ) -> str:
        phase_name = self._phase_name_for_task(task).lower()
        if phase_name != "plan_risks":
            return safe_content
        completed_artifacts = self._completed_dependency_planning_artifacts(task)
        if not completed_artifacts:
            return safe_content
        normalized = str(safe_content or "").strip()
        if not normalized:
            return safe_content
        contradiction_markers = [
            re.compile(r"(?i)\b(?:no puede|cannot|can't)\b.{0,80}\b(?:iniciarse|continuar|start|proceed)\b"),
            re.compile(r"(?i)\bdependenc(?:ia|y)\b.{0,40}\b(?:cr[ií]tica|critical)\b.{0,40}\b(?:violad|failed|incomplete|truncat|insuficiente)\w*"),
            re.compile(r"(?i)\b(?:artifact|artefacto|planning_artifact|plan)\b.{0,40}\b(?:truncad|incomplet|insuficiente)\w*"),
            re.compile(r"(?i)\bintegridad\b.{0,40}\b(?:no|insuficiente|violad)\w*"),
        ]
        if not any(pattern.search(normalized) for pattern in contradiction_markers):
            return safe_content

        dep_phase, artifact = completed_artifacts[0]
        objective = str(artifact.get("objective", "") or "").strip() or f"Continuar desde {dep_phase}"
        acceptance = [
            str(item).strip()
            for item in list(artifact.get("acceptance_criteria", []) or [])
            if str(item).strip()
        ]
        constraints = [
            str(item).strip()
            for item in list(artifact.get("constraints", []) or [])
            if str(item).strip()
        ]
        steps = [
            str(item).strip()
            for item in list(artifact.get("steps", []) or [])
            if str(item).strip()
        ]
        reconciled = (
            "Riesgos:\n"
            f"- Riesgo principal: el slice definido en {dep_phase} debe mantenerse dentro del objetivo aprobado ({objective}).\n"
            + (
                f"- Riesgo de regresion: validar especificamente {acceptance[0]}.\n"
                if acceptance
                else "- Riesgo de regresion: confirmar que el cambio mantiene el comportamiento esperado del slice.\n"
            )
            + "Quality Gates:\n"
            + (
                "".join(f"- {item}\n" for item in acceptance[:2])
                if acceptance
                else "- El resultado debe cumplir los criterios de aceptacion del planning upstream.\n"
            )
            + "Pruebas Minimas:\n"
            + (
                f"- Verificar el paso de mayor impacto ligado a: {steps[0]}.\n"
                if steps
                else "- Verificar el comportamiento mas critico del slice aprobado.\n"
            )
            + "Supuestos/Huecos:\n"
            + f"- Upstream autoritativo: {dep_phase} ya esta completed; esta fase no debe relitigar si el planning debio existir.\n"
            + (
                f"- Restriccion a vigilar: {constraints[0]}.\n"
                if constraints
                else "- Si aparece un hueco nuevo, tratarlo como riesgo residual y no como bloqueo automatico.\n"
            )
            + "[PHASE_VERDICT]\n"
            + "phase_id: plan_risks\n"
            + "status: completed\n"
            + "reason_codes: aligned\n"
            + "contract_status: aligned\n"
            + "summary: Riesgos, quality gates y pruebas minimas derivados desde planning upstream completado, sin relitigar dependencias ya cerradas.\n"
            + "[/PHASE_VERDICT]\n"
        )
        self.event_logger.emit(
            "plan_risks_upstream_relitigation_repaired",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "dependency_phase": dep_phase,
            },
        )
        task.metadata["plan_risks_repaired_from_output"] = True
        task.metadata["plan_risks_original_output"] = normalized
        return reconciled

    def _build_dependency_output_context(self, task: WorkTask) -> str:
        if not task.dependencies:
            return ""
        # lead_close necesita mas contexto para no fabricar informacion:
        # Researcher (900) y QA (800) deben llegar completos al Team Lead.
        _phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
        _is_lead_close = _phase_name == "lead_close"
        _is_compact_executor = (
            task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}
            and not self._phase_name_for_task(task).lower().startswith("plan_")
        )
        lines: list[str] = []
        recovery_context = self._compact_recovery_context(task)
        if recovery_context:
            lines.append(recovery_context)
        for dep_id in task.dependencies:
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                if (
                    dep_task is not None
                    and TaskBoard._is_soft_support_dependency(task, dep_task)
                    and dep_task.state
                    in (
                        TaskState.FAILED,
                        TaskState.BLOCKED,
                        TaskState.SKIPPED,
                        TaskState.ARCHIVED,
                    )
                ):
                    dep_phase = str(dep_task.metadata.get("phase", "") or "").strip()
                    dep_label = dep_phase or dep_task.task_id
                    dep_state = getattr(dep_task.state, "value", str(dep_task.state))
                    reason = str(
                        dep_task.metadata.get("error")
                        or dep_task.metadata.get("blocked_reason")
                        or dep_task.metadata.get("skipped_reason")
                        or "support_unavailable"
                    ).strip()
                    lines.append(
                        "[Support] "
                        f"support_dependency=degraded; phase={dep_label}; "
                        f"state={dep_state}; reason={self._compact_text(reason, 220)}; "
                        "decision_authority=parent_phase"
                    )
                continue
            task_root = self._task_root(task.task_id)
            ws = self.workflow_state.get(task_root, {})
            phase_summaries = ws.get("phase_context_summaries", {})
            planning_artifacts = ws.get("planning_artifacts", {})
            dep_phase = str(dep_task.metadata.get("phase", "") or "").strip()
            if _is_compact_executor and (
                self._to_bool(dep_task.metadata.get("advisory_context_phase", False))
                or self._to_bool(dep_task.metadata.get("advisory_planning_phase", False))
            ):
                continue
            if dep_phase.startswith("lead_preflight_"):
                preflight_target = dep_phase.removeprefix("lead_preflight_") or dep_phase
                lines.append(
                    f"[Team Lead] state=completed; preflight=approved; phase={preflight_target}"
                )
                continue
            compact_summary = ""
            if isinstance(phase_summaries, dict) and dep_phase:
                compact_summary = str(phase_summaries.get(dep_phase, "") or "").strip()
            planning_summary = ""
            planning_context = ""
            phase_label = dep_task.role.value.replace("_", " ").title()
            role = dep_task.role.value
            if _is_lead_close and role in ("researcher", "qa"):
                limit = 900 if role == "researcher" else 800
            elif _is_compact_executor and dep_phase.lower().startswith("plan_"):
                limit = 1400
            else:
                limit = 400
            if isinstance(planning_artifacts, dict) and dep_phase:
                dep_artifact = dict(
                    dep_task.metadata.get("planning_artifact", {})
                    or planning_artifacts.get(dep_phase, {})
                    or {}
                )
                planning_summary = str(
                    (planning_artifacts.get(dep_phase, {}) or {}).get("summary", "") or ""
                ).strip()
                planning_limit = limit
                if dep_phase.lower().startswith("plan_"):
                    planning_limit = 1400 if _is_compact_executor else max(limit, 550)
                planning_context = self._format_dependency_planning_artifact(
                    dep_artifact,
                    limit=planning_limit,
                )
            result = dep_task.metadata.get("result", "")
            if not result and not compact_summary and not planning_summary and not planning_context:
                continue
            if planning_context:
                compacted = planning_context
            elif planning_summary:
                compacted = planning_summary
            elif compact_summary:
                compacted = compact_summary
            else:
                compacted = self._compact_text(str(result or ""), limit)
            dep_state = getattr(dep_task.state, "value", str(dep_task.state))
            artifact_paths = [
                str(item).strip()
                for item in list(dep_task.metadata.get("artifact_paths", []) or [])
                if str(item).strip()
            ]
            prefix_bits: list[str] = []
            if dep_state:
                prefix_bits.append(f"state={dep_state}")
            if artifact_paths:
                prefix_bits.append(f"artifacts={', '.join(artifact_paths[:4])}")
            prefix = ("; ".join(prefix_bits) + "; ") if prefix_bits else ""
            lines.append(f"[{phase_label}] {prefix}{compacted}")
        if _is_compact_executor and task.role in {Role.REVIEWER, Role.QA}:
            reviewable_context = self._build_reviewable_artifact_context(task)
            if reviewable_context:
                lines.append(reviewable_context)
        if not lines:
            task_root = self._task_root(task.task_id)
            ws = self.workflow_state.get(task_root, {})
            phase_outputs = ws.get("phase_outputs", {})
            phase_summaries = ws.get("phase_context_summaries", {})
            chat_context_summary = str(ws.get("chat_context_summary", "") or "").strip()
            if chat_context_summary:
                lines.append(f"[Context Curator] {self._compact_text(chat_context_summary, 500)}")
            fallback_limit = 600 if _is_lead_close else 300
            for phase, output in phase_outputs.items():
                compacted = ""
                if isinstance(phase_summaries, dict):
                    compacted = str(phase_summaries.get(phase, "") or "").strip()
                lines.append(
                    f"[{phase}] {compacted or self._compact_text(output, fallback_limit)}"
                )
        return "\n".join(lines[:8])

    def _build_reviewable_artifact_context(self, task: WorkTask) -> str:
        """Attach small, contract-scoped file snippets for review/QA phases."""

        if task.role not in {Role.REVIEWER, Role.QA}:
            return ""
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        raw_hints = list(self._dependency_artifact_hints(task))
        raw_hints.extend(
            str(item).strip().replace("\\", "/")
            for item in list(phase_contract.get("allowed_module_path_hints", []) or [])
            if str(item).strip()
        )
        hints = list(dict.fromkeys(raw_hints))
        if not hints:
            return ""

        root = self.project_root if self.project_root.exists() else self.runtime_dir
        try:
            root_resolved = root.resolve()
        except Exception:
            root_resolved = root
        focus_text = " ".join(
            str(item or "")
            for item in (
                task.task_id,
                task.title,
                task.description,
                phase_contract.get("phase_id", ""),
                phase_contract.get("objective", ""),
            )
        ).lower()
        ignored_roots = {
            ".aiteam",
            ".git",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
        }
        scored: list[tuple[int, str, Path]] = []
        for hint in hints:
            normalized = str(hint or "").strip().replace("\\", "/").strip("/")
            if not normalized or normalized.endswith("/"):
                continue
            if normalized.split("/", 1)[0] in ignored_roots:
                continue
            candidate = (root / normalized)
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root_resolved)
            except Exception:
                continue
            if not resolved.is_file():
                continue
            try:
                if resolved.stat().st_size > 16_000:
                    continue
            except OSError:
                continue
            basename = resolved.name.lower()
            stem = resolved.stem.lower()
            score = 0
            if normalized in raw_hints[:8]:
                score += 4
            if basename == "__init__.py":
                score -= 4
            if basename in focus_text or stem in focus_text:
                score += 4
            for token in stem.replace("-", "_").split("_"):
                if len(token) >= 3 and token in focus_text:
                    score += 2
            if "test" in basename and task.role == Role.QA:
                score += 2
            if "test" in basename and task.role == Role.REVIEWER:
                score += 1
            scored.append((score, normalized, resolved))

        if not scored:
            return ""
        snippets: list[str] = []
        total_chars = 0
        for _score, normalized, resolved in sorted(scored, key=lambda item: (-item[0], item[1]))[:3]:
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            remaining = max(0, 3200 - total_chars)
            if remaining < 300:
                break
            snippet = self._compact_text(text, min(1400, remaining))
            total_chars += len(snippet)
            snippets.append(f"- {normalized}:\n{snippet}")
        if not snippets:
            return ""
        return "Reviewable artifact snippets:\n" + "\n".join(snippets)

    def _compact_recovery_context(self, task: WorkTask) -> str:
        if task.role not in {Role.REVIEWER, Role.QA}:
            return ""
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent:
            return ""

        pending_checkpoints: list[str] = []
        retried_phases: list[str] = []
        for candidate in self.taskboard.list_tasks():
            if str(candidate.metadata.get("chat_parent", "") or "").strip() != chat_parent:
                continue
            phase_name = self._phase_name_for_task(candidate)
            if phase_name.startswith("lead_failure_") and candidate.state not in (
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.SKIPPED,
                TaskState.ARCHIVED,
            ):
                pending_checkpoints.append(phase_name)
                continue
            if self._to_bool(candidate.metadata.get("retry_route_requested", False)):
                retried_phases.append(phase_name or candidate.task_id)

        if pending_checkpoints:
            return (
                "[System] recovery=pending; unresolved_failure_checkpoints="
                + ", ".join(sorted(dict.fromkeys(pending_checkpoints))[:3])
            )
        if retried_phases:
            return (
                "[System] recovery=applied; retried_phases="
                + ", ".join(sorted(dict.fromkeys(retried_phases))[:3])
            )
        return "[System] recovery=stable; unresolved_failure_checkpoints=none"

    @staticmethod
    def _phase_name_for_task(task: WorkTask) -> str:
        return str(
            task.metadata.get("phase", "")
            or (task.task_id.split("::")[-1] if "::" in task.task_id else "")
        ).strip()

    @classmethod
    def _is_planning_phase_task(cls, task: WorkTask) -> bool:
        return cls._phase_name_for_task(task).lower().startswith("plan_")

    @staticmethod
    def _normalize_delivery_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _is_usable_delivery_text(
        self,
        value: Any,
        *,
        role: Role,
        phase: str,
        source_kind: str,
    ) -> tuple[bool, str]:
        text = self._normalize_delivery_text(value)
        if not text:
            return False, "empty"

        lower = text.lower()
        explicit_empty_markers = (
            "sin datos disponibles",
            "sin informacion disponible",
            "sin información disponible",
            "no data available",
            "n/a",
            "none",
            "null",
        )
        if any(lower == marker or lower.startswith(f"{marker}.") for marker in explicit_empty_markers):
            return False, "explicit_empty_marker"

        quality_ok, quality_reason = self._assess_output_quality(text, role, phase)
        if quality_ok:
            return True, quality_reason
        if quality_reason in {
            "placeholder_output",
            "output_vacio",
            "output_trivial_sin_contenido_tecnico",
        }:
            return False, quality_reason

        min_length = 24 if source_kind == "summary" else 80
        if len(text) >= min_length:
            return True, f"{source_kind}_fallback_length:{len(text)}"
        return False, f"{source_kind}_too_short:{len(text)}"

    def _dependency_delivery_gaps(self, task: WorkTask) -> list[dict[str, str]]:
        if not task.dependencies or task.role == Role.TEAM_LEAD:
            return []
        is_chat_phase = bool(task.metadata.get("phase_contract_enforced")) or bool(
            task.metadata.get("chat_parent")
        ) or self._task_root(task.task_id).startswith("CHAT-")
        if not is_chat_phase:
            return []

        task_root = self._task_root(task.task_id)
        ws = self._get_workflow_state(task_root)
        phase_summaries = dict(ws.get("phase_context_summaries", {}) or {})
        phase_outputs = dict(ws.get("phase_outputs", {}) or {})
        planning_artifacts = dict(ws.get("planning_artifacts", {}) or {})
        gaps: list[dict[str, str]] = []

        for dep_id in task.dependencies:
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                continue

            dep_phase = self._phase_name_for_task(dep_task)
            dep_planning_artifact = dict(
                dep_task.metadata.get("planning_artifact", {})
                or planning_artifacts.get(dep_phase, {})
                or {}
            )
            dependency_artifacts = [
                str(item).strip()
                for item in list(dep_task.metadata.get("artifact_paths", []) or [])
                if str(item).strip()
            ]
            if dependency_artifacts:
                continue
            if self._format_dependency_planning_artifact(dep_planning_artifact, limit=500):
                continue
            candidates = [
                ("planning_artifact", (planning_artifacts.get(dep_phase, {}) or {}).get("summary", "")),
                ("summary", phase_summaries.get(dep_phase, "")),
                ("result", dep_task.metadata.get("result", "")),
                ("output", phase_outputs.get(dep_phase, "")),
            ]
            reasons: list[str] = []
            usable = False
            for source_kind, candidate in candidates:
                ok, reason = self._is_usable_delivery_text(
                    candidate,
                    role=dep_task.role,
                    phase=dep_phase,
                    source_kind=source_kind,
                )
                if ok:
                    usable = True
                    break
                reasons.append(f"{source_kind}:{reason}")
            if usable:
                continue
            gaps.append(
                {
                    "dependency_task_id": dep_id,
                    "phase": dep_phase,
                    "role": dep_task.role.value,
                    "reason": ", ".join(reasons) or "missing_delivery",
                }
            )
        return gaps

    @staticmethod
    def _workspace_visible_project_files(
        workspace: Path,
        *,
        limit: int = 8,
    ) -> list[str]:
        ignored_roots = {
            ".aiteam",
            ".git",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
        }
        visible: list[str] = []
        if not workspace.exists():
            return visible
        try:
            for path in workspace.rglob("*"):
                parts = set(path.relative_to(workspace).parts)
                if parts & ignored_roots:
                    continue
                if not path.is_file():
                    continue
                visible.append(path.relative_to(workspace).as_posix())
                if len(visible) >= limit:
                    break
        except Exception:
            return visible
        return visible

    @staticmethod
    def _workspace_grounding_visible_files(
        project_workspace: Path,
        *,
        limit: int = 12,
    ) -> list[str]:
        visible = AITeamOrchestrator._workspace_visible_project_files(
            project_workspace,
            limit=max(limit, 8),
        )
        internal_hints: list[str] = []
        aiteam_dir = project_workspace / ".aiteam"
        if aiteam_dir.exists():
            internal_hints.append(".aiteam/")
            for candidate in (
                "aiteam.db",
                "lead_memory.md",
                "instructions.md",
                "context/",
                "memory/",
                "sandboxes/",
            ):
                internal_name = candidate.rstrip("/")
                internal_path = aiteam_dir / internal_name
                if internal_path.exists():
                    suffix = "/" if candidate.endswith("/") else ""
                    internal_hints.append(f".aiteam/{internal_name}{suffix}")
        root_dir_hints: list[str] = []
        for candidate in ("src", "tests", "docs", "api", "config", "scripts"):
            if (project_workspace / candidate).exists():
                root_dir_hints.append(f"{candidate}/")
        return list(dict.fromkeys(visible + root_dir_hints + internal_hints))[:limit]

    @staticmethod
    def _workspace_grounding_candidate_paths(
        path_hint: str,
        *,
        project_workspace: Path,
        task_workspace: Path,
    ) -> list[Path]:
        normalized = str(path_hint or "").strip().strip("/")
        if not normalized:
            return []
        candidates: list[Path] = []
        for base in (project_workspace, task_workspace):
            try:
                candidates.append(base / Path(normalized))
            except Exception:
                continue

        if normalized.startswith(".aiteam/"):
            internal_name = normalized.split("/", 1)[1].strip()
            if internal_name:
                candidates.append(project_workspace / ".aiteam" / internal_name)
        elif normalized in {
            ".aiteam",
            "aiteam.db",
            "lead_memory.md",
            "instructions.md",
            "context",
            "context/",
            "memory",
            "memory/",
            "sandboxes",
            "sandboxes/",
        }:
            if normalized == ".aiteam":
                candidates.append(project_workspace / ".aiteam")
            elif normalized == "aiteam.db":
                candidates.append(project_workspace / ".aiteam" / "aiteam.db")
            else:
                candidates.append(project_workspace / ".aiteam" / normalized.rstrip("/"))
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _path_hint_exists_in_workspace(
        path_hint: str,
        *,
        project_workspace: Path,
        task_workspace: Path,
    ) -> bool:
        for candidate in AITeamOrchestrator._workspace_grounding_candidate_paths(
            path_hint,
            project_workspace=project_workspace,
            task_workspace=task_workspace,
        ):
            try:
                if candidate.exists():
                    return True
                if not str(candidate.suffix or "").strip():
                    parent = candidate.parent
                    stem = candidate.name
                    if parent.exists():
                        for sibling in parent.glob(f"{stem}.*"):
                            if sibling.exists():
                                return True
                normalized = str(path_hint or "").strip().replace("\\", "/").strip("/")
                if "/" not in normalized and candidate.suffix:
                    ignored_roots = {
                        ".aiteam",
                        ".git",
                        "venv",
                        "node_modules",
                        "__pycache__",
                        ".pytest_cache",
                    }
                    for base in (project_workspace, task_workspace):
                        if not base.exists():
                            continue
                        for sibling in base.rglob(candidate.name):
                            try:
                                parts = set(sibling.relative_to(base).parts)
                            except Exception:
                                continue
                            if parts & ignored_roots:
                                continue
                            if sibling.is_file():
                                return True
            except Exception:
                continue
        return False

    @staticmethod
    def _path_hint_matches_grounding_hints(
        path_hint: str,
        *,
        visible_files: list[str],
        dependency_artifacts: list[str],
    ) -> bool:
        normalized = str(path_hint or "").strip().replace("\\", "/").strip("/").lower()
        if not normalized:
            return False

        hint_pool = [
            str(item or "").strip().replace("\\", "/").strip("/").lower()
            for item in list(visible_files or []) + list(dependency_artifacts or [])
            if str(item or "").strip()
        ]
        if not hint_pool:
            return False

        normalized_parent = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        normalized_basename = normalized.rsplit("/", 1)[-1]
        normalized_stem = (
            normalized_basename.rsplit(".", 1)[0]
            if "." in normalized_basename
            else normalized_basename
        )

        def _matches_segment_prefix(partial: str, full: str) -> bool:
            partial_segments = [segment for segment in partial.split("/") if segment]
            full_segments = [segment for segment in full.split("/") if segment]
            if (
                len(partial_segments) < 2
                or len(partial_segments) > len(full_segments)
            ):
                return False
            if any(len(segment) < 2 for segment in partial_segments[:-1]):
                return False
            if len(partial_segments[-1]) < 4:
                return False
            if partial_segments[:-1] != full_segments[: len(partial_segments) - 1]:
                return False
            return full_segments[len(partial_segments) - 1].startswith(partial_segments[-1])

        for hint in hint_pool:
            if not hint:
                continue
            hint_clean = hint.rstrip("/")
            hint_parent = hint_clean.rsplit("/", 1)[0] if "/" in hint_clean else ""
            hint_basename = hint_clean.rsplit("/", 1)[-1]
            hint_stem = (
                hint_basename.rsplit(".", 1)[0]
                if "." in hint_basename
                else hint_basename
            )
            if hint == normalized or hint.endswith("/" + normalized):
                return True
            if hint_basename == normalized_basename:
                return True
            if normalized_stem and hint_stem == normalized_stem:
                if normalized_parent and hint_parent:
                    if hint_parent.endswith(normalized_parent) or normalized_parent.endswith(hint_parent):
                        return True
                else:
                    return True
            if "/" in normalized and "." not in normalized and _matches_segment_prefix(normalized, hint):
                return True
        return False

    def _dependency_artifact_hints(self, task: WorkTask) -> list[str]:
        hints: list[str] = []
        for dep_id in list(task.dependencies or []):
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None:
                continue
            dep_artifacts = [
                str(item).strip()
                for item in list(dep_task.metadata.get("artifact_paths", []) or [])
                if str(item).strip()
            ]
            hints.extend(dep_artifacts[:6])
        deduped = list(dict.fromkeys(hints))[:8]
        if deduped:
            return deduped

        metadata_hints = [
            str(item).strip()
            for item in list(task.metadata.get("workspace_artifact_hints", []) or [])
            if str(item).strip()
        ]
        if metadata_hints and task.role in {Role.REVIEWER, Role.QA}:
            return list(dict.fromkeys(metadata_hints))[:8]
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        contract_hints = [
            str(item).strip().replace("\\", "/")
            for item in list(phase_contract.get("allowed_module_path_hints", []) or [])
            if str(item).strip()
        ]
        if contract_hints and task.role in {Role.RESEARCHER, Role.REVIEWER, Role.QA}:
            return list(dict.fromkeys(contract_hints))[:8]
        return []

    def _detect_ungrounded_evidence_issue(
        self,
        *,
        task: WorkTask,
        safe_content: str,
        workspace: Path,
    ) -> dict[str, Any]:
        phase_name = self._phase_name_for_task(task).lower()
        is_support_or_delegate = phase_name.startswith("delegate_") or task.role in {
            Role.SCOUT,
            Role.RESEARCHER,
            Role.REVIEWER,
            Role.QA,
        }
        if not is_support_or_delegate:
            return {}

        normalized_text = str(safe_content or "").strip()
        if not normalized_text:
            return {}
        lower = normalized_text.lower()
        project_workspace = self.project_root if self.project_root.exists() else workspace
        visible_files = self._workspace_grounding_visible_files(project_workspace, limit=12)
        dependency_artifacts = self._dependency_artifact_hints(task)
        if not visible_files and not dependency_artifacts:
            return {}

        path_candidates = [
            path
            for path in extract_path_candidates(normalized_text)
            if path and not path.startswith(".aiteam/")
        ]
        missing_paths: list[str] = []
        for path_hint in path_candidates:
            if not self._path_hint_exists_in_workspace(
                path_hint,
                project_workspace=project_workspace,
                task_workspace=workspace,
            ) and not self._path_hint_matches_grounding_hints(
                path_hint,
                visible_files=visible_files,
                dependency_artifacts=dependency_artifacts,
            ):
                missing_paths.append(path_hint)

        presence_claim_markers = (
            "confirma",
            "confirma la estructura",
            "confirm",
            "confirmed",
            "ya implementado",
            "ya está implementado",
            "ya esta implementado",
            "implemented",
            "presente",
            "presente en",
            "existing",
            "exists",
            "utiliza",
            "usa ",
            "encapsula",
            "estructura y las interdependencias",
            "se verificó la presencia",
            "se verifico la presencia",
        )
        if missing_paths and any(marker in lower for marker in presence_claim_markers):
            return {
                "reason": "ungrounded_evidence_paths",
                "missing_paths": missing_paths[:8],
                "visible_files": visible_files[:8],
                "dependency_artifacts": dependency_artifacts[:8],
            }

        empty_claim_markers = (
            "workspace vacío",
            "workspace vacio",
            "sin archivos de proyecto",
            "no hay artefactos",
            "no se han generado artefactos",
            "ausencia total de base de código",
            "ausencia total de base de codigo",
        )
        if any(marker in lower for marker in empty_claim_markers) and (
            visible_files or dependency_artifacts
        ):
            return {
                "reason": "ungrounded_evidence_state",
                "visible_files": visible_files[:8],
                "dependency_artifacts": dependency_artifacts[:8],
            }
        return {}

    def _fail_task_for_ungrounded_evidence_output(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        workspace: Path,
        session,
    ) -> bool:
        issue = self._detect_ungrounded_evidence_issue(
            task=task,
            safe_content=safe_content,
            workspace=workspace,
        )
        if not issue:
            return False

        phase_name = self._phase_name_for_task(task)
        summary_bits = [str(issue.get("reason", "") or "ungrounded_evidence_output")]
        missing_paths = list(issue.get("missing_paths", []) or [])
        if missing_paths:
            summary_bits.append("missing=" + ", ".join(missing_paths[:4]))
        visible_files = list(issue.get("visible_files", []) or [])
        if visible_files:
            summary_bits.append("visible=" + ", ".join(visible_files[:4]))
        dependency_artifacts = list(issue.get("dependency_artifacts", []) or [])
        if dependency_artifacts:
            summary_bits.append("dependency_artifacts=" + ", ".join(dependency_artifacts[:4]))
        summary = " | ".join(summary_bits)

        diagnostic = (
            f"[PHASE_VERDICT]\n"
            f"phase_id: {phase_name}\n"
            "status: failed\n"
            "reason_codes: ungrounded_evidence\n"
            "contract_status: drift\n"
            "slice_id: \n"
            f"summary: {summary}\n"
            "[/PHASE_VERDICT]\n"
            "La salida afirmo evidencia o estado del workspace que no se pudo corroborar "
            "contra los archivos visibles y los artefactos de dependencias completadas."
        )
        task.metadata["_last_agent_output"] = safe_content
        self._append_agent_output_history(task, safe_content)
        task.metadata["ungrounded_evidence_issue"] = dict(issue)
        self._update_workflow_state(self._task_root(task.task_id), phase_name, diagnostic)
        error = f"ungrounded_evidence_output_detected: {summary}"
        self.taskboard.mark_failed(task.task_id, error=error)
        self.event_logger.emit(
            "ungrounded_evidence_output_detected",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                **dict(issue),
            },
        )
        self.session_store.close_session(
            session,
            summary="ungrounded_evidence_output",
            status="failed",
        )
        return True

    def _detect_ungrounded_phase_block_issue(
        self,
        *,
        task: WorkTask,
        safe_content: str,
        workspace: Path,
    ) -> dict[str, Any]:
        if task.role in {Role.TEAM_LEAD, Role.SCOUT, Role.RESEARCHER}:
            return {}
        normalized_text = str(safe_content or "").strip()
        if not normalized_text:
            return {}
        lower = normalized_text.lower()
        project_workspace = self.project_root if self.project_root.exists() else workspace
        contradiction_markers = (
            "workspace vacío",
            "workspace vacio",
            "no hay artefactos",
            "no se han generado artefactos",
            "ausencia total de base de código",
            "ausencia total de base de codigo",
            "no hay evidencia ejecutable",
            "no se ha proporcionado",
            "no se han proporcionado",
            "no fueron proporcionados",
            "no son accesibles",
            "sin acceso a los artefactos",
            "sin acceso al codigo",
            "sin acceso al código",
            "necesito acceso a los artefactos",
            "imposible realizar la revisión sin acceso",
            "imposible realizar la revision sin acceso",
            "no tengo acceso a los artefactos",
            "no se ha proporcionado el código fuente",
            "no se ha proporcionado el codigo fuente",
        )
        if any(marker in lower for marker in contradiction_markers):
            visible_files = self._workspace_visible_project_files(project_workspace, limit=12)
            dependency_artifacts = self._dependency_artifact_hints(task)
            if visible_files or dependency_artifacts:
                return {
                    "reason": "ungrounded_phase_block",
                    "visible_files": visible_files[:8],
                    "dependency_artifacts": dependency_artifacts[:8],
                }

        dependency_state_markers = (
            "dependency pending",
            "dependencia pendiente",
            "estado pending",
            "status pending",
            "depends on",
            "depende de",
            "upstream",
        )
        if not task.dependencies or not any(marker in lower for marker in dependency_state_markers):
            return {}

        dependency_states: list[str] = []
        dependency_artifacts: list[str] = []
        for dep_id in list(task.dependencies or []):
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None:
                continue
            dependency_states.append(str(dep_task.state.value))
            dependency_artifacts.extend(self._dependency_artifact_hints(dep_task))

        normalized_dependency_states = {
            str(item or "").strip().lower()
            for item in dependency_states
            if str(item or "").strip()
        }
        if normalized_dependency_states and normalized_dependency_states.issubset({"completed", "approved"}):
            return {
                "reason": "stale_dependency_block",
                "dependency_states": sorted(normalized_dependency_states),
                "dependency_artifacts": list(dict.fromkeys(dependency_artifacts))[:8],
                "visible_files": self._workspace_visible_project_files(project_workspace, limit=8),
            }
        return {}

    def _sanitize_peer_consultation_report(
        self,
        *,
        task: WorkTask,
        report: PeerConsultationReport,
    ) -> PeerConsultationReport:
        raw_text = str(getattr(report, "text", "") or "").strip()
        if not raw_text:
            return report

        project_workspace = self.project_root if self.project_root.exists() else self.runtime_dir
        visible_files = self._workspace_visible_project_files(project_workspace, limit=8)
        dependency_artifacts = self._dependency_artifact_hints(task)
        dependency_states: set[str] = set()
        for dep_id in list(task.dependencies or []):
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None:
                continue
            dep_state = str(getattr(dep_task.state, "value", dep_task.state) or "").strip().lower()
            if dep_state:
                dependency_states.add(dep_state)
        dependencies_completed = bool(dependency_states) and dependency_states.issubset(
            {"completed", "approved"}
        )

        workspace_contradiction_markers = (
            "workspace vacío",
            "workspace vacio",
            "workspace empty",
            "no hay artefactos",
            "no se han generado artefactos",
            "ausencia total de base de código",
            "ausencia total de base de codigo",
            "no hay repositorio git",
            "no existe repositorio git",
            "sin repositorio git",
            "no existen los archivos",
            "no existen archivos",
            "no hay archivos",
            "allowed_module_scope",
        )
        stale_dependency_markers = (
            "dependency pending",
            "dependencia pendiente",
            "estado pending",
            "status pending",
            "sigue pending",
            "está pending",
            "esta pending",
            "blocked by upstream",
            "bloqueado por upstream",
            "bloqueada por upstream",
            "esperando upstream",
            "waiting on upstream",
        )

        filtered_lines: list[str] = []
        dropped_lines: list[str] = []
        for line in raw_text.splitlines():
            normalized_line = str(line or "").strip()
            if not normalized_line:
                continue
            lower_line = normalized_line.lower()

            contradicts_workspace = (
                (visible_files or dependency_artifacts)
                and any(marker in lower_line for marker in workspace_contradiction_markers)
            )
            contradicts_dependencies = dependencies_completed and any(
                marker in lower_line for marker in stale_dependency_markers
            )
            if contradicts_workspace or contradicts_dependencies:
                dropped_lines.append(normalized_line)
                continue
            filtered_lines.append(normalized_line)

        if not dropped_lines:
            return report

        authoritative_note = (
            "- sistema: se ignoraron aportes entre pares que contradicen el estado "
            "autoritativo actual del workspace o dependencias ya completadas."
        )
        if authoritative_note not in filtered_lines:
            filtered_lines.append(authoritative_note)

        self.event_logger.emit(
            "peer_context_filtered",
            {
                "task_id": task.task_id,
                "phase": self._phase_name_for_task(task),
                "role": task.role.value,
                "dropped_lines": dropped_lines[:6],
                "visible_files": visible_files[:6],
                "dependency_artifacts": dependency_artifacts[:6],
                "dependency_states": sorted(dependency_states),
            },
        )
        if self.taskboard.get_task(task.task_id) is not None:
            self.taskboard.update_metadata(
                task.task_id,
                {
                    "peer_context_filtered": True,
                    "peer_context_filtered_lines": dropped_lines[:6],
                },
            )
        return PeerConsultationReport(
            text="\n".join(filtered_lines),
            consulted_roles=list(report.consulted_roles or []),
            unavailable_roles=list(report.unavailable_roles or []),
            consulted_providers=list(report.consulted_providers or []),
        )

    def _block_task_for_missing_dependency_delivery(
        self,
        task: WorkTask,
        assignee: str,
        missing_dependencies: list[dict[str, str]],
    ) -> bool:
        if not missing_dependencies:
            return False

        self.taskboard.mark_blocked(task.task_id, reason="missing_dependency_artifacts")
        self.taskboard.update_metadata(
            task.task_id,
            {
                "blocked_dependencies": [
                    item.get("dependency_task_id", "") for item in missing_dependencies
                ],
                "blocked_missing_artifacts": missing_dependencies,
            },
        )
        self.event_logger.emit(
            "dependency_artifact_preflight_blocked",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "assignee": assignee,
                "missing_dependencies": missing_dependencies,
            },
        )
        self._emit_agent_event(
            {
                "type": "agent_blocked",
                "task_id": task.task_id,
                "agent_id": assignee,
                "role": task.role.value,
                "phase": self._phase_name_for_task(task),
                "reason": "missing_dependency_artifacts",
            }
        )
        return True

    def _runtime_phase_contract_objective(
        self,
        *,
        phase_id: str,
        role_upper: str,
        objective: str,
        task: WorkTask,
    ) -> str:
        resolved = str(objective or "").strip()
        if not is_missing_contract_objective(resolved):
            return resolved

        delegation_brief = str(task.metadata.get("delegation_brief", "") or "").strip()
        is_chat_phase = bool(task.metadata.get("phase_contract_enforced")) or bool(
            task.metadata.get("chat_parent")
        ) or self._task_root(task.task_id).startswith("CHAT-")
        if not is_chat_phase and not is_missing_contract_objective(delegation_brief):
            return delegation_brief
        return ""

    def _missing_phase_contract_objective_details(self, task: WorkTask) -> list[str]:
        if task.role == Role.TEAM_LEAD:
            return []

        task_root = self._task_root(task.task_id)
        ws = self._get_workflow_state(task_root)
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        workflow_contracts = dict(ws.get("phase_contracts", {}) or {})
        phase_id = str(
            phase_contract.get("phase_id")
            or task.metadata.get("phase")
            or (task.task_id.split("::")[-1] if "::" in task.task_id else "")
            or task.title
        ).strip()
        is_contract_bound = bool(task.metadata.get("phase_contract_enforced")) or bool(
            phase_contract
        ) or phase_id in workflow_contracts
        if not is_contract_bound:
            return []

        workflow_contract = dict(workflow_contracts.get(phase_id, {}) or {})
        objective_candidates = [
            ("task.metadata.phase_contract.objective", phase_contract.get("objective")),
            ("workflow_state.phase_contracts.objective", workflow_contract.get("objective")),
        ]
        if any(not is_missing_contract_objective(value) for _, value in objective_candidates):
            return []

        return [
            f"phase={phase_id or 'unknown'}",
            f"role={task.role.value}",
            f"task_root={task_root or 'unknown'}",
            "objective missing or placeholder in phase contract",
        ]

    def _build_runtime_phase_contract_block(self, task: WorkTask) -> str:
        task_root = self._task_root(task.task_id)
        ws = self.workflow_state.get(task_root, {})
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        workflow_contracts = dict(ws.get("phase_contracts", {}) or {})

        phase_id = str(
            phase_contract.get("phase_id")
            or task.metadata.get("phase")
            or (task.task_id.split("::")[-1] if "::" in task.task_id else "")
            or task.title
        ).strip()
        if not phase_id:
            return ""

        workflow_contract = dict(workflow_contracts.get(phase_id, {}) or {})
        role_upper = str(
            phase_contract.get("role") or workflow_contract.get("role") or task.role.name
        ).strip().upper()

        objective = self._runtime_phase_contract_objective(
            phase_id=phase_id,
            role_upper=role_upper,
            objective=str(
                phase_contract.get("objective")
                or workflow_contract.get("objective")
                or ""
            ).strip(),
            task=task,
        )
        objective_missing = is_missing_contract_objective(objective)
        objective_display = (
            f"[CONTRATO INVALIDO: objective ausente para '{phase_id}']"
            if objective_missing
            else objective
        )

        depends_on_raw = (
            phase_contract.get("depends_on")
            or workflow_contract.get("depends_on")
            or []
        )
        depends_on = [str(dep).strip() for dep in list(depends_on_raw or []) if str(dep).strip()]
        if not depends_on and task.dependencies:
            inferred_depends: list[str] = []
            for dep_id in task.dependencies:
                dep_task = self.taskboard.get_task(dep_id)
                dep_phase = str(
                    (dep_task.metadata.get("phase", "") if dep_task is not None else "")
                    or (str(dep_id).split("::")[-1] if "::" in str(dep_id) else "")
                ).strip()
                if dep_phase:
                    inferred_depends.append(dep_phase)
            depends_on = inferred_depends[:4]

        phase_outputs = dict(ws.get("phase_outputs", {}) or {})
        phase_summaries = dict(ws.get("phase_context_summaries", {}) or {})
        upstream_lines: list[str] = []
        for dep in depends_on[:4]:
            dep_task_id = f"{task_root}::{dep}" if task_root else dep
            dep_task = self.taskboard.get_task(dep_task_id)
            dep_summary = str(phase_summaries.get(dep, "") or "").strip()
            dep_output = str(phase_outputs.get(dep, "") or "").strip()
            dep_contract = dict(workflow_contracts.get(dep, {}) or {})
            dep_objective = str(dep_contract.get("objective", "") or "").strip()
            dep_planning_artifact = {}
            if dep_task is not None:
                dep_planning_artifact = dict(
                    dep_task.metadata.get("planning_artifact", {})
                    or dict(ws.get("planning_artifacts", {}) or {}).get(dep, {})
                    or {}
                )
            artifact_paths = [
                str(item).strip()
                for item in list(
                    (dep_task.metadata.get("artifact_paths", []) if dep_task is not None else [])
                    or []
                )
                if str(item).strip()
            ]
            state_bits: list[str] = []
            if dep_task is not None:
                dep_state = getattr(dep_task.state, "value", str(dep_task.state))
                if dep_state:
                    state_bits.append(f"state={dep_state}")
            if artifact_paths:
                state_bits.append(f"artifacts={', '.join(artifact_paths[:4])}")
            dep_planning_context = self._format_dependency_planning_artifact(
                dep_planning_artifact,
                limit=1400 if role_upper == "ENGINEER" else 650,
            )
            dep_context = (
                dep_planning_context
                or dep_summary
                or self._compact_text(dep_output, 280)
                or dep_objective
            )
            dep_prefix = f"{dep}:"
            if dep_context.lower().startswith(dep_prefix.lower()):
                dep_context = dep_context[len(dep_prefix):].strip()
            if state_bits:
                dep_context = ("; ".join(state_bits) + (f"; {dep_context}" if dep_context else "")).strip()
            if dep_context:
                upstream_lines.append(f"- {dep}: {dep_context}")

        if objective_missing and role_upper != "TEAM_LEAD":
            role_guidance = (
                "Contrato invalido: objective ausente. No infieras el objetivo por nombre de fase, "
                "contexto o memoria heredada. Declara bloqueo contractual y pide replanificacion del Lead."
            )
        else:
            validation_guidance = (
                "Valida estrictamente si lo ejecutado respeta este contrato. Si detectas deriva, "
                "declárala explícitamente. No afirmes tests, cobertura, rutas o artefactos que no "
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
                "de mayor impacto sin una nueva directiva del Lead. Si tus dependencias estan "
                "completed y el upstream_context contiene planning_artifact con objective, steps "
                "y acceptance, ese artefacto estructurado es suficiente autoridad para ejecutar; "
                "no bloquees por narrativa resumida, elipsis o falta de transcripcion completa."
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
        if upstream_lines:
            lines.append("upstream_context:")
            lines.extend(upstream_lines)
        lines.append("[/PHASE_CONTRACT]")
        return "\n".join(lines)

    def _retry_executor_on_stale_dependency_block(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        issue: dict[str, Any],
        session,
    ) -> bool:
        if task.role not in {Role.ENGINEER, Role.REVIEWER, Role.QA}:
            return False
        if str(issue.get("reason", "") or "").strip().lower() != "stale_dependency_block":
            return False
        retry_count = int(task.metadata.get("stale_dependency_block_retry_count", 0) or 0)
        max_retries = int(task.metadata.get("max_stale_dependency_block_retries", 1) or 1)
        if retry_count >= max_retries:
            return False

        completed_artifacts = self._completed_dependency_planning_artifacts(task)
        dependency_artifacts = list(issue.get("dependency_artifacts", []) or [])
        visible_files = list(issue.get("visible_files", []) or [])
        if not completed_artifacts and not dependency_artifacts and not visible_files:
            return False

        artifact_hint = ""
        if completed_artifacts:
            dep_phase, artifact = completed_artifacts[0]
            artifact_hint = self._format_dependency_planning_artifact(artifact, limit=1200)
            artifact_hint = f"Planning autoritativo de {dep_phase}: {artifact_hint}"
        elif dependency_artifacts:
            artifact_hint = "Artefactos upstream visibles: " + ", ".join(
                str(item).strip() for item in dependency_artifacts[:6] if str(item).strip()
            )
        else:
            artifact_hint = "Archivos visibles del workspace: " + ", ".join(
                str(item).strip() for item in visible_files[:6] if str(item).strip()
            )

        feedback = (
            "Retry automatico por bloqueo stale_dependency_block. Las dependencias requeridas "
            "ya estan completed; no bloquees por elipsis, resumen parcial o falta de transcripcion "
            "literal si existe evidencia estructurada o archivos visibles. Ejecuta/valida el slice "
            "minimo dentro del contrato vigente.\n"
            f"{artifact_hint}"
        )
        task.metadata["result"] = safe_content
        task.metadata["_last_agent_output"] = safe_content
        self._append_agent_output_history(task, safe_content)
        task.metadata["review_feedback"] = feedback
        task.metadata["gate_iteration"] = int(task.metadata.get("gate_iteration", 0) or 0) + 1
        task.metadata["stale_dependency_block_retry_count"] = retry_count + 1
        task.metadata["stale_dependency_block_issue"] = dict(issue)
        self.taskboard.retry_task(
            task.task_id,
            reason=f"stale_dependency_block_retry_{retry_count + 1}",
        )
        self.event_logger.emit(
            "stale_dependency_block_retried",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": self._phase_name_for_task(task),
                "retry_count": retry_count + 1,
                "has_planning_artifact": bool(completed_artifacts),
                "visible_files": visible_files[:6],
                "dependency_artifacts": dependency_artifacts[:6],
            },
        )
        self.session_store.close_session(
            session,
            summary="stale_dependency_block_retry",
            status="retried",
        )
        return True

    def _retry_executor_on_recoverable_ungrounded_phase_block(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        issue: dict[str, Any],
        session,
    ) -> bool:
        if task.role not in {Role.REVIEWER, Role.QA}:
            return False
        if str(issue.get("reason", "") or "").strip().lower() != "ungrounded_phase_block":
            return False
        retry_count = int(task.metadata.get("ungrounded_phase_block_retry_count", 0) or 0)
        max_retries = int(task.metadata.get("max_ungrounded_phase_block_retries", 1) or 1)
        if retry_count >= max_retries:
            return False
        visible_files = [
            str(item).strip()
            for item in list(issue.get("visible_files", []) or [])
            if str(item).strip()
        ]
        dependency_artifacts = [
            str(item).strip()
            for item in list(issue.get("dependency_artifacts", []) or [])
            if str(item).strip()
        ]
        if not visible_files and not dependency_artifacts:
            return False

        artifact_hint = []
        if dependency_artifacts:
            artifact_hint.append(
                "Artefactos/scope revisables: " + ", ".join(dependency_artifacts[:8])
            )
        if visible_files:
            artifact_hint.append(
                "Archivos visibles del workspace: " + ", ".join(visible_files[:8])
            )
        feedback = (
            "Retry automatico por bloqueo de visibilidad no fundamentado. "
            "No afirmes que faltan artefactos/codigo si hay archivos visibles, "
            "workspace_artifact_hints o allowed_module_path_hints. Inspecciona o revisa "
            "contra esos paths y solo bloquea si el problema permanece tras usar esa evidencia.\n"
            + "\n".join(artifact_hint)
        )
        task.metadata["result"] = safe_content
        task.metadata["_last_agent_output"] = safe_content
        self._append_agent_output_history(task, safe_content)
        task.metadata["review_feedback"] = feedback
        task.metadata["gate_iteration"] = int(task.metadata.get("gate_iteration", 0) or 0) + 1
        task.metadata["ungrounded_phase_block_retry_count"] = retry_count + 1
        task.metadata["ungrounded_phase_block_retry_issue"] = dict(issue)
        self.taskboard.retry_task(
            task.task_id,
            reason=f"ungrounded_phase_block_retry_{retry_count + 1}",
        )
        self.event_logger.emit(
            "ungrounded_phase_block_retried",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": self._phase_name_for_task(task),
                "retry_count": retry_count + 1,
                "visible_files": visible_files[:8],
                "dependency_artifacts": dependency_artifacts[:8],
            },
        )
        self.session_store.close_session(
            session,
            summary="ungrounded_phase_block_retry",
            status="retried",
        )
        return True

    def _complete_research_phase_as_degraded(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        verdict_status: str,
        session,
    ) -> None:
        """Researcher is support: report degraded evidence, do not block the workflow."""
        phase_name = self._phase_name_for_task(task)
        status = str(verdict_status or "").strip().lower() or "partial"
        self._update_workflow_state(self._task_root(task.task_id), phase_name, safe_content)
        self.taskboard.mark_completed(task.task_id, details=safe_content)
        refreshed = self.taskboard.get_task(task.task_id) or task
        self._append_agent_output_history(refreshed, safe_content)
        self.taskboard.update_metadata(
            task.task_id,
            {
                "research_degraded": True,
                "research_self_reported_status": status,
                "research_degraded_reason": "support_phase_self_reported_block",
                "result": safe_content,
                "_last_agent_output": safe_content,
            },
        )
        self.event_logger.emit(
            "research_phase_degraded_completed",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "reported_status": status,
            },
        )
        self.session_store.close_session(
            session,
            summary=f"research_degraded:{status}",
            status="completed",
        )

    def _complete_validation_visibility_issue_as_degraded(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        issue: dict[str, Any],
        session,
    ) -> bool:
        if task.role not in {Role.REVIEWER, Role.QA}:
            return False
        if str(issue.get("reason", "") or "").strip().lower() != "ungrounded_phase_block":
            return False
        visible_files = [
            str(item).strip()
            for item in list(issue.get("visible_files", []) or [])
            if str(item).strip()
        ]
        dependency_artifacts = [
            str(item).strip()
            for item in list(issue.get("dependency_artifacts", []) or [])
            if str(item).strip()
        ]
        if not visible_files and not dependency_artifacts:
            return False

        phase_name = self._phase_name_for_task(task)
        evidence_lines: list[str] = []
        if visible_files:
            evidence_lines.append("visible_files: " + ", ".join(visible_files[:8]))
        if dependency_artifacts:
            evidence_lines.append(
                "dependency_artifacts: " + ", ".join(dependency_artifacts[:8])
            )
        degraded_content = "\n".join(
            [
                "[PHASE_VERDICT]",
                f"phase_id: {phase_name}",
                "status: completed",
                "contract_status: completed",
                "reason_codes: validation_visibility_degraded",
                (
                    "summary: Validacion degradada: el agente reporto falta de visibilidad, "
                    "pero el runtime detecto evidencia revisable."
                ),
                "[/PHASE_VERDICT]",
                "[DEGRADED_VALIDATION_REPORT]",
                "decision_authority: team_lead",
                "degraded_reason: validation_visibility_degraded",
                *evidence_lines,
                "note: No se trata como rechazo de producto; queda como senal para el Lead.",
                "[/DEGRADED_VALIDATION_REPORT]",
            ]
        )

        self._update_workflow_state(self._task_root(task.task_id), phase_name, degraded_content)
        self.taskboard.mark_completed(task.task_id, details=degraded_content)
        refreshed = self.taskboard.get_task(task.task_id) or task
        self._append_agent_output_history(refreshed, safe_content)
        self.taskboard.update_metadata(
            task.task_id,
            {
                "validation_degraded": True,
                "validation_degraded_reason": "ungrounded_visibility_block",
                "ungrounded_phase_block_issue": dict(issue),
                "validation_original_output_preview": safe_content[:4000],
                "result": degraded_content,
                "_last_agent_output": safe_content,
            },
        )
        self.event_logger.emit(
            "validation_visibility_issue_degraded",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "role": task.role.value,
                "visible_files": visible_files[:8],
                "dependency_artifacts": dependency_artifacts[:8],
            },
        )
        self.session_store.close_session(
            session,
            summary="validation_visibility_degraded",
            status="completed",
        )
        return True

    def _resolved_phase_objective_for_task(self, task: WorkTask) -> str:
        task_root = self._task_root(task.task_id)
        ws = self.workflow_state.get(task_root, {})
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        workflow_contracts = dict(ws.get("phase_contracts", {}) or {})
        phase_id = str(
            phase_contract.get("phase_id")
            or task.metadata.get("phase")
            or (task.task_id.split("::")[-1] if "::" in task.task_id else "")
            or task.title
        ).strip()
        if not phase_id:
            return ""
        workflow_contract = dict(workflow_contracts.get(phase_id, {}) or {})
        role_upper = str(
            phase_contract.get("role") or workflow_contract.get("role") or task.role.name
        ).strip().upper()
        return self._runtime_phase_contract_objective(
            phase_id=phase_id,
            role_upper=role_upper,
            objective=str(
                phase_contract.get("objective")
                or workflow_contract.get("objective")
                or ""
            ).strip(),
            task=task,
        )

    @staticmethod
    def _format_phase_verdict_block(verdict: dict[str, Any]) -> str:
        phase_id = str(verdict.get("phase_id", "") or "").strip()
        status = str(verdict.get("status", "") or "").strip()
        contract_status = str(verdict.get("contract_status", "") or "").strip()
        reason_codes = ", ".join(
            str(item).strip()
            for item in list(verdict.get("reason_codes", []) or [])
            if str(item).strip()
        )
        slice_id = str(verdict.get("slice_id", "") or "").strip()
        summary = str(verdict.get("summary", "") or "").strip()
        return (
            "[PHASE_VERDICT]\n"
            f"phase_id: {phase_id}\n"
            f"status: {status}\n"
            f"reason_codes: {reason_codes}\n"
            f"contract_status: {contract_status}\n"
            f"slice_id: {slice_id}\n"
            f"summary: {summary}\n"
            "[/PHASE_VERDICT]"
        )

    def _fail_task_for_continuation_drift(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        session,
    ) -> bool:
        phase_name = self._phase_name_for_task(task).lower()
        if (
            task.role != Role.ENGINEER
            and not bool(task.metadata.get("direct_coding_executor", False))
        ) or phase_name != "build":
            return False

        objective = self._resolved_phase_objective_for_task(task)
        drift = detect_continuation_drift(
            objective=objective,
            output_text=safe_content,
        )
        if not drift:
            return False

        expected_hints = list(drift.get("expected_path_hints", []) or [])
        proposed_paths = list(drift.get("proposed_paths", []) or [])
        if self._repair_first_context_allows_continuation_paths(task, proposed_paths):
            self.event_logger.emit(
                "continuation_drift_suppressed",
                {
                    "task_id": task.task_id,
                    "assignee": assignee,
                    "phase": phase_name,
                    "reason": "repair_first_validation_context_allows_path",
                    "expected_path_hints": expected_hints[:8],
                    "proposed_paths": proposed_paths[:8],
                },
            )
            return False
        verdict_payload = {
            "phase_id": str(drift.get("phase_id", phase_name) or phase_name),
            "status": str(drift.get("status", "rejected") or "rejected"),
            "contract_status": str(drift.get("contract_status", "drift") or "drift"),
            "reason_codes": list(drift.get("reason_codes", []) or []),
            "summary": str(drift.get("summary", "") or "").strip(),
        }
        diagnostic = (
            f"{self._format_phase_verdict_block(verdict_payload)}\n"
            "Continuation drift detectada antes de aplicar cambios.\n"
            f"Objetivo vigente: {objective}\n"
            f"Paths esperados: {', '.join(expected_hints[:6]) or 'sin pistas explicitas'}\n"
            f"Paths propuestos: {', '.join(proposed_paths[:6]) or 'sin paths detectados'}"
        )
        self._snapshot_agent_output_on_task(task=task, safe_content=safe_content)
        task.metadata["continuation_drift_expected_paths"] = expected_hints
        task.metadata["continuation_drift_proposed_paths"] = proposed_paths
        task.metadata["continuation_drift_summary"] = verdict_payload["summary"]
        task_root = self._task_root(task.task_id)
        self._update_workflow_state(task_root, phase_name, diagnostic)

        error = (
            "continuation_drift_detected: "
            f"expected={', '.join(expected_hints[:4]) or 'unknown'} "
            f"proposed={', '.join(proposed_paths[:4]) or 'unknown'}"
        )
        self.taskboard.mark_failed(task.task_id, error=error)
        self._maybe_spawn_lead_failure_checkpoint(task, error)
        self._maybe_run_event_meeting(
            trigger="task_failed",
            task_id=task.task_id,
            reason=error,
        )
        self.event_logger.emit(
            "continuation_drift_detected",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "expected_path_hints": expected_hints[:8],
                "proposed_paths": proposed_paths[:8],
                "objective": objective[:240],
            },
        )
        self.session_store.close_session(
            session,
            summary=f"continuation_drift:{verdict_payload['summary']}",
            status="failed",
        )
        return True

    @staticmethod
    def _repair_first_context_allows_continuation_paths(
        task: WorkTask,
        proposed_paths: list[str],
    ) -> bool:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if not bool(metadata.get("repair_first_required", False)):
            return False
        origin = str(metadata.get("repair_first_origin", "") or "").strip().lower()
        if origin and not origin.startswith("auto_"):
            return False

        context_parts = [
            str(task.description or ""),
            str(metadata.get("review_feedback", "") or ""),
            str(metadata.get("repair_first_command", "") or ""),
        ]
        phase_contract = metadata.get("phase_contract", {})
        if isinstance(phase_contract, dict):
            context_parts.append(str(phase_contract.get("objective", "") or ""))
            context_parts.append(
                str(phase_contract.get("repair_first_original_objective", "") or "")
            )
        auto_pre = metadata.get("auto_pre_validation_result", {})
        if isinstance(auto_pre, dict):
            context_parts.append(str(auto_pre.get("command", "") or ""))
            context_parts.append(str(auto_pre.get("reason", "") or ""))
        context = "\n".join(part for part in context_parts if part).lower()
        if not context.strip():
            return False

        context_paths = extract_path_candidates(context)
        for proposed in list(proposed_paths or []):
            normalized = str(proposed or "").strip().replace("\\", "/").lower()
            if not normalized:
                continue
            if detect_continuation_drift(
                objective="\n".join(context_paths),
                proposed_paths=[normalized],
            ) == {} and context_paths:
                return True
            if normalized.startswith("src/") and normalized.endswith(".py"):
                module_name = normalized[:-3].replace("/", ".")
                module_without_src = module_name[4:] if module_name.startswith("src.") else module_name
                basename = normalized.rsplit("/", 1)[-1][:-3]
                if (
                    "importerror" in context
                    or "cannot import name" in context
                    or "no module named" in context
                ) and (
                    module_name in context
                    or module_without_src in context
                    or normalized in context
                    or basename in context
                ):
                    return True
        return False

    def _fail_task_for_contract_path_drift(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        session,
    ) -> bool:
        if task.role != Role.ENGINEER and not bool(
            task.metadata.get("direct_coding_executor", False)
        ):
            return False
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        proposed_paths = extract_path_candidates(safe_content)
        drift = detect_contract_path_drift(
            proposed_paths=proposed_paths,
            forbidden_path_hints=list(phase_contract.get("forbidden_path_hints", []) or []),
            allowed_module_path_hints=list(
                phase_contract.get("allowed_module_path_hints", []) or []
            ),
        )
        if not drift:
            return False

        phase_name = self._phase_name_for_task(task).lower()
        verdict_payload = {
            "phase_id": str(drift.get("phase_id", phase_name) or phase_name),
            "status": str(drift.get("status", "rejected") or "rejected"),
            "contract_status": str(drift.get("contract_status", "drift") or "drift"),
            "reason_codes": list(drift.get("reason_codes", []) or []),
            "summary": str(drift.get("summary", "") or "").strip(),
        }
        diagnostic = (
            f"{self._format_phase_verdict_block(verdict_payload)}\n"
            "Contract path drift detectada antes de escribir archivos.\n"
            f"Propuesta: {', '.join(list(drift.get('proposed_paths', []) or [])[:6])}"
        )
        self._snapshot_agent_output_on_task(task=task, safe_content=safe_content)
        task.metadata["contract_path_drift_summary"] = verdict_payload["summary"]
        task.metadata["contract_path_drift_proposed_paths"] = list(
            drift.get("proposed_paths", []) or []
        )
        self._update_workflow_state(
            self._task_root(task.task_id),
            phase_name,
            diagnostic,
        )
        self.taskboard.mark_failed(
            task.task_id,
            error=f"contract_path_drift_detected: {verdict_payload['summary']}",
        )
        self.event_logger.emit(
            "contract_path_drift_detected",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "summary": verdict_payload["summary"],
                "proposed_paths": list(drift.get("proposed_paths", []) or []),
            },
        )
        self._maybe_spawn_lead_failure_checkpoint(task, verdict_payload["summary"])
        self._maybe_run_event_meeting(
            trigger="task_failed",
            task_id=task.task_id,
            reason="contract_path_drift",
        )
        self.session_store.close_session(
            session,
            summary=f"contract_path_drift:{verdict_payload['summary']}",
            status="failed",
        )
        return True

    def _fail_task_for_planning_phase_implementation_drift(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        session,
    ) -> bool:
        phase_name = self._phase_name_for_task(task).lower()
        if not phase_name.startswith("plan_"):
            return False

        raw_text = str(safe_content or "")
        has_code_block = "```" in raw_text
        has_path_annotation = bool(
            re.search(r"\bpath\s*=\s*[\w./\\\\-]+", raw_text, re.IGNORECASE)
        )
        has_shell_command = bool(_EXECUTION_LINE_COMMAND_RE.search(raw_text))
        has_write_or_file_action = bool(
            re.search(
                r"(?i)\b(?:crear|create|modificar|modify|editar|edit|escribir|write|guardar|save|implementar|implement)\b.{0,80}\b(?:archivo|file|modulo|m[oó]dulo|src/|tests/)\b",
                raw_text,
            )
        )
        has_concrete_path_action = bool(
            re.search(
                r"(?i)\b(?:crear|create|modificar|modify|editar|edit|escribir|write|guardar|save|mover|move|renombrar|rename|extraer|extract|separar|split|aislar|isolate)\b.{0,120}\b(?:src|tests|docs|api|config|scripts)/[\w./-]+",
                raw_text,
            )
        )
        has_runtime_file_path = bool(
            re.search(r"(?i)\b(?:src|tests|docs|api|config|scripts)/[\w./-]+", raw_text)
        )
        if not (
            has_code_block
            or has_path_annotation
            or (
                phase_name == "plan_risks"
                and (
                    has_shell_command
                    or has_write_or_file_action
                    or has_concrete_path_action
                )
            )
        ):
            return False

        summary = (
            "planning phase must stay at plan level and cannot emit code blocks or path= annotations"
        )
        if phase_name == "plan_risks":
            summary = (
                "plan_risks must stay at risk/gate/test level and cannot emit implementation commands, file actions or concrete source paths"
            )
        self._snapshot_agent_output_on_task(task=task, safe_content=safe_content)
        diagnostic = (
            "[PHASE_VERDICT]\n"
            f"phase_id: {phase_name}\n"
            "status: rejected\n"
            "reason_codes: planning_phase_scope_drift\n"
            "contract_status: drift\n"
            "slice_id: \n"
            f"summary: {summary}\n"
            "[/PHASE_VERDICT]\n"
            "La fase de planning intento emitir implementacion concreta en lugar de "
            "corte, tareas y criterios."
        )
        self._update_workflow_state(
            self._task_root(task.task_id),
            phase_name,
            diagnostic,
        )
        self.taskboard.mark_failed(
            task.task_id,
            error=f"planning_phase_scope_drift_detected: {summary}",
        )
        self.event_logger.emit(
            "planning_phase_scope_drift_detected",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
                "summary": summary,
            },
        )
        self._maybe_spawn_lead_failure_checkpoint(task, summary)
        self._maybe_run_event_meeting(
            trigger="task_failed",
            task_id=task.task_id,
            reason="planning_phase_scope_drift",
        )
        self.session_store.close_session(
            session,
            summary=f"planning_phase_scope_drift:{summary}",
            status="failed",
        )
        return True

    @staticmethod
    def _sanitize_planning_phase_output(content: str) -> tuple[str, bool]:
        """Elimina code fences (``` ... ```) del output de una fase plan_*.

        Devuelve (contenido_sanitizado, fue_modificado).
        Los agentes de planning no deben emitir código; si lo hacen, lo stripemos
        programáticamente en lugar de depender solo de prompts o de fallar la fase.
        """
        sanitized = re.sub(r"```[^\n]*\n.*?```", "", content, flags=re.DOTALL)
        sanitized = re.sub(r"```[^\n]*```", "", sanitized)  # inline fences
        return sanitized, sanitized != content

    @staticmethod
    def _sanitize_plan_risks_output(content: str) -> tuple[str, bool]:
        """Reduce `plan_risks` a contenido de riesgo/gates/tests y un verdict estructurado.

        El modelo a veces mezcla un [PHASE_VERDICT] valido con lineas narrativas
        que ya entran en comandos, acciones de archivo o rutas concretas. En vez
        de matar la fase de inmediato, limpiamos esas lineas y conservamos la
        parte estructurada.
        """
        raw = str(content or "")
        if not raw.strip():
            return raw, False

        verdict_blocks = re.findall(
            r"(?is)\[PHASE_VERDICT\].*?\[/PHASE_VERDICT\]",
            raw,
        )
        placeholder = "\n".join(verdict_blocks)
        body = re.sub(r"(?is)\[PHASE_VERDICT\].*?\[/PHASE_VERDICT\]", "", raw)

        sanitized_lines: list[str] = []
        changed = False
        action_verb_re = re.compile(
            r"(?i)\b(?:crear|create|modificar|modify|editar|edit|escribir|write|guardar|save|mover|move|renombrar|rename|extraer|extract|separar|split|aislar|isolate|implementar|implement)\b"
        )
        operational_line_re = re.compile(
            r"(?i)(?:"
            r"\bpython\s+-m\s+pytest\b|"
            r"\bpytest\b|"
            r"\bpath\s*=\s*[\w./\\\\-]+|"
            r"\b(?:src|tests|docs|api|config|scripts)/[\w./-]+"
            r")"
        )
        slash_phrase_re = re.compile(
            r"(?i)\b([a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)+)\b"
        )
        for line in body.splitlines():
            if operational_line_re.search(line) and not action_verb_re.search(line):
                changed = True
                continue
            rewritten = slash_phrase_re.sub(
                lambda match: (
                    match.group(1).replace("/", " or ")
                    if _looks_like_noise_path_hint(match.group(1))
                    else match.group(1)
                ),
                line,
            )
            if rewritten != line:
                changed = True
                line = rewritten
            sanitized_lines.append(line)

        sanitized_body = "\n".join(sanitized_lines).strip()
        parts = [part for part in (sanitized_body, placeholder.strip()) if part]
        sanitized = "\n\n".join(parts).strip()
        return sanitized, changed or sanitized != raw.strip()

    @staticmethod
    def _sanitize_review_output(content: str) -> tuple[str, bool]:
        """Reduce ruido tecnico no-ruta en outputs de review.

        Algunas respuestas de review incluyen literales regex o snippets raw entre
        backticks que no son evidencia material del repo y luego contaminan el
        grounding como si fueran paths. Aqui los reformulamos de forma generica
        sin tocar rutas reales, simbolos reales ni el veredicto estructurado.
        """
        raw = str(content or "")
        if not raw.strip():
            return raw, False

        regexish_inline_re = re.compile(
            r"`?(?:r|rf|fr|f)?['\"][^`\n]{0,200}(?:\^|\$|\[|\]|\(|\)|\{|\}|\*|\+|\?)[^`\n]{0,200}['\"]`?",
            re.IGNORECASE,
        )
        sanitized = regexish_inline_re.sub("patron tecnico", raw)
        return sanitized, sanitized != raw

    @staticmethod
    def _sanitize_lead_close_output(content: str) -> tuple[str, bool]:
        """Limpia marcadores editoriales blandos en sintesis de cierre.

        `lead_close` resume una corrida; no es un artefacto de producto ni una fase
        de implementacion. Marcadores blandos como `todo:` o `fixme:` no deben
        derribar una run avanzada si el resto del cierre es valido.
        """
        raw = str(content or "")
        if not raw.strip():
            return raw, False

        replacements = {
            r"\btodo:\s*": "pendiente editorial: ",
            r"\bfixme:\s*": "nota editorial: ",
            r"\btbd:\s*": "pendiente editorial: ",
            r"\bpending:\s*": "seguimiento: ",
            r"\bfollow[\s-]?up:\s*": "seguimiento: ",
        }
        sanitized = raw
        for pattern, replacement in replacements.items():
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
        return sanitized, sanitized != raw

    def _sanitize_test_runner_output(
        self,
        *,
        task: WorkTask,
        content: str,
        workspace: Path,
    ) -> tuple[str, bool]:
        """Evita que `test_runner` convierta nombres hipoteticos de tests en paths.

        Regla generica: si un reporte menciona nombres de test `.py` que no estan
        ni en el workspace visible ni en artefactos upstream, se reformulan como
        referencias genericas a tests visibles, sin inventar rutas.
        """
        raw = str(content or "")
        if not raw.strip():
            return raw, False

        specialist_name = str(task.metadata.get("tool_specialist", "") or "").strip().lower()
        if specialist_name != "test_runner":
            return raw, False

        project_workspace = self.project_root if self.project_root.exists() else workspace
        visible_files = self._workspace_grounding_visible_files(project_workspace, limit=24)
        dependency_artifacts = self._dependency_artifact_hints(task)
        known_test_paths = {
            str(item).strip().replace("\\", "/").lower()
            for item in list(visible_files or []) + list(dependency_artifacts or [])
            if str(item).strip().lower().endswith(".py")
            and ("/tests/" in f"/{str(item).strip().replace('\\', '/').lower()}" or str(item).strip().lower().startswith("tests/"))
        }
        known_test_basenames = {item.rsplit("/", 1)[-1] for item in known_test_paths}
        if not known_test_paths and not known_test_basenames:
            return raw, False

        pattern = re.compile(
            r"(?<![\w/.-])(?:(tests/)?([A-Za-z0-9_]+\.py))(?![\w/.-])",
            re.IGNORECASE,
        )

        def _looks_like_test_filename(name: str, has_tests_prefix: bool) -> bool:
            normalized = str(name or "").strip().lower()
            return bool(
                has_tests_prefix
                or normalized.startswith("test_")
                or normalized.endswith("_test.py")
                or normalized.endswith("_tests.py")
            )

        def _replace(match: re.Match[str]) -> str:
            tests_prefix = bool(str(match.group(1) or "").strip())
            basename = str(match.group(2) or "").strip().lower()
            if not _looks_like_test_filename(basename, tests_prefix):
                return match.group(0)
            full = str(match.group(0) or "").strip().replace("\\", "/").lower()
            normalized = full if full.startswith("tests/") else f"tests/{basename}"
            if normalized in known_test_paths or basename in known_test_basenames:
                return match.group(0)
            return "tests visibles del workspace"

        sanitized = pattern.sub(_replace, raw)
        return sanitized, sanitized != raw

    def _effective_placeholder_labels(self, task: WorkTask, labels: list[str]) -> list[str]:
        phase_name = self._phase_name_for_task(task).lower()
        if phase_name == "lead_close" or phase_name.startswith(("lead_failure_", "lead_report_", "lead_preflight_")):
            return [label for label in list(labels or []) if label not in _SOFT_PLACEHOLDER_LABELS]
        return list(labels or [])

    @staticmethod
    def _planning_section_kind(line: str) -> str:
        normalized = re.sub(r"[^a-z_ ]+", " ", str(line or "").strip().lower()).strip(" :")
        normalized = normalized.replace("ó", "o").replace("í", "i").replace("á", "a")
        normalized = normalized.replace("é", "e").replace("ú", "u")
        if normalized in {"objective", "objetivo"}:
            return "objective"
        if normalized in {
            "steps",
            "pasos",
            "implementation steps",
            "pasos de implementacion",
            "plan steps",
            "tareas",
            "tasks",
            "tareas secuenciadas",
            "sequenced tasks",
            "sequence",
            "secuencia",
        }:
            return "steps"
        if normalized in {
            "acceptance criteria",
            "acceptance_criteria",
            "criteria",
            "criterios",
            "criterios de aceptacion",
            "quality gates",
            "quality_gates",
            "checks",
            "validation",
            "validacion",
            "pruebas minimas",
            "pruebas minimas",
            "minimum tests",
        }:
            return "acceptance_criteria"
        if normalized in {
            "constraints",
            "restrictions",
            "restricciones",
            "risks",
            "riesgos",
            "guardrails",
            "forbidden paths",
            "forbidden_path_hints",
        }:
            return "constraints"
        return ""

    @classmethod
    def _planning_section_with_inline_value(cls, line: str) -> tuple[str, str]:
        stripped = str(line or "").strip().strip("*")
        if not stripped:
            return "", ""
        inline_match = re.match(r"^\s*([^:]{2,40})\s*:\s*(.+?)\s*$", stripped)
        if not inline_match:
            return "", ""
        section_kind = cls._planning_section_kind(str(inline_match.group(1) or ""))
        if not section_kind:
            return "", ""
        value = str(inline_match.group(2) or "").strip()
        return section_kind, value

    @staticmethod
    def _planning_action_candidates_from_text(text: str) -> list[str]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return []
        sentences = re.split(r"[\n.;]+", raw_text)
        action_phrases: list[str] = []
        action_re = re.compile(
            r"(?i)\b(?:revisar|analizar|definir|implementar|ajustar|integrar|validar|preparar|actualizar|refactorizar|probar|documentar|extraer|generar|insertar|calcular|construir)\b"
        )
        for sentence in sentences:
            candidate = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", str(sentence or "").strip())
            if not candidate:
                continue
            if " antes de " in candidate.lower():
                parts = re.split(r"(?i)\bantes de\b", candidate)
                for part in parts:
                    normalized_part = str(part or "").strip(" ,")
                    if normalized_part and action_re.search(normalized_part):
                        action_phrases.append(normalized_part)
                continue
            if action_re.search(candidate):
                action_phrases.append(candidate)
        return list(dict.fromkeys(item for item in action_phrases if item))[:6]

    def _derive_minimal_planning_artifact_from_narrative(
        self,
        *,
        task: WorkTask,
        text: str,
    ) -> dict[str, Any]:
        phase_name = self._phase_name_for_task(task).lower()
        if phase_name != "plan_engineering":
            return {}
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        objective = str(phase_contract.get("objective", "") or "").strip()
        action_candidates = self._planning_action_candidates_from_text(text)
        validation_hint_re = re.compile(
            r"(?i)\b(?:validar|validate|validation|verificar|verify|verified|test|tests|pytest|check|checks|criteri[oa]s?|aceptaci[oó]n|acceptance)\b"
        )
        if (
            not objective
            or len(action_candidates) < 2
            or not validation_hint_re.search(str(text or ""))
        ):
            return {}
        acceptance = [
            "La implementación respeta el objetivo del slice y el scope permitido por el contrato."
        ]
        return {
            "objective": objective,
            "steps": action_candidates[:4],
            "acceptance_criteria": acceptance,
            "constraints": [],
            "summary": " | ".join(
                [
                    f"objective={objective}",
                    f"steps={'; '.join(action_candidates[:2])}",
                    f"criteria={acceptance[0]}",
                ]
            ),
        }

    @classmethod
    def _extract_planning_artifact(cls, text: str) -> dict[str, Any]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return {}

        block_match = _PLANNING_ARTIFACT_BLOCK_RE.search(raw_text)
        artifact_text = str(block_match.group(1) if block_match else raw_text).strip()
        data: dict[str, Any] = {
            "objective": "",
            "steps": [],
            "acceptance_criteria": [],
            "constraints": [],
        }
        current_section = ""

        for raw_line in artifact_text.splitlines():
            line = str(raw_line or "").rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            inline_section_kind, inline_value = cls._planning_section_with_inline_value(stripped)
            if inline_section_kind:
                current_section = inline_section_kind
                if inline_section_kind == "objective" and inline_value and not data["objective"]:
                    data["objective"] = inline_value
                elif (
                    inline_section_kind in {"steps", "acceptance_criteria", "constraints"}
                    and inline_value
                ):
                    data[inline_section_kind].append(inline_value)
                continue
            section_kind = cls._planning_section_kind(stripped)
            if section_kind:
                current_section = section_kind
                continue
            bullet_match = _PLANNING_BULLET_RE.match(stripped)
            if current_section == "objective":
                if bullet_match and not data["objective"]:
                    data["objective"] = str(bullet_match.group(1) or "").strip()
                elif not data["objective"]:
                    data["objective"] = stripped
                continue
            if current_section in {"steps", "acceptance_criteria", "constraints"}:
                item = str(bullet_match.group(1) if bullet_match else stripped).strip()
                if item:
                    data[current_section].append(item)

        if not block_match:
            bullets = [
                str(match.group(1) or "").strip()
                for line in raw_text.splitlines()
                for match in [_PLANNING_BULLET_RE.match(line.strip())]
                if match and str(match.group(1) or "").strip()
            ]
            if not data["steps"] and bullets:
                data["steps"] = bullets[:4]
            if not data["acceptance_criteria"]:
                criteria_candidates = [
                    item
                    for item in bullets
                    if re.search(
                        r"(?i)\b(?:must|debe|verify|verificar|validar|accept|acept|pytest|test|check)\b",
                        item,
                    )
                ]
                data["acceptance_criteria"] = criteria_candidates[:4]

        data["objective"] = str(data.get("objective", "") or "").strip()
        data["steps"] = [
            str(item).strip() for item in list(data.get("steps", []) or []) if str(item).strip()
        ][:6]
        data["acceptance_criteria"] = [
            str(item).strip()
            for item in list(data.get("acceptance_criteria", []) or [])
            if str(item).strip()
        ][:6]
        data["constraints"] = [
            str(item).strip()
            for item in list(data.get("constraints", []) or [])
            if str(item).strip()
        ][:6]

        if not data["objective"] or len(data["steps"]) < 2 or len(data["acceptance_criteria"]) < 1:
            return {}

        summary_parts = [
            f"objective={data['objective']}",
            f"steps={'; '.join(data['steps'][:2])}",
            f"criteria={'; '.join(data['acceptance_criteria'][:2])}",
        ]
        if data["constraints"]:
            summary_parts.append(f"constraints={'; '.join(data['constraints'][:2])}")
        data["summary"] = " | ".join(summary_parts)
        return data

    def _persist_planning_artifact(
        self,
        *,
        task: WorkTask,
        phase_name: str,
        artifact: dict[str, Any],
    ) -> None:
        task.metadata["planning_artifact"] = dict(artifact)
        task_root = self._task_root(task.task_id)
        ws = self._get_workflow_state(task_root)
        planning_artifacts = dict(ws.get("planning_artifacts", {}) or {})
        planning_artifacts[str(phase_name or "").strip()] = dict(artifact)
        ws["planning_artifacts"] = planning_artifacts
        self._save_workflow_state(task_root)
        self.event_logger.emit(
            "planning_artifact_persisted",
            {
                "task_root": task_root,
                "task_id": task.task_id,
                "phase": phase_name,
                "steps": len(list(artifact.get("steps", []) or [])),
                "acceptance_criteria": len(
                    list(artifact.get("acceptance_criteria", []) or [])
                ),
            },
        )

    @staticmethod
    def _looks_like_execution_command(candidate: object) -> bool:
        text = str(candidate or "").strip().strip("`")
        if not text:
            return False
        normalized = text.lower()
        if any(normalized.startswith(prefix) for prefix in _NON_COMMAND_INLINE_PREFIXES):
            return False
        first = normalized.split()[0]
        if first in _POWERSHELL_COMMAND_STARTERS:
            return True
        return bool(
            re.match(
                r"^(pytest|python|py|pip|npm|node|pnpm|yarn|uv|npx|tox|coverage|playwright|git|cargo|go|dotnet|mvn|gradle|make|pwsh|powershell|bash|sh|cmd)\b",
                normalized,
            )
        )

    @classmethod
    def _extract_execution_command_candidates(cls, text: object) -> list[str]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return []

        candidates: list[str] = []
        for match in _EXECUTION_INLINE_CODE_RE.finditer(raw_text):
            candidate = str(match.group(1) or "").strip()
            if cls._looks_like_execution_command(candidate):
                candidates.append(candidate)

        for match in _EXECUTION_LINE_COMMAND_RE.finditer(raw_text):
            candidate = str(match.group(0) or "").strip()
            if cls._looks_like_execution_command(candidate):
                candidates.append(candidate)

        return list(dict.fromkeys(candidates))[:8]

    @staticmethod
    def _dependency_execution_plan_preview(task: WorkTask) -> list[str]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        raw_plan = metadata.get("execution_plan", [])
        if not isinstance(raw_plan, list):
            return []
        commands = [
            str((step or {}).get("command", "") or "").strip()
            for step in raw_plan
            if isinstance(step, dict)
        ]
        return [command for command in commands if command][:6]

    @staticmethod
    def _execution_step_type_for_command(command: str) -> str:
        first = str(command or "").strip().split()[0].lower() if str(command or "").strip() else ""
        if first in _POWERSHELL_COMMAND_STARTERS or command.strip().startswith("$"):
            return "powershell"
        return "cmd"

    @classmethod
    def _build_execution_plan_from_commands(
        cls,
        commands: list[str],
    ) -> list[dict[str, Any]]:
        unique_commands = list(dict.fromkeys(cmd for cmd in commands if str(cmd).strip()))[:6]
        plan: list[dict[str, Any]] = []
        for command in unique_commands:
            step_type = cls._execution_step_type_for_command(command)
            timeout = 180 if re.match(r"(?i)^(pytest|python|py|npm|pnpm|yarn|uv|npx|tox|coverage|cargo|go|dotnet|mvn|gradle|make)\b", command.strip()) else 90
            plan.append(
                {
                    "type": step_type,
                    "command": command,
                    "timeout": timeout,
                }
            )
        return plan

    @staticmethod
    def _source_text_contains_explicit_commands(source_text: str) -> bool:
        normalized = str(source_text or "").strip()
        if not normalized:
            return False
        if re.search(
            r"(?is)`(?:pytest|python\s+-m\s+pytest|py\s+-m\s+pytest|npm|pnpm|yarn|uv|npx|tox|coverage|cargo|go\s+test|dotnet\s+test|mvn(?:\s+test)?|gradle(?:\s+test)?|make(?:\s+test)?)\b[^`\n]*`",
            normalized,
        ):
            return True
        if re.search(
            r"(?im)^\s*(?:\d+[.)]|[-*])\s*(?:pytest|python\s+-m\s+pytest|py\s+-m\s+pytest|npm|pnpm|yarn|uv|npx|tox|coverage|cargo|go\s+test|dotnet\s+test|mvn(?:\s+test)?|gradle(?:\s+test)?|make(?:\s+test)?)\b",
            normalized,
        ):
            return True
        return False

    def _execution_plan_source_candidates(
        self,
        task: WorkTask,
    ) -> tuple[list[tuple[str, object]], list[str]]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        planning_artifact = dict(metadata.get("planning_artifact", {}) or {})
        phase_contract = dict(metadata.get("phase_contract", {}) or {})
        objective = str(phase_contract.get("objective", "") or "").strip()

        text_sources: list[tuple[str, object]] = []
        for key in ("steps", "acceptance_criteria"):
            values = list(planning_artifact.get(key, []) or [])
            for index, value in enumerate(values, start=1):
                text_sources.append((f"task.planning_artifact.{key}[{index}]", value))
        if objective:
            text_sources.append(("task.phase_contract.objective", objective))

        task_root = self._task_root(task.task_id)
        ws = self._get_workflow_state(task_root)
        planning_artifacts = dict(ws.get("planning_artifacts", {}) or {})
        phase_outputs = dict(ws.get("phase_outputs", {}) or {})
        dependency_phases: list[str] = []
        for dep_id in list(task.dependencies or []):
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                continue
            dep_phase = self._phase_name_for_task(dep_task)
            if dep_phase:
                dependency_phases.append(dep_phase)
            dep_execution_preview = self._dependency_execution_plan_preview(dep_task)
            for index, command in enumerate(dep_execution_preview, start=1):
                text_sources.append((f"dependency.{dep_phase}.execution_plan[{index}]", command))
            dep_artifact = dict(
                dep_task.metadata.get("planning_artifact", {})
                or planning_artifacts.get(dep_phase, {})
                or {}
            )
            dep_is_planning = dep_phase.lower().startswith("plan_")
            for key in ("steps", "acceptance_criteria"):
                values = list(dep_artifact.get(key, []) or [])
                for index, value in enumerate(values, start=1):
                    text_sources.append((f"dependency.{dep_phase}.{key}[{index}]", value))

        filtered_sources: list[tuple[str, object]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for source_name, source_value in text_sources:
            normalized_value = str(source_value or "").strip()
            if not normalized_value:
                continue
            pair = (str(source_name), normalized_value)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            filtered_sources.append((str(source_name), normalized_value))
        return filtered_sources, list(dict.fromkeys(phase for phase in dependency_phases if phase))

    def _derive_execution_plan_from_task(
        self,
        task: WorkTask,
    ) -> list[dict[str, Any]]:
        plan, _ = self._derive_execution_plan_with_diagnostics(task)
        return plan

    def _derive_execution_plan_with_diagnostics(
        self,
        task: WorkTask,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        existing_plan = metadata.get("execution_plan", [])
        if isinstance(existing_plan, list) and existing_plan:
            return (
                [dict(step) for step in existing_plan if isinstance(step, dict)],
                {
                    "status": "already_present",
                    "source_count": 1,
                    "command_count": len(list(existing_plan or [])),
                    "checked_sources": [{"source": "task.execution_plan", "commands": len(list(existing_plan or []))}],
                    "dependency_phases": [],
                },
            )

        text_sources, dependency_phases = self._execution_plan_source_candidates(task)
        commands: list[str] = []
        checked_sources: list[dict[str, Any]] = []
        for source_name, source in text_sources:
            source_text = str(source or "").strip()
            if (
                source_name.endswith("phase_contract.objective")
                and not self._source_text_contains_explicit_commands(source_text)
            ):
                found = []
            else:
                found = self._extract_execution_command_candidates(source_text)
            checked_sources.append(
                {
                    "source": source_name,
                    "chars": len(source_text),
                    "commands": len(found),
                }
            )
            commands.extend(found)

        unique_commands = list(dict.fromkeys(cmd for cmd in commands if str(cmd).strip()))[:6]
        status = "derived" if unique_commands else "no_commands_detected"
        return (
            self._build_execution_plan_from_commands(unique_commands),
            {
                "status": status,
                "source_count": len(checked_sources),
                "command_count": len(unique_commands),
                "checked_sources": checked_sources[:12],
                "dependency_phases": dependency_phases[:8],
            },
        )

    def _materialize_execution_plan_if_possible(
        self,
        *,
        task: WorkTask,
        assignee: str,
        persist: bool,
    ) -> list[dict[str, Any]]:
        derived_plan, diagnostics = self._derive_execution_plan_with_diagnostics(task)
        task.metadata["execution_plan_derivation"] = dict(diagnostics)
        if not derived_plan:
            if persist:
                self.taskboard.persist_tasks([task.task_id])
            self.event_logger.emit(
                "execution_plan_derivation_failed",
                {
                    "task_id": task.task_id,
                    "assignee": assignee,
                    "phase": self._phase_name_for_task(task),
                    **dict(diagnostics),
                },
            )
            return []
        task.metadata["execution_plan"] = [dict(step) for step in derived_plan]
        task.metadata["execution_plan_source"] = "derived_from_contract"
        if persist:
            self.taskboard.persist_tasks([task.task_id])
        self.event_logger.emit(
            "execution_plan_derived",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "step_count": len(derived_plan),
                "phase": self._phase_name_for_task(task),
                "dependency_phases": list(diagnostics.get("dependency_phases", []) or []),
            },
        )
        return derived_plan

    def _snapshot_agent_output_on_task(
        self,
        *,
        task: WorkTask,
        safe_content: str,
    ) -> None:
        normalized_output = str(safe_content or "").strip()
        if not normalized_output:
            return
        task.metadata["_last_agent_output"] = normalized_output
        task.metadata["result"] = normalized_output
        self._append_agent_output_history(task, normalized_output)

    def _fail_task_for_missing_planning_artifact(
        self,
        *,
        task: WorkTask,
        assignee: str,
        safe_content: str,
        session,
    ) -> bool:
        phase_name = self._phase_name_for_task(task).lower()
        if task.role != Role.ENGINEER or not phase_name.startswith("plan_"):
            return False

        artifact = self._extract_planning_artifact(safe_content)
        if not artifact:
            artifact = self._derive_minimal_planning_artifact_from_narrative(
                task=task,
                text=safe_content,
            )
        if artifact:
            self._persist_planning_artifact(
                task=task,
                phase_name=phase_name,
                artifact=artifact,
            )
            return False

        summary = (
            "planning phase must emit a structured planning artifact with objective, at least "
            "two implementation steps, and at least one acceptance criterion"
        )
        self._snapshot_agent_output_on_task(task=task, safe_content=safe_content)
        diagnostic = (
            "[PHASE_VERDICT]\n"
            f"phase_id: {phase_name}\n"
            "status: failed\n"
            "reason_codes: missing_planning_artifact\n"
            "contract_status: unknown\n"
            "slice_id: \n"
            f"summary: {summary}\n"
            "[/PHASE_VERDICT]\n"
            "El planning del Engineer no dejo un artefacto estructurado reutilizable para las "
            "fases siguientes."
        )
        self._update_workflow_state(self._task_root(task.task_id), phase_name, diagnostic)
        self.taskboard.mark_failed(
            task.task_id,
            error=f"missing_planning_artifact_required: {summary}",
        )
        self.event_logger.emit(
            "missing_planning_artifact_required",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "phase": phase_name,
            },
        )
        self._maybe_spawn_lead_failure_checkpoint(task, summary)
        self._maybe_run_event_meeting(
            trigger="task_failed",
            task_id=task.task_id,
            reason="missing_planning_artifact",
        )
        self.session_store.close_session(
            session,
            summary="missing_planning_artifact",
            status="failed",
        )
        return True

    def _phase_tasks_for_run_health(self, task_root: str) -> dict[str, WorkTask]:
        ws = self._get_workflow_state(task_root)
        phase_task_ids = dict(ws.get("phase_task_ids", {}) or {})
        phase_tasks: dict[str, WorkTask] = {}
        for phase_name, task_id in phase_task_ids.items():
            if not isinstance(phase_name, str):
                continue
            normalized_phase = phase_name.strip()
            if not normalized_phase or normalized_phase in {"lead_intake", "lead_close"}:
                continue
            task = self.taskboard.get_task(str(task_id or "").strip())
            if task is not None:
                phase_tasks[normalized_phase] = task
        if phase_tasks:
            return phase_tasks
        prefix = f"{task_root}::"
        for task in self.taskboard.list_tasks():
            if not task.task_id.startswith(prefix):
                continue
            phase_name = str(task.metadata.get("phase", "") or "").strip()
            if not phase_name or phase_name in {"lead_intake", "lead_close"}:
                continue
            if task.metadata.get("is_gate"):
                continue
            phase_tasks.setdefault(phase_name, task)
        return phase_tasks

    def _gate_tasks_for_run_health(self, phase_tasks: dict[str, WorkTask]) -> dict[str, WorkTask]:
        gate_tasks: dict[str, WorkTask] = {}
        for task in phase_tasks.values():
            for gate_id in list(task.metadata.get("quality_gate_tasks", []) or []):
                gate_task = self.taskboard.get_task(str(gate_id or "").strip())
                if gate_task is not None:
                    gate_tasks[gate_task.task_id] = gate_task
        return gate_tasks

    def _run_health_budget_summary(self, task_root: str) -> tuple[int, int]:
        round_budget = 0
        auto_extensions = 0
        for event in self.event_logger.recent_events(hours=72):
            payload = event.get("payload", {}) or {}
            if not isinstance(payload, dict):
                continue
            event_task_id = str(payload.get("task_id", "") or "").strip()
            if not event_task_id:
                continue
            if not event_task_id.startswith(task_root):
                continue
            for key_name in ("round_budget", "new_round_budget", "to_round_budget"):
                try:
                    round_budget = max(round_budget, int(payload.get(key_name, 0) or 0))
                except (TypeError, ValueError):
                    continue
            try:
                from_budget = int(payload.get("from_round_budget", 0) or 0)
                to_budget = int(payload.get("to_round_budget", 0) or 0)
            except (TypeError, ValueError):
                from_budget = 0
                to_budget = 0
            if to_budget > from_budget:
                auto_extensions += to_budget - from_budget
        return round_budget, auto_extensions

    def _build_run_health_report(self, task_root: str) -> RunHealthReport:
        phase_tasks = self._phase_tasks_for_run_health(task_root)
        gate_tasks = self._gate_tasks_for_run_health(phase_tasks)
        rounds_used = 0
        for task in self.taskboard.list_tasks():
            if not task.task_id.startswith(f"{task_root}::"):
                continue
            try:
                rounds_used = max(rounds_used, int(task.metadata.get("execution_round", 0) or 0))
            except (TypeError, ValueError):
                continue
        round_budget, auto_extensions = self._run_health_budget_summary(task_root)
        exec_steps_total = 0
        exec_steps_success = 0
        for event in self.event_logger.recent_events(hours=72):
            payload = event.get("payload", {}) or {}
            if not isinstance(payload, dict):
                continue
            ev_task_id = str(payload.get("task_id", "") or "").strip()
            if not ev_task_id.startswith(task_root):
                continue
            if event.get("event_type") == "execution_step":
                exec_steps_total += 1
                if bool(payload.get("success", False)):
                    exec_steps_success += 1
        return build_run_health_report(
            phase_tasks=phase_tasks,
            gate_tasks=gate_tasks,
            routing_failures=self.router.get_recent_routing_failures(task_root=task_root),
            missing_api_keys=self.router.get_missing_api_keys(task_root=task_root),
            unavailable_models=self.router.get_unavailable_models(task_root=task_root),
            rounds_used=rounds_used,
            round_budget=round_budget,
            auto_extensions=auto_extensions,
            execution_steps_total=exec_steps_total,
            execution_steps_success=exec_steps_success,
        )

    def _build_run_health_prompt_block(self, task: WorkTask) -> str:
        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if phase_name != "lead_close":
            return ""
        task_root = str(task.metadata.get("chat_parent", "") or self._task_root(task.task_id)).strip()
        report = self._build_run_health_report(task_root)
        block = report.to_prompt_block()
        self.event_logger.emit(
            "run_health_report_built",
            {
                "task_id": task.task_id,
                "task_root": task_root,
                "phase_count": len(report.phases),
                "rounds_used": report.rounds_used,
                "round_budget": report.round_budget,
                "auto_extensions": report.auto_extensions,
                "missing_api_keys": report.missing_api_keys,
                "unavailable_models": report.unavailable_models,
            },
        )
        return block

    def _build_lead_close_policy_block(self, task: WorkTask) -> str:
        phase_name = str(task.metadata.get("phase", "") or "").strip()
        if phase_name != "lead_close":
            return ""

        task_root = str(task.metadata.get("chat_parent", "") or self._task_root(task.task_id)).strip()
        ws = self._get_workflow_state(task_root)
        phase_tasks = self._phase_tasks_for_run_health(task_root)
        phase_states = {
            phase_id: phase_task.state.value
            for phase_id, phase_task in phase_tasks.items()
        }
        run_verdict = dict(ws.get("run_verdict", {}) or {})
        if not str(run_verdict.get("run_profile", "") or "").strip():
            run_verdict["run_profile"] = str(
                ws.get("run_profile") or task.metadata.get("run_profile") or ""
            )
        policy = derive_lead_close_policy(
            phase_verdicts=ws.get("phase_verdicts", {}),
            phase_states=phase_states,
            run_verdict=run_verdict,
            phase_outputs=ws.get("phase_outputs", {}),
        )
        authoritative_close_state = str(
            policy.get("authoritative_close_state", "") or ""
        ).strip().lower()
        unique_reasons = [
            str(item).strip()
            for item in list(policy.get("blocking_signals", []) or [])
            if str(item).strip()
        ]
        self.event_logger.emit(
            "lead_close_policy_built",
            {
                "task_id": task.task_id,
                "task_root": task_root,
                "authoritative_close_state": authoritative_close_state,
                "blocking_signals": unique_reasons,
            },
        )
        policy_block = build_lead_close_policy_prompt_block(policy)

        # Para solo_lead, adjuntar el resultado de pytest si existe en workflow_state.
        _lc_run_profile = str(
            ws.get("run_profile") or task.metadata.get("run_profile") or ""
        ).strip().lower()
        if _lc_run_profile in {"solo_lead", "direct"}:
            _pytest_result = str(ws.get("solo_lead_pytest_result", "") or "").strip()
            if _pytest_result:
                policy_block = (
                    f"{policy_block}\n\n== PYTEST RESULT (solo_lead build) ==\n{_pytest_result}"
                )
        return policy_block

    # ── Gate feedback collection ────────────────────────────────────

    def _collect_gate_feedback(self, failed_gate_ids: list[str]) -> str:
        lines: list[str] = []
        for gate_id in failed_gate_ids:
            gate_task = self.taskboard.get_task(gate_id)
            if gate_task is None:
                lines.append(f"- {gate_id}: gate task no encontrado")
                continue
            gate_type = str(gate_task.metadata.get("gate_type", "unknown"))
            result = gate_task.metadata.get("result", "")
            error = gate_task.metadata.get("error", "")
            feedback_text = result or error or "sin detalle"
            lines.append(
                f"- [{gate_type.upper()}] {self._compact_text(feedback_text, 300)}"
            )
        return "\n".join(lines) if lines else "Gates fallaron sin feedback detallado."

    def _cleanup_gate_tasks(self, gate_task_ids: list[str]) -> None:
        """Elimina gate tasks del taskboard para permitir re-creacion."""
        self.taskboard.remove_tasks(gate_task_ids)

    def start_mcp_servers(self) -> dict[str, str]:
        """Inicia todos los servidores MCP habilitados. Llamar al inicio del workflow."""
        if self.mcp_manager is None:
            return {}
        results = self.mcp_manager.start_enabled()
        for name, status in results.items():
            self.event_logger.emit(
                "mcp_server_start", {"server": name, "status": status}
            )
        return results

    def stop_mcp_servers(self) -> None:
        """Detiene todos los servidores MCP. Llamar al finalizar el workflow."""
        if self.mcp_manager is not None:
            self.mcp_manager.stop_all()
            self.event_logger.emit("mcp_servers_stopped", {})

    def submit_task(self, task: WorkTask) -> None:
        # Sanitizar input de usuario antes de que llegue a cualquier agente
        task.title = self.compliance.sanitize_context(task.title)
        task.description = self.compliance.sanitize_context(task.description)
        self._attach_existing_failure_checkpoints_to_new_task(task)
        self.taskboard.add_task(task)
        _phase_name = str(task.metadata.get("phase", "") or "").strip().lower()
        _mailbox_body = str(task.title or "").strip()
        if _phase_name == "lead_close" or _phase_name.startswith("lead_"):
            _objective = str(
                (
                    dict(task.metadata.get("phase_contract") or {}).get("objective")
                    or task.metadata.get("delegation_brief")
                    or _phase_name
                    or "control_task"
                )
            ).strip()
            _mailbox_body = (
                f"control_task phase={_phase_name or 'unknown'} "
                f"objective={_objective[:160]}"
            ).strip()
        self.mailbox.send(
            sender="system",
            recipient="team_lead",
            subject=f"Nueva tarea: {task.task_id}",
            body=_mailbox_body,
            task_id=task.task_id,
            kind="actionable",
        )
        self._remember_memory(
            agent_id="lead-1",
            role=Role.TEAM_LEAD.value,
            kind="task_submitted",
            content=f"{task.task_id}: {task.title}",
            task_id=task.task_id,
            tags=["task", "inbox"],
        )

    def _attach_existing_failure_checkpoints_to_new_task(self, task: WorkTask) -> None:
        chat_parent = str(task.metadata.get("chat_parent", "") or "").strip()
        if not chat_parent:
            return
        delegate_source_phase = str(task.metadata.get("delegate_source_phase", "") or "").strip()
        if delegate_source_phase.startswith("lead_failure_"):
            return
        phase_name = self._phase_name_for_task(task)
        if phase_name == "lead_intake" or phase_name.startswith(("lead_failure_", "lead_report_")):
            return
        checkpoint_ids = [
            f"{chat_parent}::{phase_name}"
            for phase_name in self._active_failure_checkpoint_phase_names(chat_parent)
        ]
        if not checkpoint_ids:
            return
        existing = list(task.dependencies or [])
        task.dependencies = list(dict.fromkeys(existing + checkpoint_ids))
        if checkpoint_ids:
            task.metadata["lead_failure_gate_dependencies"] = sorted(
                set(list(task.metadata.get("lead_failure_gate_dependencies", [])) + checkpoint_ids)
            )

    def _maybe_spawn_deferred_delegates(self, task_id: str) -> None:
        """C1: Spawn evidence delegate tasks that were deferred until the parent
        phase starts executing. Runs once per task (guarded by delegates_spawned flag)."""
        task = self.taskboard.get_task(task_id)
        if task is None:
            return
        if (
            str(task.metadata.get("run_profile", "") or "").strip().lower() == "solo_lead"
            or bool(task.metadata.get("direct_coding_executor", False))
        ):
            return
        if task.metadata.get("delegates_spawned"):
            return
        deferred_specs = list(task.metadata.get("deferred_evidence_specs", []) or [])
        if not deferred_specs:
            return
        spawned: list[str] = []
        for spec in deferred_specs:
            try:
                child_task_id = str(spec.get("task_id", "")).strip()
                if not child_task_id:
                    continue
                if self.taskboard.get_task(child_task_id) is not None:
                    spawned.append(child_task_id)
                    continue
                role_str = str(spec.get("role", "scout")).strip().lower()
                try:
                    role_enum = Role(role_str)
                except ValueError:
                    role_enum = Role.SCOUT
                criticality_str = str(spec.get("criticality", "medium")).strip().lower()
                try:
                    criticality_enum = Criticality(criticality_str)
                except ValueError:
                    criticality_enum = Criticality.MEDIUM
                child_task = WorkTask(
                    task_id=child_task_id,
                    title=str(spec.get("title", f"Delegate {child_task_id}")),
                    description=str(spec.get("description", "")),
                    role=role_enum,
                    complexity=Complexity.LOW,
                    criticality=criticality_enum,
                    dependencies=[
                        str(dep).strip()
                        for dep in list(spec.get("dependencies") or [task_id])
                        if str(dep).strip()
                    ],
                    metadata=dict(spec.get("metadata", {})),
                )
                self.submit_task(child_task)
                spawned.append(child_task_id)
            except Exception as _e:
                self.event_logger.emit(
                    "deferred_delegate_spawn_error",
                    {"parent_task_id": task_id, "child_task_id": child_task_id, "error": str(_e)},
                )
        if spawned:
            self.taskboard.update_metadata(task_id, {"delegates_spawned": True})
            self.event_logger.emit(
                "deferred_delegates_spawned",
                {"parent_task_id": task_id, "spawned": spawned},
            )

    def _claim_ready_tasks(
        self, active_round: int, sub_iteration: int
    ) -> list[WorkTask]:
        """Reclama todas las tareas READY disponibles."""
        claimed_tasks: list[WorkTask] = []
        for task in self.taskboard.ready_tasks():
            if self._maybe_spawn_lead_preflight_checkpoint(task):
                continue
            if self._has_pending_chat_directive_checkpoint(task):
                continue
            assignee = task.assignee or self._assignee_for_role(task.role)
            if not self.taskboard.claim_task(task.task_id, assignee=assignee):
                current = self.taskboard.get_task(task.task_id)
                if current is not None and current.state == TaskState.BLOCKED:
                    self._maybe_run_event_meeting(
                        trigger="file_conflict",
                        task_id=task.task_id,
                        reason=str(current.metadata.get("blocked_by_files", [])),
                    )
                continue
            execution_order = self._next_execution_order()
            self.taskboard.update_metadata(
                task.task_id,
                {
                    "execution_round": active_round,
                    "execution_sub_iteration": sub_iteration,
                    "execution_order": execution_order,
                },
            )
            refreshed = self.taskboard.get_task(task.task_id)
            if refreshed is None:
                continue
            if self._block_task_for_missing_dependency_delivery(
                refreshed,
                assignee=assignee,
                missing_dependencies=self._dependency_delivery_gaps(refreshed),
            ):
                continue
            # C1: spawn deferred evidence delegate tasks lazily when the parent
            # phase is first claimed.
            self._maybe_spawn_deferred_delegates(task.task_id)
            claimed = self.taskboard.get_task(task.task_id)
            if claimed is not None:
                claimed_tasks.append(claimed)
        return claimed_tasks

    def _execute_claimed_tasks(
        self, claimed_tasks: list[WorkTask], active_round: int
    ) -> None:
        """Ejecuta tareas reclamadas (secuencial o paralelo segun config)."""
        processed = len(claimed_tasks)
        effective_parallel = self._effective_parallel_tasks(processed)
        if effective_parallel <= 1 or processed == 1:
            for claimed in claimed_tasks:
                self._run_task(claimed)
        else:
            max_workers = min(effective_parallel, processed)
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="aiteam-worker"
            ) as executor:
                future_to_task = {
                    executor.submit(self._run_task, claimed): claimed
                    for claimed in claimed_tasks
                }
                for future in as_completed(future_to_task):
                    claimed = future_to_task[future]
                    try:
                        future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        current = self.taskboard.get_task(claimed.task_id)
                        if current is not None and current.state not in {
                            TaskState.COMPLETED,
                            TaskState.FAILED,
                            TaskState.BLOCKED,
                        }:
                            self.taskboard.mark_failed(
                                claimed.task_id, error=f"parallel_worker_error:{exc}"
                            )
                        self.event_logger.emit(
                            "parallel_worker_failure",
                            {
                                "task_id": claimed.task_id,
                                "error": str(exc),
                                "execution_round": active_round,
                            },
                        )
        self._autotune_parallelism(active_round)

    def process_once(self) -> int:
        total_processed = 0
        active_round = self._round + 1
        max_sub_iterations = 20
        used_sub_iterations = 0

        # ── Timeout de quality gates: desbloquea tareas atascadas ──
        self._check_gate_timeouts()

        # ── Eager dependency processing: re-check READY tras cada batch ──
        for _sub in range(max_sub_iterations):
            sub_iteration = _sub + 1
            used_sub_iterations = sub_iteration
            self.event_logger.emit(
                "round_sub_iteration",
                {
                    "execution_round": active_round,
                    "sub_iteration": sub_iteration,
                    "phase": "claim_attempt",
                },
            )
            claimed_tasks = self._claim_ready_tasks(active_round, sub_iteration)
            if not claimed_tasks:
                self._release_blocked_parent_tasks()
                # Re-check: releasing blocked tasks may have made new ones READY
                claimed_tasks = self._claim_ready_tasks(active_round, sub_iteration)
                if not claimed_tasks:
                    break

            self.event_logger.emit(
                "round_sub_iteration",
                {
                    "execution_round": active_round,
                    "sub_iteration": sub_iteration,
                    "phase": "execute_batch",
                    "claimed_tasks": [task.task_id for task in claimed_tasks],
                    "claimed_count": len(claimed_tasks),
                },
            )
            self._execute_claimed_tasks(claimed_tasks, active_round)
            total_processed += len(claimed_tasks)
            self._release_blocked_parent_tasks()
            self.event_logger.emit(
                "sub_iteration_barrier",
                {
                    "execution_round": active_round,
                    "sub_iteration": sub_iteration,
                    "tasks_processed_so_far": total_processed,
                },
            )

        if total_processed > 0:
            self.event_logger.emit(
                "round_completed",
                {
                    "execution_round": active_round,
                    "sub_iterations_used": used_sub_iterations,
                    "tasks_processed": total_processed,
                },
            )
            self._round += 1
            self._run_round_sync_meeting()
        return total_processed

    def run_until_idle(self, max_rounds: int = 10) -> None:
        for _ in range(max_rounds):
            processed = self.process_once()
            if processed == 0:
                break

    def run_until_idle_with_progress(self, max_rounds: int = 10):
        """Generator que ejecuta rondas y yield'ea progreso despues de cada una.

        Uso: for progress in orch.run_until_idle_with_progress(max_rounds=5): ...
        """
        for round_num in range(1, max_rounds + 1):
            processed = self.process_once()
            tasks = self.taskboard.list_tasks()
            completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
            failed = sum(1 for t in tasks if t.state == TaskState.FAILED)
            in_progress = sum(1 for t in tasks if t.state == TaskState.IN_PROGRESS)
            ready = sum(1 for t in tasks if t.state == TaskState.READY)
            blocked = sum(1 for t in tasks if t.state == TaskState.BLOCKED)
            total = len(tasks)

            yield {
                "round": round_num,
                "max_rounds": max_rounds,
                "processed_this_round": processed,
                "tasks_total": total,
                "tasks_completed": completed,
                "tasks_failed": failed,
                "tasks_in_progress": in_progress,
                "tasks_ready": ready,
                "tasks_blocked": blocked,
                "done": processed == 0,
            }
            if processed == 0:
                break

    def _resolve_max_parallel_tasks(self) -> int:
        env_key = f"AITEAM_MAX_PARALLEL_TASKS_{self.environment.upper()}"
        raw = (
            os.getenv(env_key, "").strip()
            or os.getenv("AITEAM_MAX_PARALLEL_TASKS", "1").strip()
        )
        try:
            value = int(raw)
        except ValueError:
            return 1
        return max(1, value)

    def _resolve_parallel_min_tasks(self) -> int:
        env_key = f"AITEAM_MIN_PARALLEL_TASKS_{self.environment.upper()}"
        raw = (
            os.getenv(env_key, "").strip()
            or os.getenv("AITEAM_MIN_PARALLEL_TASKS", "1").strip()
        )
        try:
            value = int(raw)
        except ValueError:
            value = 1
        return max(1, min(value, self.max_parallel_tasks))

    @staticmethod
    def _resolve_parallel_autotune_enabled() -> bool:
        raw = os.getenv("AITEAM_PARALLEL_AUTOTUNE", "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _resolve_parallel_target_latency_ms() -> int:
        raw = os.getenv("AITEAM_PARALLEL_TARGET_LATENCY_MS", "1200").strip()
        try:
            value = int(raw)
        except ValueError:
            return 1200
        return max(200, value)

    @staticmethod
    def _resolve_parallel_max_failure_rate() -> float:
        raw = os.getenv("AITEAM_PARALLEL_MAX_FAILURE_RATE", "25").strip()
        try:
            value = float(raw)
        except ValueError:
            return 25.0
        return max(1.0, min(value, 100.0))

    def _effective_parallel_tasks(self, pending_count: int) -> int:
        if pending_count <= 0:
            return 0
        configured = (
            self._dynamic_parallel_tasks
            if self._parallel_autotune_enabled
            else self.max_parallel_tasks
        )
        bounded = max(
            self._parallel_min_tasks, min(configured, self.max_parallel_tasks)
        )
        return min(pending_count, bounded)

    def _autotune_parallelism(self, execution_round: int) -> None:
        if not self._parallel_autotune_enabled:
            return

        records = self.event_logger.recent_events(hours=1)
        round_records = [
            item
            for item in records
            if item.get("event_type") == "task_execution"
            and isinstance(item.get("payload"), dict)
            and int(item["payload"].get("execution_round", -1)) == execution_round
        ]
        if not round_records:
            return

        failures = 0
        latencies: list[int] = []
        for item in round_records:
            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if not bool(payload.get("success", False)):
                failures += 1
            latency = int(payload.get("latency_ms", 0) or 0)
            if latency > 0:
                latencies.append(latency)

        failure_rate = (failures / len(round_records)) * 100.0 if round_records else 0.0
        avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0

        previous = self._dynamic_parallel_tasks
        updated = previous
        if failure_rate > self._parallel_max_failure_rate or (
            avg_latency > 0 and avg_latency > self._parallel_target_latency_ms
        ):
            updated = max(self._parallel_min_tasks, previous - 1)
        elif failure_rate == 0.0 and (
            avg_latency == 0.0
            or avg_latency < (self._parallel_target_latency_ms * 0.55)
        ):
            updated = min(self.max_parallel_tasks, previous + 1)

        self._dynamic_parallel_tasks = updated
        self.event_logger.emit(
            "parallel_tuning",
            {
                "execution_round": execution_round,
                "tasks": len(round_records),
                "failure_rate": round(failure_rate, 2),
                "avg_latency_ms": round(avg_latency, 2),
                "parallel_previous": previous,
                "parallel_current": updated,
                "target_latency_ms": self._parallel_target_latency_ms,
                "max_failure_rate": self._parallel_max_failure_rate,
            },
        )

    def _next_execution_order(self) -> int:
        with self._execution_order_lock:
            self._execution_order += 1
            return self._execution_order

    def _emit_agent_event(self, event: dict) -> None:
        """Emite un evento de ciclo de vida de agente al callback registrado (si existe)."""
        if self.agent_event_callback is not None:
            try:
                self.agent_event_callback(event)
            except Exception:
                pass

    def _run_task(self, task: WorkTask) -> None:
        _task_type = (
            task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        )
        _phase = _task_type  # alias legible para eventos de agente
        assignee = task.assignee or self._assignee_for_role(
            task.role, task_type=_task_type
        )
        execution_round = int(task.metadata.get("execution_round", self._round + 1))
        execution_sub_iteration = int(task.metadata.get("execution_sub_iteration", 1))
        execution_order = int(task.metadata.get("execution_order", 0))
        gate_iteration = int(task.metadata.get("gate_iteration", 0))
        thread = self.thread_store.get_thread(
            agent_id=assignee,
            project_key=self._project_thread_key(),
            role=task.role.value,
        )
        consumed_mailbox_messages = self._consume_actionable_mailbox_messages(
            task=task,
            assignee=assignee,
            thread=thread,
        )

        # ── Crear sesion auditable ──
        session = self.session_store.create_session(
            agent_id=assignee,
            role=task.role.value,
            task_id=task.task_id,
            gate_iteration=gate_iteration,
        )
        task.metadata["session_id"] = session.session_id

        self.event_logger.emit(
            "task_started",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "assignee": assignee,
                "execution_round": execution_round,
                "execution_sub_iteration": execution_sub_iteration,
                "execution_order": execution_order,
                "gate_iteration": gate_iteration,
                "session_id": session.session_id,
                "thread_id": thread.thread_id,
                "thread_generation": int(getattr(thread, "generation", 1) or 1),
                "thread_version": str(getattr(thread, "thread_version", "") or ""),
                "thread_provider": str(getattr(thread, "provider", "") or ""),
                "thread_channel": str(getattr(thread, "channel", "") or ""),
                "thread_model_family": str(getattr(thread, "model_family", "") or ""),
            },
        )
        workspace = self.sandboxes.task_workspace(
            agent_id=assignee, task_id=task.task_id
        )

        session.record_action(
            "compliance_check", f"evaluate_sensitive_approval:{task.task_id}"
        )
        sensitive_approval, approval_reason = (
            self.compliance.evaluate_sensitive_approval(task.metadata)
        )
        tool_report = self._integrate_tools_for_task(
            task=task,
            assignee=assignee,
            internet_allowed=(self.environment != "prod" or sensitive_approval),
        )
        if not tool_report.success:
            self._fail_task_due_to_compliance(
                task=task,
                assignee=assignee,
                reason="tool_integration_failed",
                details=tool_report.errors,
            )
            self.session_store.close_session(
                session, summary="tool_integration_failed", status="failed"
            )
            return
        if tool_report.integrated_adapters:
            self._sync_router_external_adapters()

        missing_contract_details = self._missing_phase_contract_objective_details(task)
        if missing_contract_details:
            self._fail_task_due_to_compliance(
                task=task,
                assignee=assignee,
                reason="missing_phase_contract_objective",
                details=missing_contract_details,
            )
            self.session_store.close_session(
                session, summary="missing_phase_contract_objective", status="failed"
            )
            return

        execution_plan = task.metadata.get("execution_plan", [])
        execution_context = ""
        require_execution_plan = bool(
            task.metadata.get("require_execution_plan", False)
        )
        demo_fast_mode = sim_mode_enabled()
        if require_execution_plan and (
            not isinstance(execution_plan, list) or not execution_plan
        ):
            execution_plan = self._materialize_execution_plan_if_possible(
                task=task,
                assignee=assignee,
                persist=True,
            )
        if (
            require_execution_plan
            and (not isinstance(execution_plan, list) or not execution_plan)
            and not demo_fast_mode
        ):
            waiver_reason = self._missing_execution_plan_waiver_reason(task)
            if waiver_reason:
                task.metadata["execution_plan_requirement_waived"] = waiver_reason
                self.taskboard.persist_tasks([task.task_id])
                self.event_logger.emit(
                    "execution_plan_requirement_waived",
                    {
                        "task_id": task.task_id,
                        "assignee": assignee,
                        "reason": waiver_reason,
                    },
                )
            else:
                derivation_details = []
                derivation_report = dict(
                    task.metadata.get("execution_plan_derivation", {}) or {}
                )
                checked_sources = list(
                    derivation_report.get("checked_sources", []) or []
                )[:6]
                dependency_phases = list(
                    derivation_report.get("dependency_phases", []) or []
                )[:6]
                if checked_sources:
                    derivation_details.append(
                        "Execution-plan derivation checked sources: "
                        + ", ".join(
                            str(item.get("source", "") or "").strip()
                            for item in checked_sources
                            if str(item.get("source", "") or "").strip()
                        )
                    )
                if dependency_phases:
                    derivation_details.append(
                        "Completed dependency phases reviewed: "
                        + ", ".join(str(item).strip() for item in dependency_phases if str(item).strip())
                    )
                self._fail_task_due_to_compliance(
                    task=task,
                    assignee=assignee,
                    reason="missing_execution_plan_required",
                    details=[
                        "Task requires execution_plan but none was provided or derivable",
                        *derivation_details,
                    ],
                )
                self.session_store.close_session(
                    session, summary="missing_execution_plan", status="failed"
                )
                return
        if isinstance(execution_plan, list) and execution_plan:
            allowed, reason, sensitive_commands = (
                self.compliance.validate_execution_plan(
                    execution_plan,
                    task.metadata,
                )
            )
            if not allowed:
                self._fail_task_due_to_compliance(
                    task=task,
                    assignee=assignee,
                    reason=reason,
                    details=sensitive_commands,
                )
                self.session_store.close_session(
                    session, summary=f"compliance:{reason}", status="failed"
                )
                return
            if sensitive_commands:
                self.event_logger.emit(
                    "compliance_sensitive_plan",
                    {
                        "task_id": task.task_id,
                        "assignee": assignee,
                        "environment": self.environment,
                        "approved": True,
                        "steps": [
                            self.compliance.redact_text(item)[:160]
                            for item in sensitive_commands
                        ],
                    },
                )
            session.record_action(
                "command_exec", f"execute_plan:{len(execution_plan)} steps"
            )
            step_results = self.execution.execute_plan(
                task_id=task.task_id,
                plan=execution_plan,
                workspace=workspace,
            )
            for sr in step_results:
                session.record_action(
                    "command_exec",
                    f"{sr.step_type}:{(sr.command or '')[:80]}",
                    success=sr.success,
                    duration_ms=0,
                    metadata={"exit_code": sr.exit_code},
                )
            execution_context = self._compose_execution_context(step_results)
            self._remember_memory(
                agent_id=assignee,
                role=task.role.value,
                kind="execution_plan_result",
                content=execution_context,
                task_id=task.task_id,
                tags=["execution", "environment"],
            )

        self._ensure_tool_specialist_metadata(task)
        context = self._build_collaboration_context(task=task, assignee=assignee)
        skill_mcp_context = self._build_skill_mcp_context(task=task, assignee=assignee)
        specialist_prefetch_context = self._collect_specialist_prefetch_context(task)

        # RC-H: inject actual upstream task RESULTS into context so that
        # review/QA agents see real engineer/researcher output rather than
        # just the phase objectives written at task-creation time.
        _upstream_results_lines: list[str] = []
        for _dep_id in (task.dependencies or [])[:8]:
            _dep_task = self.taskboard.get_task(_dep_id)
            if _dep_task is None:
                continue
            _dep_result = str(_dep_task.metadata.get("result", "") or "").strip()
            if not _dep_result:
                continue
            _dep_label = _dep_id.split("::")[-1] if "::" in _dep_id else _dep_id
            _dep_role = getattr(_dep_task.role, "value", str(_dep_task.role))
            _short = _dep_result[:2000] + ("…[truncado]" if len(_dep_result) > 2000 else "")
            _upstream_results_lines.append(f"[{_dep_label} / {_dep_role}]:\n{_short}")
        if _upstream_results_lines:
            _upstream_block = (
                "\n\n[RESULTADOS_UPSTREAM]\n"
                "Resultados REALES producidos por las fases anteriores "
                "(usa esto para revisar, validar o continuar el trabajo):\n\n"
                + "\n\n---\n\n".join(_upstream_results_lines)
                + "\n[/RESULTADOS_UPSTREAM]"
            )
            context = f"{context}{_upstream_block}" if context else _upstream_block

        if specialist_prefetch_context:
            skill_mcp_context = (
                f"{skill_mcp_context}\n\n{specialist_prefetch_context}"
                if skill_mcp_context
                else specialist_prefetch_context
            )
        specialist_quorum = dict(task.metadata.get("specialist_quorum_result", {}) or {})
        if specialist_quorum and not self._to_bool(
            specialist_quorum.get("quorum_met", True)
        ):
            quorum_metadata = {
                "specialist_quorum_missing": list(
                    specialist_quorum.get("missing_specialists", []) or []
                ),
                "specialist_quorum_received": list(
                    specialist_quorum.get("received_specialists", []) or []
                ),
            }
            if self._is_planning_phase_task(task):
                quorum_metadata.update(
                    {
                        "specialist_quorum_degraded": True,
                        "specialist_quorum_warning": (
                            "planning_phase_continues_without_full_specialist_quorum"
                        ),
                    }
                )
                self.taskboard.update_metadata(task.task_id, quorum_metadata)
                self._emit_agent_event(
                    {
                        "type": "specialist_quorum_degraded",
                        "task_id": task.task_id,
                        "agent_id": assignee,
                        "role": task.role.value,
                        "phase": _phase,
                        "reason": "specialist_quorum_not_met",
                        "missing_specialists": quorum_metadata["specialist_quorum_missing"],
                        "received_specialists": quorum_metadata["specialist_quorum_received"],
                    }
                )
            elif task.role in {Role.REVIEWER, Role.QA}:
                quorum_metadata.update(
                    {
                        "specialist_quorum_degraded": True,
                        "specialist_quorum_warning": (
                            "review_or_qa_continues_without_full_specialist_quorum"
                        ),
                    }
                )
                self.taskboard.update_metadata(task.task_id, quorum_metadata)
                self._emit_agent_event(
                    {
                        "type": "specialist_quorum_degraded",
                        "task_id": task.task_id,
                        "agent_id": assignee,
                        "role": task.role.value,
                        "phase": _phase,
                        "reason": "specialist_quorum_not_met",
                        "missing_specialists": quorum_metadata["specialist_quorum_missing"],
                        "received_specialists": quorum_metadata["specialist_quorum_received"],
                    }
                )
            else:
                self.taskboard.mark_blocked(task.task_id, reason="specialist_quorum_not_met")
                self.taskboard.update_metadata(task.task_id, quorum_metadata)
                self._emit_agent_event(
                    {
                        "type": "agent_blocked",
                        "task_id": task.task_id,
                        "agent_id": assignee,
                        "role": task.role.value,
                        "phase": _phase,
                        "reason": "specialist_quorum_not_met",
                    }
                )
                self.session_store.close_session(
                    session, summary="specialist_quorum_not_met", status="blocked"
                )
                return
        peer_report = self._run_peer_consultation(task=task, assignee=assignee)
        peer_report = self._sanitize_peer_consultation_report(task=task, report=peer_report)
        peer_context = peer_report.text
        decision_governance = self._build_decision_governance_context(
            task=task,
            assignee=assignee,
            peer_report=peer_report,
        )

        # ── Decision rank enforcement: escalate si criticality alta y rank insuficiente ──
        charter = role_charter_for(task.role)
        if (
            task.criticality.value == "high"
            and charter.decision_rank < 5
            and not task.metadata.get("rank_escalation_approved")
        ):
            escalation_note = (
                f"NOTA DE ESCALACION: Esta tarea tiene criticidad ALTA pero tu rango de decision es "
                f"R{charter.decision_rank}/5. Las decisiones criticas requieren aprobacion R5 (Team Lead). "
                "Incluye en tu respuesta una seccion '[ESCALATION_NEEDED]' si tu decision implica "
                "cambios breaking, deploys a produccion, o modificaciones de seguridad. "
                "El Team Lead revisara y aprobara antes de proceder."
            )
            decision_governance = f"{decision_governance}\n{escalation_note}"
            task.metadata["rank_escalation_flagged"] = True
            self.event_logger.emit(
                "decision_rank_escalation",
                {
                    "task_id": task.task_id,
                    "role": task.role.value,
                    "assignee": assignee,
                    "agent_rank": charter.decision_rank,
                    "required_rank": 5,
                    "criticality": task.criticality.value,
                },
        )
        context = self.compliance.sanitize_context(context) if context else ""
        peer_context = (
            self.compliance.sanitize_context(peer_context) if peer_context else ""
        )
        decision_governance = (
            self.compliance.sanitize_context(decision_governance)
            if decision_governance
            else ""
        )
        skill_mcp_context = (
            self.compliance.sanitize_context(skill_mcp_context)
            if skill_mcp_context
            else ""
        )
        execution_context = (
            self.compliance.sanitize_context(execution_context)
            if execution_context
            else ""
        )

        ab_version = str(task.metadata.get("prompt_ab_version", "A"))
        prompt = build_prompt(
            task.role,
            task.title,
            task.description,
            ab_version=ab_version,
            task_metadata=task.metadata,
        )

        # ── Tool context: herramientas disponibles + recomendaciones ──
        if self.tool_dispatcher is not None:
            required_caps = set(task.metadata.get("required_capabilities", []))
            tool_context = self.tool_dispatcher.build_tool_context_for_agent(
                role=task.role.value,
                required_capabilities=required_caps,
                task_description=f"{task.title}\n{task.description}",
            )
            if tool_context:
                prompt = f"{prompt}\n\n{tool_context}"

        # ── Review feedback injection (gate iteration loop) ──
        review_feedback = task.metadata.get("review_feedback")
        gate_iteration = int(task.metadata.get("gate_iteration", 0))
        review_feedback_text = ""
        if review_feedback:
            review_feedback_text = (
                f"Iteracion {gate_iteration or 1}. "
                f"{review_feedback}\n"
                "Corrige cada punto de feedback de forma explicita."
            )

        # ── Session history: inyectar resumen del intento previo en retries ──
        prev_summary = ""
        if gate_iteration > 0:
            prev_sessions = self.session_store.sessions_for_task(task.task_id)
            if prev_sessions:
                last = prev_sessions[-1]
                raw_actions = (
                    last.get("actions")
                    if isinstance(last, dict)
                    else (last.actions or [])
                )
                actions_summary = "; ".join(
                    f"{a.get('action_type', '')}:{str(a.get('detail', ''))[:60]}"
                    if isinstance(a, dict)
                    else f"{a.action_type}:{a.detail[:60]}"
                    for a in (raw_actions or [])[-5:]
                )
                last_status = (
                    last.get("status") if isinstance(last, dict) else last.status
                )
                last_summary = (
                    last.get("summary") if isinstance(last, dict) else last.summary
                )
                prev_summary = (
                    f"Intento anterior (iteracion {gate_iteration - 1}): "
                    f"status={last_status or 'unknown'}, "
                    f"acciones=[{actions_summary}], "
                    f"resumen={self._compact_text(last_summary or '', 200)}"
                )

        run_health_report = self._build_run_health_prompt_block(task)
        lead_close_policy = self._build_lead_close_policy_block(task)

        current_user_turn = self._build_current_task_message(
            task,
            context=context,
            peer_context=peer_context,
            decision_governance=decision_governance,
            skill_mcp_context=skill_mcp_context,
            execution_context=execution_context,
            review_feedback=review_feedback_text,
            gate_iteration=gate_iteration,
            prev_summary=prev_summary,
            run_health_report=run_health_report,
            lead_close_policy=lead_close_policy,
        )
        messages = self._build_task_messages(
            task,
            assignee=assignee,
            ab_version=ab_version,
            thread=thread,
            context=context,
            peer_context=peer_context,
            decision_governance=decision_governance,
            skill_mcp_context=skill_mcp_context,
            execution_context=execution_context,
            review_feedback=review_feedback_text,
            gate_iteration=gate_iteration,
            prev_summary=prev_summary,
            run_health_report=run_health_report,
            lead_close_policy=lead_close_policy,
        )
        attempt_message_cache: dict[tuple[str, str, str], tuple[ConversationThread, list[dict[str, str]]]] = {}

        def _resolve_attempt_messages(adapter) -> list[dict[str, str]]:
            cache_key = (
                str(getattr(adapter, "provider", "") or "").strip().lower(),
                str(getattr(getattr(adapter, "channel", ""), "value", "") or "").strip().lower(),
                self._thread_model_family(str(getattr(adapter, "model", "") or "")),
            )
            cached = attempt_message_cache.get(cache_key)
            if cached is not None:
                return cached[1]
            bound_thread, bound_messages = self._messages_for_adapter_attempt(
                task=task,
                assignee=assignee,
                ab_version=ab_version,
                base_thread=thread,
                adapter=adapter,
                context=context,
                peer_context=peer_context,
                decision_governance=decision_governance,
                skill_mcp_context=skill_mcp_context,
                execution_context=execution_context,
                review_feedback=review_feedback_text,
                gate_iteration=gate_iteration,
                prev_summary=prev_summary,
                run_health_report=run_health_report,
                lead_close_policy=lead_close_policy,
            )
            attempt_message_cache[cache_key] = (bound_thread, bound_messages)
            return bound_messages
        requested_approved_adapters = task.metadata.get("approved_adapters", [])
        if requested_approved_adapters and not sensitive_approval:
            self._fail_task_due_to_compliance(
                task=task,
                assignee=assignee,
                reason=approval_reason,
                details=["approved_adapters_requires_sensitive_approval"],
            )
            self.session_store.close_session(
                session, summary="approved_adapters_blocked", status="failed"
            )
            return

        request = RoutingRequest(
            role=task.role,
            complexity=task.complexity,
            criticality=task.criticality,
            required_capabilities=self._routing_capabilities_for_task(task),
            tool_specialist=str(task.metadata.get("tool_specialist", "") or "").strip(),
            tool_rewiring_preferred_specialist=str(
                task.metadata.get("tool_rewiring_preferred_specialist", "") or ""
            ).strip(),
            prefer_economic_routing=self._to_bool(
                task.metadata.get("tool_specialist_economic_routing", False)
            ),
            preferred_tool_tier=self._resolve_task_preferred_tool_tier(task),
            skill_targets={
                str(item).strip().lower()
                for item in list(task.metadata.get("skill_targets", []) or [])
                if str(item).strip()
            },
            lsp_targets={
                str(item).strip().lower()
                for item in list(task.metadata.get("lsp_targets", []) or [])
                if str(item).strip()
            },
            approved_adapters=self.compliance.approved_adapters(task.metadata),
            preferred_adapters={
                str(name).strip()
                for name in list(task.metadata.get("preferred_adapters", []) or [])
                if str(name).strip()
            },
            excluded_adapters={
                str(name).strip()
                for name in list(task.metadata.get("excluded_adapters", []) or [])
                if str(name).strip()
            },
            sensitive_approval=sensitive_approval,
            environment=self.environment,
        )
        session.record_action("llm_call", f"route_and_invoke:{task.role.value}")
        native_tools = self._build_native_tools_for_task(task)

        # ── Emitir agent_started antes de la llamada al LLM ──
        self._emit_agent_event(
            {
                "type": "agent_started",
                "task_id": task.task_id,
                "agent_id": assignee,
                "role": task.role.value,
                "phase": _phase,
                "title": task.title,
            }
        )

        on_chunk = None
        if (
            self.token_chunk_callback is not None
            or self.agent_event_callback is not None
        ):
            _tid_for_chunk = task.task_id
            _role_for_chunk = task.role.value
            _phase_for_chunk = _phase
            _agent_for_chunk = assignee
            _token_cb = self.token_chunk_callback
            _agent_cb = self.agent_event_callback

            def on_chunk(
                chunk: str | StreamChunk,
                _tid=_tid_for_chunk,
                _role=_role_for_chunk,
                _ph=_phase_for_chunk,
                _aid=_agent_for_chunk,
                _tcb=_token_cb,
                _acb=_agent_cb,
            ) -> None:
                chunk_text = chunk.text if isinstance(chunk, StreamChunk) else chunk
                chunk_type = (
                    chunk.chunk_type if isinstance(chunk, StreamChunk) else "output"
                )
                if _tcb is not None and chunk_type == "output" and chunk_text:
                    _tcb(_tid, chunk_text)
                if _acb is not None:
                    try:
                        _acb(
                            {
                                "type": "agent_chunk",
                                "task_id": _tid,
                                "agent_id": _aid,
                                "role": _role,
                                "phase": _ph,
                                "chunk": chunk_text,
                                "chunk_type": chunk_type,
                            }
                        )
                    except Exception:
                        pass

        decision = self._route_and_invoke_with_compat(
            request=request,
            prompt=prompt,
            task_id=task.task_id,
            messages=messages,
            messages_resolver=_resolve_attempt_messages,
            tools=native_tools if native_tools else None,
            on_chunk=on_chunk,
        )
        session.record_action(
            "llm_call",
            f"response:{decision.provider}/{decision.model}",
            success=decision.success,
            duration_ms=int(decision.response.latency_ms),
            metadata={
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
                "input_tokens": decision.response.input_tokens,
                "output_tokens": decision.response.output_tokens,
            },
        )
        session.total_tokens += (
            decision.response.input_tokens + decision.response.output_tokens
        )
        # ── Adaptive error recovery: strategy switching on failures ──
        if not decision.success:
            retry_count = int(task.metadata.get("retry_count", 0))
            failure_reason = decision.reason or "unknown"

            if failure_reason == "no_eligible_adapter" and self._to_bool(
                task.metadata.get("auto_discover_tools", True)
            ):
                # Strategy 1: auto-discover tools
                suggestion_report = self._auto_discover_tools(
                    task=task,
                    assignee=assignee,
                    internet_allowed=(self.environment != "prod" or sensitive_approval),
                )
                if suggestion_report.integrated_adapters:
                    self._sync_router_external_adapters()
                    session.record_action("llm_call", "retry_after_tool_discovery")
                    decision = self._route_and_invoke_with_compat(
                        request=request,
                        prompt=prompt,
                        task_id=task.task_id,
                        messages=messages,
                        messages_resolver=_resolve_attempt_messages,
                    )

            if not decision.success and retry_count < 2:
                # Strategy 2: widen adapter eligibility (allow any channel)
                fallback_request = RoutingRequest(
                    role=request.role,
                    complexity=request.complexity,
                    criticality=request.criticality,
                    required_capabilities=set(),  # relax capabilities
                    tool_specialist=request.tool_specialist,
                    tool_rewiring_preferred_specialist=request.tool_rewiring_preferred_specialist,
                    prefer_economic_routing=request.prefer_economic_routing,
                    preferred_tool_tier=request.preferred_tool_tier,
                    skill_targets=set(request.skill_targets or set()),
                    lsp_targets=set(request.lsp_targets or set()),
                    approved_adapters=request.approved_adapters,
                    preferred_adapters=set(request.preferred_adapters or set()),
                    excluded_adapters=set(request.excluded_adapters or set()),
                    excluded_providers=set(request.excluded_providers or set()),
                    sensitive_approval=request.sensitive_approval,
                    environment=request.environment,
                )
                session.record_action(
                    "llm_call",
                    f"adaptive_retry_{retry_count + 1}:relaxed_capabilities",
                )
                decision = self._route_and_invoke_with_compat(
                    request=fallback_request,
                    prompt=prompt,
                    task_id=task.task_id,
                    messages=messages,
                    messages_resolver=_resolve_attempt_messages,
                )

            if not decision.success:
                # Record failure mode for future learning
                self._remember_memory(
                    agent_id=assignee,
                    role=task.role.value,
                    kind="failure_pattern",
                    content=(
                        f"task_type={task.task_id.split('::')[-1] if '::' in task.task_id else task.role.value} "
                        f"failure={failure_reason} provider={decision.provider} "
                        f"model={decision.model} retry_count={retry_count}"
                    ),
                    task_id=task.task_id,
                    tags=["failure", "recovery", failure_reason],
                )
                self.event_logger.emit(
                    "adaptive_retry_exhausted",
                    {
                        "task_id": task.task_id,
                        "assignee": assignee,
                        "failure_reason": failure_reason,
                        "retry_count": retry_count,
                        "strategies_tried": ["tool_discovery", "relaxed_capabilities"],
                    },
                )

        thread = self._bind_runtime_thread(
            thread=thread,
            assignee=assignee,
            role=task.role,
            decision=decision,
            task_id=task.task_id,
        )
        persisted_user_turn = self._build_persisted_thread_task_turn(
            task,
            gate_iteration=gate_iteration,
            current_user_turn=current_user_turn,
        )
        thread.append_turn(
            role="user",
            content=persisted_user_turn,
            source="task_retry" if gate_iteration > 0 else "task",
            task_id=task.task_id,
        )
        self.thread_store.save_thread(thread)
        self._emit_agent_event(
            {
                "type": "agent_routed",
                "task_id": task.task_id,
                "agent_id": assignee,
                "role": task.role.value,
                "phase": _phase,
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
                "success": decision.success,
                "thread_id": thread.thread_id,
                "thread_generation": int(getattr(thread, "generation", 1) or 1),
            }
        )

        self.event_logger.emit(
            "task_execution",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "assignee": assignee,
                "success": decision.success,
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
                "latency_ms": int(decision.response.latency_ms),
                "execution_round": execution_round,
                "execution_sub_iteration": execution_sub_iteration,
                "execution_order": execution_order,
                "gate_iteration": gate_iteration,
                "thread_id": thread.thread_id,
                "thread_generation": int(getattr(thread, "generation", 1) or 1),
                "thread_version": str(getattr(thread, "thread_version", "") or ""),
                "thread_provider": str(getattr(thread, "provider", "") or ""),
                "thread_channel": str(getattr(thread, "channel", "") or ""),
                "thread_model_family": str(getattr(thread, "model_family", "") or ""),
            },
        )

        current_total = task.metadata.get("total_latency_ms", 0)
        task.metadata["total_latency_ms"] = current_total + int(
            decision.response.latency_ms
        )
        _selected_adapter_name = self._selected_adapter_name(decision)
        if _selected_adapter_name:
            task.metadata["last_adapter_name"] = _selected_adapter_name
        task.metadata["last_provider"] = decision.provider
        task.metadata["last_model"] = decision.model
        task.metadata["last_channel"] = decision.channel.value

        role_key = f"latency_{task.role.value}_ms"
        current_role_time = task.metadata.get(role_key, 0)
        task.metadata[role_key] = current_role_time + int(decision.response.latency_ms)

        task_type = (
            task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        )
        self._update_agent_performance(
            assignee=assignee, decision=decision, task_type=task_type
        )

        # ── Native function calling: si el LLM pidio herramientas, ejecutar y re-invocar ──
        if (
            decision.success
            and decision.response.tool_calls
            and not task.metadata.get("_native_tool_round_done")
        ):
            task.metadata["_native_tool_round_done"] = True
            tc_results = self._execute_native_tool_calls(
                decision.response.tool_calls, task, assignee, session
            )
            tool_summary_lines = []
            for r in tc_results:
                status = "OK" if r["success"] else "ERROR"
                body = r["output"] if r["success"] else r["error"]
                tool_summary_lines.append(f"[{r['name']}] {status}: {body[:800]}")
            tool_msg = "Resultados de herramientas:\n" + "\n".join(tool_summary_lines)
            followup_messages = list(messages or []) + [
                {
                    "role": "assistant",
                    "content": f"[Usando herramientas: {', '.join(tc.name for tc in decision.response.tool_calls)}]",
                },
                {
                    "role": "user",
                    "content": tool_msg
                    + "\n\nContinua con tu tarea usando los resultados anteriores.",
                },
            ]
            decision = self._route_and_invoke_with_compat(
                request=request,
                prompt=prompt,
                task_id=task.task_id,
                messages=followup_messages,
                messages_resolver=None,
                tools=None,  # no tools en el segundo round para evitar bucle infinito
            )
            session.record_action(
                "llm_call", f"native_tool_followup:{len(tc_results)}_tools"
            )

        if decision.success:
            safe_content = self.compliance.redact_text(decision.response.content)
            _specialist_name_early = str(task.metadata.get("tool_specialist", "") or "").strip().lower()

            # ── Agent tool invocation: parsear [USE_TOOL] y ejecutar ──
            if self._USE_TOOL_RE.search(safe_content):
                safe_content, _tool_results = self._parse_and_invoke_tools(
                    task,
                    assignee,
                    safe_content,
                    session,
                )
                safe_content = self.compliance.redact_text(safe_content)

            if self._fail_task_for_continuation_drift(
                task=task,
                assignee=assignee,
                safe_content=safe_content,
                session=session,
            ):
                return

            _plan_phase_name = self._phase_name_for_task(task).lower()
            _raw_planning_content = safe_content
            if _plan_phase_name == "plan_risks":
                safe_content, _risks_sanitized = self._sanitize_plan_risks_output(
                    safe_content
                )
                if _risks_sanitized:
                    task.metadata["plan_risks_output_sanitized"] = True
                    self.event_logger.emit(
                        "plan_risks_output_sanitized",
                        {
                            "task_id": task.task_id,
                            "phase": _plan_phase_name,
                            "assignee": assignee,
                        },
                    )
                _raw_planning_content = safe_content

            # Para planning, validamos drift antes del parser/materializacion.
            # En plan_risks primero saneamos lineas operativas residuales para
            # no tumbar una salida estructurada correcta por narrativa sobrante.
            if self._fail_task_for_planning_phase_implementation_drift(
                task=task,
                assignee=assignee,
                safe_content=_raw_planning_content,
                session=session,
            ):
                return

            if _plan_phase_name.startswith("plan_"):
                safe_content, _code_stripped = self._sanitize_planning_phase_output(safe_content)
                if _code_stripped:
                    self.event_logger.emit(
                        "planning_phase_code_stripped",
                            {"task_id": task.task_id, "phase": _plan_phase_name, "assignee": assignee},
                        )
                safe_content = self._repair_plan_risks_upstream_relitigation(
                    task=task,
                    safe_content=safe_content,
                    assignee=assignee,
                )
            elif task.role == Role.REVIEWER:
                safe_content, _review_sanitized = self._sanitize_review_output(safe_content)
                if _review_sanitized:
                    task.metadata["review_output_sanitized"] = True
                    self.event_logger.emit(
                        "review_output_sanitized",
                        {
                            "task_id": task.task_id,
                            "phase": _plan_phase_name,
                            "assignee": assignee,
                        },
                    )
            elif task.role == Role.TEAM_LEAD and _plan_phase_name == "lead_close":
                safe_content, _lead_close_sanitized = self._sanitize_lead_close_output(safe_content)
                if _lead_close_sanitized:
                    task.metadata["lead_close_output_sanitized"] = True
                    self.event_logger.emit(
                        "lead_close_output_sanitized",
                        {
                            "task_id": task.task_id,
                            "phase": _plan_phase_name,
                            "assignee": assignee,
                        },
                    )

            if _specialist_name_early == "test_runner":
                safe_content, _test_runner_sanitized = self._sanitize_test_runner_output(
                    task=task,
                    content=safe_content,
                    workspace=workspace,
                )
                if _test_runner_sanitized:
                    task.metadata["test_runner_output_sanitized"] = True
                    self.event_logger.emit(
                        "test_runner_output_sanitized",
                        {
                            "task_id": task.task_id,
                            "phase": _plan_phase_name,
                            "assignee": assignee,
                        },
                    )

            if self._fail_task_for_contract_path_drift(
                task=task,
                assignee=assignee,
                safe_content=safe_content,
                session=session,
            ):
                return

            if self._fail_task_for_missing_planning_artifact(
                task=task,
                assignee=assignee,
                safe_content=safe_content,
                session=session,
            ):
                return

            # ── Fix B: extraccion de bloques de codigo con path anotado ──
            # Fallback cuando filesystem_mcp no esta disponible: el Engineer
            # incluye codigo con ```lang path=archivo.py en su output y el
            # orchestrator lo escribe directamente al workspace del proyecto.
            if (
                (
                    task.role == Role.ENGINEER
                    or bool(task.metadata.get("direct_coding_executor", False))
                )
                and not self._phase_name_for_task(task).lower().startswith("plan_")
            ):
                self._extract_and_write_code_blocks(task, safe_content)

                # Post-write validation para solo_lead / direct_coding_executor:
                # corre py_compile en cada .py escrito. Si falla → mark_failed con
                # el error real; nunca marcar completed con archivos rotos.
                if bool(task.metadata.get("direct_coding_executor", False)):
                    _pw_error = self._solo_lead_post_write_validation(task)
                    if _pw_error:
                        self.taskboard.mark_failed(task.task_id, error=_pw_error)
                        self._maybe_spawn_lead_failure_checkpoint(task, _pw_error)
                        self.event_logger.emit(
                            "solo_lead_build_failed_post_write",
                            {
                                "task_id": task.task_id,
                                "phase": _phase_name,
                                "error": _pw_error[:300],
                                "artifact_paths": list(task.metadata.get("artifact_paths", []) or [])[:8],
                            },
                        )
                        self.session_store.close_session(
                            session, summary=f"post_write_syntax_error:{_pw_error[:120]}", status="failed"
                        )
                        return
                    task.metadata["post_write_validated"] = True

                    # Gap 4: correr pytest tras escritura exitosa y guardar resultado
                    # para que lead_close lo consuma. No bloquea si pytest falla —
                    # el Lead decide si el fallo requiere reparacion.
                    _pytest_task_root = self._task_root(task.task_id)
                    task.metadata["solo_lead_pytest_result"] = self._run_solo_lead_pytest(
                        task, _pytest_task_root
                    )

            found_placeholders = [
                label
                for label, pattern in _PLACEHOLDER_OUTPUT_PATTERNS
                if pattern.search(safe_content)
            ]
            found_placeholders = self._effective_placeholder_labels(task, found_placeholders)
            if found_placeholders and not task.metadata.get("skip_placeholder_check"):
                reason = f"Placeholder detected: {', '.join(found_placeholders)}"
                self.taskboard.mark_failed(task.task_id, error=reason)
                self._maybe_spawn_lead_failure_checkpoint(task, reason)
                self._maybe_run_event_meeting(
                    trigger="task_failed",
                    task_id=task.task_id,
                    reason=reason,
                )
                self.event_logger.emit(
                    "placeholder_gate_failed",
                    {"task_id": task.task_id, "assignee": assignee, "reason": reason},
                )
                self.session_store.close_session(
                    session, summary=f"placeholder:{reason}", status="failed"
                )
                return

            self._persist_decision_record(
                task=task,
                assignee=assignee,
                decision=decision,
                output=safe_content,
                peer_report=peer_report,
            )
            specialist_name = str(task.metadata.get("tool_specialist", "") or "").strip().lower()
            if specialist_name:
                parsed_report = parse_specialist_report(
                    safe_content,
                    specialist=specialist_name,
                    provider=decision.provider,
                    model=decision.model,
                    toolset_used=list(task.metadata.get("tool_specialist_tool_families", []) or []),
                    tokens_used=decision.response.input_tokens + decision.response.output_tokens,
                )
                _report_added = self._append_specialist_report_once(
                    task=task,
                    report=parsed_report.to_metadata(),
                )
                self.event_logger.emit(
                    "specialist_report_parsed",
                    {
                        "task_id": task.task_id,
                        "specialist": specialist_name,
                        "provider": decision.provider,
                        "model": decision.model,
                        "validation_status": parsed_report.validation_status,
                        "validation_errors": list(parsed_report.validation_errors),
                        "report_version": parsed_report.report_version,
                        "deduplicated": not _report_added,
                    },
                )
            thread.append_turn(
                role="assistant",
                content=safe_content,
                source="llm",
                task_id=task.task_id,
            )
            self.thread_store.save_thread(thread)
            self._maybe_reply_to_team_lead(
                task=task,
                assignee=assignee,
                response_text=safe_content,
                consumed_messages=consumed_mailbox_messages,
            )
            self._remember_memory(
                agent_id=assignee,
                role=task.role.value,
                kind="task_success",
                content=safe_content,
                task_id=task.task_id,
                tags=["result", decision.channel.value],
            )
            # ── E7-D4: Pausa mid-run — DEBE ir ANTES del evidence gate ─────────
            # Si el agente emitió [CLARIFY], pausar la tarea antes de evaluar
            # evidencia. Una tarea que pide aclaración no produce artefactos y
            # debe ser tratada como pausada, no como fallida.
            # Excluir lead_intake (manejado por api/main.py) y scouts.
            # lead_close SI puede pausar: es el primer checkpoint real del Lead
            # durante la run antes del cierre final.
            _phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
            _is_scout_task = task.metadata.get("is_scout", False) or task.role.value == "scout"
            _can_pause_early = (
                not _is_scout_task
                and task.role.value != "scout"
                and _phase_name != "lead_intake"
            )
            if _can_pause_early:
                import re as _re_mid
                _mid_clarify = _re_mid.search(
                    r'\[CLARIFY:\s*"(.+?)"\]', safe_content,
                    _re_mid.DOTALL | _re_mid.IGNORECASE,
                )
                if _mid_clarify:
                    _mid_cq = _mid_clarify.group(1).strip()
                    if self._direct_profile_should_suppress_midrun_clarify(task, _mid_cq):
                        self.event_logger.emit(
                            "direct_profile_clarify_suppressed",
                            {
                                "task_id": task.task_id,
                                "phase": _phase_name,
                                "question": _mid_cq[:500],
                                "reason": "solo_lead_autonomy",
                            },
                        )
                        safe_content = _re_mid.sub(
                            r'\[CLARIFY:\s*".+?"\]',
                            "[CLARIFY_SUPPRESSED: solo_lead repair-first continues autonomously]",
                            safe_content,
                            flags=_re_mid.DOTALL | _re_mid.IGNORECASE,
                        )
                    else:
                        _wstate_root = self._task_root(task.task_id)
                        _wstate_phase = _phase_name or task.role.value
                        self._update_workflow_state(_wstate_root, _wstate_phase, safe_content)
                        self.taskboard.mark_waiting_user(task.task_id, question=_mid_cq)
                        self.event_logger.emit(
                            "chat_waiting_user",
                            {
                                "task_id": task.task_id,
                                "phase": _phase_name,
                                "question": _mid_cq,
                            },
                        )
                        self.session_store.close_session(
                            session, summary="mid_run_waiting_user", status="paused"
                        )
                        return

            # Evidence gate: solo para fases de build/ejecucion, no para plan_* ni scouts
            _is_planning_phase = _is_scout_task or _phase_name.startswith("plan_") or _phase_name in (
                "lead_intake",
                "lead_close",
                "discovery",
            )
            # Guardar output del agente para evidence gate conversacional
            task.metadata["_last_agent_output"] = safe_content
            self._append_agent_output_history(task, safe_content)
            # Auto-detectar tarea conversacional/teorica si no ya marcada
            if not task.metadata.get("conversational") and not _is_planning_phase:
                if self._detect_conversational_task(task):
                    task.metadata["conversational"] = True
            if (
                task.role in (Role.ENGINEER, Role.REVIEWER, Role.QA)
                and not task.metadata.get("skip_evidence_gate")
                and not _is_planning_phase
            ):
                has_evidence, reason = self._verify_task_evidence(task, workspace)
                if not has_evidence:
                    evidence_error = f"EvidenceGate Blocked: {reason}"
                    self.taskboard.mark_failed(task.task_id, error=evidence_error)
                    self._maybe_spawn_lead_failure_checkpoint(task, evidence_error)
                    self._maybe_run_event_meeting(
                        trigger="task_failed",
                        task_id=task.task_id,
                        reason=evidence_error,
                    )
                    self.event_logger.emit(
                        "evidence_gate_failed",
                        {
                            "task_id": task.task_id,
                            "assignee": assignee,
                            "reason": reason,
                        },
                    )
                    self.session_store.close_session(
                        session, summary=f"evidence_gate:{reason}", status="failed"
                    )
                    return
                task.metadata["evidence_reason"] = reason

            if self._fail_task_for_ungrounded_evidence_output(
                task=task,
                assignee=assignee,
                safe_content=safe_content,
                workspace=workspace,
                session=session,
            ):
                return

            verdict = extract_phase_verdict(safe_content, phase_id=_phase)
            verdict_status = str(verdict.get("status", "") or "").strip().lower()
            if (
                verdict_status in {"blocked", "partial", "rejected", "failed"}
                and not _is_scout_task
                and task.role != Role.TEAM_LEAD
            ):
                self._update_workflow_state(
                    self._task_root(task.task_id),
                    _phase,
                    safe_content,
                )
                is_chat_phase = bool(task.metadata.get("phase_contract_enforced")) or bool(
                    task.metadata.get("chat_parent")
                ) or self._task_root(task.task_id).startswith("CHAT-")
                if (
                    is_chat_phase
                    and task.role == Role.RESEARCHER
                    and verdict_status in {"blocked", "partial"}
                ):
                    self._complete_research_phase_as_degraded(
                        task=task,
                        assignee=assignee,
                        safe_content=safe_content,
                        verdict_status=verdict_status,
                        session=session,
                    )
                    return
                if verdict_status in {"blocked", "partial"}:
                    ungrounded_block = self._detect_ungrounded_phase_block_issue(
                        task=task,
                        safe_content=safe_content,
                        workspace=workspace,
                    )
                    if ungrounded_block:
                        if self._retry_executor_on_stale_dependency_block(
                            task=task,
                            assignee=assignee,
                            safe_content=safe_content,
                            issue=ungrounded_block,
                            session=session,
                        ):
                            return
                        if self._retry_executor_on_recoverable_ungrounded_phase_block(
                            task=task,
                            assignee=assignee,
                            safe_content=safe_content,
                            issue=ungrounded_block,
                            session=session,
                        ):
                            return
                        if self._complete_validation_visibility_issue_as_degraded(
                            task=task,
                            assignee=assignee,
                            safe_content=safe_content,
                            issue=ungrounded_block,
                            session=session,
                        ):
                            return
                        summary = "ungrounded_phase_block"
                        visible_files = list(ungrounded_block.get("visible_files", []) or [])
                        dependency_artifacts = list(
                            ungrounded_block.get("dependency_artifacts", []) or []
                        )
                        if visible_files:
                            summary += f" | visible={', '.join(visible_files[:4])}"
                        if dependency_artifacts:
                            summary += (
                                f" | dependency_artifacts={', '.join(dependency_artifacts[:4])}"
                            )
                        self.taskboard.mark_failed(
                            task.task_id,
                            error=f"ungrounded_phase_block_detected: {summary}",
                        )
                        self.taskboard.update_metadata(
                            task.task_id,
                            {
                                "result": safe_content,
                                "ungrounded_phase_block_issue": dict(ungrounded_block),
                            },
                        )
                        self.event_logger.emit(
                            "ungrounded_phase_block_detected",
                            {
                                "task_id": task.task_id,
                                "assignee": assignee,
                                "phase": _phase,
                                **dict(ungrounded_block),
                            },
                        )
                        self._emit_agent_event(
                            {
                                "type": "agent_failed",
                                "task_id": task.task_id,
                                "agent_id": assignee,
                                "role": task.role.value,
                                "phase": _phase,
                                "error": summary,
                                "full_text": safe_content,
                                "duration_ms": int(decision.response.latency_ms),
                                "provider": decision.provider,
                                "model": decision.model,
                                "channel": decision.channel.value,
                            }
                        )
                    else:
                        self.taskboard.mark_blocked(
                            task.task_id,
                            reason=(
                                "phase_self_reported_partial"
                                if verdict_status == "partial"
                                else "phase_self_reported_blocked"
                            ),
                        )
                        self.taskboard.update_metadata(task.task_id, {"result": safe_content})
                        self._emit_agent_event(
                            {
                                "type": "agent_blocked",
                                "task_id": task.task_id,
                                "agent_id": assignee,
                                "role": task.role.value,
                                "phase": _phase,
                                "preview": safe_content[:200] if safe_content else "",
                                "full_text": safe_content,
                                "duration_ms": int(decision.response.latency_ms),
                                "provider": decision.provider,
                                "model": decision.model,
                                "channel": decision.channel.value,
                            }
                        )
                else:
                    self.taskboard.mark_failed(
                        task.task_id,
                        error=str(verdict.get("summary", "") or verdict_status),
                    )
                    self.taskboard.update_metadata(task.task_id, {"result": safe_content})
                    self._emit_agent_event(
                        {
                            "type": "agent_failed",
                            "task_id": task.task_id,
                            "agent_id": assignee,
                            "role": task.role.value,
                            "phase": _phase,
                            "error": str(verdict.get("summary", "") or safe_content[:200]),
                            "full_text": safe_content,
                            "duration_ms": int(decision.response.latency_ms),
                            "provider": decision.provider,
                            "model": decision.model,
                            "channel": decision.channel.value,
                        }
                    )
                self.session_store.close_session(
                    session,
                    summary=f"phase_verdict:{verdict_status}",
                    status="failed" if verdict_status in {"rejected", "failed"} else "completed",
                )
                return

            if self._should_open_quality_gates(task):
                self._spawn_quality_gates(task)
                self.taskboard.mark_blocked(
                    task.task_id,
                    reason="waiting_quality_gates",
                )
                self.taskboard.update_metadata(
                    task.task_id,
                    {"gate_opened_at": datetime.now(timezone.utc).isoformat()},
                )
                gate_titles = [
                    g.title
                    for g in self.taskboard.list_tasks()
                    if g.metadata.get("parent_task") == task.task_id
                ]
                gates_str = ", ".join(gate_titles) if gate_titles else "Review, QA"
                self.communicator.broadcast(
                    sender=task.role.value,
                    subject=f"Quality gates opened: {task.task_id}",
                    body=(
                        f"La tarea '{task.title}' requiere revision.\n"
                        f"Gates activos: {gates_str}\n"
                        f"-> Revisa el Diff de evidencia y actua en el Dashboard."
                    ),
                    task_id=task.task_id,
                )
                self._maybe_run_event_meeting(
                    trigger="quality_gate_opened",
                    task_id=task.task_id,
                    reason="review_qa_required",
                )
                self.session_store.close_session(
                    session, summary="waiting_quality_gates", status="completed"
                )
                return

            # Guardia post-write para solo_lead: si el Lead escribió archivos .py
            # via filesystem_mcp (no code blocks) y no hay validación registrada,
            # corre py_compile como safety net antes de marcar completed.
            if bool(task.metadata.get("direct_coding_executor", False)):
                _artifacts = [
                    p for p in list(task.metadata.get("artifact_paths", []) or [])
                    if str(p).strip().endswith(".py")
                ]
                if _artifacts and not task.metadata.get("post_write_validated"):
                    _pw_late_error = self._solo_lead_post_write_validation(task)
                    if _pw_late_error:
                        self.taskboard.mark_failed(task.task_id, error=_pw_late_error)
                        self._maybe_spawn_lead_failure_checkpoint(task, _pw_late_error)
                        self.event_logger.emit(
                            "solo_lead_build_failed_late_validation",
                            {
                                "task_id": task.task_id,
                                "phase": _phase_name,
                                "error": _pw_late_error[:300],
                            },
                        )
                        self.session_store.close_session(
                            session, summary=f"late_post_write_error:{_pw_late_error[:120]}", status="failed"
                        )
                        return
                    task.metadata["post_write_validated"] = True

            self.taskboard.mark_completed(task.task_id, details=safe_content)
            self._emit_agent_event(
                {
                    "type": "agent_completed",
                    "task_id": task.task_id,
                    "agent_id": assignee,
                    "role": task.role.value,
                    "phase": _phase,
                    "preview": safe_content[:200] if safe_content else "",
                    "full_text": safe_content,
                    "duration_ms": int(decision.response.latency_ms),
                    "provider": decision.provider,
                    "model": decision.model,
                    "channel": decision.channel.value,
                }
            )

            # ── Agent self-delegation: parsear [REQUEST_TASK] del output ──
            delegated = self._parse_agent_requests(task, assignee, safe_content)
            if delegated:
                titles = [d.title for d in delegated]
                self.communicator.broadcast(
                    sender=assignee,
                    subject=f"Sub-tareas delegadas desde {task.task_id}",
                    body=f"Nuevas sub-tareas creadas: {', '.join(titles)}",
                    task_id=task.task_id,
                )

            # ── Actualizar workflow state y ledger ──
            task_root = self._task_root(task.task_id)
            phase_name = (
                task.task_id.split("::")[-1]
                if "::" in task.task_id
                else task.role.value
            )
            self._update_workflow_state(task_root, phase_name, safe_content)
            self._update_team_ledger(task, assignee, safe_content, success=True)

            # ── Registrar uso de skills recomendadas ──
            recommended_skills = task.metadata.get("_recommended_skills", [])
            for skill_name in recommended_skills:
                self.tool_integrator.record_skill_usage(
                    skill_name=skill_name,
                    task_id=task.task_id,
                    agent_id=assignee,
                    role=task.role.value,
                    success=True,
                    duration_ms=session.elapsed_ms(),
                )

            self.mailbox.send(
                sender=task.role.value,
                recipient="team_lead",
                subject=f"Task completed: {task.task_id}",
                body=(
                    f"provider={decision.provider} model={decision.model} "
                    f"channel={decision.channel.value} attempts={decision.attempts}"
                ),
                task_id=task.task_id,
            )
            self._notify_dependents(task.task_id, summary=safe_content, task_role=task.role.value)
            self._maybe_spawn_lead_report_checkpoint(task, safe_content)

            # ── Cerrar sesion exitosa ──
            self.session_store.close_session(
                session, summary=safe_content[:300], status="completed"
            )
            return

        failure_text = self.compliance.redact_text(
            decision.response.error or decision.reason
        )

        # Scouts que fallan se completan con output vacio para no bloquear lead_intake
        if task.metadata.get("is_scout") or task.role.value == "scout":
            task.metadata["scout_failed"] = True
            task.metadata["scout_error"] = failure_text[:200]
            self.taskboard.mark_completed(task.task_id, details="Sin datos disponibles.")
            self.event_logger.emit(
                "scout_graceful_failure",
                {"task_id": task.task_id, "reason": failure_text[:200]},
            )
            self.session_store.close_session(
                session, summary="scout_graceful_failure", status="completed"
            )
            return

        if self._maybe_handoff_and_retry(
            task=task, failed_assignee=assignee, decision=decision
        ):
            self.session_store.close_session(
                session, summary=f"handoff:{failure_text[:200]}", status="handoff"
            )
            return

        self._remember_memory(
            agent_id=assignee,
            role=task.role.value,
            kind="task_failure",
            content=failure_text,
            task_id=task.task_id,
            tags=["failure"],
        )
        self.taskboard.mark_failed(task.task_id, error=failure_text)
        self._maybe_spawn_lead_failure_checkpoint(task, failure_text)
        self._emit_agent_event(
            {
                "type": "agent_failed",
                "task_id": task.task_id,
                "agent_id": assignee,
                "role": task.role.value,
                "phase": _phase,
                "error": failure_text[:200] if failure_text else "",
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
            }
        )

        # ── Ledger de fallo ──
        self._update_team_ledger(task, assignee, failure_text, success=False)

        # ── Cerrar sesion fallida ──
        self.session_store.close_session(
            session, summary=failure_text[:300], status="failed"
        )
        self._maybe_run_event_meeting(
            trigger="task_failed",
            task_id=task.task_id,
            reason=failure_text,
        )
        self.mailbox.send(
            sender=task.role.value,
            recipient="team_lead",
            subject=f"Task failed: {task.task_id}",
            body=(
                f"reason={decision.reason} error={decision.response.error} "
                f"attempts={decision.attempts}"
            ),
            task_id=task.task_id,
        )

    # ── Agent self-delegation ──────────────────────────────────────────

    # ── Agent tool invocation ────────────────────────────────────────

    _USE_TOOL_RE = re.compile(
        r"\[USE_TOOL\s+"
        r"(?:server=(?P<server>[^\s]+)\s+)?"
        r"tool=(?P<tool>[^\s]+)"
        r"(?:\s+args=(?P<args>\{[^}]*\}))?"
        r"\s*\]",
        re.IGNORECASE,
    )

    _MAX_TOOL_CALLS_PER_TASK = 8  # suficiente para scaffold completo de un proyecto (5-8 archivos)

    def _build_native_tools_for_task(self, task: WorkTask) -> list:
        """Convierte herramientas disponibles del task a NativeToolDefinition para function calling."""
        from aiteam.adapters.base import NativeToolDefinition

        if self.tool_dispatcher is None:
            return []
        try:
            tools_info = self.tool_dispatcher.build_tool_context_for_agent(
                role=task.role.value,
                required_capabilities=set(
                    task.metadata.get("required_capabilities", [])
                ),
                task_description=f"{task.title}\n{task.description}",
            )
        except Exception:
            return []
        if not tools_info:
            return []
        # Exponer max 5 tools para no inflar el contexto
        native = []
        for t in list(self.tool_dispatcher._tools.values())[:5]:
            if not t.enabled:
                continue
            native.append(
                NativeToolDefinition(
                    name=t.name,
                    description=t.description or f"Herramienta: {t.name}",
                    parameters={
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "El comando o accion a ejecutar con esta herramienta",
                            }
                        },
                        "required": ["command"],
                    },
                )
            )
        return native[:5]

    def _execute_native_tool_calls(
        self, tool_calls: list, task: WorkTask, assignee: str, session
    ) -> list[dict]:
        """Ejecuta tool_calls nativos y retorna lista de resultados."""
        results = []
        for tc in tool_calls[: self._MAX_TOOL_CALLS_PER_TASK]:
            command = tc.arguments.get("command", tc.name)
            if self.tool_dispatcher is not None:
                result = self.tool_dispatcher.invoke_cli_tool(
                    tool_name=tc.name,
                    command=str(command)[:500],
                    session=session,
                    timeout=60,
                )
            else:
                from aiteam.tool_dispatch import ToolResult

                result = ToolResult(
                    tool_name=tc.name, success=False, error="no_dispatcher"
                )
            self.event_logger.emit(
                "agent_tool_invocation",
                {
                    "task_id": task.task_id,
                    "assignee": assignee,
                    "tool": tc.name,
                    "native": True,
                    "success": result.success,
                },
            )
            results.append(
                {
                    "id": tc.id,
                    "name": tc.name,
                    "success": result.success,
                    "output": (result.output or "")[:2000],
                    "error": (result.error or "")[:500],
                }
            )
        return results

    def _parse_and_invoke_tools(
        self, task: WorkTask, assignee: str, output: str, session: AgentSession
    ) -> tuple[str, list[dict]]:
        """Parse [USE_TOOL] blocks from agent output, invoke tools, return augmented output."""
        matches = list(self._USE_TOOL_RE.finditer(output))
        if not matches:
            return output, []

        matches = matches[: self._MAX_TOOL_CALLS_PER_TASK]
        tool_results: list[dict] = []
        result_texts: list[str] = []

        for m in matches:
            server = m.group("server") or ""
            tool_name = m.group("tool")
            args_raw = m.group("args") or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}

            # Dispatch: MCP tool (has server) or CLI tool (no server)
            if server and self.tool_dispatcher is not None:
                can, reason = self.tool_dispatcher.can_access(
                    f"{server}/{tool_name}",
                    task.role.value,
                    approved=True,
                )
                result = self.tool_dispatcher.invoke_mcp_tool(
                    server_name=server,
                    tool_name=tool_name,
                    arguments=args,
                    session=session,
                    timeout=60,
                )
            elif self.tool_dispatcher is not None:
                can, reason = self.tool_dispatcher.can_access(
                    tool_name,
                    task.role.value,
                    approved=True,
                )
                command = args.get("command", tool_name)
                result = self.tool_dispatcher.invoke_cli_tool(
                    tool_name=tool_name,
                    command=str(command)[:500],
                    session=session,
                    timeout=60,
                )
            else:
                from aiteam.tool_dispatch import ToolResult

                result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error="tool_dispatcher_not_available",
                )

            tool_results.append(
                {
                    "tool": tool_name,
                    "server": server,
                    "success": result.success,
                    "output": result.output[:2000],
                    "error": result.error[:500],
                    "duration_ms": result.duration_ms,
                }
            )
            status = "OK" if result.success else "ERROR"
            result_texts.append(
                f"[TOOL_RESULT tool={tool_name} status={status}]\n"
                f"{result.output[:2000] if result.success else result.error[:500]}\n"
                f"[/TOOL_RESULT]"
            )

            self.event_logger.emit(
                "agent_tool_invocation",
                {
                    "task_id": task.task_id,
                    "assignee": assignee,
                    "tool": tool_name,
                    "server": server,
                    "success": result.success,
                    "duration_ms": result.duration_ms,
                },
            )

        # Append tool results to output
        augmented = (
            output
            + "\n\n--- Resultados de herramientas ---\n"
            + "\n".join(result_texts)
        )
        return augmented, tool_results

    # ── Agent self-delegation ──────────────────────────────────────────

    _REQUEST_TASK_RE = re.compile(
        r"\[REQUEST_TASK\s+"
        r"type=(?P<type>research|engineer|review)\s+"
        r'topic="(?P<topic>[^"]{1,200})"\s+'
        r"priority=(?P<priority>high|medium|low)"
        r"\s*\]",
        re.IGNORECASE,
    )

    _MAX_DELEGATIONS_PER_AGENT = 2
    _MAX_DELEGATIONS_PER_WORKFLOW = 10

    def _parse_agent_requests(
        self, task: WorkTask, assignee: str, output: str
    ) -> list[WorkTask]:
        """Parse agent output for [REQUEST_TASK] blocks and create sub-tasks."""
        matches = list(self._REQUEST_TASK_RE.finditer(output))
        if not matches:
            return []

        # Enforce per-workflow cap
        task_root = self._task_root(task.task_id)
        existing_delegations = sum(
            1 for t in self.taskboard.list_tasks() if t.metadata.get("delegated_by")
        )
        remaining_budget = max(
            0, self._MAX_DELEGATIONS_PER_WORKFLOW - existing_delegations
        )

        # Enforce per-agent cap
        matches = matches[: min(self._MAX_DELEGATIONS_PER_AGENT, remaining_budget)]
        if not matches:
            return []

        role_map = {
            "research": Role.RESEARCHER,
            "engineer": Role.ENGINEER,
            "review": Role.REVIEWER,
        }
        created: list[WorkTask] = []
        for i, m in enumerate(matches):
            role = role_map[m.group("type").lower()]
            topic = m.group("topic")
            priority = m.group("priority").lower()
            sub_id = f"{task.task_id}::delegated_{i}"

            sub_task = WorkTask(
                task_id=sub_id,
                title=f"[Delegated] {topic}",
                description=topic,
                role=role,
                complexity=task.complexity,
                criticality=task.criticality,
                dependencies=[task.task_id],
                metadata={
                    "delegated_by": assignee,
                    "parent_task": task.task_id,
                    "delegation_priority": priority,
                    "task_root": task_root,
                },
            )
            try:
                self.taskboard.add_task(sub_task)
                created.append(sub_task)
                self.event_logger.emit(
                    "agent_delegation",
                    {
                        "parent_task": task.task_id,
                        "sub_task": sub_id,
                        "role": role.value,
                        "topic": topic,
                        "priority": priority,
                        "delegated_by": assignee,
                    },
                )
            except ValueError:
                pass  # Task ID already exists — skip

        return created

    def _build_agent_pools(self) -> dict[Role, list[str]]:
        defaults: dict[Role, list[str]] = {
            Role.TEAM_LEAD: ["lead-1", "lead-2"],
            Role.RESEARCHER: ["research-1", "research-2"],
            Role.ENGINEER: ["eng-1", "eng-2", "eng-3"],
            Role.REVIEWER: ["review-1", "review-2"],
            Role.QA: ["qa-1", "qa-2"],
        }
        pools: dict[Role, list[str]] = {}
        for role, fallback in defaults.items():
            env_key = f"AITEAM_ROLE_{role.value.upper()}_POOL"
            raw = os.getenv(env_key, "").strip()
            if not raw:
                pools[role] = fallback
                continue
            values = [item.strip() for item in raw.split(",") if item.strip()]
            pools[role] = values or fallback
        return pools

    def _assignee_for_role(
        self,
        role: Role,
        avoid: set[str] | None = None,
        task_type: str = "",
    ) -> str:
        with self._assignment_lock:
            blocked = {
                item.strip().lower() for item in (avoid or set()) if item.strip()
            }
            candidates = self.agent_pools.get(role, [])
            if not candidates:
                return "lead-1"

            enabled_candidates = [
                candidate
                for candidate in candidates
                if candidate.lower() not in blocked and self._agent_enabled(candidate)
            ]
            if enabled_candidates:
                start = self._role_assignment_cursor.get(role, 0)
                rotated = (
                    enabled_candidates[start % len(enabled_candidates) :]
                    + enabled_candidates[: start % len(enabled_candidates)]
                )
                order_map = {candidate: idx for idx, candidate in enumerate(rotated)}
                load_map = self._active_load_by_agent(rotated)
                tt = task_type  # capture for lambda
                selected = min(
                    rotated,
                    key=lambda candidate: (
                        self._agent_selection_score(
                            candidate, load_map.get(candidate, 0), tt
                        ),
                        order_map.get(candidate, 0),
                    ),
                )
                self._role_assignment_cursor[role] = (start + 1) % len(
                    enabled_candidates
                )
                return selected

            return candidates[0] if candidates else "lead-1"

    def _active_load_by_agent(self, candidates: list[str]) -> dict[str, int]:
        active_states = {
            TaskState.READY,
            TaskState.PENDING,
            TaskState.CLAIMED,
            TaskState.BLOCKED,
        }
        loads = {candidate: 0 for candidate in candidates}
        for task in self.taskboard.list_tasks():
            if not task.assignee or task.assignee not in loads:
                continue
            if task.state in active_states:
                loads[task.assignee] += 1
        return loads

    def _agent_selection_score(
        self,
        agent_id: str,
        active_load: int,
        task_type: str = "",
    ) -> float:
        latency = float(self._agent_latency_ewma_ms.get(agent_id, 400.0))
        penalty = int(self._agent_failure_penalty.get(agent_id, 0))
        base = (active_load * 1000.0) + (penalty * 250.0) + latency
        # Specialization bonus: lower score = better
        if task_type:
            spec = self._agent_specialization.get((agent_id, task_type))
            if spec and spec[1] >= 2:
                rate = spec[0] / spec[1]
                # Good specialization reduces score by up to 300
                base -= rate * 300.0
        return base

    def _update_agent_performance(
        self,
        assignee: str,
        decision: RoutingDecision,
        task_type: str = "",
    ) -> None:
        latency_ms = max(1, int(decision.response.latency_ms or 1))
        current = self._agent_latency_ewma_ms.get(assignee)
        if current is None:
            self._agent_latency_ewma_ms[assignee] = float(latency_ms)
        else:
            self._agent_latency_ewma_ms[assignee] = (current * 0.7) + (
                float(latency_ms) * 0.3
            )

        penalty = int(self._agent_failure_penalty.get(assignee, 0))
        if decision.success:
            self._agent_failure_penalty[assignee] = max(0, penalty - 1)
        else:
            self._agent_failure_penalty[assignee] = min(10, penalty + 1)

        # Specialization tracking
        if task_type:
            key = (assignee, task_type)
            prev = self._agent_specialization.get(key, (0, 0))
            succ = prev[0] + (1 if decision.success else 0)
            total = prev[1] + 1
            self._agent_specialization[key] = (succ, total)

    @staticmethod
    def _agent_enabled(agent_id: str) -> bool:
        key = f"AITEAM_AGENT_{agent_id.upper().replace('-', '_')}_ENABLED"
        value = os.getenv(key, "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _maybe_handoff_and_retry(
        self,
        *,
        task: WorkTask,
        failed_assignee: str,
        decision: RoutingDecision,
    ) -> bool:
        if task.metadata.get("skip_handoff_failover"):
            return False

        retry_count = int(task.metadata.get("retry_count", 0))
        max_handoffs = int(task.metadata.get("max_handoff_retries", 5))

        if retry_count >= max_handoffs:
            self.event_logger.emit(
                "stale_loop_detected",
                {
                    "task_id": task.task_id,
                    "retry_count": retry_count,
                    "reason": "Max retries reached. Potential stale loop (Bugs <-> Fixes).",
                },
            )
            # Notify lead about the block
            self.mailbox.send(
                sender="system",
                recipient="team_lead",
                subject=f"STALE LOOP BLOCK: {task.task_id}",
                body=(
                    f"La tarea {task.task_id} ha superado {max_handoffs} reintentos.\n"
                    "Posible bucle infinito detectado. Bloqueando para intervencion humana."
                ),
                task_id=task.task_id,
            )
            return False

        if not self._is_model_or_runtime_failure(decision):
            return False

        history = task.metadata.get("handoff_history", [])
        blocked = {failed_assignee}
        if isinstance(history, list):
            blocked.update(str(item).strip() for item in history if str(item).strip())
        substitute = self._assignee_for_role(task.role, avoid=blocked)
        if not substitute or substitute == failed_assignee:
            return False

        handoff_context = self._build_handoff_context(
            task=task, source_agent=failed_assignee, decision=decision
        )
        self._remember_memory(
            agent_id=substitute,
            role=task.role.value,
            kind="handoff_context",
            content=handoff_context,
            task_id=task.task_id,
            tags=["handoff", "continuity"],
        )
        self.mailbox.send(
            sender=failed_assignee,
            recipient=substitute,
            subject=f"Handoff required: {task.task_id}",
            body=handoff_context,
            task_id=task.task_id,
        )
        self.mailbox.send(
            sender="team_lead",
            recipient="team_lead",
            subject=f"Handoff executed: {task.task_id}",
            body=(
                f"Se reasigna {task.task_id} de {failed_assignee} a {substitute}.\n"
                f"Motivo: {decision.response.error or decision.reason}\n"
                f"Resumen: {self._compact_text(handoff_context, 260)}"
            ),
            task_id=task.task_id,
        )

        new_history = sorted(blocked)
        self.taskboard.update_metadata(
            task.task_id,
            {
                "handoff_history": new_history,
                "handoff_from": failed_assignee,
                "handoff_to": substitute,
                "handoff_reason": decision.response.error or decision.reason,
            },
        )
        self.taskboard.retry_task(
            task.task_id,
            reason=f"handoff:{failed_assignee}->{substitute}",
            assignee=substitute,
        )
        self.event_logger.emit(
            "agent_handoff",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "from": failed_assignee,
                "to": substitute,
                "reason": decision.response.error or decision.reason,
                "attempts": decision.attempts,
                "summary": self._compact_text(handoff_context, 220),
            },
        )
        return True

    @staticmethod
    def _is_model_or_runtime_failure(decision: RoutingDecision) -> bool:
        reason = (decision.reason or "").strip().lower()
        error = (decision.response.error or "").strip().lower()
        attempts = " ".join(decision.attempts).lower()
        blob = " ".join([reason, error, attempts])
        markers = [
            "all_attempts_failed",
            "no_eligible_adapter",
            "quota",
            "limit",
            "timeout",
            "unavailable",
            "forced_api_fallback",
            "external_exec_error",
            "command_not_found",
            "api_budget_block",
        ]
        return any(marker in blob for marker in markers)

    @staticmethod
    def _selected_adapter_name(decision: RoutingDecision) -> str:
        for attempt in reversed(list(decision.attempts or [])):
            raw = str(attempt or "").strip()
            if ":ok" not in raw:
                continue
            parts = raw.split(":")
            if parts:
                return parts[0].strip()
        return ""

    def _build_handoff_context(
        self, task: WorkTask, source_agent: str, decision: RoutingDecision | None = None
    ) -> str:
        excluded = {"meeting_minutes"}
        memory_query = self._memory_query_for_task(task)
        recent = self.memory.recent(
            source_agent,
            limit=5,
            exclude_kinds=excluded,
            project_key=self._project_thread_key(),
        )
        relevant = self.memory.relevant(
            source_agent,
            memory_query,
            limit=4,
            exclude_kinds=excluded,
            project_key=self._project_thread_key(),
        )
        lines = [
            f"Handoff Task: {task.task_id} | {task.title}",
            f"Descripcion: {self._compact_text(task.description, 180)}",
            f"Objetivo inmediato: retomar la ejecucion sin repetir el fallo previo.",
        ]
        if decision and not decision.success:
            err_msg = decision.response.error or decision.reason
            lines.append(f"Fallo anterior: {err_msg}")
            if task.metadata.get("execution_plan"):
                lines.append(
                    f"Plan parcial heredado: {task.metadata['execution_plan']}"
                )
        if task.metadata.get("review_feedback"):
            lines.append(
                f"Feedback pendiente: {self._compact_text(str(task.metadata.get('review_feedback', '')), 180)}"
            )
        lines.append("Contexto transferido:")
        seen: set[str] = set()
        for entry in relevant + recent:
            token = f"{entry.kind}|{entry.content}"[:300]
            if token in seen:
                continue
            seen.add(token)
            lines.append(f"- [{entry.kind}] {self._compact_text(entry.content, 180)}")
        if len(lines) == 3:
            lines.append("- Sin memoria relevante, revisar taskboard y mailbox")
        lines.append(
            "Siguiente accion esperada: continuar desde este contexto, validar el fallo y producir una salida concreta o nuevo bloqueo justificable."
        )
        return self._compact_context(lines, max_lines=12, max_chars=1200)

    def _memory_query_for_task(self, task: WorkTask) -> str:
        phase_name = self._phase_name_for_task(task)
        phase_contract = dict(task.metadata.get("phase_contract", {}) or {})
        role_upper = str(
            phase_contract.get("role") or task.role.name
        ).strip().upper()
        objective = self._runtime_phase_contract_objective(
            phase_id=str(phase_contract.get("phase_id") or phase_name or task.title).strip(),
            role_upper=role_upper,
            objective=str(
                phase_contract.get("objective")
                or task.metadata.get("delegation_brief")
                or ""
            ).strip(),
            task=task,
        )
        parts = [
            str(task.title or "").strip(),
            f"phase:{phase_name}" if phase_name else "",
            f"role:{task.role.value}",
            str(objective or "").strip(),
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for part in parts:
            normalized = str(part or "").strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return "\n".join(deduped)

    @staticmethod
    def _same_task_root(task_id: str | None, expected_root: str) -> bool:
        normalized_root = str(expected_root or "").strip()
        normalized_task_id = str(task_id or "").strip()
        if not normalized_root or not normalized_task_id:
            return False
        return normalized_task_id == normalized_root or normalized_task_id.startswith(
            f"{normalized_root}::"
        )

    def _filter_memory_entries_for_run(
        self,
        entries: list,
        *,
        task_root: str,
        allow_cross_run_fallback: bool = False,
    ) -> list:
        if not task_root:
            return list(entries or [])
        same_root = [
            entry
            for entry in list(entries or [])
            if self._same_task_root(getattr(entry, "task_id", None), task_root)
        ]
        if same_root:
            return same_root
        if allow_cross_run_fallback:
            return list(entries or [])
        return []

    def _thread_turns_for_run(
        self,
        thread: ConversationThread,
        *,
        task_root: str,
        limit: int = 8,
        allow_cross_run_fallback: bool = True,
    ) -> list:
        turns = list(thread.recent_turns(limit=0))
        normalized_root = str(task_root or "").strip()
        if not normalized_root:
            return turns[-limit:] if limit > 0 else turns
        same_root = [
            turn
            for turn in turns
            if self._same_task_root(getattr(turn, "task_id", None), normalized_root)
        ]
        if same_root:
            turns = same_root
        elif not allow_cross_run_fallback:
            turns = []
        return turns[-limit:] if limit > 0 else turns

    def _verify_task_evidence(
        self, task: WorkTask, workspace: Path
    ) -> tuple[bool, str]:
        return _verify_task_evidence_fn(
            task,
            workspace,
            project_root=self.project_root,
            runtime_dir=self.runtime_dir,
        )

    @staticmethod
    def _assess_output_quality(output: str, role: Role, phase: str) -> tuple[bool, str]:
        return _assess_output_quality_fn(output, role, phase)

    # ── Conversational task detection ────────────────────────────────────

    # Delegated to aiteam.evidence_gate — kept as alias for any remaining internal use
    _CONVERSATIONAL_KEYWORDS = _EVIDENCE_GATE_KEYWORDS

    @classmethod
    def _detect_conversational_task(cls, task: "WorkTask") -> bool:  # type: ignore[name-defined]
        return _detect_conversational_task_fn(task)

    def _check_gate_timeouts(self) -> None:
        """Escala al Team Lead las tareas bloqueadas en quality gates que exceden el timeout."""
        now = datetime.now(timezone.utc)
        for task in self.taskboard.list_tasks():
            if task.metadata.get("blocked_reason") != "waiting_quality_gates":
                continue
            opened_raw = task.metadata.get("gate_opened_at")
            if not opened_raw:
                continue
            try:
                opened_at = datetime.fromisoformat(opened_raw)
            except ValueError:
                continue
            elapsed = (now - opened_at).total_seconds()
            if elapsed < _GATE_TIMEOUT_SECONDS:
                continue
            # Timeout alcanzado: cancelar gate tasks pendientes y desbloquear
            self._cleanup_gate_tasks(
                [
                    t.task_id
                    for t in self.taskboard.list_tasks()
                    if t.metadata.get("parent_task") == task.task_id
                    and t.state not in {TaskState.COMPLETED, TaskState.FAILED}
                ]
            )
            timeout_msg = (
                f"Quality gate timeout tras {int(elapsed // 60)} min "
                f"(límite: {_GATE_TIMEOUT_SECONDS // 60} min). "
                "Tarea aprobada automáticamente por timeout — revisar manualmente."
            )
            self.taskboard.update_metadata(
                task.task_id,
                {"gate_timeout": True, "gate_timeout_reason": timeout_msg},
            )
            self.taskboard.mark_completed(task.task_id, details=timeout_msg)
            self.event_logger.emit(
                "gate_timeout_escalated",
                {"task_id": task.task_id, "elapsed_seconds": int(elapsed)},
            )

    @staticmethod
    def _should_open_quality_gates(task: WorkTask) -> bool:
        if task.role != Role.ENGINEER:
            return False
        if task.metadata.get("quality_gate_spawned"):
            return False
        if task.metadata.get("skip_quality_gates") or uses_chat_policy(task.metadata):
            return False
        return True

    def _build_gate_evidence_context(self, task: WorkTask) -> str:
        return _build_gate_evidence_context_fn(
            task,
            session_store=self.session_store,
            compact_fn=self._compact_text,
        )

    @staticmethod
    def _summarize_git_diff(raw_diff: str) -> str:
        return _summarize_git_diff_fn(raw_diff)

    def _spawn_quality_gates(self, task: WorkTask) -> None:
        skip_evidence = task.metadata.get("skip_evidence_gate", False) or uses_chat_policy(
            task.metadata
        )
        skip_placeholder = task.metadata.get("skip_placeholder_check", False)

        # Build rich evidence context for gates
        evidence_context = self._build_gate_evidence_context(task)
        review_desc = (
            (
                "Revisar cambios de implementacion, riesgos y deuda tecnica.\n\n"
                f"--- Evidencia del Engineer ---\n{evidence_context}"
            )
            if evidence_context
            else "Revisar cambios de implementacion, riesgos y deuda tecnica."
        )
        qa_desc = (
            (
                "Validar pruebas, regresion y criterios de salida.\n\n"
                f"--- Evidencia del Engineer ---\n{evidence_context}"
            )
            if evidence_context
            else "Validar pruebas, regresion y criterios de salida."
        )

        review_id = f"{task.task_id}::review"
        qa_id = f"{task.task_id}::qa"
        gate_tasks = [
            WorkTask(
                task_id=review_id,
                title=f"Review {task.title}",
                description=review_desc,
                role=Role.REVIEWER,
                dependencies=[],
                metadata={
                    "is_gate": True,
                    "parent_task": task.task_id,
                    "gate_type": "review",
                    "skip_evidence_gate": skip_evidence,
                    "skip_placeholder_check": skip_placeholder,
                },
            ),
            WorkTask(
                task_id=qa_id,
                title=f"QA {task.title}",
                description=qa_desc,
                role=Role.QA,
                dependencies=[],
                metadata={
                    "is_gate": True,
                    "parent_task": task.task_id,
                    "gate_type": "qa",
                    "skip_evidence_gate": skip_evidence,
                    "skip_placeholder_check": skip_placeholder,
                },
            ),
        ]
        if self._should_open_security_gate(task):
            gate_tasks.append(
                WorkTask(
                    task_id=f"{task.task_id}::security",
                    title=f"Security {task.title}",
                    description=(
                        "Validar seguridad/compliance: secretos, comandos sensibles y "
                        "riesgo operacional."
                    ),
                    role=Role.REVIEWER,
                    dependencies=[],
                    metadata={
                        "is_gate": True,
                        "parent_task": task.task_id,
                        "gate_type": "security",
                        "required_capabilities": ["review"],
                        "skip_evidence_gate": skip_evidence,
                        "skip_placeholder_check": skip_placeholder,
                    },
                )
            )

        for gate_task in gate_tasks:
            if not self.taskboard.get_task(gate_task.task_id):
                self.taskboard.add_task(gate_task)

        if self.taskboard.get_task(task.task_id) is not None:
            gate_ids = [gate_task.task_id for gate_task in gate_tasks]
            self.taskboard.update_metadata(
                task.task_id,
                {
                    "quality_gate_spawned": True,
                    "quality_gate_tasks": gate_ids,
                    "provisional_result": "implementation_done_waiting_gates",
                },
            )
            self.event_logger.emit(
                "quality_gates_opened",
                {
                    "task_id": task.task_id,
                    "gates": gate_ids,
                },
            )
            self._notify_gate_agents(task.task_id, gate_ids=gate_ids)

    @staticmethod
    def _should_open_security_gate(task: WorkTask) -> bool:
        if task.metadata.get("skip_security_gate"):
            return False
        if task.metadata.get("require_security_gate"):
            return True
        if task.complexity.value == "high" or task.criticality.value == "high":
            return True
        plan = task.metadata.get("execution_plan", [])
        if not isinstance(plan, list):
            return False
        risky_steps = {"cmd", "powershell", "browser_script"}
        for step in plan:
            if not isinstance(step, dict):
                continue
            step_type = str(step.get("type", "")).strip().lower()
            if step_type in risky_steps:
                return True
        return False

    def _release_blocked_parent_tasks(self) -> None:
        for task in self.taskboard.list_tasks():
            if task.state != TaskState.BLOCKED:
                continue
            gate_tasks = task.metadata.get("quality_gate_tasks", [])
            if not gate_tasks:
                continue
            gate_objects = {
                gate_id: self.taskboard.get_task(gate_id) for gate_id in gate_tasks
            }
            failed_gates = [
                gate_id
                for gate_id, obj in gate_objects.items()
                if obj is None or obj.state == TaskState.FAILED
            ]
            if failed_gates:
                # ── Gate Iteration Loop (evaluator-optimizer pattern) ──
                iteration = int(task.metadata.get("gate_iteration", 0))
                max_iterations = int(task.metadata.get("max_gate_iterations", 2))

                if iteration < max_iterations:
                    feedback = self._collect_gate_feedback(failed_gates)
                    task.metadata["review_feedback"] = feedback
                    task.metadata["gate_iteration"] = iteration + 1
                    self._cleanup_gate_tasks(gate_tasks)
                    task.metadata.pop("quality_gate_spawned", None)
                    task.metadata.pop("quality_gate_tasks", None)
                    self.taskboard.retry_task(
                        task.task_id,
                        reason=f"gate_iteration_{iteration + 1}",
                    )
                    self.event_logger.emit(
                        "gate_iteration",
                        {
                            "task_id": task.task_id,
                            "iteration": iteration + 1,
                            "max_iterations": max_iterations,
                            "failed_gates": failed_gates,
                            "feedback_length": len(feedback),
                            "execution_round": int(
                                task.metadata.get("execution_round", self._round + 1)
                            ),
                            "execution_sub_iteration": int(
                                task.metadata.get("execution_sub_iteration", 1)
                            ),
                        },
                    )
                    self.mailbox.send(
                        sender="system",
                        recipient="team_lead",
                        subject=f"Gate iteration {iteration + 1}: {task.task_id}",
                        body=f"Re-ejecutando con feedback de gates: {feedback[:200]}",
                        task_id=task.task_id,
                    )
                    self._remember_memory(
                        agent_id="lead-1",
                        role=Role.TEAM_LEAD.value,
                        kind="gate_iteration",
                        content=f"{task.task_id} iteration {iteration + 1}: {feedback[:200]}",
                        task_id=task.task_id,
                        tags=["quality", "iteration"],
                    )
                    continue

                # Max iteraciones alcanzadas → conflict resolution via Team Lead
                feedback = self._collect_gate_feedback(failed_gates)

                # ── Conflict resolution: escalate to team lead ──
                if not task.metadata.get("conflict_resolved"):
                    task.metadata["conflict_resolved"] = True
                    escalation_id = f"{task.task_id}::conflict_resolution"
                    escalation_task = WorkTask(
                        task_id=escalation_id,
                        title=f"[Conflicto] Mediar: {task.title}",
                        description=(
                            f"El reviewer y el engineer no llegaron a acuerdo tras {iteration} iteraciones.\n"
                            f"Feedback del reviewer: {feedback[:500]}\n"
                            f"Tarea original: {task.description[:300]}\n"
                            "Como Team Lead, decide: (1) aprobar con condiciones, "
                            "(2) asignar a otro engineer, o (3) marcar como fallida."
                        ),
                        role=Role.TEAM_LEAD,
                        complexity=task.complexity,
                        criticality=task.criticality,
                        metadata={
                            "conflict_parent": task.task_id,
                            "failed_gates": failed_gates,
                            "iterations_exhausted": iteration,
                            "skip_peer_consultation": True,
                        },
                    )
                    try:
                        self.taskboard.add_task(escalation_task)
                    except ValueError:
                        pass
                    self.communicator.broadcast(
                        sender="system",
                        subject=f"Conflicto escalado: {task.task_id}",
                        body=(
                            f"El reviewer y el engineer no llegaron a acuerdo tras {iteration} iteraciones. "
                            f"Escalado al Team Lead para mediacion."
                        ),
                        task_id=task.task_id,
                    )
                    self.event_logger.emit(
                        "conflict_escalation",
                        {
                            "task_id": task.task_id,
                            "escalation_task": escalation_id,
                            "failed_gates": failed_gates,
                            "iterations_exhausted": iteration,
                        },
                    )
                    continue

                # Conflict already resolved and still failing → final failure
                reason = f"quality_gates_failed:{','.join(failed_gates)}"
                self.taskboard.mark_failed(task.task_id, error=reason)
                self._maybe_spawn_lead_failure_checkpoint(task, reason)
                self.mailbox.send(
                    sender="system",
                    recipient="team_lead",
                    subject=f"Task failed by gates: {task.task_id}",
                    body=(
                        f"Gates fallaron tras {iteration} iteraciones (post-mediacion). "
                        f"failed_gates={failed_gates}"
                    ),
                    task_id=task.task_id,
                )
                self._remember_memory(
                    agent_id="lead-1",
                    role=Role.TEAM_LEAD.value,
                    kind="quality_gate_failure",
                    content=f"{task.task_id} failed by gates {failed_gates} after {iteration} iterations",
                    task_id=task.task_id,
                    tags=["quality", "failure"],
                )
                self.event_logger.emit(
                    "quality_gates_failed",
                    {
                        "task_id": task.task_id,
                        "failed_gates": failed_gates,
                        "iterations_exhausted": iteration,
                    },
                )
                continue

            all_completed = all(
                obj is not None and obj.state == TaskState.COMPLETED
                for obj in gate_objects.values()
            )
            if all_completed:
                self.taskboard.mark_completed(
                    task.task_id,
                    details="quality_gates_passed",
                )

                # ── Actualizar workflow state tras pasar gates ──
                task_root = self._task_root(task.task_id)
                phase_name = (
                    task.task_id.split("::")[-1]
                    if "::" in task.task_id
                    else task.role.value
                )
                gate_result = task.metadata.get("result", "quality_gates_passed")
                self._update_workflow_state(task_root, phase_name, gate_result)

                self.mailbox.send(
                    sender="system",
                    recipient="team_lead",
                    subject=f"Task released by gates: {task.task_id}",
                    body="Todas las gates de calidad finalizaron correctamente.",
                    task_id=task.task_id,
                )
                self._remember_memory(
                    agent_id="lead-1",
                    role=Role.TEAM_LEAD.value,
                    kind="quality_gate_release",
                    content=f"{task.task_id} released",
                    task_id=task.task_id,
                    tags=["quality", "release"],
                )

    def _build_decision_governance_context(
        self,
        task: WorkTask,
        assignee: str,
        peer_report: PeerConsultationReport,
    ) -> str:
        charter = role_charter_for(task.role)
        consulted = ", ".join(peer_report.consulted_roles) or "ninguno"
        consulted_providers = ", ".join(peer_report.consulted_providers or []) or "ninguno"
        unavailable = ", ".join(peer_report.unavailable_roles) or "ninguno"
        return (
            f"Agente: {assignee} ({task.role.value}).\n"
            f"Rango de decision activo: R{charter.decision_rank}/5.\n"
            f"Personalidad esperada: {charter.personality}.\n"
            f"Peers consultados: {consulted}.\n"
            f"Familias/proveedores consultados: {consulted_providers}.\n"
            f"Peers no disponibles: {unavailable}.\n"
            "Debes justificar la decision final con evidencia, tradeoffs y razones de desacuerdo si existen."
        )

    def _persist_decision_record(
        self,
        task: WorkTask,
        assignee: str,
        decision: RoutingDecision,
        output: str,
        peer_report: PeerConsultationReport,
    ) -> None:
        charter = role_charter_for(task.role)
        consulted = ", ".join(peer_report.consulted_roles) or "none"
        consulted_providers = ", ".join(peer_report.consulted_providers or []) or "none"
        unavailable = ", ".join(peer_report.unavailable_roles) or "none"
        output_text = str(output or "").strip()
        demo_fast_mode = sim_mode_enabled()
        if re.search(r"^\[demo\]", output_text, flags=re.IGNORECASE):
            output_summary = "demo"
        elif re.search(r"^\[simulado\s*\|", output_text, flags=re.IGNORECASE):
            output_summary = "demo" if demo_fast_mode else "placeholder/simulado"
        elif re.search(
            r"^\[[a-z0-9_\-]+:[a-z0-9_\.\-]+:(subscription|api)\]",
            output_text,
            flags=re.IGNORECASE,
        ):
            output_summary = "placeholder/adapter"
        else:
            output_summary = self._compact_text(output_text, 3000)
        justification = (
            f"decision_rank=R{charter.decision_rank}/5 assignee={assignee} role={task.role.value}; "
            f"consulted={consulted}; consulted_providers={consulted_providers}; unavailable={unavailable}; "
            f"provider={decision.provider} model={decision.model} channel={decision.channel.value}; "
            f"attempts={self._compact_text(str(decision.attempts), 280)}; output_summary={output_summary}"
        )
        diversity_observed = len(set(peer_report.consulted_providers or [])) >= 2
        self.taskboard.update_metadata(
            task.task_id,
            {
                "decision_rank": charter.decision_rank,
                "decision_personality": charter.personality,
                "consulted_roles": peer_report.consulted_roles,
                "consulted_providers": list(peer_report.consulted_providers or []),
                "peer_diversity_observed": diversity_observed,
                "unavailable_consultations": peer_report.unavailable_roles,
                "decision_justification": justification,
            },
        )
        self._remember_memory(
            agent_id=assignee,
            role=task.role.value,
            kind="decision_justification",
            content=justification,
            task_id=task.task_id,
            tags=["decision", "justification", f"rank_r{charter.decision_rank}"],
        )
        self.event_logger.emit(
            "decision_recorded",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "assignee": assignee,
                "decision_rank": charter.decision_rank,
                "consulted_roles": peer_report.consulted_roles,
                "consulted_providers": list(peer_report.consulted_providers or []),
                "peer_diversity_observed": diversity_observed,
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
            },
        )

    def _peer_diversity_required(self, task: WorkTask) -> bool:
        override = task.metadata.get("peer_consultation_diversity_required")
        if isinstance(override, bool):
            return override
        if isinstance(override, str):
            normalized = override.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(
            getattr(self.router.policy, "peer_consultation_diversity_required", True)
        )

    def _peer_lenient_route(
        self,
        task: WorkTask,
        peer_role: Role,
        prompt: str,
        messages: list[dict[str, str]],
    ) -> RoutingDecision:
        """Last-resort peer routing with no capability gate.

        Called when all capability-filtered attempts return no_eligible_adapter.
        Subscription adapters (gemini_worker, claude_haiku) lack 'review'/'qa'
        capabilities in their registry entries but can still provide useful peer
        opinions.  Removing the capability filter lets them participate.
        """
        lenient_request = RoutingRequest(
            role=peer_role,
            complexity=task.complexity,
            criticality=task.criticality,
            required_capabilities=set(),
            environment=self.environment,
        )
        lenient_decision = self.router.route_and_invoke(
            request=lenient_request,
            prompt=prompt,
            task_id=task.task_id,
            messages=messages,
        )
        self.event_logger.emit(
            "peer_capability_relaxed_fallback",
            {
                "task_id": task.task_id,
                "peer_role": peer_role.value,
                "success": lenient_decision.success,
                "provider": lenient_decision.provider,
            },
        )
        return lenient_decision

    def _route_peer_with_diversity(
        self,
        task: WorkTask,
        peer_role: Role,
        assignee: str,
        round_label: str,
        prompt: str,
        messages: list[dict[str, str]],
        used_providers: set[str],
    ) -> RoutingDecision:
        base_request = RoutingRequest(
            role=peer_role,
            complexity=task.complexity,
            criticality=task.criticality,
            required_capabilities=self._peer_capabilities(peer_role),
            tool_rewiring_preferred_specialist=str(
                task.metadata.get("tool_rewiring_preferred_specialist", "") or ""
            ).strip(),
            environment=self.environment,
        )
        diversity_required = self._peer_diversity_required(task)
        normalized_used = {item.strip().lower() for item in used_providers if str(item).strip()}
        if not diversity_required or not normalized_used:
            decision = self.router.route_and_invoke(
                request=base_request,
                prompt=prompt,
                task_id=task.task_id,
                messages=messages,
            )
            if not decision.success and decision.reason == "no_eligible_adapter":
                return self._peer_lenient_route(task, peer_role, prompt, messages)
            return decision

        diverse_request = RoutingRequest(
            role=peer_role,
            complexity=task.complexity,
            criticality=task.criticality,
            required_capabilities=self._peer_capabilities(peer_role),
            tool_rewiring_preferred_specialist=base_request.tool_rewiring_preferred_specialist,
            environment=self.environment,
            excluded_providers=set(normalized_used),
        )
        diverse_eligible = self.router.eligible_adapters(diverse_request)
        diverse_decision = None
        fallback_reason = ""
        if diverse_eligible:
            diverse_decision = self.router.route_and_invoke(
                request=diverse_request,
                prompt=prompt,
                task_id=task.task_id,
                messages=messages,
            )
            if diverse_decision.success:
                return diverse_decision
            fallback_reason = "diverse_candidates_failed"
        else:
            fallback_reason = "no_diverse_provider_available"

        fallback_decision = self.router.route_and_invoke(
            request=base_request,
            prompt=prompt,
            task_id=task.task_id,
            messages=messages,
        )
        self.event_logger.emit(
            "peer_diversity_fallback",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "peer_role": peer_role.value,
                "round": round_label,
                "used_providers": sorted(normalized_used),
                "fallback_reason": fallback_reason,
                "fallback_provider": fallback_decision.provider,
                "fallback_success": fallback_decision.success,
            },
        )
        if fallback_decision.success:
            return fallback_decision
        # Both diversity and capability-filtered fallback failed.
        # If no eligible adapters at all, retry without capability gate.
        if fallback_decision.reason == "no_eligible_adapter":
            return self._peer_lenient_route(task, peer_role, prompt, messages)
        if diverse_decision is None:
            return fallback_decision
        return diverse_decision

    def _run_peer_consultation(
        self, task: WorkTask, assignee: str
    ) -> PeerConsultationReport:
        if not self._needs_peer_consultation(task):
            return PeerConsultationReport(
                text="", consulted_roles=[], unavailable_roles=[]
            )

        peer_roles = self._peer_roles_for(task.role)
        if not peer_roles:
            return PeerConsultationReport(
                text="", consulted_roles=[], unavailable_roles=[]
            )

        summaries: list[str] = []
        consulted_roles: list[str] = []
        unavailable_roles: list[str] = []
        consulted_providers: list[str] = []
        round1_used_providers: set[str] = set()
        for peer_role in peer_roles:
            peer_agent = self._assignee_for_role(peer_role)
            peer_prompt = self._peer_prompt_for_task(task, peer_role)
            peer_messages = self._build_peer_messages(
                task=task,
                peer_role=peer_role,
                assignee=assignee,
                round_label="round1",
            )
            decision = self._route_peer_with_diversity(
                task=task,
                peer_role=peer_role,
                assignee=assignee,
                round_label="round1",
                prompt=peer_prompt,
                messages=peer_messages,
                used_providers=round1_used_providers,
            )
            if decision.success:
                content = self.compliance.redact_text(decision.response.content)
                self.communicator.send_dm(
                    sender=peer_role.value,
                    recipient=assignee,
                    subject=f"Peer input for {task.task_id}",
                    body=content[:500],
                    task_id=task.task_id,
                )
                self._remember_memory(
                    agent_id=peer_agent,
                    role=peer_role.value,
                    kind="peer_consultation_outbound",
                    content=content,
                    task_id=task.task_id,
                    tags=["peer", "consultation"],
                )
                self._remember_memory(
                    agent_id=assignee,
                    role=task.role.value,
                    kind="peer_consultation_inbound",
                    content=f"{peer_role.value}: {content}",
                    task_id=task.task_id,
                    tags=["peer", "consultation"],
                )
                consulted_roles.append(peer_role.value)
                summaries.append(f"{peer_role.value}: {content[:220]}")
                provider_name = str(decision.provider or "").strip().lower()
                round1_used_providers.add(provider_name)
                if provider_name and provider_name not in consulted_providers:
                    consulted_providers.append(provider_name)
            else:
                unavailable_roles.append(peer_role.value)
                summaries.append(
                    f"{peer_role.value}: no disponible ({decision.response.error or decision.reason})"
                )

        # ── Round 2: peer response to collected inputs ──
        if consulted_roles and len(consulted_roles) >= 2:
            all_inputs = "\n".join(
                f"- {s}" for s in summaries if "no disponible" not in s
            )
            round2_summaries: list[str] = []
            round2_used_providers: set[str] = set()
            for peer_role in peer_roles:
                if peer_role.value not in consulted_roles:
                    continue
                peer_agent = self._assignee_for_role(peer_role)
                r2_prompt = (
                    f"Eres {peer_role.value}. En la ronda anterior de consulta para la tarea "
                    f"'{task.title}', los peers opinaron:\n{all_inputs}\n\n"
                    f"Tarea: {task.description[:300]}\n"
                    "Revisa las opiniones y da tu posicion final en 2-3 oraciones: "
                    "acuerdo, desacuerdo, o matiz importante."
                )
                r2_messages = self._build_peer_messages(
                    task=task,
                    peer_role=peer_role,
                    assignee=assignee,
                    round_label="round2",
                    prior_inputs=all_inputs,
                )
                r2_decision = self._route_peer_with_diversity(
                    task=task,
                    peer_role=peer_role,
                    assignee=assignee,
                    round_label="round2",
                    prompt=r2_prompt,
                    messages=r2_messages,
                    used_providers=round2_used_providers,
                )
                if r2_decision.success:
                    r2_content = self.compliance.redact_text(
                        r2_decision.response.content
                    )
                    self.communicator.send_dm(
                        sender=peer_role.value,
                        recipient=assignee,
                        subject=f"Peer dialogue R2 for {task.task_id}",
                        body=r2_content[:500],
                        task_id=task.task_id,
                    )
                    self._remember_memory(
                        agent_id=peer_agent,
                        role=peer_role.value,
                        kind="peer_dialogue_r2",
                        content=r2_content,
                        task_id=task.task_id,
                        tags=["peer", "dialogue", "round2"],
                    )
                    round2_summaries.append(
                        f"{peer_role.value} (R2): {r2_content[:220]}"
                    )
                    round2_used_providers.add(
                        str(r2_decision.provider or "").strip().lower()
                    )

            if round2_summaries:
                summaries.extend(round2_summaries)
                self.event_logger.emit(
                    "peer_dialogue_round2",
                    {
                        "task_id": task.task_id,
                        "assignee": assignee,
                        "peers": consulted_roles,
                        "round2_count": len(round2_summaries),
                    },
                )

        return PeerConsultationReport(
            text="\n".join(f"- {item}" for item in summaries),
            consulted_roles=consulted_roles,
            unavailable_roles=unavailable_roles,
            consulted_providers=consulted_providers,
        )

    @staticmethod
    def _needs_peer_consultation(task: WorkTask) -> bool:
        if task.metadata.get("skip_peer_consultation"):
            return False
        run_profile = str(task.metadata.get("run_profile", "") or "").strip().lower()
        if run_profile in {"solo_lead", "direct"} or task.metadata.get(
            "direct_coding_executor"
        ):
            return False
        if task.metadata.get("require_peer_consultation"):
            return True
        phase_name = str(task.metadata.get("phase", "") or "").strip().lower()
        if phase_name == "lead_close":
            return False
        if phase_name.startswith("plan_"):
            return False
        if task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}:
            return False
        if task.metadata.get("advisory_context_phase") or task.metadata.get(
            "advisory_planning_phase"
        ):
            return False
        return task.complexity.value in {
            "medium",
            "high",
        } or task.criticality.value in {
            "medium",
            "high",
        }

    @staticmethod
    def _peer_roles_for(role: Role) -> list[Role]:
        mapping = {
            Role.TEAM_LEAD: [Role.RESEARCHER, Role.ENGINEER, Role.REVIEWER, Role.QA],
            Role.RESEARCHER: [Role.TEAM_LEAD, Role.ENGINEER],
            Role.ENGINEER: [Role.RESEARCHER, Role.REVIEWER, Role.QA],
            Role.REVIEWER: [Role.ENGINEER, Role.RESEARCHER],
            Role.QA: [Role.ENGINEER, Role.REVIEWER],
        }
        return mapping.get(role, [])

    @staticmethod
    def _peer_capabilities(role: Role) -> set[str]:
        mapping = {
            Role.TEAM_LEAD: {"reasoning"},
            Role.RESEARCHER: {"analysis"},
            Role.ENGINEER: {"coding"},
            Role.REVIEWER: {"review"},
            Role.QA: {"analysis"},
        }
        return mapping.get(role, set())

    @staticmethod
    def _peer_prompt_for_task(task: WorkTask, peer_role: Role) -> str:
        if task.complexity.value == "high" or task.criticality.value == "high":
            return (
                f"Actua como {peer_role.value}.\n"
                f"Analiza la tarea {task.task_id}: {task.title}.\n"
                f"Descripcion: {task.description}.\n"
                "Responde en <= 10 lineas con este formato:\n"
                "1) Hipotesis principal\n"
                "2) Contra-hipotesis\n"
                "3) Riesgos clave\n"
                "4) Siguiente paso recomendado\n"
                "5) Justificacion del paso recomendado"
            )
        return (
            f"Actua como {peer_role.value}.\n"
            f"Analiza la tarea {task.task_id}: {task.title}.\n"
            f"Descripcion: {task.description}.\n"
            "Devuelve recomendaciones concretas, riesgos, orden de ejecucion y justificacion en <= 8 lineas."
        )

    def _notify_dependents(
        self, task_id: str, summary: str, *, task_role: str = ""
    ) -> None:
        # Content-based block detection guards:
        #
        # Content-based block detection guards:
        #
        # Guard 1 — Delegates: tasks whose phase part starts with "delegate_" OR
        #   "delegated_" act as sub-specialists that REPORT findings about a parent
        #   phase state.  Two naming conventions exist:
        #     • deferred spec delegates:   "delegate_qa_scout", "delegate_context"
        #     • inline [REQUEST_TASK] delegates: "{parent}::delegated_0", "::delegated_1"
        #   Their output may describe that the parent is "bloqueada" without the delegate
        #   itself being blocked. Sending "Dependency blocked" from a delegate would
        #   falsely propagate blocking signals to lead_close and other dependents.
        #
        # Guard 2 — Scout role: scouts describe project context (past runs, historical
        #   blocks). Their output routinely contains "bloqueada" as contextual prose,
        #   not as a self-report of their own blocking state.
        #
        # For all other roles (engineer, reviewer, qa, team_lead, researcher primary
        # tasks), apply narrowed structural keywords that indicate the TASK ITSELF is
        # self-reporting a block — NOT merely describing one in context.
        # Narrowed keywords vs. Fix-J: bare "bloqueada" / "bloqueado" are dropped
        # because they match too broadly in contextual descriptions. We require either
        # the status-label pattern ("bloqueada:", "bloqueado:") or specific system
        # phrases that never appear in contextual prose.
        _phase_part = (task_id.split("::")[-1] if "::" in task_id else task_id).lower()
        _role_lower = (task_role or "").lower()
        _is_delegate = (
            _phase_part.startswith("delegate_")
            or _phase_part.startswith("delegated_")
        )
        _is_scout = _role_lower == "scout"
        _applies_content_check = not _is_delegate and not _is_scout

        _summary_lower = (summary or "").lower()
        # Structural self-block indicators (never appear in normal contextual prose):
        _BLOCK_KEYWORDS = (
            "bloqueada:",        # explicit status label: "BLOQUEADA: razon..."
            "bloqueado:",        # same, masculine form
            "evidencegate",      # compound system word, only in gate messages
            "evidence gate",     # alternative gate phrase
            "no hay evidencia",  # explicit missing-evidence claim
            "missing evidence",  # same in English
            "status: blocked",   # structured field (colon alone is too broad)
        )
        _is_blocked_output = (
            _applies_content_check
            and any(kw in _summary_lower for kw in _BLOCK_KEYWORDS)
        )

        for candidate in self.taskboard.list_tasks():
            if task_id not in candidate.dependencies:
                continue
            recipient = self._assignee_for_role(candidate.role)
            if _is_blocked_output:
                subject = f"Dependency blocked: {task_id}"
                body = (
                    f"La dependencia {task_id} de {candidate.task_id} indica bloqueo. "
                    f"Resumen: {summary[:200]}"
                )
            else:
                subject = f"Dependency ready: {task_id}"
                body = f"La dependencia de {candidate.task_id} esta resuelta. Resumen: {summary[:200]}"
            self.communicator.send_dm(
                sender="team_lead",
                recipient=recipient,
                subject=subject,
                body=body,
                task_id=candidate.task_id,
            )

    def _notify_gate_agents(self, parent_task_id: str, gate_ids: list[str]) -> None:
        for gate_id in gate_ids:
            gate_task = self.taskboard.get_task(gate_id)
            if gate_task is None:
                continue
            recipient = self._assignee_for_role(gate_task.role)
            gate_type = str(gate_task.metadata.get("gate_type", gate_task.role.value))
            label = gate_type.replace("_", " ").title()
            self.communicator.send_dm(
                sender="team_lead",
                recipient=recipient,
                subject=f"{label} requested: {gate_id}",
                body=f"Validar artefactos de {parent_task_id} ({gate_type}).",
                task_id=gate_id,
            )

    def _build_collaboration_context(self, task: WorkTask, assignee: str) -> str:
        excluded_memory = {"meeting_minutes"}
        memory_query = self._memory_query_for_task(task)
        task_root = self._task_root(task.task_id)
        relevant_memory = self.memory.relevant(
            assignee,
            memory_query,
            limit=3,
            exclude_kinds=excluded_memory,
            project_key=self._project_thread_key(),
        )
        relevant_memory = self._filter_memory_entries_for_run(
            relevant_memory,
            task_root=task_root,
        )
        recent_memory = self.memory.recent(
            assignee,
            limit=2,
            exclude_kinds=excluded_memory,
            project_key=self._project_thread_key(),
        )
        recent_memory = self._filter_memory_entries_for_run(
            recent_memory,
            task_root=task_root,
        )
        dm_messages = self._context_messages(recipient=assignee, task_root=task_root)
        role_messages = self._context_messages(recipient=task.role.value, task_root=task_root)

        lines: list[str] = []

        # ── Output propio del intento anterior (gate retry) ──────────────────
        # retry_task() no borra metadata["result"], así que el output previo sigue
        # disponible cuando gate_iteration > 0. Lo inyectamos para que el agente
        # sepa qué produjo antes y pueda corregirlo con precisión.
        gate_iter = int(task.metadata.get("gate_iteration", 0))
        if gate_iter > 0:
            own_prev = str(task.metadata.get("result") or "")
            if not own_prev:
                # Fallback: phase_outputs del workflow state
                task_root = self._task_root(task.task_id)
                _phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
                own_prev = self.workflow_state.get(task_root, {}).get(
                    "phase_outputs", {}
                ).get(_phase_name, "")
            if own_prev:
                lines.append(
                    f"Tu output anterior (iteracion {gate_iter - 1}, revisa y mejora):\n"
                    f"{self._compact_text(own_prev, 600)}"
                )

        # ── Resultados de fases anteriores (propagacion de contexto) ──
        dep_context = self._build_dependency_output_context(task)
        if dep_context:
            lines.append("Resultados de fases anteriores:")
            lines.append(dep_context)
        if self._phase_name_for_task(task).lower() == "plan_risks":
            completed_artifacts = self._completed_dependency_planning_artifacts(task)
            if completed_artifacts:
                authority_bits = [
                    f"- {phase}: state=completed; planning_artifact autoritativo disponible"
                    for phase, _artifact in completed_artifacts[:3]
                ]
                lines.append("Dependencias autoritativas de planning:")
                lines.extend(authority_bits)

        # ── Facts del equipo (workflow state) ──
        ws = self.workflow_state.get(task_root, {})
        facts = ws.get("facts", [])
        if facts:
            lines.append("Hechos establecidos por el equipo:")
            for fact in facts[-5:]:
                lines.append(f"- {fact}")

        # ── Ledger de progreso (ultimas acciones del equipo) ──
        ledger = ws.get("ledger", [])
        if ledger:
            lines.append("Progreso del equipo:")
            for entry in ledger[-4:]:
                status_icon = "OK" if entry.get("status") == "completed" else "FAIL"
                lines.append(
                    f"- [{status_icon}] {entry.get('phase', '?')}/{entry.get('assignee', '?')}: "
                    f"{self._compact_text(entry.get('output_summary', ''), 120)}"
                )

        # ── Decisiones del equipo ──
        task_decisions = self.communicator.get_decisions(task_id=task.task_id, limit=5)
        if not task_decisions:
            task_decisions = self.communicator.get_decisions(task_id=task_root, limit=3)
        if task_decisions:
            lines.append("Decisiones del equipo:")
            for d in task_decisions:
                icon = {"accepted": "OK", "rejected": "NO", "proposed": "??"}.get(
                    d.status, "??"
                )
                lines.append(f"- [{icon}] {d.decision_text[:120]} (por {d.proposer})")

        # ── Multi-query cross-agent memory ──
        cross_agent_memory = self.memory.relevant_across_agents(
            query=memory_query,
            exclude_agent=assignee,
            limit=3,
            project_key=self._project_thread_key(),
        )
        # Query for failure patterns on similar task types
        task_type = (
            task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        )
        failure_memory = self.memory.relevant_across_agents(
            query=f"failure {task_type} error",
            exclude_agent=assignee,
            limit=2,
            exclude_kinds={"meeting_minutes", "task_success"},
            project_key=self._project_thread_key(),
        )
        # Query for architectural decisions
        arch_memory = self.memory.relevant_across_agents(
            query=f"decision architecture design {task.title}",
            exclude_agent=assignee,
            limit=2,
            exclude_kinds={"meeting_minutes", "execution_plan_result"},
            project_key=self._project_thread_key(),
        )
        cross_agent_memory = self._filter_memory_entries_for_run(
            cross_agent_memory,
            task_root=task_root,
        )
        failure_memory = self._filter_memory_entries_for_run(
            failure_memory,
            task_root=task_root,
        )
        arch_memory = self._filter_memory_entries_for_run(
            arch_memory,
            task_root=task_root,
        )
        # Merge and deduplicate
        seen_ids: set[str] = set()
        all_cross: list = []
        for entry in cross_agent_memory + failure_memory + arch_memory:
            key = f"{entry.agent_id}:{entry.content[:50]}"
            if key not in seen_ids:
                seen_ids.add(key)
                all_cross.append(entry)
        if all_cross:
            lines.append("Conocimiento del equipo (otros agentes):")
            for entry in all_cross[:7]:
                lines.append(
                    f"- [{entry.role}/{entry.agent_id}] [{entry.kind}] "
                    f"{self._compact_text(entry.content, 180)}"
                )

        # ── Specialization context: past success patterns ──
        spec_key = (assignee, task_type)
        spec = self._agent_specialization.get(spec_key)
        if spec and spec[1] >= 2:
            success_rate = (spec[0] / spec[1]) * 100
            if success_rate >= 70:
                spec_memory = self.memory.relevant(
                    assignee,
                    f"task_success {task_type}",
                    limit=2,
                    exclude_kinds={"meeting_minutes"},
                    project_key=self._project_thread_key(),
                )
                if spec_memory:
                    lines.append(
                        f"Especializacion: {success_rate:.0f}% exito en tareas '{task_type}' "
                        f"({spec[0]}/{spec[1]}). Patrones exitosos previos:"
                    )
                    for entry in spec_memory:
                        lines.append(f"  - {self._compact_text(entry.content, 140)}")

        if relevant_memory:
            lines.append("Memoria relevante:")
            for entry in relevant_memory:
                lines.append(
                    f"- [{entry.kind}] {self._compact_text(entry.content, 180)}"
                )
        if recent_memory:
            lines.append("Memoria reciente:")
            for entry in recent_memory:
                lines.append(
                    f"- [{entry.kind}] {self._compact_text(entry.content, 140)}"
                )
        if dm_messages or role_messages:
            lines.append("Mensajes recientes:")
            for message in (dm_messages + role_messages)[-6:]:
                lines.append(
                    "- "
                    f"{message.sender} -> {message.recipient}: {message.subject} | "
                    f"{self._compact_text(message.body, 120)}"
                )

        # ── Budget signaling ──
        if self.router.budget_manager is not None:
            signal = self.router.budget_manager.api_signal()
            pressure = max(
                signal.daily_utilization_ratio, signal.monthly_utilization_ratio
            )
            if pressure >= 0.5:
                if pressure >= 0.9:
                    level = "CRITICO"
                elif pressure >= 0.75:
                    level = "ALTO"
                else:
                    level = "MODERADO"
                lines.append(
                    f"Presupuesto API [{level}]: "
                    f"uso diario {signal.daily_utilization_ratio:.0%}, "
                    f"uso mensual {signal.monthly_utilization_ratio:.0%}. "
                    f"Max intentos API sugeridos: {signal.suggested_max_api_attempts}. "
                    "Prioriza eficiencia y evita llamadas innecesarias."
                )

        return self._compact_context(lines, max_lines=30, max_chars=3800)

    def _build_skill_mcp_context(self, task: WorkTask, assignee: str) -> str:
        skill_targets = normalize_skill_targets(task.metadata.get("skill_targets", []))
        lsp_targets = normalize_lsp_targets(task.metadata.get("lsp_targets", []))
        required = {
            item.strip().lower()
            for item in task.metadata.get("required_capabilities", [])
            if str(item).strip()
        }
        required.update(
            derive_target_capabilities(
                skill_targets=skill_targets,
                lsp_targets=lsp_targets,
            )
        )
        guidance_mode = (
            "coordinator"
            if task.role == Role.TEAM_LEAD and (skill_targets or lsp_targets)
            else "operator"
        )
        guidance = self.tool_integrator.guidance_for_task(
            role=task.role.value,
            description=f"{task.title}\n{task.description}",
            required_capabilities=required,
            preferred_skills=skill_targets,
            lsp_targets=lsp_targets,
            guidance_mode=guidance_mode,
        )
        text = str(guidance.get("text", "")).strip()
        if not text:
            return ""
        compacted = self._compact_context(
            text.splitlines(),
            max_lines=8 if guidance_mode == "coordinator" else 14,
            max_chars=700 if guidance_mode == "coordinator" else 1200,
        )
        self._remember_memory(
            agent_id=assignee,
            role=task.role.value,
            kind="skill_mcp_guidance",
            content=compacted,
            task_id=task.task_id,
            tags=["skills", "mcp", "guidance"],
        )
        self.event_logger.emit(
            "skill_mcp_guidance",
            {
                "task_id": task.task_id,
                "role": task.role.value,
                "guidance_mode": guidance.get("guidance_mode", guidance_mode),
                "preferred_skills": guidance.get("preferred_skills", []),
                "lsp_targets": guidance.get("lsp_targets", []),
                "skills": guidance.get("skills", []),
                "recommended_mcp": guidance.get("recommended_mcp", []),
                "active_mcp": guidance.get("active_mcp", []),
            },
        )
        # Store recommended skills in task metadata for usage tracking on completion
        task.metadata["_recommended_skills"] = guidance.get("skills", [])
        return compacted

    def _integrate_tools_for_task(
        self,
        *,
        task: WorkTask,
        assignee: str,
        internet_allowed: bool,
    ) -> ToolIntegrationReport:
        report = self.tool_integrator.integrate_from_metadata(
            task_id=task.task_id,
            metadata=task.metadata,
            internet_allowed=internet_allowed,
        )
        if report.messages:
            summary = "; ".join(report.messages)[:500]
            self._remember_memory(
                agent_id=assignee,
                role=task.role.value,
                kind="tool_integration",
                content=summary,
                task_id=task.task_id,
                tags=["tools", "integration"],
            )
        if (
            report.integrated_adapters
            or report.integrated_mcp_servers
            or report.integrated_skills
        ):
            self.event_logger.emit(
                "tool_integration",
                {
                    "task_id": task.task_id,
                    "success": report.success,
                    "adapters": report.integrated_adapters,
                    "mcp_servers": report.integrated_mcp_servers,
                    "skills": report.integrated_skills,
                    "errors": report.errors,
                },
            )
            # ── Tool availability broadcast ──
            new_tools: list[str] = []
            new_tools.extend(f"adapter:{a}" for a in report.integrated_adapters)
            new_tools.extend(f"mcp:{s}" for s in report.integrated_mcp_servers)
            new_tools.extend(f"skill:{s}" for s in report.integrated_skills)
            if new_tools:
                self.communicator.broadcast(
                    sender="system",
                    subject="Nuevas herramientas disponibles",
                    body=(
                        f"Se activaron herramientas para {task.task_id}: "
                        f"{', '.join(new_tools[:10])}. "
                        "Disponibles para tareas futuras."
                    ),
                    task_id=task.task_id,
                )
        return report

    def _auto_discover_tools(
        self,
        *,
        task: WorkTask,
        assignee: str,
        internet_allowed: bool,
    ) -> ToolIntegrationReport:
        required = {
            item.strip().lower()
            for item in task.metadata.get("required_capabilities", [])
            if str(item).strip()
        }
        suggestions = self.tool_integrator.suggest_requirements(required, limit=3)
        if not suggestions:
            return ToolIntegrationReport(success=True)

        prepared = []
        for item in suggestions:
            row = dict(item)
            row.setdefault("required", False)
            row["enabled"] = True
            prepared.append(row)

        metadata = {"tool_requirements": prepared}
        report = self.tool_integrator.integrate_from_metadata(
            task_id=f"{task.task_id}::auto_discovery",
            metadata=metadata,
            internet_allowed=internet_allowed,
        )
        if report.messages:
            self._remember_memory(
                agent_id=assignee,
                role=task.role.value,
                kind="tool_auto_discovery",
                content="; ".join(report.messages)[:500],
                task_id=task.task_id,
                tags=["tools", "auto_discovery"],
            )
        if (
            report.integrated_adapters
            or report.integrated_skills
            or report.integrated_mcp_servers
        ):
            self.event_logger.emit(
                "tool_auto_discovery",
                {
                    "task_id": task.task_id,
                    "required_capabilities": sorted(required),
                    "success": report.success,
                    "integrated_adapters": report.integrated_adapters,
                    "integrated_skills": report.integrated_skills,
                    "integrated_mcp_servers": report.integrated_mcp_servers,
                    "errors": report.errors,
                },
            )
        return report

    def _sync_router_external_adapters(self) -> None:
        external = load_external_adapters(self.runtime_dir / "adapters.json")
        if not external:
            return
        external_names = [adapter.name for adapter in external]
        existing = {adapter.name: adapter for adapter in self.router.adapters}
        merged = [existing.get(adapter.name, adapter) for adapter in external]
        for adapter in self.router.adapters:
            if adapter.name in external_names:
                continue
            merged.append(adapter)
        self.router.adapters = merged

    def _context_messages(self, recipient: str, task_root: str = "") -> list:
        messages = self.mailbox.list_messages(recipient=recipient)
        filtered = [
            message
            for message in messages
            if not message.subject.lower().startswith("sync meeting:")
        ]
        normalized_root = str(task_root or "").strip()
        if normalized_root:
            same_root = [
                message
                for message in filtered
                if self._same_task_root(message.task_id, normalized_root)
            ]
            if same_root:
                filtered = same_root
            else:
                filtered = []
        return filtered[-4:]

    def _project_thread_key(self) -> str:
        return str(self.project_root)

    @staticmethod
    def _thread_model_family(model: str) -> str:
        return str(model or "").strip().lower()

    def _bind_runtime_thread(
        self,
        *,
        thread: ConversationThread,
        assignee: str,
        role: Role,
        decision: RoutingDecision,
        task_id: str,
    ) -> ConversationThread:
        if not decision.success or str(decision.provider or "").strip().lower() in {"", "none"}:
            thread.bind_provider(role=role.value)
            self.thread_store.save_thread(thread)
            return thread
        rebound = self.thread_store.get_thread(
            agent_id=assignee,
            project_key=self._project_thread_key(),
            role=role.value,
            provider=decision.provider,
            channel=decision.channel.value,
            model_family=self._thread_model_family(decision.model),
        )
        rebound.bind_provider(
            role=role.value,
            provider=decision.provider,
            channel=decision.channel.value,
            model_family=self._thread_model_family(decision.model),
            model=decision.model,
        )
        self.thread_store.save_thread(rebound)
        if rebound.thread_id != thread.thread_id:
            self.event_logger.emit(
                "conversation_thread_rebound",
                {
                    "task_id": task_id,
                    "from_thread_id": thread.thread_id,
                    "to_thread_id": rebound.thread_id,
                    "from_generation": int(getattr(thread, "generation", 1) or 1),
                    "to_generation": int(getattr(rebound, "generation", 1) or 1),
                    "provider": decision.provider,
                    "channel": decision.channel.value,
                    "model_family": self._thread_model_family(decision.model),
                },
            )
        return rebound

    def _remember_memory(
        self,
        *,
        agent_id: str,
        role: str,
        kind: str,
        content: str,
        task_id: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.memory.remember(
            agent_id=agent_id,
            role=role,
            kind=kind,
            content=content,
            task_id=task_id,
            tags=tags,
            project_key=self._project_thread_key(),
        )

    def _thread_context(self, thread: ConversationThread, limit: int = 6, task_root: str = "") -> str:
        turns = self._thread_turns_for_run(thread, task_root=task_root, limit=limit)
        if not turns:
            return ""
        lines = ["Hilo conversacional reciente:"]
        for turn in turns:
            role = turn.role.upper()
            source = turn.source
            content = self._compact_text(turn.content, 220)
            lines.append(f"- [{role}/{source}] {content}")
        return "\n".join(lines)

    def _thread_messages(
        self,
        thread: ConversationThread,
        *,
        task_root: str = "",
        limit: int = 6,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in self._thread_turns_for_run(thread, task_root=task_root, limit=limit):
            role = str(turn.role or "user").strip().lower() or "user"
            if role not in {"system", "user", "assistant"}:
                role = (
                    "user" if role in {"team_lead", "lead", "mailbox"} else "assistant"
                )
            content = str(turn.content or "").strip()
            if not content:
                continue
            messages.append({"role": role, "content": self._compact_text(content, 700)})
        return messages

    def _context_block(self, title: str, value: str, limit: int = 1200) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return f"{title}:\n{self._compact_text(text, limit)}"

    def _build_current_task_message(
        self,
        task: WorkTask,
        *,
        context: str,
        peer_context: str,
        decision_governance: str,
        skill_mcp_context: str,
        execution_context: str,
        review_feedback: str,
        gate_iteration: int,
        prev_summary: str,
        run_health_report: str,
        lead_close_policy: str,
    ) -> str:
        delegation_brief = str(task.metadata.get("delegation_brief", "") or "").strip()
        phase_contract_block = self._build_runtime_phase_contract_block(task)
        normalized_phase = self._phase_name_for_task(task).lower()
        is_compact_executor_phase = (
            task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}
            and not normalized_phase.startswith("plan_")
            and not normalized_phase.startswith("lead_")
            and bool(task.metadata.get("phase_contract_enforced"))
        )
        if gate_iteration > 0:
            retry_parts = [
                f"Retry de la tarea {task.task_id}.",
                f"Titulo: {task.title}",
                f"Gate iteration: {gate_iteration}",
                "Manten el enfoque valido anterior y cambia solo lo necesario para resolver el feedback.",
            ]
            if phase_contract_block:
                retry_parts.append(f"Contrato de fase vigente:\n{phase_contract_block}")
            for block in (
                self._context_block("Delegation brief", delegation_brief, 700),
                self._context_block("Feedback de revision", review_feedback, 900),
                self._context_block("Historial del intento previo", prev_summary, 700),
                self._context_block("Resultados de ejecucion", execution_context, 700),
            ):
                if block:
                    retry_parts.append(block)
            retry_parts.append(
                "Respuesta eficiente: enumera cambios concretos, evidencia actualizada, riesgos residuales y siguiente accion."
            )
            return "\n\n".join(retry_parts)

        if is_compact_executor_phase:
            parts = [
                f"Tarea actual: {task.task_id}",
                f"Titulo: {task.title}",
                "Modo de ejecucion: sigue el contrato del Lead sin reinterpretar la solicitud original ni reabrir el slice.",
                "Entrega: solo decision ejecutora, evidencia material, cambios concretos y siguiente accion inmediata.",
            ]
            if phase_contract_block:
                parts.append(f"Contrato de fase vigente:\n{phase_contract_block}")
            for block in (
                self._context_block("Delegation brief", delegation_brief, 500),
                self._context_block("Contexto operativo", context, 900),
                self._context_block("Resultados de ejecucion", execution_context, 900),
                self._context_block("Feedback de revision", review_feedback, 900),
            ):
                if block:
                    parts.append(block)
            parts.append(
                "No reinterpretes la solicitud inicial del usuario. Si el contrato es ambiguo o invalido, reporta la contradiccion concreta y para."
            )
            return "\n\n".join(parts)

        parts = [
            f"Tarea actual: {task.task_id}",
            f"Titulo: {task.title}",
            f"Descripcion: {task.description}",
            f"Gate iteration: {gate_iteration}",
            "Entrega: propuesta, evidencia, aportes considerados, decision final, plan ejecutable inmediato y definition of done.",
        ]
        if normalized_phase.startswith("plan_"):
            parts.append(
                "FORMATO DE RESPUESTA: esquema compacto. Usa secciones cortas y bullets; evita auditorias narrativas largas."
            )
        elif normalized_phase == "lead_intake":
            parts.append(
                "FORMATO DE RESPUESTA: decide de forma compacta. Prioriza objetivo, slice, fases y siguiente accion."
            )
        if phase_contract_block:
            parts.append(f"Contrato de fase vigente:\n{phase_contract_block}")

        # Gap 3: inyectar contenido de archivos del workspace para build en solo_lead.
        # El Lead escribe codigo ciego sin ver los archivos existentes → esto corrige eso.
        if bool(task.metadata.get("direct_coding_executor", False)) and normalized_phase == "build":
            _ws_ctx = self._build_solo_lead_workspace_context(task)
            if _ws_ctx:
                parts.append(_ws_ctx)

        for block in (
                self._context_block("Delegation brief", delegation_brief, 800),
                self._context_block("Lead Close Policy", lead_close_policy, 1800),
                self._context_block("Run Health Report", run_health_report, 2400),
                self._context_block("Contexto de equipo", context, 1200),
                self._context_block("Consulta entre pares", peer_context, 1000),
                self._context_block("Gobernanza de decision", decision_governance, 900),
                self._context_block("Skills y MCP relevantes", skill_mcp_context, 800),
            self._context_block("Resultados de ejecucion", execution_context, 1000),
            self._context_block("Feedback de revision", review_feedback, 1000),
            self._context_block("Historial del intento previo", prev_summary, 700),
        ):
            if block:
                parts.append(block)
        parts.append(
            "Responde de forma eficiente: poco relleno, detalle solo donde cambie la decision o la ejecucion."
        )
        return "\n\n".join(parts)

    def _build_persisted_thread_task_turn(
        self,
        task: WorkTask,
        *,
        gate_iteration: int,
        current_user_turn: str,
    ) -> str:
        phase_name = str(task.metadata.get("phase", "") or "").strip()
        normalized_phase = phase_name.lower()
        if (
            task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}
            and not normalized_phase.startswith("plan_")
            and not normalized_phase.startswith("lead_")
            and bool(task.metadata.get("phase_contract_enforced"))
        ):
            compact_bits = [
                f"Tarea ejecutora: {task.task_id}",
                f"Fase: {phase_name or task.title}",
            ]
            phase_contract = dict(task.metadata.get("phase_contract") or {})
            objective = str(
                phase_contract.get("objective")
                or task.metadata.get("delegation_brief")
                or ""
            ).strip()
            if objective:
                compact_bits.append(
                    "Objetivo compacto: " + " ".join(objective.split())[:280]
                )
            if gate_iteration > 0:
                compact_bits.append(f"Retry gate_iteration={gate_iteration}")
            return "\n".join(compact_bits)
        if normalized_phase == "lead_close" or normalized_phase.startswith("lead_"):
            compact_bits = [
                f"Tarea de control: {task.task_id}",
            ]
            if phase_name:
                compact_bits.append(f"Fase: {phase_name}")
            if gate_iteration > 0:
                compact_bits.append(f"Retry gate_iteration={gate_iteration}")
            phase_contract = dict(task.metadata.get("phase_contract") or {})
            objective = str(
                phase_contract.get("objective")
                or task.metadata.get("delegation_brief")
                or ""
            ).strip()
            if not objective and normalized_phase.startswith("lead_preflight_"):
                preflight_phase = str(task.metadata.get("preflight_phase", "") or "").strip()
                sensitive_reasons = ", ".join(
                    str(item).strip()
                    for item in list(task.metadata.get("preflight_sensitive_reasons", []) or [])
                    if str(item).strip()
                )
                objective = (
                    f"Autorizar fase sensible {preflight_phase or task.title}."
                    + (f" Motivos: {sensitive_reasons}." if sensitive_reasons else "")
                ).strip()
            if not objective and normalized_phase == "lead_close":
                objective = "Sintetizar el run, decidir cierre y siguiente paso."
            if not objective:
                objective = phase_name or "control_task"
            if objective:
                compact_bits.append(
                    "Objetivo compacto: " + " ".join(objective.split())[:240]
                )
            return "\n".join(compact_bits)
        return current_user_turn

    def _build_task_messages(
        self,
        task: WorkTask,
        *,
        assignee: str,
        ab_version: str,
        thread: ConversationThread,
        context: str,
        peer_context: str,
        decision_governance: str,
        skill_mcp_context: str,
        execution_context: str,
        review_feedback: str,
        gate_iteration: int,
        prev_summary: str,
        run_health_report: str,
        lead_close_policy: str,
    ) -> list[dict[str, str]]:
        system_message = build_system_prompt(
            task.role,
            ab_version=ab_version,
            task_metadata=task.metadata,
        )
        current_user_turn = self._build_current_task_message(
            task,
            context=context,
            peer_context=peer_context,
            decision_governance=decision_governance,
            skill_mcp_context=skill_mcp_context,
            execution_context=execution_context,
            review_feedback=review_feedback,
            gate_iteration=gate_iteration,
            prev_summary=prev_summary,
            run_health_report=run_health_report,
            lead_close_policy=lead_close_policy,
        )
        if (
            task.role in {Role.ENGINEER, Role.REVIEWER, Role.QA}
            and not self._phase_name_for_task(task).lower().startswith("plan_")
            and bool(task.metadata.get("phase_contract_enforced"))
        ):
            thread_messages = []
        else:
            thread_messages = self._thread_messages(
                thread,
                task_root=self._task_root(task.task_id),
                limit=6,
            )
        messages = [{"role": "system", "content": system_message}]
        messages.extend(thread_messages)
        messages.append({"role": "user", "content": current_user_turn})
        self.event_logger.emit(
            "conversation_messages_built",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "thread_id": thread.thread_id,
                "thread_generation": int(getattr(thread, "generation", 1) or 1),
                "thread_version": str(getattr(thread, "thread_version", "") or ""),
                "thread_provider": str(getattr(thread, "provider", "") or ""),
                "thread_channel": str(getattr(thread, "channel", "") or ""),
                "thread_model_family": str(getattr(thread, "model_family", "") or ""),
                "message_count": len(messages),
                "history_turns": len(thread_messages),
                "gate_iteration": gate_iteration,
            },
        )
        return messages

    def _messages_for_adapter_attempt(
        self,
        *,
        task: WorkTask,
        assignee: str,
        ab_version: str,
        base_thread: ConversationThread,
        adapter,
        context: str,
        peer_context: str,
        decision_governance: str,
        skill_mcp_context: str,
        execution_context: str,
        review_feedback: str,
        gate_iteration: int,
        prev_summary: str,
        run_health_report: str,
        lead_close_policy: str,
    ) -> tuple[ConversationThread, list[dict[str, str]]]:
        bound_thread = self.thread_store.get_thread(
            agent_id=assignee,
            project_key=self._project_thread_key(),
            role=task.role.value,
            provider=str(getattr(adapter, "provider", "") or ""),
            channel=str(getattr(getattr(adapter, "channel", ""), "value", "") or ""),
            model_family=self._thread_model_family(str(getattr(adapter, "model", "") or "")),
        )
        bound_thread.bind_provider(
            role=task.role.value,
            provider=str(getattr(adapter, "provider", "") or ""),
            channel=str(getattr(getattr(adapter, "channel", ""), "value", "") or ""),
            model_family=self._thread_model_family(str(getattr(adapter, "model", "") or "")),
            model=str(getattr(adapter, "model", "") or ""),
        )
        self.thread_store.save_thread(bound_thread)
        if bound_thread.thread_id != base_thread.thread_id:
            self.event_logger.emit(
                "conversation_thread_candidate_selected",
                {
                    "task_id": task.task_id,
                    "from_thread_id": base_thread.thread_id,
                    "to_thread_id": bound_thread.thread_id,
                    "provider": str(getattr(adapter, "provider", "") or ""),
                    "channel": str(getattr(getattr(adapter, "channel", ""), "value", "") or ""),
                    "model_family": self._thread_model_family(str(getattr(adapter, "model", "") or "")),
                    "thread_generation": int(getattr(bound_thread, "generation", 1) or 1),
                },
            )
        messages = self._build_task_messages(
            task,
            assignee=assignee,
            ab_version=ab_version,
            thread=bound_thread,
            context=context,
            peer_context=peer_context,
            decision_governance=decision_governance,
            skill_mcp_context=skill_mcp_context,
            execution_context=execution_context,
            review_feedback=review_feedback,
            gate_iteration=gate_iteration,
            prev_summary=prev_summary,
            run_health_report=run_health_report,
            lead_close_policy=lead_close_policy,
        )
        return bound_thread, messages

    def _build_peer_messages(
        self,
        *,
        task: WorkTask,
        peer_role: Role,
        assignee: str,
        round_label: str,
        prior_inputs: str = "",
    ) -> list[dict[str, str]]:
        system_message = build_system_prompt(peer_role)
        user_parts = [
            f"Consulta para {assignee} sobre la tarea {task.task_id}.",
            f"Titulo: {task.title}",
            f"Descripcion: {task.description}",
            f"Modo: {round_label}",
        ]
        if prior_inputs.strip():
            user_parts.append(
                self._context_block("Aportes previos de peers", prior_inputs, 900)
            )
        if round_label == "round2":
            user_parts.append(
                "Da posicion final breve: acuerdo, desacuerdo o matiz importante. Maximo 3 oraciones."
            )
        else:
            user_parts.append(
                "Responde con recomendaciones concretas, riesgos y orden de ejecucion. Maximo 8 lineas."
            )
        messages = [
            {"role": "system", "content": system_message},
            {
                "role": "user",
                "content": "\n\n".join(part for part in user_parts if part),
            },
        ]
        self.event_logger.emit(
            "peer_messages_built",
            {
                "task_id": task.task_id,
                "peer_role": peer_role.value,
                "assignee": assignee,
                "round_label": round_label,
                "message_count": len(messages),
            },
        )
        return messages

    def _consume_actionable_mailbox_messages(
        self,
        task: WorkTask,
        assignee: str,
        thread: ConversationThread,
    ) -> list:
        task_root = self._task_root(task.task_id)
        candidates = self.mailbox.list_messages(
            recipient=assignee
        ) + self.mailbox.list_messages(recipient=task.role.value)
        selected = []
        seen_ids: set[str] = set()
        for message in candidates:
            if not message.message_id or message.message_id in seen_ids:
                continue
            seen_ids.add(message.message_id)
            if self.mailbox.is_read(message.message_id) or thread.has_consumed_message(
                message.message_id
            ):
                continue
            if message.kind != "actionable":
                continue
            subject_lower = message.subject.lower()
            if subject_lower.startswith("sync meeting:"):
                continue
            if message.task_id and message.task_id not in {task.task_id, task_root}:
                continue
            if message.sender not in {
                "team_lead",
                "lead-1",
                "system",
            } and message.recipient not in {
                assignee,
                task.role.value,
            }:
                continue
            selected.append(message)

        if not selected:
            return []

        read_ids = []
        for message in selected:
            thread.append_turn(
                role="user",
                content=(
                    f"[MAILBOX] De {message.sender} a {message.recipient}. "
                    f"Asunto: {message.subject}\n{message.body}"
                ),
                source="mailbox",
                task_id=message.task_id or task.task_id,
                message_id=message.message_id,
            )
            if message.message_id:
                read_ids.append(message.message_id)
        if read_ids:
            self.mailbox.mark_read_bulk(read_ids)
            for message in selected:
                if message.message_id:
                    self.mailbox.mark_consumed(message.message_id, consumed_by=assignee)
        self.thread_store.save_thread(thread)
        self.event_logger.emit(
            "conversation_mailbox_consumed",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "message_ids": read_ids,
                "message_count": len(selected),
            },
        )
        return selected

    def _maybe_reply_to_team_lead(
        self,
        task: WorkTask,
        assignee: str,
        response_text: str,
        consumed_messages: list,
    ) -> None:
        if not consumed_messages:
            return
        senders = {msg.sender for msg in consumed_messages}
        if not ({"team_lead", "lead-1", "system"} & senders):
            return
        subject = f"Reply: {task.task_id}"
        body = self._compact_text(response_text, 280)
        self.mailbox.send(
            sender=assignee,
            recipient="team_lead",
            subject=subject,
            body=body,
            task_id=task.task_id,
        )
        self.event_logger.emit(
            "conversation_mailbox_reply",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "recipient": "team_lead",
                "consumed_messages": len(consumed_messages),
            },
        )

    def _compact_text(self, value: str, max_chars: int) -> str:
        clean = self.compliance.redact_text(value.strip().replace("\n", " "))
        if len(clean) <= max_chars:
            return clean
        if max_chars <= 3:
            return clean[:max_chars]
        return clean[: max_chars - 3] + "..."

    def _compact_context(self, lines: list[str], max_lines: int, max_chars: int) -> str:
        compacted = [self._compact_text(line, 220) for line in lines if line.strip()]
        if len(compacted) > max_lines:
            compacted = compacted[:max_lines]
        output = "\n".join(compacted)
        if len(output) <= max_chars:
            return output
        return self._compact_text(output, max_chars)

    def _compose_execution_context(self, results: list) -> str:
        lines = []
        for index, result in enumerate(results, start=1):
            stdout = self._compact_text(result.stdout or "", 180)
            stderr = self._compact_text(result.stderr or "", 180)
            safe_command = self._compact_text(result.command or "", 140)
            lines.append(
                f"{index}. [{result.step_type}] success={result.success} exit={result.exit_code} "
                f"cmd={safe_command} stdout={stdout} stderr={stderr} reason={result.reason}"
            )
        return "\n".join(lines)

    def _fail_task_due_to_compliance(
        self,
        task: WorkTask,
        assignee: str,
        reason: str,
        details: list[str],
    ) -> None:
        safe_details = [self._compact_text(item, 140) for item in details]
        body = f"reason={reason} details={safe_details}"
        self.taskboard.mark_failed(task.task_id, error=reason)
        self.memory.remember(
            agent_id=assignee,
            role=task.role.value,
            kind="compliance_violation",
            content=body,
            task_id=task.task_id,
            tags=["compliance", "blocked"],
        )
        self.event_logger.emit(
            "compliance_violation",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "environment": self.environment,
                "reason": reason,
                "details": safe_details,
            },
        )
        self.mailbox.send(
            sender="compliance",
            recipient="team_lead",
            subject=f"Task blocked by compliance: {task.task_id}",
            body=body,
            task_id=task.task_id,
        )
        self._maybe_run_event_meeting(
            trigger="compliance_violation",
            task_id=task.task_id,
            reason=reason,
        )

    @staticmethod
    def _to_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        return normalized in {"1", "true", "yes", "on"}

    def _meeting_participants(self) -> list[MeetingParticipant]:
        participants: list[MeetingParticipant] = []
        for role in (
            Role.TEAM_LEAD,
            Role.RESEARCHER,
            Role.ENGINEER,
            Role.REVIEWER,
            Role.QA,
        ):
            agents = self.agent_pools.get(role, [])
            if agents:
                participants.append(
                    MeetingParticipant(agent_id=agents[0], role=role.value)
                )
        return participants

    def _run_round_sync_meeting(self) -> None:
        self.communicator.run_sync_meeting(
            topic=f"Round {self._round}",
            participants=self._meeting_participants(),
            meeting_kind="informational",
        )

    def _maybe_run_event_meeting(self, trigger: str, task_id: str, reason: str) -> None:
        key = f"{trigger}:{task_id}"
        if self._last_event_meeting_round.get(key) == self._round:
            return
        self._last_event_meeting_round[key] = self._round
        self.communicator.run_sync_meeting(
            topic=f"Event {trigger} @ {task_id} reason={reason}",
            participants=self._meeting_participants(),
            task_id=task_id,
            meeting_kind="actionable",
        )
