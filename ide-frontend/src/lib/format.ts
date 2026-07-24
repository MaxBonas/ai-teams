const STATUS_LABELS: Record<string, string> = {
  accepted: 'aceptado',
  blocked: 'bloqueado',
  cancelled: 'cancelado',
  completed: 'completado',
  degraded: 'degradado',
  done: 'done',
  failed: 'fallido',
  in_progress: 'en progreso',
  pending: 'pendiente',
  queued: 'en cola',
  rejected: 'rechazado',
  running: 'ejecutando',
  skipped: 'sin trabajo',
  todo: 'todo',
};

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status.replaceAll('_', ' ');
}

export function parseTime(value?: string | null): number {
  if (!value) return 0;
  let iso = value.includes('T') ? value : value.replace(' ', 'T');
  // SQLite CURRENT_TIMESTAMP no incluye offset; se interpreta siempre como UTC.
  if (iso.includes('T') && !/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)) iso += 'Z';
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function formatTime(value?: string | null): string {
  const parsed = parseTime(value);
  if (!parsed) return '-';
  return new Date(parsed).toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}
