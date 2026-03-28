import { useEffect, useState } from 'react';
import { PanelRightClose, PanelRightOpen } from 'lucide-react';
import { apiFetch } from '../lib/api';

interface StatusPanelProps {
  workspacePath: string;
  minimized: boolean;
  onToggleMinimize: () => void;
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

export default function StatusPanel({ workspacePath, minimized, onToggleMinimize }: StatusPanelProps) {
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [taskCounts, setTaskCounts] = useState({ total: 0, completed: 0, failed: 0 });

  useEffect(() => {
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

  const stateColor = (s: string) => {
    if (s.includes('completed') || s === 'completed_or_closed') return 'var(--status-green)';
    if (s.includes('fail') || s.includes('error')) return 'var(--status-red)';
    if (s.includes('progress') || s.includes('exhaust')) return 'var(--status-amber)';
    return 'var(--text-secondary)';
  };

  return (
    <div className="status-panel">
      <div className="status-panel-header">
        <span className="status-panel-title">Estado</span>
        <button
          className="status-panel-toggle"
          onClick={onToggleMinimize}
          title={minimized ? 'Expandir panel' : 'Colapsar panel'}
        >
          {minimized ? <PanelRightOpen size={13} /> : <PanelRightClose size={13} />}
        </button>
      </div>

      {!minimized && (
        <div className="status-panel-body">
          {/* Budget */}
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
              <div className="status-empty-hint">Backend offline</div>
            )}
          </section>

          {/* Task counts */}
          {taskCounts.total > 0 && (
            <section className="status-section">
              <div className="status-section-label">Tareas (sesión)</div>
              <div className="status-counts">
                <span className="status-count-ok">{taskCounts.completed} ok</span>
                {taskCounts.failed > 0 && (
                  <span className="status-count-fail">{taskCounts.failed} fail</span>
                )}
                <span className="status-count-total">{taskCounts.total} total</span>
              </div>
            </section>
          )}

          {/* Recent runs */}
          {recentRuns.length > 0 && (
            <section className="status-section">
              <div className="status-section-label">Últimas ejecuciones</div>
              <div className="status-runs">
                {recentRuns.map((run) => (
                  <div key={run.task_id} className="status-run-item">
                    <span className="status-run-dot" style={{ background: stateColor(run.state) }} />
                    <span className="status-run-id">{run.task_id}</span>
                    <span className="status-run-rounds">{run.rounds_used}/{run.round_budget}r</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {!budget && recentRuns.length === 0 && (
            <div className="status-empty-hint">
              Inicia el backend para ver datos en tiempo real.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
