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
  authoritative_close_state?: string;
  authoritative_blockers?: Array<{ reason_code: string; reason_label: string; count: number }>;
}

interface LeadClosePolicySummary {
  authoritative_close_state: string;
  blocking_signals: string[];
  can_declare_done: boolean;
  requires_close_rewrite: boolean;
}

interface PhaseDeliveryItem {
  phase_id: string;
  role: string;
  objective: string;
  objective_missing: boolean;
  depends_on: string[];
  verdict_status: string;
  contract_status: string;
  reason_codes: string[];
  verdict_summary: string;
  delivery_summary: string;
  delivery_source: string;
  has_delivery: boolean;
  has_output: boolean;
}

const resolveWorkspaceRunState = (run: Record<string, unknown>): string => {
  const workflowRunStatus = String(run.workflow_run_status ?? '').trim().toLowerCase();
  if (workflowRunStatus) return workflowRunStatus;
  const authoritativeState = String(run.authoritative_state ?? '').trim().toLowerCase();
  if (authoritativeState) return authoritativeState;
  return String(run.status ?? run.state ?? '').trim().toLowerCase();
};

type WorkbenchTab = 'status' | 'routing' | 'files' | 'terminal';

const isTerminalWorkspaceRunState = (state: string): boolean => {
  const normalized = String(state || '').trim().toLowerCase();
  return ['completed', 'failed', 'rejected', 'cancelled', 'aborted', 'not_completed'].includes(normalized);
};

const nextStatusPanelPollDelay = (runActive: boolean, minimized: boolean, activeTab: WorkbenchTab): number => {
  if (minimized) return 20000;
  if (runActive) {
    return activeTab === 'status' ? 12000 : 15000;
  }
  return 4000;
};

export default function StatusPanel({ workspacePath, minimized, onToggleMinimize, onLoadChat }: StatusPanelProps) {
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [taskCounts, setTaskCounts] = useState({ total: 0, completed: 0, failed: 0 });
  const [leadDecisions, setLeadDecisions] = useState<LeadDecisions | null>(null);
  const [peerConsultation, setPeerConsultation] = useState<PeerConsultationSummary | null>(null);
  const [productArtifacts, setProductArtifacts] = useState<ProductArtifactsSummary | null>(null);
  const [operationalSummary, setOperationalSummary] = useState<OperationalTaskSummary | null>(null);
  const [leadClosePolicy, setLeadClosePolicy] = useState<LeadClosePolicySummary | null>(null);
  const [phaseDeliverySummary, setPhaseDeliverySummary] = useState<PhaseDeliveryItem[]>([]);
  const [activeTab, setActiveTab] = useState<WorkbenchTab>('status');
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileRefreshToken, setFileRefreshToken] = useState(0);
  const [refreshingTree, setRefreshingTree] = useState(false);
  const [runActive, setRunActive] = useState(false);

  useEffect(() => {
    setRecentRuns([]);
    const headers: Record<string, string> = workspacePath ? { 'x-workspace-path': workspacePath } : {};
    let cancelled = false;
    let timerId: ReturnType<typeof window.setTimeout> | null = null;

    const poll = async () => {
      try {
        const res = await apiFetch('/api/dashboard', { headers });
        if (!res.ok) return;
        const data = await res.json() as Record<string, unknown>;
        if (cancelled) return;
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
        if (cancelled) return;
        const run = data.last_chat_run;
        if (run && typeof run === 'object') {
          const r = run as Record<string, unknown>;
          const runState = resolveWorkspaceRunState(r);
          setRunActive(Boolean(runState) && !isTerminalWorkspaceRunState(runState));
          setRecentRuns(prev => {
            const entry: RecentRun = {
              task_id: String(r.task_id ?? '').slice(0, 22),
              state: runState,
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
              authoritative_close_state: String(operational.authoritative_close_state ?? ''),
              authoritative_blockers: Array.isArray(operational.authoritative_blockers)
                ? (operational.authoritative_blockers as unknown[])
                  .map((item) => {
                    const row = (item && typeof item === 'object') ? item as Record<string, unknown> : {};
                    return {
                      reason_code: String(row.reason_code ?? ''),
                      reason_label: String(row.reason_label ?? ''),
                      count: Number.isFinite(Number(row.count)) ? Number(row.count) : 0,
                    };
                  })
                  .filter((item) => item.reason_code.length > 0)
                : [],
            });
          } else {
            setOperationalSummary(null);
          }
          const closePolicyRaw = r.lead_close_policy;
          if (closePolicyRaw && typeof closePolicyRaw === 'object') {
            const policy = closePolicyRaw as Record<string, unknown>;
            setLeadClosePolicy({
              authoritative_close_state: String(policy.authoritative_close_state ?? '').trim(),
              blocking_signals: Array.isArray(policy.blocking_signals)
                ? (policy.blocking_signals as unknown[]).map((item) => String(item ?? '').trim()).filter((item) => item.length > 0)
                : [],
              can_declare_done: Boolean(policy.can_declare_done),
              requires_close_rewrite: Boolean(policy.requires_close_rewrite),
            });
          } else {
            setLeadClosePolicy(null);
          }
          const phaseDeliveryRaw = Array.isArray(r.phase_delivery_summary) ? r.phase_delivery_summary : [];
          setPhaseDeliverySummary(
            phaseDeliveryRaw
              .map((item) => {
                const row = (item && typeof item === 'object') ? item as Record<string, unknown> : {};
                return {
                  phase_id: String(row.phase_id ?? '').trim(),
                  role: String(row.role ?? '').trim(),
                  objective: String(row.objective ?? '').trim(),
                  objective_missing: Boolean(row.objective_missing),
                  depends_on: Array.isArray(row.depends_on)
                    ? (row.depends_on as unknown[]).map((dep) => String(dep ?? '').trim()).filter((dep) => dep.length > 0)
                    : [],
                  verdict_status: String(row.verdict_status ?? '').trim(),
                  contract_status: String(row.contract_status ?? '').trim(),
                  reason_codes: Array.isArray(row.reason_codes)
                    ? (row.reason_codes as unknown[]).map((reason) => String(reason ?? '').trim()).filter((reason) => reason.length > 0)
                    : [],
                  verdict_summary: String(row.verdict_summary ?? '').trim(),
                  delivery_summary: String(row.delivery_summary ?? '').trim(),
                  delivery_source: String(row.delivery_source ?? '').trim(),
                  has_delivery: Boolean(row.has_delivery),
                  has_output: Boolean(row.has_output),
                } satisfies PhaseDeliveryItem;
              })
              .filter((item) => item.phase_id.length > 0),
          );
        } else {
          setRunActive(false);
          setLeadDecisions(null);
          setPeerConsultation(null);
          setProductArtifacts(null);
          setOperationalSummary(null);
          setLeadClosePolicy(null);
          setPhaseDeliverySummary([]);
        }
      } catch { /* ignore */ }
    };

    const scheduleNextPoll = () => {
      if (cancelled) return;
      timerId = window.setTimeout(() => {
        void tick();
      }, nextStatusPanelPollDelay(runActive, minimized, activeTab));
    };

    const tick = async () => {
      await Promise.allSettled([poll(), pollState()]);
      scheduleNextPoll();
    };

    void tick();
    return () => {
      cancelled = true;
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
    };
  }, [workspacePath, runActive, minimized, activeTab]);

  useEffect(() => {
    setSelectedFile(null);
  }, [workspacePath]);

  const stateColor = (s: string) => {
    if (s === 'completed') return 'var(--status-green)';
    if (s.includes('fail') || s.includes('error')) return 'var(--status-red)';
    if (s === 'not_completed') return 'var(--status-red)';
    if (s.includes('reject')) return 'var(--status-red)';
    if (s.includes('waiting')) return 'var(--status-amber)';
    if (s.includes('progress') || s.includes('exhaust')) return 'var(--status-amber)';
    return 'var(--text-secondary)';
  };

  const rootCauseTone = (state: string) => {
    if (state === 'rejected') return 'status-root-cause status-root-cause--hard';
    if (state === 'not_completed' || state === 'failed') return 'status-root-cause status-root-cause--warn';
    if (state === 'eligible_for_done') return 'status-root-cause status-root-cause--ok';
    return 'status-root-cause status-root-cause--neutral';
  };

  const phaseTone = (item: PhaseDeliveryItem) => {
    if (item.contract_status === 'drift' || item.verdict_status === 'rejected') return 'status-phase-card status-phase-card--hard';
    if (item.verdict_status === 'blocked' || !item.has_delivery || item.objective_missing) return 'status-phase-card status-phase-card--warn';
    if (item.verdict_status === 'completed' || item.verdict_status === 'approved') return 'status-phase-card status-phase-card--ok';
    return 'status-phase-card status-phase-card--neutral';
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

              {leadClosePolicy && (
                <section className="status-section">
                  <div className="status-section-label">Causa autoritativa</div>
                  <div className={rootCauseTone(leadClosePolicy.authoritative_close_state)}>
                    <div className="status-root-cause-header">
                      <span className="status-root-cause-title">
                        {leadClosePolicy.authoritative_close_state === 'rejected'
                          ? 'Run rechazada'
                          : leadClosePolicy.authoritative_close_state === 'not_completed'
                            ? 'Run no completada'
                            : leadClosePolicy.authoritative_close_state === 'eligible_for_done'
                              ? 'Run elegible para cierre'
                              : 'Estado autoritativo disponible'}
                      </span>
                      <span className="status-root-cause-state">{leadClosePolicy.authoritative_close_state || 'unknown'}</span>
                    </div>
                    {leadClosePolicy.blocking_signals.length > 0 ? (
                      <>
                        <div className="status-root-cause-copy">
                          La capa autoritativa del run está señalando estos bloqueadores principales.
                        </div>
                        <div className="status-root-cause-tags">
                          {leadClosePolicy.blocking_signals.slice(0, 6).map((signal) => (
                            <span key={signal} className="status-root-cause-tag">{signal}</span>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="status-root-cause-copy">
                        No hay bloqueadores autoritativos activos en este cierre.
                      </div>
                    )}
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
                        <div>
                          <div>Sin adapter elegible: {operationalSummary.counts.blocked_by_no_eligible_adapter} tarea(s)</div>
                          <div style={{fontSize: '11px', opacity: 0.8, marginTop: 2}}>
                            Configura un provider: <code>OPENAI_API_KEY</code>, <code>ANTHROPIC_API_KEY</code> o <code>GOOGLE_API_KEY</code> en <code>.env</code> y activa <code>AITEAM_ENABLE_LIVE_API=1</code>
                          </div>
                        </div>
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

              {phaseDeliverySummary.length > 0 && (
                <section className="status-section">
                  <div className="status-section-label">Fases — contrato, veredicto y evidencia</div>
                  <div className="status-phase-list">
                    {phaseDeliverySummary.map((item) => (
                      <div key={item.phase_id} className={phaseTone(item)}>
                        <div className="status-phase-card-header">
                          <div className="status-phase-card-title">
                            <span>{item.phase_id}</span>
                            {item.role && <span className="status-phase-role">{item.role}</span>}
                          </div>
                          <div className="status-phase-badges">
                            {item.verdict_status && (
                              <span className="status-phase-badge">{item.verdict_status}</span>
                            )}
                            {item.contract_status && (
                              <span className={`status-phase-badge ${item.contract_status === 'drift' ? 'status-phase-badge--hard' : ''}`}>
                                {item.contract_status}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="status-phase-row">
                          <span className="status-phase-label">Objetivo</span>
                          <span className={item.objective_missing ? 'status-phase-value status-phase-value--missing' : 'status-phase-value'}>
                            {item.objective || 'No especificado'}
                          </span>
                        </div>
                        {item.depends_on.length > 0 && (
                          <div className="status-phase-row">
                            <span className="status-phase-label">Deps</span>
                            <span className="status-phase-value">{item.depends_on.join(', ')}</span>
                          </div>
                        )}
                        <div className="status-phase-row">
                          <span className="status-phase-label">Evidencia</span>
                          <span className={item.has_delivery ? 'status-phase-value' : 'status-phase-value status-phase-value--missing'}>
                            {item.delivery_summary || 'No hay entrega visible para esta fase'}
                          </span>
                        </div>
                        {(item.verdict_summary || item.reason_codes.length > 0) && (
                          <div className="status-phase-row">
                            <span className="status-phase-label">Veredicto</span>
                            <span className="status-phase-value">
                              {item.verdict_summary || 'Sin summary de veredicto'}
                              {item.reason_codes.length > 0 ? ` · ${item.reason_codes.join(', ')}` : ''}
                            </span>
                          </div>
                        )}
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
            <RoutingCatalogPanel workspacePath={workspacePath} autoRefreshPaused={runActive} />
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
