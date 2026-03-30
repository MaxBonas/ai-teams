import { useState, useEffect } from 'react';
import { ExternalLink, FolderOpen, Pin, PinOff, PlusCircle, Settings2, X } from 'lucide-react';
import {
    apiFetch,
    getPinnedWorkspaces,
    getRecentWorkspaces,
    pushRecentWorkspace,
    removeRecentWorkspace,
    togglePinnedWorkspace,
} from '../lib/api';

interface WorkspaceSelectorProps {
    currentWorkspace: string;
    onWorkspaceChange: (newPath: string) => void;
}

export default function WorkspaceSelector({ currentWorkspace, onWorkspaceChange }: WorkspaceSelectorProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [inputPath, setInputPath] = useState('');
    const [recentWorkspaces, setRecentWorkspaces] = useState<string[]>([]);
    const [pinnedWorkspaces, setPinnedWorkspaces] = useState<string[]>([]);
    const [filterText, setFilterText] = useState('');
    const [newProjectName, setNewProjectName] = useState('');
    const [creatingProject, setCreatingProject] = useState(false);
    const [createError, setCreateError] = useState('');

    useEffect(() => {
        setInputPath(currentWorkspace);
        if (currentWorkspace) {
            setRecentWorkspaces(pushRecentWorkspace(currentWorkspace));
        } else {
            setRecentWorkspaces(getRecentWorkspaces());
        }
        setPinnedWorkspaces(getPinnedWorkspaces());
    }, [currentWorkspace]);

    const workspaceLabel = (path: string) => path.split(/[\\/]/).pop() || path;

    const openWorkspace = (path: string) => {
        const nextPath = path.trim();
        if (!nextPath) {
            return;
        }
        setCreateError('');
        apiFetch('/api/workspace', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: nextPath })
        })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    const workspace = String(data.workspace || nextPath);
                    onWorkspaceChange(workspace);
                    setRecentWorkspaces(pushRecentWorkspace(workspace));
                    setInputPath(workspace);
                    setPinnedWorkspaces(getPinnedWorkspaces());
                    setIsOpen(false);
                } else {
                    setCreateError(data.error || data.detail || 'Failed to open workspace');
                }
            })
            .catch(err => {
                console.error("Error setting workspace:", err);
                setCreateError(err instanceof Error ? err.message : 'Failed to reach backend');
            });
    };

    const openWorkspaceInNewWindow = (path: string) => {
        const targetPath = path.trim();
        if (!targetPath) {
            return;
        }
        const url = new URL(window.location.href);
        url.searchParams.set('workspace', targetPath);
        window.open(url.toString(), '_blank', 'noopener,noreferrer');
    };

    const handleTogglePinned = (path: string) => {
        setPinnedWorkspaces(togglePinnedWorkspace(path));
    };

    const handleRemoveRecent = (path: string) => {
        setRecentWorkspaces(removeRecentWorkspace(path));
    };

    const createNewProject = async () => {
        const name = newProjectName.trim();
        if (!name || creatingProject) return;
        setCreatingProject(true);
        setCreateError('');
        try {
            const response = await apiFetch('/api/projects/new', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name }),
            });
            const payload = await response.json() as { success?: boolean; workspace?: string; error?: string; detail?: string };
            if (payload.success && payload.workspace) {
                setNewProjectName('');
                openWorkspace(payload.workspace);
            } else {
                setCreateError(payload.error || payload.detail || `Error ${response.status}`);
            }
        } catch (error) {
            setCreateError(error instanceof Error ? error.message : 'Failed to reach backend');
        } finally {
            setCreatingProject(false);
        }
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!inputPath.trim()) return;
        openWorkspace(inputPath.trim());
    };

    const filter = filterText.trim().toLowerCase();
    const filteredPinned = pinnedWorkspaces
        .filter((path) => !filter || path.toLowerCase().includes(filter))
        .slice(0, 10);
    const filteredRecent = recentWorkspaces
        .filter((path) => !pinnedWorkspaces.includes(path))
        .filter((path) => !filter || path.toLowerCase().includes(filter))
        .slice(0, 10);

    return (
        <div className="workspace-selector">
            <div
                className="workspace-header"
                onClick={() => setIsOpen(!isOpen)}
                title={currentWorkspace}
            >
                <FolderOpen size={14} />
                <span className="workspace-name">
                    {currentWorkspace.split(/[/\\]/).pop() || 'Workspace'}
                </span>
                <Settings2 size={12} className="workspace-settings-icon" />
            </div>

            {isOpen && (
                <form className="workspace-dropdown" onSubmit={handleSubmit}>
                    <div className="workspace-dropdown-label">Change Environment Path</div>
                    <input
                        type="text"
                        value={inputPath}
                        onChange={(e) => setInputPath(e.target.value)}
                        className="workspace-input"
                        placeholder="e.g. C:\Projects\MyNewApp"
                        autoFocus
                    />
                    <button type="submit" className="workspace-submit">Open Workspace</button>

                    <div className="workspace-new-project-row">
                        <input
                            type="text"
                            value={newProjectName}
                            onChange={(e) => { setNewProjectName(e.target.value); setCreateError(''); }}
                            className="workspace-input"
                            placeholder="Proyecto Nuevo (en Antigravity Projects)"
                            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void createNewProject(); } }}
                        />
                        <button
                            type="button"
                            className="workspace-submit workspace-create-btn"
                            onClick={() => void createNewProject()}
                            disabled={creatingProject || !newProjectName.trim()}
                        >
                            <PlusCircle size={12} /> {creatingProject ? 'Creating...' : 'Proyecto Nuevo'}
                        </button>
                    </div>
                    {createError && (
                        <div className="workspace-error">{createError}</div>
                    )}

                    {(pinnedWorkspaces.length > 0 || recentWorkspaces.length > 0) && (
                        <div className="workspace-recent-wrap">
                            <input
                                type="text"
                                value={filterText}
                                onChange={(e) => setFilterText(e.target.value)}
                                className="workspace-input"
                                placeholder="Filter recent projects..."
                            />

                            {filteredPinned.length > 0 && (
                                <>
                                    <div className="workspace-dropdown-label">Pinned</div>
                                    <div className="workspace-recent-list">
                                        {filteredPinned.map((path) => (
                                            <div key={`pinned-${path}`} className="workspace-recent-row">
                                                <button
                                                    type="button"
                                                    className="workspace-recent-item workspace-recent-open"
                                                    onClick={() => openWorkspace(path)}
                                                    title={path}
                                                >
                                                    {workspaceLabel(path)}
                                                </button>
                                                <button
                                                    type="button"
                                                    className="workspace-recent-action"
                                                    onClick={() => handleTogglePinned(path)}
                                                    title="Unpin"
                                                >
                                                    <PinOff size={12} />
                                                </button>
                                                <button
                                                    type="button"
                                                    className="workspace-recent-action"
                                                    onClick={() => openWorkspaceInNewWindow(path)}
                                                    title="Open in new window"
                                                >
                                                    <ExternalLink size={12} />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )}

                            <div className="workspace-dropdown-label">Recent Projects</div>
                            <div className="workspace-recent-list">
                                {filteredRecent.map((path) => (
                                    <div key={path} className="workspace-recent-row">
                                        <button
                                            type="button"
                                            className="workspace-recent-item workspace-recent-open"
                                            onClick={() => openWorkspace(path)}
                                            title={path}
                                        >
                                            {workspaceLabel(path)}
                                        </button>
                                        <button
                                            type="button"
                                            className="workspace-recent-action"
                                            onClick={() => handleTogglePinned(path)}
                                            title="Pin"
                                        >
                                            <Pin size={12} />
                                        </button>
                                        <button
                                            type="button"
                                            className="workspace-recent-action"
                                            onClick={() => openWorkspaceInNewWindow(path)}
                                            title="Open in new window"
                                        >
                                            <ExternalLink size={12} />
                                        </button>
                                        <button
                                            type="button"
                                            className="workspace-recent-action workspace-recent-remove"
                                            onClick={() => handleRemoveRecent(path)}
                                            title="Remove from recent"
                                        >
                                            <X size={12} />
                                        </button>
                                    </div>
                                ))}
                                {filteredPinned.length === 0 && filteredRecent.length === 0 && (
                                    <div className="workspace-empty-note">No recent projects match the filter.</div>
                                )}
                            </div>
                        </div>
                    )}
                </form>
            )}
        </div>
    );
}
