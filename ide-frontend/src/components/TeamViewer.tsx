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
  mcp_overview?: {
    total_servers?: number;
    enabled_servers?: number;
    healthy_servers?: number;
    running_servers?: number;
    bootstrapped_servers?: number;
    machine_profile?: {
      machine_name?: string;
      username?: string;
      userprofile?: string;
    };
    portability_counts?: Record<string, number>;
    health_categories?: Record<string, number>;
    health_recommendations?: Record<string, number>;
    fallback_counts?: Record<string, number>;
    replacement_counts?: Record<string, number>;
    opencode?: {
      available?: boolean;
      existing_candidate_paths?: string[];
      bootstrapped_servers?: string[];
      last_import?: {
        ts?: string;
        count?: number;
        path?: string;
      };
    };
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
    lead_run_mode?: string;
    phase_evidence_plan?: Record<string, { delegate_intents?: string[]; wait_policy?: string; delegate_budget?: number }>;
    delegate_batches?: Array<Record<string, unknown>>;
    delegate_economics?: {
      estimated?: boolean;
      batch_count?: number;
      specialist_task_count?: number;
      estimated_lead_tokens_avoided?: number;
      estimated_operator_tokens_used?: number;
      estimated_net_tokens_saved?: number;
      estimated_cost_units_saved?: number;
      quorum_met_count?: number;
      quorum_met_ratio?: number;
      specialist_breakdown?: Record<string, {
        count?: number;
        completed?: number;
        failed?: number;
        estimated_net_tokens_saved?: number;
        estimated_cost_units_saved?: number;
      }>;
    };
    tool_rewiring_summary?: {
      count?: number;
      by_specialist?: Record<string, number>;
      replacements?: Record<string, number>;
    };
    specialist_reports?: Array<{
      specialist?: string;
      summary?: string;
      recommendation?: string;
      provider?: string;
      model?: string;
      validation_status?: string;
      validation_errors?: string[];
      phase?: string;
      source?: string;
    }>;
    specialist_report_summary?: {
      count?: number;
      valid_count?: number;
      invalid_count?: number;
      by_specialist?: Record<string, { count?: number; valid?: number; invalid?: number }>;
    };
    context_pressure?: {
      score?: number;
      level?: string;
      signals?: string[];
      recommend_context_curator?: boolean;
    };
    context_curator_summary?: {
      project_updated_at?: string;
      chat_updated_at?: string;
      freshness_status?: string;
      project_layer_counts?: Record<string, number>;
      chat_layer_counts?: Record<string, number>;
      project_summary?: string;
      chat_summary?: string;
      context_curator_recommended?: boolean;
      invalidation_count?: number;
      open_question_count?: number;
      estimated_context_chars_saved?: number;
      estimated_context_tokens_saved?: number;
      raw_context_chars?: number;
      compact_context_chars?: number;
      compression_ratio?: number;
    };
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

export default function TeamViewer({ workspacePath, refreshMs = 8000 }: TeamViewerProps) {
  const [state, setState] = useState<ViewerState>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [syncingNotebook, setSyncingNotebook] = useState(false);
  const [syncNote, setSyncNote] = useState('');
  const [syncingOpenCodeMcp, setSyncingOpenCodeMcp] = useState(false);
  const [mcpSyncNote, setMcpSyncNote] = useState('');
  const [refreshingMcpHealth, setRefreshingMcpHealth] = useState(false);

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
    }, Math.max(5000, refreshMs));
    return () => window.clearInterval(timer);
  }, [workspacePath, refreshMs]);

  const topTasks = useMemo(() => (state.tasks || []).slice(0, 8), [state.tasks]);
  const topEvents = useMemo(() => (state.recent_events || []).slice(-8).reverse(), [state.recent_events]);
  const activeTasks = useMemo(
    () => (state.tasks || []).filter((task) => ['claimed', 'pending', 'running', 'in_progress'].includes(task.state)).slice(0, 8),
    [state.tasks],
  );
  const lastRunEvidencePlanCount = useMemo(
    () => Object.keys(state.last_chat_run?.phase_evidence_plan || {}).length,
    [state.last_chat_run?.phase_evidence_plan],
  );
  const topSpecialists = useMemo(() => {
    const entries = Object.entries(state.last_chat_run?.delegate_economics?.specialist_breakdown || {});
    return entries
      .sort((a, b) => Number((b[1]?.estimated_net_tokens_saved || 0)) - Number((a[1]?.estimated_net_tokens_saved || 0)))
      .slice(0, 3);
  }, [state.last_chat_run?.delegate_economics?.specialist_breakdown]);
  const topSpecialistReports = useMemo(
    () => (state.last_chat_run?.specialist_reports || []).slice(0, 4),
    [state.last_chat_run?.specialist_reports],
  );
  const topRewiringSpecialists = useMemo(() => {
    const entries = Object.entries(state.last_chat_run?.tool_rewiring_summary?.by_specialist || {});
    return entries
      .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
      .slice(0, 3);
  }, [state.last_chat_run?.tool_rewiring_summary?.by_specialist]);
  const topMcpReplacements = useMemo(() => {
    const entries = Object.entries(state.mcp_overview?.replacement_counts || {});
    return entries
      .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
      .slice(0, 4);
  }, [state.mcp_overview?.replacement_counts]);
  const chatLayerCounts = useMemo(
    () => state.last_chat_run?.context_curator_summary?.chat_layer_counts || {},
    [state.last_chat_run?.context_curator_summary?.chat_layer_counts],
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

  const syncOpenCodeMcp = async () => {
    if (syncingOpenCodeMcp) {
      return;
    }
    setSyncingOpenCodeMcp(true);
    setMcpSyncNote('');
    try {
      const response = await apiFetch('/api/aiteam/mcp/bootstrap-opencode', {
        method: 'POST',
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = await response.json();
      if (!response.ok || json.error || json.detail) {
        setMcpSyncNote(`Sync failed: ${json.detail || json.error || `HTTP ${response.status}`}`);
      } else {
        setMcpSyncNote(`Imported ${Number(json.imported || 0)} server(s) from OpenCode.`);
      }
      await loadState(true);
    } catch (error) {
      const err = error instanceof Error ? error.message : 'Unknown sync error';
      setMcpSyncNote(`Sync failed: ${err}`);
    } finally {
      setSyncingOpenCodeMcp(false);
    }
  };

  const refreshMcpHealth = async () => {
    if (refreshingMcpHealth) {
      return;
    }
    setRefreshingMcpHealth(true);
    setMcpSyncNote('');
    try {
      const response = await apiFetch('/api/aiteam/mcp/refresh-health', {
        method: 'POST',
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = await response.json();
      if (!response.ok || json.error || json.detail) {
        setMcpSyncNote(`Refresh failed: ${json.detail || json.error || `HTTP ${response.status}`}`);
      } else {
        const healthy = Number(json.report?.healthy || 0);
        const total = Number(json.report?.total || 0);
        const quarantined = Number(json.report?.auto_disabled || 0);
        setMcpSyncNote(`Health refreshed: ${healthy}/${total} healthy, quarantined=${quarantined}.`);
      }
      await loadState(true);
    } catch (error) {
      const err = error instanceof Error ? error.message : 'Unknown refresh error';
      setMcpSyncNote(`Refresh failed: ${err}`);
    } finally {
      setRefreshingMcpHealth(false);
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
                {`lead_run_mode=${state.last_chat_run.lead_run_mode || 'standard'} evidence_plan_phases=${lastRunEvidencePlanCount}\n`}
                {`execution_mode=${state.last_chat_run.execution_mode || '-'} placeholder_outputs=${state.last_chat_run.placeholder_outputs || 0}\n`}
                {`live_mode_gate=${state.last_chat_run.live_mode_rejected ? 'rejected' : (state.last_chat_run.live_mode_required ? 'required' : 'off')}\n`}
                {`checks=${state.last_chat_run.successful_check_count || 0} ${(state.last_chat_run.successful_checks || []).join(', ') || 'none'}\n`}
                {`evidence_gate=${state.last_chat_run.evidence_gate_rejected ? 'rejected' : 'pass'}\n`}
                {`task=${state.last_chat_run.task_id || '-'} phases=${state.last_chat_run.phase_count || 0} delegated=${state.last_chat_run.delegated_count || 0}\n`}
                {`context_pressure=${state.last_chat_run.context_pressure?.level || 'none'} score=${state.last_chat_run.context_pressure?.score || 0} curator=${state.last_chat_run.context_curator_summary?.context_curator_recommended ? 'recommended' : 'optional'}\n`}
                {`rewiring=${state.last_chat_run.tool_rewiring_summary?.count || 0} top=${topRewiringSpecialists.map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
                {`continuation=${state.last_chat_run.continuation_requested ? 'yes' : 'no'} from=${state.last_chat_run.continuation_of || '-'}\n`}
                {`ts=${state.last_chat_run.ts || '-'}`}
              </div>
            </>
          ) : (
            <div className="team-viewer-note">No chat execution detected yet.</div>
          )}
        </div>

        <div className="team-viewer-section">
          <h4>Curated context</h4>
          {state.last_chat_run?.context_curator_summary ? (
            <>
              <div className="team-viewer-note team-viewer-pre">
                {`freshness=${state.last_chat_run.context_curator_summary.freshness_status || 'unknown'} pressure=${state.last_chat_run.context_pressure?.level || 'none'} score=${state.last_chat_run.context_pressure?.score || 0}\n`}
                {`signals=${(state.last_chat_run.context_pressure?.signals || []).join(', ') || 'none'}\n`}
                {`chat_layers=${Object.entries(chatLayerCounts).map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
                {`invalidations=${state.last_chat_run.context_curator_summary.invalidation_count || 0} open_questions=${state.last_chat_run.context_curator_summary.open_question_count || 0}\n`}
                {`raw_chars=${state.last_chat_run.context_curator_summary.raw_context_chars || 0} compact_chars=${state.last_chat_run.context_curator_summary.compact_context_chars || 0}\n`}
                {`saved_chars=${state.last_chat_run.context_curator_summary.estimated_context_chars_saved || 0} saved_tokens≈${state.last_chat_run.context_curator_summary.estimated_context_tokens_saved || 0} ratio=${(((state.last_chat_run.context_curator_summary.compression_ratio || 0) as number) * 100).toFixed(0)}%\n`}
                {`chat_updated=${state.last_chat_run.context_curator_summary.chat_updated_at || '-'}\n`}
                {`project_updated=${state.last_chat_run.context_curator_summary.project_updated_at || '-'}`}
              </div>
              <div className="team-viewer-note team-viewer-pre">
                {state.last_chat_run.context_curator_summary.chat_summary || state.last_chat_run.context_curator_summary.project_summary || 'No curated summary available yet.'}
              </div>
            </>
          ) : (
            <div className="team-viewer-note">No curated context aggregated yet.</div>
          )}
        </div>

        <div className="team-viewer-section">
          <h4>Delegation economics</h4>
          {state.last_chat_run?.delegate_economics ? (
            <>
              <div className="team-viewer-note team-viewer-pre">
                {`estimated=${state.last_chat_run.delegate_economics.estimated ? 'yes' : 'no'} batches=${state.last_chat_run.delegate_economics.batch_count || 0} specialist_tasks=${state.last_chat_run.delegate_economics.specialist_task_count || 0}\n`}
                {`lead_tokens_avoided=${state.last_chat_run.delegate_economics.estimated_lead_tokens_avoided || 0} operator_tokens_used=${state.last_chat_run.delegate_economics.estimated_operator_tokens_used || 0}\n`}
                {`net_tokens_saved=${state.last_chat_run.delegate_economics.estimated_net_tokens_saved || 0} cost_units_saved=${state.last_chat_run.delegate_economics.estimated_cost_units_saved || 0}\n`}
                {`quorum=${state.last_chat_run.delegate_economics.quorum_met_count || 0}/${state.last_chat_run.delegate_economics.batch_count || 0} ratio=${(((state.last_chat_run.delegate_economics.quorum_met_ratio || 0) as number) * 100).toFixed(0)}%`}
              </div>
              <ul className="team-list">
                {topSpecialists.length === 0 ? (
                  <li>none</li>
                ) : (
                  topSpecialists.map(([name, stats]) => (
                    <li key={name}>
                      <span>{name}</span>
                      <strong>{`${stats.count || 0} tasks · net ${stats.estimated_net_tokens_saved || 0} · cost ${stats.estimated_cost_units_saved || 0}`}</strong>
                    </li>
                  ))
                )}
              </ul>
            </>
          ) : (
            <div className="team-viewer-note">No delegated economics reported yet.</div>
          )}
        </div>

        <div className="team-viewer-section">
          <h4>Specialist reports</h4>
          {state.last_chat_run?.specialist_report_summary ? (
            <>
              <div className="team-viewer-note team-viewer-pre">
                {`reports=${state.last_chat_run.specialist_report_summary.count || 0} valid=${state.last_chat_run.specialist_report_summary.valid_count || 0} invalid=${state.last_chat_run.specialist_report_summary.invalid_count || 0}\n`}
                {`specialists=${Object.entries(state.last_chat_run.specialist_report_summary.by_specialist || {}).map(([key, value]) => `${key}:${value.count || 0}/${value.valid || 0}v`).join(', ') || 'none'}`}
              </div>
              <ul className="team-list">
                {topSpecialistReports.length === 0 ? (
                  <li>none</li>
                ) : (
                  topSpecialistReports.map((report, index) => (
                    <li key={`${report.specialist || 'specialist'}-${index}`}>
                      <span>
                        {`${report.specialist || 'unknown'} · ${report.phase || '-'} · ${report.validation_status || 'unknown'}`}
                      </span>
                      <strong>{report.summary || report.recommendation || 'No summary available'}</strong>
                    </li>
                  ))
                )}
              </ul>
            </>
          ) : (
            <div className="team-viewer-note">No specialist reports aggregated yet.</div>
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

        <div className="team-viewer-section">
          <h4>MCP Fabric</h4>
          <div className="team-integration-row">
            <span className={`team-status-pill ${state.mcp_overview?.opencode?.available ? 'is-connected' : 'is-disconnected'}`}>
              {state.mcp_overview?.opencode?.available ? 'OpenCode detected' : 'OpenCode MCP unavailable'}
            </span>
            <span className="team-integration-meta">
              {`servers=${state.mcp_overview?.total_servers || 0} enabled=${state.mcp_overview?.enabled_servers || 0} healthy=${state.mcp_overview?.healthy_servers || 0} bootstrapped=${state.mcp_overview?.bootstrapped_servers || 0}`}
            </span>
            <button className="team-notebook-sync" onClick={() => void syncOpenCodeMcp()} disabled={syncingOpenCodeMcp}>
              {syncingOpenCodeMcp ? 'Syncing...' : 'Sync OpenCode MCPs'}
            </button>
            <button className="team-notebook-sync" onClick={() => void refreshMcpHealth()} disabled={refreshingMcpHealth}>
              {refreshingMcpHealth ? 'Refreshing...' : 'Refresh health'}
            </button>
          </div>
          <div className="team-viewer-note team-viewer-pre">
            {`machine=${state.mcp_overview?.machine_profile?.machine_name || '-'} user=${state.mcp_overview?.machine_profile?.username || '-'}\n`}
            {`running=${state.mcp_overview?.running_servers || 0}\n`}
            {`portability=${Object.entries(state.mcp_overview?.portability_counts || {}).map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
            {`health_categories=${Object.entries(state.mcp_overview?.health_categories || {}).map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
            {`actions=${Object.entries(state.mcp_overview?.health_recommendations || {}).map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
            {`fallbacks=${Object.entries(state.mcp_overview?.fallback_counts || {}).map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
            {`replacements=${topMcpReplacements.map(([key, value]) => `${key}:${value}`).join(', ') || 'none'}\n`}
            {`candidates=${(state.mcp_overview?.opencode?.existing_candidate_paths || []).join(', ') || 'none'}\n`}
            {`last_import=${state.mcp_overview?.opencode?.last_import?.ts || '-'} count=${state.mcp_overview?.opencode?.last_import?.count || 0}\n`}
            {`last_path=${state.mcp_overview?.opencode?.last_import?.path || '-'}\n`}
            {`servers=${(state.mcp_overview?.opencode?.bootstrapped_servers || []).join(', ') || 'none'}`}
          </div>
          {mcpSyncNote && <div className="team-viewer-note">{mcpSyncNote}</div>}
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
