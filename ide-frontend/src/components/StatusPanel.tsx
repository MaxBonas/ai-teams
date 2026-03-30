import { useEffect, useState } from 'react';
import { Activity, FolderTree, PanelRightClose, PanelRightOpen, RefreshCw, TerminalSquare } from 'lucide-react';
import { apiFetch } from '../lib/api';
import FileExplorer from './FileExplorer';
import FileViewer from './FileViewer';
import TerminalPanel from './TerminalPanel';

interface StatusPanelProps {
  workspacePath: string;
  minimized: boolean;
  onToggleMinimize: () => void;
  onLoadChat?: (taskId: string) => void;
}

interface BudgetInfo {
  daily_api_spend_usd: number;
  monthly_api_spend_usd: number;
  daily_budget_usd: number;
}

interface RecentRun {
  task_id: string;
  state: string;
  rounds_used: number;
  round_budget: number;
  elapsed_ms?: number;
  ts: string;
}

type WorkbenchTab = 'status' | 'files' | 'terminal';

export default function StatusPanel({ workspacePath, minimized, onToggleMinimize, onLoadChat }: StatusPanelProps) {
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [taskCounts, setTaskCounts] = useState({ total: 0, completed: 0, failed: 0 });
  const [activeTab, setActiveTab] = useState<WorkbenchTab>('status');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileRefreshToken, setFileRefreshToken] = useState(0);
  const [refreshingTree, setRefreshingTree] = useState(false);

  useEffect(() => {
    setRecentRuns([]);
    const headers: Record<string, string> = workspacePath ? { 'x-workspace-path': workspacePath } : {};

    const poll = async () => {
      try {
        const res = await apiFetch('/api/dashboard', { headers });
        if (!res.ok) return;
        const data = await res.json() as Record<string, unknown>;
        if (data.budget && typeof data.budget === 'object') {
          setBudget(data.budget as BudgetInfo);
        }
        setTaskCounts({
          total: Number(data.total_tasks ?? 0),
          completed: Number(data.completed_tasks ?? 0),
          failed: Number(data.failed_tasks ?? 0),
        });
      } catch { /* backend offline */ }
    };

    const pollState = async () => {
      try {
        const res = await apiFetch('/api/aiteam/state?environment=dev', { headers });
        if (!res.ok) return;
        const data = await res.json() as Record<string, unknown>;
        const run = data.last_chat_run;
        if (run && typeof run === 'object') {
          const r = run as Record<string, unknown>;
          setRecentRuns(prev => {
            const entry: RecentRun = {
              task_id: String(r.task_id ?? '').slice(0, 22),
              state: String(r.status ?? r.state ?? ''),
              rounds_used: Number(r.rounds_used ?? 0),
              round_budget: Number(r.round_budget ?? 0),
              elapsed_ms: Number(r.elapsed_ms ?? 0),
              ts: String(r.ts ?? new Date().toISOString()),
            };
            if (!entry.task_id) return prev;
            const existing = prev.find(x => x.task_id === entry.task_id);
            if (existing) return prev;
            return [entry, ...prev].slice(0, 5);
          });
        }
      } catch { /* ignore */ }
    };

    void poll();
    void pollState();
    const interval = setInterval(() => { void poll(); void pollState(); }, 4000);
    return () => clearInterval(interval);
  }, [workspacePath]);

  useEffect(() => {
    setSelectedFile(null);
  }, [workspacePath]);

  const stateColor = (s: string) => {
    if (s.includes('completed') || s === 'completed_or_closed') return 'var(--status-green)';
    if (s.includes('fail') || s.includes('error')) return 'var(--status-red)';
    if (s.includes('progress') || s.includes('exhaust')) return 'var(--status-amber)';
    return 'var(--text-secondary)';
  };

  return (
    <div className="status-panel">
      <div className="status-panel-header">
        <span className="status-panel-title">Workspace</span>
        <button
          className="status-panel-toggle"
          onClick={onToggleMinimize}
          title={minimized ? 'Expandir panel' : 'Colapsar panel'}
        >
          {minimized ? <PanelRightOpen size={13} /> : <PanelRightClose size={13} />}
        </button>
      </div>

      {!minimized && (
        <div className="workbench-tabs" role="tablist" aria-label="Workspace panel tabs">
          <button
            className={`workbench-tab ${activeTab === 'status' ? 'is-active' : ''}`}
            onClick={() => setActiveTab('status')}
            type="button"
          >
            <Activity size={13} /> Estado
          </button>
          <button
            className={`workbench-tab ${activeTab === 'files' ? 'is-active' : ''}`}
            onClick={() => setActiveTab('files')}
            type="button"
          >
            <FolderTree size={13} /> Archivos
          </button>
          <button
            className={`workbench-tab ${activeTab === 'terminal' ? 'is-active' : ''}`}
            onClick={() => setActiveTab('terminal')}
            type="button"
          >
            <TerminalSquare size={13} /> Terminal
          </button>
        </div>
      )}

      {!minimized && (
        <div className="status-panel-body">
          {activeTab === 'status' && (
            <>
              <section className="status-section">
                <div className="status-section-label">Presupuesto hoy</div>
                {budget ? (
                  <div className="status-budget-row">
                    <span className="status-budget-spend">
                      ${(budget.daily_api_spend_usd ?? 0).toFixed(3)}
                    </span>
                    {budget.daily_budget_usd > 0 && (
                      <span className="status-budget-limit">
                        / ${budget.daily_budget_usd.toFixed(2)}
                      </span>
                    )}
                  </div>
                ) : (
                  <div className="status-empty-hint">Sin datos reales del backend.</div>
                )}
              </section>

              {taskCounts.total > 0 && (
                <section className="status-section">
                  <div className="status-section-label">Tareas (sesion)</div>
                  <div className="status-counts">
                    <span className="status-count-ok">{taskCounts.completed} ok</span>
                    {taskCounts.failed > 0 && (
                      <span className="status-count-fail">{taskCounts.failed} fail</span>
                    )}
                    <span className="status-count-total">{taskCounts.total} total</span>
                  </div>
                </section>
              )}

              {recentRuns.length > 0 && (
                <section className="status-section">
                  <div className="status-section-label">Ultimas ejecuciones</div>
                  <div className="status-runs">
                    {recentRuns.map((run) => (
                      <button
                        key={run.task_id}
                        className={`status-run-item${onLoadChat ? ' status-run-item--clickable' : ''}`}
                        onClick={() => onLoadChat?.(run.task_id)}
                        title={onLoadChat ? `Cargar chat ${run.task_id}` : undefined}
                        disabled={!onLoadChat}
                      >
                        <span className="status-run-dot" style={{ background: stateColor(run.state) }} />
                        <span className="status-run-id">{run.task_id}</span>
                        <span className="status-run-rounds">{run.rounds_used}/{run.round_budget}r</span>
                      </button>
                    ))}
                  </div>
                </section>
              )}

              {!budget && recentRuns.length === 0 && (
                <div className="status-empty-hint">
                  Conecta el backend real para ver estado operativo.
                </div>
              )}
            </>
          )}

          {activeTab === 'files' && (
            <div className="workbench-files">
              <aside className="sidebar workbench-sidebar">
                <div className="sidebar-header">
                  <div className="sidebar-header-title">
                    <FolderTree size={14} />
                    <span>Archivos reales</span>
                  </div>
                  <div className="sidebar-header-actions">
                    <button
                      className="sidebar-refresh-btn"
                      onClick={() => setFileRefreshToken((prev) => prev + 1)}
                      disabled={refreshingTree}
                      type="button"
                    >
                      <RefreshCw size={12} className={refreshingTree ? 'spin' : ''} />
                      Refresh
                    </button>
                  </div>
                </div>
                <FileExplorer
                  activeFile={selectedFile}
                  onFileSelect={setSelectedFile}
                  workspacePath={workspacePath}
                  refreshToken={fileRefreshToken}
                  onRefreshStateChange={setRefreshingTree}
                />
              </aside>

              <section className="workbench-file-view">
                {selectedFile ? (
                  <FileViewer filePath={selectedFile} workspacePath={workspacePath} />
                ) : (
                  <div className="status-empty-hint">
                    Selecciona un archivo del arbol para ver su contenido real.
                  </div>
                )}
              </section>
            </div>
          )}

          {activeTab === 'terminal' && (
            <div className="terminal-pane workbench-terminal-pane">
              <div className="terminal-header">Terminal real del workspace</div>
              <div className="terminal-container">
                <TerminalPanel workspacePath={workspacePath} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
