import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleHelp,
  Gauge,
  Layers3,
  LockKeyhole,
  RefreshCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';

import { apiFetch } from '../../lib/api';
import './ModelCatalog.css';

type StateValue = true | false | null;

interface CatalogState {
  value: StateValue;
  reason?: string | null;
  source?: string | null;
  version?: string | null;
  observed_at?: string | null;
}

interface ScoreComponent {
  value?: number | null;
  status?: string;
  reason?: string;
  source?: string;
  weight_percent?: number;
  weighted_points?: number | null;
  sample_count?: number;
  basis?: string;
  latency_ms?: number | null;
}

interface RoleScore {
  score?: number | null;
  score_range?: { minimum?: number; maximum?: number };
  confidence?: {
    value?: number;
    minimum_for_auto?: number;
    evidence_status?: string;
    seeds?: number;
    cases?: number;
    goodhart_risk?: string;
    fresh?: boolean;
    evaluated_at?: string | null;
    provider_version?: string | null;
    unmeasured_constructs?: string[];
  };
  breakdown?: Record<string, ScoreComponent>;
  hard_gates?: Record<string, { passed?: StateValue; reason?: string; source?: string }>;
  auto_eligible?: boolean;
  auto_ineligible_reasons?: string[];
  known_weight_percent?: number;
  rollout?: string;
}

interface RoleEvaluation {
  canonical_role: string;
  compatibility?: { allowed?: boolean; code?: string; reason?: string };
  evaluation?: {
    status?: string;
    evaluated_at?: string | null;
    provider_version?: string | null;
    evidence_receipts?: string[];
    diagnostic_receipts?: string[];
    diagnostic_stale_reasons?: string[];
    rerun_policy?: string | null;
    material_change_triggers?: string[];
    next_action?: string | null;
    stale_reasons?: string[];
  };
  runtime_metrics?: Record<string, unknown>;
  provenance?: {
    evaluation_receipts?: string[];
    diagnostic_receipts?: string[];
    runtime_database_ids?: string[];
    runtime_run_ids?: string[];
    metric_sources?: string[];
  };
  score?: RoleScore;
  score_inputs?: Record<string, unknown>;
  input_hash?: string;
}

interface CatalogCandidate {
  candidate_id: string;
  label?: string;
  identity: {
    profile_id: string;
    provider_org: string;
    model_vendor?: string;
    perspective_key?: string;
    channel: string;
    capacity_pool?: string;
    model_id: string;
  };
  states: Record<string, CatalogState>;
  provider_metadata?: {
    label?: string | null;
    adapter_type?: string | null;
    status?: string | null;
    data_policy?: string | null;
    privacy_note?: string | null;
    workspace_mode?: string | null;
    mcp_transport?: string | null;
    structured_output?: string | null;
  };
  model_metadata: {
    tier?: string | null;
    capability_band?: string | null;
    capabilities?: string[];
    economy?: {
      cost_class?: string;
      measurement_basis?: string;
      input_cents_per_mtok?: number | null;
      output_cents_per_mtok?: number | null;
      quota_unlimited?: boolean;
    };
    speed_class?: string | null;
    speed_source?: string | null;
    context_window_tokens?: number | null;
    price_note?: string | null;
    capability_basis?: string | null;
    probe_status?: string | null;
    probe_reason?: string | null;
    probe_version?: string | null;
    probe_evaluated_at?: string | null;
    probe_receipts?: string[];
  };
  roles: RoleEvaluation[];
  rank?: number;
  selection_reason?: string;
  role_evaluation?: RoleEvaluation;
}

interface ProviderSummary {
  profile_id: string;
  provider: string;
  channel: string;
  capacity_pool?: string | null;
  model_count: number;
  configured_count: number;
  green_count: number;
  selectable_count: number;
  blocked_count: number;
  data_policy?: string | null;
  privacy_note?: string | null;
  economy_classes?: string[];
}

interface CatalogPayload {
  success: boolean;
  schema_version: string;
  score_version: string;
  content_hash: string;
  observed_at: string;
  rollout: string;
  counts: { candidates: number; providers: number };
  providers: ProviderSummary[];
  candidates: CatalogCandidate[];
}

interface RoleCandidatesPayload {
  success: boolean;
  canonical_role: string;
  content_hash: string;
  rollout: string;
  counts: { candidates: number; auto_eligible: number };
  candidates: CatalogCandidate[];
}

interface Filters {
  query: string;
  role: string;
  provider: string;
  channel: string;
  tier: string;
  state: string;
}

interface DetailSelection {
  candidate: CatalogCandidate;
  role: string;
}

const INITIAL_FILTERS: Filters = {
  query: '',
  role: '',
  provider: '',
  channel: '',
  tier: '',
  state: '',
};

const STATE_LABELS: Record<string, string> = {
  catalogued: 'Catalogado',
  configured: 'Configurado',
  adapter_green: 'Adapter verde',
  model_verified: 'Modelo verificado',
  selectable: 'Seleccionable',
  compatible: 'Compatible',
  calibrated: 'Calibrado',
  stale: 'Evidencia stale',
  manual_only: 'Solo manual',
  blocked: 'Bloqueado',
  retired: 'Retirado',
};

const ROLE_LABELS: Record<string, string> = {
  lead: 'Lead',
  team_lead: 'Team Lead',
  lead_executor: 'Lead executor',
  architect: 'Arquitectura',
  quorum_auditor: 'Auditor quorum',
  engineer: 'Engineer',
  software_engineer: 'Software engineer',
  reviewer: 'Reviewer',
  code_reviewer: 'Code reviewer',
  qa: 'QA',
  test_designer: 'Test designer',
  mcp_operator: 'MCP operator',
  worker: 'Worker',
  file_scout: 'File scout',
  web_scout: 'Web scout',
  context_curator: 'Context curator',
};

const COMPONENT_LABELS: Record<string, string> = {
  quality: 'Calidad',
  capability: 'Capacidad',
  reliability: 'Fiabilidad',
  economy: 'Economía',
  speed: 'Velocidad',
};

function humanize(value?: string | null): string {
  if (!value) return 'No declarado';
  return value.replaceAll('_', ' ').replace(/^./, (char) => char.toUpperCase());
}

function formatObservedAt(value?: string | null): string {
  if (!value) return 'Sin fecha';
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Date(parsed).toLocaleString([], {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function scoreText(score?: number | null): string {
  return score === null || score === undefined ? '—' : score.toFixed(score % 1 ? 1 : 0);
}

function percentText(value?: number | null): string {
  return value === null || value === undefined ? '—' : `${Math.round(value)}%`;
}

function stateClass(value: StateValue): string {
  if (value === true) return 'is-yes';
  if (value === false) return 'is-no';
  return 'is-unknown';
}

function candidateRole(candidate: CatalogCandidate, role: string): RoleEvaluation | undefined {
  if (candidate.role_evaluation?.canonical_role === role) return candidate.role_evaluation;
  return candidate.roles.find((item) => item.canonical_role === role);
}

function primaryRole(candidate: CatalogCandidate): string {
  return candidate.role_evaluation?.canonical_role || candidate.roles[0]?.canonical_role || '';
}

function matchesState(candidate: CatalogCandidate, state: string): boolean {
  if (!state) return true;
  return candidate.states[state]?.value === true;
}

function ModelStateStrip({ states }: { states: Record<string, CatalogState> }) {
  const compact = ['catalogued', 'configured', 'adapter_green', 'model_verified', 'selectable'];
  return (
    <div className="model-state-strip" aria-label="Estados operativos">
      {compact.map((name) => (
        <span
          key={name}
          className={`model-state-dot ${stateClass(states[name]?.value ?? null)}`}
          title={`${STATE_LABELS[name]}: ${humanize(states[name]?.reason)}`}
          aria-label={`${STATE_LABELS[name]}: ${states[name]?.value === true ? 'sí' : states[name]?.value === false ? 'no' : 'desconocido'}`}
        />
      ))}
    </div>
  );
}

function RoleCell({
  candidate,
  role,
  onOpen,
}: {
  candidate: CatalogCandidate;
  role: string;
  onOpen: (candidate: CatalogCandidate, role: string) => void;
}) {
  const evaluation = candidateRole(candidate, role);
  if (!evaluation) return <span className="role-cell-empty" aria-label={`${ROLE_LABELS[role] || role}: sin evaluación`}>·</span>;
  const score = evaluation.score;
  const confidence = score?.confidence?.value;
  const status = evaluation.evaluation?.status || 'untested';
  const allowed = evaluation.compatibility?.allowed !== false;
  return (
    <button
      type="button"
      className={`role-score-cell ${allowed ? '' : 'is-denied'} ${score?.auto_eligible ? 'is-eligible' : ''}`}
      onClick={() => onOpen(candidate, role)}
      aria-label={`${candidate.label || candidate.identity.model_id}, ${ROLE_LABELS[role] || role}: score ${scoreText(score?.score)}, confianza ${percentText(confidence)}`}
      data-testid={`model-cell-${candidate.identity.model_id}-${role}`}
    >
      <span className="role-score-value">{scoreText(score?.score)}</span>
      <span className="role-score-confidence">{percentText(confidence)}</span>
      <span className={`role-evidence-pip status-${status}`} title={humanize(status)} />
    </button>
  );
}

function ProviderCard({ provider }: { provider: ProviderSummary }) {
  const allGreen = provider.model_count > 0 && provider.green_count === provider.model_count;
  const economy = provider.economy_classes?.[0];
  return (
    <article className={`model-provider-card ${allGreen ? 'is-green' : ''}`} data-testid={`provider-${provider.profile_id}`}>
      <header>
        <span
          className={`provider-pulse ${allGreen ? 'is-green' : ''}`}
          role="img"
          aria-label={allGreen ? 'Adapter verde' : 'Adapter no completamente verde'}
        />
        <div>
          <strong>{humanize(provider.provider)}</strong>
          <small>{provider.profile_id}</small>
        </div>
        <span className="channel-stamp">{humanize(provider.channel)}</span>
      </header>
      <div className="provider-card-counts">
        <span><strong>{provider.model_count}</strong> modelos</span>
        <span><strong>{provider.configured_count}</strong> configurados</span>
        <span><strong>{provider.green_count}</strong> verdes</span>
        <span><strong>{provider.selectable_count}</strong> seleccionables</span>
        <span className={provider.blocked_count ? 'has-blocked' : ''}><strong>{provider.blocked_count}</strong> bloqueados</span>
      </div>
      <dl>
        <div><dt>Economía</dt><dd>{humanize(economy)}</dd></div>
        <div><dt>Datos</dt><dd>{humanize(provider.data_policy)}</dd></div>
      </dl>
    </article>
  );
}

function CandidateDetail({ selection, onClose }: { selection: DetailSelection; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const { candidate, role } = selection;
  const evaluation = candidateRole(candidate, role);
  const score = evaluation?.score;
  const receipts = [
    ...(evaluation?.provenance?.evaluation_receipts || []),
    ...(evaluation?.provenance?.diagnostic_receipts || []),
  ];
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const handleDialogKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button, a[href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
      ) || [])].filter((element) => !element.hasAttribute('disabled'));
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current?.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', handleDialogKeys);
    return () => {
      window.removeEventListener('keydown', handleDialogKeys);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  return (
    <div className="model-detail-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside ref={dialogRef} className="model-detail" role="dialog" aria-modal="true" aria-labelledby="model-detail-title" data-testid="model-detail" tabIndex={-1}>
        <header className="model-detail-header">
          <div>
            <span className="eyebrow">Ficha operacional · {ROLE_LABELS[role] || humanize(role)}</span>
            <h2 id="model-detail-title">{candidate.label || candidate.identity.model_id}</h2>
            <p>{candidate.identity.provider_org} · {humanize(candidate.identity.channel)} · {candidate.identity.profile_id}</p>
          </div>
          <button ref={closeButtonRef} type="button" className="model-detail-close" onClick={onClose} aria-label="Cerrar detalle"><X size={18} /></button>
        </header>

        <section className="detail-score-hero">
          <div className="score-orbit">
            <strong>{scoreText(score?.score)}</strong>
            <span>score base</span>
          </div>
          <div className="score-hero-copy">
            <span className={`eligibility-label ${score?.auto_eligible ? 'is-eligible' : ''}`}>
              {score?.auto_eligible ? <Check size={13} /> : <LockKeyhole size={13} />}
              {score?.auto_eligible ? 'Elegible en shadow' : 'No elegible automáticamente'}
            </span>
            <p>{evaluation?.compatibility?.reason || humanize(candidate.selection_reason || score?.auto_ineligible_reasons?.[0])}</p>
            <div className="detail-inline-metrics">
              <span>Confianza <strong>{percentText(score?.confidence?.value)}</strong></span>
              <span>Cobertura <strong>{score?.known_weight_percent || 0}%</strong></span>
              <span>Evidencia <strong>{humanize(evaluation?.evaluation?.status)}</strong></span>
            </div>
          </div>
        </section>

        <section className="detail-section">
          <div className="detail-section-title"><Gauge size={15} /><h3>Desglose del score</h3></div>
          <div className="score-breakdown">
            {Object.entries(score?.breakdown || {}).map(([name, component]) => (
              <div className="breakdown-row" key={name}>
                <div><span>{COMPONENT_LABELS[name] || humanize(name)}</span><small>{component.weight_percent || 0}% del score</small></div>
                <div className="breakdown-track"><span style={{ width: `${component.value || 0}%` }} /></div>
                <strong>{scoreText(component.value)}</strong>
                <p>{humanize(component.reason)} · {humanize(component.source)}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="detail-section">
          <div className="detail-section-title"><ShieldCheck size={15} /><h3>Estados y hard gates</h3></div>
          <div className="state-ledger">
            {Object.entries(candidate.states).map(([name, state]) => (
              <div key={name} className="state-ledger-row">
                <span className={`state-ledger-icon ${stateClass(state.value)}`}>{state.value === true ? '✓' : state.value === false ? '×' : '?'}</span>
                <div><strong>{STATE_LABELS[name] || humanize(name)}</strong><small>{humanize(state.reason)}</small></div>
                <code>{state.source || 'unknown'}</code>
              </div>
            ))}
            {Object.entries(score?.hard_gates || {}).map(([name, gate]) => (
              <div key={`gate-${name}`} className="state-ledger-row is-hard-gate">
                <span className={`state-ledger-icon ${stateClass(gate.passed ?? null)}`}>{gate.passed === true ? '✓' : gate.passed === false ? '×' : '?'}</span>
                <div><strong>Gate · {humanize(name)}</strong><small>{humanize(gate.reason)}</small></div>
                <code>{gate.source || 'unknown'}</code>
              </div>
            ))}
          </div>
        </section>

        <section className="detail-section detail-grid-two">
          <div>
            <div className="detail-section-title"><Activity size={15} /><h3>Evidencia</h3></div>
            <dl className="evidence-list">
              <div><dt>Muestras</dt><dd>{score?.confidence?.seeds || 0} seeds · {score?.confidence?.cases || 0} casos</dd></div>
              <div><dt>Evaluado</dt><dd>{formatObservedAt(evaluation?.evaluation?.evaluated_at)}</dd></div>
              <div><dt>Versión</dt><dd>{evaluation?.evaluation?.provider_version || 'No observada'}</dd></div>
              <div><dt>Siguiente acción</dt><dd>{humanize(evaluation?.evaluation?.next_action)}</dd></div>
              <div><dt>Política de repetición</dt><dd>{humanize(evaluation?.evaluation?.rerun_policy)}</dd></div>
              <div><dt>Goodhart</dt><dd>{humanize(score?.confidence?.goodhart_risk)}</dd></div>
            </dl>
            {evaluation?.evaluation?.material_change_triggers?.length ? (
              <p className="detail-empty-note">
                Se reabre con: {evaluation.evaluation.material_change_triggers.map(humanize).join(' · ')}
              </p>
            ) : null}
            {receipts.length ? (
              <div className="receipt-stack">{receipts.map((receipt) => <code key={receipt}>{receipt}</code>)}</div>
            ) : <p className="detail-empty-note">Sin recibos exactos para este par.</p>}
          </div>
          <div>
            <div className="detail-section-title"><Zap size={15} /><h3>Canal y ejecución</h3></div>
            <dl className="evidence-list">
              <div><dt>Tier</dt><dd>{humanize(candidate.model_metadata.tier)}</dd></div>
              <div><dt>Economía</dt><dd>{humanize(candidate.model_metadata.economy?.cost_class)}</dd></div>
              <div><dt>Velocidad</dt><dd>{humanize(candidate.model_metadata.speed_class)}</dd></div>
              <div><dt>Probe del modelo</dt><dd>{humanize(candidate.model_metadata.probe_status)}</dd></div>
              <div><dt>Privacidad</dt><dd>{humanize(candidate.provider_metadata?.data_policy)}</dd></div>
              <div><dt>Workspace</dt><dd>{humanize(candidate.provider_metadata?.workspace_mode)}</dd></div>
            </dl>
            {candidate.model_metadata.probe_receipts?.length ? (
              <div className="receipt-stack">
                {candidate.model_metadata.probe_receipts.map((receipt) => <code key={receipt}>{receipt}</code>)}
              </div>
            ) : null}
          </div>
        </section>

        <details className="detail-raw">
          <summary>Provenance y métricas crudas</summary>
          <pre>{JSON.stringify({ runtime_metrics: evaluation?.runtime_metrics, provenance: evaluation?.provenance, score_inputs: evaluation?.score_inputs, input_hash: evaluation?.input_hash }, null, 2)}</pre>
        </details>
      </aside>
    </div>
  );
}

export function ModelCatalog() {
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [roleCandidates, setRoleCandidates] = useState<RoleCandidatesPayload | null>(null);
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS);
  const [loading, setLoading] = useState(true);
  const [roleLoading, setRoleLoading] = useState(false);
  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [detail, setDetail] = useState<DetailSelection | null>(null);
  const closeDetail = useCallback(() => setDetail(null), []);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/model-catalog');
      if (!response.ok) throw new Error(`catalog_http_${response.status}`);
      setCatalog(await response.json() as CatalogPayload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'catalog_unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadCatalog(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadCatalog, refreshKey]);

  useEffect(() => {
    if (!filters.role) {
      const timer = window.setTimeout(() => setRoleCandidates(null), 0);
      return () => window.clearTimeout(timer);
    }
    let cancelled = false;
    const loadRole = async () => {
      setRoleLoading(true);
      setRoleCandidates(null);
      setError('');
      const params = new URLSearchParams({ role: filters.role });
      if (filters.provider) params.set('provider', filters.provider);
      if (filters.channel) params.set('channel', filters.channel);
      if (filters.tier) params.set('tier', filters.tier);
      if (filters.state) params.set('state', filters.state);
      try {
        const response = await apiFetch(`/api/model-catalog/candidates?${params.toString()}`);
        if (!response.ok) throw new Error(`role_catalog_http_${response.status}`);
        const payload = await response.json() as RoleCandidatesPayload;
        if (!cancelled) setRoleCandidates(payload);
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : 'role_catalog_unavailable');
      } finally {
        if (!cancelled) setRoleLoading(false);
      }
    };
    void loadRole();
    return () => { cancelled = true; };
  }, [filters.role, filters.provider, filters.channel, filters.tier, filters.state, refreshKey]);

  const roles = useMemo(() => {
    const seen = new Set<string>();
    catalog?.candidates.forEach((candidate) => candidate.roles.forEach((role) => seen.add(role.canonical_role)));
    return [...seen].sort((a, b) => (ROLE_LABELS[a] || a).localeCompare(ROLE_LABELS[b] || b));
  }, [catalog]);

  const providers = useMemo(() => [...new Set(catalog?.providers.map((item) => item.provider) || [])].sort(), [catalog]);

  const visibleCandidates = useMemo(() => {
    const base = filters.role ? roleCandidates?.candidates || [] : catalog?.candidates || [];
    const query = filters.query.trim().toLowerCase();
    return base.filter((candidate) => {
      if (query && !`${candidate.label || ''} ${candidate.identity.model_id} ${candidate.identity.profile_id}`.toLowerCase().includes(query)) return false;
      if (!filters.role && filters.provider && candidate.identity.provider_org !== filters.provider) return false;
      if (!filters.role && filters.channel && candidate.identity.channel !== filters.channel) return false;
      if (!filters.role && filters.tier && candidate.model_metadata.tier !== filters.tier) return false;
      if (!filters.role && !matchesState(candidate, filters.state)) return false;
      return true;
    });
  }, [catalog, filters, roleCandidates]);

  const matrixRoles = filters.role ? [filters.role] : roles;
  const activeFilterCount = Object.values(filters).filter(Boolean).length;
  const autoEligible = filters.role ? roleCandidates?.counts.auto_eligible || 0 : 0;

  if (loading && !catalog) {
    return (
      <section className="model-catalog-view model-catalog-loading" aria-busy="true" data-testid="models-loading">
        <div className="catalog-scanline" />
        <Sparkles size={24} />
        <strong>Componiendo catálogo operacional</strong>
        <span>Identidad, health, evidencia y economía permanecen separados.</span>
      </section>
    );
  }

  if (error && !catalog) {
    return (
      <section className="model-catalog-view model-catalog-error" role="alert" data-testid="models-error">
        <AlertTriangle size={28} />
        <h2>No se pudo abrir el catálogo</h2>
        <p>{humanize(error)}. El equipo y el routing no han sido modificados.</p>
        <button type="button" onClick={() => setRefreshKey((value) => value + 1)}><RefreshCcw size={14} /> Reintentar</button>
      </section>
    );
  }

  if (!catalog) return null;

  return (
    <section className="model-catalog-view" data-testid="model-catalog-view">
      <header className="catalog-hero">
        <div className="catalog-hero-copy">
          <span className="eyebrow"><Layers3 size={13} /> Observatorio de capacidad</span>
          <h1>Modelos <em>por rol</em>, no por reputación</h1>
          <p>Inventario universal con evidencia operacional. Visible no significa ejecutable; un score alto nunca elude un hard gate.</p>
        </div>
        <div className="catalog-kpis" aria-label="Resumen del catálogo">
          <div><strong>{catalog.counts.candidates}</strong><span>modelos</span></div>
          <div><strong>{catalog.counts.providers}</strong><span>perfiles/canal</span></div>
          <div><strong>{filters.role ? autoEligible : '—'}</strong><span>{filters.role ? 'auto-elegibles' : 'elige un rol'}</span></div>
          <div className="kpi-shadow"><strong>SHADOW</strong><span>{catalog.score_version}</span></div>
        </div>
        <button className="catalog-refresh" type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading || roleLoading} title="Actualizar catálogo">
          <RefreshCcw size={15} className={loading || roleLoading ? 'spin' : ''} />
        </button>
      </header>

      <section className="provider-observatory" aria-label="Proveedores y canales">
        {catalog.providers.map((provider) => <ProviderCard provider={provider} key={`${provider.profile_id}:${provider.channel}`} />)}
      </section>

      <section className="catalog-workbench">
        <div className="catalog-filterbar">
          <label className="catalog-search">
            <span className="sr-only">Buscar modelo</span>
            <Search size={15} />
            <input
              value={filters.query}
              onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))}
              placeholder="Modelo, slug o perfil…"
              data-testid="model-search"
            />
          </label>
          <label><span>Rol</span><select value={filters.role} onChange={(event) => setFilters((current) => ({ ...current, role: event.target.value }))} data-testid="model-role-filter"><option value="">Todos</option>{roles.map((role) => <option key={role} value={role}>{ROLE_LABELS[role] || humanize(role)}</option>)}</select></label>
          <label><span>Proveedor</span><select value={filters.provider} onChange={(event) => setFilters((current) => ({ ...current, provider: event.target.value }))}><option value="">Todos</option>{providers.map((provider) => <option key={provider}>{provider}</option>)}</select></label>
          <label><span>Canal</span><select value={filters.channel} onChange={(event) => setFilters((current) => ({ ...current, channel: event.target.value }))}><option value="">Todos</option><option value="api">API</option><option value="subscription">Suscripción</option><option value="local">Local</option><option value="free_gateway">Gateway free</option></select></label>
          <label><span>Tier</span><select value={filters.tier} onChange={(event) => setFilters((current) => ({ ...current, tier: event.target.value }))}><option value="">Todos</option><option value="premium">Premium</option><option value="standard">Standard</option><option value="budget">Budget</option></select></label>
          <label><span>Estado</span><select value={filters.state} onChange={(event) => setFilters((current) => ({ ...current, state: event.target.value }))} data-testid="model-state-filter"><option value="">Todos</option>{Object.entries(STATE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {activeFilterCount ? <button type="button" className="clear-filters" onClick={() => setFilters(INITIAL_FILTERS)}><X size={13} /> Limpiar {activeFilterCount}</button> : null}
        </div>

        <div className="matrix-legend">
          <div><SlidersHorizontal size={14} /><strong>{visibleCandidates.length}</strong> pares visibles</div>
          <span><i className="legend-dot score-known" /> score</span>
          <span><i className="legend-dot confidence" /> confianza</span>
          <span><i className="legend-dot evidence" /> evidencia</span>
          <span className="matrix-note"><CircleHelp size={13} /> “—” significa desconocido, no cero.</span>
        </div>

        {error ? <div className="catalog-inline-error" role="alert"><AlertTriangle size={14} /> {humanize(error)}</div> : null}
        {roleLoading ? <div className="catalog-role-loading" aria-live="polite"><RefreshCcw className="spin" size={14} /> Reordenando desde la API canónica…</div> : null}

        {!visibleCandidates.length && !roleLoading ? (
          <div className="catalog-empty" data-testid="models-empty">
            <Search size={24} />
            <strong>Ningún par coincide</strong>
            <p>Prueba otro rol o conserva los modelos bloqueados visibles quitando el filtro de estado.</p>
            <button type="button" onClick={() => setFilters(INITIAL_FILTERS)}>Restablecer filtros</button>
          </div>
        ) : (
          <div className="model-matrix-scroll" tabIndex={0} aria-label="Matriz de modelos por rol">
            <table className="model-matrix" data-testid="model-matrix">
              <thead><tr><th className="sticky-model-col">Modelo operacional</th>{matrixRoles.map((role) => <th key={role}>{ROLE_LABELS[role] || humanize(role)}</th>)}</tr></thead>
              <tbody>
                {visibleCandidates.map((candidate) => (
                  <tr key={candidate.candidate_id} className={candidate.states.blocked?.value ? 'candidate-blocked' : ''} data-testid={`model-row-${candidate.identity.model_id}`}>
                    <th className="sticky-model-col">
                      <button type="button" className="model-identity-button" onClick={() => setDetail({ candidate, role: filters.role || primaryRole(candidate) })}>
                        <span className={`tier-rail tier-${candidate.model_metadata.tier || 'unknown'}`} />
                        <span className="model-identity-copy"><strong>{candidate.label || candidate.identity.model_id}</strong><small>{candidate.identity.profile_id} · {humanize(candidate.identity.channel)}</small><code>{candidate.identity.model_id}</code></span>
                        <ModelStateStrip states={candidate.states} />
                        <ChevronRight size={14} />
                      </button>
                    </th>
                    {matrixRoles.map((role) => <td key={role}><RoleCell candidate={candidate} role={role} onOpen={(item, selectedRole) => setDetail({ candidate: item, role: selectedRole })} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <footer className="catalog-footnote">
        <span><ShieldCheck size={14} /> Fuente: {catalog.schema_version}</span>
        <span>Observado {formatObservedAt(catalog.observed_at)}</span>
        <span className="catalog-hash">{catalog.content_hash.slice(0, 12)}</span>
        <span className="catalog-policy"><ArrowUpRight size={13} /> Selección contextual: API canónica activa</span>
      </footer>

      {detail ? <CandidateDetail selection={detail} onClose={closeDetail} /> : null}
    </section>
  );
}
