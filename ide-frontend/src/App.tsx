import { useState, useEffect, useCallback, useRef, type RefObject } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle, type PanelImperativeHandle, type PanelSize } from 'react-resizable-panels';
import TopBar from './components/TopBar';
import TeamChat from './components/TeamChat';
import StatusPanel from './components/StatusPanel';
import { apiFetch, getWorkspacePath, setWorkspacePath as storeWorkspacePath } from './lib/api';
import { useIdeStore } from './store';

type MinimizedPanelKey = 'chat' | 'status';

export default function App() {
  const { workspaceId: workspacePath, setWorkspaceId: setWorkspacePathState } = useIdeStore();
  const [backendOffline, setBackendOffline] = useState(true);
  const [chatMinimized, setChatMinimized] = useState(false);
  const [statusMinimized, setStatusMinimized] = useState(false);
  const [chatToLoad, setChatToLoad] = useState<string | null>(null);

  const chatPanelRef = useRef<PanelImperativeHandle | null>(null);
  const statusPanelRef = useRef<PanelImperativeHandle | null>(null);

  const toPercent = (panelSize: PanelSize): number => Number(panelSize.asPercentage || 0);

  const collapsePanel = useCallback(
    (panelRef: RefObject<PanelImperativeHandle | null>, setMinimized: (v: boolean) => void) => {
      panelRef.current?.collapse();
      setMinimized(true);
    },
    [],
  );

  const expandPanel = useCallback(
    (panelRef: RefObject<PanelImperativeHandle | null>, setMinimized: (v: boolean) => void) => {
      panelRef.current?.expand();
      setMinimized(false);
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;

    const params = new URLSearchParams(window.location.search);
    const workspaceFromUrl = (params.get('workspace') || '').trim();
    if (workspaceFromUrl) {
      storeWorkspacePath(workspaceFromUrl);
      setWorkspacePathState(workspaceFromUrl);
    }

    const stored = getWorkspacePath();
    if (stored && !workspaceFromUrl) {
      setWorkspacePathState(stored);
    }

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
        setBackendOffline(false);
        const workspace = (data as Record<string, unknown>).workspace || '';
        storeWorkspacePath(String(workspace));
        setWorkspacePathState(String(workspace));
      })
      .catch(() => {
        if (!cancelled) setBackendOffline(true);
      });

    return () => { cancelled = true; };
  }, []);

  const minimizedWindows: Array<{ id: MinimizedPanelKey; label: string }> = [
    ...(chatMinimized ? [{ id: 'chat' as const, label: 'AI Chat' }] : []),
    ...(statusMinimized ? [{ id: 'status' as const, label: 'Workspace' }] : []),
  ];

  const handleRestorePanel = (key: string) => {
    if (key === 'chat') expandPanel(chatPanelRef, setChatMinimized);
    if (key === 'status') expandPanel(statusPanelRef, setStatusMinimized);
  };

  return (
    <div className="ide-container">
      <TopBar
        currentWorkspace={workspacePath}
        onWorkspaceChange={(path) => {
          storeWorkspacePath(path);
          setWorkspacePathState(path);
        }}
        minimizedWindows={minimizedWindows}
        onRestoreWindow={handleRestorePanel}
      />

      {backendOffline && (
        <div className="backend-offline-banner">
          Backend no disponible — arranca backend + frontend reales con <code>start_ide.bat</code>
        </div>
      )}

      <div className="ide-workspace">
        <PanelGroup orientation="horizontal" id="ide-root-layout-v4" style={{ width: '100%', height: '100%' }}>
          <Panel
            panelRef={chatPanelRef}
            defaultSize="68%"
            minSize="30%"
            collapsible
            collapsedSize="0%"
            onResize={(size) => setChatMinimized(toPercent(size) <= 1)}
          >
            <TeamChat
              workspacePath={workspacePath}
              minimized={chatMinimized}
              onToggleMinimize={() => {
                if (chatMinimized) {
                  expandPanel(chatPanelRef, setChatMinimized);
                  return;
                }
                collapsePanel(chatPanelRef, setChatMinimized);
              }}
              chatToLoad={chatToLoad}
              onChatLoaded={() => setChatToLoad(null)}
            />
          </Panel>

          <PanelResizeHandle className="resize-handle-v" />

          <Panel
            panelRef={statusPanelRef}
            defaultSize="32%"
            minSize="10%"
            collapsible
            collapsedSize="0%"
            onResize={(size) => setStatusMinimized(toPercent(size) <= 1)}
          >
            <StatusPanel
              workspacePath={workspacePath}
              minimized={statusMinimized}
              onToggleMinimize={() => {
                if (statusMinimized) {
                  expandPanel(statusPanelRef, setStatusMinimized);
                  return;
                }
                collapsePanel(statusPanelRef, setStatusMinimized);
              }}
              onLoadChat={(taskId) => {
                setChatToLoad(taskId);
                if (chatMinimized) expandPanel(chatPanelRef, setChatMinimized);
              }}
            />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}
