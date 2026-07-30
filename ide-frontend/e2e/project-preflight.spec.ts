import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';

const proposalHash = 'a'.repeat(64);
const preflightHash = 'b'.repeat(64);
const planHash = 'c'.repeat(64);
const executionReceiptHash = 'd'.repeat(64);
const noGoPreflightHash = 'e'.repeat(64);
const goPreflightHash = 'f'.repeat(64);
const noGoDurableReceiptHash = '1'.repeat(64);
const goDurableReceiptHash = '2'.repeat(64);

function sha256(value: string | Buffer) {
  return createHash('sha256').update(value).digest('hex');
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

async function expectNoAccessibilityViolations(page: Page, state: string) {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(
    result.violations,
    `${state}\n${JSON.stringify(result.violations, null, 2)}`,
  ).toEqual([]);
}

const candidate = {
  candidate_id: 'codex:lead',
  profile_id: 'codex_subscription',
  model_id: 'gpt-lead',
  provider: 'OpenAI',
  channel: 'subscription',
  tier: 'premium',
  score: 94,
  coverage_eligible: true,
  owner_selectable: true,
  economics: { class: 'zero_marginal', marginal_cost: 'zero' },
  privacy: { allowed: true },
  capabilities: ['reasoning'],
  gates: { calibrated: true },
};

test('projects the server preflight without enabling project entry', async ({ page }, testInfo) => {
  let revision = 1;
  let executionCount = 0;
  const browserErrors: string[] = [];
  const screenshots: Array<{
    file: string;
    state: string;
    viewport: { width: number; height: number } | null;
    sha256: string;
    authority_hashes: Record<string, string | null>;
  }> = [];
  const captureEvidence = async (
    file: string,
    state: string,
    authorityHashes: Record<string, string | null>,
  ) => {
    await page.evaluate(async () => {
      window.scrollTo(0, 0);
      await document.fonts.ready;
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()));
      });
    });
    const path = testInfo.outputPath(file);
    await page.screenshot({ path, fullPage: true });
    screenshots.push({
      file,
      state,
      viewport: page.viewportSize(),
      sha256: sha256(await readFile(path)),
      authority_hashes: authorityHashes,
    });
  };
  page.on('pageerror', (error) => browserErrors.push(error.message));

  await page.route('http://127.0.0.1:8010/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/health') body = { status: 'ok', mode: 'test' };
    else if (path === '/api/settings') {
      body = { configured: true, projects_root_effective: 'C:/projects' };
    } else if (path === '/api/workspace') {
      body = { configured: false, projects_root: 'C:/projects' };
    } else if (path === '/api/user-adapters') {
      body = {
        profiles: [{
          id: 'codex_subscription',
          label: 'Codex',
          provider: 'OpenAI',
          adapter_type: 'subscription_cli',
          channel: 'subscription',
          status: 'active',
          health: { status: 'ok' },
        }],
        cli_status: [],
        secrets: [],
      };
    } else if (path === '/api/model-catalog/selection') {
      body = {
        success: true,
        selection_version: 'model_contextual_selection_v1',
        schema_version: 'model_catalog_read_model_v1',
        score_version: 'model_role_score_v1',
        content_hash: 'preflight-e2e',
        rollout: 'shadow_only',
        canonical_role: 'team_lead',
        context: {},
        default: { candidate_id: null, action: 'require_owner_selection' },
        counts: { candidates: 0, auto_eligible: 0, owner_selectable: 0 },
        candidates: [],
      };
    } else if (path.endsWith('/needs-assessment')) {
      body = { submission: { schema_version: 'guided_setup_needs_v1' } };
    } else if (path.endsWith('/sessions')) {
      body = { session: { id: 'session-e2e', revision } };
    } else if (path.includes('/steps/')) {
      revision += 1;
      body = { session: { id: 'session-e2e', revision } };
    } else if (path.endsWith('/project-proposal')) {
      body = {
        proposal: {
          proposal_hash: proposalHash,
          project: {
            mode: 'create',
            name: 'Portal visual',
            target: 'C:/projects/Portal-visual',
            instructions_preview: '',
            objective: 'Crear un portal accesible',
          },
          ecosystems: { detected_ids: ['javascript_typescript'], scan_truncated: false },
          profile: {
            recommended: 'full_team',
            selected: 'full_team',
            owner_override: false,
            automatic_coverage_ready: true,
            coverage_status: 'covered',
            coverage_blockers: [],
          },
          team: {
            assignments: [{
              agent_id: 'role:team_lead',
              role: 'team_lead',
              name: 'Team Lead',
              supervisor_agent_id: null,
              assignment_reason: 'Dirige el proyecto.',
              selection_mode: 'automatic',
              candidate,
            }],
            quorum_diversity: { ready: true, perspective_count: 0, capacity_pool_count: 0 },
            manual_override_count: 0,
          },
          budget: { mode: 'balanced' },
          degradations: [],
          save_gate: { allowed: true, blockers: [], requires_owner_confirmation: false },
        },
        coverage: {
          roles: { team_lead: { candidates: [candidate], excluded_candidates: [] } },
        },
      };
    } else if (path.endsWith('/project-preflight')) {
      body = {
        preflight: {
          preflight_hash: preflightHash,
          objective: {
            kind: 'software',
            software_surface_detected: true,
            detected_ecosystems: ['javascript_typescript'],
          },
          fixture_policy: {
            kind: 'software_toolchain_smoke',
            software_fixture_required: true,
            remote_probe_requires_consent: true,
            possible_quota_must_be_confirmed: true,
            automatic_install: false,
            max_attempts: 1,
          },
          gates: [{
            id: 'proportional_fixture',
            required: true,
            status: 'not_checked',
            code: 'software_fixture_required',
            message: 'Falta ejecutar el fixture proporcional.',
            next_action: 'run_proportional_fixture',
          }],
          summary: {
            status: 'no_go',
            go: false,
            commit_allowed: false,
            enter_project_allowed: false,
            blockers: [],
            warnings: [],
            optional_pending: [],
            next_action: 'run_proportional_fixture',
          },
        },
        execution_plan: {
          plan_hash: planHash,
          actions: [{
            id: 'local_fixture',
            kind: 'software_toolchain_smoke',
            timeout_seconds: 300,
            remote: false,
            quota_possible: false,
            consent_requirements: ['confirm_local_fixture'],
          }],
          planning_blockers: [],
          summary: {
            status: 'ready',
            action_count: 1,
            remote_action_count: 0,
            requires_consent: true,
            next_action: 'confirm_preflight_execution',
          },
        },
      };
    } else if (path.endsWith('/project-preflight-execute')) {
      executionCount += 1;
      const status = executionCount === 1 ? 'no_go' : 'go';
      const postPreflightHash = status === 'go' ? goPreflightHash : noGoPreflightHash;
      body = {
        receipt: {
          receipt_hash: executionReceiptHash,
          summary: {
            status: status === 'go' ? 'passed' : 'failed',
            planned_count: 1,
            executed_count: 1,
            passed_count: status === 'go' ? 1 : 0,
          },
        },
        post_execution_preflight: {
          preflight_hash: postPreflightHash,
          objective: {
            kind: 'software',
            software_surface_detected: true,
            detected_ecosystems: ['javascript_typescript'],
          },
          fixture_policy: {
            kind: 'software_toolchain_smoke',
            software_fixture_required: true,
            remote_probe_requires_consent: true,
            possible_quota_must_be_confirmed: true,
            automatic_install: false,
            max_attempts: 1,
          },
          gates: [{
            id: 'proportional_fixture',
            required: true,
            status: status === 'go' ? 'passed' : 'blocked',
            code: status === 'go' ? 'fixture_passed' : 'fixture_failed',
            message: status === 'go'
              ? 'Fixture proporcional superado.'
              : 'El fixture proporcional falló.',
            next_action: status === 'go' ? 'persist_receipt' : 'review_resources',
          }],
          summary: {
            status,
            go: status === 'go',
            commit_allowed: status === 'go',
            enter_project_allowed: false,
            blockers: status === 'go' ? [] : [{
              gate: 'proportional_fixture',
              code: 'fixture_failed',
              message: 'El fixture proporcional falló.',
              next_action: 'review_resources',
            }],
            warnings: [],
            optional_pending: [],
            next_action: status === 'go' ? 'commit_project' : 'review_resources',
          },
        },
        persistence: {
          persisted: true,
          idempotent_replay: false,
          required_before_commit: false,
          durable_receipt: {
            id: `receipt-${status}`,
            receipt_hash: status === 'go'
              ? goDurableReceiptHash
              : noGoDurableReceiptHash,
            preflight_hash: postPreflightHash,
            execution_plan_hash: planHash,
            execution_receipt_hash: executionReceiptHash,
            status,
            fixture_evidence_refs: [`sha256:${'3'.repeat(64)}`],
          },
        },
      };
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Configura el proyecto/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Crear nuevo' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('main')).toHaveCount(1);
  await expect(page.getByRole('navigation', {
    name: 'Progreso de configuración',
  })).toBeVisible();
  await expectNoAccessibilityViolations(page, 'Paso Proyecto');
  await page.getByLabel('Nombre visible').fill('');
  await page.getByRole('button', { name: /Continuar/ }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel(/Nombre visible/)).toBeFocused();
  await expect(page.getByLabel(/Nombre visible/)).toHaveAttribute('aria-invalid', 'true');
  await expectNoAccessibilityViolations(page, 'Paso Proyecto con error');
  await page.getByLabel('Nombre visible').fill('Portal visual');
  await page.getByRole('button', { name: /Continuar/ }).click();
  await expect(page.getByRole('region', {
    name: 'Define el resultado esperado',
  })).toBeFocused();
  await expect(page.getByRole('button', {
    name: 'Paso 2 de 4: Objetivo. Actual',
  })).toHaveAttribute('aria-current', 'step');
  await expectNoAccessibilityViolations(page, 'Paso Objetivo');
  await page.getByRole('button', { name: /Continuar/ }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel(/Resultado que debe conseguir el Lead/)).toBeFocused();
  await expect(page.getByLabel(/Resultado que debe conseguir el Lead/))
    .toHaveAttribute('aria-invalid', 'true');
  await expectNoAccessibilityViolations(page, 'Paso Objetivo con error');
  await page.getByLabel(/Resultado que debe conseguir el Lead/).fill('Crear un portal accesible');
  await page.getByRole('button', { name: /Continuar/ }).click();
  await expect(page.getByRole('region', {
    name: 'Elige canales; el backend forma el equipo',
  })).toBeFocused();
  await expectNoAccessibilityViolations(page, 'Paso Recursos y equipo');
  await page.getByRole('button', { name: /Generar propuesta/ }).click();

  await expect(page.getByRole('heading', { name: 'Preflight antes de despertar al equipo' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Portal visual' })).toBeFocused();
  await expect(page.getByRole('button', {
    name: 'Paso 4 de 4: Revisión. Actual',
  })).toHaveAttribute('aria-current', 'step');
  await expect(page.getByText('Autorización durable')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Estado del protocolo' }))
    .toBeVisible();
  await expect(page.getByRole('listitem')).toHaveCount(3);
  await expect(page.getByRole('checkbox', {
    name: /Autorizo el fixture local/,
  })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ejecutar y sellar preflight' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Entrar al proyecto' })).toHaveCount(0);
  await expect(page.locator('.legacy-project-setup')).toHaveCount(0);
  const headingLevels = await page.locator('.project-setup h1, .project-setup h2, .project-setup h3')
    .evaluateAll((headings) => headings.map((heading) => ({
      level: Number(heading.tagName.slice(1)),
      text: heading.textContent?.trim() ?? '',
    })));
  expect(headingLevels[0]).toMatchObject({
    level: 1,
    text: 'Configura el proyecto antes de despertar al equipo',
  });
  expect(
    headingLevels.every((heading, index) => (
      index === 0 || heading.level - headingLevels[index - 1].level <= 1
    )),
    JSON.stringify(headingLevels, null, 2),
  ).toBe(true);
  const overflow = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('body *')]
    .filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1)
    .slice(0, 5)
    .map((element) => ({
      className: element.className,
      right: Math.round(element.getBoundingClientRect().right),
      width: Math.round(element.getBoundingClientRect().width),
    })));
  expect(overflow).toEqual([]);
  await expectNoAccessibilityViolations(page, 'Propuesta y preflight pendiente');
  expect(browserErrors).toEqual([]);
  await captureEvidence('preflight-desktop.png', 'pending-desktop', {
    proposal_hash: proposalHash,
    preflight_hash: preflightHash,
    execution_plan_hash: planHash,
    durable_receipt_hash: null,
  });

  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.getByRole('button', { name: 'Ejecutar y sellar preflight' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Atrás/ })).toBeVisible();
  await expect(page.getByText('Autoriza y sella el preflight para entrar.')).toBeVisible();
  const tabletOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(tabletOverflow, 'El wizard no debe desbordar horizontalmente en tablet').toBeLessThanOrEqual(1);
  await captureEvidence('preflight-tablet.png', 'pending-tablet', {
    proposal_hash: proposalHash,
    preflight_hash: preflightHash,
    execution_plan_hash: planHash,
    durable_receipt_hash: null,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', {
    name: 'Paso 4 de 4: Revisión. Actual',
  })).toBeVisible();
  await expect(page.getByText('Autoriza y sella el preflight para entrar.')).toBeVisible();
  await expect(page.getByText('Autorizo el fixture local')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ejecutar y sellar preflight' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Atrás/ })).toBeVisible();
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(mobileOverflow, 'El wizard no debe desbordar horizontalmente en móvil').toBeLessThanOrEqual(1);
  await captureEvidence('preflight-mobile.png', 'pending-mobile', {
    proposal_hash: proposalHash,
    preflight_hash: preflightHash,
    execution_plan_hash: planHash,
    durable_receipt_hash: null,
  });

  await page.setViewportSize({ width: 320, height: 800 });
  await expect(page.getByText('Autoriza y sella el preflight para entrar.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ejecutar y sellar preflight' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Atrás/ })).toBeVisible();
  const reflowOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(
    reflowOverflow,
    'El wizard debe mantener reflow sin scroll bidimensional a 320 CSS px',
  ).toBeLessThanOrEqual(1);
  await captureEvidence('preflight-reflow-320.png', 'pending-reflow-320', {
    proposal_hash: proposalHash,
    preflight_hash: preflightHash,
    execution_plan_hash: planHash,
    durable_receipt_hash: null,
  });

  await page.emulateMedia({ reducedMotion: 'reduce' });
  const motionDurations = await page.evaluate(() => {
    const toMilliseconds = (value: string) => value
      .split(',')
      .map((part) => part.trim())
      .map((part) => (part.endsWith('ms')
        ? Number.parseFloat(part)
        : Number.parseFloat(part) * 1000))
      .filter(Number.isFinite);
    return [...document.querySelectorAll<HTMLElement>('.project-setup, .project-setup *')]
      .reduce((maximum, element) => {
        const style = window.getComputedStyle(element);
        return Math.max(
          maximum,
          ...toMilliseconds(style.animationDuration),
          ...toMilliseconds(style.transitionDuration),
        );
      }, 0);
  });
  expect(
    motionDurations,
    'prefers-reduced-motion debe anular transiciones y animaciones del wizard',
  ).toBeLessThanOrEqual(0.011);
  const backButton = page.getByRole('button', { name: /Atrás/ });
  await backButton.focus();
  await page.keyboard.press('Shift+Tab');
  await page.keyboard.press('Tab');
  await expect(backButton).toBeFocused();
  const focusIndicator = await backButton.evaluate((element) => {
    const parseColor = (color: string) => (
      color.match(/\d+(?:\.\d+)?/g)?.slice(0, 3).map(Number) ?? [0, 0, 0]
    );
    const luminance = (color: number[]) => {
      const channels = color.map((channel) => {
        const normalized = channel / 255;
        return normalized <= 0.04045
          ? normalized / 12.92
          : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const style = window.getComputedStyle(element);
    const foreground = luminance(parseColor(style.outlineColor));
    const background = luminance(parseColor(style.backgroundColor));
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      contrastRatio: (Math.max(foreground, background) + 0.05)
        / (Math.min(foreground, background) + 0.05),
    };
  });
  expect(focusIndicator.outlineStyle).not.toBe('none');
  expect(focusIndicator.outlineWidth).toBeGreaterThanOrEqual(2);
  expect(
    focusIndicator.contrastRatio,
    'El indicador de foco debe contrastar al menos 3:1 con el control',
  ).toBeGreaterThanOrEqual(3);

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.getByRole('checkbox', { name: /Autorizo el fixture local/ }).check();
  await page.getByRole('button', { name: 'Ejecutar y sellar preflight' }).click();
  await expect(page.getByText('NO-GO · intento sellado')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Entrar al proyecto' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Revisar recursos' })).toBeVisible();
  await expectNoAccessibilityViolations(page, 'Receipt durable NO-GO');
  await captureEvidence('preflight-no-go.png', 'durable-no-go', {
    proposal_hash: proposalHash,
    preflight_hash: noGoPreflightHash,
    execution_plan_hash: planHash,
    execution_receipt_hash: executionReceiptHash,
    durable_receipt_hash: noGoDurableReceiptHash,
  });

  await page.getByRole('button', { name: 'Revisar recursos' }).click();
  await expect(page.getByRole('region', {
    name: 'Elige canales; el backend forma el equipo',
  })).toBeFocused();
  await page.getByRole('button', { name: /Generar propuesta/ }).click();
  await expect(page.getByText('Pendiente de receipt')).toBeVisible();
  await page.getByRole('checkbox', { name: /Autorizo el fixture local/ }).check();
  await page.getByRole('button', { name: 'Ejecutar y sellar preflight' }).click();
  await expect(page.getByText('GO · entrada habilitada')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Entrar al proyecto' })).toBeVisible();
  await expect(page.getByText('Fixture proporcional superado.')).toBeVisible();
  await expectNoAccessibilityViolations(page, 'Receipt durable GO');
  await captureEvidence('preflight-go.png', 'durable-go', {
    proposal_hash: proposalHash,
    preflight_hash: goPreflightHash,
    execution_plan_hash: planHash,
    execution_receipt_hash: executionReceiptHash,
    durable_receipt_hash: goDurableReceiptHash,
  });

  expect(executionCount).toBe(2);
  const visualEvidence = {
    schema_version: 'guided_setup_project_visual_evidence_v1',
    screenshots,
  };
  const evidenceManifest = {
    ...visualEvidence,
    evidence_sha256: sha256(canonicalJson(visualEvidence)),
  };
  const evidencePath = testInfo.outputPath('visual-evidence.json');
  await writeFile(evidencePath, `${JSON.stringify(evidenceManifest, null, 2)}\n`, 'utf8');
  const persistedEvidence = JSON.parse(await readFile(evidencePath, 'utf8')) as {
    evidence_sha256: string;
    screenshots: typeof screenshots;
    schema_version: string;
  };
  expect(persistedEvidence.screenshots).toHaveLength(6);
  expect(persistedEvidence.evidence_sha256).toBe(
    sha256(canonicalJson({
      schema_version: persistedEvidence.schema_version,
      screenshots: persistedEvidence.screenshots,
    })),
  );
  expect(browserErrors).toEqual([]);
});
