import { projectSetupRequest } from './projectSetupApi';
import type {
  ProjectPreflightExecutionResponse,
  ProjectPreflightResponse,
  Session,
} from './types';

export interface ProjectRequestContext {
  selectedApiProfileIds: string[];
  requestedProfile: string;
  instructions: string;
  overridesByAgentId: Record<string, string>;
}

export interface PreflightConsentState {
  localFixture: boolean;
  remoteProbe: boolean;
  quota: boolean;
}

export function preflightExecutionAuthorizesCommit(
  preview: ProjectPreflightResponse,
  execution: ProjectPreflightExecutionResponse | null,
) {
  const durable = execution?.persistence.durable_receipt;
  return Boolean(
    execution?.persistence.persisted === true
    && durable?.status === 'go'
    && durable.preflight_hash === execution.post_execution_preflight.preflight_hash
    && durable.execution_plan_hash === preview.execution_plan.plan_hash
    && durable.execution_receipt_hash === execution.receipt.receipt_hash
  );
}

function sealedRequest(
  session: Session,
  proposalHash: string,
  context: ProjectRequestContext,
) {
  return {
    expected_revision: session.revision,
    selected_api_profile_ids: context.selectedApiProfileIds,
    requested_profile: context.requestedProfile,
    instructions: context.instructions,
    overrides_by_agent_id: context.overridesByAgentId,
    proposal_hash: proposalHash,
  };
}

export function loadProjectPreflight(
  session: Session,
  proposalHash: string,
  context: ProjectRequestContext,
) {
  return projectSetupRequest<ProjectPreflightResponse>(
    `/api/guided-setup/sessions/${session.id}/project-preflight`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sealedRequest(session, proposalHash, context)),
    },
  );
}

export function executeProjectPreflight(
  session: Session,
  proposalHash: string,
  context: ProjectRequestContext,
  preview: ProjectPreflightResponse,
  consent: PreflightConsentState,
) {
  return projectSetupRequest<ProjectPreflightExecutionResponse>(
    `/api/guided-setup/sessions/${session.id}/project-preflight-execute`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...sealedRequest(session, proposalHash, context),
        preflight_hash: preview.preflight.preflight_hash,
        execution_plan_hash: preview.execution_plan.plan_hash,
        confirm_local_fixture: consent.localFixture,
        confirm_remote_probe: consent.remoteProbe,
        acknowledge_possible_quota: consent.quota,
      }),
    },
  );
}

export function commitPreflightedProject(
  session: Session,
  proposalHash: string,
  context: ProjectRequestContext,
) {
  return projectSetupRequest<{
    result: {
      workspace?: string;
      configured?: boolean;
      project_name?: string;
      success?: boolean;
    };
  }>(
    `/api/guided-setup/sessions/${session.id}/project-commit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...sealedRequest(session, proposalHash, context),
        confirm: true,
      }),
    },
  );
}
