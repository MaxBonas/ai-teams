import { useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronRight, LoaderCircle, SendHorizontal, Settings, UserRound, PanelTopOpen, PanelTopClose } from 'lucide-react';
import { apiFetch } from '../lib/api';
import AgentPanel from './AgentPanel';
import type { AgentLaneState } from './AgentLane';
import Modal from './Modal';

interface ChatMessage {
  id: string;
  sender: 'user' | 'team';
  text: string;
  meta?: string;
}

type ChatMode = 'sprint5' | 'classic';
type ChatLevel = 'low' | 'medium' | 'high';

interface StoredChatConfig {
  mode: ChatMode;
  rounds: number;
  complexity: ChatLevel;
  criticality: ChatLevel;
  strictMode: boolean;
  allowLowProductivityOverride: boolean;
}

const TEAM_CHAT_DEFAULTS: StoredChatConfig = {
  mode: 'sprint5',
  rounds: 5,
  complexity: 'medium',
  criticality: 'medium',
  strictMode: true,
  allowLowProductivityOverride: false,
};

const TEAM_CHAT_REMEMBER_KEY = 'aiteam.team_chat.remember_config';
const TEAM_CHAT_WORKSPACE_KEY_PREFIX = 'aiteam.team_chat.config.';

const clampRounds = (value: number): number => Math.max(3, Math.min(value, 80));

const isChatMode = (value: string): value is ChatMode => value === 'sprint5' || value === 'classic';
const isChatLevel = (value: string): value is ChatLevel => value === 'low' || value === 'medium' || value === 'high';

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
    const mode = typeof row.mode === 'string' && isChatMode(row.mode) ? row.mode : TEAM_CHAT_DEFAULTS.mode;
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
    return {
      mode,
      rounds,
      complexity,
      criticality,
      strictMode,
      allowLowProductivityOverride,
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
  round_budget?: number;
  rounds_used?: number;
  phase_count?: number;
  delegated_count?: number;
  continuation_requested?: boolean;
  continuation_of?: string;
  status?: string;
  execution_mode?: string;
  placeholder_outputs?: number;
  successful_check_count?: number;
  live_mode_required?: boolean;
  live_mode_rejected?: boolean;
  ts?: string;
}

interface TeamChatProgress {
  task_id: string;
  exists: boolean;
  state: string;
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
  last_event: string;
  last_event_ts: string;
  dynamic_phases_ready: boolean;
  phase_task_ids: Record<string, string>;
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
  return {
    task_id: taskId,
    exists: Boolean(row.exists),
    state: typeof row.state === 'string' ? row.state : 'queued',
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
    last_event: typeof row.last_event === 'string' ? row.last_event : '',
    last_event_ts: typeof row.last_event_ts === 'string' ? row.last_event_ts : '',
    dynamic_phases_ready: Boolean(row.dynamic_phases_ready),
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

const createClientTaskId = (): string => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `CHAT-${crypto.randomUUID().replace(/-/g, '').slice(0, 8).toUpperCase()}`;
  }
  const fallback = Math.random().toString(16).slice(2, 10).padEnd(8, '0').slice(0, 8);
  return `CHAT-${fallback.toUpperCase()}`;
};

const buildContinuePrompt = (lastRun: LastChatRun): string => {
  const target = (lastRun.task_id || '').trim();
  if (!target) {
    return 'Continue.';
  }
  if (lastRun.status === 'window_exhausted') {
    return `Continue from ${target}. Close pending phases first, then provide a compact final synthesis with done, pending, risks, and next step.`;
  }
  return `Continue from ${target}. Start the next highest-impact slice for the same project objective, and report done, pending, risks, and next step.`;
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
  const provider = field('provider');
  const modelRaw = text.match(/model=([^\s;]+)/)?.[1] ?? '';
  const channel = field('channel');
  const attemptRaw = field('attempts');
  const attempts = attemptRaw.replace(/[\[\]']/g, '');
  const summaryIdx = text.indexOf('output_summary=');
  const outputSummary = summaryIdx >= 0 ? text.slice(summaryIdx + 'output_summary='.length).trim() : '';
  return { rank, assignee, role, consulted, provider, model: modelRaw, channel, attempts, outputSummary };
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

  return (
    <div
      className="team-chat-progress-v2"
      onClick={() => setExpanded(!expanded)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded(!expanded); }}
    >
      <div className="progress-header">
        <strong>{loading ? 'Running' : 'Completed'}</strong>
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
          <div className="team-chat-progress-line">state {progress.state} · execution attempts {progress.execution_attempts} · steps {progress.execution_steps} (ok {progress.execution_steps_success})</div>
          <div className="team-chat-progress-line">checks passed {progress.successful_check_count} · {progress.successful_checks.join(', ') || 'none'}</div>
          <div className="team-chat-progress-line">live mode gate {progress.live_mode_rejected ? 'rejected' : (progress.live_mode_required ? 'required' : 'off')}</div>
          {progress.evidence_gate_rejected && (
            <div className="team-chat-progress-line">evidence gate rejected · {progress.evidence_gate_failures.slice(0, 4).join(' | ') || 'missing evidence'}</div>
          )}
          {!progress.dynamic_phases_ready && progress.state !== 'completed' && loading && (
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

export default function TeamChat({ workspacePath, minimized = false, onToggleMinimize, chatToLoad, onChatLoaded }: TeamChatProps) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatMode, setChatMode] = useState<ChatMode>(TEAM_CHAT_DEFAULTS.mode);
  const [maxRounds, setMaxRounds] = useState(TEAM_CHAT_DEFAULTS.rounds);
  const [complexity, setComplexity] = useState<ChatLevel>(TEAM_CHAT_DEFAULTS.complexity);
  const [criticality, setCriticality] = useState<ChatLevel>(TEAM_CHAT_DEFAULTS.criticality);
  const [strictMode, setStrictMode] = useState<boolean>(TEAM_CHAT_DEFAULTS.strictMode);
  const [allowLowProductivityOverride, setAllowLowProductivityOverride] = useState<boolean>(
    TEAM_CHAT_DEFAULTS.allowLowProductivityOverride,
  );
  const [rememberConfig, setRememberConfig] = useState<boolean>(readRememberConfig);
  const [lastChatRun, setLastChatRun] = useState<LastChatRun | null>(null);
  const [chatProgress, setChatProgress] = useState<TeamChatProgress | null>(null);
  const [showConfig, setShowConfig] = useState<boolean>(readShowConfig);
  const [roundsInput, setRoundsInput] = useState<string>(String(TEAM_CHAT_DEFAULTS.rounds));
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamingTaskId, setStreamingTaskId] = useState<string>('');
  const [agentLanes, setAgentLanes] = useState<Map<string, AgentLaneState>>(new Map());
  const [expandedMessage, setExpandedMessage] = useState<ChatMessage | null>(null);

  const logRef = useRef<HTMLDivElement>(null);

  // Cargar chat histórico cuando se selecciona desde el panel de estado
  useEffect(() => {
    if (!chatToLoad || !workspacePath) return;
    const taskId = chatToLoad;
    apiFetch(`/api/aiteam/chat/load/${encodeURIComponent(taskId)}`, {
      headers: { 'x-workspace-path': workspacePath },
    })
      .then(r => r.json())
      .then((data: unknown) => {
        const d = data as { messages?: Array<{ sender: string; text: string }> };
        if (!d.messages?.length) return;
        setMessages(
          d.messages.map((m, i) => ({
            id: `history-${taskId}-${i}`,
            sender: m.sender as 'user' | 'team',
            text: m.text,
          }))
        );
        setStreamingText(null);
        setAgentLanes(new Map());
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
      setMaxRounds(TEAM_CHAT_DEFAULTS.rounds);
      setRoundsInput(String(TEAM_CHAT_DEFAULTS.rounds));
      setComplexity(TEAM_CHAT_DEFAULTS.complexity);
      setCriticality(TEAM_CHAT_DEFAULTS.criticality);
      setStrictMode(TEAM_CHAT_DEFAULTS.strictMode);
      setAllowLowProductivityOverride(TEAM_CHAT_DEFAULTS.allowLowProductivityOverride);
      return;
    }
    const stored = readWorkspaceConfig(workspacePath);
    if (!stored) {
      setChatMode(TEAM_CHAT_DEFAULTS.mode);
      setMaxRounds(TEAM_CHAT_DEFAULTS.rounds);
      setRoundsInput(String(TEAM_CHAT_DEFAULTS.rounds));
      setComplexity(TEAM_CHAT_DEFAULTS.complexity);
      setCriticality(TEAM_CHAT_DEFAULTS.criticality);
      setStrictMode(TEAM_CHAT_DEFAULTS.strictMode);
      setAllowLowProductivityOverride(TEAM_CHAT_DEFAULTS.allowLowProductivityOverride);
      return;
    }
    setChatMode(stored.mode);
    setMaxRounds(stored.rounds);
    setRoundsInput(String(stored.rounds));
    setComplexity(stored.complexity);
    setCriticality(stored.criticality);
    setStrictMode(stored.strictMode);
    setAllowLowProductivityOverride(stored.allowLowProductivityOverride);
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
          setLastChatRun(candidate as LastChatRun);
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
      rounds: maxRounds,
      complexity,
      criticality,
      strictMode,
      allowLowProductivityOverride,
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
    maxRounds,
    complexity,
    criticality,
    strictMode,
    allowLowProductivityOverride,
  ]);

  // Auto-scroll al final cuando llegan mensajes o streaming
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [messages, streamingText, agentLanes]);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);
  const currentExecutionMode = chatProgress?.execution_mode || lastChatRun?.execution_mode || 'unknown';
  const sendMessage = async (overrideMessage?: string) => {
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
    let progressIntervalId: ReturnType<typeof window.setInterval> | null = null;
    const pollProgress = async () => {
      try {
        const progressResponse = await apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(clientTaskId)}`, {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (!progressResponse.ok) return;
        const progressPayload = await progressResponse.json();
        const parsed = parseChatProgress(progressPayload, clientTaskId);
        if (parsed) {
          setChatProgress(parsed);
        }
      } catch {
        // ignore transient polling errors
      }
    };

    setChatProgress({
      task_id: clientTaskId,
      exists: false,
      state: 'queued',
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
    });
    void pollProgress();
    progressIntervalId = window.setInterval(() => {
      void pollProgress();
    }, 900);

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
          max_rounds: maxRounds,
          client_task_id: clientTaskId,
          strict_mode: strictMode,
          allow_low_productivity_override: allowLowProductivityOverride,
        }),
      });
      if (!response.ok) {
        const errorText = await response.text().catch(() => `HTTP ${response.status}`);
        throw new Error(errorText);
      }

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
                        status: 'active',
                        outputText: '',
                        thinkingText: '',
                        preview: '',
                        durationMs: 0,
                        startedAt: Date.now(),
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
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_completed') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; preview?: string; duration_ms?: number;
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
                      });
                      return next;
                    });
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'agent_failed') {
                try {
                  const ev = JSON.parse(rawData) as {
                    task_id?: string; error?: string;
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
                      });
                      return next;
                    });
                  }
                } catch { /* ignore */ }
                currentEventType = '';
              } else if (currentEventType === 'result') {
                setAgentLanes(new Map()); // limpiar lanes al terminar
                setStreamingText(null);
                setStreamingTaskId('');
                try {
                  const json = JSON.parse(rawData) as Record<string, unknown>;
                  const modeUsed = typeof json.chat_mode === 'string' ? json.chat_mode : chatMode;
                  const roundBudget = Number.isFinite(Number(json.round_budget)) ? Number(json.round_budget) : maxRounds;
                  const roundsUsed = Number.isFinite(Number(json.rounds_used)) ? Number(json.rounds_used) : 0;
                  const completedTasks = Number.isFinite(Number(json.completed_tasks)) ? Number(json.completed_tasks) : 0;
                  const pendingTasks = Number.isFinite(Number(json.pending_tasks)) ? Number(json.pending_tasks) : 0;
                  const artifactCreated = Number.isFinite(Number(json.artifact_created)) ? Number(json.artifact_created) : 0;
                  const artifactModified = Number.isFinite(Number(json.artifact_modified)) ? Number(json.artifact_modified) : 0;
                  const productivityScore = Number.isFinite(Number(json.productivity_score)) ? Number(json.productivity_score) : 0;
                  const reasoningScore = Number.isFinite(Number(json.reasoning_score)) ? Number(json.reasoning_score) : 0;
                  const productivityStatus = typeof json.productivity_status === 'string' ? json.productivity_status : '-';
                  const strictModeApplied = Boolean(json.strict_mode_applied);
                  const autoExtendedRounds = Number.isFinite(Number(json.auto_extended_rounds)) ? Number(json.auto_extended_rounds) : 0;
                  const lowGateRejected = Boolean(json.low_productivity_rejected);
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
                  const statusMeta = `mode ${modeUsed} · exec ${executionMode} · live-gate ${liveModeRejected ? 'rejected' : (liveModeRequired ? 'required' : 'off')} · checks ${checkList.join(',') || 'none'} · evidence ${evidenceMeta} · rounds ${roundsUsed}/${roundBudget} (+${autoExtendedRounds}) · done ${completedTasks} · pending ${pendingTasks} · delegated ${(Array.isArray(json.delegated_task_ids) ? json.delegated_task_ids : []).length} · artifacts +${artifactCreated}/~${artifactModified} · quality P${productivityScore}/R${reasoningScore} (${productivityStatus}) · strict ${strictModeApplied ? 'blocked_close' : (strictMode ? 'on' : 'off')} · low-gate ${lowGateRejected ? `rejected(<${productivityThreshold})` : (allowLowProductivityOverride ? 'override' : 'active')} · state ${String(json.state || '-')} · ${Number(json.elapsed_ms) || 0}ms`;
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
                  const teamMessage: ChatMessage = {
                    id: `team-${Date.now()}`,
                    sender: 'team',
                    text: answer,
                    meta: statusMeta,
                  };
                  setMessages((prev) => [...prev, teamMessage]);

                  const latestRun = typeof json.task_id === 'string' && json.task_id
                    ? {
                      task_id: String(json.task_id),
                      mode: String(json.chat_mode ?? chatMode),
                      round_budget: Number(json.round_budget ?? maxRounds),
                      rounds_used: Number(json.rounds_used ?? 0),
                      phase_count: Object.keys(typeof json.phase_task_ids === 'object' && json.phase_task_ids ? json.phase_task_ids as object : {}).length,
                      delegated_count: Array.isArray(json.delegated_task_ids) ? json.delegated_task_ids.length : 0,
                      continuation_requested: /\bcontinue\b|\bcontinua\b|\bproceed\b|\bgo on\b/i.test(trimmed),
                      continuation_of: typeof json.continuation_of === 'string' ? json.continuation_of : '',
                      status: typeof json.state === 'string' && json.state === 'in_progress' ? 'window_exhausted' : 'completed_or_closed',
                      execution_mode: executionMode,
                      placeholder_outputs: placeholderOutputs,
                      successful_check_count: Number.isFinite(Number(json.successful_check_count)) ? Number(json.successful_check_count) : 0,
                      live_mode_required: liveModeRequired,
                      live_mode_rejected: liveModeRejected,
                      ts: new Date().toISOString(),
                    }
                    : null;
                  if (latestRun) setLastChatRun(latestRun);

                  setChatProgress((prev) => ({
                    task_id: typeof json.task_id === 'string' && json.task_id.trim().length > 0 ? json.task_id : clientTaskId,
                    exists: true,
                    state: typeof json.state === 'string' ? json.state : (prev?.state ?? 'completed'),
                    round_budget: roundBudget,
                    rounds_used: roundsUsed,
                    phase_states: prev?.phase_states ?? {},
                    completed_tasks: completedTasks,
                    pending_tasks: pendingTasks,
                    failed_tasks: prev?.failed_tasks ?? 0,
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
                    phase_task_ids: json.phase_task_ids != null && typeof json.phase_task_ids === 'object' ? (json.phase_task_ids as Record<string, string>) : (prev?.phase_task_ids ?? {}),
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
    } finally {
      setStreamingText(null);
      setStreamingTaskId('');
      if (progressIntervalId !== null) {
        window.clearInterval(progressIntervalId);
      }
      try {
        const finalProgressResponse = await apiFetch(`/api/aiteam/chat/progress/${encodeURIComponent(clientTaskId)}`, {
          headers: {
            'x-workspace-path': workspacePath,
          },
        });
        if (!finalProgressResponse.ok) return;
        const finalProgressPayload = await finalProgressResponse.json();
        const parsed = parseChatProgress(finalProgressPayload, clientTaskId);
        if (parsed) {
          setChatProgress(parsed);
        }
      } catch {
        // keep the latest in-memory progress snapshot
      }
      setLoading(false);
    }
  };

  const MSG_TRUNCATE = 600;

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
              const isLong = !isError && message.text.length > MSG_TRUNCATE;
              const displayText = isLong ? message.text.slice(0, MSG_TRUNCATE) + '…' : message.text;
              return (
                <article key={message.id} className={`team-msg team-msg-${message.sender} ${isError ? 'msg-error' : ''} ${isJustification ? 'msg-justification' : ''}`}>
                  <div className="team-msg-icon">
                    {message.sender === 'user' ? <UserRound size={14} /> : <Bot size={14} />}
                  </div>
                  <div className="team-msg-body">
                    {isJustification ? (
                      <InspectorTrace text={message.text} onExpand={() => setExpandedMessage(message)} />
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

          {streamingText !== null && (() => {
            // Mientras el buffer está vacío, mostrar la fase activa del agente
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
              title={chatMode === 'sprint5' ? 'Sprint: plan + execute highest-impact slice' : 'Classic: legacy phased pipeline'}
            >
              <option value="sprint5">Sprint</option>
              <option value="classic">Classic</option>
            </select>
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
                disabled={loading || !lastChatRun?.task_id}
                onClick={() => {
                  if (!lastChatRun) return;
                  void sendMessage(buildContinuePrompt(lastChatRun));
                }}
              >
                Continue
              </button>
              {lastChatRun?.task_id && (
                <span className="team-chat-last-run-badge" title={`Last: ${lastChatRun.task_id} · ${lastChatRun.rounds_used || 0}/${lastChatRun.round_budget || 0} rounds · ${lastChatRun.status || '-'}`}>
                  {lastChatRun.task_id}
                </span>
              )}
            </div>
            <button className="team-chat-send" disabled={!canSend} onClick={() => void sendMessage()}>
              {loading ? <LoaderCircle size={16} className="spin" /> : <SendHorizontal size={16} />}
              Send
            </button>
          </div>

          {showConfig && (
            <div className="team-chat-config">
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
                <input type="checkbox" checked={rememberConfig} onChange={(e) => setRememberConfig(e.target.checked)} disabled={loading} />
                <span>Remember per workspace</span>
              </label>
            </div>
          )}
        </footer>
      </div>

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
