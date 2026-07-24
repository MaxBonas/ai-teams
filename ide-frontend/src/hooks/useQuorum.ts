import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';

import { apiFetch } from '../lib/api';

export interface QuorumContribution {
  ordinal: number;
  provider?: string | null;
  model?: string | null;
  channel?: string | null;
  result?: Record<string, unknown> | string | null;
  valid: boolean;
}

export interface QuorumPayload {
  success: boolean;
  issue_id: string;
  session: {
    id: string;
    issue_id: string;
    status: string;
    requested_contributions: number;
    min_valid_contributions: number;
    skipped_reason?: string | null;
    final_plan_revision_id?: string | null;
  };
  contributions: QuorumContribution[];
  gate: {
    ready: boolean;
    status: string;
    valid_contributions: number;
    total_contributions: number;
    distinct_providers: number;
    missing_valid: number;
    diversity_satisfied: boolean;
    reduced_quorum: boolean;
  };
}

interface QuorumResourceState {
  key: string | null;
  data: QuorumPayload | null;
  loading: boolean;
}

interface UseQuorumOptions {
  workspaceConfigured: boolean;
  issueId: string;
  issueProfile: string | null;
  reportError: Dispatch<SetStateAction<string>>;
}

export function useQuorum({
  workspaceConfigured,
  issueId,
  issueProfile,
  reportError,
}: UseQuorumOptions): { quorum: QuorumPayload | null; quorumLoading: boolean } {
  const requestKey = workspaceConfigured && issueId && issueProfile === 'lead_quorum'
    ? issueId
    : null;
  const [resource, setResource] = useState<QuorumResourceState>({
    key: null,
    data: null,
    loading: false,
  });

  useEffect(() => {
    if (!requestKey) return undefined;

    const controller = new AbortController();
    apiFetch(`/api/issues/${encodeURIComponent(requestKey)}/quorum`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (response.status === 404) return null;
        const json = (await response.json()) as QuorumPayload & { detail?: string };
        if (!response.ok) throw new Error(json.detail || `quorum:${response.status}`);
        return json;
      })
      .then((payload) => {
        if (!controller.signal.aborted) {
          setResource({ key: requestKey, data: payload, loading: false });
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setResource({ key: requestKey, data: null, loading: false });
          reportError(reason instanceof Error ? reason.message : 'No se pudo leer el quorum');
        }
      });

    return () => controller.abort();
  }, [requestKey, reportError]);

  if (!requestKey) return { quorum: null, quorumLoading: false };
  if (resource.key !== requestKey) return { quorum: null, quorumLoading: true };
  return { quorum: resource.data, quorumLoading: resource.loading };
}
