import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectPreflightPanel } from './ProjectPreflightPanel';
import type {
  ProjectPreflightExecutionResponse,
  ProjectPreflightResponse,
} from './types';

function preview(
  kind: string,
  options: {
    actions?: ProjectPreflightResponse['execution_plan']['actions'];
    blocked?: boolean;
  } = {},
): ProjectPreflightResponse {
  const blocked = options.blocked ?? false;
  const actions = options.actions ?? [];
  return {
    preflight: {
      preflight_hash: 'a'.repeat(64),
      objective: {
        kind,
        software_surface_detected: kind === 'software',
        detected_ecosystems: kind === 'software' ? ['javascript_typescript'] : [],
      },
      fixture_policy: {
        kind: kind === 'research'
          ? 'research_evidence_contract'
          : 'software_toolchain_smoke',
        software_fixture_required: kind === 'software',
        remote_probe_requires_consent: true,
        possible_quota_must_be_confirmed: true,
        automatic_install: false,
        max_attempts: 1,
      },
      gates: [{
        id: 'selected_adapters',
        required: true,
        status: blocked ? 'blocked' : 'passed',
        code: blocked ? 'adapter_not_ready' : 'selected_adapters_ready',
        message: blocked ? 'El adapter no está listo.' : 'Adapters listos.',
        next_action: blocked ? 'configure_adapter' : 'continue',
      }],
      summary: {
        status: blocked ? 'no_go' : 'go',
        go: !blocked,
        commit_allowed: !blocked,
        enter_project_allowed: false,
        blockers: blocked ? [{
          gate: 'selected_adapters',
          code: 'adapter_not_ready',
          message: 'El adapter no está listo.',
          next_action: 'configure_adapter',
        }] : [],
        warnings: [],
        optional_pending: [],
        next_action: blocked ? 'configure_adapter' : 'persist_preflight_before_commit',
      },
    },
    execution_plan: {
      plan_hash: 'b'.repeat(64),
      actions,
      planning_blockers: blocked ? [{
        code: 'adapter_not_ready',
        message: 'El adapter no está listo.',
        next_action: 'configure_adapter',
      }] : [],
      summary: {
        status: blocked ? 'blocked' : actions.length ? 'ready' : 'nothing_to_run',
        action_count: actions.length,
        remote_action_count: actions.filter((action) => action.remote).length,
        requires_consent: Boolean(actions.length),
        next_action: blocked
          ? 'configure_adapter'
          : actions.length
            ? 'confirm_preflight_execution'
            : 'persist_preflight_before_commit',
      },
    },
  };
}

function execution(status: 'go' | 'no_go'): ProjectPreflightExecutionResponse {
  const post = preview('research').preflight;
  return {
    receipt: {
      receipt_hash: 'c'.repeat(64),
      summary: { status: status === 'go' ? 'nothing_to_run' : 'failed' },
    },
    post_execution_preflight: {
      ...post,
      preflight_hash: 'd'.repeat(64),
      summary: {
        ...post.summary,
        status,
        go: status === 'go',
        commit_allowed: status === 'go',
      },
    },
    persistence: {
      persisted: true,
      idempotent_replay: false,
      required_before_commit: false,
      durable_receipt: {
        id: 'receipt',
        receipt_hash: 'e'.repeat(64),
        preflight_hash: 'd'.repeat(64),
        execution_plan_hash: 'b'.repeat(64),
        execution_receipt_hash: 'c'.repeat(64),
        status,
        fixture_evidence_refs: [],
      },
    },
  };
}

const emptyConsent = {
  localFixture: false,
  remoteProbe: false,
  quota: false,
};

describe('ProjectPreflightPanel', () => {
  it('seals research without presenting software tests or remote consent', () => {
    const { container } = render(
      <ProjectPreflightPanel
        preview={preview('research')}
        execution={null}
        consent={emptyConsent}
        busy={false}
        onConsent={vi.fn()}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={vi.fn()}
      />,
    );

    expect(screen.getByText('Sin pruebas de software')).toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Sellar comprobación sin ejecutar',
    })).toBeEnabled();
    expect(screen.queryByText(/consumo de cuota/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {
      name: 'Entrar al proyecto',
    })).not.toBeInTheDocument();
    expect(screen.getByRole('list', {
      name: 'Estado del protocolo',
    })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
    expect(container.querySelector('.preflight-console')).not.toHaveAttribute('aria-live');
    expect(container.querySelector('.preflight-seal')).toHaveAttribute('aria-live', 'polite');
    expect(container.querySelector('.preflight-seal')).toHaveAttribute('aria-atomic', 'true');
  });

  it('requires both remote and quota consent before enabling a probe', async () => {
    const remoteAction = {
      id: 'exact_adapter_probe' as const,
      kind: 'structured_output_probe',
      profile_id: 'gemini_api_free',
      model_id: 'gemini-3.6-flash',
      timeout_seconds: 90,
      remote: true,
      quota_possible: true,
      consent_requirements: [
        'confirm_remote_probe',
        'acknowledge_possible_quota',
      ],
    };
    const onConsent = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <ProjectPreflightPanel
        preview={preview('research', { actions: [remoteAction] })}
        execution={null}
        consent={emptyConsent}
        busy={false}
        onConsent={onConsent}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={vi.fn()}
      />,
    );
    const run = screen.getByRole('button', { name: 'Ejecutar y sellar preflight' });
    expect(run).toBeDisabled();
    await user.click(screen.getByLabelText(/Autorizo el probe remoto exacto/));
    expect(onConsent).toHaveBeenCalledWith('remoteProbe', true);

    rerender(
      <ProjectPreflightPanel
        preview={preview('research', { actions: [remoteAction] })}
        execution={null}
        consent={{ ...emptyConsent, remoteProbe: true, quota: true }}
        busy={false}
        onConsent={onConsent}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', {
      name: 'Ejecutar y sellar preflight',
    })).toBeEnabled();
  });

  it('exposes entry only for a matching durable go receipt', async () => {
    const onCommit = vi.fn();
    const user = userEvent.setup();
    render(
      <ProjectPreflightPanel
        preview={preview('research')}
        execution={execution('go')}
        consent={emptyConsent}
        busy={false}
        onConsent={vi.fn()}
        onExecute={vi.fn()}
        onCommit={onCommit}
        onReviewResources={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', {
      name: 'Sellar comprobación sin ejecutar',
    })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Entrar al proyecto' }));
    expect(onCommit).toHaveBeenCalledOnce();
    expect(screen.getByText('GO · entrada habilitada')).toBeInTheDocument();
    expect(within(screen.getByRole('list', {
      name: 'Estado del protocolo',
    })).getAllByText('GO')).toHaveLength(2);
  });

  it('fails closed when any durable receipt hash link is inconsistent', () => {
    const tampered = execution('go');
    tampered.persistence.durable_receipt.execution_plan_hash = 'f'.repeat(64);
    render(
      <ProjectPreflightPanel
        preview={preview('research')}
        execution={tampered}
        consent={emptyConsent}
        busy={false}
        onConsent={vi.fn()}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', {
      name: 'Entrar al proyecto',
    })).not.toBeInTheDocument();
    expect(screen.getByText('Receipt inconsistente')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'La cadena de hashes del receipt no coincide.',
    );
    expect(screen.getByRole('button', {
      name: 'Revisar recursos',
    })).toBeEnabled();
  });

  it('keeps a durable no-go sealed and sends the user back to resources', async () => {
    const reviewResources = vi.fn();
    const user = userEvent.setup();
    render(
      <ProjectPreflightPanel
        preview={preview('research')}
        execution={execution('no_go')}
        consent={emptyConsent}
        busy={false}
        onConsent={vi.fn()}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={reviewResources}
      />,
    );

    expect(screen.queryByRole('button', {
      name: 'Entrar al proyecto',
    })).not.toBeInTheDocument();
    expect(screen.getByText('NO-GO · intento sellado')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Revisar recursos' }));
    expect(reviewResources).toHaveBeenCalledOnce();
  });

  it('shows a blocked server plan without offering execution', () => {
    render(
      <ProjectPreflightPanel
        preview={preview('software', { blocked: true })}
        execution={null}
        consent={emptyConsent}
        busy={false}
        onConsent={vi.fn()}
        onExecute={vi.fn()}
        onCommit={vi.fn()}
        onReviewResources={vi.fn()}
      />,
    );

    expect(screen.getAllByText('El adapter no está listo.').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', {
      name: /Ejecutar y sellar/,
    })).not.toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: 'Revisar recursos',
    })).toBeEnabled();
  });
});
