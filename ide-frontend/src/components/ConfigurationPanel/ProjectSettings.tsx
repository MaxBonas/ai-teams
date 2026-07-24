import { InfoTip } from '../InfoTip';

interface ProjectSettingsProps {
  health: { status?: string; mode?: string } | null;
  workspace: string;
  workspaceDraft: string;
  loading: boolean;
  onWorkspaceDraftChange: (value: string) => void;
  onSaveWorkspace: () => Promise<void>;
}

export function ProjectSettings({
  health,
  workspace,
  workspaceDraft,
  loading,
  onWorkspaceDraftChange,
  onSaveWorkspace,
}: ProjectSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        Proyecto activo
        <InfoTip tip="El proyecto que el backend tiene abierto ahora. Cada proyecto tiene su propia base de datos SQLite. Para cambiar de proyecto usa el selector del nombre en la barra superior." wide />
      </div>
      <dl className="config-dl config-dl-compact">
        <dt>Estado</dt>
        <dd>
          <span className={`cfg-status-chip${health?.status === 'ok' ? ' ok' : ''}`}>
            {health?.status || '—'}
          </span>
        </dd>
        <dt>Modo</dt><dd>{health?.mode || '—'}</dd>
        <dt>Ruta</dt><dd className="config-path">{workspace || '—'}</dd>
      </dl>
      <details className="config-advanced">
        <summary>Abrir ruta manualmente (avanzado)</summary>
        <div className="config-field-row" style={{ marginTop: '8px' }}>
          <input
            value={workspaceDraft}
            onChange={(event) => onWorkspaceDraftChange(event.target.value)}
            placeholder="Ruta absoluta al proyecto"
          />
          <button className="config-inline-btn" onClick={() => void onSaveWorkspace()} disabled={loading}>
            Aplicar
          </button>
        </div>
      </details>
    </div>
  );
}
