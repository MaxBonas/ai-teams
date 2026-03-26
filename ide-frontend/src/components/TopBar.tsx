import { Settings, PlayCircle, BarChart2, DollarSign } from 'lucide-react';
import WorkspaceSelector from './WorkspaceSelector';
import { useIdeStore } from '../store';

interface TopBarProps {
    currentWorkspace: string;
    onWorkspaceChange: (path: string) => void;
    activeTab: 'editor' | 'dashboard';
    onTabChange: (tab: 'editor' | 'dashboard') => void;
    minimizedWindows?: Array<{ id: string; label: string }>;
    onRestoreWindow?: (id: string) => void;
}

export default function TopBar({ currentWorkspace, onWorkspaceChange, activeTab, onTabChange, minimizedWindows = [], onRestoreWindow }: TopBarProps) {
    const { dashboardData } = useIdeStore();
    const budget = dashboardData?.budget;

    return (
        <div className="top-bar">
            <div className="top-bar-left">
                <div className="app-logo">AI Teams IDE</div>
                {currentWorkspace && (
                    <WorkspaceSelector
                        currentWorkspace={currentWorkspace}
                        onWorkspaceChange={onWorkspaceChange}
                    />
                )}
            </div>

            <div className="top-bar-center">
                <button
                    className={`tab-btn ${activeTab === 'editor' ? 'active' : ''}`}
                    onClick={() => onTabChange('editor')}
                >
                    Editor
                </button>
                <button
                    className={`tab-btn ${activeTab === 'dashboard' ? 'active' : ''}`}
                    onClick={() => onTabChange('dashboard')}
                >
                    <BarChart2 size={14} /> Dashboard
                </button>

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
                    <div className="finops-hud" style={{ display: 'flex', alignItems: 'center', gap: '6px', marginRight: '16px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                        <DollarSign size={13} style={{ color: 'var(--accent)' }} />
                        <span>Today: ${budget.daily_api_spend_usd?.toFixed(2)}</span>
                        <span style={{ opacity: 0.5 }}>|</span>
                        <span>Forecast: {(budget as any).forecast_utilization_ratio ? ((budget as any).forecast_utilization_ratio * 100).toFixed(1) : 0}%</span>
                    </div>
                )}
                <button className="icon-btn tool-active" title="Run AI Team Orchestrator">
                    <PlayCircle size={16} />
                </button>
                <button className="icon-btn" title="Settings">
                    <Settings size={16} />
                </button>
            </div>
        </div>
    );
}
