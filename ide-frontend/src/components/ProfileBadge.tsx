import './ProfileBadge.css';

const PROFILE_BADGES: Record<string, { label: string; cls: string }> = {
  full_team: { label: 'Equipo completo', cls: 'team' },
  lead_quorum: { label: 'Lead + Quorum', cls: 'quorum' },
  solo_lead: { label: 'Solo Lead', cls: 'solo' },
};

export function ProfileBadge({ profile, compact }: { profile: string | null; compact?: boolean }) {
  if (!profile) return null;
  const badge = PROFILE_BADGES[profile];
  if (!badge) return null;
  return (
    <span className={`profile-badge profile-${badge.cls}${compact ? ' profile-badge-compact' : ''}`}>
      {badge.label}
    </span>
  );
}
