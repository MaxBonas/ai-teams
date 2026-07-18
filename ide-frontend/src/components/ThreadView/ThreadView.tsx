/**
 * ThreadView — compact/full thread display with context-summary block support.
 *
 * Compact (default): shows synthesized summary blocks (collapsible) +
 *   recent unsynthesized comments.  A "Ver hilo completo" button opens a
 *   modal with all comments in full.
 *
 * Full (modal): all comments chronological, fetched lazily on first open.
 */
import React, { useEffect, useRef, useState } from 'react';
import { apiFetch } from '../../lib/api';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ThreadComment {
  id: string;
  body: string;
  author_agent_id?: string | null;
  author_user_id?: string | null;
  created_at?: string;
}

interface SummaryBlock {
  summary_markdown: string;
  start_comment_id?: string | null;
  end_comment_id?: string | null;
  char_count_original?: number;
}

interface CompactThreadData {
  view: 'compact';
  issue_id: string;
  total_comments: number;
  summary_blocks: SummaryBlock[];
  synthesized_through: string | null;
  recent_comments: ThreadComment[];
  has_synthesized_history: boolean;
}

interface FullThreadData {
  view: 'full';
  issue_id: string;
  total_comments: number;
  comments: ThreadComment[];
  truncated: boolean;
}

// ── Props ─────────────────────────────────────────────────────────────────────

export interface ThreadViewProps {
  issueId: string;
  /** Optional pre-loaded comments (e.g. already fetched by parent). Used as
   *  instant placeholder until the compact thread data loads. */
  preloadedComments?: ThreadComment[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(ts?: string | null): string {
  if (!ts) return '';
  try {
    // DB timestamps are UTC; naive strings must be tagged as such or they are
    // parsed as local time. Offset-suffixed ("+00:00") strings stay untouched
    // (the old endsWith('Z') check produced the invalid "...+00:00Z").
    let iso = ts.includes('T') ? ts : ts.replace(' ', 'T');
    if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)) iso += 'Z';
    return new Date(iso).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return ts;
  }
}

function authorLabel(c: ThreadComment): string {
  return c.author_user_id ?? c.author_agent_id ?? 'sistema';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function CommentBubble({ comment }: { comment: ThreadComment }) {
  const isUser = Boolean(comment.author_user_id);
  return (
    <article
      style={{
        background: isUser ? 'var(--surface-2)' : 'var(--surface)',
        border: `1px solid ${isUser ? 'var(--border-strong)' : 'var(--border)'}`,
        borderRadius: 'var(--radius)',
        padding: '8px 12px',
        marginBottom: 6,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: '0.72rem',
          color: 'var(--text-muted)',
          marginBottom: 4,
        }}
      >
        <span style={{ fontWeight: 600, color: isUser ? 'var(--accent-text)' : 'var(--text-bright)' }}>
          {authorLabel(comment)}
        </span>
        <span>{formatTime(comment.created_at)}</span>
      </div>
      <p style={{ margin: 0, fontSize: '0.85rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {comment.body}
      </p>
    </article>
  );
}

function SummaryBlockCard({
  block,
  index,
}: {
  block: SummaryBlock;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const ratio = block.char_count_original
    ? Math.round((block.summary_markdown.length / block.char_count_original) * 100)
    : null;

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius)',
        marginBottom: 6,
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setExpanded((e) => !e)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '7px 12px',
          background: 'var(--surface-2)',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--text-muted)',
          fontSize: '0.78rem',
          textAlign: 'left',
        }}
      >
        <span style={{ color: 'var(--text-dim)' }}>{expanded ? '▾' : '▸'}</span>
        <span style={{ color: 'var(--text-bright)', fontWeight: 600 }}>
          Síntesis #{index + 1}
        </span>
        {ratio !== null && (
          <span style={{ marginLeft: 'auto', color: 'var(--text-dim)' }}>
            {ratio}% del original
          </span>
        )}
      </button>
      {expanded && (
        <div
          style={{
            padding: '10px 12px',
            background: 'var(--surface)',
            fontSize: '0.83rem',
            color: 'var(--text)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {block.summary_markdown}
        </div>
      )}
    </div>
  );
}

function FullThreadModal({
  issueId,
  onClose,
}: {
  issueId: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<FullThreadData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/issues/${encodeURIComponent(issueId)}/thread?view=full`)
      .then((r) => r.json())
      .then((json) => {
        if (!cancelled) setData(json as FullThreadData);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [issueId]);

  // Close on overlay click
  const handleOverlayClick = (e: React.MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
  };

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          width: 'min(720px, 94vw)',
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: 'var(--shadow)',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 16px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <span style={{ fontWeight: 600, color: 'var(--text-bright)' }}>
            Hilo completo{' '}
            {data ? (
              <span style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.8rem' }}>
                ({data.total_comments} comentarios{data.truncated ? ', truncado' : ''})
              </span>
            ) : null}
          </span>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              color: 'var(--text-muted)',
              padding: '3px 10px',
              cursor: 'pointer',
              fontSize: '0.82rem',
            }}
          >
            Cerrar
          </button>
        </div>

        {/* Body */}
        <div style={{ overflowY: 'auto', padding: 16, flex: 1 }}>
          {error ? (
            <p style={{ color: 'var(--blocked)', fontSize: '0.85rem' }}>
              Error cargando hilo: {error}
            </p>
          ) : !data ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Cargando…</p>
          ) : data.comments.length === 0 ? (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Sin comentarios.</p>
          ) : (
            data.comments.map((c) => <CommentBubble key={c.id} comment={c} />)
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function ThreadView({ issueId, preloadedComments }: ThreadViewProps) {
  const [compact, setCompact] = useState<CompactThreadData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/api/issues/${encodeURIComponent(issueId)}/thread?view=compact`)
      .then((r) => r.json())
      .then((json) => {
        if (cancelled) return;
        setCompact(json as CompactThreadData);
        // Un fallo anterior (p.ej. de otra issue) no debe dejar el error pegado.
        setError(null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId]);

  // ── Render ─────────────────────────────────────────────────────────────────

  // While loading use preloaded comments as instant placeholder
  if (loading && !compact) {
    const placeholder = preloadedComments ?? [];
    return (
      <div style={{ opacity: 0.6 }}>
        {placeholder.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Cargando hilo…</p>
        ) : (
          placeholder.map((c) => <CommentBubble key={c.id} comment={c} />)
        )}
      </div>
    );
  }

  if (error) {
    // Graceful fallback to preloaded comments
    const fallback = preloadedComments ?? [];
    return (
      <>
        {fallback.map((c) => <CommentBubble key={c.id} comment={c} />)}
        <p style={{ color: 'var(--text-dim)', fontSize: '0.78rem', marginTop: 4 }}>
          (No se pudo cargar el hilo comprimido)
        </p>
      </>
    );
  }

  if (!compact) return null;

  const {
    summary_blocks,
    recent_comments,
    total_comments,
    has_synthesized_history,
  } = compact;

  const synthesizedCount = total_comments - recent_comments.length;

  return (
    <>
      {/* ── Summary blocks ─────────────────────────────────────────────────── */}
      {has_synthesized_history && (
        <div style={{ marginBottom: 10 }}>
          <div
            style={{
              fontSize: '0.72rem',
              color: 'var(--text-dim)',
              marginBottom: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span
              style={{
                display: 'inline-block',
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: 'var(--accent)',
              }}
            />
            {summary_blocks.length} bloque{summary_blocks.length !== 1 ? 's' : ''} sintetizado{summary_blocks.length !== 1 ? 's' : ''}
            {synthesizedCount > 0 && (
              <span style={{ color: 'var(--text-dim)' }}>
                ({synthesizedCount} comentario{synthesizedCount !== 1 ? 's' : ''} archivados)
              </span>
            )}
          </div>
          {summary_blocks.map((block, i) => (
            <SummaryBlockCard key={i} block={block} index={i} />
          ))}
        </div>
      )}

      {/* ── Recent comments ────────────────────────────────────────────────── */}
      {recent_comments.length > 0 ? (
        <>
          {has_synthesized_history && (
            <div
              style={{
                fontSize: '0.72rem',
                color: 'var(--text-dim)',
                marginBottom: 6,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <span
                style={{
                  flex: 1,
                  borderTop: '1px dashed var(--border)',
                  display: 'inline-block',
                }}
              />
              <span>recientes</span>
              <span
                style={{
                  flex: 1,
                  borderTop: '1px dashed var(--border)',
                  display: 'inline-block',
                }}
              />
            </div>
          )}
          {recent_comments.map((c) => <CommentBubble key={c.id} comment={c} />)}
        </>
      ) : (
        !has_synthesized_history && (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Sin comentarios.</p>
        )
      )}

      {/* ── Footer: full-thread button ──────────────────────────────────────── */}
      {total_comments > 0 && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
          <button
            onClick={() => setShowModal(true)}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              color: 'var(--text-muted)',
              padding: '4px 12px',
              cursor: 'pointer',
              fontSize: '0.78rem',
            }}
          >
            Ver hilo completo ({total_comments})
          </button>
        </div>
      )}

      {/* ── Full-thread modal ──────────────────────────────────────────────── */}
      {showModal && (
        <FullThreadModal issueId={issueId} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}
