import { useState, useEffect, useCallback, useRef, type RefObject } from 'react';
import { Terminal, Code2, RefreshCcw, PanelLeftClose, PanelBottomClose } from 'lucide-react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle, type PanelImperativeHandle, type PanelSize } from 'react-resizable-panels';
import FileExplorer from './components/FileExplorer';
import CodeEditor from './components/CodeEditor';
import DiffEditor from './components/DiffEditor';
import TerminalPanel from './components/TerminalPanel';
import TopBar from './components/TopBar';
import AIDashboard from './components/AIDashboard';
import TeamChat from './components/TeamChat';
import OpsHub from './components/OpsHub';
import { apiFetch, getWorkspacePath, setWorkspacePath as storeWorkspacePath } from './lib/api';
import { useIdeStore } from './store';

type MinimizedPanelKey = 'explorer' | 'chat' | 'ops' | 'terminal';

export default function App() {
  const { workspaceId: workspacePath, setWorkspaceId: setWorkspacePathState, selectedFile: activeFile, setSelectedFile: setActiveFile, activeDiff } = useIdeStore();
  const [activeTab, setActiveTab] = useState<'editor' | 'dashboard'>('editor');
  const [fileTreeRefreshToken, setFileTreeRefreshToken] = useState(0);
  const [fileTreeRefreshing, setFileTreeRefreshing] = useState(false);
  const [sidebarMinimized, setSidebarMinimized] = useState(false);
  const [chatMinimized, setChatMinimized] = useState(false);
  const [opsMinimized, setOpsMinimized] = useState(false);
  const [terminalMinimized, setTerminalMinimized] = useState(false);

  const sidebarPanelRef = useRef<PanelImperativeHandle | null>(null);
  const chatPanelRef = useRef<PanelImperativeHandle | null>(null);
  const rightPanelRef = useRef<PanelImperativeHandle | null>(null);
  const terminalPanelRef = useRef<PanelImperativeHandle | null>(null);

  const handleFileTreeRefreshState = useCallback((refreshing: boolean) => {
    setFileTreeRefreshing(refreshing);
  }, []);

  const collapsePanel = useCallback(
    (
      panelRef: RefObject<PanelImperativeHandle | null>,
      setMinimized: (value: boolean) => void,
    ) => {
      const panel = panelRef.current;
      if (!panel) {
        return;
      }
      panel.collapse();
      setMinimized(true);
    },
    [],
  );

  const expandPanel = useCallback(
    (
      panelRef: RefObject<PanelImperativeHandle | null>,
      setMinimized: (value: boolean) => void,
    ) => {
      const panel = panelRef.current;
      if (!panel) {
        return;
      }
      panel.expand();
      setMinimized(false);
    },
    [],
  );

  const toPercent = (panelSize: PanelSize): number => Number(panelSize.asPercentage || 0);

  const minimizedWindows: Array<{ id: MinimizedPanelKey; label: string }> = [
    ...(sidebarMinimized ? [{ id: 'explorer' as const, label: 'Explorer' }] : []),
    ...(chatMinimized ? [{ id: 'chat' as const, label: 'AI Chat' }] : []),
    ...(opsMinimized ? [{ id: 'ops' as const, label: 'Ops Hub' }] : []),
    ...(terminalMinimized ? [{ id: 'terminal' as const, label: 'Terminal' }] : []),
  ];

  const handleRestorePanel = (key: MinimizedPanelKey) => {
    if (key === 'explorer') {
      expandPanel(sidebarPanelRef, setSidebarMinimized);
      return;
    }
    if (key === 'chat') {
      expandPanel(chatPanelRef, setChatMinimized);
      return;
    }
    if (key === 'ops') {
      expandPanel(rightPanelRef, setOpsMinimized);
      return;
    }
    expandPanel(terminalPanelRef, setTerminalMinimized);
  };

  const handleRestorePanelById = (id: string) => {
    if (id === 'explorer' || id === 'chat' || id === 'ops' || id === 'terminal') {
      handleRestorePanel(id);
    }
  };

  const [backendOffline, setBackendOffline] = useState(true);

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
        const workspace = data.workspace || '';
        storeWorkspacePath(workspace);
        setWorkspacePathState(workspace);
      })
      .catch(() => {
        if (!cancelled) setBackendOffline(true);
      });

    return () => { cancelled = true; };
  }, []);

  const renderRightPane = () => (
    <Panel
      panelRef={rightPanelRef}
      defaultSize="32%"
      minSize="10%"
      maxSize="52%"
      collapsible
      collapsedSize="0%"
      className="team-side-pane"
      onResize={(size) => setOpsMinimized(toPercent(size) <= 1)}
    >
      <OpsHub
        workspacePath={workspacePath}
        minimized={opsMinimized}
        onToggleMinimize={() => collapsePanel(rightPanelRef, setOpsMinimized)}
      />
    </Panel>
  );

  return (
    <div className="ide-container">
      <TopBar
        currentWorkspace={workspacePath}
        onWorkspaceChange={(path) => {
          storeWorkspacePath(path);
          setWorkspacePathState(path);
          setActiveFile(null);
        }}
        activeTab={activeTab}
        onTabChange={setActiveTab}
        minimizedWindows={minimizedWindows}
        onRestoreWindow={handleRestorePanelById}
      />

      {backendOffline && (
        <div className="backend-offline-banner">
          Backend no disponible — arranca el proyecto con <code>npm start</code> o <code>start_ide.bat</code>
        </div>
      )}

      <div className="ide-workspace">
        <PanelGroup orientation="horizontal" id="ide-root-layout-v3" style={{ width: '100%', height: '100%' }}>
          <Panel
            panelRef={sidebarPanelRef}
            defaultSize="18%"
            minSize="8%"
            maxSize="35%"
            collapsible
            collapsedSize="0%"
            className="sidebar"
            onResize={(size) => setSidebarMinimized(toPercent(size) <= 1)}
          >
            <div className="sidebar-header">
              <div className="sidebar-header-title">
                <Code2 size={16} />
                <span>Project Explorer</span>
              </div>
              <div className="sidebar-header-actions">
                <button
                  className="sidebar-refresh-btn"
                  onClick={() => collapsePanel(sidebarPanelRef, setSidebarMinimized)}
                  title="Minimize explorer"
                >
                  <PanelLeftClose size={14} />
                </button>
                <button
                  className="sidebar-refresh-btn"
                  onClick={() => setFileTreeRefreshToken((prev) => prev + 1)}
                  disabled={!workspacePath || fileTreeRefreshing}
                  title="Refresh project files"
                >
                  <RefreshCcw size={14} className={fileTreeRefreshing ? 'spin' : ''} />
                  Refresh files
                </button>
              </div>
            </div>

            {workspacePath ? (
              <FileExplorer
                activeFile={activeFile}
                onFileSelect={(path) => {
                  setActiveFile(path);
                  setActiveTab('editor');
                }}
                workspacePath={workspacePath}
                refreshToken={fileTreeRefreshToken}
                onRefreshStateChange={handleFileTreeRefreshState}
              />
            ) : (
              <div style={{ padding: 16, color: 'var(--text-secondary)' }}>Loading environment...</div>
            )}
          </Panel>

          <PanelResizeHandle className="resize-handle-v" />

          <Panel minSize="25%">
            <PanelGroup orientation="vertical" id="ide-center-layout-v3" style={{ width: '100%', height: '100%' }}>
              <Panel defaultSize="72%" minSize="25%" className="main-content">
                <PanelGroup orientation="vertical" id="ide-main-stack-v1" style={{ width: '100%', height: '100%' }}>
                  <Panel
                    panelRef={chatPanelRef}
                    defaultSize="40%"
                    minSize="10%"
                    collapsible
                    collapsedSize="0%"
                    className="team-chat-center-panel"
                    onResize={(size) => setChatMinimized(toPercent(size) <= 1)}
                  >
                    <TeamChat
                      workspacePath={workspacePath}
                      minimized={chatMinimized}
                      onToggleMinimize={() => collapsePanel(chatPanelRef, setChatMinimized)}
                    />
                  </Panel>

                  <PanelResizeHandle className="resize-handle-h" />

                  <Panel defaultSize="60%" minSize="28%" className="work-canvas-panel">
                    {activeTab === 'editor' ? (
                      <PanelGroup orientation="horizontal" id="ide-editor-main-v3" style={{ width: '100%', height: '100%' }}>
                        <Panel defaultSize="68%" minSize="35%" className="editor-pane">
                          <div className="editor-header">
                            {activeDiff ? `Diff Viewer: ${activeDiff.path}` : (activeFile || 'No file selected')}
                          </div>
                          {activeDiff ? (
                            <DiffEditor
                              originalContent={activeDiff.original}
                              modifiedContent={activeDiff.modified}
                              filePath={activeDiff.path}
                            />
                          ) : activeFile ? (
                            <CodeEditor filePath={activeFile} />
                          ) : (
                            <div className="empty-state">
                              Select a file from the explorer to start editing
                            </div>
                          )}
                        </Panel>

                        <PanelResizeHandle className="resize-handle-v" />
                        {renderRightPane()}
                      </PanelGroup>
                    ) : (
                      <PanelGroup orientation="horizontal" id="ide-dashboard-main-v3" style={{ width: '100%', height: '100%' }}>
                        <Panel defaultSize="68%" minSize="35%" className="dashboard-pane">
                          <AIDashboard workspacePath={workspacePath} />
                        </Panel>

                        <PanelResizeHandle className="resize-handle-v" />
                        {renderRightPane()}
                      </PanelGroup>
                    )}
                  </Panel>
                </PanelGroup>
              </Panel>

              <PanelResizeHandle className="resize-handle-h" />

              <Panel
                panelRef={terminalPanelRef}
                defaultSize="28%"
                minSize="8%"
                collapsible
                collapsedSize="0%"
                className="terminal-pane"
                onResize={(size) => setTerminalMinimized(toPercent(size) <= 1)}
              >
                <div className="terminal-header">
                  <Terminal size={14} style={{ marginRight: '6px' }} />
                  <span>Terminal</span>
                  <button
                    className="terminal-toggle-btn"
                    onClick={() => collapsePanel(terminalPanelRef, setTerminalMinimized)}
                    title="Minimize terminal"
                  >
                    <PanelBottomClose size={13} />
                  </button>
                </div>
                <TerminalPanel workspacePath={workspacePath} />
              </Panel>
            </PanelGroup>
          </Panel>
        </PanelGroup>
      </div>
    </div>
  );
}
