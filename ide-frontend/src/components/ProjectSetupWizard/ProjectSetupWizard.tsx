import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  LoaderCircle,
  LockKeyhole,
  RefreshCcw,
} from 'lucide-react';
import { ProjectProposalReview } from './ProjectProposalReview';
import { ProjectPreflightPanel } from './ProjectPreflightPanel';
import { ProjectIdentityStep } from './ProjectIdentityStep';
import { ProjectSetupProgress } from './ProjectSetupProgress';
import {
  commitPreflightedProject,
  executeProjectPreflight,
  loadProjectPreflight,
  preflightExecutionAuthorizesCommit,
} from './projectPreflightApi';
import { buildProjectProposalFlow } from './projectProposalApi';
import { ProjectSetupError } from './projectSetupApi';
import { invalidProjectStepControls } from './projectStepValidation';
import type {
  ProjectPreflightExecutionResponse,
  ProjectPreflightResponse,
  ProjectSetupWizardProps,
  ProposalResponse,
  Session,
} from './types';
import {
  PROJECT_PROFILE_OPTIONS,
  PROJECT_SETUP_STEPS,
} from './wizardConfig';
import { useWizardStageFocus } from './useWizardStageFocus';
import './ProjectSetupWizard.css';
import './ProjectSetupWizardResponsive.css';

export function ProjectSetupWizard({
  projectsRoot,
  adapters,
  preparedAdapterIds,
  selectedAdapterIds,
  onToggleAdapter,
  onCommitted,
  onOpenConfiguration,
}: ProjectSetupWizardProps) {
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<'create' | 'import'>('create');
  const [name, setName] = useState('Nuevo proyecto');
  const [path, setPath] = useState('');
  const [goal, setGoal] = useState('');
  const [objectiveKind, setObjectiveKind] = useState('software');
  const [languages, setLanguages] = useState('TypeScript, React');
  const [dataSensitivity, setDataSensitivity] = useState('internal');
  const [criticality, setCriticality] = useState('medium');
  const [budgetPriority, setBudgetPriority] = useState('balanced');
  const [autonomy, setAutonomy] = useState('supervised');
  const [externalTools, setExternalTools] = useState('optional');
  const [profile, setProfile] = useState('full_team');
  const [instructions, setInstructions] = useState('');
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [session, setSession] = useState<Session | null>(null);
  const [proposalResponse, setProposalResponse] = useState<ProposalResponse | null>(null);
  const [preflightResponse, setPreflightResponse] = useState<ProjectPreflightResponse | null>(null);
  const [preflightExecution, setPreflightExecution] = useState<ProjectPreflightExecutionResponse | null>(null);
  const [preflightConsent, setPreflightConsent] = useState({
    localFixture: false,
    remoteProbe: false,
    quota: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [validationStep, setValidationStep] = useState<number | null>(null);
  const stageRef = useWizardStageFocus(step);
  const prepared = useMemo(
    () => new Set(preparedAdapterIds),
    [preparedAdapterIds],
  );
  const selectedProfiles = useMemo(
    () => adapters.filter((adapter) => selectedAdapterIds.includes(adapter.id)),
    [adapters, selectedAdapterIds],
  );
  const selectedApiProfileIds = selectedProfiles
    .filter((adapter) => adapter.channel === 'api')
    .map((adapter) => adapter.id);
  const proposal = proposalResponse?.proposal ?? null;
  const coverageRoles = proposalResponse?.coverage.roles ?? {};
  const invalidateProposal = () => {
    setProposalResponse(null);
    setPreflightResponse(null);
    setPreflightExecution(null);
    setPreflightConsent({ localFixture: false, remoteProbe: false, quota: false });
    setSession(null);
    setError('');
    setValidationStep(null);
  };

  const subscriptions = () => {
    const desired = new Set<string>();
    selectedProfiles
      .filter((adapter) => adapter.channel === 'subscription')
      .forEach((adapter) => {
        const identity = `${adapter.id} ${adapter.provider ?? ''}`.toLowerCase();
        if (identity.includes('codex') || identity.includes('openai')) desired.add('codex');
        else if (identity.includes('antigravity') || identity.includes('gemini') || identity.includes('google')) desired.add('antigravity');
        else if (identity.includes('claude') || identity.includes('anthropic')) desired.add('claude');
        else desired.add('other');
      });
    return desired.size ? [...desired] : ['none'];
  };

  const needsAnswers = () => {
    const answers: Record<string, unknown> = {
      goal: goal.trim(),
      objective_kind: objectiveKind,
      data_sensitivity: dataSensitivity,
      budget_priority: budgetPriority,
      subscriptions: subscriptions(),
      api_access: selectedApiProfileIds.length ? 'existing' : 'not_willing',
      local_models: selectedProfiles.some((adapter) => adapter.channel === 'local')
        ? 'available'
        : 'not_wanted',
      autonomy,
      criticality,
      team_preference: profile,
      external_tools: externalTools,
    };
    if (['software', 'mixed', 'unknown'].includes(objectiveKind)) {
      answers.languages = languages
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
    }
    return answers;
  };

  const requestContext = () => ({
    selectedApiProfileIds,
    requestedProfile: profile,
    instructions,
    overridesByAgentId: overrides,
  });

  const buildProposal = async () => {
    setBusy(true);
    setError('');
    try {
      const built = await buildProjectProposalFlow({
        needsAnswers: needsAnswers(),
        identity: {
          mode,
          name: name.trim(),
          path: mode === 'import' ? path.trim() : '',
        },
        selectedApiProfileIds,
        requestedProfile: profile,
        instructions,
        overridesByAgentId: overrides,
      });
      const preflight = await loadProjectPreflight(
        built.session,
        built.proposalResponse.proposal.proposal_hash,
        requestContext(),
      );
      setSession(built.session);
      setProposalResponse(built.proposalResponse);
      setPreflightResponse(preflight);
      setPreflightExecution(null);
      setPreflightConsent({ localFixture: false, remoteProbe: false, quota: false });
      setStep(3);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'No se pudo generar la propuesta.');
    } finally {
      setBusy(false);
    }
  };

  const executePreflight = async () => {
    if (!session || !proposal || !preflightResponse) return;
    setBusy(true);
    setError('');
    try {
      const executed = await executeProjectPreflight(
        session,
        proposal.proposal_hash,
        requestContext(),
        preflightResponse,
        preflightConsent,
      );
      setPreflightExecution(executed);
    } catch (reason) {
      if (reason instanceof ProjectSetupError && reason.status === 409) {
        invalidateProposal();
        setStep(2);
        setError('El preflight quedó obsoleto. Revisa recursos y genera una propuesta nueva.');
      } else {
        setError(reason instanceof Error ? reason.message : 'No se pudo ejecutar el preflight.');
      }
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (
      !session
      || !proposal
      || !preflightResponse
      || !preflightExecutionAuthorizesCommit(preflightResponse, preflightExecution)
    ) return;
    setBusy(true);
    setError('');
    try {
      const committed = await commitPreflightedProject(
        session,
        proposal.proposal_hash,
        requestContext(),
      );
      await onCommitted({
        ...committed.result,
        configured: true,
        success: true,
      });
    } catch (reason) {
      if (reason instanceof ProjectSetupError && reason.status === 409) {
        invalidateProposal();
        setStep(2);
        setError('La autorización ya no coincide con la máquina. Genera otro preflight.');
      } else {
        setError(reason instanceof Error ? reason.message : 'No se pudo guardar el proyecto.');
      }
    } finally {
      setBusy(false);
    }
  };

  const resourcesReady = selectedProfiles.some((adapter) => prepared.has(adapter.id));
  const invalidControlIds = invalidProjectStepControls({
    step,
    mode,
    name,
    path,
    goal,
    objectiveKind,
    languages,
    resourcesReady,
  });
  const validationReady = !invalidControlIds.length;
  const readinessMessage = step === 3
    ? 'Autoriza y sella el preflight para entrar.'
    : validationReady
      ? 'Paso completo.'
      : 'Revisa los campos señalados.';
  const showValidation = validationStep === step && !validationReady;
  const fieldIsInvalid = (id: string) => (
    showValidation && invalidControlIds.includes(id)
  );

  const next = () => {
    if (!validationReady) {
      setValidationStep(step);
      const targetId = invalidControlIds[0];
      window.setTimeout(() => document.getElementById(targetId)?.focus(), 0);
      return;
    }
    setValidationStep(null);
    if (step === 0) setStep(1);
    else if (step === 1) setStep(2);
    else if (step === 2) void buildProposal();
  };

  return (
    <section className="project-setup" aria-labelledby="project-setup-title">
      <header className="project-setup-hero">
        <div>
          <span className="project-setup-kicker">04 decisiones · 01 gate de arranque</span>
          <h1 id="project-setup-title">Configura el proyecto antes de despertar al equipo</h1>
          <p>
            El servidor comprueba cada decisión. Nada se crea hasta confirmar el recibo.
          </p>
        </div>
        <div className="project-setup-root" title={projectsRoot}>
          <LockKeyhole size={15} />
          <span>Raíz confinada</span>
          <code>{projectsRoot || 'sin configurar'}</code>
        </div>
      </header>

      <ProjectSetupProgress
        step={step}
        busy={busy}
        onNavigate={(targetStep) => {
          if (targetStep < step) setStep(targetStep);
        }}
      />

      {error ? (
        <div id="project-setup-error" className="project-setup-alert" role="alert">
          <AlertTriangle size={17} />
          <span>{error}</span>
        </div>
      ) : null}

      <div
        className="project-setup-stage"
        ref={stageRef}
        tabIndex={-1}
        role="region"
        aria-labelledby={PROJECT_SETUP_STEPS[step].headingId}
        aria-describedby={error ? 'project-setup-error' : undefined}
      >
        {step === 0 ? (
          <ProjectIdentityStep
            mode={mode}
            name={name}
            path={path}
            projectsRoot={projectsRoot}
            invalidControlIds={showValidation ? invalidControlIds : []}
            onModeChange={(nextMode) => { setMode(nextMode); invalidateProposal(); }}
            onNameChange={(value) => { setName(value); invalidateProposal(); }}
            onPathChange={(value) => { setPath(value); invalidateProposal(); }}
          />
        ) : null}

        {step === 1 ? (
          <div className="project-setup-grid objective-layout">
            <div className="project-setup-copy">
              <span className="stage-index">02 / OBJETIVO</span>
              <h2 id="project-step-objective-title">Define el resultado esperado</h2>
              <p>
                El tipo de objetivo determina la evidencia; un estudio no se trata como software.
              </p>
            </div>
            <div className="project-setup-form wide">
              <label className="span-two">
                Resultado que debe conseguir el Lead
                <textarea
                  id="project-goal"
                  value={goal}
                  onChange={(event) => { setGoal(event.target.value); invalidateProposal(); }}
                  placeholder="Ej.: entregar formularios utilizables para una empresa de limpieza."
                  aria-invalid={fieldIsInvalid('project-goal')}
                  aria-describedby={fieldIsInvalid('project-goal') ? 'project-goal-error' : undefined}
                  required
                />
                {fieldIsInvalid('project-goal') ? (
                  <small id="project-goal-error" className="field-error" role="alert">
                    Describe el resultado.
                  </small>
                ) : null}
              </label>
              <label>
                Tipo de resultado
                <select value={objectiveKind} onChange={(event) => { setObjectiveKind(event.target.value); invalidateProposal(); }}>
                  <option value="software">Software</option>
                  <option value="research">Investigación o estudio</option>
                  <option value="operations">Operaciones o procedimientos</option>
                  <option value="mixed">Mixto</option>
                  <option value="unknown">Que el sistema lo determine</option>
                </select>
              </label>
              {['software', 'mixed', 'unknown'].includes(objectiveKind) ? (
                <label>
                  Lenguajes o stack
                  <input
                    id="project-languages"
                    value={languages}
                    onChange={(event) => { setLanguages(event.target.value); invalidateProposal(); }}
                    aria-invalid={fieldIsInvalid('project-languages')}
                    aria-describedby={fieldIsInvalid('project-languages') ? 'project-languages-error' : undefined}
                    required
                  />
                  {fieldIsInvalid('project-languages') ? (
                    <small id="project-languages-error" className="field-error" role="alert">
                      Indica un lenguaje o stack.
                    </small>
                  ) : null}
                </label>
              ) : null}
              <label>
                Sensibilidad de datos
                <select value={dataSensitivity} onChange={(event) => { setDataSensitivity(event.target.value); invalidateProposal(); }}>
                  <option value="public">Públicos</option>
                  <option value="internal">Internos</option>
                  <option value="confidential">Confidenciales</option>
                  <option value="restricted">Restringidos</option>
                </select>
              </label>
              <label>
                Criticidad
                <select value={criticality} onChange={(event) => { setCriticality(event.target.value); invalidateProposal(); }}>
                  <option value="low">Baja</option>
                  <option value="medium">Media</option>
                  <option value="high">Alta</option>
                  <option value="critical">Crítica</option>
                </select>
              </label>
              <label className="span-two">
                Instrucciones persistentes para el Lead
                <textarea
                  value={instructions}
                  onChange={(event) => { setInstructions(event.target.value); invalidateProposal(); }}
                  placeholder="Preferencias que deben sobrevivir entre runs."
                />
                <small>Se guardarán en <code>.aiteam/instructions.md</code>.</small>
              </label>
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="project-setup-resources">
            <div className="project-setup-copy resource-heading">
              <span className="stage-index">03 / RECURSOS Y EQUIPO</span>
              <h2 id="project-step-resources-title">Elige canales; el backend forma el equipo</h2>
              <p>
                Solo un adapter preparado y probado concede cobertura automática.
              </p>
            </div>

            <div className="resource-control-grid">
              <div className="profile-rail">
                <span className="control-label">Perfil operativo</span>
                {PROJECT_PROFILE_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={profile === option.value ? 'active' : ''}
                    onClick={() => { setProfile(option.value); invalidateProposal(); }}
                    aria-pressed={profile === option.value}
                  >
                    <span>
                      <strong>{option.label}</strong>
                      <small>{option.detail}</small>
                    </span>
                    {profile === option.value ? <Check size={16} /> : null}
                  </button>
                ))}
              </div>
              <div className="policy-grid">
                <label>
                  Prioridad económica
                  <select value={budgetPriority} onChange={(event) => { setBudgetPriority(event.target.value); invalidateProposal(); }}>
                    <option value="zero_cost">Solo coste marginal cero</option>
                    <option value="prefer_free">Priorizar gratuitos</option>
                    <option value="balanced">Equilibrio</option>
                    <option value="quality_first">Máxima calidad</option>
                  </select>
                </label>
                <label>
                  Autonomía
                  <select value={autonomy} onChange={(event) => { setAutonomy(event.target.value); invalidateProposal(); }}>
                    <option value="supervised">Supervisada</option>
                    <option value="balanced">Equilibrada</option>
                    <option value="autonomous">Autónoma</option>
                  </select>
                </label>
                <label>
                  Herramientas externas / MCP
                  <select value={externalTools} onChange={(event) => { setExternalTools(event.target.value); invalidateProposal(); }}>
                    <option value="none">No necesarias</option>
                    <option value="optional">Opcionales</option>
                    <option value="required">Necesarias</option>
                  </select>
                </label>
              </div>
            </div>

            <div className="adapter-board">
              <div className="adapter-board-head">
                <div>
                  <span className="control-label">Adapters disponibles</span>
                  <strong>{prepared.size} preparados · {selectedProfiles.length} seleccionados</strong>
                </div>
                {onOpenConfiguration ? (
                  <button type="button" className="text-action" onClick={onOpenConfiguration}>
                    Configurar otro adapter <ArrowRight size={14} />
                  </button>
                ) : null}
              </div>
              <div className="adapter-board-grid">
                {adapters
                  .filter((adapter) => adapter.status !== 'blocked_by_provider')
                  .map((adapter) => {
                    const ready = prepared.has(adapter.id);
                    const selected = selectedAdapterIds.includes(adapter.id);
                    return (
                      <button
                        key={adapter.id}
                        type="button"
                        className={`adapter-tile${ready ? ' ready' : ''}${selected ? ' selected' : ''}`}
                        onClick={() => {
                          if (ready) {
                            onToggleAdapter(adapter.id);
                            invalidateProposal();
                          }
                        }}
                        disabled={!ready}
                        aria-pressed={ready ? selected : undefined}
                        title={adapter.health?.detail || adapter.health?.reason || adapter.label}
                      >
                        <span className="adapter-signal" />
                        <span>
                          <strong>{adapter.label}</strong>
                          <small>{adapter.channel || 'canal'} · {ready ? 'preparado' : 'requiere configuración'}</small>
                        </span>
                        {selected ? <Check size={16} /> : null}
                      </button>
                    );
                  })}
              </div>
              {!adapters.length ? <p className="empty-note">Cargando inventario de adapters…</p> : null}
              {fieldIsInvalid('resources-error') ? (
                <p
                  id="resources-error"
                  className="field-error"
                  role="alert"
                  tabIndex={-1}
                >
                  Selecciona un adapter preparado.
                </p>
              ) : null}
            </div>
          </div>
        ) : null}

        {step === 3 && proposal ? (
          <>
            <ProjectProposalReview
              proposal={proposal}
              coverageRoles={coverageRoles}
              overrides={overrides}
              budgetPriority={budgetPriority}
              onOverride={(agentId, candidateId) => {
                setOverrides((current) => ({ ...current, [agentId]: candidateId }));
                invalidateProposal();
                setStep(2);
              }}
            />
            {preflightResponse ? (
              <ProjectPreflightPanel
                preview={preflightResponse}
                execution={preflightExecution}
                consent={preflightConsent}
                busy={busy}
                onConsent={(key, checked) => {
                  setPreflightConsent((current) => ({ ...current, [key]: checked }));
                }}
                onExecute={() => void executePreflight()}
                onCommit={() => void commit()}
                onReviewResources={() => {
                  invalidateProposal();
                  setStep(2);
                }}
              />
            ) : null}
          </>
        ) : null}
      </div>

      <footer className="project-setup-actions">
        <button
          type="button"
          className="back-action"
          onClick={() => setStep((current) => Math.max(0, current - 1))}
          disabled={step === 0 || busy}
        >
          <ArrowLeft size={15} /> Atrás
        </button>
        <span id="project-setup-readiness" aria-live="polite">{readinessMessage}</span>
        {step < 3 ? (
          <button
            type="button"
            onClick={next}
            aria-describedby="project-setup-readiness"
            disabled={
              busy
            }
          >
            {step === 2 ? (
              <>
                {busy ? <LoaderCircle className="spin" size={15} /> : <RefreshCcw size={15} />}
                Generar propuesta
              </>
            ) : (
              <>Continuar <ArrowRight size={15} /></>
            )}
          </button>
        ) : null}
      </footer>
    </section>
  );
}
