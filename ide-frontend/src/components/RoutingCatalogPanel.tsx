import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '../lib/api';

interface RoleOverridePayload {
  providers?: string[] | null;
  models?: string[] | null;
  primary_provider?: string | null;
  excluded_providers?: string[];
}

interface EditableRoleOverride {
  providers: string[];
  models: string[];
  primary_provider: string;
  excluded_providers: string[];
}

interface DraftImpactSummary {
  dirty: boolean;
  changes: string[];
  errors: string[];
  warnings: string[];
  estimated_primary: string;
  estimated_fallbacks: string[];
  eligible_count: number;
}

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
  supports_tools?: boolean;
  supports_streaming?: boolean;
  supports_vision?: boolean;
  supports_thinking?: boolean;
}

interface RoutingBlockerDetail {
  code: string;
  label: string;
  reason: string;
  severity: string;
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
  blocker_details?: RoutingBlockerDetail[];
}

interface RoleRoutingRow {
  role: string;
  defaults?: {
    providers?: string[];
    models?: string[];
  };
  override_local?: RoleOverridePayload | null;
  effective?: {
    primary?: Record<string, unknown> | null;
    fallbacks?: Array<Record<string, unknown>>;
  };
  configured_provider_order: string[];
  configured_model_order: string[];
  effective_provider_order: string[];
  configured_vs_effective_gap?: boolean;
  eligibility_summary?: {
    eligible_count?: number;
    blocked_count?: number;
    available_count?: number;
    operational_count?: number;
  };
  primary_resolution?: {
    status?: string;
    reason?: string;
    strict_role_policy?: boolean;
  };
  primary: Record<string, unknown>;
  fallbacks: Array<Record<string, unknown>>;
  adapters: RoleAdapterRow[];
}

interface RoutingCatalogPayload {
  payload_version?: number | string;
  generated_at?: string;
  summary?: {
    role_count?: number;
    provider_count?: number;
    adapter_count?: number;
    operational_provider_count?: number;
  };
  policy?: {
    source?: string;
    override_local_present?: boolean;
    override_local?: Record<string, unknown> | null;
    preferred_subscription_providers?: string[];
    preferred_api_providers?: string[];
    enforce_role_model_preferences?: boolean;
    strict_role_policy_environments?: string[];
  };
  providers?: RoutingProviderSummary[];
  adapters?: RoutingAdapterRow[];
  role_matrix?: RoleRoutingRow[];
  error?: string;
}

function pillText(label: string): string {
  return label.replaceAll('_', ' ');
}

function normalizeList(values: string[] | null | undefined): string[] {
  return (values || [])
    .map((item) => String(item || '').trim().toLowerCase())
    .filter(Boolean)
    .filter((item, index, array) => array.indexOf(item) === index);
}

function parseCommaSeparatedList(value: string): string[] {
  return normalizeList(value.split(','));
}

function editableOverrideFromPayload(payload?: RoleOverridePayload | null): EditableRoleOverride {
  return {
    providers: normalizeList(payload?.providers || []),
    models: normalizeList(payload?.models || []),
    primary_provider: String(payload?.primary_provider || '').trim().toLowerCase(),
    excluded_providers: normalizeList(payload?.excluded_providers || []),
  };
}

function serializeEditableOverride(
  payload: EditableRoleOverride,
): RoleOverridePayload | null {
  const providers = normalizeList(payload.providers);
  const models = normalizeList(payload.models);
  const primaryProvider = String(payload.primary_provider || '').trim().toLowerCase();
  const excludedProviders = normalizeList(payload.excluded_providers);
  if (!providers.length && !models.length && !primaryProvider && !excludedProviders.length) {
    return null;
  }
  return {
    providers: providers.length ? providers : null,
    models: models.length ? models : null,
    primary_provider: primaryProvider || null,
    excluded_providers: excludedProviders,
  };
}

function formatOverridePreview(payload?: RoleOverridePayload | null): string {
  if (!payload) return 'sin override local';
  const segments: string[] = [];
  if ((payload.providers || []).length) {
    segments.push(`providers ${(payload.providers || []).join(' → ')}`);
  }
  if ((payload.models || []).length) {
    segments.push(`models ${(payload.models || []).join(' → ')}`);
  }
  if (payload.primary_provider) {
    segments.push(`primario ${payload.primary_provider}`);
  }
  if ((payload.excluded_providers || []).length) {
    segments.push(`excluye ${(payload.excluded_providers || []).join(', ')}`);
  }
  return segments.join(' · ') || 'sin override local';
}

function normalizeOverridePayload(payload?: RoleOverridePayload | null): RoleOverridePayload | null {
  return serializeEditableOverride(editableOverrideFromPayload(payload));
}

function listEquals(left: string[] | null | undefined, right: string[] | null | undefined): boolean {
  const leftNormalized = normalizeList(left || []);
  const rightNormalized = normalizeList(right || []);
  if (leftNormalized.length !== rightNormalized.length) return false;
  return leftNormalized.every((item, index) => item === rightNormalized[index]);
}

function sameOverride(
  left?: RoleOverridePayload | null,
  right?: RoleOverridePayload | null,
): boolean {
  const normalizedLeft = normalizeOverridePayload(left);
  const normalizedRight = normalizeOverridePayload(right);
  if (!normalizedLeft && !normalizedRight) return true;
  if (!normalizedLeft || !normalizedRight) return false;
  return (
    listEquals(normalizedLeft.providers, normalizedRight.providers) &&
    listEquals(normalizedLeft.models, normalizedRight.models) &&
    String(normalizedLeft.primary_provider || '') === String(normalizedRight.primary_provider || '') &&
    listEquals(normalizedLeft.excluded_providers, normalizedRight.excluded_providers)
  );
}

function providerUniverseForRole(role: RoleRoutingRow): string[] {
  return Array.from(
    new Set(
      [
        ...(role.defaults?.providers || []),
        ...(role.configured_provider_order || []),
        ...(role.effective_provider_order || []),
        ...(role.adapters || []).map((adapter) => adapter.provider),
      ]
        .map((item) => String(item || '').trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function modelUniverseForRole(role: RoleRoutingRow): string[] {
  return Array.from(
    new Set(
      [
        ...(role.defaults?.models || []),
        ...(role.configured_model_order || []),
        ...(role.adapters || []).map((adapter) => adapter.model),
      ]
        .map((item) => String(item || '').trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function buildDraftImpact(role: RoleRoutingRow, draft: EditableRoleOverride): DraftImpactSummary {
  const currentOverride = normalizeOverridePayload(role.override_local);
  const draftOverride = serializeEditableOverride(draft);
  const providerUniverse = providerUniverseForRole(role);
  const modelUniverse = modelUniverseForRole(role);
  const excludedProviders = new Set(normalizeList(draft.excluded_providers));
  const preferredProviders = normalizeList(
    draft.providers.length ? draft.providers : role.configured_provider_order,
  );
  const preferredModels = normalizeList(
    draft.models.length ? draft.models : role.configured_model_order,
  );
  const primaryProvider = String(draft.primary_provider || '').trim().toLowerCase();
  const errors: string[] = [];
  const warnings: string[] = [];
  const changes: string[] = [];

  const unknownProviders = draft.providers.filter((provider) => !providerUniverse.includes(provider));
  const unknownModels = draft.models.filter((model) => !modelUniverse.includes(model));
  if (unknownProviders.length) {
    errors.push(`providers desconocidos: ${unknownProviders.join(', ')}`);
  }
  if (unknownModels.length) {
    errors.push(`models desconocidos: ${unknownModels.join(', ')}`);
  }
  if (primaryProvider && excludedProviders.has(primaryProvider)) {
    errors.push(`primary_provider "${primaryProvider}" no puede estar excluido`);
  }
  if (primaryProvider && !providerUniverse.includes(primaryProvider)) {
    errors.push(`primary_provider "${primaryProvider}" no existe para este rol`);
  }

  const draftCandidates = (role.adapters || []).filter(
    (adapter) => !excludedProviders.has(String(adapter.provider || '').trim().toLowerCase()),
  );
  if (draftCandidates.length === 0) {
    errors.push('el override deja al rol sin providers candidatos');
  }

  const eligibleCandidates = draftCandidates
    .filter((adapter) => Boolean(adapter.eligible))
    .sort((left, right) => {
      const leftProvider = String(left.provider || '').trim().toLowerCase();
      const rightProvider = String(right.provider || '').trim().toLowerCase();
      const leftModel = String(left.model || '').trim().toLowerCase();
      const rightModel = String(right.model || '').trim().toLowerCase();
      const leftPrimaryScore = primaryProvider && leftProvider === primaryProvider ? 0 : 1;
      const rightPrimaryScore = primaryProvider && rightProvider === primaryProvider ? 0 : 1;
      if (leftPrimaryScore !== rightPrimaryScore) return leftPrimaryScore - rightPrimaryScore;
      const leftProviderIndex = preferredProviders.indexOf(leftProvider);
      const rightProviderIndex = preferredProviders.indexOf(rightProvider);
      const leftProviderRank = leftProviderIndex >= 0 ? leftProviderIndex : preferredProviders.length + 10;
      const rightProviderRank = rightProviderIndex >= 0 ? rightProviderIndex : preferredProviders.length + 10;
      if (leftProviderRank !== rightProviderRank) return leftProviderRank - rightProviderRank;
      const leftModelIndex = preferredModels.indexOf(leftModel);
      const rightModelIndex = preferredModels.indexOf(rightModel);
      const leftModelRank = leftModelIndex >= 0 ? leftModelIndex : preferredModels.length + 10;
      const rightModelRank = rightModelIndex >= 0 ? rightModelIndex : preferredModels.length + 10;
      if (leftModelRank !== rightModelRank) return leftModelRank - rightModelRank;
      if (left.operational !== right.operational) return left.operational ? -1 : 1;
      if (left.available !== right.available) return left.available ? -1 : 1;
      return `${left.provider}/${left.model}`.localeCompare(`${right.provider}/${right.model}`);
    });

  const estimatedPrimary =
    eligibleCandidates.length > 0
      ? `${eligibleCandidates[0].provider}/${eligibleCandidates[0].model}`
      : 'sin primario elegible';
  const estimatedFallbacks = eligibleCandidates
    .slice(1, 5)
    .map((adapter) => `${adapter.provider}/${adapter.model}`);

  if (!eligibleCandidates.length) {
    errors.push('el override deja al rol sin ruta elegible');
  }
  if (eligibleCandidates.length === 1) {
    warnings.push('solo queda una ruta elegible; no habra fallback real');
  }

  if (!sameOverride(currentOverride, draftOverride)) {
    if (!listEquals(currentOverride?.providers, draftOverride?.providers)) {
      changes.push(
        `providers ${currentOverride?.providers?.join(' → ') || 'inherit'} → ${draftOverride?.providers?.join(' → ') || 'inherit'}`,
      );
    }
    if (!listEquals(currentOverride?.models, draftOverride?.models)) {
      changes.push(
        `models ${currentOverride?.models?.join(' → ') || 'inherit'} → ${draftOverride?.models?.join(' → ') || 'inherit'}`,
      );
    }
    if (String(currentOverride?.primary_provider || '') !== String(draftOverride?.primary_provider || '')) {
      changes.push(
        `primario ${String(currentOverride?.primary_provider || 'inherit')} → ${String(draftOverride?.primary_provider || 'inherit')}`,
      );
    }
    if (!listEquals(currentOverride?.excluded_providers, draftOverride?.excluded_providers)) {
      changes.push(
        `exclusiones ${currentOverride?.excluded_providers?.join(', ') || 'ninguna'} → ${draftOverride?.excluded_providers?.join(', ') || 'ninguna'}`,
      );
    }
  }

  if (primaryProvider && !eligibleCandidates.some((adapter) => adapter.provider === primaryProvider)) {
    warnings.push(`"${primaryProvider}" queda como primario preferido, pero hoy no tiene ruta elegible`);
  }

  return {
    dirty: !sameOverride(currentOverride, draftOverride),
    changes,
    errors,
    warnings,
    estimated_primary: estimatedPrimary,
    estimated_fallbacks: estimatedFallbacks,
    eligible_count: eligibleCandidates.length,
  };
}

function roleMatchesFilters(
  role: RoleRoutingRow,
  query: string,
  providerFilter: string,
  channelFilter: string,
  statusFilter: string,
): boolean {
  const normalizedQuery = query.trim().toLowerCase();
  const adapters = role.adapters || [];
  const queryMatches =
    !normalizedQuery ||
    role.role.toLowerCase().includes(normalizedQuery) ||
    adapters.some((adapter) =>
      [adapter.provider, adapter.model, adapter.adapter_name]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(normalizedQuery),
    );
  if (!queryMatches) return false;
  if (providerFilter !== 'all' && !adapters.some((adapter) => adapter.provider === providerFilter)) {
    return false;
  }
  if (channelFilter !== 'all' && !adapters.some((adapter) => adapter.channel === channelFilter)) {
    return false;
  }
  if (statusFilter === 'eligible' && !adapters.some((adapter) => adapter.eligible)) {
    return false;
  }
  if (statusFilter === 'blocked' && !adapters.some((adapter) => !adapter.eligible)) {
    return false;
  }
  if (statusFilter === 'degraded' && !adapters.some((adapter) => adapter.available && !adapter.operational)) {
    return false;
  }
  return true;
}

export default function RoutingCatalogPanel({ workspacePath }: { workspacePath: string }) {
  const [payload, setPayload] = useState<RoutingCatalogPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [editMode, setEditMode] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [pendingOverrides, setPendingOverrides] = useState<Record<string, EditableRoleOverride>>(
    {},
  );
  const [query, setQuery] = useState('');
  const [providerFilter, setProviderFilter] = useState('all');
  const [channelFilter, setChannelFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setPayload(null);
      setError('');
      setSaveStatus('');
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

    void load();
    return () => {
      cancelled = true;
    };
  }, [workspacePath]);

  useEffect(() => {
    if (editMode) return undefined;
    const timer = window.setInterval(() => {
      void refreshCatalog();
    }, 15000);
    return () => {
      window.clearInterval(timer);
    };
  }, [workspacePath, editMode]);

  const providerRows = useMemo(() => payload?.providers || [], [payload?.providers]);
  const roleRows = useMemo(() => payload?.role_matrix || [], [payload?.role_matrix]);
  const adapterRows = useMemo(() => payload?.adapters || [], [payload?.adapters]);
  const draftImpactByRole = useMemo(() => {
    const nextState: Record<string, DraftImpactSummary> = {};
    for (const role of roleRows) {
      const draft = pendingOverrides[role.role] || editableOverrideFromPayload(role.override_local);
      nextState[role.role] = buildDraftImpact(role, draft);
    }
    return nextState;
  }, [roleRows, pendingOverrides]);

  const dirtyRoleNames = useMemo(
    () =>
      roleRows
        .filter((role) => draftImpactByRole[role.role]?.dirty)
        .map((role) => role.role),
    [roleRows, draftImpactByRole],
  );
  const draftErrorCount = useMemo(
    () => dirtyRoleNames.reduce((total, roleName) => total + (draftImpactByRole[roleName]?.errors.length || 0), 0),
    [dirtyRoleNames, draftImpactByRole],
  );
  const draftWarningCount = useMemo(
    () => dirtyRoleNames.reduce((total, roleName) => total + (draftImpactByRole[roleName]?.warnings.length || 0), 0),
    [dirtyRoleNames, draftImpactByRole],
  );

  useEffect(() => {
    if (editMode) return;
    const nextState: Record<string, EditableRoleOverride> = {};
    for (const role of roleRows) {
      nextState[role.role] = editableOverrideFromPayload(role.override_local);
    }
    setPendingOverrides(nextState);
    setValidationErrors([]);
  }, [roleRows, editMode]);

  const providerOptions = useMemo(
    () => ['all', ...providerRows.map((provider) => provider.provider)],
    [providerRows],
  );
  const channelOptions = useMemo(() => {
    const channels = new Set<string>();
    for (const adapter of adapterRows) {
      if (adapter.channel) channels.add(adapter.channel);
    }
    return ['all', ...Array.from(channels).sort()];
  }, [adapterRows]);

  const filteredRoles = useMemo(
    () =>
      roleRows.filter((role) =>
        roleMatchesFilters(role, query, providerFilter, channelFilter, statusFilter),
      ),
    [roleRows, query, providerFilter, channelFilter, statusFilter],
  );

  const filteredAdapters = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return adapterRows.filter((adapter) => {
      if (providerFilter !== 'all' && adapter.provider !== providerFilter) return false;
      if (channelFilter !== 'all' && adapter.channel !== channelFilter) return false;
      if (statusFilter === 'eligible') return false;
      if (statusFilter === 'blocked') return false;
      if (statusFilter === 'degraded' && !(adapter.available && !adapter.operational)) return false;
      if (!normalizedQuery) return true;
      return [adapter.adapter_name, adapter.provider, adapter.model]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(normalizedQuery);
    });
  }, [adapterRows, query, providerFilter, channelFilter, statusFilter]);

  const refreshCatalog = async () => {
    setRefreshing(true);
    try {
      const response = await apiFetch('/api/aiteam/routing/catalog', {
        headers: workspacePath ? { 'x-workspace-path': workspacePath } : {},
      });
      const json = (await response.json()) as RoutingCatalogPayload;
      setPayload(json);
      setError(String(json.error ?? ''));
      return json;
    } finally {
      setRefreshing(false);
    }
  };

  const updatePendingRole = (
    roleName: string,
    updater: (current: EditableRoleOverride) => EditableRoleOverride,
  ) => {
    setPendingOverrides((current) => ({
      ...current,
      [roleName]: updater(current[roleName] || editableOverrideFromPayload(null)),
    }));
  };

  const handleToggleEditMode = () => {
    if (editMode) {
      const resetState: Record<string, EditableRoleOverride> = {};
      for (const role of roleRows) {
        resetState[role.role] = editableOverrideFromPayload(role.override_local);
      }
      setPendingOverrides(resetState);
      setValidationErrors([]);
      setSaveStatus('');
      setEditMode(false);
      return;
    }
    setValidationErrors([]);
    setSaveStatus('');
    setEditMode(true);
  };

  const handleSaveOverrides = async () => {
    const localErrors = dirtyRoleNames.flatMap((roleName) =>
      (draftImpactByRole[roleName]?.errors || []).map((item) => `${roleName}: ${item}`),
    );
    if (localErrors.length > 0) {
      setValidationErrors(localErrors);
      setSaveStatus('');
      return;
    }
    setRefreshing(true);
    setValidationErrors([]);
    setSaveStatus('');
    try {
      const overridesByRole = Object.fromEntries(
        Object.entries(pendingOverrides)
          .map(([roleName, override]) => [roleName, serializeEditableOverride(override)] as const)
          .filter((entry): entry is [string, RoleOverridePayload] => Boolean(entry[1])),
      );
      const response = await apiFetch('/api/aiteam/routing/overrides', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          ...(workspacePath ? { 'x-workspace-path': workspacePath } : {}),
        },
        body: JSON.stringify({ overrides_by_role: overridesByRole }),
      });
      const json = (await response.json()) as {
        detail?: { errors?: string[] };
      };
      if (!response.ok) {
        const errors = json?.detail?.errors || ['No se pudo guardar el override local.'];
        setValidationErrors(errors);
        return;
      }
      await refreshCatalog();
      setEditMode(false);
      setSaveStatus(`Overrides locales guardados para ${dirtyRoleNames.length} rol(es).`);
    } catch (err) {
      setValidationErrors([err instanceof Error ? err.message : 'Error desconocido guardando overrides.']);
    } finally {
      setRefreshing(false);
    }
  };

  const handleResetOverrides = async () => {
    setRefreshing(true);
    setValidationErrors([]);
    setSaveStatus('');
    try {
      const response = await apiFetch('/api/aiteam/routing/overrides', {
        method: 'DELETE',
        headers: workspacePath ? { 'x-workspace-path': workspacePath } : {},
      });
      if (!response.ok) {
        const json = (await response.json()) as { detail?: { errors?: string[] } };
        setValidationErrors(json?.detail?.errors || ['No se pudo resetear el override local.']);
        return;
      }
      await refreshCatalog();
      setEditMode(false);
      setSaveStatus('Overrides locales reseteados.');
    } catch (err) {
      setValidationErrors([err instanceof Error ? err.message : 'Error desconocido reseteando overrides.']);
    } finally {
      setRefreshing(false);
    }
  };

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
          Mostrar qué está configurado por rol, qué adapters/modelos existen de verdad en esta máquina y por qué el router elige o bloquea una ruta antes de abrir la fase editable.
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
        <div className="routing-generated-at">
          versión {payload?.payload_version || 'desconocida'}
          {payload?.generated_at ? ` · actualizado ${new Date(payload.generated_at).toLocaleString()}` : ''}
          {refreshing ? ' · refrescando…' : ''}
        </div>
      </section>

      <section className="status-section">
        <div className="status-section-label">Política activa</div>
        <div className="routing-toolbar">
          <div className="routing-catalog-note">
            Modo actual: {editMode ? 'edición local' : 'inspección'}
          </div>
          <div className="routing-toolbar-actions">
            <button type="button" className="routing-action-btn" onClick={handleToggleEditMode}>
              {editMode ? 'Cancelar edición' : 'Editar overrides'}
            </button>
            <button
              type="button"
              className="routing-action-btn routing-action-btn-primary"
              onClick={handleSaveOverrides}
              disabled={!editMode || refreshing || dirtyRoleNames.length === 0}
            >
              Guardar
            </button>
            <button
              type="button"
              className="routing-action-btn routing-action-btn-danger"
              onClick={handleResetOverrides}
              disabled={refreshing}
            >
              Reset local
            </button>
          </div>
        </div>
        <div className="routing-policy-grid">
          <div className="routing-policy-card">
            <span>source</span>
            <strong>{payload?.policy?.source || 'unknown'}</strong>
          </div>
          <div className="routing-policy-card">
            <span>override local</span>
            <strong>{payload?.policy?.override_local_present ? 'sí' : 'no'}</strong>
          </div>
          <div className="routing-policy-card">
            <span>strict role policy</span>
            <strong>{payload?.policy?.enforce_role_model_preferences ? 'on' : 'off'}</strong>
          </div>
        </div>
        <div className="routing-catalog-note">
          Subscription: {(payload?.policy?.preferred_subscription_providers || []).join(' → ') || '—'}
        </div>
        <div className="routing-catalog-note">
          API: {(payload?.policy?.preferred_api_providers || []).join(' → ') || '—'}
        </div>
        {editMode ? (
          <div className="routing-draft-summary">
            <div className="routing-draft-summary-card">
              <span>roles editados</span>
              <strong>{dirtyRoleNames.length}</strong>
            </div>
            <div className="routing-draft-summary-card">
              <span>errores locales</span>
              <strong>{draftErrorCount}</strong>
            </div>
            <div className="routing-draft-summary-card">
              <span>alertas</span>
              <strong>{draftWarningCount}</strong>
            </div>
          </div>
        ) : null}
        {editMode && dirtyRoleNames.length > 0 ? (
          <div className="routing-draft-role-list">
            {dirtyRoleNames.map((roleName) => (
              <span key={roleName} className="routing-pill routing-pill-info">
                {roleName}
              </span>
            ))}
          </div>
        ) : null}
        {saveStatus ? <div className="routing-save-feedback">{saveStatus}</div> : null}
        {validationErrors.length > 0 ? (
          <div className="routing-validation-list">
            {validationErrors.map((item) => (
              <div key={item} className="routing-validation-item">
                {item}
              </div>
            ))}
          </div>
        ) : null}
      </section>

      <section className="status-section">
        <div className="status-section-label">Filtros</div>
        <div className="routing-filter-grid">
          <label className="routing-filter-field">
            <span>Búsqueda</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="rol, provider, modelo, adapter..."
            />
          </label>
          <label className="routing-filter-field">
            <span>Provider</span>
            <select value={providerFilter} onChange={(event) => setProviderFilter(event.target.value)}>
              {providerOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="routing-filter-field">
            <span>Canal</span>
            <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
              {channelOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="routing-filter-field">
            <span>Estado</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">all</option>
              <option value="eligible">eligible</option>
              <option value="blocked">blocked</option>
              <option value="degraded">degraded</option>
            </select>
          </label>
        </div>
        <div className="routing-catalog-note">
          {filteredRoles.length}/{roleRows.length} roles visibles · {filteredAdapters.length}/{adapterRows.length} adapters visibles
        </div>
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
        {filteredRoles.length === 0 ? (
          <div className="status-empty-hint">Ningún rol coincide con los filtros actuales.</div>
        ) : (
          <div className="routing-role-grid">
            {filteredRoles.map((role) => {
              const primary = role.primary || {};
              const summary = role.eligibility_summary || {};
              const primaryResolution = role.primary_resolution || {};
              const currentOverride = pendingOverrides[role.role] || editableOverrideFromPayload(role.override_local);
              const impact = draftImpactByRole[role.role] || buildDraftImpact(role, currentOverride);
              const providerUniverse = providerUniverseForRole(role);
              return (
                <article key={role.role} className="routing-role-card">
                  <div className="routing-role-header">
                    <div>
                      <div className="routing-role-name">{role.role}</div>
                      <div className="routing-role-subtitle">
                        defaults: {(role.defaults?.providers || []).join(' → ') || '—'}
                      </div>
                      <div className="routing-role-subtitle">
                        configurado: {(role.configured_provider_order || []).join(' → ') || '—'}
                      </div>
                      <div className="routing-role-subtitle">
                        efectivo: {(role.effective_provider_order || []).join(' → ') || '—'}
                      </div>
                    </div>
                    <div className="routing-pill-row">
                      {role.configured_vs_effective_gap ? (
                        <span className="routing-pill routing-pill-warn">gap config/efectivo</span>
                      ) : (
                        <span className="routing-pill">config alineada</span>
                      )}
                      <span className="routing-role-badge">
                        {Number(summary.eligible_count || 0)} elegibles
                      </span>
                    </div>
                  </div>

                  <div className="routing-role-primary">
                    <span>Primario efectivo</span>
                    <strong>
                      {primary.provider && primary.model
                        ? `${String(primary.provider)}/${String(primary.model)}`
                        : 'sin primario elegible'}
                    </strong>
                    <div className="routing-catalog-note">
                      {pillText(String(primaryResolution.reason || 'no_eligible_adapter'))}
                    </div>
                  </div>

                  <div className="routing-override-preview">
                    <span>Override local</span>
                    <strong>{formatOverridePreview(role.override_local)}</strong>
                  </div>

                  <div className="routing-role-stats">
                    <span>{Number(summary.eligible_count || 0)} elegibles</span>
                    <span>{Number(summary.blocked_count || 0)} bloqueados</span>
                    <span>{Number(summary.operational_count || 0)} operativos</span>
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

                  {editMode ? (
                    <div className="routing-editor-card">
                      <div className="routing-editor-header">
                        <div>
                          <strong>Edicion local</strong>
                          <div className="routing-inline-note">
                            Override reversible solo para esta maquina/proyecto.
                          </div>
                        </div>
                        <button
                          type="button"
                          className="routing-action-btn"
                          onClick={() =>
                            updatePendingRole(role.role, () => editableOverrideFromPayload(null))
                          }
                        >
                          Limpiar rol
                        </button>
                      </div>
                      <div className="routing-editor-grid">
                        <label className="routing-editor-field">
                          <span>Providers override</span>
                          <input
                            value={currentOverride.providers.join(', ')}
                            onChange={(event) =>
                              updatePendingRole(role.role, (current) => ({
                                ...current,
                                providers: parseCommaSeparatedList(event.target.value),
                              }))
                            }
                            placeholder={(role.configured_provider_order || []).join(', ') || 'inherit'}
                          />
                        </label>
                        <label className="routing-editor-field">
                          <span>Models override</span>
                          <input
                            value={currentOverride.models.join(', ')}
                            onChange={(event) =>
                              updatePendingRole(role.role, (current) => ({
                                ...current,
                                models: parseCommaSeparatedList(event.target.value),
                              }))
                            }
                            placeholder={(role.configured_model_order || []).join(', ') || 'inherit'}
                          />
                        </label>
                        <label className="routing-editor-field">
                          <span>Primary provider</span>
                          <select
                            value={currentOverride.primary_provider}
                            onChange={(event) =>
                              updatePendingRole(role.role, (current) => ({
                                ...current,
                                primary_provider: event.target.value,
                              }))
                            }
                          >
                            <option value="">inherit</option>
                            {providerUniverse.map((provider) => (
                              <option key={`${role.role}-${provider}`} value={provider}>
                                {provider}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <div className="routing-inline-note">
                        Deja providers/models vacíos para heredar la política actual del repo.
                      </div>
                      <div className="routing-editor-field">
                        <span>Excluded providers</span>
                        <div className="routing-checkbox-grid">
                          {providerUniverse.length > 0 ? (
                            providerUniverse.map((provider) => {
                              const checked = currentOverride.excluded_providers.includes(provider);
                              return (
                                <label key={`${role.role}-exclude-${provider}`} className="routing-checkbox">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() =>
                                      updatePendingRole(role.role, (current) => ({
                                        ...current,
                                        excluded_providers: checked
                                          ? current.excluded_providers.filter((item) => item !== provider)
                                          : normalizeList([...current.excluded_providers, provider]),
                                      }))
                                    }
                                  />
                                  <span>{provider}</span>
                                </label>
                              );
                            })
                          ) : (
                            <div className="routing-catalog-note">Sin providers detectados para este rol.</div>
                          )}
                        </div>
                      </div>
                      {impact.dirty ? (
                        <div className="routing-diff-card">
                          <div className="routing-diff-title">Cambios pendientes</div>
                          <div className="routing-pill-row">
                            {impact.changes.map((item) => (
                              <span key={`${role.role}-${item}`} className="routing-pill routing-pill-info">
                                {item}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="routing-inline-note">Sin cambios pendientes para este rol.</div>
                      )}
                      <div className="routing-impact-grid">
                        <div className="routing-impact-card">
                          <span>Preview local</span>
                          <strong>{formatOverridePreview(serializeEditableOverride(currentOverride))}</strong>
                        </div>
                        <div className="routing-impact-card">
                          <span>Primario estimado</span>
                          <strong>{impact.estimated_primary}</strong>
                        </div>
                        <div className="routing-impact-card">
                          <span>Fallbacks estimados</span>
                          <strong>{impact.estimated_fallbacks.join(' · ') || 'sin fallback'}</strong>
                        </div>
                      </div>
                      {impact.warnings.length > 0 ? (
                        <div className="routing-warning-list">
                          {impact.warnings.map((item) => (
                            <div key={`${role.role}-warn-${item}`} className="routing-warning-item">
                              {item}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {impact.errors.length > 0 ? (
                        <div className="routing-validation-list">
                          {impact.errors.map((item) => (
                            <div key={`${role.role}-error-${item}`} className="routing-validation-item">
                              {item}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <div className="routing-catalog-note">
                        {impact.eligible_count} ruta(s) elegible(s) tras aplicar esta estimación local.
                      </div>
                    </div>
                  ) : null}

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
                              <span className={`routing-state-pill ${adapter.operational ? 'is-ok' : 'is-muted'}`}>
                                {adapter.operational ? 'operativo' : 'degraded'}
                              </span>
                              <span>{adapter.channel}</span>
                              {adapter.tier && <span>{adapter.tier}</span>}
                              {adapter.configured_provider_preferred && <span>provider preferido</span>}
                              {adapter.configured_model_preferred && <span>modelo preferido</span>}
                            </div>
                          </div>
                          {adapter.blocker_details && adapter.blocker_details.length > 0 && (
                            <div className="routing-blocker-list">
                              {adapter.blocker_details.map((blocker) => (
                                <div key={`${role.role}-${adapter.adapter_name}-${blocker.code}`} className="routing-blocker-card">
                                  <div className="routing-pill-row">
                                    <span className="routing-pill routing-pill-warn">{blocker.label}</span>
                                    <span className="routing-pill routing-pill-muted">{blocker.severity}</span>
                                  </div>
                                  <div className="routing-catalog-note">{blocker.reason}</div>
                                </div>
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
        )}
      </section>

      <section className="status-section">
        <div className="status-section-label">Adapters registrados</div>
        <details className="routing-role-details">
          <summary>Ver inventario completo</summary>
          <div className="routing-adapter-list">
            {filteredAdapters.map((adapter) => (
              <div key={adapter.adapter_name} className="routing-adapter-row">
                <div className="routing-adapter-main">
                  <div className="routing-adapter-title">{adapter.adapter_name}</div>
                  <div className="routing-adapter-meta">
                    <span>{adapter.provider}/{adapter.model}</span>
                    <span>{adapter.channel}</span>
                    {adapter.tier && <span>{adapter.tier}</span>}
                    <span>cost {adapter.cost_tier}</span>
                    {adapter.role_targets.length > 0 && <span>roles {adapter.role_targets.join(', ')}</span>}
                    {adapter.supports_tools && <span>tools</span>}
                    {adapter.supports_streaming && <span>stream</span>}
                    {adapter.supports_vision && <span>vision</span>}
                    {adapter.supports_thinking && <span>thinking</span>}
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
