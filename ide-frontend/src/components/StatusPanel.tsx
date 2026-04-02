import { useEffect, useState } from 'react';
import { Activity, FolderTree, GitBranch, PanelRightClose, PanelRightOpen, RefreshCw, TerminalSquare } from 'lucide-react';
import { apiFetch } from '../lib/api';
import FileExplorer from './FileExplorer';
import FileViewer from './FileViewer';
import RoutingCatalogPanel from './RoutingCatalogPanel';
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

interface LeadDecisions {
  advisory_mode: boolean;
  advisory_reason: string;
  auto_extended_rounds: number;
  lead_budget_extended: boolean;
  lead_budget_extension: number;
}

interface PeerConsultationSummary {
  consulted_roles: string[];
  consulted_providers: string[];
  unavailable_roles: string[];
  provider_count: number;
  diversity_observed: boolean;
}

interface ProductArtifactsSummary {
  has_artifacts: boolean;
  created: number;
  modified: number;
  file_count: number;
  files_preview_truncated: boolean;
  files: string[];
  message: string;
  internal_runtime_excluded: boolean;
}

interface OperationalSummaryItem {
  task_id: string;
  short_id: string;
  title: string;
  role: string;
  state: string;
  operational_state: string;
  reason_code: string;
  reason_label: string;
  source_root: string;
}

interface OperationalReason {
  code: string;
  label: string;
  count: number;
}

interface OperationalTaskSummary {
  has_actionable_items: boolean;
  active_total: number;
  counts: Record<string, number>;
  blocked_reasons: OperationalReason[];
  sample_items: OperationalSummaryItem[];
  carryover_roots: string[];
}

type WorkbenchTab = 'status' | 'routing' | 'files' | 'terminal';

export default function StatusPanel({ workspacePath, minimized, onToggleMinimize, onLoadChat }: StatusPanelProps) {
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [taskCounts, setTaskCounts] = useState({ total: 0, completed: 0, failed: 0 });
  const [leadDecisions, setLeadDecisions] = useState<LeadDecisions | null>(null);
  const [peerConsultation, setPeerConsultation] = useState<PeerConsultationSummary | null>(null);
  const [productArtifacts, setProductArtifacts] = useState<ProductArtifactsSummary | null>(null);
  const [operationalSummary, setOperationalSummary] = useState<OperationalTaskSummary | null>(null);
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
          const hasDecision =
            Boolean(r.advisory_mode) ||
            Number(r.auto_extended_rounds ?? 0) > 0 ||
            Boolean(r.lead_budget_extended);
          if (hasDecision) {
            setLeadDecisions({
              advisory_mode: Boolean(r.advisory_mode),
              advisory_reason: String(r.advisory_reason ?? ''),
              auto_extended_rounds: Number(r.auto_extended_rounds ?? 0),
              lead_budget_extended: Boolean(r.lead_budget_extended),
              lead_budget_extension: Number(r.lead_budget_extension ?? 0),
            });
          } else {
            setLeadDecisions(null);
          }
          const peerRaw = r.peer_consultation_summary;
          if (peerRaw && typeof peerRaw === 'object') {
            const peer = peerRaw as Record<string, unknown>;
            setPeerConsultation({
              consulted_roles: Array.isArray(peer.consulted_roles)
                ? (peer.consulted_roles as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                : [],
              consulted_providers: Array.isArray(peer.consulted_providers)
                ? (peer.consulted_providers as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                : [],
              unavailable_roles: Array.isArray(peer.unavailable_roles)
                ? (peer.unavailable_roles as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                : [],
              provider_count: Number.isFinite(Number(peer.provider_count)) ? Number(peer.provider_count) : 0,
              diversity_observed: Boolean(peer.diversity_observed),
            });
          } else {
            setPeerConsultation(null);
          }
          const artifactRaw = r.product_artifacts;
          if (artifactRaw && typeof artifactRaw === 'object') {
            const artifact = artifactRaw as Record<string, unknown>;
            setProductArtifacts({
                has_artifacts: Boolean(artifact.has_artifacts),
                created: Number.isFinite(Number(artifact.created)) ? Number(artifact.created) : 0,
                modified: Number.isFinite(Number(artifact.modified)) ? Number(artifact.modified) : 0,
                file_count: Number.isFinite(Number(artifact.file_count))
                  ? Number(artifact.file_count)
                  : (
                    (Number.isFinite(Number(artifact.created)) ? Number(artifact.created) : 0)
                    + (Number.isFinite(Number(artifact.modified)) ? Number(artifact.modified) : 0)
                  ),
                files_preview_truncated: Boolean(artifact.files_preview_truncated),
                files: Array.isArray(artifact.files)
                  ? (artifact.files as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                  : [],
                message: String(artifact.message ?? ''),
                internal_runtime_excluded: Boolean(artifact.internal_runtime_excluded),
            });
          } else {
            setProductArtifacts(null);
          }
          const operationalRaw = r.task_operational_summary;
          if (operationalRaw && typeof operationalRaw === 'object') {
            const operational = operationalRaw as Record<string, unknown>;
            const blockedReasonsRaw = Array.isArray(operational.blocked_reasons)
              ? operational.blocked_reasons
              : [];
            const sampleItemsRaw = Array.isArray(operational.sample_items)
              ? operational.sample_items
              : [];
            setOperationalSummary({
              has_actionable_items: Boolean(operational.has_actionable_items),
              active_total: Number.isFinite(Number(operational.active_total)) ? Number(operational.active_total) : 0,
              counts: typeof operational.counts === 'object' && operational.counts !== null
                ? Object.fromEntries(
                  Object.entries(operational.counts as Record<string, unknown>)
                    .map(([key, value]) => [key, Number.isFinite(Number(value)) ? Number(value) : 0]),
                )
                : {},
              blocked_reasons: blockedReasonsRaw.map((item) => {
                const row = (item && typeof item === 'object') ? item as Record<string, unknown> : {};
                return {
                  code: String(row.code ?? ''),
                  label: String(row.label ?? ''),
                  count: Number.isFinite(Number(row.count)) ? Number(row.count) : 0,
                };
              }).filter((item) => item.code.length > 0),
              sample_items: sampleItemsRaw.map((item) => {
                const row = (item && typeof item === 'object') ? item as Record<string, unknown> : {};
                return {
                  task_id: String(row.task_id ?? ''),
                  short_id: String(row.short_id ?? ''),
                  title: String(row.title ?? ''),
                  role: String(row.role ?? ''),
                  state: String(row.state ?? ''),
                  operational_state: String(row.operational_state ?? ''),
                  reason_code: String(row.reason_code ?? ''),
                  reason_label: String(row.reason_label ?? ''),
                  source_root: String(row.source_root ?? ''),
                };
              }).filter((item) => item.task_id.length > 0),
              carryover_roots: Array.isArray(operational.carryover_roots)
                ? (operational.carryover_roots as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                : [],
            });
          } else {
            setOperationalSummary(null);
          }
        } else {
          setLeadDecisions(null);
          setPeerConsultation(null);
          setProductArtifacts(null);
          setOperationalSummary(null);
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
            className={`workbench-tab ${activeTab === 'routing' ? 'is-active' : ''}`}
            onClick={() => setActiveTab('routing')}
            type="button"
          >
            <GitBranch size={13} /> Routing
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

              {leadDecisions && (
                <section className="status-section">
                  <div className="status-section-label">Lead — decisiones autónomas</div>
                  <div className="status-lead-decisions">
                    {leadDecisions.advisory_mode && (
                      <div className="status-lead-decision status-lead-decision--warn">
                        <span className="status-lead-decision-icon">⚠</span>
                        <span>
                          Advisory mode
                          {leadDecisions.advisory_reason ? `: ${leadDecisions.advisory_reason}` : ''}
                        </span>
                      </div>
                    )}
                    {leadDecisions.auto_extended_rounds > 0 && (
                      <div className="status-lead-decision status-lead-decision--info">
                        <span className="status-lead-decision-icon">+</span>
                        <span>Rounds extendidos automáticamente: +{leadDecisions.auto_extended_rounds}</span>
                      </div>
                    )}
                    {leadDecisions.lead_budget_extended && (
                      <div className="status-lead-decision status-lead-decision--info">
                        <span className="status-lead-decision-icon">+</span>
                        <span>Lead extendió presupuesto: +{leadDecisions.lead_budget_extension} rounds</span>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {peerConsultation && (peerConsultation.consulted_roles.length > 0 || peerConsultation.consulted_providers.length > 0) && (
                <section className="status-section">
                  <div className="status-section-label">Lead — consulta a pares</div>
                  <div className="status-lead-decisions">
                    <div className="status-lead-decision status-lead-decision--info">
                      <span className="status-lead-decision-icon">↔</span>
                      <span>Peers: {peerConsultation.consulted_roles.join(', ') || 'ninguno'}</span>
                    </div>
                    <div className="status-lead-decision status-lead-decision--info">
                      <span className="status-lead-decision-icon">◌</span>
                      <span>Providers: {peerConsultation.consulted_providers.join(', ') || 'ninguno'}</span>
                    </div>
                    <div className={`status-lead-decision ${peerConsultation.diversity_observed ? 'status-lead-decision--info' : 'status-lead-decision--warn'}`}>
                      <span className="status-lead-decision-icon">{peerConsultation.diversity_observed ? '✓' : '!'}</span>
                      <span>
                        Diversidad observada: {peerConsultation.diversity_observed ? 'sí' : 'no'}
                        {peerConsultation.provider_count > 0 ? ` (${peerConsultation.provider_count} provider${peerConsultation.provider_count === 1 ? '' : 's'})` : ''}
                      </span>
                    </div>
                  </div>
                </section>
              )}

              {productArtifacts && (
                <section className="status-section">
                  <div className="status-section-label">Artefactos de producto</div>
                  <div className="status-artifact-summary">
                    <div className={`status-artifact-banner ${productArtifacts.has_artifacts ? 'status-artifact-banner--ok' : 'status-artifact-banner--muted'}`}>
                      <span className="status-artifact-banner-icon">{productArtifacts.has_artifacts ? '✓' : '·'}</span>
                      <span>{productArtifacts.message || 'Esta run no genero artefactos de producto.'}</span>
                    </div>
                    {productArtifacts.has_artifacts && (
                      <>
                        <div className="status-counts">
                          <span className="status-count-ok">{productArtifacts.created} nuevos</span>
                          {productArtifacts.modified > 0 && (
                            <span className="status-count-total">{productArtifacts.modified} modificados</span>
                          )}
                        </div>
                        <div className="status-artifact-files">
                          {productArtifacts.files.slice(0, 6).map((file) => (
                            <span key={file} className="status-artifact-file">{file}</span>
                          ))}
                        </div>
                        {productArtifacts.files_preview_truncated && productArtifacts.file_count > productArtifacts.files.length && (
                          <div className="status-empty-hint">
                            +{productArtifacts.file_count - productArtifacts.files.length} mas no listados en este resumen.
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </section>
              )}

              {operationalSummary && operationalSummary.has_actionable_items && (
                <section className="status-section">
                  <div className="status-section-label">Estado operativo</div>
                  <div className="status-lead-decisions">
                    <div className="status-lead-decision status-lead-decision--info">
                      <span className="status-lead-decision-icon">≡</span>
                      <span>Activas: {operationalSummary.active_total}</span>
                    </div>
                    {(operationalSummary.counts.pending ?? 0) > 0 && (
                      <div className="status-lead-decision status-lead-decision--info">
                        <span className="status-lead-decision-icon">…</span>
                        <span>Pendientes: {operationalSummary.counts.pending}</span>
                      </div>
                    )}
                    {(operationalSummary.counts.blocked_by_dependency ?? 0) > 0 && (
                      <div className="status-lead-decision status-lead-decision--warn">
                        <span className="status-lead-decision-icon">!</span>
                        <span>Bloqueadas por dependencia: {operationalSummary.counts.blocked_by_dependency}</span>
                      </div>
                    )}
                    {(operationalSummary.counts.blocked_by_quorum ?? 0) > 0 && (
                      <div className="status-lead-decision status-lead-decision--warn">
                        <span className="status-lead-decision-icon">!</span>
                        <span>Bloqueadas por quorum: {operationalSummary.counts.blocked_by_quorum}</span>
                      </div>
                    )}
                    {(operationalSummary.counts.blocked_by_no_eligible_adapter ?? 0) > 0 && (
                      <div className="status-lead-decision status-lead-decision--warn">
                        <span className="status-lead-decision-icon">!</span>
                        <span>Bloqueadas sin adapter elegible: {operationalSummary.counts.blocked_by_no_eligible_adapter}</span>
                      </div>
                    )}
                    {(operationalSummary.counts.carried_over_from_previous_run ?? 0) > 0 && (
                      <div className="status-lead-decision status-lead-decision--info">
                        <span className="status-lead-decision-icon">↺</span>
                        <span>Arrastradas de runs previas: {operationalSummary.counts.carried_over_from_previous_run}</span>
                      </div>
                    )}
                    {operationalSummary.blocked_reasons.slice(0, 3).map((reason) => (
                      <div key={reason.code} className="status-lead-decision status-lead-decision--warn">
                        <span className="status-lead-decision-icon">·</span>
                        <span>{reason.label}: {reason.count}</span>
                      </div>
                    ))}
                    {operationalSummary.sample_items.slice(0, 4).map((item) => (
                      <div key={item.task_id} className="status-lead-decision status-lead-decision--info">
                        <span className="status-lead-decision-icon">{item.operational_state === 'carried_over_from_previous_run' ? '↺' : '•'}</span>
                        <span>
                          {item.short_id || item.task_id}
                          {item.reason_label ? ` — ${item.reason_label}` : ''}
                          {item.source_root && item.operational_state === 'carried_over_from_previous_run' ? ` (${item.source_root})` : ''}
                        </span>
                      </div>
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

          {activeTab === 'routing' && (
            <RoutingCatalogPanel workspacePath={workspacePath} />
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
