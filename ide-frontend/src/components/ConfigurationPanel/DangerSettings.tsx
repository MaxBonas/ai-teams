import { InfoTip } from '../InfoTip';

interface DangerSettingsProps {
  projectLabel: string;
  confirmation: string;
  busy: boolean;
  onConfirmationChange: (value: string) => void;
  onDelete: () => Promise<void>;
}

export function DangerSettings({
  projectLabel,
  confirmation,
  busy,
  onConfirmationChange,
  onDelete,
}: DangerSettingsProps) {
  return (
    <div className="config-subsection danger-config-section">
      <div className="config-subsection-label danger-config-title">
        Zona de peligro
        <InfoTip
          tip="Las acciones de esta sección son irreversibles. No hay papelera de reciclaje: el proyecto se borra permanentemente del disco."
          wide
        />
      </div>
      <div className="danger-zone">
        <div className="danger-zone-desc">
          <strong>Eliminar proyecto actual</strong>
          <p>
            Borra la carpeta completa del proyecto{projectLabel ? `: ${projectLabel}` : ''}.
            Esta acción no se puede deshacer.
          </p>
        </div>
        <div className="delete-row">
          <input
            aria-label="Confirmación para eliminar el proyecto"
            value={confirmation}
            onChange={(event) => onConfirmationChange(event.target.value)}
            placeholder="Escribe DELETE para confirmar"
          />
          <button
            type="button"
            className="danger-button"
            onClick={() => void onDelete()}
            disabled={busy || confirmation !== 'DELETE'}
          >
            Eliminar proyecto
          </button>
        </div>
      </div>
    </div>
  );
}
