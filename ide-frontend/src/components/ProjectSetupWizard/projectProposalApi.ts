import { projectSetupRequest } from './projectSetupApi';
import type { ProposalResponse, Session } from './types';

interface BuildProjectProposalInput {
  needsAnswers: Record<string, unknown>;
  identity: {
    mode: 'create' | 'import';
    name: string;
    path: string;
  };
  selectedApiProfileIds: string[];
  requestedProfile: string;
  instructions: string;
  overridesByAgentId: Record<string, string>;
}

async function transition(
  current: Session,
  stepKey: string,
  response: Record<string, unknown>,
): Promise<Session> {
  const started = await projectSetupRequest<{ session: Session }>(
    `/api/guided-setup/sessions/${current.id}/steps/${stepKey}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: 'in_progress',
        expected_revision: current.revision,
      }),
    },
  );
  const passed = await projectSetupRequest<{ session: Session }>(
    `/api/guided-setup/sessions/${current.id}/steps/${stepKey}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: 'passed',
        expected_revision: started.session.revision,
        response,
        evidence: { source: 'project_setup_wizard' },
      }),
    },
  );
  return passed.session;
}

export async function buildProjectProposalFlow(
  input: BuildProjectProposalInput,
): Promise<{ session: Session; proposalResponse: ProposalResponse }> {
  const assessment = await projectSetupRequest<{
    submission: Record<string, unknown>;
  }>('/api/guided-setup/needs-assessment', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scope: 'project_setup',
      answers: input.needsAnswers,
    }),
  });
  const created = await projectSetupRequest<{ session: Session }>(
    '/api/guided-setup/sessions',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scope: 'project_setup',
        subject_key: `draft:${globalThis.crypto?.randomUUID?.() ?? Date.now()}`,
        metadata: { source: 'project_setup_wizard' },
      }),
    },
  );
  let session = await transition(
    created.session,
    'project_identity',
    input.identity,
  );
  session = await transition(
    session,
    'objective_profile',
    assessment.submission,
  );
  const proposalResponse = await projectSetupRequest<ProposalResponse>(
    `/api/guided-setup/sessions/${session.id}/project-proposal`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_revision: session.revision,
        selected_api_profile_ids: input.selectedApiProfileIds,
        requested_profile: input.requestedProfile,
        instructions: input.instructions,
        overrides_by_agent_id: input.overridesByAgentId,
      }),
    },
  );
  return { session, proposalResponse };
}
