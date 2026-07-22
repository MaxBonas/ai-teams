import { expect, test, type Page, type Route } from '@playwright/test';

const observedAt = '2026-07-22T18:00:00+00:00';

function state(value: boolean | null, reason: string, source = 'fixture') {
  return { value, reason, source, version: 'fixture-v1', observed_at: observedAt };
}

function candidate(
  id: string,
  model: string,
  label: string,
  profile: string,
  provider: string,
  channel: string,
  options: { blocked?: boolean; green?: boolean; score?: number | null; confidence?: number; role?: string; eligible?: boolean },
) {
  const role = options.role || 'reviewer';
  const blocked = Boolean(options.blocked);
  return {
    candidate_id: id,
    label,
    identity: {
      profile_id: profile,
      provider_org: provider,
      model_vendor: provider,
      perspective_key: provider,
      channel,
      capacity_pool: profile,
      model_id: model,
    },
    states: {
      catalogued: state(true, 'declared_catalog'),
      configured: state(true, 'profile_configuration_observed'),
      adapter_green: state(Boolean(options.green), options.green ? 'adapter_health_ok' : 'adapter_health_missing', 'adapter_health'),
      model_verified: state(!blocked, blocked ? 'probe_failed' : 'run_completed', 'model_health'),
      selectable: state(!blocked, blocked ? 'model_unavailable' : 'run_completed'),
      compatible: state(null, 'requires_role_context', 'model_compatibility'),
      calibrated: state(null, 'requires_exact_role_evidence', 'model_evaluation_coverage'),
      stale: state(null, 'requires_evidence_date', 'model_calibration'),
      manual_only: state(false, 'automatic_policy_not_disabled'),
      blocked: state(blocked, blocked ? 'model_unavailable' : 'not_blocked'),
      retired: state(false, 'no_retirement_evidence'),
    },
    provider_metadata: {
      label: profile,
      adapter_type: `${provider}_fixture`,
      data_policy: blocked ? 'non_confidential_only' : 'owner_account',
      privacy_note: blocked ? 'Solo datos públicos' : 'Cuenta administrada por el owner',
      workspace_mode: blocked ? 'read' : 'write',
      mcp_transport: 'none',
      structured_output: 'json_schema',
    },
    model_metadata: {
      tier: blocked ? 'budget' : 'standard',
      capability_band: blocked ? 'budget' : 'standard',
      capabilities: ['reasoning', 'coding'],
      economy: {
        cost_class: channel === 'subscription' ? 'flat_subscription_quota_limited' : 'api_token_priced',
        measurement_basis: channel === 'subscription' ? 'tokens_runs_duration' : 'api_price_per_token',
        quota_unlimited: false,
      },
      speed_class: blocked ? 'fast' : 'balanced',
      speed_source: 'fixture_measurement',
      price_note: blocked ? 'Barato pero no ejecutable' : 'Suscripción con cuota',
    },
    roles: [roleEvaluation(id, role, options)],
  };
}

function roleEvaluation(
  id: string,
  role: string,
  options: { blocked?: boolean; score?: number | null; confidence?: number; eligible?: boolean },
) {
  const blocked = Boolean(options.blocked);
  const score = options.score ?? null;
  const confidence = options.confidence ?? 0;
  return {
    canonical_role: role,
    compatibility: {
      allowed: !blocked,
      code: blocked ? 'model_unavailable' : 'compatible',
      reason: blocked ? 'El modelo exacto falló su probe y no puede ejecutarse.' : 'Cumple el contrato del rol.',
    },
    evaluation: {
      status: options.eligible ? 'calibrated' : blocked ? 'blocked' : 'partial',
      evaluated_at: observedAt,
      provider_version: 'fixture-2.0',
      evidence_receipts: options.eligible ? ['benchmarks/reviewer-aggregate.json'] : [],
      diagnostic_receipts: blocked ? ['benchmarks/probe-failed.json'] : [],
      stale_reasons: [],
    },
    runtime_metrics: { run_count: 3, completed_count: blocked ? 0 : 3, median_duration_ms: blocked ? null : 42000 },
    provenance: {
      evaluation_receipts: options.eligible ? ['benchmarks/reviewer-aggregate.json'] : [],
      diagnostic_receipts: blocked ? ['benchmarks/probe-failed.json'] : [],
      runtime_database_ids: ['fixture-db'],
      runtime_run_ids: ['run-1', 'run-2', 'run-3'],
      metric_sources: ['fixture_normalizer'],
    },
    score: {
      score,
      score_range: { minimum: score ?? 20, maximum: score ?? 100 },
      known_weight_percent: score === null ? 40 : 100,
      confidence: {
        value: confidence,
        minimum_for_auto: 75,
        evidence_status: options.eligible ? 'calibrated' : 'blocked',
        seeds: 3,
        cases: 1,
        goodhart_risk: 'moderate',
        fresh: true,
        evaluated_at: observedAt,
        provider_version: 'fixture-2.0',
        unmeasured_constructs: ['novel_projects'],
      },
      breakdown: {
        quality: { value: score, reason: 'behavioral_fixture', source: 'hidden_tests', status: score === null ? 'unknown' : 'known', weight_percent: 40 },
        capability: { value: blocked ? 20 : 85, reason: 'capability_fixture', source: 'compatibility', status: 'known', weight_percent: 15 },
        reliability: { value: blocked ? 0 : 90, reason: 'three_runs', source: 'sqlite_runs', status: 'known', weight_percent: 15 },
        economy: { value: 70, reason: 'channel_normalized', source: 'cost_events', status: 'known', weight_percent: 20 },
        speed: { value: 75, reason: 'latency_normalized', source: 'sqlite_runs', status: 'known', weight_percent: 10 },
      },
      hard_gates: {
        adapter_green: { passed: !blocked, reason: blocked ? 'health_missing' : 'passed', source: 'fixture' },
        model_verified: { passed: !blocked, reason: blocked ? 'probe_failed' : 'passed', source: 'fixture' },
      },
      auto_eligible: Boolean(options.eligible),
      auto_ineligible_reasons: options.eligible ? [] : ['gate:model_verified:probe_failed'],
      rollout: 'shadow_only',
      candidate_id: id,
    },
    input_hash: `hash-${id}`,
  };
}

const good = candidate('candidate-good', 'model-good', 'Model Good', 'profile-green', 'provider-a', 'subscription', {
  green: true,
  score: 84,
  confidence: 88,
  eligible: true,
});
const blocked = candidate('candidate-blocked', 'model-blocked', 'Model Blocked', 'profile-blocked', 'provider-b', 'api', {
  blocked: true,
  score: 95,
  confidence: 92,
});
const engineer = candidate('candidate-engineer', 'model-engineer', 'Model Engineer', 'profile-green', 'provider-a', 'subscription', {
  green: true,
  role: 'engineer',
  score: null,
  confidence: 35,
});

const catalog = {
  success: true,
  schema_version: 'model_catalog_read_model_v1',
  score_version: 'model_role_score_v1',
  content_hash: '0123456789abcdef',
  observed_at: observedAt,
  rollout: 'shadow_only',
  counts: { candidates: 3, providers: 2 },
  providers: [
    {
      profile_id: 'profile-green', provider: 'provider-a', channel: 'subscription', capacity_pool: 'profile-green',
      model_count: 2, configured_count: 2, green_count: 2, selectable_count: 2, blocked_count: 0,
      data_policy: 'owner_account', privacy_note: 'Cuenta administrada', economy_classes: ['flat_subscription_quota_limited'],
    },
    {
      profile_id: 'profile-blocked', provider: 'provider-b', channel: 'api', capacity_pool: 'profile-blocked',
      model_count: 1, configured_count: 1, green_count: 0, selectable_count: 0, blocked_count: 1,
      data_policy: 'non_confidential_only', privacy_note: 'Solo datos públicos', economy_classes: ['api_token_priced'],
    },
  ],
  runtime: { database_sources: [], diagnostics: [] },
  candidates: [blocked, engineer, good],
};

async function installApiFixture(page: Page, options: { failCatalogOnce?: boolean } = {}) {
  let catalogCalls = 0;
  await page.route('http://127.0.0.1:8010/api/**', async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let status = 200;
    let body: unknown = { success: true };
    if (path === '/api/health') body = { status: 'ok', mode: 'test' };
    else if (path === '/api/settings') body = { configured: true, projects_root_effective: 'C:/projects' };
    else if (path === '/api/workspace') body = { configured: true, workspace: 'C:/projects/demo', project_name: 'Demo', projects_root: 'C:/projects' };
    else if (path === '/api/project/state') body = {
      success: true,
      selected_issue_id: 'issue:intake',
      issues: [{ id: 'issue:intake', title: 'Demo', status: 'todo', role: 'lead', metadata_json: '{}' }],
      agents: [], runs: [], timeline: [], comments: [], interactions: [], plan_document: null,
    };
    else if (path === '/api/chat') body = { messages: [] };
    else if (path === '/api/workspace/files') body = { files: [] };
    else if (path === '/api/projects') body = { projects: [] };
    else if (path === '/api/budget') body = { budgets: [] };
    else if (path === '/api/costs/summary') body = { totals: { actual_cost_cents: 0, estimated_savings_cents: 0, runs: 0 }, by_role: [] };
    else if (path === '/api/loop-health') body = { detected_loops: [], at_risk: [], capacity_profiles: [], summary: { total_loops: 0, total_at_risk: 0, requires_attention: false } };
    else if (path === '/api/tools/catalog') body = { catalog: {} };
    else if (path === '/api/user-adapters') body = { profiles: [], cli_status: [], secrets: [] };
    else if (path === '/api/project/skills') body = { skills: [], governance: null };
    else if (path === '/api/project/extensions/mcp') body = { mcp_servers: [] };
    else if (path === '/api/project/extensions/mcp/catalog') body = { entries: [] };
    else if (path === '/api/orientation-measurement') body = {
      success: true,
      consent: { enabled: false, current_session_id: null, consented_at: null, revoked_at: null },
      sessions: { active: 0, completed: 0, abandoned: 0, revoked: 0 }, event_count: 0, flows: {},
      privacy: { storage: 'local', external_transmission: false, free_text_collected: false, issue_or_workspace_ids_collected: false },
      interpretation: { constructs_not_measured: [], conclusion_allowed: false, reason: 'fixture' },
    };
    else if (path === '/api/model-catalog') {
      catalogCalls += 1;
      // React StrictMode ejecuta el efecto inicial dos veces en desarrollo.
      // Ambas peticiones deben fallar para ejercitar el botón de reintento.
      if (options.failCatalogOnce && catalogCalls <= 2) {
        status = 503;
        body = { detail: 'fixture_unavailable' };
      } else {
        await new Promise((resolve) => setTimeout(resolve, 180));
        body = catalog;
      }
    } else if (path === '/api/model-catalog/candidates') {
      const stateFilter = url.searchParams.get('state');
      const ranked = stateFilter === 'blocked' ? [blocked] : [good, blocked];
      body = {
        success: true,
        schema_version: catalog.schema_version,
        score_version: catalog.score_version,
        content_hash: catalog.content_hash,
        observed_at: observedAt,
        rollout: 'shadow_only',
        canonical_role: 'reviewer',
        compatibility_context: {},
        counts: { candidates: ranked.length, auto_eligible: ranked.filter((item) => item.roles[0].score.auto_eligible).length },
        candidates: ranked.map((item, index) => ({ ...item, rank: index + 1, selection_reason: item === blocked ? 'compatibility:model_unavailable' : 'auto_eligible_shadow_only', role_evaluation: item.roles[0] })),
      };
    }
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('Modelos: filtros, orden backend, adapter verde y detalle bloqueado son visibles', async ({ page }, testInfo) => {
  const browserErrors: string[] = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  await installApiFixture(page);
  await page.goto('/');
  await expect(page.getByTestId('project-cockpit')).toBeVisible();

  await page.getByTestId('models-tab').click();
  await expect(page.getByTestId('models-loading')).toBeVisible();
  await expect(page.getByTestId('model-catalog-view')).toBeVisible();
  await expect(page.getByTestId('provider-profile-green')).toHaveClass(/is-green/);
  await expect(page.getByTestId('provider-profile-blocked')).toContainText('1 bloqueados');
  await expect(page.getByTestId('model-matrix')).toContainText('Model Blocked');

  await page.getByTestId('model-role-filter').selectOption('reviewer');
  await expect(page.getByTestId('model-row-model-good')).toBeVisible();
  const rankedRows = page.locator('.model-matrix tbody tr');
  await expect(rankedRows.nth(0)).toHaveAttribute('data-testid', 'model-row-model-good');
  await expect(rankedRows.nth(1)).toHaveAttribute('data-testid', 'model-row-model-blocked');

  await page.getByTestId('model-state-filter').selectOption('blocked');
  await expect(page.getByTestId('model-row-model-blocked')).toBeVisible();
  await expect(page.getByTestId('model-row-model-good')).toHaveCount(0);
  await page.getByTestId('model-cell-model-blocked-reviewer').click();
  const detail = page.getByTestId('model-detail');
  await expect(detail).toContainText('No elegible automáticamente');
  await expect(detail).toContainText('El modelo exacto falló su probe');
  await expect(detail).toContainText('Calidad');
  await expect(detail).toContainText('model_health');
  await expect(detail).toContainText('Gate · Model verified');
  await expect(detail).toContainText('benchmarks/probe-failed.json');
  await page.screenshot({ path: testInfo.outputPath('model-catalog-detail.png'), fullPage: true });

  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /Limpiar/ }).click();
  await page.getByTestId('model-search').fill('does-not-exist');
  await expect(page.getByTestId('models-empty')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Restablecer filtros' }).click();
  await expect(page.getByTestId('model-matrix')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('model-catalog-mobile.png'), fullPage: true });
  expect(browserErrors).toEqual([]);
});

test('Modelos: error inicial conserva seguridad y permite reintentar', async ({ page }) => {
  await installApiFixture(page, { failCatalogOnce: true });
  await page.goto('/');
  await page.getByTestId('models-tab').click();
  await expect(page.getByTestId('models-error')).toContainText('routing no han sido modificados');
  await page.getByRole('button', { name: 'Reintentar' }).click();
  await expect(page.getByTestId('model-catalog-view')).toBeVisible();
});
