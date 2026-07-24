import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ChatPanel } from './ChatPanel';
import type { ChatMessage } from '../../types/cockpit';

const baseProps = {
  issueTitle: 'Issue de prueba',
  profile: 'full_team',
  messages: [] as ChatMessage[],
  feedRef: createRef<HTMLDivElement>(),
  onFeedScroll: vi.fn(),
  jumpVisible: false,
  draft: '',
  sending: false,
  onReviewInteraction: vi.fn(),
  onJumpToBottom: vi.fn(),
  onDraftChange: vi.fn(),
  onSend: vi.fn(async () => undefined),
  onRefresh: vi.fn(async () => undefined),
};

describe('ChatPanel', () => {
  it('presenta el estado vacío y bloquea enviar sin texto', () => {
    render(<ChatPanel {...baseProps} />);
    expect(screen.getByText(/Sin mensajes aún/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Enviar mensaje al Lead' })).toBeDisabled();
  });

  it('envía con Enter y conserva el foco de teclado', async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(async () => undefined);
    const onDraftChange = vi.fn();
    const { rerender } = render(
      <ChatPanel {...baseProps} draft="hola" onSend={onSend} onDraftChange={onDraftChange} />,
    );
    const input = screen.getByRole('textbox');
    await user.click(input);
    await user.keyboard('{Enter}');
    expect(onSend).toHaveBeenCalledOnce();
    expect(input).toHaveFocus();

    rerender(<ChatPanel {...baseProps} draft="hola" sending />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });

  it('abre una decisión pendiente mediante teclado', async () => {
    const user = userEvent.setup();
    const onReviewInteraction = vi.fn();
    const interaction: ChatMessage = {
      id: 'chat:decision',
      source_id: 'interaction:1',
      item_type: 'interaction',
      sender: 'agent',
      author: 'role:lead',
      body: '',
      title: 'Confirmar contratación',
      summary: 'Revisa el equipo propuesto.',
      kind: 'suggest_tasks',
      interaction_status: 'pending',
      payload: {},
      issue_id: 'issue:1',
      source_run_id: null,
      created_at: '2026-07-24 10:00:00',
    };
    render(
      <ChatPanel
        {...baseProps}
        messages={[interaction]}
        onReviewInteraction={onReviewInteraction}
      />,
    );
    const review = screen.getByRole('button', { name: /Revisar en Bandeja/ });
    review.focus();
    await user.keyboard('{Enter}');
    expect(onReviewInteraction).toHaveBeenCalledWith('interaction:1');
  });
});
