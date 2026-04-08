import { useState, useEffect, useCallback, useRef, type RefObject } from 'react';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle, type PanelImperativeHandle, type PanelSize } from 'react-resizable-panels';
import TopBar from './components/TopBar';
import TeamChat from './components/TeamChat';
import StatusPanel from './components/StatusPanel';
import { apiFetch, getWorkspacePath, setWorkspacePath as storeWorkspacePath } from './lib/api';
import { useIdeStore } from './store';

type MinimizedPanelKey = 'chat' | 'status';
type BootstrapStatus = 'pending' | 'ready' | 'error';
type BootstrapAuditLevel = 'info' | 'success' | 'error';

interface BootstrapCheck {
  key: 'backend' | 'workspace' | 'state' | 'routing';
  label: string;
  status: BootstrapStatus;
  detail: string;
}

interface BootstrapAuditEntry {
  ts: string;
  level: BootstrapAuditLevel;
  step: string;
  detail: string;
}

const INITIAL_BOOTSTRAP_CHECKS: BootstrapCheck[] = [
  { key: 'backend', label: 'Backend', status: 'pending', detail: 'Conectando…' },
  { key: 'workspace', label: 'Workspace', status: 'pending', detail: 'Resolviendo workspace…' },
  { key: 'state', label: 'Estado operativo', status: 'pending', detail: 'Esperando…' },
  { key: 'routing', label: 'Routing', status: 'pending', detail: 'Esperando…' },
];

async function fetchWithTimeout(path: string, init: RequestInit = {}, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await apiFetch(path, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut || (error instanceof Error && error.name === 'AbortError')) {
      throw new Error(`timeout:${path}:${timeoutMs}ms`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export default function App() {
  const { workspaceId: workspacePath, setWorkspaceId: setWorkspacePathState } = useIdeStore();
  const [backendOffline, setBackendOffline] = useState(true);
  const [bootstrapReady, setBootstrapReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<string>('');
  const [bootstrapChecks, setBootstrapChecks] = useState<BootstrapCheck[]>(INITIAL_BOOTSTRAP_CHECKS);
  const [bootstrapAudit, setBootstrapAudit] = useState<BootstrapAuditEntry[]>([]);
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0);
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
    const startedAt = new Date().toISOString();

    const appendAudit = (level: BootstrapAuditLevel, step: string, detail: string) => {
      if (cancelled) return;
      const entry = { ts: new Date().toISOString(), level, step, detail };
      setBootstrapAudit((current) => [...current.slice(-11), entry]);
      const logger = level === 'error' ? console.error : console.info;
      logger(`[bootstrap][${step}] ${detail}`);
      try {
        window.sessionStorage.setItem(
          'aiteam.bootstrap.audit',
          JSON.stringify([...bootstrapAudit.slice(-11), entry]),
        );
      } catch {
        // Ignore storage failures.
      }
    };

    const setCheck = (key: BootstrapCheck['key'], status: BootstrapStatus, detail: string) => {
      if (cancelled) return;
      setBootstrapChecks((current) =>
        current.map((item) => (item.key === key ? { ...item, status, detail } : item)),
      );
    };

    const runBootstrap = async () => {
      setBootstrapReady(false);
      setBootstrapError('');
      setBootstrapChecks(INITIAL_BOOTSTRAP_CHECKS.map((item) => ({ ...item })));
      setBootstrapAudit([{ ts: startedAt, level: 'info', step: 'bootstrap', detail: `Intento ${bootstrapAttempt + 1}` }]);

      const params = new URLSearchParams(window.location.search);
      const workspaceFromUrl = (params.get('workspace') || '').trim();
      if (workspaceFromUrl) {
        storeWorkspacePath(workspaceFromUrl);
        setWorkspacePathState(workspaceFromUrl);
        appendAudit('info', 'workspace', `Workspace desde URL: ${workspaceFromUrl}`);
      }

      const stored = getWorkspacePath();
      if (stored && !workspaceFromUrl) {
        setWorkspacePathState(stored);
        appendAudit('info', 'workspace', `Workspace almacenado: ${stored}`);
      }

      try {
        const bootstrapWorkspace = workspaceFromUrl || stored;
        const workspaceStart = performance.now();
        const bootPromise = bootstrapWorkspace
          ? fetchWithTimeout('/api/workspace', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: bootstrapWorkspace }),
            }, 10000)
          : fetchWithTimeout('/api/workspace', {}, 10000);

        const workspaceResponse = await bootPromise;
        if (!workspaceResponse.ok) {
          throw new Error(`workspace_bootstrap_http_${workspaceResponse.status}`);
        }
        const workspaceElapsed = Math.round(performance.now() - workspaceStart);
        setCheck('backend', 'ready', `Backend accesible (${workspaceElapsed} ms).`);
        const data = (await workspaceResponse.json()) as Record<string, unknown>;
        const workspace = String(data.workspace || '');
        storeWorkspacePath(workspace);
        setWorkspacePathState(workspace);
        setBackendOffline(false);
        setCheck('workspace', 'ready', workspace ? workspace : 'Workspace por defecto resuelto.');
        appendAudit('success', 'workspace', `Bootstrap workspace OK en ${workspaceElapsed} ms`);

        const stateStart = performance.now();
        appendAudit('info', 'state', 'Solicitando estado operativo base');
        const stateResponse = await fetchWithTimeout('/api/aiteam/state-lite?environment=dev', {}, 5000);
        if (!stateResponse.ok) {
          throw new Error(`state_bootstrap_http_${stateResponse.status}`);
        }
        const statePayload = (await stateResponse.json()) as Record<string, unknown>;
        const stateElapsed = Math.round(performance.now() - stateStart);
        setCheck('state', 'ready', `Estado operativo base cargado (${stateElapsed} ms).`);
        appendAudit('success', 'state', `State-lite OK en ${stateElapsed} ms`);
        const startupDiagnostics = (statePayload.startup_diagnostics as Record<string, unknown> | undefined) || {};
        const serverTimings = (startupDiagnostics.timings_ms as Record<string, unknown> | undefined) || {};
        const serverTotal = Number(serverTimings.total_ms || 0);
        if (serverTotal > 0) {
          appendAudit('info', 'state-server', `Servidor /api/aiteam/state-lite: ${serverTotal} ms`);
        }

        const routingStart = performance.now();
        appendAudit('info', 'routing', 'Solicitando catálogo de routing');
        const routingResponse = await fetchWithTimeout('/api/aiteam/routing/catalog', {}, 12000);
        if (!routingResponse.ok) {
          throw new Error(`routing_bootstrap_http_${routingResponse.status}`);
        }
        const routingPayload = (await routingResponse.json()) as Record<string, unknown>;
        const cacheStatus = String((routingPayload.cache as Record<string, unknown> | undefined)?.status || 'ok');
        const routingElapsed = Math.round(performance.now() - routingStart);
        setCheck('routing', 'ready', `Routing cargado (${routingElapsed} ms, ${cacheStatus}).`);
        appendAudit('success', 'routing', `Routing OK en ${routingElapsed} ms (${cacheStatus})`);

        if (!cancelled) {
          setBootstrapReady(true);
          setBootstrapError('');
          appendAudit('success', 'bootstrap', 'Bootstrap completo');
          void apiFetch('/api/aiteam/state?environment=dev')
            .then(async (response) => {
              if (!response.ok) {
                appendAudit('error', 'state-warm', `Warmup HTTP ${response.status}`);
                return;
              }
              const payload = (await response.json()) as Record<string, unknown>;
              const diagnostics = (payload.startup_diagnostics as Record<string, unknown> | undefined) || {};
              const timings = (diagnostics.timings_ms as Record<string, unknown> | undefined) || {};
              const total = Number(timings.total_ms || 0);
              if (total > 0) {
                appendAudit('info', 'state-warm', `State pesado calentado en ${total} ms`);
              } else {
                appendAudit('success', 'state-warm', 'State pesado calentado');
              }
            })
            .catch((warmError: unknown) => {
              const warmMessage = warmError instanceof Error ? warmError.message : 'state_warm_failed';
              appendAudit('error', 'state-warm', warmMessage);
            });
        }
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : 'bootstrap_failed';
        const normalizedMessage = String(message || 'bootstrap_failed');
        const failedStep =
          normalizedMessage.includes('routing') ? 'routing' :
          normalizedMessage.includes('state') ? 'state' :
          normalizedMessage.includes('workspace') ? 'workspace' :
          normalizedMessage.includes('abort') || normalizedMessage.includes('signal') ? 'timeout' :
          'backend';
        if (failedStep === 'workspace' || failedStep === 'backend') {
          setBackendOffline(true);
          setCheck('backend', 'error', 'No se pudo completar el arranque base.');
        } else if (failedStep === 'state') {
          setCheck('state', 'error', 'Falló la carga de estado operativo.');
        } else if (failedStep === 'routing') {
          setCheck('routing', 'error', 'Falló la carga de routing.');
        } else {
          setCheck('backend', 'error', 'Timeout durante bootstrap.');
        }
        setBootstrapReady(false);
        setBootstrapError(`${failedStep}: ${normalizedMessage}`);
        appendAudit('error', failedStep, normalizedMessage);
      }
    };

    runBootstrap();

    return () => { cancelled = true; };
  }, [setWorkspacePathState, bootstrapAttempt]);

  const minimizedWindows: Array<{ id: MinimizedPanelKey; label: string }> = [
    ...(chatMinimized ? [{ id: 'chat' as const, label: 'AI Chat' }] : []),
    ...(statusMinimized ? [{ id: 'status' as const, label: 'Workspace' }] : []),
  ];

  const handleRestorePanel = (key: string) => {
    if (key === 'chat') expandPanel(chatPanelRef, setChatMinimized);
    if (key === 'status') expandPanel(statusPanelRef, setStatusMinimized);
  };

  if (!bootstrapReady) {
    return (
      <div className="ide-boot-screen">
        <div className="ide-boot-card">
          <div className="ide-boot-eyebrow">AI Teams</div>
          <h1 className="ide-boot-title">Preparando el workspace operativo</h1>
          <p className="ide-boot-copy">
            La interfaz queda bloqueada hasta que backend, workspace, estado operativo y routing respondan.
          </p>

          <div className="ide-boot-checklist">
            {bootstrapChecks.map((item) => (
              <div key={item.key} className={`ide-boot-check is-${item.status}`}>
                <span className="ide-boot-check-label">{item.label}</span>
                <span className="ide-boot-check-detail">{item.detail}</span>
              </div>
            ))}
          </div>

          {bootstrapError && (
            <div className="ide-boot-error">
              Error de arranque: <code>{bootstrapError}</code>
            </div>
          )}

          <div className="ide-boot-actions">
            <button type="button" className="ide-boot-button" onClick={() => setBootstrapAttempt((value) => value + 1)}>
              Reintentar
            </button>
          </div>

          <div className="ide-boot-audit">
            <div className="ide-boot-audit-title">Startup audit</div>
            <div className="ide-boot-audit-list">
              {bootstrapAudit.map((entry, index) => (
                <div key={`${entry.ts}-${index}`} className={`ide-boot-audit-entry is-${entry.level}`}>
                  <span className="ide-boot-audit-step">{entry.step}</span>
                  <span className="ide-boot-audit-detail">{entry.detail}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
  }

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
