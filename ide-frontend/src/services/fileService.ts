import { apiFetch } from '../lib/api';
import type { FileNode } from '../types';

/**
 * Load the full file tree from the backend.
 * Returns the root FileNode or null on failure.
 */
export async function loadFileTree(): Promise<FileNode | null> {
  try {
    const res = await apiFetch('/api/fs/tree');
    const data: FileNode = await res.json();
    return data;
  } catch (err) {
    console.error('loadFileTree failed:', err);
    return null;
  }
}

/**
 * Read the content of a single file by path.
 * Returns an object with `content` on success or `error` on failure.
 */
export async function readFile(path: string): Promise<{ content?: string; error?: string }> {
  try {
    const res = await apiFetch(`/api/fs/read?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      const text = await res.text().catch(() => `HTTP ${res.status}`);
      return { error: text };
    }
    const json = await res.json();
    return { content: typeof json.content === 'string' ? json.content : '' };
  } catch (err) {
    return { error: err instanceof Error ? err.message : 'Unknown error reading file' };
  }
}

/**
 * Write content to a file at the given path.
 * Returns true on success, false on failure.
 */
export async function writeFile(path: string, content: string): Promise<boolean> {
  try {
    const res = await apiFetch('/api/fs/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content }),
    });
    return res.ok;
  } catch (err) {
    console.error('writeFile failed:', err);
    return false;
  }
}
