import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectSetupWizard } from './ProjectSetupWizard';

const proposal = {
  proposal_hash: 'a'.repeat(64),
  project: {
    mode: 'create',
    name: 'Portal',
    target: 'C:/projects/Portal',
    instructions_preview: '',
    objective: 'Crear un portal accesible',
  },
  ecosystems: {
    detected_ids: ['javascript_typescript'],
    scan_truncated: false,
  },
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
      candidate: {
        candidate_id: 'codex:lead',
        profile_id: 'codex_subscription',
        model_id: 'gpt-lead',
        provider: 'OpenAI',
        channel: 'subscription',
        tier: 'premium',
        score: 94,
        coverage_eligible: true,
        owner_selectable: true,
        economics: {
          class: 'zero_marginal',
          marginal_cost: 'zero',
        },
        privacy: { allowed: true },
        capabilities: ['reasoning'],
        gates: { calibrated: true },
      },
    }],
    quorum_diversity: {
      ready: true,
      perspective_count: 0,
      capacity_pool_count: 0,
    },
    manual_override_count: 0,
  },
  budget: { mode: 'balanced' },
  degradations: [],
  save_gate: {
    allowed: true,
    blockers: [],
    requires_owner_confirmation: false,
  },
};

const softwarePreflight = {
  preflight: {
    preflight_hash: 'b'.repeat(64),
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
      blockers: [{
        gate: 'proportional_fixture',
        code: 'software_fixture_required',
        message: 'Falta ejecutar el fixture proporcional.',
        next_action: 'run_proportional_fixture',
      }],
      warnings: [],
      optional_pending: [],
      next_action: 'run_proportional_fixture',
    },
  },
  execution_plan: {
    plan_hash: 'c'.repeat(64),
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

const executedPreflight = {
  receipt: {
    receipt_hash: 'd'.repeat(64),
    summary: { status: 'passed' },
  },
  post_execution_preflight: {
    ...softwarePreflight.preflight,
    preflight_hash: 'e'.repeat(64),
    gates: softwarePreflight.preflight.gates.map((gate) => ({
      ...gate,
      status: 'passed',
      code: 'software_fixture_passed',
      message: 'El fixture proporcional pasó.',
      next_action: 'continue',
    })),
    summary: {
      ...softwarePreflight.preflight.summary,
      status: 'go',
      go: true,
      commit_allowed: true,
      blockers: [],
      next_action: 'persist_preflight_before_commit',
    },
  },
  persistence: {
    persisted: true,
    idempotent_replay: false,
    required_before_commit: false,
    durable_receipt: {
      id: 'receipt-1',
      receipt_hash: 'f'.repeat(64),
      preflight_hash: 'e'.repeat(64),
      execution_plan_hash: 'c'.repeat(64),
      execution_receipt_hash: 'd'.repeat(64),
      status: 'go',
      fixture_evidence_refs: ['sha256:' + '1'.repeat(64)],
    },
  },
};

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }));
}

describe('ProjectSetupWizard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('builds a sealed preview and commits the same hash', async () => {
    let revision = 1;
    const bodies: Array<Record<string, unknown>> = [];
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
      bodies.push(body);
      if (url.endsWith('/needs-assessment')) {
        return response({ submission: { schema_version: 'guided_setup_needs_v1' } });
      }
      if (url.endsWith('/sessions')) {
        return response({ session: { id: 'session-1', revision } });
      }
      if (url.includes('/steps/')) {
        revision += 1;
        return response({ session: { id: 'session-1', revision } });
      }
      if (url.endsWith('/project-proposal')) {
        return response({
          proposal,
          coverage: {
            roles: {
              team_lead: {
                candidates: [proposal.team.assignments[0].candidate],
                excluded_candidates: [],
              },
            },
          },
        });
      }
      if (url.endsWith('/project-preflight')) {
        return response(softwarePreflight);
      }
      if (url.endsWith('/project-preflight-execute')) {
        return response(executedPreflight);
      }
      if (url.endsWith('/project-commit')) {
        return response({
          result: {
            workspace: 'C:/projects/Portal',
            configured: true,
          },
        });
      }
      return response({ detail: 'unexpected' }, 500);
    });
    vi.stubGlobal('fetch', fetchMock);
    const committed = vi.fn();
    const user = userEvent.setup();

    render(
      <ProjectSetupWizard
        projectsRoot="C:/projects"
        adapters={[{
          id: 'codex_subscription',
          label: 'Codex',
          adapter_type: 'subscription_cli',
          channel: 'subscription',
        }]}
        preparedAdapterIds={['codex_subscription']}
        selectedAdapterIds={['codex_subscription']}
        onToggleAdapter={vi.fn()}
        onCommitted={committed}
      />,
    );

    await user.clear(screen.getByLabelText('Nombre visible'));
    await user.type(screen.getByLabelText('Nombre visible'), 'Portal');
    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    await user.type(
      screen.getByLabelText('Resultado que debe conseguir el Lead'),
      'Crear un portal accesible',
    );
    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    await user.click(screen.getByRole('button', { name: /Generar propuesta/ }));

    expect(await screen.findByText('Propuesta válida')).toBeInTheDocument();
    expect(screen.getByText('gpt-lead')).toBeInTheDocument();
    expect(screen.getByText('coste 0')).toBeInTheDocument();
    expect(screen.getByText('calibrated')).toBeInTheDocument();
    expect(screen.getByText('mode: balanced')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Entrar al proyecto' })).not.toBeInTheDocument();
    const executeButton = screen.getByRole('button', { name: 'Ejecutar y sellar preflight' });
    expect(executeButton).toBeDisabled();
    await user.click(screen.getByLabelText(/Autorizo el fixture local/));
    await user.click(executeButton);
    await user.click(await screen.findByRole('button', { name: 'Entrar al proyecto' }));

    await waitFor(() => expect(committed).toHaveBeenCalledWith({
      workspace: 'C:/projects/Portal',
      configured: true,
      success: true,
    }));
    const commitBody = bodies.find((body) => body.confirm === true);
    expect(commitBody).toMatchObject({
      proposal_hash: proposal.proposal_hash,
      confirm: true,
      requested_profile: 'full_team',
    });
  });

  it('requires an import path before advancing', async () => {
    const user = userEvent.setup();
    render(
      <ProjectSetupWizard
        projectsRoot="C:/projects"
        adapters={[]}
        preparedAdapterIds={[]}
        selectedAdapterIds={[]}
        onToggleAdapter={vi.fn()}
        onCommitted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Importar carpeta' }));
    const continueButton = screen.getByRole('button', { name: /Continuar/ });
    expect(continueButton).toBeEnabled();
    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    expect(await screen.findByText(
      'Indica la ruta existente.',
    )).toBeInTheDocument();
    expect(screen.getByLabelText(/Ruta existente dentro de la raíz/))
      .toHaveFocus();
    await user.type(
      screen.getByLabelText(/Ruta existente dentro de la raíz/),
      'C:/projects/existing',
    );
    expect(screen.queryByText(
      'Indica la ruta existente.',
    )).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    expect(screen.getByRole('region', {
      name: 'Define el resultado esperado',
    })).toHaveFocus();
  });

  it('announces progress and focuses the newly selected stage', async () => {
    const user = userEvent.setup();
    render(
      <ProjectSetupWizard
        projectsRoot="C:/projects"
        adapters={[]}
        preparedAdapterIds={[]}
        selectedAdapterIds={[]}
        onToggleAdapter={vi.fn()}
        onCommitted={vi.fn()}
      />,
    );

    expect(screen.getByLabelText('Nombre visible')).toHaveFocus();
    expect(screen.getByRole('button', {
      name: 'Paso 1 de 4: Proyecto. Actual',
    })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Paso completo.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Continuar/ }))
      .toHaveAttribute('aria-describedby', 'project-setup-readiness');

    await user.clear(screen.getByLabelText('Nombre visible'));
    await user.type(screen.getByLabelText('Nombre visible'), 'Portal');
    await user.click(screen.getByRole('button', { name: /Continuar/ }));

    const objectiveStage = screen.getByRole('region', {
      name: 'Define el resultado esperado',
    });
    await waitFor(() => expect(objectiveStage).toHaveFocus());
    expect(screen.getByRole('button', {
      name: 'Paso 1 de 4: Proyecto. Completado',
    })).toBeEnabled();
    expect(screen.getByRole('button', {
      name: 'Paso 2 de 4: Objetivo. Actual',
    })).toHaveAttribute('aria-current', 'step');

    await user.click(screen.getByRole('button', {
      name: 'Paso 1 de 4: Proyecto. Completado',
    }));
    await waitFor(() => expect(screen.getByRole('region', {
      name: '¿Proyecto nuevo o carpeta existente?',
    })).toHaveFocus());
  });

  it('keeps correction actions keyboard-reachable and focuses each invalid control', async () => {
    const user = userEvent.setup();
    render(
      <ProjectSetupWizard
        projectsRoot="C:/projects"
        adapters={[]}
        preparedAdapterIds={[]}
        selectedAdapterIds={[]}
        onToggleAdapter={vi.fn()}
        onCommitted={vi.fn()}
      />,
    );

    const nameInput = screen.getByLabelText('Nombre visible');
    await user.clear(nameInput);
    const continueIdentity = screen.getByRole('button', { name: /Continuar/ });
    continueIdentity.focus();
    await user.keyboard('{Enter}');
    expect(nameInput).toHaveFocus();
    expect(nameInput).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Escribe un nombre.')).toHaveAttribute(
      'role',
      'alert',
    );

    await user.type(nameInput, 'Portal');
    expect(nameInput).toHaveAttribute('aria-invalid', 'false');
    screen.getByRole('button', { name: /Continuar/ }).focus();
    await user.keyboard('{Enter}');

    const continueObjective = screen.getByRole('button', { name: /Continuar/ });
    continueObjective.focus();
    await user.keyboard('{Enter}');
    const goalInput = screen.getByLabelText(/Resultado que debe conseguir el Lead/);
    expect(goalInput).toHaveFocus();
    expect(goalInput).toHaveAttribute('aria-invalid', 'true');

    await user.type(goalInput, 'Entregar un portal');
    screen.getByRole('button', { name: /Continuar/ }).focus();
    await user.keyboard('{Enter}');

    const generate = screen.getByRole('button', { name: /Generar propuesta/ });
    expect(generate).toBeEnabled();
    generate.focus();
    await user.keyboard('{Enter}');
    const resourceError = await screen.findByText(
      'Selecciona un adapter preparado.',
    );
    expect(resourceError).toHaveFocus();
  });

  it('returns focus to resources when a stale preflight invalidates review', async () => {
    let revision = 1;
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/needs-assessment')) {
        return response({ submission: { schema_version: 'guided_setup_needs_v1' } });
      }
      if (url.endsWith('/sessions')) {
        return response({ session: { id: 'session-stale', revision } });
      }
      if (url.includes('/steps/')) {
        revision += 1;
        return response({ session: { id: 'session-stale', revision } });
      }
      if (url.endsWith('/project-proposal')) {
        return response({
          proposal,
          coverage: {
            roles: {
              team_lead: {
                candidates: [proposal.team.assignments[0].candidate],
                excluded_candidates: [],
              },
            },
          },
        });
      }
      if (url.endsWith('/project-preflight')) return response(softwarePreflight);
      if (url.endsWith('/project-preflight-execute')) {
        return response({ detail: { reason: 'stale_preflight' } }, 409);
      }
      return response({ detail: 'unexpected' }, 500);
    }));
    const user = userEvent.setup();
    render(
      <ProjectSetupWizard
        projectsRoot="C:/projects"
        adapters={[{
          id: 'codex_subscription',
          label: 'Codex',
          adapter_type: 'subscription_cli',
          channel: 'subscription',
        }]}
        preparedAdapterIds={['codex_subscription']}
        selectedAdapterIds={['codex_subscription']}
        onToggleAdapter={vi.fn()}
        onCommitted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    await user.type(
      screen.getByLabelText('Resultado que debe conseguir el Lead'),
      'Crear un portal',
    );
    await user.click(screen.getByRole('button', { name: /Continuar/ }));
    await user.click(screen.getByRole('button', { name: /Generar propuesta/ }));
    await user.click(await screen.findByLabelText(/Autorizo el fixture local/));
    await user.click(screen.getByRole('button', { name: 'Ejecutar y sellar preflight' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'El preflight quedó obsoleto',
    );
    await waitFor(() => expect(screen.getByRole('region', {
      name: 'Elige canales; el backend forma el equipo',
    })).toHaveFocus());
    expect(screen.queryByRole('button', { name: 'Entrar al proyecto' }))
      .not.toBeInTheDocument();
  });
});
