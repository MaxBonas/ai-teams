import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ProjectHygieneCard } from './ProjectHygieneCard';
import type { ProjectHygiene } from './types';

const cleanHygiene: ProjectHygiene = {
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
    direct_directories: 3,
    aiteam_projects: 2,
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
};

describe('ProjectHygieneCard', () => {
  it('muestra una observación vigente y las garantías de seguridad', () => {
    render(
      <ProjectHygieneCard
        root="C:\\Projects"
        previewRoot="C:\\Projects"
        hygiene={cleanHygiene}
        busy={false}
        onInspect={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Raíz limpia');
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText(/no mueve ni borra carpetas/i)).toBeInTheDocument();
    expect(screen.getByText(/se consideran personales/i)).toBeInTheDocument();
  });

  it('no reutiliza una observación de otra ruta', () => {
    render(
      <ProjectHygieneCard
        root="D:\\New"
        previewRoot="C:\\Old"
        hygiene={cleanHygiene}
        busy={false}
        onInspect={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Sin comprobar');
    expect(screen.queryByText('Raíz limpia')).not.toBeInTheDocument();
  });

  it('permite ejecutar la inspección explícita sin guardar', async () => {
    const onInspect = vi.fn();
    const user = userEvent.setup();
    render(
      <ProjectHygieneCard
        root="C:\\Projects"
        previewRoot=""
        hygiene={null}
        busy={false}
        onInspect={onInspect}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Comprobar sin guardar' }));
    expect(onInspect).toHaveBeenCalledOnce();
  });
});
