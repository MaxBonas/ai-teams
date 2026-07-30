import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { apiFetch } from '../../lib/api';
import { ModelRoleSelector } from './ModelRoleSelector';

vi.mock('../../lib/api', () => ({ apiFetch: vi.fn() }));

describe('ModelRoleSelector', () => {
  it('mantiene un modelo archivado visible pero deshabilitado en Equipo', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify({
      default: { candidate_id: null, action: 'require_owner' },
      candidates: [
        {
          candidate_id: 'candidate:archived',
          label: 'Modelo archivado',
          rank: 1,
          identity: {
            profile_id: 'profile-a',
            provider_org: 'provider-a',
            channel: 'subscription',
            model_id: 'model-a',
          },
          selection_score: {
            score: 91,
            auto_eligible: false,
            auto_ineligible_reasons: ['owner_archived'],
          },
          owner_selectable: false,
          owner_preference: {
            state: 'archived',
            reason: 'archivado por el owner',
          },
          disabled_reason: 'Archivado por el owner',
        },
        {
          candidate_id: 'candidate:active',
          label: 'Modelo activo',
          rank: 2,
          identity: {
            profile_id: 'profile-b',
            provider_org: 'provider-b',
            channel: 'api',
            model_id: 'model-b',
          },
          selection_score: { score: 80, auto_eligible: false },
          owner_selectable: true,
          owner_preference: { state: 'normal', reason: 'default_normal' },
        },
      ],
    }), { status: 200 }));
    const onChange = vi.fn();
    const user = userEvent.setup();

    render(
      <ModelRoleSelector
        role="reviewer"
        profileId=""
        model=""
        onChange={onChange}
      />,
    );

    const archived = await screen.findByRole('option', {
      name: /Modelo archivado.*archivado por el owner/i,
    });
    const active = screen.getByRole('option', { name: /Modelo activo/i });
    const selector = screen.getByTestId('model-role-selector');

    expect(archived).toBeDisabled();
    expect(active).toBeEnabled();
    await user.selectOptions(selector, active);
    expect(onChange).toHaveBeenCalledWith({
      profileId: 'profile-b',
      model: 'model-b',
      candidateId: 'candidate:active',
    });
  });

  it('no convierte un score máximo en autoridad seleccionable', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify({
      default: { candidate_id: null, action: 'require_owner' },
      candidates: [
        {
          candidate_id: 'candidate:quorum-only',
          label: 'Auditor excelente',
          rank: 1,
          identity: {
            profile_id: 'profile-a',
            provider_org: 'provider-a',
            channel: 'subscription',
            model_id: 'model-a',
          },
          tier1_authority: {
            policy_version: 'tier_role_coverage_v1',
            lane: 'quorum_ready',
            enabled: true,
          },
          tier1_authority_gate: {
            allowed: false,
            code: 'tier1_authority_lane_mismatch',
          },
          selection_score: {
            score: 100,
            auto_eligible: false,
            auto_ineligible_reasons: ['tier1_authority_lane_mismatch'],
          },
          owner_selectable: false,
          disabled_reason: 'Lead exige lead_ready.',
        },
      ],
    }), { status: 200 }));

    render(
      <ModelRoleSelector
        role="lead"
        profileId=""
        model=""
        onChange={vi.fn()}
      />,
    );

    const candidate = await screen.findByRole('option', {
      name: /Auditor excelente.*bloqueado.*Lead exige lead_ready/i,
    });
    expect(candidate).toBeDisabled();
    expect(screen.getByTestId('model-role-no-default')).toBeInTheDocument();
  });
});
