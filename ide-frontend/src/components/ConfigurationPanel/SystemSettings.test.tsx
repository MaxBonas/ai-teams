import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SystemSettings } from './SystemSettings';
import type { ProjectHygiene } from './types';

const hygiene = {
  schema_version: 'project_hygiene_v1',
  scope: {
    read_only: true,
    paths_emitted: false,
    symlinks_followed: false,
    database_opened: false,
    git_invoked: false,
  },
  root: {
    configured: true,
    exists: true,
    reparse_point: false,
    fingerprint: 'a'.repeat(64),
  },
  status: 'clean',
  requires_attention: false,
  counts: {
    direct_directories: 0,
    aiteam_projects: 0,
    legacy_numbered: 0,
    legacy_tombstones: 0,
    staging_leftovers: 0,
    reparse_points: 0,
    scan_errors: 0,
  },
  legacy_families: [],
  ownership: {
    aiteam_identity_is_not_cleanup_authority: true,
    folders_without_aiteam_identity_are_personal_protected: true,
  },
  lifecycle: {
    automatic_cleanup_installed: false,
    startup_cleanup_installed: false,
    ttl_cleanup_installed: false,
    doctor_can_mutate: false,
  },
  recommended_action: {
    code: 'none',
    description: 'Sin acciones.',
    requires_human: false,
    mutates_state: false,
  },
} satisfies ProjectHygiene;

function renderSettings(previewRoot: string) {
  render(
    <SystemSettings
      draftRoot={'C:\\Projects'}
      effectiveRoot={'C:\\Old'}
      backendOrigin="http://127.0.0.1:8010"
      lastResult={null}
      busy={false}
      projectHygiene={hygiene}
      projectHygieneRoot={previewRoot}
      projectHygieneBusy={false}
      onDraftChange={vi.fn()}
      onInspectRoot={vi.fn()}
      onSave={vi.fn()}
    />,
  );
}

describe('SystemSettings', () => {
  it('bloquea Guardar cuando el preview pertenece a otra ruta', () => {
    renderSettings('C:\\Old');

    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled();
    expect(screen.getByText(/comprueba la ruta actual/i)).toBeInTheDocument();
  });

  it('habilita Guardar cuando ruta y preview coinciden', () => {
    renderSettings('C:\\Projects');

    expect(screen.getByRole('button', { name: 'Guardar' })).toBeEnabled();
  });
});
