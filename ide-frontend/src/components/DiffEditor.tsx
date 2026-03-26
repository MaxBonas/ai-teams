import { DiffEditor as MonacoDiffEditor } from '@monaco-editor/react';

interface DiffEditorProps {
    originalContent: string;
    modifiedContent: string;
    filePath: string;
}

export default function DiffEditor({ originalContent, modifiedContent, filePath }: DiffEditorProps) {
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
        <div style={{ height: '100%', width: '100%' }}>
            <MonacoDiffEditor
                height="100%"
                theme="vs-dark"
                language={getLanguage(filePath)}
                original={originalContent}
                modified={modifiedContent}
                options={{
                    minimap: { enabled: false },
                    fontSize: 14,
                    wordWrap: 'on',
                    padding: { top: 16 },
                    scrollBeyondLastLine: false,
                    readOnly: true,
                    renderSideBySide: true,
                }}
            />
        </div>
    );
}
