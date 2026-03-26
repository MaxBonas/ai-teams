import { useState, useEffect } from 'react';
import { AlertTriangle, Activity, Database, RefreshCcw, Zap } from 'lucide-react';
import { apiFetch } from '../lib/api';
import { useIdeStore } from '../store';
import type { DashboardData } from '../types/dashboard';

interface AIDashboardProps {
    workspacePath: string;
}

export default function AIDashboard({ workspacePath }: AIDashboardProps) {
    const { dashboardData: data, setDashboardData: setData } = useIdeStore();
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [errorMsg, setErrorMsg] = useState('');

    const load = async (quiet = false) => {
        if (!quiet) {
            setRefreshing(true);
        }
        try {
            const res = await apiFetch('/api/dashboard');
            const json: DashboardData = await res.json();
            if (json.error) {
                setErrorMsg(json.error);
                setData(null);
            } else {
                setErrorMsg('');
                setData(json);
            }
        } catch (err) {
            console.error(err);
            setErrorMsg('Connection error fetching dashboard data. Ensure API backend is running on :8000.');
            setData(null);
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    useEffect(() => {
        setData(null);
        setLoading(true);
        setErrorMsg('');
        void load(true);
        const timer = window.setInterval(() => {
            void load(true);
        }, 5000);
        return () => window.clearInterval(timer);
    }, [workspacePath]);

    const memoryEntries = Object.entries(data?.memory_counts || {}).sort((a, b) => b[1] - a[1]);

    if (loading) return <div className="dashboard-loading">Loading AI metrics...</div>;
    if (errorMsg) {
        return (
            <div className="dashboard-error-stack">
                <div className="dashboard-error"><AlertTriangle /> {errorMsg}</div>
                <button className="dash-refresh" onClick={() => void load()}>
                    <RefreshCcw size={14} className={refreshing ? 'spin' : ''} /> Retry
                </button>
            </div>
        );
    }
    if (!data) return <div className="dashboard-error">No data available.</div>;

    return (
        <div className="ai-dashboard">
            <div className="dash-header">
                <h2>AI Team Overview</h2>
                <div className="dash-header-actions">
                    <span className="live-badge"><Zap size={12} fill="currentColor" /> Live</span>
                    <button className="dash-refresh" onClick={() => void load()}>
                        <RefreshCcw size={14} className={refreshing ? 'spin' : ''} /> Refresh
                    </button>
                </div>
            </div>

            <div className="dash-grid">
                <div className="stat-card">
                    <div className="stat-label">Total Tasks</div>
                    <div className="stat-value">{data.task_total || 0}</div>
                </div>

                <div className="stat-card">
                    <div className="stat-label">Execution Success</div>
                    <div className="stat-value">{data.summary?.task_execution_success_rate !== undefined ? `${data.summary.task_execution_success_rate}%` : '--'}</div>
                </div>

                <div className="stat-card">
                    <div className="stat-label">Daily API Spend</div>
                    <div className="stat-value">
                        {data.budget?.daily_api_spend_usd !== undefined ? `$${data.budget.daily_api_spend_usd.toFixed(2)}` : '--'}
                        {data.budget?.daily_api_budget_usd !== undefined && ` / $${data.budget.daily_api_budget_usd}`}
                    </div>
                </div>

                <div className="stat-card">
                    <div className="stat-label">Pro Model Share</div>
                    <div className="stat-value">{data.pilot_metrics?.pro_share_percent !== undefined ? `${data.pilot_metrics.pro_share_percent}%` : '--'}</div>
                </div>
            </div>

            <div className="dash-split">
                <div className="dash-panel">
                    <h3><Activity size={16} /> Task States</h3>
                    {data.task_state_counts && Object.keys(data.task_state_counts).length > 0 ? (
                        <ul className="dash-list">
                            {Object.entries(data.task_state_counts).map(([state, count]) => (
                                <li key={state}><span>{state}</span> <strong>{count}</strong></li>
                            ))}
                        </ul>
                    ) : <p className="empty-text">No active states</p>}
                </div>

                <div className="dash-panel">
                    <h3><Database size={16} /> Agent Memory Entries</h3>
                    <ul className="dash-list">
                        {memoryEntries.length === 0 ? (
                            <li><span>none</span> <strong>0</strong></li>
                        ) : (
                            memoryEntries.slice(0, 6).map(([agent, count]) => (
                                <li key={agent}><span>{agent}</span> <strong>{count}</strong></li>
                            ))
                        )}
                    </ul>
                </div>
            </div>

            {data.summary?.alerts && data.summary.alerts.length > 0 && (
                <div className="dash-alerts">
                    <h3><AlertTriangle size={16} /> Active Alerts</h3>
                    <ul>
                        {data.summary.alerts.map((alert, i) => (
                            <li key={i}>{alert}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
