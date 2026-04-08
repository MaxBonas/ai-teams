from typing import Literal

from pydantic import BaseModel


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
    quorum: bool = False
    max_rounds: int | None = None
    client_task_id: str = ""
    strict_mode: bool = False
    auto_extend_weak_runs: bool = True
    allow_low_productivity_override: bool = False
    continuation_target: str = ""
    # C2: explicit continuation policy between runs
    # "auto"           — current behavior (no archiving)
    # "clean_retry"    — archive incomplete tasks from prior runs, start fresh
    # "force_continue" — explicitly continue from prior state
    continuation_policy: Literal["auto", "clean_retry", "force_continue"] = "auto"


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
    probe_mode: bool = False
    lead_run_mode: str = "standard"
    planned_phases: list[dict[str, object]] = []
    live_mode_required: bool = False
    live_mode_rejected: bool = False
    advisory_mode: bool = False
    advisory_reason: str = ""
    degraded_delivery: bool = False
    degrade_scope: str = ""
    degrade_reason: str = ""
    skipped_phase_ids: list[str] = []
    skipped_phase_reasons: dict[str, str] = {}
    policy_review_required: bool = False
    validation_owner: str = ""
    policy_signals: list[str] = []
    run_verdict: dict[str, object] = {}
    lead_close_policy: dict[str, object] = {}
    phase_verdicts: dict[str, dict[str, object]] = {}
    phase_contracts: dict[str, dict[str, object]] = {}
    phase_evidence_plan: dict[str, dict[str, object]] = {}
    delegate_batches: list[dict[str, object]] = []
    delegate_economics: dict[str, object] = {}
    specialist_reports: list[dict[str, object]] = []
    specialist_report_summary: dict[str, object] = {}
    peer_consultation_summary: dict[str, object] = {}
    phase_states: dict[str, str] = {}
    failed_tasks: int = 0
    task_summaries: list[dict[str, object]] = []
    thread_summary: dict[str, object] = {}
    waiting_user: bool = False
    clarification_question: str = ""
    is_sim_mode: bool = False


class TeamChatProgressResponse(BaseModel):
    task_id: str
    exists: bool = False
    state: str = "queued"
    workflow_run_status: str = ""
    continuation_requested: bool = False
    continuation_effective: bool = False
    continuation_block_reason: str = ""
    run_verdict_reconstructed: bool = False
    health_signals: list[str] = []
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
    semantic_gate_applied: bool = False
    semantic_gate_failures: list[str] = []
    evidence_gate_rejected: bool = False
    evidence_gate_failures: list[str] = []
    run_verdict: dict[str, object] = {}
    lead_close_policy: dict[str, object] = {}
    phase_verdicts: dict[str, dict[str, object]] = {}
    phase_contracts: dict[str, dict[str, object]] = {}
    is_sim_mode: bool = False
    last_event: str = ""
    last_event_ts: str = ""
    dynamic_phases_ready: bool = False
    phase_task_ids: dict[str, str] = {}
    phase_evidence_plan: dict[str, dict[str, object]] = {}
    delegate_batches: list[dict[str, object]] = []
    delegate_economics: dict[str, object] = {}
    specialist_reports: list[dict[str, object]] = []
    specialist_report_summary: dict[str, object] = {}
    peer_consultation_summary: dict[str, object] = {}
    task_summaries: list[dict[str, object]] = []
    thread_summary: dict[str, object] = {}
    task_operational_summary: dict[str, object] = {}
    waiting_user: bool = False
    clarification_question: str = ""


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
    thread_provider: str = ""
    thread_channel: str = ""
    thread_model_family: str = ""
    thread_generation: int = 0
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


class NotebookLMSyncRequest(BaseModel):
    title: str = "AI Team Sync"
    source: str = "api"
    content: str = ""
    export_format: str = "markdown"
    days: int = 7
    dry_run: bool = False
    notebook_id: str = ""


class ClarifyRequest(BaseModel):
    chat_id: str
    clarification: str


class FileContent(BaseModel):
    content: str
