import { AlertCircle, LockKeyhole, Trash2 } from 'lucide-react';

export interface OrientationMeasurement {
  consent: {
    enabled: boolean;
    current_session_id: string | null;
    consented_at: string | null;
    revoked_at: string | null;
  };
  sessions: Record<'active' | 'completed' | 'abandoned' | 'revoked', number>;
  event_count: number;
  flows: Record<string, Record<string, number>>;
  privacy: {
    storage: string;
    external_transmission: boolean;
    free_text_collected: boolean;
    issue_or_workspace_ids_collected: boolean;
  };
  interpretation: {
    constructs_not_measured: string[];
    conclusion_allowed: boolean;
    reason: string;
  };
}

interface OrientationSettingsProps {
  measurement: OrientationMeasurement | null;
  busy: boolean;
  onConsentChange: (enabled: boolean) => Promise<void>;
  onErase: () => Promise<void>;
}

export function OrientationSettings({
  measurement,
  busy,
  onConsentChange,
  onErase,
}: OrientationSettingsProps) {
  const enabled = Boolean(measurement?.consent.enabled);
  const hasMeasurements = Boolean(
    measurement
    && (
      measurement.event_count > 0
      || Object.values(measurement.sessions).some((count) => count > 0)
    ),
  );

  return (
    <div className="config-subsection orientation-lab" data-testid="orientation-measurement-panel">
      <div className="orientation-lab-head">
        <div className="orientation-lab-icon"><LockKeyhole size={19} /></div>
        <div>
          <span className="orientation-eyebrow">Instrumentación local · opt-in</span>
          <h3>Medir la orientación sin leer tu trabajo</h3>
        </div>
        <span className={`orientation-signal${enabled ? ' live' : ''}`} data-testid="orientation-measurement-status">
          <i />{enabled ? 'Midiendo' : 'Apagado'}
        </span>
      </div>

      <p className="orientation-intro">
        Registra únicamente pasos en Bandeja, selección de perfil y plan aceptado → tarea.
        Los datos se quedan en la SQLite de este proyecto y nunca incluyen títulos, prompts,
        rutas, issues ni texto escrito.
      </p>

      <div className="orientation-privacy-grid" aria-label="Garantías de privacidad">
        <span><strong>LOCAL</strong> SQLite del proyecto</span>
        <span><strong>0</strong> transmisión externa</span>
        <span><strong>0</strong> campos de texto</span>
        <span><strong>3</strong> flujos cerrados</span>
      </div>

      <div className="orientation-console">
        <div><strong>{Object.values(measurement?.sessions || {}).reduce((sum, count) => sum + count, 0)}</strong><span>sesiones</span></div>
        <div><strong>{measurement?.sessions.abandoned || 0}</strong><span>abandonos</span></div>
        <div><strong>{measurement?.event_count || 0}</strong><span>eventos</span></div>
      </div>

      <div className="orientation-actions">
        <button
          type="button"
          data-testid="orientation-consent-toggle"
          aria-pressed={enabled}
          className={`orientation-consent-button${enabled ? ' active' : ''}`}
          disabled={busy || !measurement}
          onClick={() => void onConsentChange(!enabled)}
        >
          <span className="orientation-toggle-track"><i /></span>
          {enabled ? 'Revocar consentimiento' : 'Activar medición local'}
        </button>
        <button
          type="button"
          data-testid="orientation-delete-data"
          className="orientation-delete-button"
          disabled={busy || !hasMeasurements}
          onClick={() => void onErase()}
        >
          <Trash2 size={14} /> Borrar medidas
        </button>
      </div>

      <p className="orientation-boundary">
        <AlertCircle size={14} /> Estos conteos detectan fricción y abandono; por sí solos no
        demuestran adopción, claridad, satisfacción ni causalidad. Las conclusiones requieren
        sesiones humanas y un criterio definido antes de observarlas.
      </p>
    </div>
  );
}
