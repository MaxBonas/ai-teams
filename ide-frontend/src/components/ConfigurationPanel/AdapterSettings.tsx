import { InfoTip } from '../InfoTip';
import type { AdapterSettingsRow } from './types';

interface AdapterSettingsProps {
  rows: AdapterSettingsRow[];
  busy: boolean;
  onModelChange: (profileId: string, model: string) => void;
  onTest: (profileId: string, model: string) => Promise<void>;
}

export function AdapterSettings({
  rows,
  busy,
  onModelChange,
  onTest,
}: AdapterSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        Estado de adapters
        <InfoTip
          tip="Prueba un adapter para verificar que su credencial es válida y la llamada funciona. El resultado se guarda y actualiza los indicadores de conexión en toda la UI."
          wide
        />
      </div>
      <div className="adapter-test-list">
        {rows.map(({ profile, connected, testModel }) => {
          const healthStatus = profile.health?.status || 'untested';
          const blocked = profile.status === 'blocked_by_provider';
          return (
            <div
              key={profile.id}
              className={`adapter-test-row${connected ? ' connected' : ''}${blocked ? ' blocked' : ''}`}
            >
              <span className={`adapter-row-dot${connected ? ' connected' : blocked ? ' blocked' : ''}`} />
              <div className="adapter-test-info">
                <span className="adapter-test-label">{profile.label}</span>
                <span className="adapter-test-meta">{profile.adapter_type} · {profile.channel}</span>
              </div>
              <span className={`adapter-test-status hs-${healthStatus}`}>
                {blocked ? 'bloqueado por proveedor'
                  : healthStatus === 'ok' ? `funcional${profile.health?.reason ? ` · ${profile.health.reason}` : ''}`
                  : healthStatus === 'failed' ? `falló: ${profile.health?.reason || 'test'}`
                  : healthStatus === 'installed' ? 'instalado, auth sin verificar'
                  : 'sin probar'}
              </span>
              {profile.model_options?.length ? (
                <select
                  aria-label={`Modelo de prueba para ${profile.label}`}
                  value={testModel}
                  onChange={(event) => onModelChange(profile.id, event.target.value)}
                >
                  {profile.model_options.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              ) : null}
              <button
                className="secondary-button"
                type="button"
                disabled={busy || blocked}
                onClick={() => void onTest(profile.id, testModel)}
              >
                Probar
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
