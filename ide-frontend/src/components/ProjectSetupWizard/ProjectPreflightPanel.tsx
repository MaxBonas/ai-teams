import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CircleDot,
  FlaskConical,
  Gauge,
  LoaderCircle,
  LockKeyhole,
  RadioTower,
  ShieldCheck,
} from 'lucide-react';
import type {
  ProjectPreflightExecutionResponse,
  ProjectPreflightResponse,
} from './types';
import { preflightExecutionAuthorizesCommit } from './projectPreflightApi';
import './ProjectPreflightPanel.css';

interface PreflightConsent {
  localFixture: boolean;
  remoteProbe: boolean;
  quota: boolean;
}

interface ProjectPreflightPanelProps {
  preview: ProjectPreflightResponse;
  execution: ProjectPreflightExecutionResponse | null;
  consent: PreflightConsent;
  busy: boolean;
  onConsent: (key: keyof PreflightConsent, checked: boolean) => void;
  onExecute: () => void;
  onCommit: () => void;
  onReviewResources: () => void;
}

const STATUS_LABELS: Record<string, string> = {
  passed: 'Superado',
  blocked: 'Bloqueado',
  not_checked: 'Pendiente',
  not_required: 'No requerido',
};

const ACTION_LABELS: Record<string, string> = {
  local_fixture: 'Smoke del stack en copia temporal',
  exact_adapter_probe: 'Probe estructurado del adapter y modelo exactos',
};

export function ProjectPreflightPanel({
  preview,
  execution,
  consent,
  busy,
  onConsent,
  onExecute,
  onCommit,
  onReviewResources,
}: ProjectPreflightPanelProps) {
  const preflight = execution?.post_execution_preflight ?? preview.preflight;
  const plan = preview.execution_plan;
  const durable = execution?.persistence.durable_receipt ?? null;
  const commitAllowed = preflightExecutionAuthorizesCommit(preview, execution);
  const inconsistentReceipt = Boolean(
    durable?.status === 'go' && !commitAllowed
  );
  const hasLocalFixture = plan.actions.some((action) => action.id === 'local_fixture');
  const hasRemoteProbe = plan.actions.some((action) => action.id === 'exact_adapter_probe');
  const needsQuotaAck = plan.actions.some((action) => action.quota_possible);
  const consentsReady = (
    (!hasLocalFixture || consent.localFixture)
    && (!hasRemoteProbe || consent.remoteProbe)
    && (!needsQuotaAck || consent.quota)
  );
  const canExecute = plan.summary.status !== 'blocked' && !durable;
  const status = commitAllowed
    ? 'authorized'
    : durable
      ? 'failed'
      : plan.summary.status === 'blocked'
        ? 'blocked'
        : 'pending';

  return (
    <section
      className={`preflight-console status-${status}`}
      aria-labelledby="preflight-title"
    >
      <div className="preflight-head">
        <div>
          <span className="stage-index">05 / AUTORIZACIÓN DE ARRANQUE</span>
          <h2 id="preflight-title">Preflight antes de despertar al equipo</h2>
          <p>
            El backend vuelve a observar ruta, toolchains y adapters. Esta pantalla
            no decide gates: representa el receipt sellado por el servidor.
          </p>
        </div>
        <div className="preflight-seal" aria-live="polite" aria-atomic="true">
          {commitAllowed ? <ShieldCheck size={22} /> : <LockKeyhole size={22} />}
          <span>
            <small>Autorización durable</small>
            <strong>
              {commitAllowed
                ? 'GO · entrada habilitada'
                : inconsistentReceipt
                  ? 'Receipt inconsistente'
                : durable
                  ? 'NO-GO · intento sellado'
                  : 'Pendiente de receipt'}
            </strong>
          </span>
        </div>
      </div>

      <ol className="preflight-track" aria-label="Estado del protocolo">
        <li className="done"><Check size={13} /><span>Propuesta<strong>sellada</strong></span></li>
        <li className={preflight.summary.go ? 'done' : 'attention'}>
          {preflight.summary.go ? <Check size={13} /> : <CircleDot size={13} />}
          <span>Contrato<strong>{preflight.summary.status.toUpperCase()}</strong></span>
        </li>
        <li className={durable ? (commitAllowed ? 'done' : 'attention') : ''}>
          {durable ? <Check size={13} /> : <CircleDot size={13} />}
          <span>Receipt<strong>{durable ? durable.status.toUpperCase() : 'pendiente'}</strong></span>
        </li>
      </ol>

      <div className="preflight-layout">
        <div className="preflight-gates">
          <div className="preflight-section-label">
            <span>Gates observados</span>
            <code>{preflight.preflight_hash.slice(0, 12)}…</code>
          </div>
          {preflight.gates.map((gate) => (
            <article className={`preflight-gate gate-${gate.status}`} key={gate.id}>
              <span className="gate-signal">
                {gate.status === 'passed'
                  ? <Check size={13} />
                  : <AlertTriangle size={13} />}
              </span>
              <div>
                <strong>{gate.id.replaceAll('_', ' ')}</strong>
                <small>{gate.message}</small>
              </div>
              <em>{STATUS_LABELS[gate.status] ?? gate.status}</em>
            </article>
          ))}
        </div>

        <aside className="preflight-actions">
          <div className="preflight-section-label">
            <span>Protocolo autorizado</span>
            <code>{preflight.objective.kind}</code>
          </div>

          {plan.planning_blockers.map((blocker) => (
            <div className="preflight-notice danger" role="alert" key={blocker.code}>
              <AlertTriangle size={16} />
              <span><strong>{blocker.message}</strong><small>{blocker.next_action}</small></span>
            </div>
          ))}
          {preflight.summary.warnings.map((warning) => (
            <div className="preflight-notice warning" key={warning.code}>
              <AlertTriangle size={15} />
              <span><strong>{warning.message}</strong><small>{warning.next_action}</small></span>
            </div>
          ))}
          {inconsistentReceipt ? (
            <div className="preflight-notice danger" role="alert">
              <AlertTriangle size={16} />
              <span>
                <strong>La cadena de hashes del receipt no coincide.</strong>
                <small>Revisa recursos y genera un preflight nuevo.</small>
              </span>
            </div>
          ) : null}

          {!plan.actions.length && plan.summary.status !== 'blocked' ? (
            <div className="preflight-zero-run">
              <ShieldCheck size={19} />
              <span>
                <strong>Sin pruebas de software</strong>
                <small>
                  Este objetivo usa {preflight.fixture_policy.kind.replaceAll('_', ' ')}.
                  No ejecutará tests ni llamadas remotas.
                </small>
              </span>
            </div>
          ) : null}

          {plan.actions.map((action, index) => (
            <div className="preflight-action-card" key={action.id}>
              <span className="action-order">0{index + 1}</span>
              {action.remote ? <RadioTower size={17} /> : <FlaskConical size={17} />}
              <span>
                <strong>{ACTION_LABELS[action.id]}</strong>
                <small>
                  {action.remote ? 'Proveedor remoto' : 'Workspace temporal aislado'}
                  {' · '}{action.timeout_seconds}s · un intento
                </small>
              </span>
            </div>
          ))}

          {hasLocalFixture ? (
            <label className="preflight-consent">
              <input
                type="checkbox"
                checked={consent.localFixture}
                onChange={(event) => onConsent('localFixture', event.target.checked)}
              />
              <span>
                <strong>Autorizo el fixture local</strong>
                <small>Se ejecutará una vez en una copia temporal allowlisted.</small>
              </span>
            </label>
          ) : null}
          {hasRemoteProbe ? (
            <label className="preflight-consent">
              <input
                type="checkbox"
                checked={consent.remoteProbe}
                onChange={(event) => onConsent('remoteProbe', event.target.checked)}
              />
              <span>
                <strong>Autorizo el probe remoto exacto</strong>
                <small>Puede leer la credencial solo durante esta comprobación.</small>
              </span>
            </label>
          ) : null}
          {needsQuotaAck ? (
            <label className="preflight-consent quota">
              <input
                type="checkbox"
                checked={consent.quota}
                onChange={(event) => onConsent('quota', event.target.checked)}
              />
              <span>
                <strong>Acepto posible consumo de cuota</strong>
                <small>No se interpreta una suscripción como capacidad ilimitada.</small>
              </span>
            </label>
          ) : null}

          {execution?.persistence.idempotent_replay ? (
            <div className="preflight-notice neutral">
              <Gauge size={15} />
              <span><strong>Receipt recuperado</strong><small>No se repitió ninguna acción.</small></span>
            </div>
          ) : null}

          <div className="preflight-command">
            {canExecute ? (
              <button
                type="button"
                className="preflight-run"
                disabled={busy || !consentsReady}
                onClick={onExecute}
              >
                {busy ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}
                {plan.actions.length
                  ? 'Ejecutar y sellar preflight'
                  : 'Sellar comprobación sin ejecutar'}
              </button>
            ) : null}
            {commitAllowed ? (
              <button
                type="button"
                className="preflight-enter"
                disabled={busy}
                onClick={onCommit}
              >
                {busy ? <LoaderCircle className="spin" size={16} /> : <RadioTower size={16} />}
                Entrar al proyecto
              </button>
            ) : null}
            {(plan.summary.status === 'blocked'
              || durable?.status === 'no_go'
              || inconsistentReceipt) ? (
              <button
                type="button"
                className="preflight-review-resources"
                disabled={busy}
                onClick={onReviewResources}
              >
                <ArrowLeft size={15} /> Revisar recursos
              </button>
            ) : null}
            <small>
              {commitAllowed
                ? `Receipt ${durable?.receipt_hash.slice(0, 12)}… vigente.`
                : inconsistentReceipt
                  ? 'El receipt no autoriza entrada: su cadena de hashes es inconsistente.'
                : durable?.status === 'no_go'
                  ? 'Este intento no se repetirá; cambia el input material y genera otro.'
                  : 'El proyecto, los agentes y la wakeup todavía no existen.'}
            </small>
          </div>
        </aside>
      </div>
    </section>
  );
}
