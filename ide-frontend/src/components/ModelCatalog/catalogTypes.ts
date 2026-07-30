import type { Tier1Authority, Tier1Coverage } from './Tier1Authority';

export type StateValue = true | false | null;

export interface CatalogState {
  value: StateValue;
  reason?: string | null;
  source?: string | null;
  version?: string | null;
  observed_at?: string | null;
}

export interface ScoreComponent {
  value?: number | null;
  status?: string;
  reason?: string;
  source?: string;
  weight_percent?: number;
  weighted_points?: number | null;
  sample_count?: number;
  basis?: string;
  latency_ms?: number | null;
}

export interface RoleScore {
  score?: number | null;
  score_range?: { minimum?: number; maximum?: number };
  confidence?: {
    value?: number;
    minimum_for_auto?: number;
    evidence_status?: string;
    seeds?: number;
    cases?: number;
    goodhart_risk?: string;
    fresh?: boolean;
    evaluated_at?: string | null;
    provider_version?: string | null;
    unmeasured_constructs?: string[];
  };
  breakdown?: Record<string, ScoreComponent>;
  hard_gates?: Record<string, { passed?: StateValue; reason?: string; source?: string }>;
  auto_eligible?: boolean;
  auto_ineligible_reasons?: string[];
  known_weight_percent?: number;
  rollout?: string;
}

export interface RoleEvaluation {
  canonical_role: string;
  compatibility?: { allowed?: boolean; code?: string; reason?: string };
  evaluation?: {
    status?: string;
    evaluated_at?: string | null;
    provider_version?: string | null;
    evidence_receipts?: string[];
    diagnostic_receipts?: string[];
    diagnostic_stale_reasons?: string[];
    rerun_policy?: string | null;
    material_change_triggers?: string[];
    next_action?: string | null;
    stale_reasons?: string[];
  };
  tier1_authority?: Tier1Authority;
  runtime_metrics?: Record<string, unknown>;
  provenance?: {
    evaluation_receipts?: string[];
    diagnostic_receipts?: string[];
    runtime_database_ids?: string[];
    runtime_run_ids?: string[];
    metric_sources?: string[];
  };
  score?: RoleScore;
  score_inputs?: Record<string, unknown>;
  input_hash?: string;
  calibration_gate?: {
    schema_version?: string;
    gates: Array<{
      stage: string;
      status: 'passed' | 'blocked' | 'pending' | 'historical' | 'waiting';
      reason_code: string;
      owner: string;
    }>;
    blocker?: {
      stage: string;
      code: string;
      owner: string;
    } | null;
    owner: string;
    next_action: string;
    actionable: boolean;
    promotion_ready: boolean;
  };
}

export interface CatalogCandidate {
  candidate_id: string;
  label?: string;
  identity: {
    profile_id: string;
    provider_org: string;
    model_vendor?: string;
    perspective_key?: string;
    channel: string;
    capacity_pool?: string;
    model_id: string;
  };
  states: Record<string, CatalogState>;
  owner_preference?: {
    state: 'high' | 'normal' | 'low' | 'archived';
    reason: string;
    updated_at?: string | null;
    source?: string;
  };
  provider_metadata?: {
    label?: string | null;
    adapter_type?: string | null;
    status?: string | null;
    data_policy?: string | null;
    privacy_note?: string | null;
    workspace_mode?: string | null;
    mcp_transport?: string | null;
    structured_output?: string | null;
  };
  model_metadata: {
    tier?: string | null;
    capability_band?: string | null;
    capabilities?: string[];
    economy?: {
      cost_class?: string;
      measurement_basis?: string;
      input_cents_per_mtok?: number | null;
      output_cents_per_mtok?: number | null;
      quota_unlimited?: boolean;
    };
    speed_class?: string | null;
    speed_source?: string | null;
    context_window_tokens?: number | null;
    price_note?: string | null;
    capability_basis?: string | null;
    probe_status?: string | null;
    probe_reason?: string | null;
    probe_version?: string | null;
    probe_evaluated_at?: string | null;
    probe_receipts?: string[];
  };
  roles: RoleEvaluation[];
  rank?: number;
  selection_reason?: string;
  role_evaluation?: RoleEvaluation;
}

export interface ProviderSummary {
  profile_id: string;
  provider: string;
  channel: string;
  model_count: number;
  configured_count: number;
  green_count: number;
  selectable_count: number;
  blocked_count: number;
  data_policy?: string | null;
  economy_classes?: string[];
}

export interface CatalogPayload {
  success: boolean;
  schema_version: string;
  score_version: string;
  content_hash: string;
  observed_at: string;
  rollout: string;
  counts: { candidates: number; providers: number };
  providers: ProviderSummary[];
  tier1_coverage?: Tier1Coverage;
  candidates: CatalogCandidate[];
}

export interface RoleCandidatesPayload {
  success: boolean;
  canonical_role: string;
  content_hash: string;
  rollout: string;
  counts: { candidates: number; auto_eligible: number };
  tier1_coverage?: Tier1Coverage;
  candidates: CatalogCandidate[];
}

export interface Filters {
  query: string;
  role: string;
  provider: string;
  channel: string;
  tier: string;
  state: string;
  preference: string;
  authority: string;
}

export interface DetailSelection {
  candidate: CatalogCandidate;
  role: string;
}
