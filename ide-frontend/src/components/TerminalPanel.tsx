import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { getWsBase } from '../lib/api';
import '@xterm/xterm/css/xterm.css';

interface TerminalPanelProps {
    workspacePath: string;
}

export default function TerminalPanel({ workspacePath }: TerminalPanelProps) {
    const terminalRef = useRef<HTMLDivElement>(null);
    const xtermRef = useRef<Terminal | null>(null);
    const wsRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        if (!terminalRef.current) return;

        const terminal = new Terminal({
            theme: { background: '#000000', foreground: '#e6edf3' },
            fontFamily: 'Consolas, "Courier New", monospace',
            fontSize: 13,
            cursorBlink: true,
        });

        const fitAddon = new FitAddon();
        terminal.loadAddon(fitAddon);
        terminal.open(terminalRef.current);

        setTimeout(() => fitAddon.fit(), 50);
        xtermRef.current = terminal;

        let reconnectAttempts = 0;
        let reconnectTimerId: ReturnType<typeof setTimeout> | null = null;
        let isDisposed = false;

        const connectWebSocket = () => {
            if (isDisposed) return;
            const apiKey = (() => {
                try {
                    return window.localStorage.getItem('AITEAM_API_KEY') || '';
                } catch {
                    return '';
                }
            })();
            const params = new URLSearchParams();
            if (apiKey) params.set('api_key', apiKey);
            if (workspacePath) params.set('workspace_path', workspacePath);

            const qs = params.toString();
            const wsBase = getWsBase();
            const wsUrl = qs ? `${wsBase}/api/terminal?${qs}` : `${wsBase}/api/terminal`;
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                if (isDisposed) {
                    ws.close();
                    return;
                }
                reconnectAttempts = 0;
                terminal.write(`\r\n\x1b[32m[Terminal connected]\x1b[0m\r\n`);
                ws.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
            };

            ws.onmessage = (event) => {
                if (!isDisposed) terminal.write(event.data);
            };

            ws.onerror = () => {
                if (!isDisposed) terminal.write('\r\n\x1b[31m[Terminal: connection error]\x1b[0m\r\n');
            };

            ws.onclose = () => {
                if (isDisposed) return;

                const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 10000);
                terminal.write(`\r\n\x1b[33m[Terminal disconnected. Reconnecting in ${delay / 1000}s...]\x1b[0m\r\n`);

                reconnectTimerId = setTimeout(() => {
                    reconnectAttempts++;
                    connectWebSocket();
                }, delay);
            };

            wsRef.current = ws;

            terminal.onData((data) => {
                if (ws.readyState === WebSocket.OPEN && !isDisposed) {
                    ws.send(data);
                }
            });
        };

        connectWebSocket();

        const handleResize = () => {
            fitAddon.fit();
            if (wsRef.current?.readyState === WebSocket.OPEN && !isDisposed) {
                wsRef.current.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }));
            }
        };
        window.addEventListener('resize', handleResize);

        return () => {
            isDisposed = true;
            if (reconnectTimerId) clearTimeout(reconnectTimerId);
            window.removeEventListener('resize', handleResize);
            if (wsRef.current) wsRef.current.close();
            terminal.dispose();
        };
    }, [workspacePath]);

    return <div ref={terminalRef} style={{ width: '100%', height: '100%', overflow: 'hidden' }} />;
}
