import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { RunsPanel } from './RunsPanel';

describe('RunsPanel', () => {
  it('muestra errores de run sin ocultar el código', () => {
    render(
      <RunsPanel
        runs={[]}
        selectedRun={{
          id: 'run:error',
          agent_id: 'role:lead',
          status: 'failed',
          error: 'El adapter agotó cuota',
          error_code: 'quota_exhausted',
        }}
        events={[]}
        runId=""
        busy={false}
        onRunIdChange={vi.fn()}
        onSelectRun={vi.fn(async () => undefined)}
      />,
    );
    expect(screen.getByText(/El adapter agotó cuota/)).toHaveTextContent('quota_exhausted');
    expect(screen.getByText('Sin eventos registrados.')).toBeInTheDocument();
  });

  it('permite consultar una run sólo con teclado', async () => {
    const user = userEvent.setup();
    const onSelectRun = vi.fn(async () => undefined);
    render(
      <RunsPanel
        runs={[]}
        selectedRun={null}
        events={[]}
        runId="run:keyboard"
        busy={false}
        onRunIdChange={vi.fn()}
        onSelectRun={onSelectRun}
      />,
    );
    const input = screen.getByRole('textbox', { name: 'ID completo de la run' });
    input.focus();
    await user.keyboard('{Tab}{Enter}');
    expect(onSelectRun).toHaveBeenCalledWith('run:keyboard');
  });
});
