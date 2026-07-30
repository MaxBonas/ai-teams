export interface ProjectSetupAdapter {
  id: string;
  label: string;
  adapter_type: string;
  channel?: string;
  provider?: string;
  status?: string;
  health?: {
    status?: string;
    reason?: string;
    detail?: string;
  };
}

export interface Candidate {
  candidate_id: string;
  profile_id: string;
  model_id: string;
  provider: string;
  channel: string;
  tier?: string | null;
  rank?: number | null;
  score?: number | null;
  selection_reason?: string | null;
  coverage_eligible: boolean;
  owner_selectable: boolean;
  perspective_key?: string | null;
  capacity_pool?: string | null;
  economics: {
    class: string;
    marginal_cost: string;
    price_note?: string | null;
  };
  privacy: { allowed: boolean; code?: string | null };
  capabilities: string[];
  gates: Record<string, boolean>;
}

export interface ProjectAssignment {
  agent_id: string;
  role: string;
  name: string;
  supervisor_agent_id?: string | null;
  assignment_reason: string;
  selection_mode: 'automatic' | 'owner_explicit';
  candidate: Candidate;
}

export interface ProjectProposal {
  proposal_hash: string;
  project: {
    mode: 'create' | 'import';
    name: string;
    target: string;
    instructions_preview: string;
    objective: string;
  };
  ecosystems: {
    detected_ids: string[];
    scan_truncated: boolean;
  };
  profile: {
    recommended: string;
    selected: string;
    owner_override: boolean;
    automatic_coverage_ready: boolean;
    coverage_status: string;
    coverage_blockers: string[];
  };
  team: {
    assignments: ProjectAssignment[];
    quorum_diversity: {
      ready: boolean;
      perspective_count: number;
      capacity_pool_count: number;
    };
    manual_override_count: number;
  };
  budget: Record<string, unknown>;
  degradations: string[];
  save_gate: {
    allowed: boolean;
    blockers: string[];
    requires_owner_confirmation: boolean;
  };
}

export interface CoverageRole {
  candidates: Candidate[];
  excluded_candidates: Candidate[];
}

export interface ProposalResponse {
  proposal: ProjectProposal;
  coverage: { roles: Record<string, CoverageRole> };
}

export interface PreflightIssue {
  gate?: string;
  code: string;
  message: string;
  next_action: string;
}

export interface PreflightGate {
  id: string;
  required: boolean;
  status: string;
  code: string;
  message: string;
  next_action: string;
}

export interface PreflightAction {
  id: 'local_fixture' | 'exact_adapter_probe';
  kind: string;
  profile_id?: string;
  model_id?: string;
  timeout_seconds: number;
  remote: boolean;
  quota_possible: boolean;
  consent_requirements: string[];
}

export interface ProjectPreflightResponse {
  preflight: {
    preflight_hash: string;
    objective: {
      kind: string;
      software_surface_detected: boolean;
      detected_ecosystems: string[];
    };
    fixture_policy: {
      kind: string;
      software_fixture_required: boolean;
      remote_probe_requires_consent: boolean;
      possible_quota_must_be_confirmed: boolean;
      automatic_install: boolean;
      max_attempts: number;
    };
    gates: PreflightGate[];
    summary: {
      status: 'go' | 'no_go';
      go: boolean;
      commit_allowed: boolean;
      enter_project_allowed: false;
      blockers: PreflightIssue[];
      warnings: PreflightIssue[];
      optional_pending: PreflightIssue[];
      next_action: string;
    };
  };
  execution_plan: {
    plan_hash: string;
    actions: PreflightAction[];
    planning_blockers: PreflightIssue[];
    summary: {
      status: 'blocked' | 'ready' | 'nothing_to_run';
      action_count: number;
      remote_action_count: number;
      requires_consent: boolean;
      next_action: string;
    };
  };
}

export interface ProjectPreflightExecutionResponse {
  receipt: {
    receipt_hash: string;
    summary: {
      status: 'passed' | 'failed' | 'nothing_to_run';
      planned_count?: number;
      executed_count?: number;
      passed_count?: number;
      next_action?: string;
    };
  };
  post_execution_preflight: ProjectPreflightResponse['preflight'];
  persistence: {
    persisted: true;
    idempotent_replay: boolean;
    required_before_commit: false;
    durable_receipt: {
      id: string;
      receipt_hash: string;
      preflight_hash: string;
      execution_plan_hash: string;
      execution_receipt_hash: string;
      status: 'go' | 'no_go';
      fixture_evidence_refs: string[];
    };
  };
}

export interface Session {
  id: string;
  revision: number;
}

export interface ProjectSetupWizardProps {
  projectsRoot: string;
  adapters: ProjectSetupAdapter[];
  preparedAdapterIds: string[];
  selectedAdapterIds: string[];
  onToggleAdapter: (profileId: string) => void;
  onCommitted: (result: {
    workspace?: string;
    configured?: boolean;
    project_name?: string;
    success?: boolean;
  }) => Promise<void> | void;
  onOpenConfiguration?: () => void;
}
