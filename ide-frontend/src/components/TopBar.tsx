import { DollarSign } from 'lucide-react';
import WorkspaceSelector from './WorkspaceSelector';
import { useIdeStore } from '../store';

interface TopBarProps {
  currentWorkspace: string;
  onWorkspaceChange: (path: string) => void;
  minimizedWindows?: Array<{ id: string; label: string }>;
  onRestoreWindow?: (id: string) => void;
}

export default function TopBar({ currentWorkspace, onWorkspaceChange, minimizedWindows = [], onRestoreWindow }: TopBarProps) {
  const { dashboardData } = useIdeStore();
  const budget = dashboardData?.budget;

  return (
    <div className="top-bar">
      <div className="top-bar-left">
        <div className="app-logo">AI Teams</div>
        {currentWorkspace && (
          <WorkspaceSelector
            currentWorkspace={currentWorkspace}
            onWorkspaceChange={onWorkspaceChange}
          />
        )}
      </div>

      <div className="top-bar-center">
        {minimizedWindows.length > 0 && (
          <div className="top-bar-minimized-tray">
            <span className="top-bar-minimized-label">Minimized</span>
            {minimizedWindows.map((windowItem) => (
              <button
                key={windowItem.id}
                className="top-bar-minimized-btn"
                onClick={() => onRestoreWindow?.(windowItem.id)}
                title={`Restore ${windowItem.label}`}
              >
                {windowItem.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="top-bar-right">
        {budget && (
          <div className="finops-hud">
            <DollarSign size={13} style={{ color: 'var(--accent)' }} />
            <span>Today: ${budget.daily_api_spend_usd?.toFixed(2)}</span>
          </div>
        )}
      </div>
    </div>
  );
}
