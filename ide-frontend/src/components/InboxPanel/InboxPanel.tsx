import type { ReactNode } from 'react';
import { Bell } from 'lucide-react';

import { formatTime, parseTime, statusLabel } from '../../lib/format';
import './InboxPanel.css';

export interface InboxInteraction {
  id: string;
  issue_id: string;
  kind: string;
  status: string;
  title?: string | null;
  created_at?: string;
  resolved_at?: string | null;
}

interface InboxPanelProps<TInteraction extends InboxInteraction> {
  interactions: TInteraction[];
  pendingInteractions: TInteraction[];
  selectedInteractionId: string | null;
  onSelect: (interactionId: string) => void;
  renderDetail: (interaction: TInteraction | null) => ReactNode;
}

export function InboxPanel<TInteraction extends InboxInteraction>({
  interactions,
  pendingInteractions,
  selectedInteractionId,
  onSelect,
  renderDetail,
}: InboxPanelProps<TInteraction>) {
  const resolved = interactions
    .filter((interaction) => interaction.status !== 'pending')
    .sort((a, b) => parseTime(b.resolved_at || b.created_at) - parseTime(a.resolved_at || a.created_at))
    .slice(0, 20);
  const ordered = [...pendingInteractions, ...resolved];
  const current = ordered.find((interaction) => interaction.id === selectedInteractionId)
    || pendingInteractions[0]
    || ordered[0]
    || null;

  return (
    <section className="panel inbox-panel">
      <div className="inbox-layout">
        <aside className="inbox-list">
          <div className="inbox-list-header">
            <Bell size={14} />
            <span>Decisiones</span>
            {pendingInteractions.length > 0 && <span className="notif-badge">{pendingInteractions.length}</span>}
          </div>
          {ordered.length === 0 && (
            <p className="muted inbox-empty">
              Nada pendiente. Las preguntas y propuestas del equipo aparecerán aquí.
            </p>
          )}
          {ordered.map((interaction) => (
            <button
              key={interaction.id}
              className={`inbox-item${interaction.id === current?.id ? ' active' : ''}${interaction.status === 'pending' ? ' pending' : ' resolved'}`}
              onClick={() => onSelect(interaction.id)}
            >
              <span className="inbox-item-kind">
                {interaction.kind}{' · '}{formatTime(interaction.created_at)}
              </span>
              <span className="inbox-item-title">{interaction.title || interaction.kind}</span>
              <span className={`inbox-item-status status-${interaction.status}`}>{statusLabel(interaction.status)}</span>
            </button>
          ))}
        </aside>
        <div className="inbox-detail">{renderDetail(current)}</div>
      </div>
    </section>
  );
}
