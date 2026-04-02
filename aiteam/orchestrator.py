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
from aiteam.memory import AgentMemoryStore
from aiteam.observability import EventLogger
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

    def _collect_specialist_prefetch_context(self, task: WorkTask) -> str:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        if self._to_bool(metadata.get("skip_specialist_prefetch", False)):
            return ""
        if self._to_bool(metadata.get("_specialist_prefetch_done", False)):
            stored = metadata.get("specialist_prefetch_context", "")
            return str(stored or "")
        task_specialist_name = str(metadata.get("tool_specialist", "") or "").strip().lower()
        if task_specialist_name:
            task_specialist_profile = specialist_profile(task_specialist_name)
            if task_specialist_profile is not None and task_specialist_profile.owner_role == task.role:
                metadata["_specialist_prefetch_done"] = True
                return ""

        required_caps = normalize_tool_capabilities(metadata.get("required_capabilities", []))
        skill_targets = normalize_skill_targets(metadata.get("skill_targets", []))
        lsp_targets = normalize_lsp_targets(metadata.get("lsp_targets", []))
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
            specialist_request = RoutingRequest(
                role=profile.owner_role,
                complexity=task.complexity,
                criticality=task.criticality,
                required_capabilities=set(required_caps),
                tool_specialist=specialist_name,
                tool_rewiring_preferred_specialist=str(
                    metadata.get("tool_rewiring_preferred_specialist", "") or ""
                ).strip(),
                prefer_economic_routing=True,
                preferred_tool_tier=profile.default_tier,
                skill_targets=set(skill_targets),
                lsp_targets=set(lsp_targets),
                approved_adapters=self.compliance.approved_adapters(metadata),
                excluded_adapters=set(),
                sensitive_approval=self.compliance.evaluate_sensitive_approval(task.metadata)[0],
                environment=self.environment,
            )
            specialist_prompt = build_prompt(
                profile.owner_role,
                f"Specialist precheck: {task.title}",
                (
                    f"Tarea principal: {task.title}\n"
                    f"Descripcion: {task.description}\n"
                    "Devuelve un informe operativo compacto para ayudar a otra tarea principal."
                ),
            )
            specialist_messages = [
                {
                    "role": "system",
                    "content": build_system_prompt(
                        profile.owner_role,
                        task_metadata=specialist_metadata,
                    ),
                },
                {
                    "role": "user",
                    "content": (
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
        metadata["specialist_reports"] = existing_reports + reports
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
                    return checkpoint_id
                task.metadata["lead_preflight_approved"] = True
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
                "Objetivo: decidir si se puede continuar, si conviene pedir aclaracion "
                "al usuario o si hay que replantear el enfoque antes de ejecutar."
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
            if phase_name.startswith(("lead_preflight_", "lead_report_")):
                dependency_phases.append(phase_name)

        if not dependency_phases:
            phase_name = str(task.metadata.get("phase", "") or "").strip()
            if phase_name == "lead_close":
                dependency_phases = [
                    name
                    for name in phase_outputs.keys()
                    if isinstance(name, str) and name.startswith("lead_report_")
                ]
            else:
                dependency_phases = [
                    name
                    for name in phase_outputs.keys()
                    if isinstance(name, str) and name.startswith("lead_preflight_")
                ]

        for checkpoint_phase in dependency_phases:
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

    def _build_dependency_output_context(self, task: WorkTask) -> str:
        if not task.dependencies:
            return ""
        # lead_close necesita mas contexto para no fabricar informacion:
        # Researcher (900) y QA (800) deben llegar completos al Team Lead.
        _phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
        _is_lead_close = _phase_name == "lead_close"
        lines: list[str] = []
        for dep_id in task.dependencies:
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                continue
            task_root = self._task_root(task.task_id)
            ws = self.workflow_state.get(task_root, {})
            phase_summaries = ws.get("phase_context_summaries", {})
            dep_phase = str(dep_task.metadata.get("phase", "") or "").strip()
            compact_summary = ""
            if isinstance(phase_summaries, dict) and dep_phase:
                compact_summary = str(phase_summaries.get(dep_phase, "") or "").strip()
            result = dep_task.metadata.get("result", "")
            if not result and not compact_summary:
                continue
            phase_label = dep_task.role.value.replace("_", " ").title()
            role = dep_task.role.value
            if _is_lead_close and role in ("researcher", "qa"):
                limit = 900 if role == "researcher" else 800
            else:
                limit = 400
            compacted = compact_summary or self._compact_text(str(result or ""), limit)
            lines.append(f"[{phase_label}] {compacted}")
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
        return build_run_health_report(
            phase_tasks=phase_tasks,
            gate_tasks=gate_tasks,
            routing_failures=self.router.get_recent_routing_failures(task_root=task_root),
            missing_api_keys=self.router.get_missing_api_keys(task_root=task_root),
            unavailable_models=self.router.get_unavailable_models(task_root=task_root),
            rounds_used=rounds_used,
            round_budget=round_budget,
            auto_extensions=auto_extensions,
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
        self.taskboard.add_task(task)
        self.mailbox.send(
            sender="system",
            recipient="team_lead",
            subject=f"Nueva tarea: {task.task_id}",
            body=f"{task.title}",
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

    def _maybe_spawn_deferred_delegates(self, task_id: str) -> None:
        """C1: Spawn evidence delegate tasks that were deferred until the parent
        phase starts executing. Runs once per task (guarded by delegates_spawned flag)."""
        task = self.taskboard.get_task(task_id)
        if task is None:
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
                    dependencies=[task_id],
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

        execution_plan = task.metadata.get("execution_plan", [])
        execution_context = ""
        require_execution_plan = bool(
            task.metadata.get("require_execution_plan", False)
        )
        demo_fast_mode = sim_mode_enabled()
        if (
            require_execution_plan
            and (not isinstance(execution_plan, list) or not execution_plan)
            and not demo_fast_mode
        ):
            self._fail_task_due_to_compliance(
                task=task,
                assignee=assignee,
                reason="missing_execution_plan_required",
                details=["Task requires execution_plan but none was provided"],
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
            self.taskboard.mark_blocked(task.task_id, reason="specialist_quorum_not_met")
            self.taskboard.update_metadata(
                task.task_id,
                {
                    "specialist_quorum_missing": list(
                        specialist_quorum.get("missing_specialists", []) or []
                    ),
                    "specialist_quorum_received": list(
                        specialist_quorum.get("received_specialists", []) or []
                    ),
                },
            )
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
            task.role, task.title, task.description, ab_version=ab_version
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
        )
        thread.append_turn(
            role="user",
            content=current_user_turn,
            source="task_retry" if gate_iteration > 0 else "task",
            task_id=task.task_id,
        )
        self.thread_store.save_thread(thread)

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
            required_capabilities=set(task.metadata.get("required_capabilities", [])),
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

        decision = self.router.route_and_invoke(
            request=request,
            prompt=prompt,
            task_id=task.task_id,
            messages=messages,
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
            }
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
                    decision = self.router.route_and_invoke(
                        request=request,
                        prompt=prompt,
                        task_id=task.task_id,
                        messages=messages,
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
                    approved_adapters=request.approved_adapters,
                    sensitive_approval=request.sensitive_approval,
                    environment=request.environment,
                )
                session.record_action(
                    "llm_call",
                    f"adaptive_retry_{retry_count + 1}:relaxed_capabilities",
                )
                decision = self.router.route_and_invoke(
                    request=fallback_request,
                    prompt=prompt,
                    task_id=task.task_id,
                    messages=messages,
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
            decision = self.router.route_and_invoke(
                request=request,
                prompt=prompt,
                task_id=task.task_id,
                messages=followup_messages,
                tools=None,  # no tools en el segundo round para evitar bucle infinito
            )
            session.record_action(
                "llm_call", f"native_tool_followup:{len(tc_results)}_tools"
            )

        if decision.success:
            safe_content = self.compliance.redact_text(decision.response.content)

            # ── Agent tool invocation: parsear [USE_TOOL] y ejecutar ──
            if self._USE_TOOL_RE.search(safe_content):
                safe_content, _tool_results = self._parse_and_invoke_tools(
                    task,
                    assignee,
                    safe_content,
                    session,
                )
                safe_content = self.compliance.redact_text(safe_content)

            found_placeholders = [
                label
                for label, pattern in _PLACEHOLDER_OUTPUT_PATTERNS
                if pattern.search(safe_content)
            ]
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
                reports = list(task.metadata.get("specialist_reports", []) or [])
                reports.append(parsed_report.to_metadata())
                task.metadata["specialist_reports"] = reports
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

            self.taskboard.mark_completed(task.task_id, details=safe_content)
            self._emit_agent_event(
                {
                    "type": "agent_completed",
                    "task_id": task.task_id,
                    "agent_id": assignee,
                    "role": task.role.value,
                    "phase": _phase,
                    "preview": safe_content[:200] if safe_content else "",
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
            self._notify_dependents(task.task_id, summary=safe_content)
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

    _MAX_TOOL_CALLS_PER_TASK = 3

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
        recent = self.memory.recent(
            source_agent,
            limit=5,
            exclude_kinds=excluded,
            project_key=self._project_thread_key(),
        )
        relevant = self.memory.relevant(
            source_agent,
            f"{task.title}\n{task.description}",
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
            return self.router.route_and_invoke(
                request=base_request,
                prompt=prompt,
                task_id=task.task_id,
                messages=messages,
            )

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
        if fallback_decision.success or diverse_decision is None:
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
        if task.metadata.get("require_peer_consultation"):
            return True
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

    def _notify_dependents(self, task_id: str, summary: str) -> None:
        for candidate in self.taskboard.list_tasks():
            if task_id not in candidate.dependencies:
                continue
            recipient = self._assignee_for_role(candidate.role)
            self.communicator.send_dm(
                sender="team_lead",
                recipient=recipient,
                subject=f"Dependency ready: {task_id}",
                body=f"La dependencia de {candidate.task_id} esta resuelta. Resumen: {summary[:200]}",
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
        relevant_memory = self.memory.relevant(
            assignee,
            task.description,
            limit=3,
            exclude_kinds=excluded_memory,
            project_key=self._project_thread_key(),
        )
        recent_memory = self.memory.recent(
            assignee,
            limit=2,
            exclude_kinds=excluded_memory,
            project_key=self._project_thread_key(),
        )
        dm_messages = self._context_messages(recipient=assignee)
        role_messages = self._context_messages(recipient=task.role.value)

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

        # ── Facts del equipo (workflow state) ──
        task_root = self._task_root(task.task_id)
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
            query=task.description,
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

    def _context_messages(self, recipient: str) -> list:
        messages = self.mailbox.list_messages(recipient=recipient)
        filtered = [
            message
            for message in messages
            if not message.subject.lower().startswith("sync meeting:")
        ]
        return filtered[-4:]

    def _project_thread_key(self) -> str:
        return str(self.project_root)

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

    def _thread_context(self, thread: ConversationThread, limit: int = 6) -> str:
        turns = thread.recent_turns(limit=limit)
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
        limit: int = 6,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in thread.recent_turns(limit=limit):
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
    ) -> str:
        delegation_brief = str(task.metadata.get("delegation_brief", "") or "").strip()
        if gate_iteration > 0:
            retry_parts = [
                f"Retry de la tarea {task.task_id}.",
                f"Titulo: {task.title}",
                f"Gate iteration: {gate_iteration}",
                "Manten el enfoque valido anterior y cambia solo lo necesario para resolver el feedback.",
            ]
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

        parts = [
            f"Tarea actual: {task.task_id}",
            f"Titulo: {task.title}",
            f"Descripcion: {task.description}",
            f"Gate iteration: {gate_iteration}",
            "Entrega: propuesta, evidencia, aportes considerados, decision final, plan ejecutable inmediato y definition of done.",
        ]
        for block in (
                self._context_block("Delegation brief", delegation_brief, 800),
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
        )
        thread_messages = self._thread_messages(thread, limit=6)
        messages = [{"role": "system", "content": system_message}]
        messages.extend(thread_messages)
        messages.append({"role": "user", "content": current_user_turn})
        self.event_logger.emit(
            "conversation_messages_built",
            {
                "task_id": task.task_id,
                "assignee": assignee,
                "thread_id": thread.thread_id,
                "message_count": len(messages),
                "history_turns": len(thread_messages),
                "gate_iteration": gate_iteration,
            },
        )
        return messages

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
