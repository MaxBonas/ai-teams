import { ModelRoleSelector } from '../ModelRoleSelector';

export interface ProposedTeamMember {
  id: string;
  role: string;
  name: string;
  seniority?: string;
  adapter_type?: string;
  adapter_config?: Record<string, unknown>;
  adapter_profile_id?: string;
  model?: string;
  rationale?: string;
  supervisor_agent_id?: string | null;
}

interface HiringRoleOption {
  recommended?: boolean;
  fit_reason?: string;
}

interface HiringDecisionDetailProps {
  direct: boolean;
  team: ProposedTeamMember[];
  suggestedIssues: Array<Record<string, unknown>>;
  interactionId: string;
  issueId: string;
  runProfile: string;
  criticality: string;
  dataClass: string;
  pending: boolean;
  getRoleOptions: (profileId: string, role: string) => HiringRoleOption[] | undefined;
  onSelectionChange: (
    index: number,
    profileId: string,
    model: string,
    candidateId: string,
  ) => void;
}

export function HiringDecisionDetail({
  direct,
  team,
  suggestedIssues,
  interactionId,
  issueId,
  runProfile,
  criticality,
  dataClass,
  pending,
  getRoleOptions,
  onSelectionChange,
}: HiringDecisionDetailProps) {
  if (direct) {
    return <p className="hiring-direct">Solo Lead — ejecutará directamente sin contratar equipo.</p>;
  }

  if (team.length === 0) return null;

  return (
    <div className="inbox-hiring" data-interaction-id={interactionId}>
      <div className="hiring-header">Equipo propuesto — ajusta adapter y modelo antes de contratar:</div>
      <div className="inbox-table-scroll">
        <table className="hiring-table">
          <thead>
            <tr><th>Rol</th><th>Agente</th><th>Adapter</th><th>Modelo</th><th>Por qué</th></tr>
          </thead>
          <tbody>
            {team.map((member, index) => {
              const profileId = String(member.adapter_profile_id || member.adapter_config?.profile_id || '');
              const roleOptions = getRoleOptions(profileId, member.role || '');
              const topRecommendation = roleOptions?.find((option) => option.recommended);
              return (
                <tr key={member.id}>
                  <td className="hiring-table-role">{member.role}</td>
                  <td className="hiring-table-name">{member.name}</td>
                  <td colSpan={2}>
                    <ModelRoleSelector
                      role={member.role || ''}
                      issueId={issueId}
                      profileId={profileId}
                      model={String(member.model || member.adapter_config?.model || '')}
                      runProfile={runProfile}
                      criticality={criticality}
                      dataClass={dataClass}
                      disabled={!pending}
                      onChange={({ profileId: nextProfileId, model, candidateId }) => onSelectionChange(
                        index,
                        nextProfileId,
                        model,
                        candidateId,
                      )}
                    />
                  </td>
                  <td className="hiring-table-why">
                    {topRecommendation?.fit_reason || member.rationale || '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {suggestedIssues.length > 0 && (
        <>
          <div className="hiring-header">Issues que se crearán:</div>
          {suggestedIssues.map((issue, index) => (
            <div className="hiring-issue" key={String(issue.id || index)}>
              <span className="hiring-delegation">{String(issue.delegation_type || 'work')}</span>
              <span className="hiring-issue-title">{String(issue.title || '')}</span>
              <span className="hiring-assignee">→ {String(issue.assignee_agent_id || issue.role || '?')}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
