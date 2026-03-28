import { useState, useEffect, useRef } from 'react';

export interface AgentLaneState {
  taskId: string;
  agentId: string;
  role: string;
  phase: string;
  title: string;
  status: 'waiting' | 'active' | 'completed' | 'failed';
  outputText: string;
  thinkingText: string;
  preview: string;
  durationMs: number;
  startedAt: number;
}

const ROLE_ICONS: Record<string, string> = {
  team_lead: 'TL',
  researcher: 'RS',
  engineer: 'EG',
  reviewer: 'RV',
  qa: 'QA',
};

const ROLE_COLORS: Record<string, string> = {
  team_lead:  '#7c6af5',
  researcher: '#3b9de8',
  engineer:   '#22c55e',
  reviewer:   '#f59e0b',
  qa:         '#ec4899',
};

function ElapsedTimer({ startedAt, stopped }: { startedAt: number; stopped: boolean }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (stopped) return;
    const interval = setInterval(() => {
      setElapsed(Date.now() - startedAt);
    }, 250);
    return () => clearInterval(interval);
  }, [startedAt, stopped]);

  const secs = stopped ? Math.round(elapsed / 1000) : Math.round((Date.now() - startedAt) / 1000);
  return <span className="lane-timer">{secs}s</span>;
}

interface AgentLaneProps {
  lane: AgentLaneState;
}

export default function AgentLane({ lane }: AgentLaneProps) {
  const [thinkingExpanded, setThinkingExpanded] = useState(false);
  const [outputExpanded, setOutputExpanded] = useState(false);
  const outputRef = useRef<HTMLDivElement>(null);

  const icon = ROLE_ICONS[lane.role] ?? lane.role.slice(0, 2).toUpperCase();
  const color = ROLE_COLORS[lane.role] ?? '#888';
  const isActive = lane.status === 'active';
  const isDone = lane.status === 'completed';
  const isFailed = lane.status === 'failed';
  const isWaiting = lane.status === 'waiting';
  const stopped = isDone || isFailed;

  // Auto-scroll output area while streaming
  useEffect(() => {
    if (isActive && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [lane.outputText, isActive]);

  const statusDot = isActive
    ? <span className="lane-dot lane-dot-active" title="activo" />
    : isDone
    ? <span className="lane-dot lane-dot-done" title="completado">✓</span>
    : isFailed
    ? <span className="lane-dot lane-dot-failed" title="fallido">✗</span>
    : <span className="lane-dot lane-dot-waiting" title="en espera">○</span>;

  const displayText = isActive
    ? lane.outputText
    : isDone && lane.preview
    ? lane.preview
    : '';

  return (
    <div className={`agent-lane agent-lane-${lane.status}`}>
      <div className="lane-header">
        <span className="lane-icon" style={{ backgroundColor: color }}>{icon}</span>
        <span className="lane-phase">{lane.phase}</span>
        <span className="lane-agent-id">{lane.agentId}</span>
        <div className="lane-status-area">
          {statusDot}
          {!isWaiting && (
            <ElapsedTimer
              startedAt={lane.startedAt}
              stopped={stopped}
            />
          )}
        </div>
      </div>

      {lane.thinkingText && (
        <div className="lane-thinking-section">
          <button
            className="lane-collapse-btn"
            onClick={() => setThinkingExpanded(v => !v)}
          >
            {thinkingExpanded ? '▾' : '▸'} Razonamiento
          </button>
          {thinkingExpanded && (
            <div className="lane-thinking-body">{lane.thinkingText}</div>
          )}
        </div>
      )}

      {displayText && (
        <div className="lane-output-section">
          {isDone && displayText.length > 160 && (
            <button
              className="lane-collapse-btn"
              onClick={() => setOutputExpanded(v => !v)}
            >
              {outputExpanded ? '▾' : '▸'} {outputExpanded ? 'Ocultar' : 'Ver output'}
            </button>
          )}
          <div
            ref={outputRef}
            className={`lane-output-body${isActive ? ' lane-output-streaming' : ''}`}
            style={{ display: isDone && !outputExpanded && displayText.length > 160 ? 'none' : undefined }}
          >
            {isActive
              ? displayText
              : displayText.length > 160 && !outputExpanded
              ? displayText.slice(0, 160) + '…'
              : displayText}
          </div>
        </div>
      )}

      {isFailed && lane.preview && (
        <div className="lane-error-body">{lane.preview}</div>
      )}
    </div>
  );
}
