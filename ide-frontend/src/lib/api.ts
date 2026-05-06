const env = import.meta.env as Record<string, string | undefined>;
const API_BASE = env.VITE_API_URL || 'http://127.0.0.1:8010';
const WORKSPACE_KEY = 'AITEAM_V2_WORKSPACE_PATH';

export function getWorkspacePath(): string {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY) || '';
  } catch {
    return '';
  }
}

export function setWorkspacePath(path: string): void {
  try {
    if (path) {
      window.localStorage.setItem(WORKSPACE_KEY, path);
    } else {
      window.localStorage.removeItem(WORKSPACE_KEY);
    }
  } catch {
    // ignore storage errors
  }
}

export function apiFetch(pathOrUrl: string, init: RequestInit = {}): Promise<Response> {
  const url = pathOrUrl.startsWith('http') ? pathOrUrl : `${API_BASE}${pathOrUrl}`;
  const headers = new Headers(init.headers || {});
  const key = window.localStorage.getItem('AITEAM_API_KEY') || '';
  const workspace = getWorkspacePath();
  if (key && !headers.has('authorization')) {
    headers.set('authorization', `Bearer ${key}`);
  }
  if (key && !headers.has('x-aiteam-api-key')) {
    headers.set('x-aiteam-api-key', key);
  }
  if (workspace && !headers.has('x-aiteam-workspace')) {
    headers.set('x-aiteam-workspace', workspace);
  }
  return fetch(url, { ...init, headers });
}
