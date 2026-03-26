import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, Gauge, ListChecks, RefreshCcw } from 'lucide-react';
import { apiFetch } from '../lib/api';

interface ViewerState {
  task_total?: number;
  task_state_counts?: Record<string, number>;
  summary?: {
    task_execution_success_rate?: number;
    alerts?: string[];
  };
  tuning_recommendations?: string[];
  agent_latency_percentiles?: Record<string, { p50_ms: number; p95_ms: number; count: number }>;
  tasks?: Array<{ task_id: string; state: string; role: string; assignee: string; title: string }>;
  recent_events?: Array<{ ts?: string; event_type?: string; payload?: Record<string, unknown> }>;
  notebooklm_status?: {
    connected?: boolean;
    mode?: string;
    details?: string;
  };
  last_chat_run?: {
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
    successful_checks?: string[];
    successful_check_count?: number;
    live_mode_required?: boolean;
    live_mode_rejected?: boolean;
    evidence_gate_rejected?: boolean;
    ts?: string;
  };
  last_lead_user_summary?: {
    task_id?: string;
    subject?: string;
    body?: string;
    timestamp?: string;
  };
  project_continuity?: string;
  error?: string;
}

interface TeamViewerProps {
  workspacePath: string;
  refreshMs?: number;
}

export default function TeamViewer({ workspacePath, refreshMs = 3000 }: TeamViewerProps) {
  const [state, setState] = useState<ViewerState>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingNotebook, setSyncingNotebook] = useState(false);
  const [syncNote, setSyncNote] = useState('');

  const loadState = async (quiet = false) => {
    if (!quiet) {
      setRefreshing(true);
    }
    try {
      const response = await apiFetch('/api/aiteam/state?environment=dev', {
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = (await response.json()) as ViewerState;
      setState(json);
    } catch (error) {
      const err = error instanceof Error ? error.message : 'Unknown request failure';
      setState({ error: err });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setState({});
    if (!workspacePath) {
      setLoading(false);
      return;
    }
    void loadState(true);
    const timer = window.setInterval(() => {
      void loadState(true);
    }, Math.max(1200, refreshMs));
    return () => window.clearInterval(timer);
  }, [workspacePath, refreshMs]);

  const topTasks = useMemo(() => (state.tasks || []).slice(0, 8), [state.tasks]);
  const topEvents = useMemo(() => (state.recent_events || []).slice(-8).reverse(), [state.recent_events]);
  const activeTasks = useMemo(
    () => (state.tasks || []).filter((task) => ['claimed', 'pending', 'running', 'in_progress'].includes(task.state)).slice(0, 8),
    [state.tasks],
  );

  const syncNotebookLM = async () => {
    if (syncingNotebook) {
      return;
    }
    setSyncingNotebook(true);
    setSyncNote('');
    try {
      const syncContent = [
        '# AI Team Viewer Snapshot',
        `Generated: ${new Date().toISOString()}`,
        '',
        `Task total: ${state.task_total || 0}`,
        `Success rate: ${state.summary?.task_execution_success_rate || 0}%`,
        `Active tasks: ${activeTasks.length}`,
        '',
        '## Active tasks',
        ...(activeTasks.length > 0
          ? activeTasks.map((task) => `- ${task.task_id} [${task.state}] role=${task.role} assignee=${task.assignee || '-'}`)
          : ['- none']),
        '',
        '## Tuning recommendations',
        ...((state.tuning_recommendations || []).slice(0, 5).map((item) => `- ${item}`) || ['- none']),
      ].join('\n');

      const response = await apiFetch('/api/notebooklm/sync', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-workspace-path': workspacePath,
        },
        body: JSON.stringify({
          title: 'IDE Team Viewer Sync',
          source: 'ide_viewer',
          content: syncContent,
          export_format: 'markdown',
          days: 7,
        }),
      });
      const json = await response.json();
      if (json.error) {
        setSyncNote(`Sync failed: ${json.error}`);
      } else {
        setSyncNote(`Sync mode=${json.mode || '-'} success=${Boolean(json.success)}.`);
      }
      await loadState(true);
    } catch (error) {
      const err = error instanceof Error ? error.message : 'Unknown sync error';
      setSyncNote(`Sync failed: ${err}`);
    } finally {
      setSyncingNotebook(false);
    }
  };

  if (loading) {
    return <section className="team-card"><div className="team-empty-state">Loading team state...</div></section>;
  }

  if (state.error) {
    return (
      <section className="team-card">
        <div className="team-error">
          <AlertTriangle size={14} /> {state.error}
        </div>
      </section>
    );
  }

  return (
    <section className="team-card">
      <header className="team-card-header">
        <div className="team-card-title">Team Viewer</div>
        <button className="team-viewer-refresh" onClick={() => void loadState()} disabled={refreshing}>
          <RefreshCcw size={14} className={refreshing ? 'spin' : ''} />
          Refresh
        </button>
      </header>

      <div className="team-viewer-body">
        <div className="team-viewer-section">
          <h4>Project continuity</h4>
          <div className="team-viewer-note team-viewer-pre">
            {state.project_continuity || 'No previous session context yet.'}
          </div>
        </div>

        <div className="team-viewer-section">
          <h4>Last chat run</h4>
          {state.last_chat_run?.task_id ? (
            <>
              <div className={`team-execution-badge mode-${state.last_chat_run.execution_mode || 'idle'}`}>
                {state.last_chat_run.execution_mode ? state.last_chat_run.execution_mode.toUpperCase() : 'READY'}
                {(state.last_chat_run.placeholder_outputs || 0) > 0 && ` · placeholders ${state.last_chat_run.placeholder_outputs}`}
              </div>
              <div className="team-viewer-note team-viewer-pre">
                {`mode=${state.last_chat_run.mode || '-'} rounds=${state.last_chat_run.rounds_used || 0}/${state.last_chat_run.round_budget || 0} status=${state.last_chat_run.status || '-'}\n`}
                {`execution_mode=${state.last_chat_run.execution_mode || '-'} placeholder_outputs=${state.last_chat_run.placeholder_outputs || 0}\n`}
                {`live_mode_gate=${state.last_chat_run.live_mode_rejected ? 'rejected' : (state.last_chat_run.live_mode_required ? 'required' : 'off')}\n`}
                {`checks=${state.last_chat_run.successful_check_count || 0} ${(state.last_chat_run.successful_checks || []).join(', ') || 'none'}\n`}
                {`evidence_gate=${state.last_chat_run.evidence_gate_rejected ? 'rejected' : 'pass'}\n`}
                {`task=${state.last_chat_run.task_id || '-'} phases=${state.last_chat_run.phase_count || 0} delegated=${state.last_chat_run.delegated_count || 0}\n`}
                {`continuation=${state.last_chat_run.continuation_requested ? 'yes' : 'no'} from=${state.last_chat_run.continuation_of || '-'}\n`}
                {`ts=${state.last_chat_run.ts || '-'}`}
              </div>
            </>
          ) : (
            <div className="team-viewer-note">No chat execution detected yet.</div>
          )}
        </div>

        <div className="team-viewer-section">
          <h4>Lead summary for user</h4>
          {state.last_lead_user_summary?.body ? (
            <div className="team-viewer-note team-viewer-pre">
              {state.last_lead_user_summary.body}
            </div>
          ) : (
            <div className="team-viewer-note">No user-facing lead summary published yet.</div>
          )}
        </div>

        <div className="team-viewer-section">
          <h4>NotebookLM</h4>
          <div className="team-integration-row">
            <span className={`team-status-pill ${state.notebooklm_status?.connected ? 'is-connected' : 'is-disconnected'}`}>
              {state.notebooklm_status?.connected ? 'Connected' : 'Not connected'}
            </span>
            <span className="team-integration-meta">
              {state.notebooklm_status?.mode || 'unknown'}
            </span>
            <button className="team-notebook-sync" onClick={() => void syncNotebookLM()} disabled={syncingNotebook}>
              {syncingNotebook ? 'Syncing...' : 'Sync now'}
            </button>
          </div>
          <div className="team-viewer-note">
            {state.notebooklm_status?.details || 'No NotebookLM status reported by backend.'}
          </div>
          {syncNote && <div className="team-viewer-note">{syncNote}</div>}
        </div>

        <div className="team-stats-grid">
          <div className="team-stat-item">
            <ListChecks size={14} />
            <span>Tasks</span>
            <strong>{state.task_total || 0}</strong>
          </div>
          <div className="team-stat-item">
            <Gauge size={14} />
            <span>Success</span>
            <strong>{state.summary?.task_execution_success_rate || 0}%</strong>
          </div>
          <div className="team-stat-item">
            <Activity size={14} />
            <span>Alerts</span>
            <strong>{state.summary?.alerts?.length || 0}</strong>
          </div>
        </div>

        <div className="team-viewer-section">
          <h4>Active tasks</h4>
          <ul className="team-list">
            {activeTasks.length === 0 ? (
              <li>none</li>
            ) : (
              activeTasks.map((task) => (
                <li key={task.task_id}>
                  <span>{task.task_id} [{task.state}]</span>
                  <strong>{task.role}/{task.assignee || '-'}</strong>
                </li>
              ))
            )}
          </ul>
        </div>

        <div className="team-viewer-section">
          <h4>Latency p95 by agent</h4>
          <ul className="team-list">
            {Object.entries(state.agent_latency_percentiles || {}).length === 0 ? (
              <li>none</li>
            ) : (
              Object.entries(state.agent_latency_percentiles || {}).map(([agent, stats]) => (
                <li key={agent}>
                  <span>{agent}</span>
                  <strong>p95 {stats.p95_ms}ms</strong>
                </li>
              ))
            )}
          </ul>
        </div>

        <div className="team-viewer-section">
          <h4>Tuning recommendations</h4>
          <ul className="team-list team-list-wrap">
            {(state.tuning_recommendations || ['none']).slice(0, 4).map((item, idx) => (
              <li key={`${idx}-${item}`}>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="team-viewer-section">
          <h4>Task snapshot</h4>
          <ul className="team-list">
            {topTasks.length === 0 ? (
              <li>none</li>
            ) : (
              topTasks.map((task) => (
                <li key={task.task_id}>
                  <span>{task.task_id} [{task.state}]</span>
                  <strong>{task.role}/{task.assignee || '-'}</strong>
                </li>
              ))
            )}
          </ul>
        </div>

        <div className="team-viewer-section">
          <h4>Recent events</h4>
          <ul className="team-list">
            {topEvents.length === 0 ? (
              <li>none</li>
            ) : (
              topEvents.map((event, idx) => (
                <li key={`${event.ts || 'ts'}-${idx}`}>
                  <span>{event.event_type || 'unknown'}</span>
                  <strong>{event.ts || '-'}</strong>
                </li>
              ))
            )}
          </ul>
        </div>
      </div>
    </section>
  );
}
