import { apiFetch } from '../lib/api';

const WORKSPACE_PATH_KEY = 'AITEAM_WORKSPACE_PATH';
const RECENT_WORKSPACES_KEY = 'AITEAM_RECENT_WORKSPACES';
const PINNED_WORKSPACES_KEY = 'AITEAM_PINNED_WORKSPACES';

/**
 * Read the current workspace path from sessionStorage (preferred) or localStorage.
 */
export function getWorkspacePath(): string {
  try {
    const fromSession = window.sessionStorage.getItem(WORKSPACE_PATH_KEY) || '';
    if (fromSession) {
      return fromSession;
    }
    return window.localStorage.getItem(WORKSPACE_PATH_KEY) || '';
  } catch {
    return '';
  }
}

/**
 * Persist the workspace path into sessionStorage (clears localStorage copy).
 */
export function setWorkspacePath(path: string): void {
  try {
    if (!path) {
      window.sessionStorage.removeItem(WORKSPACE_PATH_KEY);
      window.localStorage.removeItem(WORKSPACE_PATH_KEY);
      return;
    }
    window.sessionStorage.setItem(WORKSPACE_PATH_KEY, path);
    window.localStorage.removeItem(WORKSPACE_PATH_KEY);
  } catch {
    // ignore storage errors
  }
}

/**
 * Get the list of recently-used workspaces from localStorage.
 */
export function getRecentWorkspaces(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_WORKSPACES_KEY) || '[]';
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      .slice(0, 10);
  } catch {
    return [];
  }
}

/**
 * Push a workspace path to the front of the recent list (de-duplicated, max 10).
 */
export function pushRecentWorkspace(path: string): string[] {
  const value = path.trim();
  if (!value) {
    return getRecentWorkspaces();
  }
  const existing = getRecentWorkspaces();
  const next = [value, ...existing.filter((item) => item !== value)].slice(0, 10);
  try {
    window.localStorage.setItem(RECENT_WORKSPACES_KEY, JSON.stringify(next));
  } catch {
    // ignore storage errors
  }
  return next;
}

/**
 * Get the list of pinned workspaces from localStorage.
 */
export function getPinnedWorkspaces(): string[] {
  try {
    const raw = window.localStorage.getItem(PINNED_WORKSPACES_KEY) || '[]';
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      .slice(0, 10);
  } catch {
    return [];
  }
}

/**
 * Toggle a workspace in the pinned list (add if absent, remove if present).
 */
export function togglePinnedWorkspace(path: string): string[] {
  const value = path.trim();
  if (!value) {
    return getPinnedWorkspaces();
  }
  const existing = getPinnedWorkspaces();
  const already = existing.includes(value);
  const next = already
    ? existing.filter((item) => item !== value)
    : [value, ...existing.filter((item) => item !== value)].slice(0, 10);
  try {
    window.localStorage.setItem(PINNED_WORKSPACES_KEY, JSON.stringify(next));
  } catch {
    // ignore storage errors
  }
  return next;
}

/**
 * Set the current workspace on the backend via POST and persist locally.
 * Returns the workspace path confirmed by the server.
 */
export async function setCurrentWorkspace(path: string): Promise<string> {
  const res = await apiFetch('/api/workspace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  const workspace: string = data.workspace || path;
  setWorkspacePath(workspace);
  return workspace;
}

/**
 * Fetch the current workspace from the backend.
 * Returns the workspace path string.
 */
export async function getCurrentWorkspace(): Promise<string> {
  const res = await apiFetch('/api/workspace');
  const data = await res.json();
  return (data.workspace as string) || '';
}
