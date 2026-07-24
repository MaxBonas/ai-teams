import { InfoTip } from '../InfoTip';
import { pretty } from '../../lib/format';

interface SystemSettingsProps {
  draftRoot: string;
  effectiveRoot: string;
  backendOrigin: string;
  mode?: string;
  lastResult: unknown;
  busy: boolean;
  onDraftChange: (value: string) => void;
  onSave: () => Promise<void>;
}

export function SystemSettings({
  draftRoot,
  effectiveRoot,
  backendOrigin,
  mode,
  lastResult,
  busy,
  onDraftChange,
  onSave,
}: SystemSettingsProps) {
  return (
    <>
      <div className="config-subsection">
        <div className="config-subsection-label">
          Carpeta raíz de proyectos
          <InfoTip
            tip="Todos los proyectos se crean como subcarpetas aquí. Cambiarla no mueve proyectos existentes. También configurable con AITEAM_PROJECTS_ROOT en .env (tiene prioridad)."
            wide
          />
        </div>
        <div className="config-field-row">
          <input
            className="config-path-input"
            aria-label="Carpeta raíz de proyectos"
            value={draftRoot || effectiveRoot}
            onChange={(event) => onDraftChange(event.target.value)}
            placeholder="Ruta absoluta de la carpeta de proyectos"
          />
          <button
            type="button"
            className="config-inline-btn"
            onClick={() => void onSave()}
            disabled={busy || !draftRoot.trim()}
          >
            Guardar
          </button>
        </div>
        {effectiveRoot && <p className="config-hint">Efectiva: <code>{effectiveRoot}</code></p>}
      </div>
      <div className="config-subsection">
        <div className="config-subsection-label">Sistema</div>
        <dl className="config-dl config-dl-compact">
          <dt>Backend</dt><dd><code>{backendOrigin}</code></dd>
          <dt>Modo</dt><dd>{mode || '—'}</dd>
          <dt>Var. entorno</dt>
          <dd>
            <code>AITEAM_PROJECTS_ROOT</code> en <code>.env</code> sobreescribe la carpeta raíz guardada.
            <InfoTip
              tip="Si defines AITEAM_PROJECTS_ROOT en el archivo .env del proyecto, tiene prioridad sobre lo que configures en esta pantalla. Útil para CI/CD o instalaciones sin UI."
              wide
            />
          </dd>
        </dl>
      </div>
      {lastResult ? (
        <details className="config-subsection config-debug">
          <summary>Última acción — debug</summary>
          <pre className="last-result-body">{pretty(lastResult).slice(0, 1200)}</pre>
        </details>
      ) : null}
    </>
  );
}
