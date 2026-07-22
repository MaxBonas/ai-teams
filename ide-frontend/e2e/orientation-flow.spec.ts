import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

const issue = {
  id: 'issue:intake',
  title: 'Diseñar autorización multi-tenant',
  status: 'in_progress',
  role: 'lead',
  assignee_agent_id: 'role:lead',
  metadata_json: JSON.stringify({ profile: 'lead_quorum', data_class: 'internal' }),
};

const planDocument = {
  id: 'doc:plan',
  issue_id: issue.id,
  key: 'plan',
  title: 'Plan aceptado',
  body: 'Plan durable',
  format: 'markdown',
  revision_number: 3,
  current_revision_id: 'rev:accepted',
  plan: null,
};

const quorum = {
  success: true,
  issue_id: issue.id,
  session: {
    id: 'quorum:1',
    issue_id: issue.id,
    status: 'accepted',
    requested_contributions: 2,
    min_valid_contributions: 2,
    final_plan_revision_id: 'rev:accepted',
  },
  contributions: [
    { ordinal: 1, provider: 'provider-a', valid: true },
    { ordinal: 2, provider: 'provider-b', valid: true },
  ],
  gate: {
    ready: true,
    status: 'accepted',
    valid_contributions: 2,
    total_contributions: 2,
    distinct_providers: 2,
    missing_valid: 0,
    diversity_satisfied: true,
    reduced_quorum: false,
  },
};

test('orientación: Bandeja, perfiles y CTA del plan requieren pocos pasos observables', async ({ page }, testInfo) => {
  const browserErrors: string[] = [];
  let orientationEnabled = false;
  let orientationSessionId: string | null = null;
  const orientationEvents: Array<Record<string, unknown>> = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      browserErrors.push(message.text());
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 400) browserErrors.push(`${response.status()} ${response.url()}`);
  });

  await page.route('http://127.0.0.1:8010/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    let status = 200;
    let body: unknown;

    if (path === '/api/health') body = { status: 'ok', mode: 'test' };
    else if (path === '/api/settings') body = { configured: true, projects_root_effective: 'C:/projects' };
    else if (path === '/api/workspace') body = { configured: true, workspace: 'C:/projects/demo', project_name: 'Demo', projects_root: 'C:/projects' };
    else if (path === '/api/project/state') body = {
      success: true,
      selected_issue_id: issue.id,
      issues: [issue],
      agents: [],
      runs: [],
      timeline: [],
      comments: [],
      interactions: [],
      plan_document: planDocument,
    };
    else if (path === `/api/issues/${encodeURIComponent(issue.id)}/quorum`) body = quorum;
    else if (path === '/api/chat') body = { messages: [] };
    else if (path === '/api/workspace/files') body = { files: [] };
    else if (path === '/api/projects') body = { projects: [] };
    else if (path === '/api/budget') body = { budgets: [] };
    else if (path === '/api/costs/summary') body = {
      totals: { actual_cost_cents: 0, estimated_savings_cents: 0, runs: 0 },
      by_role: [],
    };
    else if (path === '/api/loop-health') body = {
      detected_loops: [],
      at_risk: [],
      capacity_profiles: [],
    };
    else if (path === '/api/tools/catalog') body = { catalog: {} };
    else if (path === '/api/user-adapters') body = { profiles: [], cli_status: [], secrets: [] };
    else if (path === '/api/project/skills') body = { skills: [], governance: null };
    else if (path === '/api/project/extensions/mcp') body = { mcp_servers: [] };
    else if (path === '/api/project/extensions/mcp/catalog') body = { entries: [] };
    else if (path === '/api/orientation-measurement' && method === 'GET') body = {
      success: true,
      consent: { enabled: orientationEnabled, current_session_id: orientationSessionId, consented_at: null, revoked_at: null },
      sessions: { active: orientationSessionId ? 1 : 0, completed: 0, abandoned: 0, revoked: 0 },
      event_count: orientationEvents.length,
      flows: {},
      privacy: { storage: 'local_project_sqlite', external_transmission: false, free_text_collected: false, issue_or_workspace_ids_collected: false },
      interpretation: { constructs_not_measured: ['adoption', 'clarity', 'satisfaction', 'causality'], conclusion_allowed: false, reason: 'test' },
    };
    else if (path === '/api/orientation-measurement/consent' && method === 'POST') {
      orientationEnabled = Boolean((route.request().postDataJSON() as { enabled?: boolean }).enabled);
      orientationSessionId = orientationEnabled ? 'orientation:test' : null;
      body = { success: true, consent: { enabled: orientationEnabled, current_session_id: orientationSessionId } };
    }
    else if (path === '/api/orientation-measurement/events' && method === 'POST') {
      if (!orientationEnabled) {
        status = 409;
        body = { detail: 'orientation_measurement_not_consented' };
      } else {
        const payload = route.request().postDataJSON() as Record<string, unknown>;
        orientationEvents.push(payload);
        body = { success: true, event: { id: `event:${orientationEvents.length}`, ...payload } };
      }
    }
    else if (path === '/api/orientation-measurement/session/end' && method === 'POST') {
      orientationSessionId = null;
      body = { success: true, session: { status: (route.request().postDataJSON() as { status: string }).status } };
    }
    else if (path === '/api/orientation-measurement' && method === 'DELETE') {
      orientationEnabled = false;
      orientationSessionId = null;
      orientationEvents.length = 0;
      body = { success: true, deleted_events: 0, deleted_sessions: 1, enabled: false };
    }
    else {
      status = 404;
      body = { detail: `fixture_missing:${path}` };
    }

    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.goto('/');
  await expect(page.getByTestId('project-cockpit')).toBeVisible();

  await page.getByTestId('config-tab').click();
  await page.getByTestId('orientation-config-nav').click();
  await expect(page.getByTestId('orientation-measurement-panel')).toContainText('sin leer tu trabajo');
  await expect(page.getByTestId('orientation-measurement-panel')).toContainText('0 transmisión externa');
  await expect(page.getByTestId('orientation-consent-toggle')).toHaveAttribute('aria-pressed', 'false');
  await page.getByTestId('orientation-consent-toggle').click();
  await expect(page.getByTestId('orientation-consent-toggle')).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: testInfo.outputPath('orientation-consent.png'), fullPage: true });

  const metrics = {
    schema_version: 1,
    measurement: 'deterministic_ui_contract',
    real_user_adoption_measured: false,
    flows: {
      inbox: { actions: 1, completed: false },
      profile_selection: { actions_per_profile: 1, completed_profiles: [] as string[] },
      accepted_plan_to_task: { actions: 2, completed: false },
    },
    browser_errors: browserErrors,
    abandoned_flows: [] as string[],
  };

  await page.getByTestId('inbox-tab').click();
  await expect(page.getByTestId('inbox-tab')).toHaveClass(/active/);
  metrics.flows.inbox.completed = true;

  const expectedGuidance: Record<string, string> = {
    solo_lead: 'Sin revisión independiente',
    lead_quorum: 'Reduce ambigüedad',
    full_team: 'Reduce errores de implementación',
  };
  for (const [profile, guidance] of Object.entries(expectedGuidance)) {
    await page.getByTestId(`task-profile-${profile}`).click();
    await expect(page.getByTestId(`task-profile-${profile}`)).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('profile-guidance')).toContainText('Coste operativo:');
    await expect(page.getByTestId('profile-guidance')).toContainText('Riesgo:');
    await expect(page.getByTestId('profile-guidance')).toContainText(guidance);
    metrics.flows.profile_selection.completed_profiles.push(profile);
  }

  await page.getByTestId('plan-tab').click();
  await expect(page.getByTestId('accepted-plan-cta')).toBeVisible();
  await page.getByTestId('accepted-plan-cta').click();
  await expect(page.getByTestId('attached-plan')).toContainText('Plan aceptado adjunto');
  await expect(page.getByTestId('new-task-draft')).toHaveValue(/Ejecuta el plan aceptado/);
  await expect(page.getByTestId('task-profile-full_team')).toHaveAttribute('aria-pressed', 'true');
  metrics.flows.accepted_plan_to_task.completed = true;

  for (const [name, flow] of Object.entries(metrics.flows)) {
    if ('completed' in flow && !flow.completed) metrics.abandoned_flows.push(name);
  }
  expect(browserErrors).toEqual([]);
  expect(metrics.abandoned_flows).toEqual([]);
  expect(metrics.flows.profile_selection.completed_profiles).toEqual([
    'solo_lead',
    'lead_quorum',
    'full_team',
  ]);
  await expect.poll(() => orientationEvents.length).toBeGreaterThanOrEqual(11);
  expect(orientationEvents.every((event) => Object.keys(event).every((key) => ['flow', 'event', 'profile'].includes(key)))).toBe(true);
  expect(orientationEvents).toContainEqual({ flow: 'inbox', event: 'flow_completed' });
  expect(orientationEvents).toContainEqual({ flow: 'accepted_plan_to_task', event: 'flow_started', profile: 'full_team' });

  await writeFile(testInfo.outputPath('orientation-metrics.json'), JSON.stringify(metrics, null, 2));
  await page.screenshot({ path: testInfo.outputPath('orientation.png'), fullPage: true });

  await testInfo.attach('orientation-metrics.json', {
    body: JSON.stringify(metrics, null, 2),
    contentType: 'application/json',
  });
});
