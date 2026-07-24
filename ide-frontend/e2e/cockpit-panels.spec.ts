import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const issue = {
  id: 'issue:intake',
  title: 'Validar cockpit modular',
  description: 'Comprobar Chat, Issue y Runs tras la extracción.',
  status: 'in_progress',
  role: 'lead',
  assignee_agent_id: 'role:lead',
  metadata_json: JSON.stringify({ profile: 'full_team', objective_classification: { kind: 'software' } }),
};

const run = {
  id: 'run:cockpit:1',
  agent_id: 'role:lead',
  issue_id: issue.id,
  status: 'completed',
  created_at: '2026-07-24 10:00:00',
  started_at: '2026-07-24 10:00:01',
  finished_at: '2026-07-24 10:00:03',
};

test('cockpit modular conserva Chat, Detalle y Runs navegables', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) {
      browserErrors.push(message.text());
    }
  });

  await page.route('http://127.0.0.1:8010/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown;

    if (path === '/api/health') body = { status: 'ok', mode: 'test' };
    else if (path === '/api/settings') body = { configured: true, projects_root_effective: 'C:/projects' };
    else if (path === '/api/workspace') body = {
      configured: true,
      workspace: 'C:/projects/demo',
      project_name: 'Demo',
      projects_root: 'C:/projects',
    };
    else if (path === '/api/project/state') body = {
      success: true,
      selected_issue_id: issue.id,
      issues: [issue],
      agents: [],
      runs: [run],
      timeline: [],
      comments: [{ id: 'comment:1', issue_id: issue.id, body: 'Contexto durable', author_user_id: 'owner' }],
      interactions: [],
      plan_document: null,
    };
    else if (path === '/api/chat') body = {
      messages: [{
        id: 'chat:1',
        source_id: 'comment:chat:1',
        item_type: 'message',
        sender: 'agent',
        author: 'role:lead',
        body: '**Cockpit listo** para revisión.',
        title: null,
        summary: null,
        kind: null,
        interaction_status: null,
        payload: {},
        issue_id: issue.id,
        source_run_id: run.id,
        created_at: '2026-07-24 10:00:02',
      }],
    };
    else if (path === `/api/runs/${encodeURIComponent(run.id)}`) body = { run };
    else if (path === `/api/runs/${encodeURIComponent(run.id)}/events`) body = {
      events: [{
        id: 'event:1',
        run_id: run.id,
        event_type: 'completed',
        seq: 1,
        payload: { text: 'Run terminada' },
        created_at: '2026-07-24 10:00:03',
      }],
    };
    else if (path === `/api/issues/${encodeURIComponent(issue.id)}/thread`) body = {
      view: 'compact',
      issue_id: issue.id,
      total_comments: 1,
      summary_blocks: [],
      synthesized_through: null,
      recent_comments: [{
        id: 'comment:1',
        issue_id: issue.id,
        body: 'Contexto durable',
        author_user_id: 'owner',
      }],
      has_synthesized_history: false,
    };
    else if (path === '/api/workspace/files') body = { files: [] };
    else if (path === '/api/projects') body = { projects: [] };
    else if (path === '/api/budget') body = { budgets: [] };
    else if (path === '/api/costs/summary') body = {
      totals: { actual_cost_cents: 0, estimated_savings_cents: 0, runs: 1 },
      by_role: [],
    };
    else if (path === '/api/loop-health') body = { detected_loops: [], at_risk: [], capacity_profiles: [] };
    else if (path === '/api/tools/catalog') body = { catalog: {} };
    else if (path === '/api/user-adapters') body = { profiles: [], cli_status: [], secrets: [] };
    else if (path === '/api/project/skills') body = { skills: [], governance: null };
    else if (path === '/api/project/extensions/mcp') body = { mcp_servers: [] };
    else if (path === '/api/project/extensions/mcp/catalog') body = { entries: [] };
    else if (path === '/api/model-catalog/selection') body = {
      success: true,
      selection_version: 'model_contextual_selection_v1',
      schema_version: 'model_catalog_read_model_v1',
      score_version: 'model_role_score_v1',
      content_hash: 'cockpit-fixture',
      rollout: 'shadow_only',
      canonical_role: 'lead',
      context: {},
      default: { candidate_id: null, action: 'require_owner_selection' },
      counts: { candidates: 0, auto_eligible: 0, owner_selectable: 0 },
      candidates: [],
    };
    else if (path === '/api/orientation-measurement') body = {
      consent: { enabled: false, current_session_id: null },
      sessions: { active: 0, completed: 0, abandoned: 0, revoked: 0 },
      event_count: 0,
      flows: {},
      privacy: {},
      interpretation: {},
    };
    else body = { success: true, comments: [] };

    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.goto('/');
  await expect(page.getByText('Cockpit listo')).toBeVisible();

  await page.getByRole('button', { name: 'Detalle' }).click();
  await expect(page.getByRole('heading', { name: issue.title })).toBeVisible();
  await expect(page.getByText('Contexto durable')).toBeVisible();

  await page.getByRole('button', { name: 'Runs' }).click();
  await page.locator('.run-row').click();
  await expect(page.getByText('Run terminada')).toBeVisible();
  const accessibility = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(accessibility.violations, JSON.stringify(accessibility.violations, null, 2)).toEqual([]);
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(horizontalOverflow).toBeLessThanOrEqual(1);
  expect(browserErrors).toEqual([]);
});
