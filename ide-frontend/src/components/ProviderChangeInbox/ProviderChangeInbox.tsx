import { useCallback, useEffect, useState } from 'react';

import { apiFetch } from '../../lib/api';
import './ProviderChangeInbox.css';

type Severity = 'critical' | 'error' | 'warning' | 'info';

interface ProviderNotification {
  id: string;
  revision: number;
  event_status: string;
  workflow_status: string;
  severity: Severity;
  title: string;
  summary: string;
  provider: {
    profile_id?: string | null;
    provider_id: string;
    component_id: string;
    surface: string;
  };
  change: {
    kind: string;
    dimension: string;
    decision?: string | null;
    occurrences: number;
  };
  impact: {
    profile_ids: string[];
    model_ids: string[];
    roles: string[];
    existing_assignment_policy?: string | null;
    new_selection_policy?: string | null;
  };
  age: { label: string; band: string; first_seen_at: string; last_seen_at: string };
  next_action: { action: string; label: string };
  recommendation?: { decision?: string; next_step?: string } | null;
  evidence: string[];
  actions: { acknowledge: boolean; snooze: boolean; manage: boolean };
  snoozed_until?: string | null;
}

interface InboxPayload {
  schema_version: string;
  scope: {
    machine_local: boolean;
    external_delivery_enabled: boolean;
    external_delivery_reason: string;
  };
  counts: { total: number; attention: number; critical: number; snoozed: number };
  banner: { visible: boolean; tone: string; title: string; summary?: string | null };
  notifications: ProviderNotification[];
}

interface ProviderChangeInboxProps {
  compact?: boolean;
}

const SEVERITY_LABELS: Record<Severity, string> = {
  critical: 'Crítico',
  error: 'Incompatible',
  warning: 'Revisar',
  info: 'Informativo',
};

function humanize(value: string): string {
  return value.replaceAll('_', ' ');
}

export function ProviderChangeInbox({ compact = false }: ProviderChangeInboxProps) {
  const [payload, setPayload] = useState<InboxPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const response = await apiFetch('/api/provider-changes/inbox');
      const next = await response.json() as InboxPayload & { detail?: string };
      if (!response.ok) throw new Error(next.detail || `provider_inbox_http_${response.status}`);
      setPayload(next);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'provider_inbox_unavailable');
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const act = async (
    notification: ProviderNotification,
    action: 'acknowledge' | 'snooze',
  ) => {
    setBusy(true);
    try {
      const response = await apiFetch(`/api/provider-changes/cases/${notification.id}/notification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          expected_revision: notification.revision,
          ...(action === 'snooze' ? { snooze_hours: 24 } : {}),
        }),
      });
      const next = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(next.detail || `provider_action_http_${response.status}`);
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'provider_action_failed');
    } finally {
      setBusy(false);
    }
  };

  if (!payload) {
    return (
      <section className={`provider-change-center${compact ? ' compact' : ''}`} aria-busy={!error} role={error ? 'alert' : undefined}>
        <span>{error ? `Centro de cambios no disponible: ${humanize(error)}` : 'Comprobando cambios de proveedores…'}</span>
      </section>
    );
  }

  const visible = compact ? payload.notifications.slice(0, 2) : payload.notifications;
  return (
    <section
      className={`provider-change-center tone-${payload.banner.tone}${compact ? ' compact' : ''}`}
      data-testid={compact ? 'provider-change-banner' : 'provider-change-inbox'}
    >
      <header className="provider-change-header">
        <div className="provider-change-mark" aria-hidden="true">
          {payload.banner.visible ? '!' : '✓'}
        </div>
        <div>
          <span className="provider-change-kicker">RADAR DE PROVEEDORES · LOCAL</span>
          <h2>{payload.banner.title}</h2>
          {!compact && (
            <p>
              Detección, evidencia y decisión permanecen separadas. Ninguna acción
              instala, cambia routing o consume modelos.
            </p>
          )}
        </div>
        <strong className="provider-change-count">{payload.counts.attention}</strong>
      </header>

      {error && <div className="provider-change-inline-error" role="alert">{humanize(error)}</div>}

      {!compact && (
        <div className="provider-change-policy">
          <span aria-hidden="true">✓</span>
          <span>Actividad e interacción guardadas en esta máquina.</span>
          <span>Canales externos desactivados: {humanize(payload.scope.external_delivery_reason)}.</span>
        </div>
      )}

      {visible.length === 0 ? (
        <div className="provider-change-empty">
          <span aria-hidden="true">✓</span>
          <div><strong>Sin cambios accionables</strong><span>El scheduler seguirá observando sin instalar nada.</span></div>
        </div>
      ) : (
        <div className="provider-change-list">
          {visible.map((notification) => {
            const isExpanded = expanded === notification.id;
            return (
              <article className={`provider-change-item severity-${notification.severity}`} key={notification.id}>
                <div className="provider-change-rail" />
                <div className="provider-change-item-main">
                  <div className="provider-change-item-top">
                    <span className="provider-severity">{SEVERITY_LABELS[notification.severity]}</span>
                    <code>{notification.provider.provider_id} / {notification.provider.surface}</code>
                    <span className="provider-age">{notification.age.label}</span>
                  </div>
                  <strong>{notification.summary}</strong>
                  <p>{notification.next_action.label}</p>
                  <small>{humanize(notification.workflow_status)} · {humanize(notification.change.dimension)}</small>
                </div>
                <div className="provider-change-actions">
                  {notification.actions.acknowledge && (
                    <button type="button" disabled={busy} onClick={() => void act(notification, 'acknowledge')}>Reconocer</button>
                  )}
                  {notification.actions.snooze && !compact && (
                    <button type="button" disabled={busy} onClick={() => void act(notification, 'snooze')}>24 h</button>
                  )}
                  <button
                    className="primary"
                    type="button"
                    disabled={busy}
                    onClick={() => setExpanded(isExpanded ? '' : notification.id)}
                  >
                    Gestionar <span className={isExpanded ? 'rotate' : ''} aria-hidden="true">⌄</span>
                  </button>
                </div>
                {isExpanded && !compact && (
                  <div className="provider-change-detail">
                    <p><strong>Cambio:</strong> {humanize(notification.change.kind)} · {humanize(notification.change.decision || 'unknown')}</p>
                    <p><strong>Alcance:</strong> {[...notification.impact.profile_ids, ...notification.impact.roles].join(' · ') || 'Pendiente de clasificar'} · {notification.evidence.length} recibo(s)</p>
                    <p>La remediación se ejecuta fuera del workflow. Después registra doctor, probe y recibos en el expediente.</p>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

    </section>
  );
}
