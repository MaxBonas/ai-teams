import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import {
  GuidedSetupCoverage,
  type GuidedSetupCoveragePayload,
} from './GuidedSetupCoverage';

const candidate = {
  candidate_id: 'codex:sol:lead',
  profile_id: 'codex_subscription',
  model_id: 'gpt-5.6-sol',
  provider: 'OpenAI',
  channel: 'subscription',
  tier: 1,
  rank: 1,
  score: 0,
  selection_reason: 'Calibración exacta y adapter verde.',
  coverage_eligible: true,
  owner_selectable: true,
  exclusion_reasons: [],
  perspective_key: 'openai',
  capacity_pool: 'codex-subscription',
  economics: {
    class: 'zero_marginal',
    marginal_cost: 'zero',
    price_note: 'Suscripción',
  },
  privacy: { allowed: true, code: 'compatible' },
  capabilities: ['reasoning', 'structured_output'],
  gates: { configured: true, adapter_green: true, calibrated: true },
};

function fixture(): GuidedSetupCoveragePayload {
  return {
    coverage: {
      schema_version: 'guided_setup_coverage_v1',
      recommended_profile: 'lead_quorum',
      recommended_profile_ready: false,
      profiles: {
        solo_lead: {
          profile: 'solo_lead',
          ready: true,
          status: 'covered',
          requirements: [],
          blockers: [],
        },
        lead_quorum: {
          profile: 'lead_quorum',
          ready: false,
          status: 'blocked',
          requirements: [],
          blockers: ['quorum_auditor:diversity_gap'],
        },
        full_team: {
          profile: 'full_team',
          ready: false,
          status: 'blocked',
          requirements: [],
          blockers: ['engineer:missing'],
        },
      },
      roles: {
        team_lead: {
          role: 'team_lead',
          candidate_count: 1,
          eligible_count: 1,
          excluded_count: 0,
          status: 'covered',
          candidates: [candidate],
          excluded_candidates: [],
        },
        quorum_auditor: {
          role: 'quorum_auditor',
          candidate_count: 2,
          eligible_count: 1,
          excluded_count: 1,
          status: 'covered',
          candidates: [{ ...candidate, candidate_id: 'codex:sol:quorum' }],
          excluded_candidates: [{
            ...candidate,
            candidate_id: 'antigravity:gemini:quorum',
            profile_id: 'antigravity_subscription',
            model_id: 'gemini-3.1-pro-high',
            coverage_eligible: false,
            owner_selectable: true,
            exclusion_reasons: ['adapter_not_prepared_in_setup'],
          }],
        },
        engineer: {
          role: 'engineer',
          candidate_count: 2,
          eligible_count: 0,
          excluded_count: 2,
          status: 'no_eligible',
          candidates: [],
          excluded_candidates: [{
            ...candidate,
            candidate_id: 'codex:terra:engineer',
            model_id: 'gpt-5.6-terra',
            coverage_eligible: false,
            owner_selectable: false,
            disabled_reason: 'Calibración de Engineer no vigente.',
            exclusion_reasons: ['calibration_stale'],
          }],
        },
        reviewer: {
          role: 'reviewer',
          candidate_count: 1,
          eligible_count: 1,
          excluded_count: 0,
          status: 'covered',
          candidates: [{ ...candidate, candidate_id: 'codex:terra:reviewer', model_id: 'gpt-5.6-terra', score: 88 }],
          excluded_candidates: [],
        },
        worker: {
          role: 'worker',
          candidate_count: 0,
          eligible_count: 0,
          excluded_count: 0,
          status: 'no_eligible',
          candidates: [],
          excluded_candidates: [],
        },
      },
    },
    recommendations: {
      schema_version: 'guided_setup_recommendations_v1',
      recommended_profile: 'lead_quorum',
      ready_to_continue: false,
      phases: [],
      next_action: {
        code: 'expand_quorum_diversity',
        phase: 'lead_quorum',
        priority: 20,
        required: true,
        reason: 'Añadir quorum independiente',
        gaps: [{
          role: 'quorum_auditor',
          status: 'diversity_gap',
          missing_count: 0,
          perspective_count: 1,
          capacity_pool_count: 1,
        }],
      },
    },
    preparation: {
      schema_version: 'guided_setup_preparation_v1',
      ready: true,
      blockers: [],
      ready_adapter_ids: ['codex_subscription'],
    },
    selection_context: {
      source: 'contextual_model_selection',
      catalog_content_hash: '0123456789abcdef',
      run_profile: 'lead_quorum',
      criticality: 'high',
      data_class: 'internal',
      required_capabilities: [],
    },
  };
}

describe('GuidedSetupCoverage', () => {
  it('muestra perfiles, siguiente acción y huecos sin recalcularlos', () => {
    render(<GuidedSetupCoverage payload={fixture()} />);

    expect(screen.getByRole('heading', { name: 'Cobertura operativa' })).toBeInTheDocument();
    expect(screen.getByLabelText('Lead esencial: preparado')).toBeInTheDocument();
    expect(screen.getByLabelText('Lead + quorum: bloqueado')).toBeInTheDocument();
    expect(screen.getByText('Añadir una segunda perspectiva al quorum')).toBeInTheDocument();
    expect(screen.getByText(/Auditor quorum: Diversity gap/)).toBeInTheDocument();
    expect(screen.getAllByText(/El selector no encuentra un par modelo \+ adapter/)).toHaveLength(2);
    expect(screen.getByText('gemini-3.1-pro-high')).toBeInTheDocument();
    expect(screen.getAllByText('gpt-5.6-terra')).toHaveLength(2);
  });

  it('distingue score cero de dato ausente y abre evidencia accesible', async () => {
    const user = userEvent.setup();
    render(<GuidedSetupCoverage payload={fixture()} />);

    const score = screen.getAllByLabelText('Puntuación 0')[0];
    expect(score).toHaveTextContent('0');
    const leadModel = screen.getAllByText('gpt-5.6-sol')[0];
    await user.click(leadModel);
    expect(screen.getAllByText('Privacidad')[0]).toBeVisible();
    expect(screen.getAllByText('3/3')[0]).toBeVisible();
    expect(screen.getAllByText(/coste marginal 0/i)[0]).toBeInTheDocument();
  });
});
