import { ThreadView } from '../ThreadView';
import { ProfileBadge } from '../ProfileBadge';
import { formatTime, statusLabel } from '../../lib/format';
import type { Comment, Interaction, Issue } from '../../types/cockpit';
import './IssuePanel.css';

interface IssuePanelProps {
  issue: Issue | null;
  profile: string | null;
  objectiveLabel: string | null;
  interactions: Interaction[];
  comments: Comment[];
  commentDraft: string;
  busy: boolean;
  onCommentDraftChange: (value: string) => void;
  onSubmitComment: () => Promise<void>;
}

export function IssuePanel({
  issue,
  profile,
  objectiveLabel,
  interactions,
  comments,
  commentDraft,
  busy,
  onCommentDraftChange,
  onSubmitComment,
}: IssuePanelProps) {
  if (!issue) {
    return <section className="panel issue-panel"><p className="muted">Sin issue seleccionada.</p></section>;
  }

  return (
    <section className="panel issue-panel">
      <div className="issue-header">
        <div>
          <h2>{issue.title}</h2>
          <p>{issue.description || issue.title}</p>
        </div>
        <div className="issue-header-tags">
          <ProfileBadge profile={profile} />
          {objectiveLabel ? <span className="status-pill">{objectiveLabel}</span> : null}
          <span className={`status-pill status-${issue.status}`}>{statusLabel(issue.status)}</span>
        </div>
      </div>
      <div className="issue-meta">
        <span>Owner: {issue.assignee_agent_id || 'sin asignar'}</span>
        <span>Rol: {issue.role || '-'}</span>
        <span>Complejidad: {issue.complexity || '-'}</span>
        <span>Creada: {formatTime(issue.created_at)}</span>
      </div>
      {interactions.length ? (
        <div className="inline-interactions">
          {interactions.map((interaction) => (
            <span key={interaction.id}>
              {interaction.title || interaction.kind}: {statusLabel(interaction.status)}
            </span>
          ))}
        </div>
      ) : null}
      <div className="thread">
        <ThreadView
          key={issue.id}
          issueId={issue.id}
          preloadedComments={comments}
        />
      </div>
      <div className="composer">
        <textarea
          aria-label="Contexto o instrucción para la issue"
          placeholder="Añade contexto o una instrucción..."
          value={commentDraft}
          onChange={(event) => onCommentDraftChange(event.target.value)}
        />
        <button
          type="button"
          onClick={() => void onSubmitComment()}
          disabled={busy || !commentDraft.trim()}
        >
          Enviar
        </button>
      </div>
    </section>
  );
}
