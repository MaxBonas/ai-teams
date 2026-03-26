import { apiFetch } from '../lib/api';
import type { ChatMode, ChatLevel } from '../types';

/** Parameters for sending a chat message to the AI Team backend. */
export interface SendMessageParams {
  message: string;
  role?: string;
  complexity?: ChatLevel;
  criticality?: ChatLevel;
  mode?: ChatMode;
  max_rounds?: number;
  client_task_id?: string;
  strict_mode?: boolean;
  allow_low_productivity_override?: boolean;
  workspacePath?: string;
}

/**
 * Send a chat message to the AI Team orchestrator.
 * The caller is responsible for parsing the rich response payload.
 */
export async function sendChatMessage(params: SendMessageParams): Promise<unknown> {
  const {
    message,
    role = 'engineer',
    complexity,
    criticality,
    mode,
    max_rounds,
    client_task_id,
    strict_mode,
    allow_low_productivity_override,
    workspacePath,
  } = params;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (workspacePath) {
    headers['x-workspace-path'] = workspacePath;
  }

  const res = await apiFetch('/api/aiteam/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      message,
      role,
      complexity,
      criticality,
      mode,
      max_rounds,
      client_task_id,
      strict_mode,
      allow_low_productivity_override,
    }),
  });

  if (!res.ok) {
    const errorText = await res.text().catch(() => `HTTP ${res.status}`);
    throw new Error(errorText);
  }

  return res.json();
}

/**
 * Poll the progress of a running chat task by its client-generated task ID.
 */
export async function pollChatProgress(
  taskId: string,
  workspacePath: string,
): Promise<unknown> {
  const res = await apiFetch(
    `/api/aiteam/chat/progress/${encodeURIComponent(taskId)}`,
    {
      headers: { 'x-workspace-path': workspacePath },
    },
  );
  return res.json();
}

/**
 * Load the chat / team state which contains last_chat_run and other history.
 */
export async function loadChatHistory(workspacePath: string): Promise<unknown> {
  const res = await apiFetch('/api/aiteam/state?environment=dev', {
    headers: { 'x-workspace-path': workspacePath },
  });
  return res.json();
}
