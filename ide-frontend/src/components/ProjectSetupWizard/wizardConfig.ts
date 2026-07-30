import {
  FolderPlus,
  Radar,
  ShieldCheck,
  UsersRound,
} from 'lucide-react';

export const PROJECT_SETUP_STEPS = [
  { label: 'Proyecto', icon: FolderPlus, headingId: 'project-step-project-title' },
  { label: 'Objetivo', icon: Radar, headingId: 'project-step-objective-title' },
  { label: 'Equipo', icon: UsersRound, headingId: 'project-step-resources-title' },
  { label: 'Revisión', icon: ShieldCheck, headingId: 'project-review-title' },
] as const;

export const PROJECT_PROFILE_OPTIONS = [
  {
    value: 'solo_lead',
    label: 'Solo Lead',
    detail: 'Trabajo acotado.',
  },
  {
    value: 'lead_quorum',
    label: 'Lead + quorum',
    detail: 'Plan con auditoría independiente.',
  },
  {
    value: 'full_team',
    label: 'Equipo completo',
    detail: 'Ejecución y revisión separadas.',
  },
] as const;
