import { InfoTip } from '../InfoTip';
import type { SecretInfo } from './types';
import './ConnectionSettings.css';

const PROVIDERS = [
  { id: 'openai', label: 'OpenAI', desc: 'GPT-5.6 Sol / Terra / Luna' },
  { id: 'google', label: 'Google Gemini', desc: 'Gemini 3.1 Pro / 3.5 Flash / Flash-Lite' },
  { id: 'google-free', label: 'Google Gemini Free', desc: 'Free tier BYOK · separado del perfil pagado' },
  { id: 'anthropic', label: 'Anthropic', desc: 'Claude Opus 4.8 / Sonnet 5 / Haiku 4.5' },
  { id: 'groq', label: 'Groq Free', desc: 'GPT-OSS 120B, Qwen 3.6, Llama 3.3' },
] as const;

interface ApiKeysSettingsProps {
  secrets: SecretInfo[];
  activeProvider: string;
  secretValue: string;
  busy: boolean;
  onProviderChange: (provider: string) => void;
  onSecretChange: (value: string) => void;
  onSave: () => Promise<void>;
}

export function ApiKeysSettings({
  secrets,
  activeProvider,
  secretValue,
  busy,
  onProviderChange,
  onSecretChange,
  onSave,
}: ApiKeysSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        API Keys
        <InfoTip
          tip="Cada key activa los adapters de API directa de ese proveedor. Se envían al backend local y se cifran en vault; una vez guardadas solo se indica si existen — no se pueden leer de vuelta."
          wide
        />
      </div>
      <div className="api-key-rows">
        {PROVIDERS.map((provider) => {
          const saved = secrets.some((secret) => secret.provider === provider.id && secret.has_secret);
          const active = activeProvider === provider.id;
          return (
            <div key={provider.id} className={`api-key-row${saved ? ' key-saved' : ''}`}>
              <div className="api-key-row-meta">
                <span className={`api-key-dot${saved ? ' saved' : ''}`} />
                <span className="api-key-label">{provider.label}</span>
                <span className="api-key-models">{provider.desc}</span>
                <span className={`api-key-badge${saved ? ' ok' : ''}`}>
                  {saved ? 'key guardada ✓' : 'sin key'}
                </span>
              </div>
              <div className="api-key-row-input">
                <input
                  type="password"
                  aria-label={`API key de ${provider.label}`}
                  autoComplete="off"
                  placeholder={saved ? '●●●●●●  (guardada — pega nueva para actualizar)' : 'Pega tu API key aquí'}
                  value={active ? secretValue : ''}
                  onFocus={() => onProviderChange(provider.id)}
                  onChange={(event) => {
                    onProviderChange(provider.id);
                    onSecretChange(event.target.value);
                  }}
                />
                <button
                  type="button"
                  className="config-inline-btn"
                  disabled={busy || !active || !secretValue.trim()}
                  onClick={() => void onSave()}
                >
                  {saved ? 'Actualizar' : 'Guardar'}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
