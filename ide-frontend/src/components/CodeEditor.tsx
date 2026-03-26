import { useEffect, useState, useRef, useCallback } from 'react';
import Editor from '@monaco-editor/react';
import { apiFetch } from '../lib/api';

interface CodeEditorProps {
    filePath: string;
}

export default function CodeEditor({ filePath }: CodeEditorProps) {
    const [content, setContent] = useState<string>('');
    const [loading, setLoading] = useState(false);
    const [saved, setSaved] = useState(true);
    const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        setLoading(true);
        apiFetch(`/api/fs/file?path=${encodeURIComponent(filePath)}`)
            .then(res => res.json())
            .then(data => {
                if (data.content !== undefined) {
                    setContent(data.content);
                    setSaved(true);
                } else {
                    setContent('// Error loading file: ' + data.error);
                }
            })
            .finally(() => setLoading(false));
    }, [filePath]);

    const handleSave = useCallback((newContent: string) => {
        apiFetch(`/api/fs/file?path=${encodeURIComponent(filePath)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: newContent })
        }).then(res => {
            if (res.ok) {
                setSaved(true);
            } else {
                console.error("Auto-save failed: HTTP", res.status);
            }
        }).catch(err => {
            console.error("Auto-save failed:", err);
        });
    }, [filePath]);

    useEffect(() => {
        return () => {
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        };
    }, []);

    const getLanguage = (path: string) => {
        if (path.endsWith('.py')) return 'python';
        if (path.endsWith('.ts') || path.endsWith('.tsx')) return 'typescript';
        if (path.endsWith('.js') || path.endsWith('.jsx')) return 'javascript';
        if (path.endsWith('.json') || path.endsWith('.jsonl')) return 'json';
        if (path.endsWith('.md')) return 'markdown';
        if (path.endsWith('.css')) return 'css';
        if (path.endsWith('.html')) return 'html';
        return 'plaintext';
    };

    return (
        <div style={{ height: '100%', width: '100%', position: 'relative' }}>
            {!saved && (
                <div style={{ position: 'absolute', top: 8, right: 16, zIndex: 10, background: 'var(--accent)', color: '#fff', padding: '2px 8px', borderRadius: 12, fontSize: 11 }}>
                    Unsaved changes (Cmd+S to save)
                </div>
            )}
            {loading ? (
                <div style={{ padding: 16 }}>Loading {filePath}...</div>
            ) : (
                <Editor
                    height="100%"
                    theme="vs-dark"
                    path={filePath}
                    language={getLanguage(filePath)}
                    value={content}
                    onChange={(val) => {
                        const newContent = val || '';
                        setContent(newContent);
                        setSaved(false);

                        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
                        saveTimerRef.current = setTimeout(() => {
                            handleSave(newContent);
                        }, 1000);
                    }}
                    onMount={(editor, monaco) => {
                        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
                            const newContent = editor.getValue();
                            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
                            handleSave(newContent);
                        });
                    }}
                    options={{
                        minimap: { enabled: false },
                        fontSize: 14,
                        wordWrap: 'on',
                        padding: { top: 16 },
                        scrollBeyondLastLine: false,
                    }}
                />
            )}
        </div>
    );
}
