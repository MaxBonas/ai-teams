import { InfoTip } from '../InfoTip';

interface AutonomySettingsProps {
  mode: string;
  saving: boolean;
  workspaceConfigured: boolean;
  onSave: (mode: string) => Promise<void>;
}

export function AutonomySettings({
  mode,
  saving,
  workspaceConfigured,
  onSave,
}: AutonomySettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        Autonomía
        <InfoTip
          tip="Supervisado: todas las escalaciones esperan tu decisión. Autónomo: las escalaciones operativas (breakers, bucles, hijos bloqueados) se auto-resuelven con su opción segura una vez por issue; las decisiones de producto (cierre de ciclo, alcance, preguntas) siempre te esperan."
          wide
        />
      </div>
      <div className="config-field-row">
        <button
          className={mode === 'supervised' ? 'config-inline-btn' : 'secondary-button'}
          onClick={() => void onSave('supervised')}
          disabled={saving || !workspaceConfigured}
        >
          Supervisado
        </button>
        <button
          className={mode === 'autonomous' ? 'config-inline-btn' : 'secondary-button'}
          onClick={() => void onSave('autonomous')}
          disabled={saving || !workspaceConfigured}
        >
          Autónomo
        </button>
      </div>
      <p className="config-hint">
        Modo actual: <code>{mode}</code>
        {mode === 'autonomous'
          ? ' — las interacciones operativas se resuelven solas (una vez por issue y motivo).'
          : ' — el equipo se detiene en cada escalación hasta que respondas.'}
        {' '}También conmutable desde la barra superior.
      </p>
    </div>
  );
}
