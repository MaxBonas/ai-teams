import { Activity, FolderOpen, GitBranch } from 'lucide-react';
import { formatTime, statusLabel } from '../../lib/format';
import type { Run, RunEvent } from '../../types/cockpit';
import './RunsPanel.css';

interface RunsPanelProps {
  runs: Run[];
  selectedRun: Run | null;
  events: RunEvent[];
  runId: string;
  busy: boolean;
  onRunIdChange: (runId: string) => void;
  onSelectRun: (runId: string) => Promise<void>;
}

export function RunsPanel({
  runs,
  selectedRun,
  events,
  runId,
  busy,
  onRunIdChange,
  onSelectRun,
}: RunsPanelProps) {
  return (
    <section className="panel runs-panel">
      <div className="runs-layout">
        <div className="run-list-col">
          <div className="panel-title"><GitBranch size={18} />Runs</div>
          <div className="run-table">
            {runs.map((run) => (
              <button
                type="button"
                className={`run-row${selectedRun?.id === run.id ? ' active' : ''} status-bg-${run.status}`}
                key={run.id}
                onClick={() => void onSelectRun(run.id)}
              >
                <div className="run-row-header">
                  <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
                  <span className="run-time">{formatTime(run.started_at || run.created_at)}</span>
                </div>
                <span className="run-id-label">{run.id.slice(-12)}</span>
                <small>{run.agent_id}</small>
              </button>
            ))}
          </div>
          <div className="form-row compact-row">
            <input
              aria-label="ID completo de la run"
              placeholder="run id completo"
              value={runId}
              onChange={(event) => onRunIdChange(event.target.value)}
            />
            <button type="button" onClick={() => void onSelectRun(runId)} disabled={busy || !runId.trim()}>
              Ver
            </button>
          </div>
        </div>

        {selectedRun ? (
          <div className="run-detail-col">
            <div className="panel-title"><Activity size={18} />Detalle</div>
            <dl className="run-meta">
              <dt>ID</dt><dd>{selectedRun.id}</dd>
              <dt>Agente</dt><dd>{selectedRun.agent_id}</dd>
              <dt>Issue</dt><dd>{selectedRun.issue_id || '-'}</dd>
              <dt>Estado</dt>
              <dd><span className={`status-pill status-${selectedRun.status}`}>{statusLabel(selectedRun.status)}</span></dd>
              {selectedRun.error ? (
                <><dt>Error</dt><dd className="run-error">{selectedRun.error}{selectedRun.error_code ? ` (${selectedRun.error_code})` : ''}</dd></>
              ) : null}
              {selectedRun.actual_cost_cents ? <><dt>Coste</dt><dd>{selectedRun.actual_cost_cents}¢</dd></> : null}
              <dt>Inicio</dt><dd>{formatTime(selectedRun.started_at || selectedRun.created_at)}</dd>
              <dt>Fin</dt><dd>{formatTime(selectedRun.finished_at) || '-'}</dd>
            </dl>
            {events.length > 0 ? (
              <div
                className="run-events"
                role="region"
                aria-label={`Eventos de la run ${selectedRun.id}`}
                tabIndex={0}
              >
                <div className="run-events-header">Eventos ({events.length})</div>
                {events.map((event) => {
                  if (event.event_type === 'file_ops' && event.payload) {
                    const ops = (event.payload.ops as Array<{ op: string; path: string }> | undefined) || [];
                    return (
                      <div className="run-event run-event-fileops stream-tool" key={event.id}>
                        <span className="ev-seq">#{event.seq}</span>
                        <span className="ev-type ev-type-fileops">
                          <FolderOpen size={13} />file_ops ({ops.length})
                        </span>
                        {ops.length > 0 && (
                          <ul className="ev-fileops-list">
                            {ops.map((op, index) => (
                              <li key={index} className={`ev-fileop ev-fileop-${op.op}`}>
                                <span className="ev-fileop-badge">{op.op}</span>
                                <span className="ev-fileop-path">{op.path}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                        <time>{formatTime(event.created_at)}</time>
                      </div>
                    );
                  }
                  const text = event.payload?.text
                    ? String(event.payload.text).slice(0, 300)
                    : event.payload ? JSON.stringify(event.payload).slice(0, 200) : '';
                  return (
                    <div className={`run-event stream-${event.stream || 'none'}`} key={event.id}>
                      <span className="ev-seq">#{event.seq}</span>
                      <span className="ev-type">{event.event_type}{event.stream ? `/${event.stream}` : ''}</span>
                      {text ? <p className="ev-body">{text}</p> : null}
                      <time>{formatTime(event.created_at)}</time>
                    </div>
                  );
                })}
              </div>
            ) : <p className="muted">Sin eventos registrados.</p>}
          </div>
        ) : (
          <div className="run-detail-col muted-center">
            <p className="muted">Selecciona una run para ver su detalle y eventos.</p>
          </div>
        )}
      </div>
    </section>
  );
}
