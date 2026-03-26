import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';
import { apiFetch } from '../lib/api';
import {
  getWorkspacePath,
  setWorkspacePath as storeWorkspacePath,
} from '../services/workspaceService';

interface WorkspaceContextValue {
  workspacePath: string;
  setWorkspacePath: (path: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue>({
  workspacePath: '',
  setWorkspacePath: () => {},
});

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspacePath, setWorkspacePathState] = useState('');

  useEffect(() => {
    let cancelled = false;

    // 1. Check URL params for an explicit workspace override.
    const params = new URLSearchParams(window.location.search);
    const workspaceFromUrl = (params.get('workspace') || '').trim();
    if (workspaceFromUrl) {
      storeWorkspacePath(workspaceFromUrl);
      setWorkspacePathState(workspaceFromUrl);
    }

    // 2. Fall back to whatever is stored locally.
    const stored = getWorkspacePath();
    if (stored && !workspaceFromUrl) {
      setWorkspacePathState(stored);
    }

    // 3. Bootstrap with the backend (POST if we have a path, GET otherwise).
    const bootstrapWorkspace = workspaceFromUrl || stored;
    const bootPromise = bootstrapWorkspace
      ? apiFetch('/api/workspace', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: bootstrapWorkspace }),
        })
      : apiFetch('/api/workspace');

    bootPromise
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        const workspace = data.workspace || '';
        storeWorkspacePath(workspace);
        setWorkspacePathState(workspace);
      })
      .catch((err) => {
        if (!cancelled) console.error('Error fetching initial workspace:', err);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const setWorkspacePath = (path: string) => {
    storeWorkspacePath(path);
    setWorkspacePathState(path);
  };

  return (
    <WorkspaceContext.Provider value={{ workspacePath, setWorkspacePath }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceContext);
}
