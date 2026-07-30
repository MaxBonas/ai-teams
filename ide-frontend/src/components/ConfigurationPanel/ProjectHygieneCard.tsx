import { FolderSearch2, ShieldCheck } from 'lucide-react';

import type { ProjectHygiene } from './types';
import './ProjectHygieneCard.css';

interface ProjectHygieneCardProps {
  root: string;
  hygiene: ProjectHygiene | null;
  previewRoot: string;
  busy: boolean;
  onInspect: () => void;
}

const statusCopy: Record<ProjectHygiene['status'], { label: string; tone: string }> = {
  clean: { label: 'Raíz limpia', tone: 'clean' },
  legacy_artifacts_detected: { label: 'Legado detectado', tone: 'warning' },
  review_required: { label: 'Revisión necesaria', tone: 'warning' },
  root_missing: { label: 'La carpeta no existe', tone: 'neutral' },
  not_configured: { label: 'Pendiente de configurar', tone: 'neutral' },
};

export function ProjectHygieneCard({
  root,
  hygiene,
  previewRoot,
  busy,
  onInspect,
}: ProjectHygieneCardProps) {
  const normalizedRoot = root.trim();
  const isCurrent = Boolean(hygiene && normalizedRoot && previewRoot === normalizedRoot);
  const state = hygiene && isCurrent ? statusCopy[hygiene.status] : null;
  const counts = hygiene && isCurrent ? hygiene.counts : null;

  return (
    <section className="hygiene-card" aria-labelledby="project-hygiene-title">
      <div className="hygiene-card-heading">
        <span className="hygiene-card-icon" aria-hidden="true"><FolderSearch2 size={17} /></span>
        <div>
          <h3 id="project-hygiene-title">Control de la raíz</h3>
          <p>Inspección ligera, local y de solo lectura.</p>
        </div>
        <span
          className={`hygiene-status ${state?.tone || 'neutral'}`}
          role="status"
          aria-live="polite"
        >
          {busy ? 'Comprobando…' : state?.label || 'Sin comprobar'}
        </span>
      </div>

      {normalizedRoot ? <code className="hygiene-root">{normalizedRoot}</code> : null}

      {counts ? (
        <dl className="hygiene-metrics">
          <div><dt>Proyectos AI Teams</dt><dd>{counts.aiteam_projects}</dd></div>
          <div><dt>Legado numerado</dt><dd>{counts.legacy_numbered}</dd></div>
          <div><dt>Restos temporales</dt><dd>{counts.staging_leftovers}</dd></div>
          <div><dt>Revisión manual</dt><dd>{counts.reparse_points + counts.scan_errors}</dd></div>
        </dl>
      ) : (
        <p className="hygiene-prompt">
          Comprueba esta ruta antes de guardarla para detectar restos de ejecuciones antiguas.
        </p>
      )}

      {hygiene && isCurrent && hygiene.status !== 'clean' ? (
        <p className="hygiene-advice">{hygiene.recommended_action.description}</p>
      ) : null}

      <div className="hygiene-safety">
        <ShieldCheck size={15} aria-hidden="true" />
        <span>
          No mueve ni borra carpetas. Las carpetas sin identidad <code>.aiteam</code> se
          consideran personales y quedan protegidas.
        </span>
      </div>

      <button
        type="button"
        className="hygiene-inspect-button"
        onClick={onInspect}
        disabled={busy || !normalizedRoot}
      >
        {busy ? 'Comprobando…' : isCurrent ? 'Volver a comprobar' : 'Comprobar sin guardar'}
      </button>
    </section>
  );
}
