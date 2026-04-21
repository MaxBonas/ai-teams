import { useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronRight, LoaderCircle, SendHorizontal, Settings, UserRound, PanelTopOpen, PanelTopClose } from 'lucide-react';
import { apiFetch } from '../lib/api';
import AgentPanel from './AgentPanel';
import type { AgentLaneState } from './AgentLane';
import Modal from './Modal';

interface StreamBlock {
  task_id: string;
  title: string;
  role: string;
  text: string;
  complete: boolean;
}

interface ChatMessage {
  id: string;
  sender: 'user' | 'team';
  text: string;
  meta?: string;
  blocks?: StreamBlock[];
}

type ChatMode = 'sprint5' | 'plan' | 'classic';
type RunProfile = 'solo_lead' | 'lead_quorum' | 'ai_team_basic' | 'ai_teams_full' | 'team_advanced';
type ChatLevel = 'low' | 'medium' | 'high';

interface StoredChatConfig {
  mode: ChatMode;
  runProfile: RunProfile;
  rounds: number;
  complexity: ChatLevel;
  criticality: ChatLevel;
  strictMode: boolean;
  allowLowProductivityOverride: boolean;
  autoExtendWeakRuns: boolean;
  repairFirstMode: boolean;
}

const TEAM_CHAT_DEFAULTS: StoredChatConfig = {
  mode: 'sprint5',
  runProfile: 'solo_lead',
  rounds: 10,
  complexity: 'medium',
  criticality: 'low',
  strictMode: false,
  allowLowProductivityOverride: true,
  autoExtendWeakRuns: false,
  repairFirstMode: false,
};

const TEAM_CHAT_REMEMBER_KEY = 'aiteam.team_chat.remember_config';
const TEAM_CHAT_WORKSPACE_KEY_PREFIX = 'aiteam.team_chat.config.';

const clampRounds = (value: number): number => Math.max(3, Math.min(value, 80));

const isChatMode = (value: string): value is ChatMode => value === 'sprint5' || value === 'plan' || value === 'classic';
const isRunProfile = (value: string): value is RunProfile =>
  value === 'solo_lead' || value === 'lead_quorum' || value === 'ai_team_basic' || value === 'ai_teams_full' || value === 'team_advanced';
const isChatLevel = (value: string): value is ChatLevel => value === 'low' || value === 'medium' || value === 'high';

const isLegacyDefaultConfig = (row: Record<string, unknown>): boolean => {
  return (
    row.mode === 'sprint5'
    && Number.parseInt(String(row.rounds ?? 5), 10) === 5
    && row.complexity === 'medium'
    && row.criticality === 'medium'
    && row.strictMode === true
    && row.allowLowProductivityOverride === false
    && !('autoExtendWeakRuns' in row)
  );
};

const readRememberConfig = (): boolean => {
  try {
    const raw = window.localStorage.getItem(TEAM_CHAT_REMEMBER_KEY);
    if (!raw) {
      return true;
    }
    return raw === '1';
  } catch {
    return true;
  }
};

const readWorkspaceConfig = (workspacePath: string): StoredChatConfig | null => {
  try {
    const raw = window.localStorage.getItem(`${TEAM_CHAT_WORKSPACE_KEY_PREFIX}${workspacePath}`);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return null;
    }
    const row = parsed as Record<string, unknown>;
    if (isLegacyDefaultConfig(row)) {
      return { ...TEAM_CHAT_DEFAULTS };
    }
    const mode = typeof row.mode === 'string' && isChatMode(row.mode) ? row.mode : TEAM_CHAT_DEFAULTS.mode;
    const runProfile = typeof row.runProfile === 'string' && isRunProfile(row.runProfile)
      ? row.runProfile
      : TEAM_CHAT_DEFAULTS.runProfile;
    const complexity = typeof row.complexity === 'string' && isChatLevel(row.complexity)
      ? row.complexity
      : TEAM_CHAT_DEFAULTS.complexity;
    const criticality = typeof row.criticality === 'string' && isChatLevel(row.criticality)
      ? row.criticality
      : TEAM_CHAT_DEFAULTS.criticality;
    const rounds = clampRounds(Number.parseInt(String(row.rounds ?? TEAM_CHAT_DEFAULTS.rounds), 10) || TEAM_CHAT_DEFAULTS.rounds);
    const strictMode = typeof row.strictMode === 'boolean' ? row.strictMode : TEAM_CHAT_DEFAULTS.strictMode;
    const allowLowProductivityOverride = typeof row.allowLowProductivityOverride === 'boolean'
      ? row.allowLowProductivityOverride
      : TEAM_CHAT_DEFAULTS.allowLowProductivityOverride;
    const autoExtendWeakRuns = typeof row.autoExtendWeakRuns === 'boolean'
      ? row.autoExtendWeakRuns
      : TEAM_CHAT_DEFAULTS.autoExtendWeakRuns;
    const repairFirstMode = typeof row.repairFirstMode === 'boolean'
      ? row.repairFirstMode
      : TEAM_CHAT_DEFAULTS.repairFirstMode;
    return {
      mode,
      runProfile,
      rounds,
      complexity,
      criticality,
      strictMode,
      allowLowProductivityOverride,
      autoExtendWeakRuns,
      repairFirstMode,
    };
  } catch {
    return null;
  }
};

interface TeamChatProps {
  workspacePath: string;
  minimized?: boolean;
  onToggleMinimize?: () => void;
  chatToLoad?: string | null;
  onChatLoaded?: () => void;
}

interface LastChatRun {
  task_id?: string;
  mode?: string;
  run_profile?: string;
  round_budget?: number;
  rounds_used?: number;
  phase_count?: number;
  delegated_count?: number;
  continuation_requested?: boolean;
  continuation_of?: string;
  continuation_effective?: boolean;
  continuation_block_reason?: string;
  repair_first_mode?: boolean;
  repair_first_required?: boolean;
  repair_first_failures?: string[];
  status?: string;
  workflow_run_status?: string;
  authoritative_state?: string;
  failed_phases?: string[];
  pending_phases?: string[];
  next_action_hint?: string;
  policy_review_required?: boolean;
  execution_mode?: string;
  placeholder_outputs?: number;
  successful_check_count?: number;
  live_mode_required?: boolean;
  live_mode_rejected?: boolean;
  ts?: string;
}

type ContinuationPolicy = 'auto' | 'clean_retry' | 'force_continue';
type ContinueIntent = 'repair_first' | 'close_pending' | 'next_slice';

interface ContinueDraft {
  message: string;
  continuationPolicy: ContinuationPolicy;
  continuationTarget: string;
  intent: ContinueIntent;
  requiresDecision: boolean;
}

interface ContinueDialogState {
  target: string;
  forceContinue: ContinueDraft;
  cleanRetry: ContinueDraft;
}

interface TaskSummary {
  task_id: string;
  short_id: string;
  title: string;
  role: string;
  state: string;
  assignee: string;
  category: string;
  phase: string;
  provider: string;
  model: string;
  channel: string;
  thread_id: string;
  thread_provider: string;
  thread_channel: string;
  thread_model_family: string;
  thread_generation: number;
  preview: string;
  full_text: string;
  error: string;
}

interface ThreadSummary {
  thread_id: string;
  provider: string;
  channel: string;
  model_family: string;
  generation: number;
  rebound_count: number;
  candidate_count: number;
  distinct_thread_count: number;
  providers: string[];
}

interface TeamChatProgress {
  task_id: string;
  exists: boolean;
  state: string;
  workflow_run_status: string;
  continuation_requested?: boolean;
  continuation_of?: string;
  continuation_effective?: boolean;
  continuation_block_reason?: string;
  round_budget: number;
  rounds_used: number;
  phase_states: Record<string, string>;
  completed_tasks: number;
  pending_tasks: number;
  failed_tasks: number;
  execution_attempts: number;
  execution_steps: number;
  execution_steps_success: number;
  execution_mode: string;
  placeholder_outputs: number;
  successful_checks: string[];
  successful_check_count: number;
  live_mode_required: boolean;
  live_mode_rejected: boolean;
  evidence_gate_rejected: boolean;
  evidence_gate_failures: string[];
  is_sim_mode?: boolean;
  last_event: string;
  last_event_ts: string;
  dynamic_phases_ready: boolean;
  phase_task_ids: Record<string, string>;
  peer_consultation_summary: {
    consulted_roles: string[];
    consulted_providers: string[];
    unavailable_roles: string[];
    provider_count: number;
    diversity_observed: boolean;
  };
  task_summaries: TaskSummary[];
  thread_summary: ThreadSummary;
}

const parseNumber = (value: unknown, fallback = 0): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const parseChatProgress = (payload: unknown, fallbackTaskId: string): TeamChatProgress | null => {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const row = payload as Record<string, unknown>;
  const phaseStates: Record<string, string> = {};
  const evidenceFailures: string[] = [];
  const successfulChecks: string[] = [];
  const rawPhaseStates = row.phase_states;
  if (rawPhaseStates && typeof rawPhaseStates === 'object' && !Array.isArray(rawPhaseStates)) {
    for (const [key, value] of Object.entries(rawPhaseStates)) {
      phaseStates[String(key)] = String(value ?? '');
    }
  }
  const rawEvidenceFailures = row.evidence_gate_failures;
  if (Array.isArray(rawEvidenceFailures)) {
    for (const item of rawEvidenceFailures) {
      const value = String(item ?? '').trim();
      if (value) {
        evidenceFailures.push(value);
      }
    }
  }
  const rawSuccessfulChecks = row.successful_checks;
  if (Array.isArray(rawSuccessfulChecks)) {
    for (const item of rawSuccessfulChecks) {
      const value = String(item ?? '').trim();
      if (value) {
        successfulChecks.push(value);
      }
    }
  }
  const taskId = typeof row.task_id === 'string' && row.task_id.trim().length > 0
    ? row.task_id
    : fallbackTaskId;
  const peerSummaryRaw = row.peer_consultation_summary;
  const peerConsultationSummary = {
    consulted_roles: Array.isArray((peerSummaryRaw as Record<string, unknown> | undefined)?.consulted_roles)
      ? (((peerSummaryRaw as Record<string, unknown>).consulted_roles as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0))
      : [],
    consulted_providers: Array.isArray((peerSummaryRaw as Record<string, unknown> | undefined)?.consulted_providers)
      ? (((peerSummaryRaw as Record<string, unknown>).consulted_providers as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0))
      : [],
    unavailable_roles: Array.isArray((peerSummaryRaw as Record<string, unknown> | undefined)?.unavailable_roles)
      ? (((peerSummaryRaw as Record<string, unknown>).unavailable_roles as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0))
      : [],
    provider_count: parseNumber((peerSummaryRaw as Record<string, unknown> | undefined)?.provider_count, 0),
    diversity_observed: Boolean((peerSummaryRaw as Record<string, unknown> | undefined)?.diversity_observed),
  };
  const taskSummaries: TaskSummary[] = Array.isArray(row.task_summaries)
    ? (row.task_summaries as unknown[])
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
        .map((item) => ({
          task_id: String(item.task_id ?? ''),
          short_id: String(item.short_id ?? ''),
          title: String(item.title ?? ''),
          role: String(item.role ?? ''),
          state: String(item.state ?? ''),
          assignee: String(item.assignee ?? ''),
          category: String(item.category ?? ''),
          phase: String(item.phase ?? ''),
          provider: String(item.provider ?? ''),
          model: String(item.model ?? ''),
          channel: String(item.channel ?? ''),
          thread_id: String(item.thread_id ?? ''),
          thread_provider: String(item.thread_provider ?? ''),
          thread_channel: String(item.thread_channel ?? ''),
          thread_model_family: String(item.thread_model_family ?? ''),
          thread_generation: parseNumber(item.thread_generation, 0),
          preview: String(item.preview ?? ''),
          full_text: String(item.full_text ?? ''),
          error: String(item.error ?? ''),
        }))
    : [];
  const rawThreadSummary = row.thread_summary;
  const threadSummary: ThreadSummary = {
    thread_id: typeof (rawThreadSummary as Record<string, unknown> | undefined)?.thread_id === 'string'
      ? String((rawThreadSummary as Record<string, unknown>).thread_id ?? '')
      : '',
    provider: typeof (rawThreadSummary as Record<string, unknown> | undefined)?.provider === 'string'
      ? String((rawThreadSummary as Record<string, unknown>).provider ?? '')
      : '',
    channel: typeof (rawThreadSummary as Record<string, unknown> | undefined)?.channel === 'string'
      ? String((rawThreadSummary as Record<string, unknown>).channel ?? '')
      : '',
    model_family: typeof (rawThreadSummary as Record<string, unknown> | undefined)?.model_family === 'string'
      ? String((rawThreadSummary as Record<string, unknown>).model_family ?? '')
      : '',
    generation: parseNumber((rawThreadSummary as Record<string, unknown> | undefined)?.generation, 0),
    rebound_count: parseNumber((rawThreadSummary as Record<string, unknown> | undefined)?.rebound_count, 0),
    candidate_count: parseNumber((rawThreadSummary as Record<string, unknown> | undefined)?.candidate_count, 0),
    distinct_thread_count: parseNumber((rawThreadSummary as Record<string, unknown> | undefined)?.distinct_thread_count, 0),
    providers: Array.isArray((rawThreadSummary as Record<string, unknown> | undefined)?.providers)
      ? (((rawThreadSummary as Record<string, unknown>).providers as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0))
      : [],
  };
  return {
    task_id: taskId,
    exists: Boolean(row.exists),
    state: typeof row.state === 'string' ? row.state : 'queued',
    workflow_run_status: typeof row.workflow_run_status === 'string' ? row.workflow_run_status : '',
    continuation_requested: Boolean(row.continuation_requested),
    continuation_of: typeof row.continuation_of === 'string' ? row.continuation_of : '',
    continuation_effective: Boolean(row.continuation_effective),
    continuation_block_reason: typeof row.continuation_block_reason === 'string' ? row.continuation_block_reason : '',
    round_budget: parseNumber(row.round_budget, 0),
    rounds_used: parseNumber(row.rounds_used, 0),
    phase_states: phaseStates,
    completed_tasks: parseNumber(row.completed_tasks, 0),
    pending_tasks: parseNumber(row.pending_tasks, 0),
    failed_tasks: parseNumber(row.failed_tasks, 0),
    execution_attempts: parseNumber(row.execution_attempts, 0),
    execution_steps: parseNumber(row.execution_steps, 0),
    execution_steps_success: parseNumber(row.execution_steps_success, 0),
    execution_mode: typeof row.execution_mode === 'string' ? row.execution_mode : 'queued',
    placeholder_outputs: parseNumber(row.placeholder_outputs, 0),
    successful_checks: successfulChecks,
    successful_check_count: parseNumber(row.successful_check_count, successfulChecks.length),
    live_mode_required: Boolean(row.live_mode_required),
    live_mode_rejected: Boolean(row.live_mode_rejected),
    evidence_gate_rejected: Boolean(row.evidence_gate_rejected),
    evidence_gate_failures: evidenceFailures,
    is_sim_mode: row.is_sim_mode === true,
    last_event: typeof row.last_event === 'string' ? row.last_event : '',
    last_event_ts: typeof row.last_event_ts === 'string' ? row.last_event_ts : '',
    dynamic_phases_ready: Boolean(row.dynamic_phases_ready),
    peer_consultation_summary: peerConsultationSummary,
    task_summaries: taskSummaries,
    thread_summary: threadSummary,
    phase_task_ids: (() => {
      const raw = row.phase_task_ids;
      if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
        const out: Record<string, string> = {};
        for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
          out[String(k)] = String(v ?? '');
        }
        return out;
      }
      return {};
    })(),
  };
};

const progressToLastChatRun = (progress: TeamChatProgress | null): LastChatRun | null => {
  if (!progress?.task_id) {
    return null;
  }
  const pendingPhases = Object.entries(progress.phase_states || {})
    .filter(([, state]) => ['pending', 'ready', 'claimed', 'blocked', 'waiting_user'].includes(String(state || '').toLowerCase()))
    .map(([phase]) => phase);
  const failedPhases = Object.entries(progress.phase_states || {})
    .filter(([, state]) => String(state || '').toLowerCase() === 'failed')
    .map(([phase]) => phase);
  const state = resolveChatRunState(progress);
  return {
    task_id: progress.task_id,
    round_budget: progress.round_budget,
    rounds_used: progress.rounds_used,
    phase_count: Object.keys(progress.phase_task_ids || {}).length,
    delegated_count: 0,
    continuation_requested: Boolean(progress.continuation_requested),
    continuation_of: progress.continuation_of || '',
    continuation_effective: Boolean(progress.continuation_effective),
    continuation_block_reason: progress.continuation_block_reason || '',
    authoritative_state: state,
    workflow_run_status: progress.workflow_run_status || state,
    failed_phases: failedPhases,
    pending_phases: pendingPhases,
    status: state,
    execution_mode: progress.execution_mode,
    placeholder_outputs: progress.placeholder_outputs,
    successful_check_count: progress.successful_check_count,
    live_mode_required: progress.live_mode_required,
    live_mode_rejected: progress.live_mode_rejected,
    ts: progress.last_event_ts,
  };
};

const laneStatusFromTaskState = (state: string): AgentLaneState['status'] => {
  if (state === 'completed') return 'completed';
  if (state === 'failed') return 'failed';
  if (state === 'claimed' || state === 'running') return 'active';
  return 'waiting';
};

const isTerminalChatRunState = (state: string): boolean => {
  const normalized = String(state || '').trim().toLowerCase();
  return ['completed', 'failed', 'rejected', 'cancelled', 'aborted', 'not_completed'].includes(normalized);
};

const normalizeVisualRunState = (
  state: unknown,
  options?: {
    pendingTasks?: unknown;
    failedTasks?: unknown;
  },
): string => {
  const normalized = String(state ?? '').trim().toLowerCase();
  const pendingTasks = parseNumber(options?.pendingTasks, 0);
  const failedTasks = parseNumber(options?.failedTasks, 0);
  if (normalized === 'completed' && pendingTasks > 0) {
    return 'running';
  }
  if (normalized === 'completed' && failedTasks > 0) {
    return 'failed';
  }
  return normalized || 'running';
};

const deriveResponseVisualState = (
  payload: Record<string, unknown>,
  options?: {
    pendingTasks?: unknown;
    failedTasks?: unknown;
  },
): string => {
  if (payload.waiting_user === true) return 'waiting_user';
  return normalizeVisualRunState(payload.state, {
    pendingTasks: options?.pendingTasks ?? payload.pending_tasks,
    failedTasks: options?.failedTasks ?? payload.failed_tasks,
  });
};

const resolveChatRunState = (
  candidate?: {
    workflow_run_status?: unknown;
    authoritative_state?: unknown;
    status?: unknown;
    state?: unknown;
    pending_tasks?: unknown;
    failed_tasks?: unknown;
  } | null,
): string => {
  const workflowRunStatus = String(candidate?.workflow_run_status ?? '').trim().toLowerCase();
  const authoritativeState = String(candidate?.authoritative_state ?? '').trim().toLowerCase();
  const statusState = normalizeVisualRunState(candidate?.status ?? candidate?.state, {
    pendingTasks: candidate?.pending_tasks,
    failedTasks: candidate?.failed_tasks,
  });
  if (
    ['running', 'in_progress', 'queued', 'waiting_user'].includes(workflowRunStatus)
    && isTerminalChatRunState(authoritativeState)
  ) {
    return authoritativeState;
  }
  if (
    ['running', 'in_progress', 'queued', 'waiting_user'].includes(workflowRunStatus)
    && isTerminalChatRunState(statusState)
  ) {
    return statusState;
  }
  if (
    (workflowRunStatus === 'running' || workflowRunStatus === 'in_progress')
    && isTerminalChatRunState(statusState)
  ) {
    return statusState;
  }
  if (
    (workflowRunStatus === 'running' || workflowRunStatus === 'in_progress')
    && isTerminalChatRunState(authoritativeState)
  ) {
    return authoritativeState;
  }
  if (workflowRunStatus) return workflowRunStatus;
  if (authoritativeState) return authoritativeState;
  return statusState;
};

const runStateLabel = (state: string): string => {
  const normalized = String(state || '').trim().toLowerCase();
  if (normalized === 'completed') return 'Completed';
  if (normalized === 'failed') return 'Failed';
  if (normalized === 'rejected') return 'Rejected';
  if (normalized === 'not_completed') return 'Not completed';
  if (normalized === 'waiting_user') return 'Waiting';
  if (normalized === 'queued') return 'Queued';
  return 'Running';
};

const nextChatProgressPollDelay = (failureCount: number): number => {
  if (failureCount <= 0) {
    return 1500;
  }
  return Math.min(1500 * Math.pow(2, Math.min(failureCount - 1, 3)), 10000);
};

const buildTaskHistoryLanes = (tasks: TaskSummary[]): Map<string, AgentLaneState> => {
  const next = new Map<string, AgentLaneState>();
  const relevant = tasks.filter((task) => (
    task.state === 'completed'
    || task.state === 'failed'
    || Boolean(task.provider)
    || Boolean(task.model)
    || task.category === 'scout'
  ));
  const baseStartedAt = Date.now();
  relevant.forEach((task, index) => {
    next.set(task.task_id, {
      taskId: task.task_id,
      agentId: task.assignee || task.role || task.short_id,
      role: task.role || 'unknown',
      phase: task.phase || task.short_id || task.title,
      title: task.title || task.short_id || task.task_id,
      provider: task.provider,
      model: task.model,
      channel: task.channel,
      status: laneStatusFromTaskState(task.state),
      outputText: '',
      thinkingText: '',
      preview: task.error || task.preview,
      durationMs: 0,
      startedAt: baseStartedAt - (relevant.length - index) * 1000,
    });
  });
  return next;
};

const mergeAgentLaneMaps = (
  current: Map<string, AgentLaneState>,
  incoming: Map<string, AgentLaneState>,
): Map<string, AgentLaneState> => {
  if (incoming.size === 0) {
    return current;
  }
  const next = new Map(current);
  incoming.forEach((lane, taskId) => {
    const existing = next.get(taskId);
    if (!existing) {
      next.set(taskId, lane);
      return;
    }
    next.set(taskId, {
      ...lane,
      ...existing,
      provider: existing.provider || lane.provider,
      model: existing.model || lane.model,
      channel: existing.channel || lane.channel,
      title: existing.title || lane.title,
      phase: existing.phase || lane.phase,
      agentId: existing.agentId || lane.agentId,
      preview: existing.preview || lane.preview,
      status: existing.status === 'active' ? existing.status : lane.status,
      startedAt: existing.startedAt || lane.startedAt,
    });
  });
  return next;
};

const createClientTaskId = (): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `CHAT-${crypto.randomUUID().replace(/-/g, '').slice(0, 8).toUpperCase()}`;
  }
  const fallback = Math.random().toString(16).slice(2, 10).padEnd(8, '0').slice(0, 8);
  return `CHAT-${fallback.toUpperCase()}`;
};

const buildContinueDraft = (
  lastRun: LastChatRun,
  options?: {
    policy?: ContinuationPolicy;
    intent?: ContinueIntent;
  },
): ContinueDraft => {
  const target = (lastRun.task_id || '').trim();
  const policy = options?.policy ?? 'auto';
  const isSoloLead = (lastRun.run_profile || '').toLowerCase() === 'solo_lead';
  const authoritativeState = resolveChatRunState(lastRun);
  const terminalState = isTerminalChatRunState(authoritativeState);
  const failedOrRejected = authoritativeState === 'failed' || authoritativeState === 'rejected' || authoritativeState === 'not_completed';
  const pendingPhases = Array.isArray(lastRun.pending_phases) ? lastRun.pending_phases.filter((item) => String(item || '').trim().length > 0) : [];
  const failedPhases = Array.isArray(lastRun.failed_phases) ? lastRun.failed_phases.filter((item) => String(item || '').trim().length > 0) : [];
  const repairFirstFailures = Array.isArray(lastRun.repair_first_failures) ? lastRun.repair_first_failures.filter((item) => String(item || '').trim().length > 0) : [];
  const repairFirstRequired = Boolean(lastRun.repair_first_required) || repairFirstFailures.length > 0;
  const hasCarryover = pendingPhases.length > 0 || failedPhases.length > 0 || resolveChatRunState(lastRun) === 'window_exhausted';
  const intent = options?.intent ?? (repairFirstRequired ? 'repair_first' : (hasCarryover ? 'close_pending' : 'next_slice'));

  // solo_lead: prompt directo estilo Codex — inspecciona, valida, repara o avanza
  if (isSoloLead && (policy === 'auto' || policy === 'force_continue')) {
    const actionHint = String(lastRun.next_action_hint || '').trim();
    if (repairFirstRequired) {
      const failures = repairFirstFailures.slice(0, 3).join(', ') || 'material failure';
      return {
        message: `Continue from ${target || 'last run'}. Run the real validation check first. Repair the earliest failure (${failures}) before anything else — do not open a new slice until it passes. No questions, no analysis-only output: fix, validate, report.${actionHint ? ` Hint: ${actionHint}` : ''}`,
        continuationPolicy: 'force_continue',
        continuationTarget: target,
        intent: 'repair_first',
        requiresDecision: false,
      };
    }
    if (failedOrRejected) {
      return {
        message: `Continue from ${target || 'last run'}. Inspect current workspace state, run the real validation check. If broken: repair the earliest failure, validate, then report. If clean: take the next smallest concrete improvement. No questions — decide and act.${actionHint ? ` Hint: ${actionHint}` : ''}`,
        continuationPolicy: 'force_continue',
        continuationTarget: target,
        intent: 'close_pending',
        requiresDecision: false,
      };
    }
    return {
      message: `Continue from ${target || 'last run'}. Validate current workspace first. If clean: take the next smallest concrete slice toward the project objective. If broken: repair before advancing. No questions — inspect, act, validate, report done/pending/next.`,
      continuationPolicy: policy,
      continuationTarget: target,
      intent: 'next_slice',
      requiresDecision: false,
    };
  }

  if (!target) {
    return {
      message: 'Continue.',
      continuationPolicy: policy,
      continuationTarget: '',
      intent,
      requiresDecision: false,
    };
  }

  if (repairFirstRequired && policy !== 'clean_retry') {
    const failures = repairFirstFailures.slice(0, 4).join(', ') || 'material validation failure';
    const actionHint = String(lastRun.next_action_hint || '').trim();
    return {
      message: `Continue from ${target}. Repair-first: fix the earliest material failure before opening a new slice. Failures: ${failures}.${actionHint ? ` Policy hint: ${actionHint}` : ''} Keep the original project objective, use concise specialist briefs, rerun the relevant real check, then provide done, pending, risks, and next step.`,
      continuationPolicy: policy === 'auto' ? 'force_continue' : policy,
      continuationTarget: target,
      intent: 'repair_first',
      requiresDecision: false,
    };
  }

  if (terminalState && policy === 'auto') {
    return {
      message:
        'Continue from the selected run. Choose whether to continue that exact run or start a clean retry before executing.',
      continuationPolicy: 'auto',
      continuationTarget: target,
      intent,
      requiresDecision: true,
    };
  }

  if (policy === 'clean_retry') {
    return {
      message:
        'Start the next highest-impact slice for the same project objective. Treat this as a clean retry from the current validated project state, preserve project constraints, and report done, pending, risks, and next step.',
      continuationPolicy: 'clean_retry',
      continuationTarget: '',
      intent: 'next_slice',
      requiresDecision: false,
    };
  }

  if (failedOrRejected && policy !== 'force_continue') {
    return {
      message:
        'Continue from the selected run. Close pending phases first, replan minimally from the earliest failed or blocked phase, and do not open a new slice until carryover is resolved. Then provide a compact final synthesis with done, pending, risks, and next step.',
      continuationPolicy: 'auto',
      continuationTarget: target,
      intent: 'close_pending',
      requiresDecision: true,
    };
  }

  if (intent === 'close_pending') {
    return {
      message: `Continue from ${target}. Close pending phases first. Replan minimally from the earliest failed or blocked phase if needed, keep the same project objective, and do not start a new slice until the carryover is resolved. Then provide a compact final synthesis with done, pending, risks, and next step.`,
      continuationPolicy: policy,
      continuationTarget: target,
      intent,
      requiresDecision: false,
    };
  }

  return {
    message: `Continue from ${target}. Start the next highest-impact slice for the same project objective. Preserve the current project constraints and report done, pending, risks, and next step.`,
    continuationPolicy: policy,
    continuationTarget: target,
    intent,
    requiresDecision: false,
  };
};

const TEAM_CHAT_SHOW_CONFIG_KEY = 'aiteam.team_chat.show_config';

const readShowConfig = (): boolean => {
  try {
    return window.localStorage.getItem(TEAM_CHAT_SHOW_CONFIG_KEY) === '1';
  } catch {
    return false;
  }
};

/** Extract a short summary from the raw meta string for collapsed view. */
function parseRunMeta(meta: string) {
  const field = (key: string) => meta.match(new RegExp(`(?:^|·\\s*)${key}\\s+([^·]+)`))?.[1]?.trim() ?? '';
  const state = field('state');
  const mode = field('mode');
  const exec = field('exec');
  const rounds = field('rounds');
  const autoExt = meta.match(/\(\+(\d+)\)/)?.[1] ?? '0';
  const done = field('done').replace(/\D.*/, '');
  const pending = field('pending').replace(/\D.*/, '');
  const delegated = field('delegated').replace(/\D.*/, '');
  const quality = field('quality');
  const qm = quality.match(/P(\d+)\/R(\d+)\s*\((\w+)\)/);
  const evidence = field('evidence');
  const evRejected = evidence.startsWith('rejected');
  const evDetails = (evidence.match(/\(([^)]+)\)/)?.[1] ?? '').replace(/\|/g, ', ');
  const msRaw = meta.match(/(\d+)ms(?:\s*$|·)/)?.[1];
  const elapsedMs = msRaw ? Number(msRaw) : 0;
  const elapsedStr = elapsedMs >= 1000 ? `${(elapsedMs / 1000).toFixed(1)}s` : `${elapsedMs}ms`;
  const stateIcon = state === 'completed' ? '✓' : state === 'failed' || state === 'rejected' ? '✗' : '~';
  const stateColor = state === 'completed' ? 'var(--success, #3fb950)' : state === 'failed' || state === 'rejected' ? 'var(--error, #f85149)' : 'var(--text-secondary)';
  return { state, mode, exec, rounds, autoExt, done, pending, delegated,
    qualityP: qm?.[1] ?? '', qualityR: qm?.[2] ?? '', qualityLabel: qm?.[3] ?? quality,
    evRejected, evDetails, stateIcon, stateColor, elapsedStr };
}

function parseDecision(text: string) {
  const field = (key: string) => text.match(new RegExp(`${key}=([^;]+)`))?.[1]?.trim() ?? '';
  const rank = field('decision_rank');
  const assignee = field('assignee');
  const role = field('role');
  const consulted = field('consulted');
  const consultedProviders = field('consulted_providers');
  const provider = field('provider');
  const modelRaw = text.match(/model=([^\s;]+)/)?.[1] ?? '';
  const channel = field('channel');
  const attemptRaw = field('attempts');
  const attempts = attemptRaw.replace(/[\[\]']/g, '');
  const summaryIdx = text.indexOf('output_summary=');
  const outputSummary = summaryIdx >= 0 ? text.slice(summaryIdx + 'output_summary='.length).trim() : '';
  return { rank, assignee, role, consulted, consultedProviders, provider, model: modelRaw, channel, attempts, outputSummary };
}

function MessageMeta({ meta }: { meta: string }) {
  const [expanded, setExpanded] = useState(false);
  const p = parseRunMeta(meta);
  return (
    <div className="run-meta" onClick={() => setExpanded(!expanded)} role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded(!expanded); }}>
      <div className="run-meta-bar">
        <span className="run-meta-state" style={{ color: p.stateColor }}>{p.stateIcon} {p.state}</span>
        <span className="run-meta-pill">{p.mode}</span>
        {p.exec !== 'live' && <span className="run-meta-pill run-meta-pill--dim">{p.exec}</span>}
        <span className="run-meta-pill">R{p.rounds}{p.autoExt !== '0' ? `+${p.autoExt}` : ''}</span>
        {p.qualityP && <span className={`run-meta-pill ${Number(p.qualityP) < 40 ? 'run-meta-pill--warn' : ''}`}>P{p.qualityP}</span>}
        <span className="run-meta-pill run-meta-pill--dim">{p.elapsedStr}</span>
        <ChevronRight size={11} className={`run-meta-chevron ${expanded ? 'is-expanded' : ''}`} />
      </div>
      {expanded && (
        <div className="run-meta-detail">
          <div className="run-meta-row"><span>Fases</span><span>{p.done !== '' ? `✓ ${p.done} ok` : '—'}{p.pending !== '' ? ` · ⏳ ${p.pending} pendiente` : ''}</span></div>
          {p.delegated !== '' && p.delegated !== '0' && <div className="run-meta-row"><span>Delegados</span><span>{p.delegated} tareas</span></div>}
          {p.qualityP && <div className="run-meta-row"><span>Calidad</span><span>P{p.qualityP}/100 · R{p.qualityR}/100 ({p.qualityLabel})</span></div>}
          {p.evRejected && <div className="run-meta-row run-meta-row--warn"><span>Evidencia</span><span>rechazada — {p.evDetails || 'sin detalles'}</span></div>}
          <div className="run-meta-row"><span>Tiempo</span><span>{p.elapsedStr}</span></div>
        </div>
      )}
    </div>
  );
}

function InspectorTrace({ text, onExpand }: { text: string; onExpand: () => void }) {
  const d = parseDecision(text);
  return (
    <div className="inspector-card">
      <div className="inspector-header">
        <span className="inspector-icon">🔍</span>
        <span className="inspector-title">
          {d.rank} · {d.assignee} ({d.role}) · {d.provider}/{d.model}
        </span>
      </div>
      {d.consulted && <div className="inspector-row"><span>Consultó</span><span>{d.consulted}</span></div>}
      {d.consultedProviders && <div className="inspector-row"><span>Providers</span><span>{d.consultedProviders}</span></div>}
      {d.attempts && <div className="inspector-row"><span>Ruta</span><span>{d.attempts}</span></div>}
      {d.outputSummary && (
        <div className="inspector-output">
          <div className="inspector-output-label">Salida</div>
          <div className="inspector-output-text">{d.outputSummary.slice(0, 300)}{d.outputSummary.length > 300 ? '…' : ''}</div>
        </div>
      )}
      <button className="team-msg-expand-btn" onClick={onExpand}>Ver completo →</button>
    </div>
  );
}

function ChatProgressBar({ progress, loading }: { progress: TeamChatProgress; loading: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const total = progress.completed_tasks + progress.pending_tasks + progress.failed_tasks;
  const pct = total > 0 ? Math.round((progress.completed_tasks / total) * 100) : 0;
  const visualState = resolveChatRunState(progress);
  const headerLabel = runStateLabel(visualState);

  return (
    <div
      className="team-chat-progress-v2"
      onClick={() => setExpanded(!expanded)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded(!expanded); }}
    >
      <div className="progress-header">
        <strong>{headerLabel}</strong>
        <span className="progress-task-id">{progress.task_id}</span>
        <ChevronRight size={12} className={`team-msg-meta-chevron ${expanded ? 'is-expanded' : ''}`} />
      </div>
      <div className="progress-bar-track">
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-badges">
        <span className="badge badge-ok">{progress.completed_tasks} done</span>
        <span className="badge badge-pending">{progress.pending_tasks} pending</span>
        {progress.failed_tasks > 0 && <span className="badge badge-fail">{progress.failed_tasks} failed</span>}
        <span className="badge badge-round">R{progress.rounds_used}/{progress.round_budget || 0}</span>
        {progress.execution_mode !== 'queued' && (
          <span className="badge badge-mode">{progress.execution_mode}</span>
        )}
      </div>
      {expanded && (
        <div className="progress-detail">
          <div className="team-chat-progress-line">state {visualState} · workflow {progress.workflow_run_status || '-'} · execution attempts {progress.execution_attempts} · steps {progress.execution_steps} (ok {progress.execution_steps_success})</div>
          <div className="team-chat-progress-line">checks passed {progress.successful_check_count} · {progress.successful_checks.join(', ') || 'none'}</div>
          <div className="team-chat-progress-line">live mode gate {progress.live_mode_rejected ? 'rejected' : (progress.live_mode_required ? 'required' : 'off')}</div>
          {progress.evidence_gate_rejected && (
            <div className="team-chat-progress-line">evidence gate rejected · {progress.evidence_gate_failures.slice(0, 4).join(' | ') || 'missing evidence'}</div>
          )}
          {(progress.peer_consultation_summary.consulted_roles.length > 0 || progress.peer_consultation_summary.consulted_providers.length > 0) && (
            <div className="team-chat-progress-line">
              peers {progress.peer_consultation_summary.consulted_roles.join(', ') || 'none'}
              {' · '}
              providers {progress.peer_consultation_summary.consulted_providers.join(', ') || 'none'}
              {' · '}
              diversity {progress.peer_consultation_summary.diversity_observed ? 'yes' : 'no'}
            </div>
          )}
          {(progress.thread_summary.thread_id || progress.thread_summary.provider || progress.thread_summary.rebound_count > 0) && (
            <div className="team-chat-progress-line">
              thread {[
                progress.thread_summary.provider,
                progress.thread_summary.channel,
                progress.thread_summary.model_family,
              ].filter(Boolean).join('/') || '-'}
              {progress.thread_summary.generation > 0 ? ` · g${progress.thread_summary.generation}` : ''}
              {progress.thread_summary.thread_id ? ` · ${progress.thread_summary.thread_id}` : ''}
              {progress.thread_summary.rebound_count > 0 ? ` · rebounds ${progress.thread_summary.rebound_count}` : ''}
              {progress.thread_summary.distinct_thread_count > 0 ? ` · threads ${progress.thread_summary.distinct_thread_count}` : ''}
            </div>
          )}
          {!progress.dynamic_phases_ready && !isTerminalChatRunState(progress.state) && loading && (
            <div className="team-chat-progress-line planning-indicator">
              Team Lead planificando workflow...
            </div>
          )}
          {Object.keys(progress.phase_states).length > 0 && (
            <div className="team-chat-progress-line">phases {Object.entries(progress.phase_states).slice(0, 10).map(([p, s]) => `${p}:${s}`).join(' · ')}</div>
          )}
          {progress.last_event && <div className="team-chat-progress-line">latest {progress.last_event}</div>}
        </div>
      )}
    </div>
  );
}

function RunTaskSection({
  title,
  rows,
  defaultOpen = false,
}: {
  title: string;
  rows: TaskSummary[];
  defaultOpen?: boolean;
}) {
  if (rows.length === 0) {
    return null;
  }
  return (
    <details className="team-run-details-section" open={defaultOpen}>
      <summary>
        {title} <span>{rows.length}</span>
      </summary>
      <div className="team-run-task-list">
        {rows.map((task) => (
          <article key={task.task_id} className={`team-run-task team-run-task-${task.state || 'unknown'}`}>
            <div className="team-run-task-main">
              <div className="team-run-task-title">
                {task.title || task.short_id || task.task_id}
              </div>
              <div className="team-run-task-meta">
                <span className={`team-run-badge team-run-badge-${task.state || 'unknown'}`}>{task.state || 'unknown'}</span>
                <span>{task.role || '-'}</span>
                {task.assignee && <span>{task.assignee}</span>}
                {(task.provider || task.model) && (
                  <span>{[task.provider, task.model].filter(Boolean).join('/')}</span>
                )}
                {(task.thread_provider || task.thread_generation > 0) && (
                  <span>
                    thread {[
                      task.thread_provider,
                      task.thread_channel,
                      task.thread_model_family,
                    ].filter(Boolean).join('/') || '-'}
                    {task.thread_generation > 0 ? `/g${task.thread_generation}` : ''}
                  </span>
                )}
              </div>
            </div>
            {(task.error || task.preview) && (
              <div className="team-run-task-preview">
                {task.error || task.preview}
              </div>
            )}
          </article>
        ))}
      </div>
    </details>
  );
}

function RunDetailsPanel({ progress }: { progress: TeamChatProgress | null }) {
  const tasks = progress?.task_summaries || [];
  if (!progress || tasks.length === 0) {
    return null;
  }
  const phaseTasks = tasks.filter((task) => task.category === 'phase');
  const scoutTasks = tasks.filter((task) => task.category === 'scout');
  const delegateTasks = tasks.filter((task) => task.category === 'delegate');
  const otherTasks = tasks.filter((task) => !['phase', 'scout', 'delegate'].includes(task.category));
  return (
    <section className="team-run-details">
      <div className="team-run-details-header">
        <strong>Tareas creadas</strong>
        <span>{tasks.length} registradas</span>
      </div>
      <RunTaskSection title="Fases principales" rows={phaseTasks} defaultOpen />
      <RunTaskSection title="Scouts y soporte" rows={scoutTasks} />
      <RunTaskSection title="Delegadas" rows={delegateTasks} />
      <RunTaskSection title="Otras" rows={otherTasks} />
    </section>
  );
}

function StreamBlockCard({
  block,
  expanded,
  onToggle,
  live = false,
}: {
  block: StreamBlock;
  expanded: boolean;
  onToggle: () => void;
  live?: boolean;
}) {
  const preview = block.text.slice(0, 600);
  const hasMore = block.text.length > 600;
  return (
    <div className={`stream-block ${block.complete ? 'stream-block--complete' : 'stream-block--live'}`}>
      <button className="stream-block-header" onClick={onToggle} type="button">
        <span className={`stream-block-role stream-block-role--${block.role || 'agent'}`}>{block.role || 'agent'}</span>
        <span className="stream-block-title">{block.title}</span>
        {!block.complete && live && <span className="team-chat-cursor-blink" style={{ marginLeft: 4 }}>▍</span>}
        <ChevronRight size={11} className={`run-meta-chevron ${expanded ? 'is-expanded' : ''}`} style={{ marginLeft: 'auto', flexShrink: 0 }} />
      </button>
      {expanded ? (
        <div className="stream-block-body">
          <span style={{ whiteSpace: 'pre-wrap' }}>{block.text || '…'}</span>
          {!block.complete && live && <span className="team-chat-cursor-blink">▍</span>}
        </div>
      ) : (
        block.text.length > 0 && (
          <div className="stream-block-preview">
            {preview}{hasMore ? '…' : ''}
          </div>
        )
      )}
    </div>
  );
}

function MessageBlocks({
  blocks,
  expanded,
  onToggle,
}: {
  blocks: StreamBlock[];
  expanded: Record<string, boolean>;
  onToggle: (id: string) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <div className="msg-blocks">
      {blocks.map((block) => (
        <StreamBlockCard
          key={block.task_id}
          block={{ ...block, complete: true }}
          expanded={!!expanded[block.task_id]}
          onToggle={() => onToggle(block.task_id)}
        />
      ))}
    </div>
  );
}

export default function TeamChat({ workspacePath, minimized = false, onToggleMinimize, chatToLoad, onChatLoaded }: TeamChatProps) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [clarificationSubmitting, setClarificationSubmitting] = useState(false);
  const [abortSubmitting, setAbortSubmitting] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatMode, setChatMode] = useState<ChatMode>(TEAM_CHAT_DEFAULTS.mode);
  const [runProfile, setRunProfile] = useState<RunProfile>(TEAM_CHAT_DEFAULTS.runProfile);
  const [maxRounds, setMaxRounds] = useState(TEAM_CHAT_DEFAULTS.rounds);
  const [complexity, setComplexity] = useState<ChatLevel>(TEAM_CHAT_DEFAULTS.complexity);
  const [criticality, setCriticality] = useState<ChatLevel>(TEAM_CHAT_DEFAULTS.criticality);
  const [strictMode, setStrictMode] = useState<boolean>(TEAM_CHAT_DEFAULTS.strictMode);
  const [allowLowProductivityOverride, setAllowLowProductivityOverride] = useState<boolean>(
    TEAM_CHAT_DEFAULTS.allowLowProductivityOverride,
  );
  const [autoExtendWeakRuns, setAutoExtendWeakRuns] = useState<boolean>(TEAM_CHAT_DEFAULTS.autoExtendWeakRuns);
  const [repairFirstMode, setRepairFirstMode] = useState<boolean>(TEAM_CHAT_DEFAULTS.repairFirstMode);
  const [rememberConfig, setRememberConfig] = useState<boolean>(readRememberConfig);
  const [lastChatRun, setLastChatRun] = useState<LastChatRun | null>(null);
  const [continueDialog, setContinueDialog] = useState<ContinueDialogState | null>(null);
  const [chatProgress, setChatProgress] = useState<TeamChatProgress | null>(null);
  const [showConfig, setShowConfig] = useState<boolean>(readShowConfig);
  const [roundsInput, setRoundsInput] = useState<string>(String(TEAM_CHAT_DEFAULTS.rounds));
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamingTaskId, setStreamingTaskId] = useState<string>('');
  const [streamingBlocks, setStreamingBlocks] = useState<StreamBlock[]>([]);
  const [blockExpanded, setBlockExpanded] = useState<Record<string, boolean>>({});
  const [agentLanes, setAgentLanes] = useState<Map<string, AgentLaneState>>(new Map());
  const [expandedMessage, setExpandedMessage] = useState<ChatMessage | null>(null);
  const [pendingClarification, setPendingClarification] = useState<{ chatId: string; question: string } | null>(null);
  const [clarificationInput, setClarificationInput] = useState('');
  const [simMode, setSimMode] = useState<boolean>(false);

  const logRef = useRef<HTMLDivElement>(null);
  const activeRunTaskIdRef = useRef<string>('');

  // Cargar chat histórico cuando se selecciona desde el panel de estado
  useEffect(() => {
    if (!chatToLoad || !workspacePath) return;
    const taskId = chatToLoad;
    Promise.all([
      apiFetch(`/api/aiteam/chat/load/${encodeURIComponent(taskId)}`, {
        headers: { 'x-workspace-path': workspacePath },
      }).then(r => r.json()),
      apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(taskId)}`, {
        headers: { 'x-workspace-path': workspacePath },
      }).then(async (r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([data, progressPayload]) => {
        const d = data as { messages?: Array<{ sender: string; text: string }> };
        if (d.messages?.length) {
          setMessages(
            d.messages.map((m, i) => ({
              id: `history-${taskId}-${i}`,
              sender: m.sender as 'user' | 'team',
              text: m.text,
            }))
          );
        }
        const parsed = parseChatProgress(progressPayload, taskId);
        setStreamingText(null);
        if (parsed) {
          setChatProgress(parsed);
          setAgentLanes(buildTaskHistoryLanes(parsed.task_summaries));
        } else {
          setAgentLanes(new Map());
        }
      })
      .catch(() => { /* ignore */ })
      .finally(() => onChatLoaded?.());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatToLoad]);

  useEffect(() => {
    setInput('');
    setMessages([]);
    setLoading(false);
    setChatProgress(null);
    setAgentLanes(new Map());
    if (!rememberConfig) {
      setChatMode(TEAM_CHAT_DEFAULTS.mode);
      setRunProfile(TEAM_CHAT_DEFAULTS.runProfile);
      setMaxRounds(TEAM_CHAT_DEFAULTS.rounds);
      setRoundsInput(String(TEAM_CHAT_DEFAULTS.rounds));
      setComplexity(TEAM_CHAT_DEFAULTS.complexity);
      setCriticality(TEAM_CHAT_DEFAULTS.criticality);
      setStrictMode(TEAM_CHAT_DEFAULTS.strictMode);
      setAllowLowProductivityOverride(TEAM_CHAT_DEFAULTS.allowLowProductivityOverride);
      setAutoExtendWeakRuns(TEAM_CHAT_DEFAULTS.autoExtendWeakRuns);
      setRepairFirstMode(TEAM_CHAT_DEFAULTS.repairFirstMode);
      return;
    }
    const stored = readWorkspaceConfig(workspacePath);
    if (!stored) {
      setChatMode(TEAM_CHAT_DEFAULTS.mode);
      setRunProfile(TEAM_CHAT_DEFAULTS.runProfile);
      setMaxRounds(TEAM_CHAT_DEFAULTS.rounds);
      setRoundsInput(String(TEAM_CHAT_DEFAULTS.rounds));
      setComplexity(TEAM_CHAT_DEFAULTS.complexity);
      setCriticality(TEAM_CHAT_DEFAULTS.criticality);
      setStrictMode(TEAM_CHAT_DEFAULTS.strictMode);
      setAllowLowProductivityOverride(TEAM_CHAT_DEFAULTS.allowLowProductivityOverride);
      setAutoExtendWeakRuns(TEAM_CHAT_DEFAULTS.autoExtendWeakRuns);
      setRepairFirstMode(TEAM_CHAT_DEFAULTS.repairFirstMode);
      return;
    }
    setChatMode(stored.mode);
    setRunProfile(stored.runProfile);
    setMaxRounds(stored.rounds);
    setRoundsInput(String(stored.rounds));
    setComplexity(stored.complexity);
    setCriticality(stored.criticality);
    setStrictMode(stored.strictMode);
    setAllowLowProductivityOverride(stored.allowLowProductivityOverride);
    setAutoExtendWeakRuns(stored.autoExtendWeakRuns);
    setRepairFirstMode(stored.repairFirstMode);
  }, [workspacePath, rememberConfig]);

  useEffect(() => {
    let cancelled = false;
    const loadLastChatRun = async () => {
      try {
        const response = await apiFetch('/api/aiteam/state?environment=dev', {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (!response.ok) return;
        const payload = await response.json();
        if (cancelled) {
          return;
        }
        const candidate = payload?.last_chat_run;
        if (candidate && typeof candidate === 'object') {
          const candidateRun = candidate as LastChatRun;
          setLastChatRun({
            ...candidateRun,
            status: resolveChatRunState(candidateRun),
          });
          return;
        }
        setLastChatRun(null);
      } catch {
        if (!cancelled) {
          setLastChatRun(null);
        }
      }
    };
    void loadLastChatRun();
    return () => {
      cancelled = true;
    };
  }, [workspacePath]);

  useEffect(() => {
    apiFetch('/api/aiteam/system/mode')
      .then((r) => r.json())
      .then((data: unknown) => {
        if (data && typeof data === 'object' && 'is_sim_mode' in (data as Record<string, unknown>)) {
          setSimMode(Boolean((data as Record<string, unknown>).is_sim_mode));
        }
      })
      .catch(() => {});
  }, [workspacePath]);

  useEffect(() => {
    try {
      window.localStorage.setItem(TEAM_CHAT_REMEMBER_KEY, rememberConfig ? '1' : '0');
    } catch {
      // no-op
    }
  }, [rememberConfig]);

  useEffect(() => {
    try {
      window.localStorage.setItem(TEAM_CHAT_SHOW_CONFIG_KEY, showConfig ? '1' : '0');
    } catch {
      // no-op
    }
  }, [showConfig]);

  useEffect(() => {
    if (!rememberConfig) {
      return;
    }
    const payload: StoredChatConfig = {
      mode: chatMode,
      runProfile,
      rounds: maxRounds,
      complexity,
      criticality,
      strictMode,
      allowLowProductivityOverride,
      autoExtendWeakRuns,
      repairFirstMode,
    };
    try {
      window.localStorage.setItem(
        `${TEAM_CHAT_WORKSPACE_KEY_PREFIX}${workspacePath}`,
        JSON.stringify(payload),
      );
    } catch {
      // no-op
    }
  }, [
    workspacePath,
    rememberConfig,
    chatMode,
    runProfile,
    maxRounds,
    complexity,
    criticality,
    strictMode,
    allowLowProductivityOverride,
    autoExtendWeakRuns,
    repairFirstMode,
  ]);

  // Auto-scroll al final cuando llegan mensajes o streaming
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [messages, streamingText, agentLanes]);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);
  const canSendClarification = useMemo(
    () => clarificationInput.trim().length > 0 && !clarificationSubmitting,
    [clarificationInput, clarificationSubmitting],
  );
  const currentExecutionMode = chatProgress?.execution_mode || lastChatRun?.execution_mode || 'unknown';
  const continueSourceRun = useMemo(
    () => lastChatRun ?? progressToLastChatRun(chatProgress),
    [lastChatRun, chatProgress],
  );
  const activeRunTaskId = (
    activeRunTaskIdRef.current
    || pendingClarification?.chatId
    || chatProgress?.task_id
    || continueSourceRun?.task_id
    || ''
  ).trim();
  const activeRunVisualState = resolveChatRunState(chatProgress ?? lastChatRun ?? null);
  const canAbortRun = activeRunTaskId.length > 0
    && !abortSubmitting
    && !isTerminalChatRunState(activeRunVisualState);
  const continueRunVisualState = resolveChatRunState(continueSourceRun ?? null);
  const canContinueRun = Boolean(continueSourceRun?.task_id)
    && !loading
    && isTerminalChatRunState(continueRunVisualState);

  const parseClarifyResult = (raw: string): Record<string, unknown> => {
    let currentEvent = '';
    for (const line of raw.split(/\r?\n/)) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
        continue;
      }
      if (line.startsWith('data: ')) {
        if (currentEvent === 'result') {
          return JSON.parse(line.slice(6)) as Record<string, unknown>;
        }
        if (currentEvent === 'error') {
          const payload = JSON.parse(line.slice(6)) as { error?: string };
          throw new Error(payload.error ?? 'SSE error');
        }
      }
    }
    return {};
  };

  const sendClarification = async () => {
    if (!pendingClarification || !clarificationInput.trim() || clarificationSubmitting) return;
    const clarificationRequest = pendingClarification;
    const { chatId } = clarificationRequest;
    const answerText = clarificationInput.trim();
    let resumedTaskId = chatId;
    activeRunTaskIdRef.current = chatId;
    setMessages((prev) => [
      ...prev,
      { id: `user-clarify-${Date.now()}`, sender: 'user', text: answerText, meta: 'clarification' },
    ]);
    setPendingClarification(null);
    setClarificationInput('');
    setClarificationSubmitting(true);
    try {
      const res = await apiFetch('/api/aiteam/chat/clarify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-workspace-path': workspacePath },
        body: JSON.stringify({ chat_id: chatId, clarification: answerText }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err || `HTTP ${res.status}`);
      }
      const contentType = res.headers.get('content-type') || '';
      const json = contentType.includes('text/event-stream')
        ? parseClarifyResult(await res.text())
        : await res.json() as Record<string, unknown>;
      const visualState = deriveResponseVisualState(json, {
        pendingTasks: json.pending_tasks,
        failedTasks: json.failed_tasks,
      });
      resumedTaskId = typeof json.task_id === 'string' && json.task_id.trim()
        ? String(json.task_id)
        : chatId;
      activeRunTaskIdRef.current = resumedTaskId;
      const answer = typeof json.response === 'string' && json.response.trim()
        ? json.response
        : 'Respuesta del equipo recibida.';
      setLastChatRun({
        task_id: resumedTaskId,
        mode: typeof json.chat_mode === 'string' ? json.chat_mode : chatMode,
        run_profile: typeof json.run_profile === 'string' ? json.run_profile : runProfile,
        round_budget: Number.isFinite(Number(json.round_budget)) ? Number(json.round_budget) : maxRounds,
        rounds_used: Number.isFinite(Number(json.rounds_used)) ? Number(json.rounds_used) : 0,
        phase_count: Object.keys(typeof json.phase_task_ids === 'object' && json.phase_task_ids ? json.phase_task_ids as object : {}).length,
        delegated_count: Array.isArray(json.delegated_task_ids) ? json.delegated_task_ids.length : 0,
        continuation_requested: Boolean(json.continuation_requested),
        continuation_of: typeof json.continuation_of === 'string' ? json.continuation_of : '',
        continuation_effective: Boolean(json.continuation_effective),
        continuation_block_reason: typeof json.continuation_block_reason === 'string' ? json.continuation_block_reason : '',
        repair_first_mode: Boolean(json.repair_first_mode),
        repair_first_required: Boolean((json.run_verdict as Record<string, unknown> | undefined)?.repair_first_required),
        repair_first_failures: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.repair_first_failures)
          ? (((json.run_verdict as Record<string, unknown>).repair_first_failures as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
          : [],
        authoritative_state: visualState,
        workflow_run_status: typeof json.workflow_run_status === 'string'
          ? json.workflow_run_status
          : visualState,
        failed_phases: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.failed_phases)
          ? (((json.run_verdict as Record<string, unknown>).failed_phases as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
          : [],
        pending_phases: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.pending_phases)
          ? (((json.run_verdict as Record<string, unknown>).pending_phases as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
          : [],
        next_action_hint: typeof json.next_action_hint === 'string' ? json.next_action_hint : '',
        policy_review_required: Boolean(json.policy_review_required),
        status: resolveChatRunState({
          workflow_run_status: typeof json.workflow_run_status === 'string'
            ? json.workflow_run_status
            : visualState,
          authoritative_state: visualState,
          state: visualState,
        }),
        execution_mode: typeof json.execution_mode === 'string' ? json.execution_mode : 'unknown',
        placeholder_outputs: Number.isFinite(Number(json.placeholder_outputs)) ? Number(json.placeholder_outputs) : 0,
        successful_check_count: Number.isFinite(Number(json.successful_check_count)) ? Number(json.successful_check_count) : 0,
        live_mode_required: Boolean(json.live_mode_required),
        live_mode_rejected: Boolean(json.live_mode_rejected),
        ts: new Date().toISOString(),
      });
      setChatProgress((prev) => ({
        task_id: resumedTaskId,
        exists: true,
        state: visualState || (prev?.state ?? 'waiting_user'),
        workflow_run_status: typeof json.workflow_run_status === 'string'
          ? json.workflow_run_status
          : visualState,
        round_budget: Number.isFinite(Number(json.round_budget)) ? Number(json.round_budget) : (prev?.round_budget ?? maxRounds),
        rounds_used: Number.isFinite(Number(json.rounds_used)) ? Number(json.rounds_used) : (prev?.rounds_used ?? 0),
        phase_states: json.phase_states != null && typeof json.phase_states === 'object'
          ? Object.fromEntries(
              Object.entries(json.phase_states as Record<string, unknown>).map(([key, value]) => [String(key), String(value ?? '')]),
            )
          : (prev?.phase_states ?? {}),
        completed_tasks: Number.isFinite(Number(json.completed_tasks)) ? Number(json.completed_tasks) : (prev?.completed_tasks ?? 0),
        pending_tasks: Number.isFinite(Number(json.pending_tasks)) ? Number(json.pending_tasks) : (prev?.pending_tasks ?? 0),
        failed_tasks: Number.isFinite(Number(json.failed_tasks)) ? Number(json.failed_tasks) : (prev?.failed_tasks ?? 0),
        execution_attempts: Number.isFinite(Number(json.execution_attempts)) ? Number(json.execution_attempts) : (prev?.execution_attempts ?? 0),
        execution_steps: Number.isFinite(Number(json.execution_steps)) ? Number(json.execution_steps) : (prev?.execution_steps ?? 0),
        execution_steps_success: Number.isFinite(Number(json.execution_steps_success)) ? Number(json.execution_steps_success) : (prev?.execution_steps_success ?? 0),
        execution_mode: typeof json.execution_mode === 'string' ? json.execution_mode : (prev?.execution_mode ?? 'queued'),
        placeholder_outputs: Number.isFinite(Number(json.placeholder_outputs)) ? Number(json.placeholder_outputs) : (prev?.placeholder_outputs ?? 0),
        successful_checks: Array.isArray(json.successful_checks)
          ? (json.successful_checks as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
          : (prev?.successful_checks ?? []),
        successful_check_count: Number.isFinite(Number(json.successful_check_count)) ? Number(json.successful_check_count) : (prev?.successful_check_count ?? 0),
        live_mode_required: Boolean(json.live_mode_required),
        live_mode_rejected: Boolean(json.live_mode_rejected),
        evidence_gate_rejected: Boolean(json.evidence_gate_applied),
        evidence_gate_failures: Array.isArray(json.evidence_gate_failures)
          ? (json.evidence_gate_failures as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
          : (prev?.evidence_gate_failures ?? []),
        last_event: json.waiting_user === true ? 'Waiting for clarification' : (typeof json.state === 'string' ? `Run ${json.state}` : (prev?.last_event ?? '')),
        last_event_ts: new Date().toISOString(),
        dynamic_phases_ready: typeof json.dynamic_phases_ready === 'boolean' ? json.dynamic_phases_ready : (prev?.dynamic_phases_ready ?? false),
        peer_consultation_summary: prev?.peer_consultation_summary ?? {
          consulted_roles: [],
          consulted_providers: [],
          unavailable_roles: [],
          provider_count: 0,
          diversity_observed: false,
        },
        phase_task_ids: json.phase_task_ids != null && typeof json.phase_task_ids === 'object' ? (json.phase_task_ids as Record<string, string>) : (prev?.phase_task_ids ?? {}),
        task_summaries: prev?.task_summaries ?? [],
        thread_summary: prev?.thread_summary ?? {
          thread_id: '',
          provider: '',
          channel: '',
          model_family: '',
          generation: 0,
          rebound_count: 0,
          candidate_count: 0,
          distinct_thread_count: 0,
          providers: [],
        },
      }));
      if (json.waiting_user === true && typeof json.clarification_question === 'string') {
        setPendingClarification({
          chatId: resumedTaskId,
          question: json.clarification_question,
        });
        setMessages((prev) => [
          ...prev,
          {
            id: `team-clarify-${Date.now()}`,
            sender: 'team',
            text: `El agente necesita tu respuesta: "${json.clarification_question}"`,
            meta: 'waiting_user',
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { id: `team-${Date.now()}`, sender: 'team', text: answer, meta: `state ${String(json.state ?? '-')}` },
        ]);
      }
    } catch (err) {
      setPendingClarification(clarificationRequest);
      setClarificationInput(answerText);
      setMessages((prev) => [
        ...prev,
        { id: `team-err-${Date.now()}`, sender: 'team', text: `Error al reanudar: ${err instanceof Error ? err.message : String(err)}`, meta: 'error' },
      ]);
    } finally {
      try {
        const finalProgressTaskId = activeRunTaskIdRef.current || resumedTaskId;
        const finalProgressResponse = await apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(finalProgressTaskId)}`, {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (finalProgressResponse.ok) {
          const finalProgressPayload = await finalProgressResponse.json();
          const parsed = parseChatProgress(finalProgressPayload, finalProgressTaskId);
          if (parsed) {
            setChatProgress(parsed);
            setAgentLanes((prev) => mergeAgentLaneMaps(prev, buildTaskHistoryLanes(parsed.task_summaries)));
          }
        }
      } catch {
        // keep the latest in-memory clarify state
      }
      setClarificationSubmitting(false);
    }
  };

  const sendContinue = async (draft: ContinueDraft) => {
    setContinueDialog(null);
    await sendMessage({
      message: draft.message,
      continuationPolicy: draft.continuationPolicy,
      continuationTarget: draft.continuationTarget,
    });
  };

  const abortRun = async () => {
    if (!canAbortRun) return;
    const taskRoot = activeRunTaskId;
    const confirmed = window.confirm(`Abortar la run activa ${taskRoot}?`);
    if (!confirmed) return;
    setAbortSubmitting(true);
    try {
      const res = await apiFetch(`/api/aiteam/chat/${encodeURIComponent(taskRoot)}/cancel`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-workspace-path': workspacePath,
        },
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err || `HTTP ${res.status}`);
      }
      const payload = await res.json() as Record<string, unknown>;
      setLoading(false);
      setClarificationSubmitting(false);
      setPendingClarification(null);
      setClarificationInput('');
      activeRunTaskIdRef.current = taskRoot;
      setMessages((prev) => [
        ...prev,
        {
          id: `team-abort-${Date.now()}`,
          sender: 'team',
          text: `Run abortada: ${taskRoot}${Number(payload.archived_tasks ?? 0) > 0 ? ` · ${Number(payload.archived_tasks)} tareas archivadas` : ''}`,
          meta: 'cancelled',
        },
      ]);
      setLastChatRun((prev) => prev ? {
        ...prev,
        task_id: taskRoot,
        authoritative_state: 'cancelled',
        workflow_run_status: 'cancelled',
        status: 'cancelled',
        policy_review_required: false,
        ts: new Date().toISOString(),
      } : {
        task_id: taskRoot,
        authoritative_state: 'cancelled',
        workflow_run_status: 'cancelled',
        status: 'cancelled',
        policy_review_required: false,
        ts: new Date().toISOString(),
      });
      setChatProgress((prev) => prev ? {
        ...prev,
        task_id: taskRoot,
        state: 'cancelled',
        workflow_run_status: 'cancelled',
        pending_tasks: 0,
        last_event: 'Run cancelled by user',
        last_event_ts: new Date().toISOString(),
      } : {
        task_id: taskRoot,
        exists: true,
        state: 'cancelled',
        workflow_run_status: 'cancelled',
        round_budget: 0,
        rounds_used: 0,
        phase_states: {},
        completed_tasks: 0,
        pending_tasks: 0,
        failed_tasks: 0,
        execution_attempts: 0,
        execution_steps: 0,
        execution_steps_success: 0,
        execution_mode: 'cancelled',
        placeholder_outputs: 0,
        successful_checks: [],
        successful_check_count: 0,
        live_mode_required: false,
        live_mode_rejected: false,
        evidence_gate_rejected: false,
        evidence_gate_failures: [],
        last_event: 'Run cancelled by user',
        last_event_ts: new Date().toISOString(),
        dynamic_phases_ready: false,
        phase_task_ids: {},
        peer_consultation_summary: {
          consulted_roles: [],
          consulted_providers: [],
          unavailable_roles: [],
          provider_count: 0,
          diversity_observed: false,
        },
        task_summaries: [],
        thread_summary: {
          thread_id: '',
          provider: '',
          channel: '',
          model_family: '',
          generation: 0,
          rebound_count: 0,
          candidate_count: 0,
          distinct_thread_count: 0,
          providers: [],
        },
      });
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `team-abort-err-${Date.now()}`,
          sender: 'team',
          text: `Error al abortar la run: ${err instanceof Error ? err.message : String(err)}`,
          meta: 'error',
        },
      ]);
    } finally {
      setAbortSubmitting(false);
    }
  };

  const handleContinueClick = () => {
    if (!continueSourceRun) return;
    const autoDraft = buildContinueDraft(continueSourceRun);
    if (autoDraft.requiresDecision) {
      const target = (continueSourceRun.task_id || '').trim();
      setContinueDialog({
        target,
        forceContinue: buildContinueDraft(continueSourceRun, {
          policy: 'force_continue',
          intent: 'close_pending',
        }),
        cleanRetry: buildContinueDraft(continueSourceRun, {
          policy: 'clean_retry',
          intent: 'next_slice',
        }),
      });
      return;
    }
    void sendContinue(autoDraft);
  };

  const sendMessage = async (
    override?:
      | string
      | {
          message?: string;
          continuationPolicy?: ContinuationPolicy;
          continuationTarget?: string;
        },
  ) => {
    const overrideMessage = typeof override === 'string' ? override : override?.message;
    const continuationPolicy = typeof override === 'object' && override
      ? (override.continuationPolicy ?? 'auto')
      : 'auto';
    const continuationTarget = typeof override === 'object' && override
      ? (override.continuationTarget ?? '')
      : '';
    const trimmed = typeof overrideMessage === 'string' ? overrideMessage.trim() : input.trim();
    if (!trimmed || loading) {
      return;
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text: trimmed,
      meta: 'intake=team_lead',
    };
    setMessages((prev) => [...prev, userMessage]);
    if (typeof overrideMessage !== 'string') {
      setInput('');
    }
    setLoading(true);
    const clientTaskId = createClientTaskId();
    let localBlocks: StreamBlock[] = [];
    setStreamingBlocks([]);
    setBlockExpanded({});
    let progressTimerId: ReturnType<typeof window.setTimeout> | null = null;
    let progressPollingStopped = false;
    let progressPollingInFlight = false;
    let progressFailureCount = 0;
    let acceptedByBackend = false;
    let keepPollingAfterStreamDrop = false;
    let keepPollingUntilTerminal = false;
    let reconnectNoticeShown = false;
    activeRunTaskIdRef.current = clientTaskId;

    const stopProgressPolling = () => {
      progressPollingStopped = true;
      if (progressTimerId !== null) {
        window.clearTimeout(progressTimerId);
        progressTimerId = null;
      }
    };

    const scheduleProgressPoll = (delayMs: number) => {
      if (progressPollingStopped) {
        return;
      }
      if (progressTimerId !== null) {
        window.clearTimeout(progressTimerId);
      }
      progressTimerId = window.setTimeout(() => {
        progressTimerId = null;
        void pollProgress();
      }, delayMs);
    };

    const pollProgress = async () => {
      if (progressPollingStopped || progressPollingInFlight) {
        return;
      }
      if (activeRunTaskIdRef.current && activeRunTaskIdRef.current !== clientTaskId) {
        stopProgressPolling();
        return;
      }
      progressPollingInFlight = true;
      try {
        const progressResponse = await apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(clientTaskId)}`, {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (!progressResponse.ok) {
          throw new Error(`HTTP ${progressResponse.status}`);
        }
        const progressPayload = await progressResponse.json();
        const parsed = parseChatProgress(progressPayload, clientTaskId);
        if (parsed) {
          setChatProgress(parsed);
          setSimMode(parsed.is_sim_mode ?? false);
          progressFailureCount = 0;
          if (isTerminalChatRunState(parsed.state)) {
            stopProgressPolling();
            setLoading(false);
            return;
          }
        }
      } catch {
        progressFailureCount += 1;
        if (acceptedByBackend) {
          const reconnectStamp = new Date().toISOString();
          setChatProgress((prev) => {
            if (!prev) {
              return prev;
            }
            return {
              ...prev,
              last_event: `Backend temporalmente inaccesible. Reintentando en ${Math.round(nextChatProgressPollDelay(progressFailureCount) / 1000)}s...`,
              last_event_ts: reconnectStamp,
            };
          });
        }
      } finally {
        progressPollingInFlight = false;
        if (!progressPollingStopped) {
          scheduleProgressPoll(nextChatProgressPollDelay(progressFailureCount));
        }
      }
    };

    setChatProgress({
      task_id: clientTaskId,
      exists: false,
      state: 'queued',
      workflow_run_status: '',
      round_budget: maxRounds,
      rounds_used: 0,
      phase_states: {},
      completed_tasks: 0,
      pending_tasks: 0,
      failed_tasks: 0,
      execution_attempts: 0,
      execution_steps: 0,
      execution_steps_success: 0,
      execution_mode: 'queued',
      placeholder_outputs: 0,
      successful_checks: [],
      successful_check_count: 0,
      live_mode_required: false,
      live_mode_rejected: false,
      evidence_gate_rejected: false,
      evidence_gate_failures: [],
      last_event: 'Waiting for runtime activity...',
      last_event_ts: '',
      dynamic_phases_ready: false,
      phase_task_ids: {},
      peer_consultation_summary: {
        consulted_roles: [],
        consulted_providers: [],
        unavailable_roles: [],
        provider_count: 0,
        diversity_observed: false,
      },
      task_summaries: [],
      thread_summary: {
        thread_id: '',
        provider: '',
        channel: '',
        model_family: '',
        generation: 0,
        rebound_count: 0,
        candidate_count: 0,
        distinct_thread_count: 0,
        providers: [],
      },
    });
    scheduleProgressPoll(0);

    try {
      const response = await apiFetch('/api/aiteam/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-workspace-path': workspacePath,
        },
        body: JSON.stringify({
          message: trimmed,
          role: 'engineer',
          complexity,
          criticality,
          mode: chatMode,
          run_profile: runProfile,
          max_rounds: maxRounds,
          client_task_id: clientTaskId,
          strict_mode: strictMode,
          auto_extend_weak_runs: autoExtendWeakRuns,
          repair_first_mode: repairFirstMode,
          allow_low_productivity_override: allowLowProductivityOverride,
          continuation_policy: continuationPolicy,
          continuation_target: continuationTarget,
        }),
      });
      if (!response.ok) {
        let backendMessage = `HTTP ${response.status}`;
        try {
          const payload = await response.json() as { detail?: string | { message?: string } };
          if (typeof payload.detail === 'string' && payload.detail.trim()) {
            backendMessage = payload.detail;
          } else if (payload.detail && typeof payload.detail === 'object' && typeof payload.detail.message === 'string' && payload.detail.message.trim()) {
            backendMessage = payload.detail.message;
          }
        } catch {
          const errorText = await response.text().catch(() => `HTTP ${response.status}`);
          if (errorText.trim()) {
            backendMessage = errorText;
          }
        }
        throw new Error(backendMessage);
      }
      acceptedByBackend = true;

      // ── Streaming SSE reader ──────────────────────────────────────
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let sseBuffer = '';
      let currentEventType = '';
      let accumulated = '';

      if (reader) {
        // Show empty streaming bubble
        setStreamingText('');
        setStreamingTaskId(clientTaskId);

        outer: while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          sseBuffer += decoder.decode(value, { stream: true });

          // Process complete SSE lines from buffer
          const lines = sseBuffer.split('\n');
          sseBuffer = lines.pop() ?? '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              const rawData = line.slice(6);
              if (currentEventType === 'keepalive') {
                currentEventType = '';
                continue;
              }
              if (currentEventType === 'token_chunk') {
                try {
                  const parsed = JSON.parse(rawData) as { chunk?: string };
                  const chunk = parsed.chunk ?? '';
                  if (chunk) {
                    accumulated += chunk;
                    setStreamingText(accumulated);
                  }
                } catch { /* ignore malformed chunk */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_started') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; agent_id?: string; role?: string;
                    phase?: string; title?: string;
                  };
                  const tid = ev.task_id ?? '';
                  if (tid) {
                    setAgentLanes(prev => {
                      const next = new Map(prev);
                      next.set(tid, {
                        taskId: tid,
                        agentId: ev.agent_id ?? '',
                        role: ev.role ?? '',
                        phase: ev.phase ?? '',
                        title: ev.title ?? '',
                        provider: '',
                        model: '',
                        channel: '',
                        status: 'active',
                        outputText: '',
                        thinkingText: '',
                        preview: '',
                        durationMs: 0,
                        startedAt: Date.now(),
                      });
                      return next;
                    });
                    const newBlock: StreamBlock = {
                      task_id: tid,
                      title: ev.title || ev.phase || ev.role || tid,
                      role: ev.role ?? '',
                      text: '',
                      complete: false,
                    };
                    localBlocks = [...localBlocks, newBlock];
                    setStreamingBlocks([...localBlocks]);
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_routed') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; provider?: string; model?: string; channel?: string;
                  };
                  const tid = ev.task_id ?? '';
                  if (tid) {
                    setAgentLanes(prev => {
                      const lane = prev.get(tid);
                      if (!lane) return prev;
                      const next = new Map(prev);
                      next.set(tid, {
                        ...lane,
                        provider: ev.provider ?? lane.provider,
                        model: ev.model ?? lane.model,
                        channel: ev.channel ?? lane.channel,
                      });
                      return next;
                    });
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_chunk') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; chunk?: string; chunk_type?: string;
                  };
                  const tid = ev.task_id ?? '';
                  const chunk = ev.chunk ?? '';
                  const chunkType = ev.chunk_type ?? 'output';
                  if (tid && chunk) {
                    setAgentLanes(prev => {
                      const lane = prev.get(tid);
                      if (!lane) return prev;
                      const next = new Map(prev);
                      if (chunkType === 'thinking') {
                        next.set(tid, { ...lane, thinkingText: lane.thinkingText + chunk });
                      } else {
                        next.set(tid, { ...lane, outputText: lane.outputText + chunk });
                      }
                      return next;
                    });
                    if (chunkType !== 'thinking') {
                      localBlocks = localBlocks.map(b =>
                        b.task_id === tid ? { ...b, text: b.text + chunk } : b
                      );
                      setStreamingBlocks([...localBlocks]);
                    }
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_completed') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; preview?: string; full_text?: string; duration_ms?: number;
                    provider?: string; model?: string; channel?: string;
                  };
                  const tid = ev.task_id ?? '';
                  if (tid) {
                    setAgentLanes(prev => {
                      const lane = prev.get(tid);
                      if (!lane) return prev;
                      const next = new Map(prev);
                      next.set(tid, {
                        ...lane,
                        status: 'completed',
                        preview: ev.preview ?? '',
                        durationMs: ev.duration_ms ?? 0,
                        provider: ev.provider ?? lane.provider,
                        model: ev.model ?? lane.model,
                        channel: ev.channel ?? lane.channel,
                      });
                      return next;
                    });
                    // If block has no streamed text yet, seed it with the preview from agent_completed
                    // (sub-tasks like scout/evidence often complete without streaming individual chunks)
                    const preview = ev.preview ?? '';
                    const fullText = ev.full_text ?? preview;
                    localBlocks = localBlocks.map(b =>
                      b.task_id === tid
                        ? { ...b, complete: true, text: b.text || fullText }
                        : b
                    );
                    setStreamingBlocks([...localBlocks]);
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_blocked') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; preview?: string; full_text?: string; duration_ms?: number;
                    provider?: string; model?: string; channel?: string;
                  };
                  const tid = ev.task_id ?? '';
                  if (tid) {
                    setAgentLanes(prev => {
                      const lane = prev.get(tid);
                      if (!lane) return prev;
                      const next = new Map(prev);
                      next.set(tid, {
                        ...lane,
                        status: 'failed',
                        preview: ev.preview ?? lane.preview,
                        durationMs: ev.duration_ms ?? 0,
                        provider: ev.provider ?? lane.provider,
                        model: ev.model ?? lane.model,
                        channel: ev.channel ?? lane.channel,
                      });
                      return next;
                    });
                    const fullText = ev.full_text ?? ev.preview ?? '';
                    localBlocks = localBlocks.map(b =>
                      b.task_id === tid
                        ? { ...b, complete: true, text: b.text || fullText }
                        : b
                    );
                    setStreamingBlocks([...localBlocks]);
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_failed') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; error?: string; full_text?: string;
                    provider?: string; model?: string; channel?: string;
                  };
                  const tid = ev.task_id ?? '';
                  if (tid) {
                    setAgentLanes(prev => {
                      const lane = prev.get(tid);
                      if (!lane) return prev;
                      const next = new Map(prev);
                      next.set(tid, {
                        ...lane,
                        status: 'failed',
                        preview: ev.error ?? '',
                        provider: ev.provider ?? lane.provider,
                        model: ev.model ?? lane.model,
                        channel: ev.channel ?? lane.channel,
                      });
                      return next;
                    });
                    const fullText = ev.full_text ?? ev.error ?? '';
                    localBlocks = localBlocks.map(b =>
                      b.task_id === tid
                        ? { ...b, complete: true, text: b.text || fullText }
                        : b
                    );
                    setStreamingBlocks([...localBlocks]);
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'result') {
                setStreamingText(null);
                setStreamingTaskId('');
                try {
                  const json = JSON.parse(rawData) as Record<string, unknown>;
                  const modeUsed = typeof json.chat_mode === 'string' ? json.chat_mode : chatMode;
                  const profileUsed = typeof json.run_profile === 'string' ? json.run_profile : runProfile;
                  const roundBudget = Number.isFinite(Number(json.round_budget)) ? Number(json.round_budget) : maxRounds;
                  const roundsUsed = Number.isFinite(Number(json.rounds_used)) ? Number(json.rounds_used) : 0;
                  const completedTasks = Number.isFinite(Number(json.completed_tasks)) ? Number(json.completed_tasks) : 0;
                  const pendingTasks = Number.isFinite(Number(json.pending_tasks)) ? Number(json.pending_tasks) : 0;
                  const failedTasks = Number.isFinite(Number(json.failed_tasks)) ? Number(json.failed_tasks) : 0;
                  const visualState = deriveResponseVisualState(json, {
                    pendingTasks: json.pending_tasks,
                    failedTasks: json.failed_tasks,
                  });
                  const artifactCreated = Number.isFinite(Number(json.artifact_created)) ? Number(json.artifact_created) : 0;
                  const artifactModified = Number.isFinite(Number(json.artifact_modified)) ? Number(json.artifact_modified) : 0;
                  const productivityScore = Number.isFinite(Number(json.productivity_score)) ? Number(json.productivity_score) : 0;
                  const reasoningScore = Number.isFinite(Number(json.reasoning_score)) ? Number(json.reasoning_score) : 0;
                  const productivityStatus = typeof json.productivity_status === 'string' ? json.productivity_status : '-';
                  const strictModeApplied = Boolean(json.strict_mode_applied);
                  const autoExtendedRounds = Number.isFinite(Number(json.auto_extended_rounds)) ? Number(json.auto_extended_rounds) : 0;
                  const lowGateRejected = Boolean(json.low_productivity_rejected);
                  const responseRepairFirst = Boolean(json.repair_first_mode);
                  const productivityThreshold = Number.isFinite(Number(json.productivity_threshold)) ? Number(json.productivity_threshold) : 35;
                  const executionMode = typeof json.execution_mode === 'string' ? json.execution_mode : 'unknown';
                  const placeholderOutputs = Number.isFinite(Number(json.placeholder_outputs)) ? Number(json.placeholder_outputs) : 0;
                  const evidenceRejected = Boolean(json.evidence_gate_applied);
                  const liveModeRequired = Boolean(json.live_mode_required);
                  const liveModeRejected = Boolean(json.live_mode_rejected);
                  const evidenceFailures = Array.isArray(json.evidence_gate_failures)
                    ? (json.evidence_gate_failures as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                    : [];
                  const checkList = Array.isArray(json.successful_checks)
                    ? (json.successful_checks as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                    : [];
                  const evidenceMeta = evidenceRejected ? `rejected(${evidenceFailures.slice(0, 2).join('|') || 'fail'})` : 'ok';
                  const statusMeta = `profile ${profileUsed} · mode ${modeUsed} · exec ${executionMode} · live-gate ${liveModeRejected ? 'rejected' : (liveModeRequired ? 'required' : 'off')} · checks ${checkList.join(',') || 'none'} · evidence ${evidenceMeta} · rounds ${roundsUsed}/${roundBudget} (+${autoExtendedRounds}) · done ${completedTasks} · pending ${pendingTasks} · delegated ${(Array.isArray(json.delegated_task_ids) ? json.delegated_task_ids : []).length} · artifacts +${artifactCreated}/~${artifactModified} · quality P${productivityScore}/R${reasoningScore} (${productivityStatus}) · strict ${strictModeApplied ? 'blocked_close' : (strictMode ? 'on' : 'off')} · repair-first ${responseRepairFirst ? 'on' : 'off'} · low-gate ${lowGateRejected ? `rejected(<${productivityThreshold})` : (allowLowProductivityOverride ? 'override' : 'active')} · state ${visualState || '-'} · ${Number(json.elapsed_ms) || 0}ms`;
                  // Preferir el contenido streameado real (accumulated) sobre el summary estructurado.
                  // accumulated contiene el output completo de todas las fases (lead_intake, research, etc.)
                  // json.response es un resumen compacto que puede ser plantilla si lead_close fue bloqueado.
                  const streamedBody = accumulated.trim();
                  let answer: string;
                  if (streamedBody.length > 80) {
                    // Usar el contenido real streameado; extraer solo el footer de metadata de json.response
                    const responseStr = typeof json.response === 'string' ? json.response : '';
                    const dashIdx = responseStr.lastIndexOf('\n---\n');
                    const footer = dashIdx >= 0 ? responseStr.slice(dashIdx) : '';
                    answer = footer ? streamedBody + footer : streamedBody;
                  } else {
                    answer = typeof json.response === 'string' && json.response.trim().length > 0
                      ? json.response
                      : (String(json.error || '') || 'No response content returned by AI Team.');
                  }
                  // ── Pausa conversacional ─────────────────────────────────
                  const finalBlocks = localBlocks.filter(b => b.text.length > 0);
                  if (json.waiting_user === true && typeof json.clarification_question === 'string') {
                    setPendingClarification({
                      chatId: String(json.task_id ?? ''),
                      question: json.clarification_question,
                    });
                    setMessages((prev) => [
                      ...prev,
                      {
                        id: `team-clarify-${Date.now()}`,
                        sender: 'team',
                        text: `El agente necesita tu respuesta: "${json.clarification_question}"`,
                        meta: 'waiting_user',
                        blocks: finalBlocks.length > 0 ? finalBlocks : undefined,
                      },
                    ]);
                  } else {
                    const teamMessage: ChatMessage = {
                      id: `team-${Date.now()}`,
                      sender: 'team',
                      text: answer,
                      meta: statusMeta,
                      blocks: finalBlocks.length > 0 ? finalBlocks : undefined,
                    };
                    setMessages((prev) => [...prev, teamMessage]);
                  }
                  setStreamingBlocks([]);

                  const latestRun = typeof json.task_id === 'string' && json.task_id
                    ? {
                      task_id: String(json.task_id),
                      mode: String(json.chat_mode ?? chatMode),
                      run_profile: String(json.run_profile ?? runProfile),
                      round_budget: Number(json.round_budget ?? maxRounds),
                      rounds_used: Number(json.rounds_used ?? 0),
                      phase_count: Object.keys(typeof json.phase_task_ids === 'object' && json.phase_task_ids ? json.phase_task_ids as object : {}).length,
                      delegated_count: Array.isArray(json.delegated_task_ids) ? json.delegated_task_ids.length : 0,
                      continuation_requested: Boolean(json.continuation_requested),
                      continuation_of: typeof json.continuation_of === 'string' ? json.continuation_of : '',
                      continuation_effective: Boolean(json.continuation_effective),
                      continuation_block_reason: typeof json.continuation_block_reason === 'string' ? json.continuation_block_reason : '',
                      repair_first_mode: Boolean(json.repair_first_mode),
                      repair_first_required: Boolean((json.run_verdict as Record<string, unknown> | undefined)?.repair_first_required),
                      repair_first_failures: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.repair_first_failures)
                        ? (((json.run_verdict as Record<string, unknown>).repair_first_failures as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
                        : [],
                      authoritative_state: visualState,
                      workflow_run_status: typeof json.workflow_run_status === 'string' ? json.workflow_run_status : visualState,
                      failed_phases: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.failed_phases)
                        ? (((json.run_verdict as Record<string, unknown>).failed_phases as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
                        : [],
                      pending_phases: Array.isArray((json.run_verdict as Record<string, unknown> | undefined)?.pending_phases)
                        ? (((json.run_verdict as Record<string, unknown>).pending_phases as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0))
                        : [],
                      next_action_hint: typeof json.next_action_hint === 'string' ? json.next_action_hint : '',
                      policy_review_required: Boolean(json.policy_review_required),
                      status: resolveChatRunState({
                        workflow_run_status: typeof json.workflow_run_status === 'string' ? json.workflow_run_status : visualState,
                        authoritative_state: visualState,
                        state: visualState,
                      }),
                      execution_mode: executionMode,
                      placeholder_outputs: placeholderOutputs,
                      successful_check_count: Number.isFinite(Number(json.successful_check_count)) ? Number(json.successful_check_count) : 0,
                      live_mode_required: liveModeRequired,
                      live_mode_rejected: liveModeRejected,
                      ts: new Date().toISOString(),
                    }
                    : null;
                  if (latestRun) setLastChatRun(latestRun);
                  const resultTaskId = typeof json.task_id === 'string' && json.task_id.trim().length > 0
                    ? json.task_id
                    : clientTaskId;
                  activeRunTaskIdRef.current = resultTaskId;
                  keepPollingUntilTerminal = !isTerminalChatRunState(visualState);

                  setChatProgress((prev) => ({
                    task_id: resultTaskId,
                    exists: true,
                    state: visualState || (prev?.state ?? 'running'),
                    workflow_run_status: typeof json.workflow_run_status === 'string'
                      ? json.workflow_run_status
                      : (visualState || (prev?.workflow_run_status ?? '')),
                    round_budget: roundBudget,
                    rounds_used: roundsUsed,
                    phase_states: json.phase_states != null && typeof json.phase_states === 'object'
                      ? Object.fromEntries(
                          Object.entries(json.phase_states as Record<string, unknown>).map(([key, value]) => [String(key), String(value ?? '')]),
                        )
                      : (prev?.phase_states ?? {}),
                    completed_tasks: completedTasks,
                    pending_tasks: pendingTasks,
                    failed_tasks: failedTasks,
                    execution_attempts: Number.isFinite(Number(json.execution_attempts)) ? Number(json.execution_attempts) : (prev?.execution_attempts ?? 0),
                    execution_steps: Number.isFinite(Number(json.execution_steps)) ? Number(json.execution_steps) : (prev?.execution_steps ?? 0),
                    execution_steps_success: Number.isFinite(Number(json.execution_steps_success)) ? Number(json.execution_steps_success) : (prev?.execution_steps_success ?? 0),
                    execution_mode: typeof json.execution_mode === 'string' ? json.execution_mode : (prev?.execution_mode ?? 'queued'),
                    placeholder_outputs: Number.isFinite(Number(json.placeholder_outputs)) ? Number(json.placeholder_outputs) : (prev?.placeholder_outputs ?? 0),
                    successful_checks: Array.isArray(json.successful_checks)
                      ? (json.successful_checks as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                      : (prev?.successful_checks ?? []),
                    successful_check_count: Number.isFinite(Number(json.successful_check_count)) ? Number(json.successful_check_count) : (prev?.successful_check_count ?? 0),
                    live_mode_required: Boolean(json.live_mode_required),
                    live_mode_rejected: Boolean(json.live_mode_rejected),
                    evidence_gate_rejected: Boolean(json.evidence_gate_applied),
                    evidence_gate_failures: Array.isArray(json.evidence_gate_failures)
                      ? (json.evidence_gate_failures as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                      : (prev?.evidence_gate_failures ?? []),
                    last_event: typeof json.state === 'string' ? `Run ${json.state}` : (prev?.last_event ?? ''),
                    last_event_ts: new Date().toISOString(),
                    dynamic_phases_ready: typeof json.dynamic_phases_ready === 'boolean' ? json.dynamic_phases_ready : (prev?.dynamic_phases_ready ?? false),
                    peer_consultation_summary: json.peer_consultation_summary != null && typeof json.peer_consultation_summary === 'object'
                      ? {
                          consulted_roles: Array.isArray((json.peer_consultation_summary as Record<string, unknown>).consulted_roles)
                            ? ((json.peer_consultation_summary as Record<string, unknown>).consulted_roles as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                            : (prev?.peer_consultation_summary.consulted_roles ?? []),
                          consulted_providers: Array.isArray((json.peer_consultation_summary as Record<string, unknown>).consulted_providers)
                            ? ((json.peer_consultation_summary as Record<string, unknown>).consulted_providers as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                            : (prev?.peer_consultation_summary.consulted_providers ?? []),
                          unavailable_roles: Array.isArray((json.peer_consultation_summary as Record<string, unknown>).unavailable_roles)
                            ? ((json.peer_consultation_summary as Record<string, unknown>).unavailable_roles as unknown[]).map((item) => String(item ?? '')).filter((item) => item.trim().length > 0)
                            : (prev?.peer_consultation_summary.unavailable_roles ?? []),
                          provider_count: Number.isFinite(Number((json.peer_consultation_summary as Record<string, unknown>).provider_count))
                            ? Number((json.peer_consultation_summary as Record<string, unknown>).provider_count)
                            : (prev?.peer_consultation_summary.provider_count ?? 0),
                          diversity_observed: Boolean((json.peer_consultation_summary as Record<string, unknown>).diversity_observed),
                        }
                      : (prev?.peer_consultation_summary ?? {
                          consulted_roles: [],
                          consulted_providers: [],
                          unavailable_roles: [],
                          provider_count: 0,
                          diversity_observed: false,
                        }),
                    phase_task_ids: json.phase_task_ids != null && typeof json.phase_task_ids === 'object' ? (json.phase_task_ids as Record<string, string>) : (prev?.phase_task_ids ?? {}),
                    task_summaries: Array.isArray(json.task_summaries)
                      ? ((json.task_summaries as unknown[])
                          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
                          .map((item) => ({
                            task_id: String(item.task_id ?? ''),
                            short_id: String(item.short_id ?? ''),
                            title: String(item.title ?? ''),
                            role: String(item.role ?? ''),
                            state: String(item.state ?? ''),
                            assignee: String(item.assignee ?? ''),
                            category: String(item.category ?? ''),
                            phase: String(item.phase ?? ''),
                            provider: String(item.provider ?? ''),
                            model: String(item.model ?? ''),
                            channel: String(item.channel ?? ''),
                            thread_id: String(item.thread_id ?? ''),
                            thread_provider: String(item.thread_provider ?? ''),
                            thread_channel: String(item.thread_channel ?? ''),
                            thread_model_family: String(item.thread_model_family ?? ''),
                            thread_generation: parseNumber(item.thread_generation, 0),
                            preview: String(item.preview ?? ''),
                            full_text: String(item.full_text ?? ''),
                            error: String(item.error ?? ''),
                          })))
                      : (prev?.task_summaries ?? []),
                    thread_summary: json.thread_summary != null && typeof json.thread_summary === 'object'
                      ? {
                          thread_id: String((json.thread_summary as Record<string, unknown>).thread_id ?? ''),
                          provider: String((json.thread_summary as Record<string, unknown>).provider ?? ''),
                          channel: String((json.thread_summary as Record<string, unknown>).channel ?? ''),
                          model_family: String((json.thread_summary as Record<string, unknown>).model_family ?? ''),
                          generation: parseNumber((json.thread_summary as Record<string, unknown>).generation, 0),
                          rebound_count: parseNumber((json.thread_summary as Record<string, unknown>).rebound_count, 0),
                          candidate_count: parseNumber((json.thread_summary as Record<string, unknown>).candidate_count, 0),
                          distinct_thread_count: parseNumber((json.thread_summary as Record<string, unknown>).distinct_thread_count, 0),
                          providers: Array.isArray((json.thread_summary as Record<string, unknown>).providers)
                            ? (((json.thread_summary as Record<string, unknown>).providers as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0))
                            : (prev?.thread_summary.providers ?? []),
                        }
                      : (prev?.thread_summary ?? {
                          thread_id: '',
                          provider: '',
                          channel: '',
                          model_family: '',
                          generation: 0,
                          rebound_count: 0,
                          candidate_count: 0,
                          distinct_thread_count: 0,
                          providers: [],
                        }),
                  }));

                  if (json.decision_justification) {
                    setMessages((prev) => [
                      ...prev,
                      {
                        id: `team-just-${Date.now()}`,
                        sender: 'team',
                        text: String(json.decision_justification),
                        meta: 'justification',
                      },
                    ]);
                  }
                } catch { /* error parsing result JSON */ }
                currentEventType = '';
                break outer;
              } else if (currentEventType === 'error') {
                setStreamingText(null);
                setStreamingTaskId('');
                try {
                  const parsed = JSON.parse(rawData) as { error?: string };
                  throw new Error(parsed.error ?? 'SSE error event');
                } catch (parseErr) {
                  throw parseErr instanceof Error ? parseErr : new Error('SSE error');
                }
              }
            }
          }
        }
        reader.releaseLock();
      }
    } catch (error) {
      setStreamingText(null);
      setStreamingTaskId('');
      const err = error instanceof Error ? error.message : 'Unknown network error';
      if (acceptedByBackend) {
        keepPollingAfterStreamDrop = true;
        if (!reconnectNoticeShown) {
          reconnectNoticeShown = true;
          setMessages((prev) => [
            ...prev,
            {
              id: `team-reconnect-${Date.now()}`,
              sender: 'team',
              text: `Conexion temporal con AI Team perdida. Reintentando seguimiento del run: ${err}`,
              meta: 'reconnecting',
            },
          ]);
        }
        setChatProgress((prev) => {
          if (!prev) {
            return null;
          }
          return {
            ...prev,
            last_event: `Stream interrumpido. Reintentando seguimiento del run...`,
            last_event_ts: new Date().toISOString(),
          };
        });
        scheduleProgressPoll(nextChatProgressPollDelay(Math.max(progressFailureCount, 1)));
      } else {
        stopProgressPolling();
        setMessages((prev) => [
          ...prev,
          {
            id: `team-error-${Date.now()}`,
            sender: 'team',
            text: `Failed to reach AI Team backend: ${err}`,
            meta: 'error',
          },
        ]);
        setChatProgress((prev) => {
          if (!prev) {
            return null;
          }
          return {
            ...prev,
            state: 'failed',
            last_event: `Request error: ${err}`,
            last_event_ts: new Date().toISOString(),
          };
        });
      }
    } finally {
      setStreamingText(null);
      setStreamingTaskId('');
      if (!keepPollingAfterStreamDrop) {
        stopProgressPolling();
      }
      let finalParsed: TeamChatProgress | null = null;
      try {
        const finalProgressTaskId = activeRunTaskIdRef.current || clientTaskId;
        const finalProgressResponse = await apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(finalProgressTaskId)}`, {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (!finalProgressResponse.ok) return;
        const finalProgressPayload = await finalProgressResponse.json();
        const parsed = parseChatProgress(finalProgressPayload, finalProgressTaskId);
        if (parsed) {
          finalParsed = parsed;
          setChatProgress(parsed);
          setAgentLanes((prev) => mergeAgentLaneMaps(prev, buildTaskHistoryLanes(parsed.task_summaries)));
        }
      } catch {
        // keep the latest in-memory progress snapshot
      }
      if ((keepPollingAfterStreamDrop || keepPollingUntilTerminal) && (!finalParsed || !isTerminalChatRunState(finalParsed.state))) {
        scheduleProgressPoll(nextChatProgressPollDelay(progressFailureCount));
        return;
      }
      stopProgressPolling();
      setLoading(false);
    }
  };

  const MSG_TRUNCATE = 1600;
  const toggleBlock = (id: string) =>
    setBlockExpanded(prev => ({ ...prev, [id]: !prev[id] }));

  return (
    <section className="team-card">
      <header className="team-card-header">
        <div className="team-card-title">AI Team Chat</div>
        <div className="team-chat-header-actions">
          <div className="team-chat-intake-pill">Lead intake · {workspacePath.split(/[\\/]/).pop() || 'workspace'}</div>
          <div className={`team-execution-badge mode-${currentExecutionMode}`}>
            {currentExecutionMode.toUpperCase()}
          </div>
          {onToggleMinimize && (
            <button
              className="team-viewer-refresh"
              onClick={onToggleMinimize}
              title={minimized ? 'Expand chat pane' : 'Minimize chat pane'}
            >
              {minimized ? <PanelTopOpen size={14} /> : <PanelTopClose size={14} />}
            </button>
          )}
        </div>
      </header>

      <div className="team-chat-body">
        {simMode && (
          <div className="sim-mode-banner">
            <span className="sim-mode-banner-icon">⚠</span>
            <span className="sim-mode-banner-text">
              <strong>Modo simulación activo</strong> — los agentes no producen output real.
              Para resultados reales, configura <code>AITEAM_ENABLE_LIVE_API=1</code> y al menos una API key en <code>.env</code>.
            </span>
          </div>
        )}
        {/* ── Conversation log ─────────────────────── */}
        <div className="team-chat-log" ref={logRef}>
          {messages.length === 0 ? (
            <div className="team-empty-state">
              Your message is always received by a senior Team Lead first. Configure mode and round budget below to control planning depth and delivery window.
            </div>
          ) : (
            messages.map((message) => {
              const isError = message.meta === 'error';
              const isJustification = message.meta === 'justification';
              const hasBlocks = !isError && !isJustification && message.blocks && message.blocks.length > 0;
              const isLong = !isError && !hasBlocks && message.text.length > MSG_TRUNCATE;
              const displayText = isLong ? message.text.slice(0, MSG_TRUNCATE) + '…' : message.text;
              return (
                <article key={message.id} className={`team-msg team-msg-${message.sender} ${isError ? 'msg-error' : ''} ${isJustification ? 'msg-justification' : ''}`}>
                  <div className="team-msg-icon">
                    {message.sender === 'user' ? <UserRound size={14} /> : <Bot size={14} />}
                  </div>
                  <div className="team-msg-body">
                    {isJustification ? (
                      <InspectorTrace text={message.text} onExpand={() => setExpandedMessage(message)} />
                    ) : hasBlocks ? (
                      <MessageBlocks
                        blocks={message.blocks!}
                        expanded={blockExpanded}
                        onToggle={toggleBlock}
                      />
                    ) : (
                      <>
                        <p style={{ whiteSpace: 'pre-wrap' }}>{displayText}</p>
                        {isLong && (
                          <button
                            className="team-msg-expand-btn"
                            onClick={() => setExpandedMessage(message)}
                          >
                            Ver respuesta completa →
                          </button>
                        )}
                      </>
                    )}
                    {message.meta && !isJustification && message.meta !== 'error' && (
                      <MessageMeta meta={message.meta} />
                    )}
                  </div>
                </article>
              );
            })
          )}

          {/* Agent lanes dentro del thread */}
          <AgentPanel lanes={agentLanes} visible={loading || agentLanes.size > 0} />
          <RunDetailsPanel progress={chatProgress} />

          {/* Streaming blocks — one collapsible card per agent, collapsed by default */}
          {streamingBlocks.length > 0 && (
            <div className="streaming-blocks-wrap">
              {streamingBlocks.map((block) => (
                <StreamBlockCard
                  key={block.task_id}
                  block={block}
                  expanded={!!blockExpanded[block.task_id]}
                  onToggle={() => toggleBlock(block.task_id)}
                  live={!block.complete}
                />
              ))}
            </div>
          )}
          {/* Fallback single bubble when no agent_chunk blocks exist */}
          {streamingText !== null && streamingBlocks.length === 0 && (() => {
            const activePhase = streamingText === ''
              ? [...agentLanes.values()].find(l => l.status === 'active')
              : null;
            return (
              <div className="team-chat-message team-chat-message--team team-chat-message--streaming">
                <div className="team-chat-message-content">
                  {streamingText === '' ? (
                    <span className="team-chat-streaming-placeholder">
                      {activePhase ? activePhase.title || activePhase.phase : 'Procesando…'}
                    </span>
                  ) : (
                    <span className="team-chat-streaming-cursor" style={{ whiteSpace: 'pre-wrap' }}>{streamingText}</span>
                  )}
                  <span className="team-chat-cursor-blink">&#x258A;</span>
                </div>
                <div className="team-chat-message-meta">
                  {streamingTaskId ? streamingTaskId.split('::').pop() : 'streaming…'}
                </div>
              </div>
            );
          })()}
        </div>

        {/* ── Composer ─────────────────────────────── */}
        <footer className="team-chat-input-wrap">
          {chatProgress && (
            <ChatProgressBar progress={chatProgress} loading={loading} />
          )}

          <div className="team-chat-input-row">
            <select
              className="team-mode-select"
              value={chatMode}
              onChange={(e) => setChatMode(e.target.value as ChatMode)}
              disabled={loading}
              title={
                chatMode === 'plan'
                  ? 'Plan: Team Lead plans only; no execution or product deliverables'
                  : (chatMode === 'sprint5' ? 'Sprint: plan + execute highest-impact slice' : 'Classic: legacy phased pipeline')
              }
            >
              <option value="sprint5">Sprint</option>
              <option value="plan">Plan</option>
              <option value="classic">Classic</option>
            </select>
            {pendingClarification ? (
              <div className="team-chat-clarify-box">
                <div className="team-chat-clarify-label">
                  El agente pregunta: <strong>{pendingClarification.question}</strong>
                </div>
                <textarea
                  className="team-chat-input"
                  rows={2}
                  value={clarificationInput}
                  onChange={(e) => setClarificationInput(e.target.value)}
                  placeholder="Tu respuesta..."
                  disabled={clarificationSubmitting}
                  autoFocus
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                      e.preventDefault();
                      void sendClarification();
                    }
                  }}
                />
              </div>
            ) : (
              <textarea
                className="team-chat-input"
                rows={3}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Describe the coding task or question..."
                onKeyDown={(e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    void sendMessage();
                  }
                }}
              />
            )}
          </div>

          <div className="team-chat-actions">
            <div className="team-chat-actions-left">
              <button
                className="team-chat-settings-toggle"
                onClick={() => setShowConfig(!showConfig)}
                title="Toggle advanced settings"
              >
                <Settings size={14} />
                {showConfig ? 'Hide' : 'Settings'}
              </button>
              <button
                className="team-chat-continue"
                disabled={!canContinueRun}
                onClick={handleContinueClick}
                title={canContinueRun ? 'Continue a terminal run' : 'Wait for the active run to finish before continuing'}
              >
                Continue
              </button>
              <button
                className="team-chat-abort"
                disabled={!canAbortRun}
                onClick={() => void abortRun()}
              >
                {abortSubmitting ? <LoaderCircle size={14} className="spin" /> : null}
                Abort
              </button>
              {continueSourceRun?.task_id && (
                <span className="team-chat-last-run-badge" title={`Last: ${continueSourceRun.task_id} · ${continueSourceRun.rounds_used || 0}/${continueSourceRun.round_budget || 0} rounds · ${continueSourceRun.status || '-'}`}>
                  {continueSourceRun.task_id}
                </span>
              )}
            </div>
            <button
              className="team-chat-send"
              disabled={pendingClarification ? !canSendClarification : !canSend}
              onClick={() => pendingClarification ? void sendClarification() : void sendMessage()}
            >
              {(pendingClarification ? clarificationSubmitting : loading)
                ? <LoaderCircle size={16} className="spin" />
                : <SendHorizontal size={16} />}
              Send
            </button>
          </div>

          {showConfig && (
            <div className="team-chat-config">
              <div className="team-chat-config-row">
                <label className="team-chat-config-item">
                  <span>Run profile</span>
                  <select
                    className="team-role-select"
                    value={runProfile}
                    onChange={(e) => setRunProfile(e.target.value as RunProfile)}
                    disabled={loading}
                  >
                    <option value="solo_lead">Solo Lead</option>
                    <option value="lead_quorum">Lead + Quorum</option>
                    <option value="ai_team_basic">AI Team Basic</option>
                    <option value="ai_teams_full">AI Teams Full</option>
                    <option value="team_advanced">Team Advanced</option>
                  </select>
                </label>
              </div>
              <div className="team-chat-config-row">
                <label className="team-chat-config-item">
                  <span>Rounds</span>
                  <input
                    className="team-role-select"
                    type="text"
                    inputMode="numeric"
                    value={roundsInput}
                    onChange={(e) => setRoundsInput(e.target.value)}
                    onBlur={() => {
                      const parsed = Number.parseInt(roundsInput, 10);
                      const clamped = Number.isNaN(parsed) ? TEAM_CHAT_DEFAULTS.rounds : clampRounds(parsed);
                      setMaxRounds(clamped);
                      setRoundsInput(String(clamped));
                    }}
                    disabled={loading}
                  />
                </label>
                <label className="team-chat-config-item">
                  <span>Complexity</span>
                  <select
                    className="team-role-select"
                    value={complexity}
                    onChange={(e) => setComplexity(e.target.value as ChatLevel)}
                    disabled={loading}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </label>
                <label className="team-chat-config-item">
                  <span>Criticality</span>
                  <select
                    className="team-role-select"
                    value={criticality}
                    onChange={(e) => setCriticality(e.target.value as ChatLevel)}
                    disabled={loading}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </label>
              </div>
              <label className="team-chat-remember-row">
                <input type="checkbox" checked={strictMode} onChange={(e) => setStrictMode(e.target.checked)} disabled={loading} />
                <span>Strict evidence mode</span>
              </label>
              <label className="team-chat-remember-row">
                <input type="checkbox" checked={allowLowProductivityOverride} onChange={(e) => setAllowLowProductivityOverride(e.target.checked)} disabled={loading} />
                <span>Allow close below threshold</span>
              </label>
              <label className="team-chat-remember-row">
                <input type="checkbox" checked={autoExtendWeakRuns} onChange={(e) => setAutoExtendWeakRuns(e.target.checked)} disabled={loading} />
                <span>Auto-extend weak runs</span>
              </label>
              <label className="team-chat-remember-row">
                <input type="checkbox" checked={repairFirstMode} onChange={(e) => setRepairFirstMode(e.target.checked)} disabled={loading} />
                <span>Repair-first mode</span>
              </label>
              <label className="team-chat-remember-row">
                <input type="checkbox" checked={rememberConfig} onChange={(e) => setRememberConfig(e.target.checked)} disabled={loading} />
                <span>Remember per workspace</span>
              </label>
            </div>
          )}
        </footer>
      </div>

      {continueDialog && (
        <Modal title="Continue this run" onClose={() => setContinueDialog(null)}>
          <div className="team-chat-continue-modal">
            <p>
              The selected run <strong>{continueDialog.target}</strong> is terminal.
              Choose whether to force continuation of that exact run or start a clean retry from the same project objective.
            </p>
            <div className="team-chat-continue-modal-actions">
              <button
                className="team-chat-send"
                type="button"
                onClick={() => { void sendContinue(continueDialog.forceContinue); }}
              >
                Force continue
              </button>
              <button
                className="team-chat-continue"
                type="button"
                onClick={() => { void sendContinue(continueDialog.cleanRetry); }}
              >
                Clean retry
              </button>
            </div>
            <div className="team-chat-continue-modal-note">
              <strong>Force continue:</strong> close pending phases first on the same run.
              <br />
              <strong>Clean retry:</strong> start a fresh run from current validated project state.
            </div>
          </div>
        </Modal>
      )}

      {/* ── Modal respuesta completa ──────────────── */}
      {expandedMessage && (
        <Modal title={expandedMessage.meta === 'justification' ? 'Inspector Decision Trace' : 'Respuesta completa'} onClose={() => setExpandedMessage(null)} wide>
          {expandedMessage.meta === 'justification' ? (() => {
            const d = parseDecision(expandedMessage.text);
            return (
              <div className="inspector-modal">
                <div className="inspector-modal-grid">
                  <span>Rol</span><span>{d.role} ({d.rank})</span>
                  <span>Agente</span><span>{d.assignee}</span>
                  <span>Consultó</span><span>{d.consulted || '—'}</span>
                  <span>Providers</span><span>{d.consultedProviders || '—'}</span>
                  <span>Proveedor</span><span>{d.provider} / {d.model}</span>
                  <span>Canal</span><span>{d.channel}</span>
                  <span>Intentos</span><span>{d.attempts || '—'}</span>
                </div>
                {d.outputSummary && (
                  <div className="inspector-modal-output">
                    <div className="inspector-modal-output-label">Salida del agente</div>
                    <pre className="modal-pre">{d.outputSummary}</pre>
                  </div>
                )}
              </div>
            );
          })() : <pre className="modal-pre">{expandedMessage.text}</pre>}
          {expandedMessage.meta && expandedMessage.meta !== 'error' && expandedMessage.meta !== 'justification' && (
            <div className="modal-meta-section">
              <MessageMeta meta={expandedMessage.meta} />
            </div>
          )}
        </Modal>
      )}
    </section>
  );
}
