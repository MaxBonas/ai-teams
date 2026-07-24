import { InfoTip } from '../InfoTip';
import type { CliStatus } from './types';

export function CliSetupGuide({ item }: { item: CliStatus }) {
  if (!item.setup_steps?.length && !item.setup_url && !item.credential_storage) return null;
  return (
    <details className="cli-setup-guide">
      <summary>Guía de configuración</summary>
      {item.login_hint && <p>{item.login_hint}</p>}
      {item.setup_steps?.length ? (
        <ol>
          {item.setup_steps.map((step) => <li key={step}>{step}</li>)}
        </ol>
      ) : null}
      {item.setup_url && (
        <a href={item.setup_url} target="_blank" rel="noreferrer">
          {item.setup_url_label || 'Abrir documentación oficial'}
        </a>
      )}
      {item.credential_storage && <p className="cli-credential-note">{item.credential_storage}</p>}
      {item.post_login_check && <p>Comprobación manual: <code>{item.post_login_check}</code></p>}
    </details>
  );
}

interface CliSettingsProps {
  items: CliStatus[];
  busy: boolean;
  onLogin: (cliId: string) => Promise<void>;
}

export function CliSettings({ items, busy, onLogin }: CliSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        CLIs de suscripción
        <InfoTip
          tip="Si tienes una suscripción activa a ChatGPT Plus, Claude Pro o Gemini Advanced, el CLI correspondiente puede ejecutar agentes sin consumir créditos de API. Requiere instalar el CLI y hacer login en tu cuenta."
          wide
        />
      </div>
      <div className="cli-status-grid">
        {items.map((item) => (
          <div key={item.id} className={`cli-card${item.available ? ' ok' : ''}`}>
            <div className="cli-card-header">
              <span className={`cli-dot${item.available ? ' ok' : ''}`} />
              <span className="cli-card-label">{item.label}</span>
              <span className="cli-card-avail">{item.available ? 'disponible' : 'no instalado'}</span>
            </div>
            <code className="cli-command">{item.login_command || item.command}</code>
            {item.login_supported && (
              <button
                type="button"
                onClick={() => void onLogin(item.id)}
                disabled={busy || !item.available}
                title={item.login_hint}
              >
                {item.id === 'opencode' ? 'Conectar OpenCode Zen' : 'Login'}
              </button>
            )}
            <CliSetupGuide item={item} />
          </div>
        ))}
      </div>
    </div>
  );
}
