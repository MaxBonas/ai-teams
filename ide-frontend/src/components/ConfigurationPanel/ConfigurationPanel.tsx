import type { ReactNode } from 'react';

import './ConfigurationPanel.css';

export type ConfigSection =
  | 'proyecto' | 'autonomia' | 'medicion' | 'skills' | 'mcp' | 'danger'
  | 'keys' | 'clis' | 'adapters' | 'sistema';

interface ConfigurationPanelProps {
  projectDisplayName: string;
  section: ConfigSection;
  onSectionChange: (section: ConfigSection) => void;
  children: ReactNode;
}

const PROJECT_SECTIONS: Array<{ id: ConfigSection; label: string; danger?: boolean; testId?: string }> = [
  { id: 'proyecto', label: 'Proyecto activo' },
  { id: 'autonomia', label: 'Autonomía' },
  { id: 'medicion', label: 'Privacidad y medición', testId: 'orientation-config-nav' },
  { id: 'skills', label: 'Skills del proyecto' },
  { id: 'mcp', label: 'Extensiones MCP' },
  { id: 'danger', label: 'Zona de peligro', danger: true },
];

const GLOBAL_SECTIONS: Array<{ id: ConfigSection; label: string }> = [
  { id: 'keys', label: 'Credenciales API' },
  { id: 'clis', label: 'CLIs de suscripción' },
  { id: 'adapters', label: 'Adapters y salud' },
  { id: 'sistema', label: 'Carpeta y sistema' },
];

export function ConfigurationPanel({
  projectDisplayName,
  section,
  onSectionChange,
  children,
}: ConfigurationPanelProps) {
  const renderButton = (item: { id: ConfigSection; label: string; danger?: boolean; testId?: string }) => (
    <button
      key={item.id}
      data-testid={item.testId}
      className={`config-nav-item${item.danger ? ' config-nav-danger' : ''}${section === item.id ? ' active' : ''}`}
      onClick={() => onSectionChange(item.id)}
    >
      {item.label}
    </button>
  );

  return (
    <section className="panel config-panel">
      <div className="config-layout">
        <nav className="config-nav" aria-label="Secciones de configuración">
          <div className="config-nav-group">Este proyecto · {projectDisplayName}</div>
          {PROJECT_SECTIONS.map(renderButton)}
          <div className="config-nav-group">Aplicación · global</div>
          {GLOBAL_SECTIONS.map(renderButton)}
        </nav>
        <div className="config-main">
          {PROJECT_SECTIONS.some((item) => item.id === section) ? (
            <p className="config-scope-note">Ámbito: solo este proyecto. Los demás proyectos no cambian.</p>
          ) : (
            <p className="config-scope-note">Ámbito: toda la aplicación — afecta a todos los proyectos.</p>
          )}
          {children}
        </div>
      </div>
    </section>
  );
}
