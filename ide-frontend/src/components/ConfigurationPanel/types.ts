export interface ProjectSkill {
  name: string;
  body?: string;
  applies_to_roles?: string[];
  origin?: string;
  status?: string;
  approved_by?: string;
  updated_at?: string;
  evidence?: string[];
}

export interface SkillGovernance {
  max_project_skills: number;
  max_learned_skills: number;
  max_active_skill_bytes: number;
  project_skills: number;
  learned_skills: number;
  active_skill_bytes: number;
}

export interface SkillDraft {
  name: string;
  roles: string;
  body: string;
  status: string;
}

export type McpToolAccess = 'off' | 'read' | 'write';
export type McpToolDrafts = Record<string, Record<string, McpToolAccess>>;

export interface McpServer {
  name: string;
  source?: string;
  version?: string;
  applies_to_roles?: string[];
  status?: string;
  approved_by?: string;
  justification?: string;
  updated_at?: string;
  approved_tools?: Array<{ name: string; access: 'read' | 'write' }>;
  health?: {
    status?: string;
    detail?: string;
    checked_at?: string;
    next_check_at?: string | null;
    consecutive_failures?: number;
    tools?: Array<{ name: string; read_only?: boolean }>;
  };
}

export interface McpCatalogEntry {
  id: string;
  display_name: string;
  description: string;
  publisher: string;
  homepage: string;
  source: string;
  required_local_command: string;
  version: string;
  distribution_version: string;
  env_required: string[];
  applies_to_roles: string[];
  capabilities: string[];
  risk: string;
  reviewed_at: string;
}

export interface ModelCompatibility {
  allowed?: boolean;
  code?: string;
  reason?: string;
  alternatives?: Array<{ value: string; label: string }>;
}

export interface AdapterHealth {
  status: 'ok' | 'installed' | 'failed' | 'untested' | string;
  checked_at?: string;
  reason?: string;
  detail?: string;
  hint?: string;
}

export interface AdapterProfile {
  id: string;
  label: string;
  adapter_type: string;
  channel?: string;
  provider?: string;
  status?: string;
  config?: Record<string, unknown>;
  model_options?: Array<{
    value: string;
    label: string;
    available?: boolean;
    selectable?: boolean;
    availability?: string;
    availability_reason?: string;
    compatibility?: ModelCompatibility;
  }>;
  model_catalog?: {
    status?: string;
    source?: string;
    reason?: string;
    installed_version?: string | null;
    catalog_client_version?: string | null;
  };
  health?: AdapterHealth;
}

export interface AdapterSettingsRow {
  profile: AdapterProfile;
  connected: boolean;
  testModel: string;
}

export interface CliStatus {
  id: string;
  label: string;
  command: string;
  available: boolean;
  login_supported?: boolean;
  login_hint?: string;
  login_command?: string;
  alternate_login_commands?: string[];
  setup_url?: string;
  setup_url_label?: string;
  setup_steps?: string[];
  credential_storage?: string;
  post_login_check?: string;
}

export interface SecretInfo {
  ref: string;
  provider: string;
  name: string;
  has_secret: boolean;
}
