import { useState } from 'react';
import AgentLane from './AgentLane';
import type { AgentLaneState } from './AgentLane';

interface AgentPanelProps {
  lanes: Map<string, AgentLaneState>;
  visible: boolean;
}

// Orden de visualizacion por rol (Team Lead primero, QA ultimo)
const ROLE_ORDER: Record<string, number> = {
  team_lead:  0,
  researcher: 1,
  engineer:   2,
  reviewer:   3,
  qa:         4,
};

function sortLanes(lanes: AgentLaneState[]): AgentLaneState[] {
  return [...lanes].sort((a, b) => {
    const orderA = ROLE_ORDER[a.role] ?? 5;
    const orderB = ROLE_ORDER[b.role] ?? 5;
    if (orderA !== orderB) return orderA - orderB;
    // Si mismo rol, mas reciente primero
    return b.startedAt - a.startedAt;
  });
}

export default function AgentPanel({ lanes, visible }: AgentPanelProps) {
  const [collapsed, setCollapsed] = useState(false);

  if (!visible || lanes.size === 0) return null;

  const sorted = sortLanes(Array.from(lanes.values()));
  const activeCount = sorted.filter(l => l.status === 'active').length;
  const doneCount = sorted.filter(l => l.status === 'completed').length;
  const failedCount = sorted.filter(l => l.status === 'failed').length;

  return (
    <div className="agent-panel">
      <div className="agent-panel-header" onClick={() => setCollapsed(v => !v)}>
        <span className="agent-panel-title">
          {collapsed ? '▸' : '▾'} Equipo en ejecución
        </span>
        <span className="agent-panel-summary">
          {activeCount > 0 && <span className="apanel-badge apanel-active">{activeCount} activo{activeCount > 1 ? 's' : ''}</span>}
          {doneCount > 0 && <span className="apanel-badge apanel-done">{doneCount} ok</span>}
          {failedCount > 0 && <span className="apanel-badge apanel-fail">{failedCount} fail</span>}
          <span className="apanel-badge apanel-total">{sorted.length} fases</span>
        </span>
      </div>
      {!collapsed && (
        <div className="agent-panel-lanes">
          {sorted.map(lane => (
            <AgentLane key={lane.taskId} lane={lane} />
          ))}
        </div>
      )}
    </div>
  );
}
