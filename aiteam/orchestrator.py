from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import threading
from dataclasses import dataclass
from pathlib import Path

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
from aiteam.mailbox import Mailbox
from aiteam.memory import AgentMemoryStore
from aiteam.observability import EventLogger
from aiteam.profiles import build_prompt, build_system_prompt, role_charter_for
from aiteam.router import HybridRouter
from aiteam.runtime import SandboxManager
from aiteam.taskboard import TaskBoard
from aiteam.types import Role, RoutingDecision, RoutingRequest, TaskState, WorkTask


@dataclass
class PeerConsultationReport:
    text: str
    consulted_roles: list[str]
    unavailable_roles: list[str]


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
        self.taskboard = TaskBoard(runtime_dir / "tasks.json")
        self.mailbox = Mailbox(runtime_dir / "mailbox.jsonl")
        self.sandboxes = SandboxManager(runtime_dir / "sandboxes")
        self.event_logger = EventLogger(runtime_dir)
        self.project_root = (project_root or runtime_dir.parent).resolve()
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
        self._workflow_state_path = runtime_dir / "workflow_state.json"
        self._load_workflow_state()
        self.session_store = SessionStore(runtime_dir)
        self._init_tool_dispatcher()

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
        except Exception:
            self.mcp_manager = None

    # ── Workflow State (shared blackboard) ──────────────────────────

    def _load_workflow_state(self) -> None:
        if self._workflow_state_path.exists():
            try:
                raw = self._workflow_state_path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
                if isinstance(data, dict):
                    self.workflow_state = data
            except (json.JSONDecodeError, OSError):
                pass

    def _save_workflow_state(self) -> None:
        import tempfile

        content = json.dumps(
            self.workflow_state, indent=2, ensure_ascii=False, default=str
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self._workflow_state_path.parent,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
            tmp_path.replace(self._workflow_state_path)
        except Exception:
            if "tmp_path" in dir():
                tmp_path.unlink(missing_ok=True)

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
        self._save_workflow_state()
        self.event_logger.emit(
            "workflow_state_updated",
            {"task_root": task_root, "phase": phase, "facts_count": len(ws["facts"])},
        )

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
        self._save_workflow_state()

    # ── Dependency output context ───────────────────────────────────

    def _build_dependency_output_context(self, task: WorkTask) -> str:
        if not task.dependencies:
            return ""
        lines: list[str] = []
        for dep_id in task.dependencies:
            dep_task = self.taskboard.get_task(dep_id)
            if dep_task is None or dep_task.state != TaskState.COMPLETED:
                continue
            result = dep_task.metadata.get("result", "")
            if not result:
                continue
            phase_label = dep_task.role.value.replace("_", " ").title()
            compacted = self._compact_text(result, 400)
            lines.append(f"[{phase_label}] {compacted}")
        if not lines:
            task_root = self._task_root(task.task_id)
            ws = self.workflow_state.get(task_root, {})
            phase_outputs = ws.get("phase_outputs", {})
            for phase, output in phase_outputs.items():
                lines.append(f"[{phase}] {self._compact_text(output, 300)}")
        return "\n".join(lines[:8])

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

    def _claim_ready_tasks(
        self, active_round: int, sub_iteration: int
    ) -> list[WorkTask]:
        """Reclama todas las tareas READY disponibles."""
        claimed_tasks: list[WorkTask] = []
        for task in self.taskboard.ready_tasks():
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
            self.taskboard.checkpoint()

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

    def _run_task(self, task: WorkTask) -> None:
        _task_type = (
            task.task_id.split("::")[-1] if "::" in task.task_id else task.role.value
        )
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
        if require_execution_plan and (
            not isinstance(execution_plan, list) or not execution_plan
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

        context = self._build_collaboration_context(task=task, assignee=assignee)
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
        skill_mcp_context = self._build_skill_mcp_context(task=task, assignee=assignee)

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
                actions_summary = "; ".join(
                    f"{a.action_type}:{a.detail[:60]}"
                    for a in (last.actions or [])[-5:]
                )
                prev_summary = (
                    f"Intento anterior (iteracion {gate_iteration - 1}): "
                    f"status={last.status or 'unknown'}, "
                    f"acciones=[{actions_summary}], "
                    f"resumen={self._compact_text(last.summary or '', 200)}"
                )

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
            approved_adapters=self.compliance.approved_adapters(task.metadata),
            sensitive_approval=sensitive_approval,
            environment=self.environment,
        )
        session.record_action("llm_call", f"route_and_invoke:{task.role.value}")
        native_tools = self._build_native_tools_for_task(task)
        decision = self.router.route_and_invoke(
            request=request,
            prompt=prompt,
            task_id=task.task_id,
            messages=messages,
            tools=native_tools if native_tools else None,
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
                {"role": "assistant", "content": f"[Usando herramientas: {', '.join(tc.name for tc in decision.response.tool_calls)}]"},
                {"role": "user", "content": tool_msg + "\n\nContinua con tu tarea usando los resultados anteriores."},
            ]
            decision = self.router.route_and_invoke(
                request=request,
                prompt=prompt,
                task_id=task.task_id,
                messages=followup_messages,
                tools=None,  # no tools en el segundo round para evitar bucle infinito
            )
            session.record_action("llm_call", f"native_tool_followup:{len(tc_results)}_tools")

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

            lower_content = safe_content.lower()
            placeholders = [
                "todo:",
                "fixme:",
                "simulated output",
                "insert code here",
                "placeholder",
            ]
            found_placeholders = [p for p in placeholders if p in lower_content]
            if found_placeholders and not task.metadata.get("skip_placeholder_check"):
                reason = f"Placeholder detected: {', '.join(found_placeholders)}"
                self.taskboard.mark_failed(task.task_id, error=reason)
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
            # Evidence gate: solo para fases de build/ejecucion, no para plan_*
            _phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
            _is_planning_phase = _phase_name.startswith("plan_") or _phase_name in (
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
                task.role in (Role.ENGINEER, Role.QA)
                and not task.metadata.get("skip_evidence_gate")
                and not _is_planning_phase
            ):
                has_evidence, reason = self._verify_task_evidence(task, workspace)
                if not has_evidence:
                    evidence_error = f"EvidenceGate Blocked: {reason}"
                    self.taskboard.mark_failed(task.task_id, error=evidence_error)
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

            # ── Cerrar sesion exitosa ──
            self.session_store.close_session(
                session, summary=safe_content[:300], status="completed"
            )
            return

        failure_text = self.compliance.redact_text(
            decision.response.error or decision.reason
        )
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
                required_capabilities=set(task.metadata.get("required_capabilities", [])),
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
            native.append(NativeToolDefinition(
                name=t.name,
                description=t.description or f"Herramienta: {t.name}",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "El comando o accion a ejecutar con esta herramienta"
                        }
                    },
                    "required": ["command"],
                },
            ))
        return native[:5]

    def _execute_native_tool_calls(
        self, tool_calls: list, task: WorkTask, assignee: str, session
    ) -> list[dict]:
        """Ejecuta tool_calls nativos y retorna lista de resultados."""
        results = []
        for tc in tool_calls[:self._MAX_TOOL_CALLS_PER_TASK]:
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
                result = ToolResult(tool_name=tc.name, success=False, error="no_dispatcher")
            self.event_logger.emit("agent_tool_invocation", {
                "task_id": task.task_id,
                "assignee": assignee,
                "tool": tc.name,
                "native": True,
                "success": result.success,
            })
            results.append({
                "id": tc.id,
                "name": tc.name,
                "success": result.success,
                "output": (result.output or "")[:2000],
                "error": (result.error or "")[:500],
            })
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
        import subprocess

        try:
            repo = self.project_root or workspace
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                diff_proc = subprocess.run(
                    ["git", "diff"],
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                diff_content = diff_proc.stdout.strip()
                if not diff_content:
                    diff_proc = subprocess.run(
                        ["git", "diff", "--cached"],
                        cwd=str(repo),
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    diff_content = diff_proc.stdout.strip()

                task.metadata["git_diff_evidence"] = diff_content
                return True, "git_diff_detected"
        except Exception:
            pass

        _agent_output = str(task.metadata.get("_last_agent_output", ""))

        # Modo simulado/mock: si el agente produjo alguna salida no vacia,
        # aceptarla como evidencia minima para no cortar el workflow artificialmente.
        live_api_enabled = os.getenv("AITEAM_ENABLE_LIVE_API", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not live_api_enabled and _agent_output.strip():
            return True, "simulated_mode_accepted"

        # ── Modo live sin git diff: validar calidad minima del output por rol ──
        # Un LLM puede devolver "Tarea completada." y no hacer nada. Se exige
        # contenido tecnico minimo apropiado al rol antes de aceptar como evidencia.
        if live_api_enabled and _agent_output.strip():
            _phase_name = (
                task.task_id.split("::")[-1] if "::" in task.task_id else ""
            )
            quality_ok, quality_reason = self._assess_output_quality(
                _agent_output, task.role, _phase_name
            )
            if quality_ok:
                return True, f"live_output_quality:{quality_reason}"

        # ── Fallback: tarea conversacional / teorica ────────────────────────
        # Si la tarea fue marcada como conversacional, aceptar:
        #   (a) documentacion generada (.md/.txt) en el workspace o runtime/
        #   (b) output LLM sustancial (>400 chars) → se persiste como artefacto
        if task.metadata.get("conversational"):
            # (a) buscar archivos de documentacion recien creados
            doc_exts = {".md", ".txt", ".rst", ".adoc"}
            for search_root in [workspace, self.runtime_dir]:
                try:
                    for p in Path(search_root).rglob("*"):
                        if p.suffix.lower() in doc_exts and p.is_file():
                            task.metadata["doc_evidence"] = str(p)
                            return True, f"conversational_doc:{p.name}"
                except Exception:
                    pass
            # (b) output sustancial del LLM
            if len(_agent_output.strip()) >= 400:
                # Persistir como artefacto de documentacion en runtime/
                try:
                    doc_dir = Path(self.runtime_dir) / "docs"
                    doc_dir.mkdir(parents=True, exist_ok=True)
                    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", task.task_id)
                    doc_path = doc_dir / f"{safe_id}.md"
                    doc_path.write_text(
                        f"# {task.title}\n\n{_agent_output}\n",
                        encoding="utf-8",
                    )
                    task.metadata["doc_evidence"] = str(doc_path)
                    return True, f"conversational_output_persisted:{doc_path.name}"
                except Exception:
                    pass
            # (c) si tiene output pero no alcanza umbral → pasa igualmente (es una respuesta valida)
            if _agent_output.strip():
                return True, "conversational_response_accepted"

        return (
            False,
            "Strict Evidence Gate: No file modifications detected. Tasks must produce tangible output.",
        )

    @staticmethod
    def _assess_output_quality(
        output: str, role: Role, phase: str
    ) -> tuple[bool, str]:
        """Valida calidad minima del output LLM en modo live (sin git diff).

        Evita que respuestas triviales como "Tarea completada." pasen el gate.
        El orden de checks es: trivial → rol especifico → longitud minima.
        Retorna (pasa, razon).
        """
        text = output.strip()
        if not text:
            return False, "output_vacio"

        lower = text.lower()

        # Detectar respuestas triviales sin contenido tecnico real (cualquier longitud).
        trivial_patterns = [
            "tarea completada", "task completed", "done.", "listo.",
            "completado.", "finalizado.", "he completado", "he realizado",
            "he implementado", "como se solicito",
        ]
        is_trivial = any(p in lower for p in trivial_patterns) and len(text) < 200
        if is_trivial:
            return False, "output_trivial_sin_contenido_tecnico"

        if role == Role.REVIEWER:
            # Un reviewer debe producir observaciones accionables.
            reviewer_signals = [
                "issue", "problema", "error", "bug", "sugerencia", "mejora",
                "recomendacion", "fix:", "correc", "falta", "observacion",
                "nota:", "- ", "* ", "1.", "2.", "•",
            ]
            has_signal = any(s in lower for s in reviewer_signals)
            if has_signal or len(text) >= 300:
                return True, "review_con_observaciones"
            if len(text) < 80:
                return False, f"output_muy_corto:{len(text)}_chars"
            return False, "review_sin_observaciones_accionables"

        if role == Role.QA:
            # QA debe reportar resultados de tests o analisis de calidad.
            qa_signals = [
                "passed", "failed", "error", "test", "prueba", "resultado",
                "pass", "fail", "assert", "verificado", "ok:", "✓", "✗",
                "coverage", "cobertura", "suite",
            ]
            has_signal = any(s in lower for s in qa_signals)
            if has_signal:
                return True, "qa_con_resultados"
            if len(text) >= 300:
                return True, "qa_output_sustancial"
            return False, "qa_sin_resultados_de_test"

        # ENGINEER y otros roles: exigir output tecnico sustancial.
        if len(text) < 80:
            return False, f"output_muy_corto:{len(text)}_chars"

        if len(text) >= 200:
            return True, "substantial_technical_output"

        return False, f"output_insuficiente_en_live:{len(text)}_chars"

    # ── Conversational task detection ────────────────────────────────────

    # Keywords que indican preguntas o tareas puramente conceptuales/teoricas
    _CONVERSATIONAL_KEYWORDS = frozenset(
        {
            # Español
            "¿",
            "explica",
            "explícame",
            "describe",
            "qué es",
            "qué son",
            "cuál es",
            "cuáles son",
            "cómo funciona",
            "cómo se",
            "por qué",
            "para qué",
            "diferencia entre",
            "compara",
            "análisis",
            "analiza",
            "reflexión",
            "reflexiona",
            "opinión",
            "filosofía",
            "filosófico",
            "teoría",
            "teórico",
            "estrategia",
            "recomendación",
            "recomienda",
            "debería",
            "consejo",
            "resumen",
            "resume",
            "resume",
            "enumera",
            "lista de",
            "ventajas",
            "desventajas",
            "pros y contras",
            "cuándo",
            "qué piensas",
            # English
            "what is",
            "what are",
            "how does",
            "how do",
            "why is",
            "why are",
            "explain",
            "describe",
            "compare",
            "analysis",
            "analyze",
            "review",
            "theory",
            "theoretical",
            "philosophy",
            "opinion",
            "strategy",
            "recommend",
            "should i",
            "pros and cons",
            "when to",
            "what do you think",
            "summarize",
            "summary",
            "list of",
            "advantages",
            "disadvantages",
        }
    )

    @classmethod
    def _detect_conversational_task(cls, task: "WorkTask") -> bool:  # type: ignore[name-defined]
        """Detecta si una tarea es conversacional/teorica (no requiere artefactos)."""
        blob = f"{task.title} {task.description}".lower()
        # Si contiene signo de interrogacion → pregunta directa
        if "?" in blob:
            return True
        # Si contiene keywords conversacionales
        return any(kw in blob for kw in cls._CONVERSATIONAL_KEYWORDS)

    @staticmethod
    def _should_open_quality_gates(task: WorkTask) -> bool:
        if task.role != Role.ENGINEER:
            return False
        if task.metadata.get("quality_gate_spawned"):
            return False
        if task.metadata.get("skip_quality_gates"):
            return False
        return True

    def _build_gate_evidence_context(self, task: WorkTask) -> str:
        """Build rich context for Review/QA gates from the Engineer's work."""
        lines: list[str] = []

        # 1. Engineer's output (from memory)
        parent_sessions = self.session_store.sessions_for_task(task.task_id)
        if parent_sessions:
            last_session = parent_sessions[-1]
            exec_actions = [
                a
                for a in (last_session.actions or [])
                if a.action_type in ("command_exec", "llm_call")
            ]
            if exec_actions:
                lines.append("Acciones del engineer:")
                for a in exec_actions[-6:]:
                    status = "OK" if a.success else "FAIL"
                    lines.append(f"  [{status}] {a.action_type}: {a.detail[:120]}")

        # 2. Parsed git diff summary
        raw_diff = task.metadata.get("git_diff_evidence", "")
        if raw_diff:
            diff_summary = self._summarize_git_diff(raw_diff)
            lines.append(f"Resumen de cambios:\n{diff_summary}")

        # 3. Engineer's decision rationale
        justification = task.metadata.get("decision_justification", "")
        if justification:
            lines.append(
                f"Razonamiento del engineer: {self._compact_text(justification, 300)}"
            )

        # 4. Peer feedback that informed the decision
        consulted = task.metadata.get("consulted_roles", [])
        if consulted:
            lines.append(f"Peers consultados: {', '.join(consulted)}")

        # 5. Gate iteration context (if retry)
        gate_iter = int(task.metadata.get("gate_iteration", 0))
        if gate_iter > 0:
            lines.append(f"NOTA: Esta es la iteracion {gate_iter + 1} de revision.")
            prev_feedback = task.metadata.get("review_feedback", "")
            if prev_feedback:
                lines.append(
                    f"Feedback previo: {self._compact_text(prev_feedback, 300)}"
                )

        return "\n".join(lines)

    @staticmethod
    def _summarize_git_diff(raw_diff: str) -> str:
        """Parse raw git diff into human-readable summary."""
        if not raw_diff:
            return "Sin diferencias detectadas."
        files_changed: dict[str, tuple[int, int]] = {}
        current_file = ""
        added = 0
        removed = 0
        for line in raw_diff.split("\n"):
            if line.startswith("diff --git"):
                if current_file:
                    files_changed[current_file] = (added, removed)
                parts = line.split(" b/")
                current_file = parts[-1] if len(parts) > 1 else line
                added = 0
                removed = 0
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        if current_file:
            files_changed[current_file] = (added, removed)

        total_added = sum(a for a, _ in files_changed.values())
        total_removed = sum(r for _, r in files_changed.values())
        summary_lines = [
            f"{len(files_changed)} archivos, +{total_added}/-{total_removed} lineas"
        ]
        for fname, (a, r) in list(files_changed.items())[:8]:
            summary_lines.append(f"  {fname}: +{a}/-{r}")
        if len(files_changed) > 8:
            summary_lines.append(f"  ... y {len(files_changed) - 8} archivos mas")
        return "\n".join(summary_lines)

    def _spawn_quality_gates(self, task: WorkTask) -> None:
        skip_evidence = task.metadata.get("skip_evidence_gate", False)
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
        unavailable = ", ".join(peer_report.unavailable_roles) or "ninguno"
        return (
            f"Agente: {assignee} ({task.role.value}).\n"
            f"Rango de decision activo: R{charter.decision_rank}/5.\n"
            f"Personalidad esperada: {charter.personality}.\n"
            f"Peers consultados: {consulted}.\n"
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
        unavailable = ", ".join(peer_report.unavailable_roles) or "none"
        justification = (
            f"decision_rank=R{charter.decision_rank}/5 assignee={assignee} role={task.role.value}; "
            f"consulted={consulted}; unavailable={unavailable}; "
            f"provider={decision.provider} model={decision.model} channel={decision.channel.value}; "
            f"attempts={decision.attempts}; output_excerpt={output[:240]}"
        )
        self.taskboard.update_metadata(
            task.task_id,
            {
                "decision_rank": charter.decision_rank,
                "decision_personality": charter.personality,
                "consulted_roles": peer_report.consulted_roles,
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
                "provider": decision.provider,
                "model": decision.model,
                "channel": decision.channel.value,
            },
        )

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
        for peer_role in peer_roles:
            peer_agent = self._assignee_for_role(peer_role)
            request = RoutingRequest(
                role=peer_role,
                complexity=task.complexity,
                criticality=task.criticality,
                required_capabilities=self._peer_capabilities(peer_role),
                environment=self.environment,
            )
            peer_prompt = self._peer_prompt_for_task(task, peer_role)
            peer_messages = self._build_peer_messages(
                task=task,
                peer_role=peer_role,
                assignee=assignee,
                round_label="round1",
            )
            decision = self.router.route_and_invoke(
                request=request,
                prompt=peer_prompt,
                task_id=task.task_id,
                messages=peer_messages,
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
                r2_request = RoutingRequest(
                    role=peer_role,
                    complexity=task.complexity,
                    criticality=task.criticality,
                    required_capabilities=self._peer_capabilities(peer_role),
                    environment=self.environment,
                )
                r2_messages = self._build_peer_messages(
                    task=task,
                    peer_role=peer_role,
                    assignee=assignee,
                    round_label="round2",
                    prior_inputs=all_inputs,
                )
                r2_decision = self.router.route_and_invoke(
                    request=r2_request,
                    prompt=r2_prompt,
                    task_id=task.task_id,
                    messages=r2_messages,
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
        required = {
            item.strip().lower()
            for item in task.metadata.get("required_capabilities", [])
            if str(item).strip()
        }
        guidance = self.tool_integrator.guidance_for_task(
            role=task.role.value,
            description=f"{task.title}\n{task.description}",
            required_capabilities=required,
        )
        text = str(guidance.get("text", "")).strip()
        if not text:
            return ""
        compacted = self._compact_context(
            text.splitlines(), max_lines=14, max_chars=1200
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
    ) -> list[dict[str, str]]:
        system_message = build_system_prompt(task.role, ab_version=ab_version)
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
