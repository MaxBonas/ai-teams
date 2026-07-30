import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ProviderChangeInbox } from './ProviderChangeInbox';

const emptyInbox = {
  schema_version: 'provider_change_inbox_v1',
  scope: {
    machine_local: true,
    external_delivery_enabled: false,
    external_delivery_reason: 'external_channels_not_configured',
  },
  counts: { total: 0, attention: 0, critical: 0, snoozed: 0 },
  banner: {
    visible: false,
    tone: 'quiet',
    title: 'Proveedores sin cambios pendientes',
    summary: null,
  },
  notifications: [],
};

const notification = {
  id: 'case-1',
  revision: 3,
  event_status: 'open',
  workflow_status: 'awaiting_confirmation',
  severity: 'warning',
  title: 'Cambio de proveedor',
  summary: 'Codex cambió model_id',
  provider: {
    profile_id: 'codex_subscription',
    provider_id: 'openai',
    component_id: 'codex-catalog',
    surface: 'model_catalog',
  },
  change: {
    kind: 'model_changed',
    dimension: 'model_id',
    decision: 'blocked',
    occurrences: 1,
  },
  impact: {
    profile_ids: ['codex_subscription'],
    model_ids: ['gpt-5.6-sol'],
    roles: ['lead'],
    existing_assignment_policy: 'preserve_and_notify',
    new_selection_policy: 'block_affected',
  },
  age: {
    label: 'hace 2 h',
    band: 'today',
    first_seen_at: '2026-07-30T10:00:00+00:00',
    last_seen_at: '2026-07-30T11:00:00+00:00',
  },
  next_action: { action: 'confirm', label: 'Confirmar que el cambio es real' },
  recommendation: { decision: 'blocked', next_step: 'confirm_and_classify' },
  evidence: [],
  actions: { acknowledge: true, snooze: true, manage: true },
  snoozed_until: null,
};

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ProviderChangeInbox', () => {
  it('expone estado local quieto sin fingir canales externos', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(emptyInbox)));
    render(<ProviderChangeInbox />);

    expect(await screen.findByText('Proveedores sin cambios pendientes')).toBeInTheDocument();
    expect(screen.getByText(/actividad e interacción guardadas/i)).toBeInTheDocument();
    expect(screen.getByText(/canales externos desactivados/i)).toBeInTheDocument();
    expect(screen.getByText(/ninguna acción instala/i)).toBeInTheDocument();
  });

  it('abre el expediente y reconoce con la revisión exacta', async () => {
    const inbox = {
      ...emptyInbox,
      counts: { total: 1, attention: 1, critical: 0, snoozed: 0 },
      banner: {
        visible: true,
        tone: 'warning',
        title: '1 cambio requiere atención',
        summary: notification.summary,
      },
      notifications: [notification],
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => (
      init?.method === 'POST'
        ? response({ success: true, case: { revision: 4 }, inbox })
        : response(inbox)
    ));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<ProviderChangeInbox />);

    await screen.findByText(notification.summary);
    await user.click(screen.getByRole('button', { name: /gestionar/i }));
    expect(await screen.findByText(/remediación se ejecuta fuera del workflow/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /reconocer/i }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
      expect(post).toBeDefined();
      expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
        action: 'acknowledge',
        expected_revision: 3,
      });
    });
  });
});
