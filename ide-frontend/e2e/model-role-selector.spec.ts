import { expect, test, type Page, type Route } from '@playwright/test';

function selectionCandidate(
  id: string,
  profileId: string,
  model: string,
  rank: number,
  options: { selectable?: boolean; score?: number; reason?: string } = {},
) {
  const selectable = options.selectable !== false;
  return {
    candidate_id: id,
    label: model,
    rank,
    identity: {
      profile_id: profileId,
      provider_org: profileId.split('_')[0],
      channel: 'subscription',
      model_id: model,
    },
    provider_metadata: { label: profileId },
    selection_score: {
      score: options.score ?? 80,
      confidence: { value: 90 },
      auto_eligible: selectable,
      auto_ineligible_reasons: selectable ? [] : ['gate:capacity_available:capacity:exhausted_observed'],
      breakdown: {
        quality: { value: options.score ?? 80, status: 'known' },
        economy: { value: 60, status: 'known' },
      },
    },
    contextual_compatibility: { allowed: true, code: 'compatible', reason: 'Compatible' },
    owner_selectable: selectable,
    requires_configuration: false,
    disabled_reason: options.reason || null,
    capacity_evidence: { state: selectable ? 'metered' : 'exhausted_observed' },
  };
}

async function installFixture(page: Page, noDefault = false) {
  let patchedBody: Record<string, unknown> | null = null;
  let storedAdapterConfig: Record<string, unknown> = {
    profile_id: 'profile_a',
    model: 'model-first',
    selection_intent: {
      schema_version: 'model_selection_intent_v1',
      mode: 'owner_explicit',
      source: 'fixture',
      candidate_id: 'candidate:first',
    },
  };
  const first = selectionCandidate('candidate:first', 'profile_a', 'model-first', 1, { score: 90 });
  const second = selectionCandidate('candidate:second', 'profile_b', 'model-second', 2, { score: 84 });
  const blocked = selectionCandidate('candidate:blocked', 'profile_c', 'model-blocked', 3, {
    selectable: false,
    score: 99,
    reason: 'La cuota de este adapter está agotada según el último error observado.',
  });
  await page.route('http://127.0.0.1:8010/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = { success: true };
    if (path === '/api/health') body = { status: 'ok', mode: 'test' };
    else if (path === '/api/settings') body = { configured: true, projects_root_effective: 'C:/projects' };
    else if (path === '/api/workspace') body = { configured: true, workspace: 'C:/projects/demo', project_name: 'Demo', projects_root: 'C:/projects' };
    else if (path === '/api/project/state') body = {
      success: true,
      selected_issue_id: 'issue:review',
      issues: [{ id: 'issue:review', title: 'Review', status: 'todo', role: 'reviewer', criticality: 'high', metadata_json: '{"data_class":"public","profile":"full_team"}' }],
      agents: [{
        id: 'role:reviewer', role: 'reviewer', name: 'Reviewer', seniority: 'senior', status: 'idle',
        adapter_type: 'subscription_cli', adapter_config: storedAdapterConfig,
        capabilities: ['repo_read'], budget_monthly_cents: 0,
      }],
      runs: [], timeline: [], comments: [], interactions: [], plan_document: null,
    };
    else if (path === '/api/chat') body = { messages: [] };
    else if (path === '/api/workspace/files') body = { files: [] };
    else if (path === '/api/projects') body = { projects: [] };
    else if (path === '/api/budget') body = { budgets: [] };
    else if (path === '/api/costs/summary') body = { totals: { actual_cost_cents: 0, estimated_savings_cents: 0, runs: 0 }, by_role: [] };
    else if (path === '/api/loop-health') body = { detected_loops: [], at_risk: [], capacity_profiles: [], summary: { requires_attention: false } };
    else if (path === '/api/tools/catalog') body = { catalog: { repo_read: { label: 'Repo read', description: '', tool_family: 'repo' } } };
    else if (path === '/api/user-adapters') body = {
      profiles: ['profile_a', 'profile_b', 'profile_c'].map((id) => ({
        id, label: id, adapter_type: 'subscription_cli', channel: 'subscription', provider: id,
        status: 'active', health: { status: 'ok' }, model_options: [],
      })),
      cli_status: [], secrets: [],
    };
    else if (path === '/api/user-adapters/models') body = { options: [] };
    else if (path === '/api/project/skills') body = { skills: [], governance: null };
    else if (path === '/api/project/extensions/mcp') body = { mcp_servers: [] };
    else if (path === '/api/project/extensions/mcp/catalog') body = { entries: [] };
    else if (path === '/api/orientation-measurement') body = {
      success: true, consent: { enabled: false }, sessions: {}, event_count: 0, flows: {},
      privacy: {}, interpretation: { conclusion_allowed: false },
    };
    else if (path === '/api/model-catalog/selection') body = {
      success: true,
      default: noDefault
        ? { candidate_id: null, action: 'preserve_explicit_or_require_owner' }
        : { candidate_id: first.candidate_id, action: 'recommend_shadow_only', score: 90, confidence: 90, advantage: { kind: 'score_delta', value: 6 } },
      candidates: noDefault
        ? [
            { ...first, selection_score: { ...first.selection_score, auto_eligible: false, auto_ineligible_reasons: ['gate:calibrated:no'] } },
            { ...second, selection_score: { ...second.selection_score, auto_eligible: false, auto_ineligible_reasons: ['gate:calibrated:no'] } },
            blocked,
          ]
        : [first, second, blocked],
    };
    else if (path === '/api/agents/role%3Areviewer' && route.request().method() === 'PATCH') {
      patchedBody = route.request().postDataJSON() as Record<string, unknown>;
      if (patchedBody.adapter_config) {
        storedAdapterConfig = patchedBody.adapter_config as Record<string, unknown>;
      }
      body = { success: true, agent: {} };
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return () => patchedBody;
}

test('selector global respeta orden backend, bloquea cuota y persiste elección owner', async ({ page }) => {
  const patchedBody = await installFixture(page);
  await page.goto('/');
  await page.getByRole('button', { name: /^Equipo 1$/ }).click();
  await page.getByTitle('Configurar agente').click();
  const selector = page.getByTestId('model-role-selector');
  await expect(selector).toBeVisible();
  const options = selector.locator('option');
  await expect(options.nth(1)).toContainText('#1 model-first');
  await expect(options.nth(2)).toContainText('#2 model-second');
  await expect(options.nth(3)).toContainText('#3 model-blocked');
  await expect(options.nth(3)).toBeDisabled();
  await expect(options.nth(3)).toContainText('cuota');
  await selector.selectOption('profile_b\u0000model-second');
  await page.getByRole('button', { name: 'Guardar cambios' }).click();
  await expect.poll(() => patchedBody()).not.toBeNull();
  expect(patchedBody()).toMatchObject({
    adapter_config: {
      profile_id: 'profile_b',
      model: 'model-second',
      selection_intent: {
        schema_version: 'model_selection_intent_v1',
        mode: 'owner_explicit',
        candidate_id: 'candidate:second',
      },
    },
  });
});

test('selector sin auto-elegibles conserva la elección y exige owner', async ({ page }) => {
  await installFixture(page, true);
  await page.goto('/');
  await page.getByRole('button', { name: /^Equipo 1$/ }).click();
  await page.getByTitle('Configurar agente').click();
  await expect(page.getByTestId('model-role-no-default')).toContainText('se conserva la selección explícita');
  await expect(page.getByTestId('model-role-selector').locator('option').first()).toContainText('owner debe elegir');
});

test('selección owner sobrevive guardado, recarga de estado y recarga de UI', async ({ page }) => {
  const patchedBody = await installFixture(page);
  await page.goto('/');
  await page.getByRole('button', { name: /^Equipo 1$/ }).click();
  await page.getByTitle('Configurar agente').click();
  await page.getByTestId('model-role-selector').selectOption('profile_b\u0000model-second');
  await page.getByRole('button', { name: 'Guardar cambios' }).click();
  await expect.poll(() => patchedBody()).not.toBeNull();

  await page.reload();
  await page.getByRole('button', { name: /^Equipo 1$/ }).click();
  await page.getByTitle('Configurar agente').click();
  await expect(page.getByTestId('model-role-selector')).toHaveValue(
    'profile_b\u0000model-second',
  );
  expect(patchedBody()).toMatchObject({
    adapter_config: {
      selection_intent: {
        mode: 'owner_explicit',
        candidate_id: 'candidate:second',
      },
    },
  });
});
