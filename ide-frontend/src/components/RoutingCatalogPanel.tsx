import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../lib/api';

interface RoutingProviderSummary {
  provider: string;
  adapter_count: number;
  operational_count: number;
}

interface RoutingAdapterRow {
  adapter_name: string;
  provider: string;
  model: string;
  channel: string;
  cost_tier: number;
  routing_priority: number;
  requires_approval: boolean;
  capabilities: string[];
  role_targets: string[];
  available: boolean;
  operational: boolean;
  tier: string;
  notes: string;
}

interface RoleAdapterRow {
  adapter_name: string;
  provider: string;
  model: string;
  channel: string;
  tier: string;
  configured_provider_preferred: boolean;
  configured_model_preferred: boolean;
  eligible: boolean;
  available: boolean;
  operational: boolean;
  role_targets: string[];
  blockers: string[];
}

interface RoleRoutingRow {
  role: string;
  configured_provider_order: string[];
  configured_model_order: string[];
  effective_provider_order: string[];
  primary: Record<string, unknown>;
  fallbacks: Array<Record<string, unknown>>;
  adapters: RoleAdapterRow[];
}

interface RoutingCatalogPayload {
  generated_at?: string;
  summary?: {
    role_count?: number;
    provider_count?: number;
    adapter_count?: number;
  };
  providers?: RoutingProviderSummary[];
  adapters?: RoutingAdapterRow[];
  role_matrix?: RoleRoutingRow[];
  error?: string;
}

function pillText(label: string): string {
  return label.replaceAll('_', ' ');
}

export default function RoutingCatalogPanel({ workspacePath }: { workspacePath: string }) {
  const [payload, setPayload] = useState<RoutingCatalogPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    const load = async (quiet = false) => {
      if (!quiet) {
        setRefreshing(true);
      }
      try {
        const response = await apiFetch('/api/aiteam/routing/catalog', {
          headers: workspacePath ? { 'x-workspace-path': workspacePath } : {},
        });
        const json = (await response.json()) as RoutingCatalogPayload;
        if (cancelled) return;
        setPayload(json);
        setError(String(json.error ?? ''));
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Unknown request error');
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    };

    setLoading(true);
    setPayload(null);
    setError('');
    void load(true);
    const timer = window.setInterval(() => {
      void load(true);
    }, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [workspacePath]);

  const providerRows = useMemo(() => payload?.providers || [], [payload?.providers]);
  const roleRows = useMemo(() => payload?.role_matrix || [], [payload?.role_matrix]);
  const adapterRows = useMemo(() => payload?.adapters || [], [payload?.adapters]);

  if (loading) {
    return <div className="status-empty-hint">Cargando catálogo de routing...</div>;
  }

  if (error) {
    return <div className="status-empty-hint">No se pudo cargar el catálogo de routing: {error}</div>;
  }

  return (
    <div className="routing-catalog-panel">
      <section className="status-section">
        <div className="status-section-label">Objetivo de esta vista</div>
        <div className="routing-catalog-note">
          Mostrar qué está configurado por rol, qué adapters/modelos existen de verdad en esta máquina y qué fallbacks efectivos tiene hoy el router.
        </div>
      </section>

      <section className="status-section">
        <div className="status-section-label">Resumen</div>
        <div className="routing-summary-grid">
          <div className="routing-summary-card">
            <strong>{Number(payload?.summary?.role_count || roleRows.length)}</strong>
            <span>roles</span>
          </div>
          <div className="routing-summary-card">
            <strong>{Number(payload?.summary?.provider_count || providerRows.length)}</strong>
            <span>providers</span>
          </div>
          <div className="routing-summary-card">
            <strong>{Number(payload?.summary?.adapter_count || adapterRows.length)}</strong>
            <span>adapters</span>
          </div>
        </div>
        {payload?.generated_at && (
          <div className="routing-generated-at">
            actualizado {new Date(payload.generated_at).toLocaleString()}
            {refreshing ? ' · refrescando…' : ''}
          </div>
        )}
      </section>

      <section className="status-section">
        <div className="status-section-label">Providers disponibles</div>
        <div className="routing-provider-grid">
          {providerRows.map((provider) => (
            <div key={provider.provider} className="routing-provider-card">
              <div className="routing-provider-name">{provider.provider}</div>
              <div className="routing-provider-meta">
                {provider.operational_count}/{provider.adapter_count} operativos
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="status-section">
        <div className="status-section-label">Matriz por rol</div>
        <div className="routing-role-grid">
          {roleRows.map((role) => {
            const primary = role.primary || {};
            return (
              <article key={role.role} className="routing-role-card">
                <div className="routing-role-header">
                  <div>
                    <div className="routing-role-name">{role.role}</div>
                    <div className="routing-role-subtitle">
                      configurado: {(role.configured_provider_order || []).join(' → ') || '—'}
                    </div>
                  </div>
                  <span className="routing-role-badge">
                    {(role.adapters || []).filter((item) => item.eligible).length} elegibles
                  </span>
                </div>

                <div className="routing-role-primary">
                  <span>Primario efectivo</span>
                  <strong>
                    {primary.provider && primary.model
                      ? `${String(primary.provider)}/${String(primary.model)}`
                      : 'sin primario elegible'}
                  </strong>
                </div>

                <div className="routing-role-fallbacks">
                  <span>Fallbacks</span>
                  <div className="routing-pill-row">
                    {(role.fallbacks || []).length > 0 ? (
                      (role.fallbacks || []).map((item, index) => (
                        <span key={`${role.role}-fb-${index}`} className="routing-pill">
                          {String(item.provider || '')}/{String(item.model || '')}
                        </span>
                      ))
                    ) : (
                      <span className="routing-pill routing-pill-muted">sin fallback</span>
                    )}
                  </div>
                </div>

                <details className="routing-role-details">
                  <summary>Ver adapters para este rol</summary>
                  <div className="routing-adapter-list">
                    {(role.adapters || []).map((adapter) => (
                      <div key={`${role.role}-${adapter.adapter_name}`} className="routing-adapter-row">
                        <div className="routing-adapter-main">
                          <div className="routing-adapter-title">
                            {adapter.provider}/{adapter.model}
                          </div>
                          <div className="routing-adapter-meta">
                            <span className={`routing-state-pill ${adapter.eligible ? 'is-ok' : 'is-muted'}`}>
                              {adapter.eligible ? 'elegible' : 'bloqueado'}
                            </span>
                            <span>{adapter.channel}</span>
                            {adapter.tier && <span>{adapter.tier}</span>}
                            {adapter.configured_provider_preferred && <span>provider preferido</span>}
                            {adapter.configured_model_preferred && <span>modelo preferido</span>}
                          </div>
                        </div>
                        {adapter.blockers.length > 0 && (
                          <div className="routing-adapter-blockers">
                            {adapter.blockers.map((blocker) => (
                              <span key={`${role.role}-${adapter.adapter_name}-${blocker}`} className="routing-pill routing-pill-warn">
                                {pillText(blocker)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              </article>
            );
          })}
        </div>
      </section>

      <section className="status-section">
        <div className="status-section-label">Adapters registrados</div>
        <details className="routing-role-details">
          <summary>Ver inventario completo</summary>
          <div className="routing-adapter-list">
            {adapterRows.map((adapter) => (
              <div key={adapter.adapter_name} className="routing-adapter-row">
                <div className="routing-adapter-main">
                  <div className="routing-adapter-title">{adapter.adapter_name}</div>
                  <div className="routing-adapter-meta">
                    <span>{adapter.provider}/{adapter.model}</span>
                    <span>{adapter.channel}</span>
                    {adapter.tier && <span>{adapter.tier}</span>}
                    <span>cost {adapter.cost_tier}</span>
                    {adapter.role_targets.length > 0 && <span>roles {adapter.role_targets.join(', ')}</span>}
                  </div>
                </div>
                {adapter.notes && <div className="routing-catalog-note">{adapter.notes}</div>}
              </div>
            ))}
          </div>
        </details>
      </section>
    </div>
  );
}
