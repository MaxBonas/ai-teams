import {
  getWorkspacePath,
  setWorkspacePath,
  getRecentWorkspaces,
  pushRecentWorkspace,
  getPinnedWorkspaces,
  togglePinnedWorkspace,
} from '../services/workspaceService';

// @ts-ignore
const env = import.meta.env || {};
const API_BASE = (env as Record<string, string>).VITE_API_URL || 'http://127.0.0.1:8010';

function apiKey(): string {
  try {
    return window.localStorage.getItem('AITEAM_API_KEY') || '';
  } catch {
    return '';
  }
}

export function getApiBase(): string {
  return API_BASE;
}

export function getWsBase(): string {
  return API_BASE.replace(/^http/, 'ws');
}

export function apiFetch(pathOrUrl: string, init: RequestInit = {}): Promise<Response> {
  const url = pathOrUrl.startsWith('http') ? pathOrUrl : `${API_BASE}${pathOrUrl}`;
  const headers = new Headers(init.headers || {});
  const key = apiKey();
  const workspacePath = getWorkspacePath();
  if (key) {
    if (!headers.has('x-api-key')) {
      headers.set('x-api-key', key);
    }
    if (!headers.has('authorization')) {
      headers.set('authorization', `Bearer ${key}`);
    }
  }
  if (workspacePath && !headers.has('x-workspace-path')) {
    headers.set('x-workspace-path', workspacePath);
  }
  return fetch(url, { ...init, headers });
}

// Re-export workspace storage helpers for backward compatibility.
// Canonical implementations now live in ../services/workspaceService.ts
export {
  getWorkspacePath,
  setWorkspacePath,
  getRecentWorkspaces,
  pushRecentWorkspace,
  getPinnedWorkspaces,
  togglePinnedWorkspace,
};
