import { CheckCircle2 } from 'lucide-react';

import type { QuorumPayload } from '../../hooks/useQuorum';
import { pretty, statusLabel } from '../../lib/format';
import './QuorumStepper.css';

interface QuorumStepperProps {
  quorum: QuorumPayload | null;
  loading: boolean;
  onCreateExecutionTask?: () => void;
}

export function QuorumStepper({
  quorum,
  loading,
  onCreateExecutionTask,
}: QuorumStepperProps) {
  if (loading) return <div className="quorum-stepper quorum-loading">Leyendo quorum…</div>;
  if (!quorum) return null;

  const { session, contributions, gate } = quorum;
  const skipped = session.status === 'skipped';
  const degraded = session.status === 'degraded';
  const synthesized = Boolean(session.final_plan_revision_id);
  const requestComplete = skipped || contributions.length > 0 || gate.ready;
  const auditComplete = skipped || gate.valid_contributions >= session.min_valid_contributions;
  const steps = [
    { label: 'Solicitud', detail: `${session.requested_contributions} aportes`, complete: requestComplete },
    { label: 'Auditorías', detail: `${gate.valid_contributions}/${session.min_valid_contributions} válidas`, complete: auditComplete },
    { label: 'Gate', detail: skipped ? 'omitido' : gate.ready ? 'superado' : `${gate.missing_valid} pendientes`, complete: skipped || gate.ready },
    { label: 'Síntesis', detail: synthesized ? 'plan aceptado' : skipped ? 'no requerida' : 'pendiente', complete: skipped || synthesized },
  ];

  return (
    <section className={`quorum-stepper${skipped ? ' skipped' : ''}${degraded ? ' degraded' : ''}`} aria-label="Estado del quorum de planificación">
      <div className="quorum-stepper-header">
        <div>
          <span className="quorum-eyebrow">Quorum de planificación</span>
          <strong>{skipped ? 'No requerido' : statusLabel(session.status)}</strong>
        </div>
        <span className={`quorum-gate-badge${gate.ready ? ' ready' : ''}`}>{gate.ready ? 'Gate listo' : 'Gate pendiente'}</span>
        {gate.reduced_quorum && <span className="quorum-gate-badge">Quorum reducido · 1 senior</span>}
      </div>
      <div className="quorum-steps">
        {steps.map((step, index) => (
          <div className={`quorum-step${step.complete ? ' complete' : ''}`} key={step.label}>
            <span className="quorum-step-node">{step.complete ? <CheckCircle2 size={15} /> : index + 1}</span>
            <div><strong>{step.label}</strong><small>{step.detail}</small></div>
          </div>
        ))}
      </div>
      {(skipped || degraded) && <p className="quorum-skip-reason">{session.skipped_reason || 'El perfil de esta issue no requiere quorum.'}</p>}
      {session.status === 'accepted' && session.final_plan_revision_id && onCreateExecutionTask && (
        <div className="quorum-next-step">
          <span>Plan aceptado — la planificación terminó. El siguiente paso es ejecutarlo:</span>
          <button className="quorum-cta" data-testid="accepted-plan-cta" onClick={onCreateExecutionTask}>
            Crear tarea de ejecución con este plan
          </button>
        </div>
      )}
      {!skipped && contributions.length > 0 && (
        <div className="quorum-contributions">
          {contributions.map((contribution) => (
            <details key={contribution.ordinal} className={contribution.valid ? 'valid' : 'invalid'}>
              <summary>
                <span>Auditoría {contribution.ordinal}</span>
                <span>{contribution.provider || 'provider'} · {contribution.model || 'modelo'}</span>
                <span>{contribution.valid ? 'válida' : 'inválida'}</span>
              </summary>
              <pre>{typeof contribution.result === 'string' ? contribution.result : pretty(contribution.result)}</pre>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
