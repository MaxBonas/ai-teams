import { useEffect, useState } from 'react';
import { Folder, File, ChevronRight, ChevronDown } from 'lucide-react';
import { apiFetch } from '../lib/api';

interface FileNode {
    name: string;
    path: string;
    type: 'file' | 'directory';
    children?: FileNode[];
}

interface FileExplorerProps {
    activeFile: string | null;
    onFileSelect: (path: string) => void;
    workspacePath: string;
    refreshToken?: number;
    onRefreshStateChange?: (refreshing: boolean) => void;
}

export default function FileExplorer({
    activeFile,
    onFileSelect,
    workspacePath,
    refreshToken = 0,
    onRefreshStateChange,
}: FileExplorerProps) {
    const [tree, setTree] = useState<FileNode | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError('');
        onRefreshStateChange?.(true);

        apiFetch('/api/fs/tree')
            .then(res => res.json())
            .then(data => {
                if (cancelled) {
                    return;
                }
                setTree(data);
            })
            .catch(err => {
                if (cancelled) {
                    return;
                }
                console.error(err);
                setError('Failed to load files. Try refresh.');
            })
            .finally(() => {
                if (cancelled) {
                    return;
                }
                setLoading(false);
                onRefreshStateChange?.(false);
            });

        return () => {
            cancelled = true;
            onRefreshStateChange?.(false);
        };
    }, [workspacePath, refreshToken, onRefreshStateChange]);

    if (loading && !tree) {
        return <div style={{ padding: 16, color: 'var(--text-secondary)' }}>Loading workspace...</div>;
    }

    if (error && !tree) {
        return <div style={{ padding: 16, color: '#ff8d8d' }}>{error}</div>;
    }

    if (!tree) {
        return <div style={{ padding: 16, color: 'var(--text-secondary)' }}>No files found.</div>;
    }

    return (
        <div className="file-tree">
            <TreeNode node={tree} activeFile={activeFile} onFileSelect={onFileSelect} defaultOpen />
        </div>
    );
}

interface TreeNodeProps {
    node: FileNode;
    activeFile: string | null;
    onFileSelect: (path: string) => void;
    defaultOpen?: boolean;
}

function TreeNode({ node, activeFile, onFileSelect, defaultOpen = false }: TreeNodeProps) {
    const [isOpen, setIsOpen] = useState(defaultOpen);
    const isDir = node.type === 'directory';

    const handleClick = () => {
        if (isDir) {
            setIsOpen(!isOpen);
        } else {
            onFileSelect(node.path);
        }
    };

    const getSlashes = (path: string) => (path.match(/[\\/]/g) || []).length;

    return (
        <div>
            <div
                className={`file-item ${isDir ? 'dir' : ''} ${activeFile === node.path ? 'active' : ''}`}
                onClick={handleClick}
                style={{ paddingLeft: `${16 + (getSlashes(node.path) * 12)}px` }}
            >
                {isDir ? (
                    isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />
                ) : (
                    <File size={14} />
                )}
                {isDir && !isOpen ? <Folder size={14} /> : null}
                <span style={{ marginLeft: 4 }}>{node.name}</span>
            </div>
            {isDir && isOpen && node.children && (
                <div>
                    {node.children.map((child: any) => (
                        <TreeNode key={child.path} node={child} activeFile={activeFile} onFileSelect={onFileSelect} />
                    ))}
                </div>
            )}
        </div>
    );
}
