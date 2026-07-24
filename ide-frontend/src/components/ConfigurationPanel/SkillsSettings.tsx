import { InfoTip } from '../InfoTip';
import type { ProjectSkill, SkillDraft, SkillGovernance } from './types';

interface SkillsSettingsProps {
  governance: SkillGovernance | null;
  skills: ProjectSkill[];
  draft: SkillDraft;
  saving: boolean;
  workspaceConfigured: boolean;
  onDraftChange: (draft: SkillDraft) => void;
  onEdit: (skill: ProjectSkill) => void;
  onToggle: (skill: ProjectSkill) => Promise<void>;
  onDelete: (skill: ProjectSkill) => Promise<void>;
  onSave: () => Promise<void>;
}

export function SkillsSettings({
  governance,
  skills,
  draft,
  saving,
  workspaceConfigured,
  onDraftChange,
  onEdit,
  onToggle,
  onDelete,
  onSave,
}: SkillsSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        Skills del proyecto
        <InfoTip
          tip="Conocimiento local que se inyecta a los roles indicados en cada run, ADEMÁS de su skill base. Refina el rol; nunca contradice tus directivas. Deja los roles vacíos para aplicar a todos."
          wide
        />
      </div>
      {governance && (
        <p className="config-help">
          Uso: {governance.project_skills}/{governance.max_project_skills} skills;{' '}
          {governance.learned_skills}/{governance.max_learned_skills} aprendidas;{' '}
          {governance.active_skill_bytes.toLocaleString()}/{governance.max_active_skill_bytes.toLocaleString()} bytes activos.
        </p>
      )}
      {skills.length > 0 && (
        <div className="skill-list">
          {skills.map((skill) => (
            <div key={skill.name} className={`skill-item${skill.status === 'active' ? '' : ' retired'}`}>
              <div className="skill-item-head">
                <strong>{skill.name}</strong>
                <span className="skill-roles">
                  {skill.applies_to_roles?.length ? skill.applies_to_roles.join(', ') : 'todos los roles'}
                </span>
                {skill.status === 'retired' && <span className="skill-badge">retirada</span>}
                {skill.origin === 'learned' && <span className="skill-badge">aprendida</span>}
                {skill.status === 'proposed' && <span className="skill-badge">pendiente de aprobación</span>}
              </div>
              {skill.evidence?.length ? (
                <div className="config-help">Evidencia: {skill.evidence.join(' · ')}</div>
              ) : null}
              <div className="skill-item-actions">
                <button className="config-inline-btn" onClick={() => onEdit(skill)}>Editar</button>
                <button className="secondary-button" onClick={() => void onToggle(skill)}>
                  {skill.status === 'active' ? 'Retirar' : 'Activar'}
                </button>
                <button className="danger-button" onClick={() => void onDelete(skill)}>Borrar</button>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="skill-form">
        <input
          placeholder="Nombre (p.ej. unity-scene-regen)"
          value={draft.name}
          onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
        />
        <input
          placeholder="Roles separados por coma (vacío = todos)"
          value={draft.roles}
          onChange={(event) => onDraftChange({ ...draft, roles: event.target.value })}
        />
        <textarea
          placeholder="Conocimiento en markdown que verán los agentes…"
          value={draft.body}
          onChange={(event) => onDraftChange({ ...draft, body: event.target.value })}
          rows={4}
        />
        <button
          className="config-inline-btn"
          onClick={() => void onSave()}
          disabled={saving || !workspaceConfigured || !draft.name.trim() || !draft.body.trim()}
        >
          {saving ? 'Guardando…' : 'Guardar skill'}
        </button>
      </div>
    </div>
  );
}
