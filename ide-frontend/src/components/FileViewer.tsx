import { useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

interface FileViewerProps {
  filePath: string;
  workspacePath: string;
}

export default function FileViewer({ filePath, workspacePath }: FileViewerProps) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await apiFetch(`/api/fs/file?path=${encodeURIComponent(filePath)}`, {
          headers: workspacePath ? { 'x-workspace-path': workspacePath } : {},
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = (await response.json()) as { content?: string };
        if (!cancelled) {
          setContent(typeof data.content === 'string' ? data.content : '');
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'No se pudo leer el archivo.');
          setContent('');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [filePath, workspacePath]);

  return (
    <div className="file-viewer-shell">
      <div className="file-viewer-header">
        <code>{filePath}</code>
      </div>
      <div className="file-viewer-body">
        {loading ? (
          <div className="status-empty-hint">Cargando contenido real...</div>
        ) : error ? (
          <div className="workbench-error">No se pudo abrir `{filePath}`: {error}</div>
        ) : (
          <pre className="file-viewer-pre">{content}</pre>
        )}
      </div>
    </div>
  );
}
