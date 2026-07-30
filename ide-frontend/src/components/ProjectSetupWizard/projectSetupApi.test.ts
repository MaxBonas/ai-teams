import { afterEach, describe, expect, it, vi } from 'vitest';
import { projectSetupRequest } from './projectSetupApi';

function rejectedResponse(status: number, detail: unknown): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify({ detail }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }));
}

describe('projectSetupRequest', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    [409, { reason: 'stale_preflight' }, 'stale_preflight'],
    [429, { code: 'provider_quota_exhausted' }, 'provider_quota_exhausted'],
  ])('preserves status %i and its actionable detail', async (status, detail, message) => {
    vi.stubGlobal('fetch', vi.fn(() => rejectedResponse(status, detail)));

    const request = projectSetupRequest('/api/guided-setup/project-preflight-execute', {
      method: 'POST',
    });

    await expect(request).rejects.toMatchObject({
      name: 'ProjectSetupError',
      status,
      message,
    });
  });

  it('preserves an offline transport failure without inventing a server status', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))));

    await expect(projectSetupRequest('/api/guided-setup/project-preflight', {
      method: 'POST',
    })).rejects.toThrow('Failed to fetch');
  });
});
