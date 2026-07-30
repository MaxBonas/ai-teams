import { LockKeyhole, ShieldCheck } from 'lucide-react';

export interface Tier1Authority {
  policy_version?: string;
  lane?: 'lead_ready' | 'quorum_ready' | 'tier1_support' | null;
  status?: 'enabled' | 'blocked' | 'not_applicable';
  enabled?: boolean | null;
  reason_code?: string;
  evaluated_at?: string | null;
  provider_version?: string | null;
  prompt_version?: string | null;
  stale_reasons?: string[];
  evidence_receipts?: string[];
  calibration_contract?: {
    version?: string;
    required_constructs?: string[];
  };
}

export interface Tier1Coverage {
  policy_version?: string;
  target_per_role?: number;
  roles?: Array<{
    canonical_role: string;
    lane: string;
    target: number;
    enabled_count: number;
    perspective_count: number;
    capacity_pool_count: number;
    status: string;
  }>;
}

const AUTHORITY_LABELS: Record<string, string> = {
  lead_ready: 'Lead-ready',
  quorum_ready: 'Quorum-ready',
  tier1_support: 'Soporte Tier 1',
};

function humanize(value?: string | null): string {
  if (!value) return 'No declarado';
  return value.replaceAll('_', ' ').replace(/^./, (char) => char.toUpperCase());
}

export function Tier1AuthorityCellMark({ authority }: { authority?: Tier1Authority }) {
  if (!authority?.lane) return null;
  return (
    <span
      className={`authority-cell-mark ${authority.enabled ? 'is-enabled' : 'is-blocked'}`}
      title={`${AUTHORITY_LABELS[authority.lane]}: ${humanize(authority.reason_code)}`}
      aria-label={`${AUTHORITY_LABELS[authority.lane]} ${authority.enabled ? 'habilitado' : 'bloqueado'}`}
    >
      {authority.enabled ? <ShieldCheck size={10} /> : <LockKeyhole size={10} />}
    </span>
  );
}

export function Tier1AuthorityBadgeStack({ authorities }: { authorities: Array<Tier1Authority | undefined> }) {
  const lanes = [...new Set(
    authorities
      .filter((authority) => authority?.enabled)
      .map((authority) => authority?.lane)
      .filter((lane): lane is NonNullable<Tier1Authority['lane']> => Boolean(lane)),
  )];
  return (
    <span className="authority-badge-stack">
      {lanes.map((lane) => (
        <span className={`authority-badge lane-${lane}`} key={lane}>
          {AUTHORITY_LABELS[lane]}
        </span>
      ))}
    </span>
  );
}

export function Tier1CoverageBoard({ coverage }: { coverage?: Tier1Coverage }) {
  const primary = (coverage?.roles || []).filter((row) => (
    row.canonical_role === 'lead' || row.canonical_role === 'quorum_auditor'
  ));
  return (
    <section className="authority-coverage-board" aria-label="Cobertura de autoridad Tier 1" data-testid="tier1-coverage">
      <div className="authority-coverage-intro">
        <span className="eyebrow"><ShieldCheck size={13} /> Autoridad Tier 1</span>
        <strong>La calidad abre la puerta; el contrato exacto entrega la llave.</strong>
        <small>{coverage?.policy_version || 'Política no observada'}</small>
      </div>
      {primary.map((row) => (
        <article className={`authority-coverage-card status-${row.status}`} key={row.canonical_role}>
          <div>
            <span>{AUTHORITY_LABELS[row.lane] || humanize(row.lane)}</span>
            <strong>{row.enabled_count}<i>/</i>{row.target}</strong>
          </div>
          <p>{humanize(row.status)}</p>
          <footer>
            <span>{row.perspective_count} perspectivas</span>
            <span>{row.capacity_pool_count} pools</span>
          </footer>
        </article>
      ))}
    </section>
  );
}

export function Tier1AuthorityDetail({ authority }: { authority?: Tier1Authority }) {
  if (!authority?.lane) return null;
  return (
    <section className={`authority-detail ${authority.enabled ? 'is-enabled' : 'is-blocked'}`} data-testid="tier1-authority-detail">
      <div className="authority-detail-icon">
        {authority.enabled ? <ShieldCheck size={19} /> : <LockKeyhole size={19} />}
      </div>
      <div>
        <span className="eyebrow">Gate de autoridad · independiente del score</span>
        <h3>{AUTHORITY_LABELS[authority.lane]}</h3>
        <p>{humanize(authority.reason_code)}</p>
      </div>
      <dl>
        <div><dt>Estado</dt><dd>{authority.enabled ? 'Habilitado' : 'Bloqueado'}</dd></div>
        <div><dt>Contrato</dt><dd>{authority.calibration_contract?.version || 'No declarado'}</dd></div>
        <div><dt>Versión</dt><dd>{authority.provider_version || 'No observada'}</dd></div>
      </dl>
      <div className="authority-constructs">
        {(authority.calibration_contract?.required_constructs || []).map((construct) => (
          <span key={construct}>{humanize(construct)}</span>
        ))}
      </div>
    </section>
  );
}
