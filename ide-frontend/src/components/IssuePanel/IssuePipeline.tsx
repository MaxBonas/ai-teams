import { statusLabel } from '../../lib/format';
import type { Issue, IssuePhase } from '../../types/cockpit';

const ISSUE_PHASES: Array<{ key: IssuePhase; short: string; label: string }> = [
  { key: 'planning', short: 'Plan', label: 'Planificación' },
  { key: 'engineer', short: 'Dev', label: 'Ingeniería' },
  { key: 'tests', short: 'Test', label: 'Pruebas' },
  { key: 'review', short: 'Rev', label: 'Revisión' },
  { key: 'gate', short: 'Gate', label: 'Gate' },
  { key: 'done', short: 'Done', label: 'Finalizada' },
];

export function IssuePipeline({ issue }: { issue: Issue }) {
  if (!issue.phase) return null;
  const currentIndex = ISSUE_PHASES.findIndex((phase) => phase.key === issue.phase);
  const actor = issue.active_agent?.name || issue.active_run?.agent_id || '';
  return (
    <div className="issue-pipeline" aria-label={`Fase actual: ${ISSUE_PHASES[currentIndex]?.label || issue.phase}`}>
      <div className="issue-pipeline-track">
        {ISSUE_PHASES.map((phase, index) => (
          <span
            key={phase.key}
            className={`issue-pipeline-step${index < currentIndex ? ' complete' : ''}${index === currentIndex ? ' current' : ''}`}
            title={phase.label}
          >
            <i />
            <small>{phase.short}</small>
          </span>
        ))}
      </div>
      {actor && issue.phase !== 'done' ? (
        <span className="issue-pipeline-actor" title={issue.active_run?.model || undefined}>
          {actor}{issue.active_run?.status ? ` · ${statusLabel(issue.active_run.status)}` : ''}
        </span>
      ) : null}
    </div>
  );
}
