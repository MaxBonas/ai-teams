export type OwnerPreferenceState = 'high' | 'normal' | 'low' | 'archived';

export interface PreferenceCandidate {
  identity: { profile_id: string; model_id: string };
  owner_preference?: { state: OwnerPreferenceState; reason: string };
}

export const PREFERENCE_LABELS: Record<OwnerPreferenceState, string> = {
  high: 'Prioridad alta',
  normal: 'Normal',
  low: 'Prioridad baja',
  archived: 'Archivado',
};
