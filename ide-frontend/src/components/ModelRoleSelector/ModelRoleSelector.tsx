import { useEffect, useMemo, useState } from 'react';

import { apiFetch } from '../../lib/api';

interface SelectionScore {
  score?: number | null;
  confidence?: { value?: number | null };
  auto_eligible?: boolean;
  auto_ineligible_reasons?: string[];
  breakdown?: Record<string, { value?: number | null; status?: string }>;
}

interface SelectionCandidate {
  candidate_id: string;
  label?: string;
  rank: number;
  identity: {
    profile_id: string;
    provider_org: string;
    channel: string;
    model_id: string;
  };
  provider_metadata?: { label?: string | null };
  selection_score: SelectionScore;
  contextual_compatibility?: { allowed?: boolean; reason?: string; code?: string };
  owner_selectable: boolean;
  requires_configuration?: boolean;
  disabled_reason?: string | null;
}

interface SelectionResponse {
  default: {
    candidate_id?: string | null;
    action?: string;
    score?: number | null;
    confidence?: number | null;
    advantage?: { kind?: string; value?: number | null } | null;
  };
  candidates: SelectionCandidate[];
}

interface Props {
  role: string;
  issueId?: string;
  profileId: string;
  model: string;
  runProfile?: string;
  criticality?: string;
  dataClass?: string;
  requiredCapabilities?: string[];
  disabled?: boolean;
  restrictProfileId?: string;
  onChange: (selection: { profileId: string; model: string; candidateId: string }) => void;
}

function optionValue(candidate: SelectionCandidate): string {
  return `${candidate.identity.profile_id}\u0000${candidate.identity.model_id}`;
}

function scoreLabel(score: SelectionScore): string {
  const value = score.score;
  const confidence = score.confidence?.value;
  const scoreText = value == null ? 'sin nota' : `${value.toFixed(1)}/100`;
  return confidence == null ? scoreText : `${scoreText} · conf. ${confidence.toFixed(0)}%`;
}

function breakdownLabel(score: SelectionScore): string {
  const labels: Record<string, string> = {
    quality: 'calidad', capability: 'capacidad', reliability: 'fiabilidad',
    economy: 'economía', speed: 'velocidad',
  };
  return Object.entries(score.breakdown || {})
    .filter(([, component]) => component.value != null)
    .map(([name, component]) => `${labels[name] || name} ${Number(component.value).toFixed(0)}`)
    .join(' · ');
}

export function ModelRoleSelector({
  role,
  issueId = '',
  profileId,
  model,
  runProfile = '',
  criticality = 'medium',
  dataClass = 'public',
  requiredCapabilities = [],
  disabled = false,
  restrictProfileId = '',
  onChange,
}: Props) {
  const capabilitiesKey = [...requiredCapabilities].sort().join('\u0000');
  const requestKey = [role, issueId, runProfile, criticality, dataClass, capabilitiesKey].join('\u0001');
  const [result, setResult] = useState<{
    key: string;
    selection: SelectionResponse | null;
    error: string;
  }>({ key: '', selection: null, error: '' });
  const selection = result.key === requestKey ? result.selection : null;
  const error = result.key === requestKey ? result.error : '';
  const loading = Boolean(role) && result.key !== requestKey;

  useEffect(() => {
    if (!role) return;
    const controller = new AbortController();
    void apiFetch('/api/model-catalog/selection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        role,
        issue_id: issueId,
        run_profile: runProfile,
        criticality,
        data_class: dataClass || 'public',
        required_capabilities: capabilitiesKey ? capabilitiesKey.split('\u0000') : [],
      }),
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(String(payload.detail || `selection:${response.status}`));
        setResult({ key: requestKey, selection: payload as SelectionResponse, error: '' });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setResult({
          key: requestKey,
          selection: null,
          error: reason instanceof Error ? reason.message : 'selection_failed',
        });
      });
    return () => controller.abort();
  }, [role, issueId, runProfile, criticality, dataClass, capabilitiesKey, requestKey]);

  const groups = useMemo(() => {
    const result = new Map<string, SelectionCandidate[]>();
    for (const candidate of selection?.candidates || []) {
      const provider = candidate.provider_metadata?.label || candidate.identity.provider_org;
      const key = `${provider} · ${candidate.identity.channel}`;
      result.set(key, [...(result.get(key) || []), candidate]);
    }
    return [...result.entries()];
  }, [selection]);
  const currentValue = profileId && model ? `${profileId}\u0000${model}` : '';
  const recommended = selection?.candidates.find(
    (candidate) => candidate.candidate_id === selection.default.candidate_id,
  );

  return (
    <div className="model-role-selector">
      <select
        data-testid="model-role-selector"
        value={currentValue}
        disabled={disabled || loading}
        aria-label={`Modelo y adapter para ${role}`}
        onChange={(event) => {
          const candidate = selection?.candidates.find((item) => optionValue(item) === event.target.value);
          if (candidate && (!restrictProfileId || candidate.identity.profile_id === restrictProfileId)) onChange({
            profileId: candidate.identity.profile_id,
            model: candidate.identity.model_id,
            candidateId: candidate.candidate_id,
          });
        }}
      >
        <option value="">
          {loading
            ? 'Calculando ranking contextual…'
            : recommended
              ? `Default → ${recommended.label || recommended.identity.model_id} · ${scoreLabel(recommended.selection_score)}`
              : 'Default no disponible — el owner debe elegir'}
        </option>
        {groups.map(([group, candidates]) => (
          <optgroup key={group} label={group}>
            {candidates.map((candidate) => {
              const reason = candidate.disabled_reason
                || candidate.selection_score.auto_ineligible_reasons?.[0]
                || candidate.contextual_compatibility?.reason;
              return (
                <option
                  key={candidate.candidate_id}
                  value={optionValue(candidate)}
                  disabled={!candidate.owner_selectable || Boolean(
                    restrictProfileId && candidate.identity.profile_id !== restrictProfileId
                  )}
                >
                  #{candidate.rank} {candidate.label || candidate.identity.model_id} · {scoreLabel(candidate.selection_score)}
                  {candidate.candidate_id === selection?.default.candidate_id ? ' · recomendado' : ''}
                  {restrictProfileId && candidate.identity.profile_id !== restrictProfileId
                    ? ' · bloqueado: recovery conserva el adapter'
                    : !candidate.owner_selectable ? ` · bloqueado: ${reason || 'no elegible'}` : ''}
                  {candidate.requires_configuration ? ' · requiere configurar adapter' : ''}
                </option>
              );
            })}
          </optgroup>
        ))}
      </select>
      {recommended && (
        <small className="model-role-default">
          Default en sombra: #{recommended.rank} · score {scoreLabel(recommended.selection_score)}
          {selection?.default.advantage?.kind === 'score_delta'
            ? ` · ventaja ${Number(selection.default.advantage.value || 0).toFixed(1)} puntos`
            : selection?.default.advantage?.kind === 'only_auto_eligible'
              ? ' · único candidato auto-elegible'
              : ' · desempate canónico por evidencia e identidad'}
          {breakdownLabel(recommended.selection_score)
            ? ` · ${breakdownLabel(recommended.selection_score)}`
            : ' · breakdown aún sin métricas completas'}
        </small>
      )}
      {!loading && selection && !recommended && (
        <small data-testid="model-role-no-default" className="field-warning">No existe candidato auto-elegible; se conserva la selección explícita.</small>
      )}
      {error && <small className="field-warning">No se pudo cargar el ranking: {error}</small>}
    </div>
  );
}
