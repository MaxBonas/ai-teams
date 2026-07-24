interface InfoTipProps {
  tip: string;
  wide?: boolean;
}

export function InfoTip({ tip, wide }: InfoTipProps) {
  return (
    <span className={`info-tip${wide ? ' info-tip-wide' : ''}`} tabIndex={0}>
      <svg className="info-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3"/>
        <path d="M8 7.5v3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        <circle cx="8" cy="5.5" r="0.75" fill="currentColor"/>
      </svg>
      <span className="info-tooltip" role="tooltip">{tip}</span>
    </span>
  );
}
import './InfoTip.css';
