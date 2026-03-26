import { apiFetch } from '../lib/api';
import type { DashboardData } from '../types';

/**
 * Load the main dashboard data from `/api/dashboard`.
 */
export async function loadDashboard(): Promise<DashboardData | null> {
  try {
    const res = await apiFetch('/api/dashboard');
    const json: DashboardData = await res.json();
    return json;
  } catch (err) {
    console.error('loadDashboard failed:', err);
    return null;
  }
}

/**
 * Load the AI team state for a given workspace.
 */
export async function loadTeamState(workspacePath: string): Promise<unknown> {
  try {
    const res = await apiFetch('/api/aiteam/state?environment=dev', {
      headers: { 'x-workspace-path': workspacePath },
    });
    return await res.json();
  } catch (err) {
    console.error('loadTeamState failed:', err);
    return null;
  }
}

/**
 * Load the operator / orchestrator status for a given workspace.
 */
export async function loadOperatorStatus(workspacePath: string): Promise<unknown> {
  try {
    const res = await apiFetch('/api/aiteam/operator/status', {
      headers: { 'x-workspace-path': workspacePath },
    });
    return await res.json();
  } catch (err) {
    console.error('loadOperatorStatus failed:', err);
    return null;
  }
}
