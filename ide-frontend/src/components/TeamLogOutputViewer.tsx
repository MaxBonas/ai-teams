import { useEffect, useState } from 'react';
import { RefreshCcw } from 'lucide-react';
import { apiFetch } from '../lib/api';

interface EventLogItem {
  ts: string;
  event_type: string;
  task_id: string;
  summary: string;
}

interface TaskOutputItem {
  task_id: string;
  role: string;
  state: string;
  ts: string;
  output: string;
}

interface LogResponse {
  event_logs?: EventLogItem[];
  task_outputs?: TaskOutputItem[];
  event_total?: number;
  output_total?: number;
  error?: string;
  detail?: string;
}

interface TeamLogOutputViewerProps {
  workspacePath: string;
}

function summarizeEvent(eventType: string, payload: Record<string, unknown>): string {
  if (eventType === 'execution_step') {
    return `execution_step success=${Boolean(payload.success)} type=${String(payload.step_type || '-')} exit=${String(payload.exit_code || '-')} cmd=${String(payload.command || '-')}`;
  }
  if (eventType === 'task_execution') {
    return `task_execution success=${Boolean(payload.success)} role=${String(payload.role || '-')} assignee=${String(payload.assignee || '-')} latency=${String(payload.latency_ms || 0)}ms`;
  }
  if (eventType === 'routing_decision') {
    return `routing success=${Boolean(payload.success)} provider=${String(payload.provider || '-')} model=${String(payload.model || '-')} channel=${String(payload.channel || '-')}`;
  }
  if (eventType === 'mail_dm' || eventType === 'mail_broadcast') {
    return `mail sender=${String(payload.sender || '-')} recipient=${String(payload.recipient || 'broadcast')} subject=${String(payload.subject || '-')}`;
  }
  return JSON.stringify(payload);
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

export default function TeamLogOutputViewer({ workspacePath }: TeamLogOutputViewerProps) {
  const [activeTab, setActiveTab] = useState<'logs' | 'outputs'>('logs');
  const [eventLogs, setEventLogs] = useState<EventLogItem[]>([]);
  const [taskOutputs, setTaskOutputs] = useState<TaskOutputItem[]>([]);
  const [eventTotal, setEventTotal] = useState(0);
  const [outputTotal, setOutputTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const loadLogs = async (quiet = false) => {
    if (!quiet) {
      setRefreshing(true);
    }
    try {
      const response = await apiFetch('/api/aiteam/logs?limit=100');
      const json = (await response.json()) as LogResponse;
      if (response.ok && Array.isArray(json.event_logs) && Array.isArray(json.task_outputs)) {
        setEventLogs(json.event_logs || []);
        setTaskOutputs(json.task_outputs || []);
        setEventTotal(Number(json.event_total || 0));
        setOutputTotal(Number(json.output_total || 0));
        setError(json.error || '');
      } else {
        const eventsResp = await apiFetch(`/api/fs/file?path=${encodeURIComponent('runtime/events.jsonl')}`);
        const eventsJson = await eventsResp.json() as { content?: string; error?: string };

        if (!eventsResp.ok || eventsJson.error) {
          const detail = json.detail || json.error || eventsJson.error || `HTTP ${response.status}`;
          setError(`Log endpoint unavailable: ${detail}`);
          setEventLogs([]);
          setTaskOutputs([]);
          setEventTotal(0);
          setOutputTotal(0);
          return;
        }

        const rawEventItems: Array<{ ts: string; event_type: string; payload: Record<string, unknown> }> = (eventsJson.content || '')
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter((line) => line.length > 0)
          .map((line) => {
            try {
              const row = JSON.parse(line) as Record<string, unknown>;
              const payload = (row.payload && typeof row.payload === 'object') ? (row.payload as Record<string, unknown>) : {};
              return {
                ts: String(row.ts || ''),
                event_type: String(row.event_type || 'unknown'),
                payload,
              };
            } catch {
              return null;
            }
          })
          .filter((item): item is { ts: string; event_type: string; payload: Record<string, unknown> } => item !== null);

        const parsedEventLogs: EventLogItem[] = rawEventItems
          .slice()
          .sort((a, b) => b.ts.localeCompare(a.ts))
          .slice(0, 100)
          .map((row) => ({
            ts: row.ts,
            event_type: row.event_type,
            task_id: String(row.payload.task_id || ''),
            summary: summarizeEvent(row.event_type, row.payload),
          }));

        const taskLastTs = new Map<string, string>();
        rawEventItems.forEach((row) => {
          if (row.event_type !== 'task_execution') {
            return;
          }
          const taskId = String(row.payload.task_id || '');
          if (taskId) {
            taskLastTs.set(taskId, row.ts);
          }
        });

        setEventLogs(parsedEventLogs);
        setTaskOutputs([]);
        setEventTotal(rawEventItems.length);
        setOutputTotal(0);
        setError('Log endpoint unavailable: mostrando solo eventos; task outputs requieren el endpoint backend.');
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
    setEventLogs([]);
    setTaskOutputs([]);
    setEventTotal(0);
    setOutputTotal(0);
    setError('');
    setLoading(true);
    void loadLogs(true);
    const timer = window.setInterval(() => {
      void loadLogs(true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [workspacePath]);

  return (
    <section className="team-card">
      <header className="team-card-header">
        <div className="team-card-title">Log + Output</div>
        <button className="team-viewer-refresh" onClick={() => void loadLogs()} disabled={refreshing}>
          <RefreshCcw size={14} className={refreshing ? 'spin' : ''} />
          {activeTab === 'logs' ? eventTotal : outputTotal}
        </button>
      </header>

      <div className="team-tabs-row">
        <button
          className={`team-tab-btn ${activeTab === 'logs' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('logs')}
        >
          Logs
        </button>
        <button
          className={`team-tab-btn ${activeTab === 'outputs' ? 'is-active' : ''}`}
          onClick={() => setActiveTab('outputs')}
        >
          Outputs
        </button>
      </div>

      <div className="team-stream-body">
        {loading ? (
          <div className="team-empty-state">Loading logs...</div>
        ) : error ? (
          <div className="team-error">{error}</div>
        ) : activeTab === 'logs' ? (
          eventLogs.length === 0 ? (
            <div className="team-empty-state">No logs yet.</div>
          ) : (
            eventLogs.map((item, idx) => (
              <article key={`${item.ts}-${item.event_type}-${idx}`} className="team-stream-item">
                <div className="team-stream-head">
                  <strong>{item.event_type}</strong>
                  <span>{item.task_id || '-'}</span>
                  <time>{formatTs(item.ts)}</time>
                </div>
                <pre className="team-stream-body-text">{item.summary || '-'}</pre>
              </article>
            ))
          )
        ) : taskOutputs.length === 0 ? (
          <div className="team-empty-state">No task outputs yet.</div>
        ) : (
          taskOutputs.map((item, idx) => (
            <article key={`${item.task_id}-${item.ts}-${idx}`} className="team-stream-item">
              <div className="team-stream-head">
                <strong>{item.task_id || '-'}</strong>
                <span>{item.role || '-'}/{item.state || '-'}</span>
                <time>{formatTs(item.ts)}</time>
              </div>
              <pre className="team-stream-body-text">{item.output || '-'}</pre>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
