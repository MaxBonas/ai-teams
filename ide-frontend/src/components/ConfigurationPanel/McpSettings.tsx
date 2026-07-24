import { InfoTip } from '../InfoTip';
import type {
  McpCatalogEntry,
  McpServer,
  McpToolAccess,
  McpToolDrafts,
} from './types';

interface McpSettingsProps {
  catalog: McpCatalogEntry[];
  servers: McpServer[];
  busyServer: string;
  toolDrafts: McpToolDrafts;
  onToolAccessChange: (serverName: string, toolName: string, access: McpToolAccess) => void;
  onHealth: (server: McpServer) => Promise<void>;
  onSavePolicy: (server: McpServer) => Promise<void>;
  onTransition: (server: McpServer, action: 'retire' | 'reactivate') => Promise<void>;
}

export function McpSettings({
  catalog,
  servers,
  busyServer,
  toolDrafts,
  onToolAccessChange,
  onHealth,
  onSavePolicy,
  onTransition,
}: McpSettingsProps) {
  return (
    <div className="config-subsection">
      <div className="config-subsection-label">
        Extensiones MCP
        <InfoTip
          tip="El Lead propone activar un servidor MCP ya instalado cuando el equipo se topa con un límite real. Ejecutar código de terceros SIEMPRE espera tu aprobación en la Bandeja. Después, esta pantalla comprueba el servidor y te permite autorizar cada herramienta como lectura o escritura."
          wide
        />
      </div>
      <div className="mcp-catalog">
        <div className="mcp-tool-policy-title">
          <span>Catálogo revisado</span>
          <small>Solo descriptores: no instala, aprueba ni ejecuta nada.</small>
        </div>
        <div className="skill-list">
          {catalog.map((entry) => (
            <div key={entry.id} className="skill-item mcp-item">
              <div className="skill-item-head">
                <strong>{entry.display_name}</strong>
                <code className="mcp-version">{entry.distribution_version}</code>
                <span className="skill-badge">revisado {entry.reviewed_at}</span>
              </div>
              <p className="config-hint" style={{ margin: 0 }}>{entry.description}</p>
              <p className="config-hint" style={{ margin: 0 }}>
                ID <code>{entry.id}</code> · requiere <code>{entry.required_local_command}</code> · serverInfo <code>{entry.version}</code>
              </p>
              <p className="config-hint" style={{ margin: 0 }}>
                Roles: {entry.applies_to_roles.join(', ')} · Capacidades: {entry.capabilities.join(', ')}
              </p>
              <p className="config-hint" style={{ margin: 0 }}>{entry.risk}</p>
              <div className="skill-item-actions">
                <a className="config-inline-btn" href={entry.homepage} target="_blank" rel="noreferrer">Fuente oficial</a>
                <button className="secondary-button" onClick={() => void navigator.clipboard.writeText(entry.id)}>Copiar ID</button>
              </div>
            </div>
          ))}
        </div>
      </div>
      {servers.length === 0 ? (
        <p className="config-hint">
          Ninguna todavía. Cuando el Lead identifique una necesidad real, te llegará una propuesta a la Bandeja.
        </p>
      ) : (
        <div className="skill-list">
          {servers.map((server) => (
            <div key={server.name} className={`skill-item mcp-item status-${server.status || 'approved'}`}>
              <div className="skill-item-head">
                <strong>{server.name}</strong>
                {server.version && <code className="mcp-version">v{server.version}</code>}
                <span className="skill-roles">
                  {server.applies_to_roles?.length ? server.applies_to_roles.join(', ') : 'sin roles asignados'}
                </span>
                <span className="skill-badge">{server.status || 'approved'}</span>
              </div>
              {server.source && <p className="config-hint" style={{ margin: 0 }}><code>{server.source}</code></p>}
              {server.justification && <p className="config-hint" style={{ margin: 0 }}>{server.justification}</p>}
              {server.health && (
                <div className="mcp-health-line">
                  <span className={`mcp-health-dot ${server.health.status === 'ok' ? 'ok' : 'bad'}`} />
                  <span>{server.health.status === 'ok' ? 'Health vigente' : server.health.detail || 'Health fallido'}</span>
                  {server.health.consecutive_failures ? <strong>{server.health.consecutive_failures}/3 fallos</strong> : null}
                </div>
              )}
              {server.status === 'active' && Boolean(server.health?.tools?.length) && (
                <div className="mcp-tool-policy">
                  <div className="mcp-tool-policy-title">
                    <span>Permisos explícitos</span>
                    <small>Las sugerencias del servidor no conceden acceso.</small>
                  </div>
                  {server.health?.tools?.map((tool) => (
                    <label key={tool.name} className="mcp-tool-row">
                      <code>{tool.name}</code>
                      <span>{tool.read_only ? 'sugiere lectura' : 'sin garantía'}</span>
                      <select
                        value={toolDrafts[server.name]?.[tool.name] || 'off'}
                        onChange={(event) => onToolAccessChange(
                          server.name,
                          tool.name,
                          event.target.value as McpToolAccess,
                        )}
                        disabled={busyServer === server.name}
                      >
                        <option value="off">No autorizar</option>
                        <option value="read">Lectura</option>
                        <option value="write">Escritura</option>
                      </select>
                    </label>
                  ))}
                </div>
              )}
              <div className="skill-item-actions mcp-actions">
                {['approved', 'failed', 'active'].includes(server.status || 'approved') && (
                  <button className="config-inline-btn" onClick={() => void onHealth(server)} disabled={Boolean(busyServer)}>
                    {busyServer === server.name ? 'Comprobando…' : server.status === 'approved' ? 'Probar y activar' : 'Comprobar ahora'}
                  </button>
                )}
                {server.status === 'active' && Boolean(server.health?.tools?.length) && (
                  <button onClick={() => void onSavePolicy(server)} disabled={Boolean(busyServer)}>
                    Guardar permisos
                  </button>
                )}
                {!['retired', 'rejected'].includes(server.status || '') ? (
                  <button className="danger-button" onClick={() => void onTransition(server, 'retire')} disabled={Boolean(busyServer)}>Retirar</button>
                ) : (
                  <button className="secondary-button" onClick={() => void onTransition(server, 'reactivate')} disabled={Boolean(busyServer)}>Reactivar</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
