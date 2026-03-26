/** Core chat message displayed in the conversation log. */
export interface ChatMessage {
  id: string;
  sender: 'user' | 'team';
  text: string;
  meta?: string;
}

/** Orchestration mode for the chat session. */
export type ChatMode = 'sprint5' | 'classic';

/** Severity / complexity level used for chat configuration. */
export type ChatLevel = 'low' | 'medium' | 'high';

/** Per-workspace chat configuration persisted in localStorage. */
export interface StoredChatConfig {
  mode: ChatMode;
  rounds: number;
  complexity: ChatLevel;
  criticality: ChatLevel;
  strictMode: boolean;
  allowLowProductivityOverride: boolean;
}

/** Summary of the last completed chat run, stored in component state. */
export interface LastChatRun {
  task_id?: string;
  mode?: string;
  round_budget?: number;
  rounds_used?: number;
  phase_count?: number;
  delegated_count?: number;
  continuation_requested?: boolean;
  continuation_of?: string;
  status?: string;
  execution_mode?: string;
  placeholder_outputs?: number;
  successful_check_count?: number;
  live_mode_required?: boolean;
  live_mode_rejected?: boolean;
  ts?: string;
}

/** Real-time progress snapshot for a running chat task. */
export interface TeamChatProgress {
  task_id: string;
  exists: boolean;
  state: string;
  round_budget: number;
  rounds_used: number;
  phase_states: Record<string, string>;
  completed_tasks: number;
  pending_tasks: number;
  failed_tasks: number;
  execution_attempts: number;
  execution_steps: number;
  execution_steps_success: number;
  execution_mode: string;
  placeholder_outputs: number;
  successful_checks: string[];
  successful_check_count: number;
  live_mode_required: boolean;
  live_mode_rejected: boolean;
  evidence_gate_rejected: boolean;
  evidence_gate_failures: string[];
  last_event: string;
  last_event_ts: string;
}
