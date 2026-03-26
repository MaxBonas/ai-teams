import { useEffect, useState } from 'react';
import { RefreshCcw, Mail } from 'lucide-react';
import { apiFetch } from '../lib/api';

interface ConversationItem {
  timestamp: string;
  sender: string;
  recipient: string;
  subject: string;
  body: string;
  task_id: string;
  message_id?: string;
}

interface ConversationResponse {
  total?: number;
  items?: ConversationItem[];
  last_chat_run?: {
    execution_mode?: string;
    placeholder_outputs?: number;
    successful_check_count?: number;
    live_mode_required?: boolean;
    live_mode_rejected?: boolean;
  };
  error?: string;
  detail?: string;
}

interface TeamConversationViewerProps {
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

type InboxMode = 'all' | 'inbox';
const ROLES = ['', 'team_lead', 'engineer', 'researcher', 'reviewer', 'qa', 'system'];

export default function TeamConversationViewer({ workspacePath }: TeamConversationViewerProps) {
  const [items, setItems] = useState<ConversationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [executionMode, setExecutionMode] = useState('unknown');
  const [placeholderOutputs, setPlaceholderOutputs] = useState(0);
  const [successfulCheckCount, setSuccessfulCheckCount] = useState(0);
  const [liveGate, setLiveGate] = useState('off');
  const [inboxMode, setInboxMode] = useState<InboxMode>('all');
  const [recipientFilter, setRecipientFilter] = useState('');
  const [senderFilter, setSenderFilter] = useState('');

  const loadInbox = async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const params = new URLSearchParams({ limit: '80' });
      if (recipientFilter) params.set('recipient', recipientFilter);
      if (senderFilter) params.set('sender', senderFilter);
      if (inboxMode === 'inbox') params.set('unread_only', 'true');
      const response = await apiFetch(`/api/aiteam/mailbox/inbox?${params.toString()}`, {
        headers: { 'x-workspace-path': workspacePath },
      });
      if (response.ok) {
        const json = await response.json() as { messages?: ConversationItem[]; total?: number; unread?: number };
        setItems(json.messages || []);
        setTotal(Number(json.total || 0));
        setUnreadCount(Number(json.unread || 0));
        setError('');
        return true;
      }
    } catch { /* fallthrough */ }
    return false;
  };

  const loadConversations = async (quiet = false) => {
    if (!quiet) {
      setRefreshing(true);
    }
    // Try inbox API first (supports filtering)
    if (recipientFilter || senderFilter || inboxMode === 'inbox') {
      const ok = await loadInbox(quiet);
      if (ok) {
        setLoading(false);
        setRefreshing(false);
        return;
      }
    }
    try {
      const response = await apiFetch('/api/aiteam/conversations?limit=80', {
        headers: {
          'x-workspace-path': workspacePath,
        },
      });
      const json = (await response.json()) as ConversationResponse;
      if (response.ok && Array.isArray(json.items)) {
        setItems(json.items || []);
        setTotal(Number(json.total || 0));
        setError(json.error || '');
        setExecutionMode(typeof json.last_chat_run?.execution_mode === 'string' ? json.last_chat_run.execution_mode : 'unknown');
        setPlaceholderOutputs(Number(json.last_chat_run?.placeholder_outputs || 0));
        setSuccessfulCheckCount(Number(json.last_chat_run?.successful_check_count || 0));
        const liveRejected = Boolean(json.last_chat_run?.live_mode_rejected);
        const liveRequired = Boolean(json.last_chat_run?.live_mode_required);
        setLiveGate(liveRejected ? 'rejected' : (liveRequired ? 'required' : 'off'));
        // Also fetch unread count
        void loadInbox(true);
      } else {
        const fallback = await apiFetch(`/api/fs/file?path=${encodeURIComponent('runtime/mailbox.jsonl')}`);
        const fallbackJson = await fallback.json() as { content?: string; error?: string };
        if (!fallback.ok || fallbackJson.error) {
          const detail = json.detail || json.error || fallbackJson.error || `HTTP ${response.status}`;
          setError(`Conversation endpoint unavailable: ${detail}`);
          setItems([]);
          setTotal(0);
          return;
        }

        const parsed: ConversationItem[] = (fallbackJson.content || '')
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter((line) => line.length > 0)
          .map((line) => {
            try {
              const row = JSON.parse(line) as Record<string, unknown>;
              return {
                timestamp: String(row.timestamp || ''),
                sender: String(row.sender || ''),
                recipient: String(row.recipient || ''),
                subject: String(row.subject || ''),
                body: String(row.body || ''),
                task_id: String(row.task_id || ''),
              };
            } catch {
              return null;
            }
          })
          .filter((item): item is ConversationItem => item !== null)
          .sort((a, b) => b.timestamp.localeCompare(a.timestamp))
          .slice(0, 80);

        setItems(parsed);
        setTotal(parsed.length);
        setError('');
        setExecutionMode('unknown');
        setPlaceholderOutputs(0);
        setSuccessfulCheckCount(0);
        setLiveGate('off');
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
    setItems([]);
    setTotal(0);
    setError('');
    setLoading(true);
    void loadConversations(true);
    const timer = window.setInterval(() => {
      void loadConversations(true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [workspacePath, inboxMode, recipientFilter, senderFilter]);

  return (
    <section className="team-card">
      <header className="team-card-header">
        <div className="team-card-title">
          AI Team Conversations
          {unreadCount > 0 && (
            <span className="status-badge status-badge-error" style={{ marginLeft: 6, fontSize: 11 }}>
              {unreadCount} unread
            </span>
          )}
        </div>
        <div className={`team-execution-badge mode-${executionMode}`}>
          {executionMode.toUpperCase()} · placeholder {placeholderOutputs} · checks {successfulCheckCount} · live-gate {liveGate}
        </div>
        <button className="team-viewer-refresh" onClick={() => void loadConversations()} disabled={refreshing}>
          <RefreshCcw size={14} className={refreshing ? 'spin' : ''} />
          {total}
        </button>
      </header>

      <div className="team-operator-controls" style={{ padding: '4px 8px', gap: 6 }}>
        <button
          className={`ops-hub-tab ${inboxMode === 'all' ? 'is-active' : ''}`}
          onClick={() => setInboxMode('all')}
          style={{ padding: '2px 8px', fontSize: '11px' }}
        >
          All
        </button>
        <button
          className={`ops-hub-tab ${inboxMode === 'inbox' ? 'is-active' : ''}`}
          onClick={() => setInboxMode('inbox')}
          style={{ padding: '2px 8px', fontSize: '11px' }}
        >
          <Mail size={10} /> Unread
        </button>
        <select
          className="team-role-select"
          value={recipientFilter}
          onChange={(e) => setRecipientFilter(e.target.value)}
          style={{ fontSize: '11px', padding: '2px 4px' }}
        >
          <option value="">To: All</option>
          {ROLES.filter(Boolean).map((r) => <option key={r} value={r}>To: {r}</option>)}
        </select>
        <select
          className="team-role-select"
          value={senderFilter}
          onChange={(e) => setSenderFilter(e.target.value)}
          style={{ fontSize: '11px', padding: '2px 4px' }}
        >
          <option value="">From: All</option>
          {ROLES.filter(Boolean).map((r) => <option key={r} value={r}>From: {r}</option>)}
        </select>
      </div>

      <div className="team-stream-body">
        {loading ? (
          <div className="team-empty-state">Loading conversations...</div>
        ) : error ? (
          <div className="team-error">{error}</div>
        ) : items.length === 0 ? (
          <div className="team-empty-state">No conversation messages yet.</div>
        ) : (
          items.map((item, idx) => (
            <article key={`${item.timestamp}-${item.task_id}-${idx}`} className="team-stream-item">
              <div className="team-stream-head">
                <strong>{item.sender || '-'}</strong>
                <span>{item.recipient || '-'}</span>
                <time>{formatTs(item.timestamp)}</time>
              </div>
              <div className="team-stream-subject">{item.subject || '(no subject)'}</div>
              <pre className="team-stream-body-text">{item.body || '(empty body)'}</pre>
              {item.task_id && <div className="team-stream-task">task: {item.task_id}</div>}
            </article>
          ))
        )}
      </div>
    </section>
  );
}
