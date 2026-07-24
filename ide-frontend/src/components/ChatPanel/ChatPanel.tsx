import type { RefObject, UIEventHandler } from 'react';
import {
  AlertCircle,
  ArrowDown,
  CheckCircle2,
  MessageSquare,
  RefreshCcw,
  Send,
} from 'lucide-react';
import { formatTime, statusLabel } from '../../lib/format';
import { renderMarkdownLite } from '../../lib/markdown';
import type { ChatMessage } from '../../types/cockpit';
import { ProfileBadge } from '../ProfileBadge';
import './ChatPanel.css';

interface ChatPanelProps {
  issueTitle: string;
  profile: string | null;
  messages: ChatMessage[];
  feedRef: RefObject<HTMLDivElement | null>;
  onFeedScroll: UIEventHandler<HTMLDivElement>;
  jumpVisible: boolean;
  draft: string;
  sending: boolean;
  onReviewInteraction: (interactionId: string) => void;
  onJumpToBottom: () => void;
  onDraftChange: (value: string) => void;
  onSend: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

function clip(text: string, max: number): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > max ? `${normalized.slice(0, max - 1)}...` : normalized;
}

function authorLabel(message: ChatMessage): string {
  if (message.sender === 'user') return 'Tú';
  return (message.author || '')
    .replace(/^role:/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase()) || 'Agente';
}

export function ChatPanel({
  issueTitle,
  profile,
  messages,
  feedRef,
  onFeedScroll,
  jumpVisible,
  draft,
  sending,
  onReviewInteraction,
  onJumpToBottom,
  onDraftChange,
  onSend,
  onRefresh,
}: ChatPanelProps) {
  const destination = issueTitle || 'intake';
  return (
    <section className="panel chat-panel-main">
      <div className="chat-context-bar">
        <MessageSquare size={13} />
        <span>
          Conversación con <strong>Lead</strong>
          {issueTitle ? <> · issue <strong>{clip(issueTitle, 60)}</strong></> : null}
        </span>
        <ProfileBadge profile={profile} compact />
      </div>
      <div className="chat-feed chat-feed-main" ref={feedRef} onScroll={onFeedScroll}>
        {messages.length === 0 && (
          <p className="muted chat-empty">Sin mensajes aún. Escribe algo al Lead o despiértalo para empezar.</p>
        )}
        {messages.map((message) => {
          if (message.item_type === 'interaction') {
            const pending = message.interaction_status === 'pending';
            return (
              <div key={message.id} className={`chat-deciref${pending ? ' pending' : ' resolved'}`}>
                {pending ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}
                <div className="chat-deciref-body">
                  <strong>{message.title || message.kind}</strong>
                  {(message.summary || message.body) && (
                    <span className="chat-deciref-sub">{clip(message.summary || message.body, 120)}</span>
                  )}
                </div>
                {pending ? (
                  <button
                    type="button"
                    className="secondary-button chat-deciref-go"
                    onClick={() => onReviewInteraction(message.source_id)}
                  >
                    Revisar en Bandeja →
                  </button>
                ) : (
                  <span className="chat-resolved-badge">{statusLabel(message.interaction_status || '')}</span>
                )}
                <time className="chat-time">{formatTime(message.created_at)}</time>
              </div>
            );
          }
          const userMessage = message.sender === 'user';
          return (
            <div key={message.id} className={`chat-bubble${userMessage ? ' user' : ' agent'}`}>
              <div className="chat-bubble-meta">
                <span className="chat-author">{authorLabel(message)}</span>
                <time className="chat-time">{formatTime(message.created_at)}</time>
              </div>
              <div className="chat-bubble-body">
                {userMessage ? message.body : renderMarkdownLite(message.body)}
              </div>
            </div>
          );
        })}
      </div>
      {jumpVisible && (
        <button
          type="button"
          className="chat-jump-bottom"
          onClick={onJumpToBottom}
          title="Ir al final del chat"
          aria-label="Ir al final del chat"
        >
          <ArrowDown size={15} />
        </button>
      )}
      <div className="chat-input-row chat-input-row-main">
        <span
          className="chat-dest-chip"
          title={`El mensaje se envía al Lead en la issue "${destination}"`}
        >
          → Lead · {clip(destination, 26)}
        </span>
        <input
          type="text"
          className="chat-input"
          placeholder="Escribe al Lead... (Enter para enviar)"
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void onSend();
            }
          }}
          disabled={sending}
        />
        <button
          type="button"
          className="chat-send-btn"
          onClick={() => void onSend()}
          disabled={sending || !draft.trim()}
          title="Enviar"
          aria-label="Enviar mensaje al Lead"
        >
          <Send size={15} />
        </button>
        <button
          className="secondary-button"
          onClick={() => void onRefresh()}
          title="Actualizar"
          aria-label="Actualizar conversación"
          type="button"
          style={{ padding: '0 10px' }}
        >
          <RefreshCcw size={14} />
        </button>
      </div>
    </section>
  );
}
