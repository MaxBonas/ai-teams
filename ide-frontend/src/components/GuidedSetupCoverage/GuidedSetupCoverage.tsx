import {
  AlertTriangle,
  ArrowRight,
  Bot,
  Check,
  CircleDollarSign,
  Gauge,
  Network,
  ShieldCheck,
} from 'lucide-react';

import './GuidedSetupCoverage.css';

type GateMap = Record<string, boolean>;

export interface GuidedCoverageCandidate {
  candidate_id: string;
  profile_id: string;
  model_id: string;
  provider: string;
  channel: string;
  tier?: string | number | null;
  rank?: number | null;
  score?: number | null;
  selection_reason?: string | null;
  coverage_eligible: boolean;
  owner_selectable: boolean;
  disabled_reason?: string | null;
  exclusion_reasons: string[];
  perspective_key?: string | null;
  capacity_pool?: string | null;
  economics: {
    class: string;
    marginal_cost: string;
    price_note?: string | null;
  };
  privacy: {
    allowed: boolean;
    code?: string | null;
  };
  capabilities: string[];
  gates: GateMap;
}

interface CoverageRequirement {
  role: string;
  required_count: number;
  eligible_count: number;
  requires_diversity: boolean;
  perspective_count: number;
  capacity_pool_count: number;
  status: string;
  missing_count: number;
}

interface CoverageProfile {
  profile: string;
  ready: boolean;
  status: string;
  requirements: CoverageRequirement[];
  blockers: string[];
}

interface CoverageRole {
  role: string;
  candidate_count: number;
  eligible_count: number;
  excluded_count: number;
  status: string;
  candidates: GuidedCoverageCandidate[];
  excluded_candidates: GuidedCoverageCandidate[];
}

interface RecommendationAction {
  code: string;
  phase: string;
  priority: number;
  required: boolean;
  profile_id?: string | null;
  reason: string;
  pending_stages?: string[];
  alternative_profile_ids?: string[];
  gaps?: Array<{
    role: string;
    status: string;
    missing_count: number;
    perspective_count: number;
    capacity_pool_count: number;
  }>;
}

interface RecommendationPhase {
  id: string;
  priority: number;
  status: string;
  actions: RecommendationAction[];
}

export interface GuidedSetupCoveragePayload {
  coverage: {
    schema_version: string;
    recommended_profile: string;
    recommended_profile_ready: boolean;
    profiles: Record<string, CoverageProfile>;
    roles: Record<string, CoverageRole>;
  };
  recommendations: {
    schema_version: string;
    recommended_profile: string;
    ready_to_continue: boolean;
    phases: RecommendationPhase[];
    next_action: RecommendationAction | null;
  };
  preparation: {
    schema_version: string;
    ready: boolean;
    blockers: string[];
    ready_adapter_ids: string[];
  };
  selection_context: {
    source: string;
    catalog_content_hash?: string | null;
    run_profile: string;
    criticality: string;
    data_class: string;
    required_capabilities: string[];
  };
}

const PROFILE_LABELS: Record<string, string> = {
  solo_lead: 'Lead esencial',
  lead_quorum: 'Lead + quorum',
  full_team: 'Equipo completo',
};

const ROLE_LABELS: Record<string, string> = {
  team_lead: 'Team Lead',
  quorum_auditor: 'Auditor quorum',
  engineer: 'Engineer',
  reviewer: 'Reviewer',
  worker: 'Worker económico',
};

const ACTION_LABELS: Record<string, string> = {
  complete_lead_adapter: 'Completar el adapter Lead',
  restore_lead_model_eligibility: 'Restaurar elegibilidad del Lead',
  choose_lead_channel: 'Elegir un canal para Lead',
  expand_quorum_diversity: 'Añadir una segunda perspectiva al quorum',
  complete_full_team: 'Completar roles de implementación',
  consider_economic_worker: 'Añadir capacidad económica opcional',
};

const STAGE_LABELS: Record<string, string> = {
  installation: 'instalación',
  version: 'versión',
  authentication: 'autenticación',
  catalog: 'catálogo',
  health: 'health',
  contract: 'contrato',
};

function humanize(value?: string | null): string {
  if (!value) return 'No declarado';
  return value.replaceAll('_', ' ').replace(/^./, (char) => char.toUpperCase());
}

function scoreText(value?: number | null): string {
  if (value === null || value === undefined) return '—';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function nextActionDetail(action: RecommendationAction): string {
  if (action.pending_stages?.length) {
    return `Falta ${action.pending_stages.map((stage) => STAGE_LABELS[stage] || humanize(stage)).join(', ')}.`;
  }
  if (action.gaps?.length) {
    return action.gaps
      .map((gap) => `${ROLE_LABELS[gap.role] || humanize(gap.role)}: ${humanize(gap.status)}`)
      .join(' · ');
  }
  return humanize(action.reason);
}

function ProfileCard({
  profile,
  recommended,
}: {
  profile: CoverageProfile;
  recommended: boolean;
}) {
  return (
    <article
      className={`guided-profile-card ${profile.ready ? 'is-ready' : 'is-blocked'} ${recommended ? 'is-recommended' : ''}`}
      aria-label={`${PROFILE_LABELS[profile.profile] || profile.profile}: ${profile.ready ? 'preparado' : 'bloqueado'}`}
    >
      <div className="guided-profile-index" aria-hidden="true">
        {profile.ready ? <Check size={13} /> : profile.blockers.length}
      </div>
      <div>
        <span>{recommended ? 'Ruta recomendada' : 'Expansión'}</span>
        <strong>{PROFILE_LABELS[profile.profile] || humanize(profile.profile)}</strong>
      </div>
      <small>{profile.ready ? 'Cobertura lista' : `${profile.blockers.length} hueco${profile.blockers.length === 1 ? '' : 's'}`}</small>
    </article>
  );
}

function CandidateEvidence({
  candidate,
  eligible,
}: {
  candidate: GuidedCoverageCandidate;
  eligible: boolean;
}) {
  const passedGates = Object.values(candidate.gates).filter(Boolean).length;
  const totalGates = Object.keys(candidate.gates).length;
  return (
    <details className={`guided-candidate ${eligible ? '' : 'is-excluded'}`}>
      <summary>
        <span className="guided-candidate-rank">#{candidate.rank ?? '—'}</span>
        <span className="guided-candidate-name">
          <strong>{candidate.model_id}</strong>
          <small>{candidate.provider} · {candidate.profile_id}</small>
        </span>
        <span className="guided-candidate-score" aria-label={`Puntuación ${scoreText(candidate.score)}`}>
          {scoreText(candidate.score)}
        </span>
        <span className={`guided-cost-stamp ${candidate.economics.class === 'zero_marginal' ? 'is-free' : ''}`}>
          {eligible
            ? candidate.economics.class === 'zero_marginal'
              ? 'coste marginal 0'
              : humanize(candidate.economics.class)
            : 'bloqueado'}
        </span>
      </summary>
      <div className="guided-evidence-grid">
        <div>
          <ShieldCheck size={14} />
          <span>Privacidad</span>
          <strong>{candidate.privacy.allowed ? 'Compatible' : humanize(candidate.privacy.code)}</strong>
        </div>
        <div>
          <Gauge size={14} />
          <span>Gates</span>
          <strong>{passedGates}/{totalGates}</strong>
        </div>
        <div>
          <Network size={14} />
          <span>Perspectiva</span>
          <strong>{candidate.perspective_key || 'No declarada'}</strong>
        </div>
        <div>
          <CircleDollarSign size={14} />
          <span>Canal</span>
          <strong>{humanize(candidate.channel)}</strong>
        </div>
      </div>
      <div className="guided-capabilities" aria-label="Capacidades">
        {candidate.capabilities.map((capability) => (
          <span key={capability}>{humanize(capability)}</span>
        ))}
      </div>
      <p>
        {eligible
          ? candidate.selection_reason || 'Elegible por el selector canónico.'
          : candidate.disabled_reason
            || candidate.exclusion_reasons.map(humanize).join(' · ')
            || 'No supera todos los gates de automatización.'}
      </p>
    </details>
  );
}

export function GuidedSetupCoverage({
  payload,
}: {
  payload: GuidedSetupCoveragePayload;
}) {
  const { coverage, recommendations, preparation, selection_context: context } = payload;
  const profileOrder = ['solo_lead', 'lead_quorum', 'full_team'];
  const roleOrder = ['team_lead', 'quorum_auditor', 'engineer', 'reviewer', 'worker'];
  const next = recommendations.next_action;

  return (
    <section className="guided-coverage" aria-labelledby="guided-coverage-title">
      <header className="guided-coverage-header">
        <div>
          <span className="guided-kicker"><Bot size={14} /> Mapa de capacidad real</span>
          <h2 id="guided-coverage-title">Cobertura operativa</h2>
          <p>Solo cuentan modelos calibrados sobre adapters verificados. Nada se instala ni se elige automáticamente.</p>
        </div>
        <dl className="guided-context">
          <div><dt>Perfil</dt><dd>{PROFILE_LABELS[context.run_profile] || humanize(context.run_profile)}</dd></div>
          <div><dt>Criticidad</dt><dd>{humanize(context.criticality)}</dd></div>
          <div><dt>Datos</dt><dd>{humanize(context.data_class)}</dd></div>
          <div><dt>Adapters verdes</dt><dd>{preparation.ready_adapter_ids.length}</dd></div>
        </dl>
      </header>

      <div className="guided-profile-rail" aria-label="Cobertura por perfil">
        {profileOrder.map((profileName) => (
          <ProfileCard
            key={profileName}
            profile={coverage.profiles[profileName]}
            recommended={coverage.recommended_profile === profileName}
          />
        ))}
      </div>

      <div
        className={`guided-next-action ${recommendations.ready_to_continue ? 'can-continue' : ''}`}
        role="status"
        aria-live="polite"
      >
        <div className="guided-action-icon" aria-hidden="true">
          {recommendations.ready_to_continue ? <Check size={18} /> : <AlertTriangle size={18} />}
        </div>
        <div>
          <span>{recommendations.ready_to_continue ? 'Ruta recomendada cubierta' : 'Siguiente acción'}</span>
          <strong>{next ? ACTION_LABELS[next.code] || humanize(next.code) : 'Puedes continuar al preflight'}</strong>
          <p>{next ? nextActionDetail(next) : 'La cobertura requerida está preparada; las ampliaciones siguen siendo opcionales.'}</p>
        </div>
        {next?.profile_id && <code>{next.profile_id}</code>}
        <ArrowRight size={18} aria-hidden="true" />
      </div>

      <div className="guided-role-board">
        <div className="guided-board-heading">
          <div>
            <span>Ranking por rol</span>
            <h3>Modelos utilizables ahora</h3>
          </div>
          <div className="guided-board-legend" aria-label="Leyenda">
            <span><i className="is-ready" /> Cubierto</span>
            <span><i className="is-blocked" /> Sin candidato elegible</span>
          </div>
        </div>
        <div className="guided-role-list">
          {roleOrder.map((roleName) => {
            const role = coverage.roles[roleName];
            if (!role) return null;
            return (
              <article className="guided-role-row" key={roleName}>
                <header>
                  <div className={`guided-role-state ${role.eligible_count ? 'is-ready' : 'is-blocked'}`} aria-hidden="true" />
                  <div>
                    <h4>{ROLE_LABELS[roleName] || humanize(roleName)}</h4>
                    <p>
                      {role.eligible_count
                        ? `${role.eligible_count} elegible${role.eligible_count === 1 ? '' : 's'}`
                        : 'Sin candidato automático'}
                      {role.excluded_count > 0 ? ` · ${role.excluded_count} visible${role.excluded_count === 1 ? '' : 's'} bloqueado${role.excluded_count === 1 ? '' : 's'}` : ''}
                    </p>
                  </div>
                </header>
                <div className="guided-role-candidates">
                  {role.candidates.length ? role.candidates.map((candidate) => (
                    <CandidateEvidence candidate={candidate} eligible key={candidate.candidate_id} />
                  )) : (
                    <div className="guided-role-empty">
                      <AlertTriangle size={15} />
                      <span>El selector no encuentra un par modelo + adapter con todos los gates verdes.</span>
                    </div>
                  )}
                  {role.excluded_candidates.map((candidate) => (
                    <CandidateEvidence
                      candidate={candidate}
                      eligible={false}
                      key={`excluded:${candidate.candidate_id}`}
                    />
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      </div>

      <footer className="guided-coverage-footer">
        <span>Fuente: {humanize(context.source)}</span>
        <span>Catálogo <code>{context.catalog_content_hash?.slice(0, 12) || 'sin hash'}</code></span>
        <span>{recommendations.schema_version}</span>
      </footer>
    </section>
  );
}
