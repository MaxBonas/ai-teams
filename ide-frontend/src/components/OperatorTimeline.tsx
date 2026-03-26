import { useEffect, useMemo, useState } from 'react';
import { RefreshCcw } from 'lucide-react';

import { apiFetch } from '../lib/api';

type EventCategory = 'all' | 'tasks' | 'quality' | 'decisions' | 'tools' | 'comms';

const EVENT_CATEGORIES: Record<EventCategory, { label: string; types: string[] }> = {
  all: { label: 'All', types: [] },
  tasks: { label: 'Tasks', types: ['task_started', 'task_execution', 'task_completed', 'task_failed', 'placeholder_gate_failed', 'evidence_gate_failed', 'round_sub_iteration', 'round_completed', 'sub_iteration_barrier'] },
  quality: { label: 'Quality', types: ['gate_iteration', 'quality_gates_failed', 'conflict_escalation', 'stall_detected'] },
  decisions: { label: 'Decisions', types: ['decision_recorded', 'decision_rank_escalation', 'peer_dialogue_round2', 'team_decision'] },
  tools: { label: 'Tools', types: ['agent_tool_invocation', 'tool_integration', 'agent_delegation', 'skill_mcp_guidance'] },
  comms: { label: 'Comms', types: ['mail_dm', 'mail_broadcast', 'sync_meeting', 'sync_meeting_skipped', 'agent_handoff', 'conversation_mailbox_consumed', 'conversation_mailbox_reply'] },
};

interface OperatorTimelineItem {
  ts: string;
  event_type: string;
  task_id: string;
  level: string;
  summary: string;
  assignee?: string;
  execution_round?: number;
  execution_sub_iteration?: number;
  gate_iteration?: number;
  blocked_reason?: string;
  handoff_from?: string;
  handoff_to?: string;
  conversation_thread_id?: string;
  meeting_kind?: string;
  artifact_created: number;
  artifact_modified: number;
  artifact_files: string[];
  productivity_score: number;
  reasoning_score: number;
}

interface OperatorTimelineResponse {
  selected_task_id?: string;
  latest_task_id?: string;
  available_runs?: string[];
  total?: number;
  items?: OperatorTimelineItem[];
  progress?: {
    task_id?: string;
    state?: string;
    rounds_used?: number;
    round_budget?: number;
    completed_tasks?: number;
    pending_tasks?: number;
    failed_tasks?: number;
    execution_attempts?: number;
    execution_steps?: number;
    execution_steps_success?: number;
    execution_mode?: string;
    placeholder_outputs?: number;
    successful_checks?: string[];
    successful_check_count?: number;
    live_mode_required?: boolean;
    live_mode_rejected?: boolean;
    evidence_gate_rejected?: boolean;
    evidence_gate_failures?: string[];
    last_event?: string;
  };
}

interface OperatorTimelineProps {
  workspacePath: string;
}

function formatTs(value: string): string {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export default function OperatorTimeline({ workspacePath }: OperatorTimelineProps) {
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [items, setItems] = useState<OperatorTimelineItem[]>([]);
  const [availableRuns, setAvailableRuns] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [keyOnly, setKeyOnly] = useState(true);
  const [eventFilter, setEventFilter] = useState<EventCategory>('all');
  const [progressText, setProgressText] = useState('');
  const [executionMode, setExecutionMode] = useState('unknown');
  const [placeholderOutputs, setPlaceholderOutputs] = useState(0);

  const loadTimeline = async (quiet = false) => {
    if (!quiet) {
      setRefreshing(true);
    }
    try {
      const query = new URLSearchParams();
      if (selectedTaskId.trim()) {
        query.set('task_id', selectedTaskId.trim());
      }
      query.set('limit', '120');
      query.set('key_only', keyOnly ? 'true' : 'false');
      const response = await apiFetch(`/api/aiteam/operator/timeline?${query.toString()}`, {
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = (await response.json()) as OperatorTimelineResponse;

      const nextItems = Array.isArray(json.items) ? json.items : [];
      const nextRuns = Array.isArray(json.available_runs) ? json.available_runs : [];
      const backendSelected = typeof json.selected_task_id === 'string' ? json.selected_task_id : '';

      setItems(nextItems);
      setAvailableRuns(nextRuns);
      setTotal(Number(json.total || 0));
      setError('');

      if (!selectedTaskId && backendSelected) {
        setSelectedTaskId(backendSelected);
      }

      const progress = json.progress;
      if (progress && typeof progress === 'object') {
        const mode = String(progress.execution_mode || 'unknown');
        setExecutionMode(mode);
        setPlaceholderOutputs(Number(progress.placeholder_outputs || 0));
        const evidenceRejected = Boolean(progress.evidence_gate_rejected);
        const evidenceFailures = Array.isArray(progress.evidence_gate_failures)
          ? progress.evidence_gate_failures.map((row) => String(row ?? '')).filter((row) => row.trim().length > 0)
          : [];
        const checks = Array.isArray(progress.successful_checks)
          ? progress.successful_checks.map((row) => String(row ?? '')).filter((row) => row.trim().length > 0)
          : [];
        const liveGate = Boolean(progress.live_mode_rejected)
          ? 'rejected'
          : (Boolean(progress.live_mode_required) ? 'required' : 'off');
        setProgressText(
          `state ${String(progress.state || '-')} · mode ${mode} · placeholder ${Number(progress.placeholder_outputs || 0)} · live-gate ${liveGate} · checks ${Number(progress.successful_check_count || checks.length)} ${checks.join(',') || 'none'} · rounds ${Number(progress.rounds_used || 0)}/${Number(progress.round_budget || 0)} · done ${Number(progress.completed_tasks || 0)} · pending ${Number(progress.pending_tasks || 0)} · failed ${Number(progress.failed_tasks || 0)} · exec ${Number(progress.execution_attempts || 0)}/${Number(progress.execution_steps || 0)} (ok ${Number(progress.execution_steps_success || 0)})${evidenceRejected ? ` · evidence rejected (${evidenceFailures.slice(0, 2).join('|') || 'fail'})` : ''}`,
        );
      } else {
        setExecutionMode('unknown');
        setPlaceholderOutputs(0);
        setProgressText('');
      }
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : 'Unknown request failure';
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setSelectedTaskId('');
    setItems([]);
    setAvailableRuns([]);
    setTotal(0);
    setError('');
    setLoading(true);
  }, [workspacePath]);

  useEffect(() => {
    if (!workspacePath) {
      setLoading(false);
      return;
    }
    void loadTimeline(true);
    const timer = window.setInterval(() => {
      void loadTimeline(true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [workspacePath, selectedTaskId, keyOnly]);

  const filteredItems = useMemo(() => {
    if (eventFilter === 'all') return items;
    const types = EVENT_CATEGORIES[eventFilter].types;
    return items.filter((item) => types.includes(item.event_type));
  }, [items, eventFilter]);

  const hasItems = useMemo(() => filteredItems.length > 0, [filteredItems]);

  return (
    <section className="team-card">
      <header className="team-card-header">
        <div className="team-card-title">Operator Timeline</div>
        <div className={`team-execution-badge mode-${executionMode}`}>
          {executionMode.toUpperCase()} · placeholder {placeholderOutputs}
        </div>
        <button className="team-viewer-refresh" onClick={() => void loadTimeline()} disabled={refreshing}>
          <RefreshCcw size={14} className={refreshing ? 'spin' : ''} />
          {total}
        </button>
      </header>

      <div className="team-stream-body">
        <div className="team-operator-controls">
          <select
            className="team-role-select"
            value={selectedTaskId}
            onChange={(event) => setSelectedTaskId(event.target.value)}
          >
            {availableRuns.length === 0 ? (
              <option value="">No runs</option>
            ) : (
              availableRuns.map((run) => (
                <option key={run} value={run}>{run}</option>
              ))
            )}
          </select>
          <label className="team-chat-remember-row">
            <input
              type="checkbox"
              checked={keyOnly}
              onChange={(event) => setKeyOnly(event.target.checked)}
            />
            <span>Key events only</span>
          </label>
        </div>
        <div className="team-operator-controls" style={{ gap: '4px', flexWrap: 'wrap' }}>
          {(Object.entries(EVENT_CATEGORIES) as [EventCategory, { label: string }][]).map(([key, { label }]) => (
            <button
              key={key}
              className={`ops-hub-tab ${eventFilter === key ? 'is-active' : ''}`}
              onClick={() => setEventFilter(key)}
              style={{ padding: '2px 8px', fontSize: '11px' }}
            >
              {label}
            </button>
          ))}
        </div>

        {progressText && <div className="team-operator-progress">{progressText}</div>}

        {loading ? (
          <div className="team-empty-state">Loading timeline...</div>
        ) : error ? (
          <div className="team-error">{error}</div>
        ) : !hasItems ? (
          <div className="team-empty-state">No events for this run yet.</div>
        ) : (
          filteredItems.map((item, index) => {
            const isHandoff = item.event_type === 'agent_handoff';
            const isConflict = item.event_type === 'conflict_escalation';
            const isEscalation = item.event_type === 'decision_rank_escalation';
            const specialClass = isHandoff ? 'handoff-event' : isConflict || isEscalation ? 'escalation-event' : '';
            const labelMap: Record<string, string> = {
              agent_handoff: 'HAND-OFF',
              agent_delegation: 'DELEGATION',
              conflict_escalation: 'CONFLICT',
              agent_tool_invocation: 'TOOL',
              decision_rank_escalation: 'ESCALATION',
              peer_dialogue_round2: 'PEER R2',
            };
            const label = labelMap[item.event_type] || item.event_type;
            return (
              <article key={`${item.ts}-${item.event_type}-${index}`} className={`team-stream-item ${specialClass}`}>
                <div className="team-stream-head">
                  <strong>{label}</strong>
                  <span>{item.task_id || '-'}</span>
                  <time>{formatTs(item.ts)}</time>
                </div>
                <div className={`team-operator-level level-${item.level || 'info'}`}>{item.level || 'info'}</div>
                <div className="team-stream-task">
                  flow r{Number(item.execution_round || 0)} / s{Number(item.execution_sub_iteration || 0)} / g{Number(item.gate_iteration || 0)}
                  {item.assignee ? ` · ${item.assignee}` : ''}
                  {item.blocked_reason ? ` · blocked=${item.blocked_reason}` : ''}
                  {item.handoff_from || item.handoff_to ? ` · ${item.handoff_from || '-'} -> ${item.handoff_to || '-'}` : ''}
                  {item.meeting_kind ? ` · meeting=${item.meeting_kind}` : ''}
                  {item.conversation_thread_id ? ` · thread=${item.conversation_thread_id}` : ''}
                </div>
                <pre className="team-stream-body-text" style={isHandoff ? { color: 'var(--accent-warning)', fontWeight: 500 } : {}}>
                  {item.summary || '-'}
                </pre>
                {(item.artifact_created > 0 || item.artifact_modified > 0) && (
                  <div className="team-stream-task">
                    artifacts +{item.artifact_created}/~{item.artifact_modified} {item.artifact_files.slice(0, 4).join(', ')}
                  </div>
                )}
                {(item.productivity_score > 0 || item.reasoning_score > 0) && (
                  <div className="team-stream-task">
                    quality P{item.productivity_score}/R{item.reasoning_score}
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
