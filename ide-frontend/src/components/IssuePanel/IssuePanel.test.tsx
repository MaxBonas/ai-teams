import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { IssuePanel } from './IssuePanel';

describe('IssuePanel', () => {
  it('expone un estado vacío comprensible sin controles huérfanos', () => {
    render(
      <IssuePanel
        issue={null}
        profile={null}
        objectiveLabel={null}
        interactions={[]}
        comments={[]}
        commentDraft=""
        busy={false}
        onCommentDraftChange={vi.fn()}
        onSubmitComment={vi.fn(async () => undefined)}
      />,
    );
    expect(screen.getByText('Sin issue seleccionada.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});
