/** Shape returned by the `/api/dashboard` endpoint. */
export interface DashboardData {
  task_total?: number;
  task_state_counts?: Record<string, number>;
  task_role_counts?: Record<string, number>;
  summary?: DashboardSummary;
  pilot_metrics?: DashboardPilotMetrics;
  budget?: DashboardBudget;
  memory_counts?: Record<string, number>;
  recent_events?: unknown[];
  error?: string;
}

export interface DashboardSummary {
  task_execution_success_rate?: number;
  api_share_percent?: number;
  compliance_violations?: number;
  alerts?: string[];
}

export interface DashboardPilotMetrics {
  pro_share_percent?: number;
}

export interface DashboardBudget {
  daily_api_spend_usd?: number;
  daily_api_budget_usd?: number;
}
