import { apiFetch } from '../../lib/api';

export class ProjectSetupError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ProjectSetupError';
    this.status = status;
  }
}

function detailText(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value) return value;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if (typeof record.reason === 'string') return record.reason;
    if (typeof record.code === 'string') return record.code;
  }
  return fallback;
}

export async function projectSetupRequest<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const response = await apiFetch(path, init);
  const payload = await response.json() as T & { detail?: unknown };
  if (!response.ok) {
    throw new ProjectSetupError(
      detailText(payload.detail, `${response.status}:${path}`),
      response.status,
    );
  }
  return payload;
}
