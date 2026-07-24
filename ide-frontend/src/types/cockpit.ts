export type IssuePhase = 'planning' | 'engineer' | 'tests' | 'review' | 'gate' | 'done';

export interface ActiveIssueRun {
  id: string;
  status: string;
  agent_id?: string | null;
  adapter_type?: string | null;
  provider?: string | null;
  model?: string | null;
  channel?: string | null;
  started_at?: string | null;
}

export interface ActiveIssueAgent {
  id: string;
  role: string;
  name: string;
}

export interface Issue {
  id: string;
  parent_id?: string | null;
  title: string;
  description?: string | null;
  status: string;
  role?: string | null;
  complexity?: string | null;
  criticality?: string | null;
  assignee_agent_id?: string | null;
  priority?: number;
  created_at?: string;
  metadata_json?: string | null;
  phase?: IssuePhase;
  active_run?: ActiveIssueRun | null;
  active_agent?: ActiveIssueAgent | null;
}

export interface Comment {
  id: string;
  issue_id: string;
  body: string;
  author_agent_id?: string | null;
  author_user_id?: string | null;
  source_run_id?: string | null;
  created_at?: string;
}

export interface Interaction {
  id: string;
  issue_id: string;
  kind: string;
  status: string;
  title?: string | null;
  summary?: string | null;
  created_by_agent_id?: string | null;
  resolved_by_user_id?: string | null;
  created_at?: string;
  resolved_at?: string | null;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
}

export interface Run {
  id: string;
  agent_id: string;
  issue_id?: string | null;
  status: string;
  invocation_source?: string;
  error?: string | null;
  error_code?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  actual_cost_cents?: number | null;
  result?: Record<string, unknown> | null;
  usage?: Record<string, unknown> | null;
}

export interface RunEvent {
  id: string;
  run_id: string;
  event_type: string;
  seq: number;
  stream?: string | null;
  payload?: Record<string, unknown> | null;
  created_at?: string;
}

export interface ChatMessage {
  id: string;
  source_id: string;
  item_type: 'message' | 'interaction';
  sender: 'user' | 'agent';
  author: string;
  body: string;
  title: string | null;
  summary: string | null;
  kind: string | null;
  interaction_status: string | null;
  payload: Record<string, unknown>;
  issue_id: string;
  source_run_id: string | null;
  created_at: string;
}
