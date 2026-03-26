import { useEffect, useState } from 'react';
import { RefreshCcw, PanelRightOpen, PanelRightClose } from 'lucide-react';
import { apiFetch } from '../lib/api';
import TeamViewer from './TeamViewer';
import OperatorTimeline from './OperatorTimeline';
import TeamConversationViewer from './TeamConversationViewer';
import TeamLogOutputViewer from './TeamLogOutputViewer';

type OpsTab = 'overview' | 'timeline' | 'conversations' | 'logs';

interface OpsHubProps {
  workspacePath: string;
  minimized?: boolean;
  onToggleMinimize?: () => void;
}

interface OpsTimelineProgress {
  state?: string;
  rounds_used?: number;
  round_budget?: number;
  completed_tasks?: number;
  pending_tasks?: number;
  failed_tasks?: number;
  execution_attempts?: number;
  execution_steps?: number;
}

interface OpsHubStatusResponse {
  selected_task_id?: string;
  latest_task_id?: string;
  progress?: OpsTimelineProgress;
}

const TAB_ORDER: Array<{ id: OpsTab; label: string }> = [
  { id: 'timeline', label: 'Timeline' },
  { id: 'overview', label: 'Overview' },
  { id: 'logs', label: 'Logs + Outputs' },
  { id: 'conversations', label: 'Conversations' },
];

export default function OpsHub({ workspacePath, minimized = false, onToggleMinimize }: OpsHubProps) {
  const [activeTab, setActiveTab] = useState<OpsTab>('timeline');
  const [refreshing, setRefreshing] = useState(false);
  const [statusRunId, setStatusRunId] = useState('');
  const [statusProgress, setStatusProgress] = useState<OpsTimelineProgress | null>(null);
  const [statusError, setStatusError] = useState('');

  const loadStatus = async (quiet = false) => {
    if (!quiet) {
      setRefreshing(true);
    }
    try {
      const response = await apiFetch('/api/aiteam/operator/timeline?limit=20&key_only=true', {
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = (await response.json()) as OpsHubStatusResponse;
      const runId = String(json.selected_task_id || json.latest_task_id || '').trim();
      setStatusRunId(runId);
      setStatusProgress(json.progress || null);
      setStatusError('');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to load operator status';
      setStatusError(message);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setStatusRunId('');
    setStatusProgress(null);
    setStatusError('');
    if (!workspacePath) {
      return;
    }
    void loadStatus(true);
    const timer = window.setInterval(() => {
      void loadStatus(true);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [workspacePath]);

  return (
    <section className="ops-hub">
      <header className="ops-hub-header">
        <div className="team-card-title">Ops Hub</div>
        <div className="ops-hub-header-actions">
          {onToggleMinimize && (
            <button
              className="team-viewer-refresh"
              onClick={onToggleMinimize}
              title={minimized ? 'Expand ops pane' : 'Minimize ops pane'}
            >
              {minimized ? <PanelRightOpen size={14} /> : <PanelRightClose size={14} />}
            </button>
          )}
          <button className="team-viewer-refresh" onClick={() => void loadStatus()} disabled={refreshing}>
            <RefreshCcw size={14} className={refreshing ? 'spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      <div className="ops-hub-status-badges">
        {statusError ? (
          <span className="status-badge status-badge-error">Unavailable</span>
        ) : !statusProgress ? (
          <span className="status-badge">No run yet</span>
        ) : (
          <>
            <span className={`status-badge status-badge-state ${statusProgress.state === 'completed' ? 'is-ok' : statusProgress.state === 'failed' ? 'is-fail' : 'is-active'}`}>
              {statusProgress.state || 'idle'}
            </span>
            {statusRunId && <span className="status-badge status-badge-id">{statusRunId}</span>}
            <span className="status-badge">R{statusProgress.rounds_used || 0}/{statusProgress.round_budget || 0}</span>
            <span className="status-badge">{statusProgress.completed_tasks || 0} done</span>
            {(statusProgress.pending_tasks || 0) > 0 && <span className="status-badge">{statusProgress.pending_tasks} pending</span>}
            {(statusProgress.failed_tasks || 0) > 0 && <span className="status-badge status-badge-error">{statusProgress.failed_tasks} failed</span>}
          </>
        )}
      </div>

      <div className="ops-hub-tabs">
        {TAB_ORDER.map((tab) => (
          <button
            key={tab.id}
            className={`ops-hub-tab ${activeTab === tab.id ? 'is-active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="ops-hub-body">
        {activeTab === 'overview' ? (
          <TeamViewer workspacePath={workspacePath} refreshMs={2500} />
        ) : activeTab === 'timeline' ? (
          <OperatorTimeline workspacePath={workspacePath} />
        ) : activeTab === 'conversations' ? (
          <TeamConversationViewer workspacePath={workspacePath} />
        ) : (
          <TeamLogOutputViewer workspacePath={workspacePath} />
        )}
      </div>
    </section>
  );
}
