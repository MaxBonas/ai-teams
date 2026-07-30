import { FolderInput, FolderPlus } from 'lucide-react';

interface ProjectIdentityStepProps {
  mode: 'create' | 'import';
  name: string;
  path: string;
  projectsRoot: string;
  invalidControlIds: string[];
  onModeChange: (mode: 'create' | 'import') => void;
  onNameChange: (name: string) => void;
  onPathChange: (path: string) => void;
}

export function ProjectIdentityStep({
  mode,
  name,
  path,
  projectsRoot,
  invalidControlIds,
  onModeChange,
  onNameChange,
  onPathChange,
}: ProjectIdentityStepProps) {
  const invalid = (id: string) => invalidControlIds.includes(id);
  return (
    <div className="project-setup-grid two-columns">
      <div className="project-setup-copy">
        <span className="stage-index">01 / IDENTIDAD</span>
        <h2 id="project-step-project-title">¿Proyecto nuevo o carpeta existente?</h2>
        <p>
          Importar solo añade <code>.aiteam/</code> y conserva archivos e historial Git.
        </p>
        <div className="mode-switch" role="group" aria-label="Modo del proyecto">
          <button
            type="button"
            className={mode === 'create' ? 'active' : ''}
            onClick={() => onModeChange('create')}
            aria-pressed={mode === 'create'}
          >
            <FolderPlus size={17} /> Crear nuevo
          </button>
          <button
            type="button"
            className={mode === 'import' ? 'active' : ''}
            onClick={() => onModeChange('import')}
            aria-pressed={mode === 'import'}
          >
            <FolderInput size={17} /> Importar carpeta
          </button>
        </div>
      </div>
      <div className="project-setup-form">
        <label>
          Nombre visible
          <input
            id="project-name"
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            aria-invalid={invalid('project-name')}
            aria-describedby={invalid('project-name') ? 'project-name-error' : undefined}
            required
            autoFocus
          />
          {invalid('project-name') ? (
            <small id="project-name-error" className="field-error" role="alert">
              Escribe un nombre.
            </small>
          ) : null}
        </label>
        {mode === 'import' ? (
          <label>
            Ruta existente dentro de la raíz
            <input
              id="project-path"
              value={path}
              onChange={(event) => onPathChange(event.target.value)}
              placeholder={`${projectsRoot}\\mi-proyecto`}
              aria-invalid={invalid('project-path')}
              aria-describedby={invalid('project-path') ? 'project-path-error' : undefined}
              required
            />
            {invalid('project-path') ? (
              <small id="project-path-error" className="field-error" role="alert">
                Indica la ruta existente.
              </small>
            ) : null}
          </label>
        ) : (
          <div className="path-preview">
            <span>Destino previsto</span>
            <code>{projectsRoot}\{name.trim() || 'proyecto'}</code>
          </div>
        )}
      </div>
    </div>
  );
}
