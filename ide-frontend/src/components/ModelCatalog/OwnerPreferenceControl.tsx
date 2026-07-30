import { useState } from 'react';
import { Archive, RotateCcw } from 'lucide-react';

import {
  PREFERENCE_LABELS,
  type OwnerPreferenceState,
  type PreferenceCandidate,
} from './preferenceTypes';

interface Props {
  candidate: PreferenceCandidate;
  onChange: (
    candidate: PreferenceCandidate,
    state: OwnerPreferenceState,
    reason: string,
  ) => Promise<void>;
}

export function OwnerPreferenceControl({ candidate, onChange }: Props) {
  const storedReason = candidate.owner_preference?.reason;
  const [reason, setReason] = useState(storedReason === 'default_normal' ? '' : storedReason || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const save = async (state: OwnerPreferenceState) => {
    const cleanReason = reason.trim();
    if (!cleanReason) {
      setError('Registra una razón para que el cambio sea auditable.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onChange(candidate, state, cleanReason);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'No se pudo guardar.');
    } finally {
      setSaving(false);
    }
  };

  const current = candidate.owner_preference?.state || 'normal';
  return (
    <section className="detail-section owner-preference-console">
      <div className="detail-section-title"><Archive size={15} /><h3>Preferencia del owner</h3></div>
      <div className="preference-current">
        <span className={`preference-badge preference-${current}`}>{PREFERENCE_LABELS[current]}</span>
        <p>Ordena trabajo y selección; nunca mejora la nota ni salta health, calibración o compatibilidad.</p>
      </div>
      <label>
        <span>Razón auditable</span>
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Por qué priorizar, bajar, archivar o reactivar…"
          maxLength={1000}
          disabled={saving}
        />
      </label>
      <div className="preference-actions">
        {(Object.keys(PREFERENCE_LABELS) as OwnerPreferenceState[]).map((state) => (
          <button
            key={state}
            type="button"
            className={`preference-action preference-${state}`}
            disabled={saving}
            onClick={() => { void save(state); }}
          >
            {state === 'archived' ? <Archive size={13} /> : state === 'normal' ? <RotateCcw size={13} /> : null}
            {state === 'normal' && current === 'archived' ? 'Reactivar' : PREFERENCE_LABELS[state]}
          </button>
        ))}
      </div>
      {error ? <p className="preference-error" role="alert">{error}</p> : null}
    </section>
  );
}
