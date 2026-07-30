import {
  AlertTriangle,
  CircleDollarSign,
  Gauge,
  Layers3,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  UsersRound,
} from 'lucide-react';
import type {
  CoverageRole,
  ProjectProposal,
} from './types';
import './ProjectProposalReview.css';

interface ProjectProposalReviewProps {
  proposal: ProjectProposal;
  coverageRoles: Record<string, CoverageRole>;
  overrides: Record<string, string>;
  budgetPriority: string;
  onOverride: (agentId: string, candidateId: string) => void;
}

export function ProjectProposalReview({
  proposal,
  coverageRoles,
  overrides,
  budgetPriority,
  onOverride,
}: ProjectProposalReviewProps) {
  const budgetSummary = Object.entries(proposal.budget)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 3)
    .map(([key, value]) => `${key.replaceAll('_', ' ')}: ${String(value)}`)
    .join(' · ');
  return (
    <div className="project-review">
      <div className="review-command-strip">
        <div>
          <span className="stage-index">04 / PROPUESTA SELLADA</span>
          <h2 id="project-review-title">{proposal.project.name}</h2>
          <code>{proposal.project.target}</code>
        </div>
        <div className={`save-readiness${proposal.save_gate.allowed ? ' ready' : ''}`}>
          {proposal.save_gate.allowed ? <ShieldCheck size={20} /> : <AlertTriangle size={20} />}
          <span>
            <strong>{proposal.save_gate.allowed ? 'Propuesta válida' : 'Bloqueada'}</strong>
            <small>{proposal.save_gate.allowed ? 'Hash y selección verificados; falta preflight' : proposal.save_gate.blockers.join(' · ')}</small>
          </span>
        </div>
      </div>

      <div className="review-metrics">
        <div><Layers3 size={16} /><span><small>Perfil</small><strong>{proposal.profile.selected}</strong></span></div>
        <div><UsersRound size={16} /><span><small>Equipo</small><strong>{proposal.team.assignments.length} roles</strong></span></div>
        <div><Gauge size={16} /><span><small>Cobertura</small><strong>{proposal.profile.coverage_status}</strong></span></div>
        <div><CircleDollarSign size={16} /><span><small>Política</small><strong>{budgetPriority.replaceAll('_', ' ')}</strong></span></div>
      </div>

      <div className="team-manifest">
        {proposal.team.assignments.map((assignment, index) => {
          const roleCoverage = coverageRoles[assignment.role];
          const choices = [
            ...(roleCoverage?.candidates ?? []),
            ...(roleCoverage?.excluded_candidates ?? []).filter((item) => item.owner_selectable),
          ];
          return (
            <article className="team-manifest-card" key={assignment.agent_id}>
              <div className="manifest-order">0{index + 1}</div>
              <div className="manifest-role">
                <small>{assignment.role.replaceAll('_', ' ')}</small>
                <h3>{assignment.name}</h3>
                <p>{assignment.assignment_reason}</p>
              </div>
              <div className="manifest-model">
                <span className={`channel-mark channel-${assignment.candidate.channel}`} />
                <div>
                  <strong>{assignment.candidate.model_id}</strong>
                  <small>{assignment.candidate.provider || assignment.candidate.profile_id} · {assignment.candidate.channel}</small>
                </div>
              </div>
              <div className="manifest-score">
                <small>score</small>
                <strong>{assignment.candidate.score ?? '—'}</strong>
              </div>
              <div className="manifest-economics">
                <small>{assignment.candidate.tier || 'sin tier'}</small>
                <strong title={assignment.candidate.economics.price_note || undefined}>
                  {assignment.candidate.economics.marginal_cost === 'zero' ? 'coste 0' : 'cuota / uso'}
                </strong>
              </div>
              <div className="manifest-gates" aria-label={`Gates de ${assignment.name}`}>
                {Object.entries(assignment.candidate.gates).map(([gate, passed]) => (
                  <span className={passed ? 'passed' : 'failed'} key={gate}>
                    {passed ? <ShieldCheck size={11} /> : <AlertTriangle size={11} />}
                    {gate.replaceAll('_', ' ')}
                  </span>
                ))}
                {!Object.keys(assignment.candidate.gates).length ? (
                  <span className="failed"><AlertTriangle size={11} /> sin gates publicados</span>
                ) : null}
              </div>
              {choices.length > 1 ? (
                <label className="manifest-override">
                  Selección por rol
                  <select
                    value={overrides[assignment.agent_id] || assignment.candidate.candidate_id}
                    onChange={(event) => onOverride(assignment.agent_id, event.target.value)}
                  >
                    {choices.map((candidate) => (
                      <option key={candidate.candidate_id} value={candidate.candidate_id}>
                        {candidate.model_id} · {candidate.provider || candidate.profile_id}
                        {candidate.coverage_eligible ? '' : ' · manual'}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </article>
          );
        })}
      </div>

      <div className="review-foot">
        <div className="review-evidence">
          <span><Sparkles size={14} /> {proposal.ecosystems.detected_ids.length ? proposal.ecosystems.detected_ids.join(', ') : 'Sin ecosistema detectado todavía'}</span>
          <span><LockKeyhole size={14} /> {proposal.proposal_hash.slice(0, 12)}…</span>
          <span><CircleDollarSign size={14} /> {budgetSummary || `prioridad: ${budgetPriority.replaceAll('_', ' ')}`}</span>
          {proposal.degradations.map((degradation) => (
            <span className="degradation" key={degradation}><AlertTriangle size={14} /> {degradation.replaceAll('_', ' ')}</span>
          ))}
        </div>
        <div className="review-next-gate">
          <ShieldCheck size={16} />
          <span>
            <strong>Propuesta completa</strong>
            <small>El preflight server-side decide si puede materializarse.</small>
          </span>
        </div>
      </div>
    </div>
  );
}
