import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ProfileBadge } from './components/ProfileBadge';
import { RunsPanel } from './components/RunsPanel';
import { IssuePanel, IssuePipeline } from './components/IssuePanel';
import { ChatPanel } from './components/ChatPanel';
import { ModelCatalog } from './components/ModelCatalog';
import { ModelRoleSelector } from './components/ModelRoleSelector';
import { QuorumStepper } from './components/QuorumStepper';
import {
  type ConfigSection,
} from './components/ConfigurationPanel';
import {
  CliSetupGuide,
} from './components/ConfigurationPanel/CliSettings';
import type { OrientationMeasurement } from './components/ConfigurationPanel/OrientationSettings';
import { ConfigurationWorkspace } from './components/ConfigurationPanel/ConfigurationWorkspace';
import type {
  ModelCompatibility,
} from './components/ConfigurationPanel/types';
import { InfoTip } from './components/InfoTip';
import { InboxPanel } from './components/InboxPanel';
import {
  HiringDecisionDetail,
  type ProposedTeamMember,
} from './components/InboxPanel/HiringDecisionDetail';
import { useQuorum } from './hooks/useQuorum';
import { useConfigurationData } from './hooks/useConfigurationData';
import './components/TeamPanel.css';
import {
  Activity,
  AlertCircle,
  Bell,
  Boxes,
  CheckCircle2,
  Clock3,
  Code2,
  FileText,
  FolderOpen,
  FolderPlus,
  GitBranch,
  KeyRound,
  ListChecks,
  MessageSquare,
  Play,
  Plus,
  RefreshCcw,
  Send,
  Users,
} from 'lucide-react';
import { API_BASE, apiFetch, getWorkspacePath, setWorkspacePath } from './lib/api';
import { formatTime, statusLabel } from './lib/format';
import { renderMarkdownLite } from './lib/markdown';
import type {
  ChatMessage,
  Comment,
  Interaction,
  Issue,
  Run,
  RunEvent,
} from './types/cockpit';

interface HealthPayload {
  status?: string;
  mode?: string;
}

interface LoopHealthEntry {
  child_issue_id: string;
  parent_issue_id?: string | null;
  child_title?: string | null;
  skip_count: number;
  loop_detected_at?: string | null;
}

interface PolicyDeviation {
  agent_id: string;
  role: string;
  provider: string;
  model: string;
  estimated_cost_cents_per_run: number;
  reason: string;
}

interface SubscriptionQuotaProfile {
  profile_id: string;
  label: string;
  state: 'no_data' | 'unmetered' | 'metered' | 'api_metered' | 'at_risk' | 'limit_reached' | 'exhausted_observed';
  quota_kind?: 'subscription_pressure' | 'api_rate_limit';
  channel?: 'subscription' | 'api' | 'local';
  requires_attention: boolean;
  usage_limit_events: number;
  api_rate_limits?: Array<{
    model?: string | null;
    dimension: 'rpm' | 'rpd' | 'tpm' | 'tpd' | 'itpm' | 'otpm';
    remaining?: number | null;
    limit?: number | null;
    reset?: string | null;
  }>;
  forecast: {
    status: string;
    source?: string | null;
    unit?: string | null;
    remaining?: number | null;
    estimated_runs_remaining?: number | null;
    estimated_exhaustion_at?: string | null;
  };
}

interface LoopHealth {
  detected_loops: LoopHealthEntry[];
  at_risk: Array<{ child_issue_id: string; child_title?: string | null; skip_count: number }>;
  thin_delegations_last_24h: number;
  policy_deviations?: PolicyDeviation[];
  capacity_profiles?: SubscriptionQuotaProfile[];
  subscription_quota?: SubscriptionQuotaProfile[];
  subscription_profiles_requiring_attention?: string[];
  orchestrator_evals?: {
    available?: boolean;
    economy?: {
      total_tokens: number;
      cost_cents: number;
    };
    context?: {
      context_curator_issues: number;
    };
    quorum?: {
      available: boolean;
      healthy?: boolean;
      invalid_contributions?: number;
      accepted_without_provider_diversity?: number;
      accepted_with_unresolved_findings?: number;
    };
    liveness?: {
      nonterminal_runs: number;
      stale_nonterminal_runs: number;
      claimed_or_running_wakeups: number;
      stale_claimed_or_running_wakeups: number;
      stranded_nonterminal_roots: number;
      healthy: boolean;
    };
  };
  summary: { total_loops: number; total_at_risk: number; requires_attention: boolean };
}

interface WorkspacePayload {
  workspace?: string;
  configured?: boolean;
  projects_root?: string;
  project_name?: string;
  success?: boolean;
  detail?: string;
  reason?: string;
  missing_workspace?: string;
}

type ObjectiveKind = 'auto' | 'software' | 'research' | 'operations' | 'mixed';

interface Agent {
  id: string;
  role: string;
  name: string;
  seniority?: string;
  adapter_type?: string | null;
  adapter_config?: Record<string, unknown> | null;
  capabilities?: string[];
  budget_monthly_cents?: number | null;
  status?: string;
  supervisor_agent_id?: string | null;
}

interface CapabilityEntry {
  description: string;
  tool_family: string;
  label: string;
}

interface RoleModelOption {
  value: string;
  label: string;
  recommended?: boolean;
  fit_reason?: string;
  role_score?: number;
  tier?: string;
  price_note?: string;
  available?: boolean;
  selectable?: boolean;
  availability?: string;
  availability_reason?: string;
  compatibility?: ModelCompatibility;
}

interface BudgetInfo {
  agent_id: string;
  agent_name: string;
  agent_role: string;
  period: string;
  budget_monthly_cents: number;
  spent_cents: number;
  remaining_cents: number;
  exceeded: boolean;
  near_limit: boolean;
  allowed: boolean;
  reason: string;
}

interface CostBucket {
  runs: number;
  actual_cost_cents: number;
  estimated_savings_cents: number;
}

interface CostSummary {
  totals: CostBucket;
  by_role: Array<CostBucket & { role: string }>;
  by_channel: Array<CostBucket & { channel: string }>;
}

function agentTier(seniority?: string | null): 1 | 2 | 3 {
  const s = (seniority ?? '').toLowerCase();
  if (s === 'lead' || s === 'senior') return 1;
  if (s === 'standard') return 2;
  return 3; // cheap, local, or unknown → Tier 3
}

const TIER_LABELS: Record<number, { title: string; sub: string }> = {
  1: { title: 'Tier 1 — Lead & Seniors', sub: 'Modelos premium · planificación y supervisión' },
  2: { title: 'Tier 2 — Engineers & Especialistas', sub: 'Modelos mid · ejecución y revisión' },
  3: { title: 'Tier 3 — Scouts & Helpers', sub: 'Modelos budget · investigación y resumen' },
};

interface RoleDef {
  title: string;
  seniority: string;
  tier: 1 | 2 | 3;
  desc: string;
  responsibilities: string;
  when: string;
}
const ROLE_CATALOG: Record<string, RoleDef> = {
  'role:lead': {
    title: 'Team Lead', seniority: 'lead', tier: 1,
    desc: 'Cerebro del equipo. Planifica, delega y supervisa. Convierte tareas en issues vivos y es el único punto de contacto con el usuario.',
    responsibilities: 'Escribe el plan, crea sub-issues, desbloquea al equipo, toma decisiones de arquitectura y escala al usuario solo cuando hay ambigüedad de producto.',
    when: 'Siempre presente. Es el primer agente creado al iniciar un proyecto.',
  },
  'role:engineer': {
    title: 'Engineer', seniority: 'standard', tier: 2,
    desc: 'Implementa código según las especificaciones del Lead. Escribe código, tests y documentación técnica.',
    responsibilities: 'Lee el spec del Lead, escribe o modifica archivos en el workspace, reporta con un AGENT-REPORT estructurado al terminar o al bloquearse.',
    when: 'Contratado por el Lead cuando hay trabajo de implementación concreto.',
  },
  'role:reviewer': {
    title: 'Reviewer', seniority: 'standard', tier: 2,
    desc: 'Revisa la entrega del Engineer. Análisis estático, bugs, seguridad y desviaciones del spec. Absorbe responsabilidades de QA estático.',
    responsibilities: 'Lee el código entregado, verifica contra criterios del Lead, aprueba o rechaza con feedback específico. No necesita CLI.',
    when: 'Contratado junto al Engineer. Imprescindible para trabajo de producción.',
  },
  'role:quorum_auditor_1': {
    title: 'Quorum Auditor 1', seniority: 'senior', tier: 1,
    desc: 'Primer revisor independiente del plan del Lead. Idealmente usa un proveedor distinto al del Lead (ej. Anthropic si el Lead es OpenAI) para aportar un ángulo de razonamiento diferente.',
    responsibilities: 'Lee el plan del Lead, identifica riesgos no cubiertos, incoherencias o lagunas. Reporta aprobación o lista de correcciones.',
    when: 'Solo en perfil Lead+Quorum. Pre-creado automáticamente. Recomienda modelo avanzado de un proveedor alternativo al Lead.',
  },
  'role:quorum_auditor_2': {
    title: 'Quorum Auditor 2', seniority: 'senior', tier: 1,
    desc: 'Segundo revisor independiente. Usa un tercer proveedor (ej. Google Gemini) para máxima diversidad de perspectivas. El Lead consolida los tres puntos de vista antes de actuar.',
    responsibilities: 'Igual que Auditor 1 pero desde un ángulo distinto. El Lead necesita aprobación de ambos para continuar la delegación.',
    when: 'Solo en perfil Lead+Quorum. Pre-creado automáticamente. Recomienda modelo avanzado de un tercer proveedor.',
  },
  'role:file_scout': {
    title: 'File Scout', seniority: 'cheap', tier: 3,
    desc: 'Lee archivos y resume su contenido para el Lead. 50-100× más barato que un Senior leyendo el mismo código.',
    responsibilities: 'Recibe una lista de archivos y una pregunta concreta. Lee los archivos y devuelve un resumen estructurado al Lead. Solo lectura.',
    when: 'Delegado por el Lead para leer código o docs sin gastar tokens caros.',
  },
  'role:web_scout': {
    title: 'Web Scout', seniority: 'cheap', tier: 3,
    desc: 'Busca en la web y resume resultados sin consumir contexto del Lead.',
    responsibilities: 'Recibe un objetivo de búsqueda y una pregunta. Usa buscadores y fetch para obtener información actual y devuelve un resumen al Lead.',
    when: 'Delegado por el Lead para research técnico o investigación de librerías.',
  },
  'role:context_curator': {
    title: 'Context Curator', seniority: 'cheap', tier: 3,
    desc: 'Comprime threads largos en documentos concisos. Cuando el contexto crece demasiado, el Curator extrae lo esencial.',
    responsibilities: 'Lee el historial de comentarios de un issue, extrae el plan activo, decisiones tomadas y estado actual. Escribe un plan doc compacto.',
    when: 'Usado por el Lead cuando el contexto del thread es demasiado largo para procesar.',
  },
};

const FIELD_TIPS = {
  adapter: 'El paquete de ejecución del agente: agrupa el mecanismo de conexión (API, CLI de suscripción, local), las credenciales y el modelo por defecto. Los adapters aparecen automáticamente cuando añades una API key o CLI en Config. Elige el que corresponde al proveedor y tipo de cuenta que quieres usar.\n\nEl "tipo técnico" (openai_api, anthropic_sonnet…) se deriva del adapter y es solo info interna.',
  model: 'Modelo de lenguaje concreto que usará este agente. "Default del perfil" deja que el sistema elija el modelo óptimo según el rol y el perfil seleccionado.',
  capabilities: 'Herramientas y permisos del agente. Repo R = leer workspace. Repo W = escribir archivos. LSP = análisis estático de código. Tests/Build = ejecutar comandos.',
  budget: 'Límite de gasto mensual en centavos de USD (100 = $1). 0 significa sin límite. Al superar el budget, el agente queda bloqueado hasta el próximo mes.',
  seniority: 'Nivel del agente que determina el tier de modelos. Lead/Senior → Tier 1 (modelos premium). Standard → Tier 2 (modelos mid). Cheap → Tier 3 (modelos económicos).',
  name: 'Identificador del agente dentro del equipo. Por convención: rol-número (ej. eng-1, review-2).',
};

const PROFILE_OPTIONS = [
  { value: 'full_team', label: 'Equipo completo', desc: 'Lead + Engineer + Reviewer' },
  { value: 'lead_quorum', label: 'Lead + Quorum', desc: 'Lead con auditores senior para planificación' },
  { value: 'solo_lead', label: 'Solo Lead', desc: 'Lead ejecuta directamente sin contratar' },
];

const PROFILE_GUIDANCE: Record<string, { cost: string; risk: string }> = {
  full_team: {
    cost: 'Más runs: ejecución y revisión separadas.',
    risk: 'Reduce errores de implementación con accountability y revisión.',
  },
  lead_quorum: {
    cost: 'Más trabajo antes de ejecutar: varios seniors auditan el plan.',
    risk: 'Reduce ambigüedad y riesgo arquitectónico antes de cambiar código.',
  },
  solo_lead: {
    cost: 'Menos runs: un único Lead ejecuta de principio a fin.',
    risk: 'Sin revisión independiente; úsalo solo en trabajo acotado y reversible.',
  },
};

const OBJECTIVE_KIND_OPTIONS: Array<{ value: ObjectiveKind; label: string }> = [
  { value: 'auto', label: 'Automático (recomendado)' },
  { value: 'software', label: 'Software' },
  { value: 'research', label: 'Investigación / análisis' },
  { value: 'operations', label: 'Operaciones' },
  { value: 'mixed', label: 'Mixto' },
];

const OBJECTIVE_KIND_LABELS: Record<string, string> = {
  software: 'Software',
  research: 'Investigación',
  operations: 'Operaciones',
  mixed: 'Mixto',
};

// Perfil de ejecución de una issue, leído de metadata_json (persistido por el backend).
const PROFILE_BADGES: Record<string, { label: string; cls: string }> = {
  full_team: { label: 'Equipo completo', cls: 'team' },
  lead_quorum: { label: 'Lead + Quorum', cls: 'quorum' },
  solo_lead: { label: 'Solo Lead', cls: 'solo' },
};

function issueMetadata(issue: Issue | null | undefined): Record<string, unknown> {
  if (!issue?.metadata_json) return {};
  try {
    return JSON.parse(issue.metadata_json) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function issueProfile(issue: Issue | null | undefined): string | null {
  const profile = String(issueMetadata(issue).profile || '').trim().toLowerCase();
  return profile in PROFILE_BADGES ? profile : null;
}

function issueObjectiveKind(issue: Issue | null | undefined): string | null {
  const metadata = issueMetadata(issue);
  const classification = typeof metadata.objective_classification === 'object' && metadata.objective_classification
    ? metadata.objective_classification as Record<string, unknown>
    : {};
  const kind = String(classification.kind || '').trim().toLowerCase();
  return kind in OBJECTIVE_KIND_LABELS ? kind : null;
}

function issueCompatibilityContext(issue: Issue | null | undefined) {
  const metadata = issueMetadata(issue);
  const classification = typeof metadata.data_classification === 'object' && metadata.data_classification
    ? metadata.data_classification as Record<string, unknown>
    : {};
  return {
    runProfile: issueProfile(issue) || '',
    criticality: String(issue?.criticality || 'medium'),
    dataClass: String(metadata.data_class || classification.class || classification.level || ''),
  };
}

function apiDetailText(detail: unknown, fallback: string): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>;
    const reason = String(value.reason || '').trim();
    const code = String(value.code || '').trim();
    if (reason) return code ? `${reason} (${code})` : reason;
  }
  return fallback;
}

function modelOptionCacheKey(profileId: string, role: string, issue: Issue | null | undefined): string {
  const context = issueCompatibilityContext(issue);
  return `${profileId}:${role}:${context.runProfile}:${context.criticality}:${context.dataClass}`;
}


interface TimelineItem {
  id: string;
  issueId?: string | null;
  time?: string;
  type: 'issue' | 'comment' | 'interaction' | 'run' | 'activity' | 'cost' | 'tool';
  title: string;
  detail: string;
  actor?: string;
  status?: string;
}

interface PlanDocument {
  id: string;
  issue_id: string;
  key: string;
  title: string;
  body: string;
  format: string;
  revision_number: number;
  current_revision_id: string;
  updated_at?: string;
  created_at?: string;
  plan?: {
    schema_version: number;
    objective: string;
    scope: string[];
    assumptions: string[];
    architecture: string;
    work_items: Array<{
      id: string;
      title: string;
      owner_role: string;
      reports_to: string;
      deliverable: string;
      evidence: string[];
      accepted_by: string;
      dependencies: string[];
    }>;
    risks: Array<{ risk: string; mitigation: string; rollback: string }>;
    verification: Array<{ criterion: string; evidence: string; owner_role: string }>;
    escalation_conditions: string[];
    next_run_risks: string[];
    narrative_markdown: string;
  } | null;
  contract_validation?: { valid: boolean; errors: string[] };
}

interface ProjectStatePayload {
  success?: boolean;
  detail?: string;
  autonomy?: string;
  cursor?: string | null;
  issues?: Issue[];
  agents?: Agent[];
  runs?: Run[];
  timeline?: TimelineItem[];
  comments?: Comment[];
  interactions?: Interaction[];
  selected_issue_id?: string;
  plan_document?: PlanDocument | null;
}

type TimelineType = 'issue' | 'comment' | 'interaction' | 'run' | 'activity' | 'cost' | 'tool';
const TIMELINE_TYPES: TimelineType[] = ['issue', 'comment', 'interaction', 'run', 'activity', 'cost', 'tool'];

// Etiquetas de cara al usuario para los tipos técnicos de la actividad.
const TIMELINE_TYPE_LABELS: Record<TimelineType, string> = {
  issue: 'Issues',
  comment: 'Comentarios',
  interaction: 'Decisiones',
  run: 'Runs',
  activity: 'Sistema',
  cost: 'Coste',
  tool: 'Herramientas',
};

type ViewMode = 'timeline' | 'issue' | 'plan' | 'runs' | 'chat' | 'inbox' | 'files' | 'team' | 'models' | 'config';

// Terminal statuses = the issue is closed (no more work expected).
const CLOSED_ISSUE_STATUSES = new Set(['done', 'cancelled', 'completed']);
function isClosedIssue(status: string): boolean {
  return CLOSED_ISSUE_STATUSES.has(status);
}

function shortPath(path: string): string {
  const parts = path.replaceAll('\\', '/').split('/');
  return parts.slice(-2).join('/');
}

function clip(text: string, max = 220): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > max ? `${normalized.slice(0, max - 1)}...` : normalized;
}

export default function App() {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  // El backend local no responde (proceso muerto o puerto equivocado) — pantalla
  // dedicada en lugar del onboarding: el proyecto sigue en disco.
  const [backendDown, setBackendDown] = useState(false);
  const [workspace, setWorkspace] = useState(getWorkspacePath());
  const [workspaceConfigured, setWorkspaceConfigured] = useState(false);
  const [workspaceDraft, setWorkspaceDraft] = useState(getWorkspacePath());
  const [projectName, setProjectName] = useState('Nuevo Proyecto AI Teams');
  const [initialTask, setInitialTask] = useState('');
  const [issues, setIssues] = useState<Issue[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [budgets, setBudgets] = useState<BudgetInfo[]>([]);
  const [costSummary, setCostSummary] = useState<CostSummary | null>(null);
  const [comments, setComments] = useState<Comment[]>([]);
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [timelineItems, setTimelineItems] = useState<TimelineItem[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState('issue:intake');
  const [viewMode, setViewMode] = useState<ViewMode>('chat');
  const [planDocument, setPlanDocument] = useState<PlanDocument | null>(null);
  const [timelineTypeFilter, setTimelineTypeFilter] = useState<TimelineType | ''>('');
  const [issueFilter, setIssueFilter] = useState<'all' | 'open' | 'closed'>('all');
  const [commentDraft, setCommentDraft] = useState('');
  const [newTaskDraft, setNewTaskDraft] = useState('');
  const [runId, setRunId] = useState('');
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[]>([]);
  const [lastResult, setLastResult] = useState<unknown>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // Team profile for new tasks
  const [newTaskProfile, setNewTaskProfile] = useState<string>('full_team');
  const [newTaskObjectiveKind, setNewTaskObjectiveKind] = useState<ObjectiveKind>('auto');
  // Perfil inicial conservador; quorum se elige explícitamente cuando el riesgo lo justifica.
  const [newProjectRunProfile, setNewProjectRunProfile] = useState<string>('full_team');
  const [newProjectObjectiveKind, setNewProjectObjectiveKind] = useState<ObjectiveKind>('auto');
  const [newProjectDataClass, setNewProjectDataClass] = useState<string>('internal');
  // Plan aceptado adjunto a la próxima tarea (recibo por revisión, no texto copiado)
  const [pendingPlanRef, setPendingPlanRef] = useState<{ revisionId: string; sourceIssueId: string } | null>(null);
  // Agent config inline edit (sidebar)
  const [, setEditingAgentId] = useState<string | null>(null);
  const [agentDraft, setAgentDraft] = useState<Partial<Agent>>({});
  // Agent config modal (team panel)
  const [configModalAgent, setConfigModalAgent] = useState<Agent | null>(null);
  const [catalogHire, setCatalogHire] = useState<{
    roleId: string;
    roleDef: RoleDef;
    profileId: string;
    model: string;
    candidateId: string;
  } | null>(null);
  // Hiring panel — editable team proposal per pending suggest_tasks interaction
  const [hiringDrafts, setHiringDrafts] = useState<Record<string, ProposedTeamMember[]>>({});
  // Free-text note per request_confirmation interaction (cleared after submit)
  const [interactionNotes, setInteractionNotes] = useState<Record<string, string>>({});
  const [fallbackSelections, setFallbackSelections] = useState<Record<string, {
    profileId: string;
    model: string;
    candidateId: string;
  }>>({});
  // Tool capability catalog
  const [capabilityCatalog, setCapabilityCatalog] = useState<Record<string, CapabilityEntry>>({});
  const [roleModelOptions, setRoleModelOptions] = useState<Record<string, RoleModelOption[]>>({});
  const [selectedProjectAdapterIds, setSelectedProjectAdapterIds] = useState<string[]>([]);
  const [leadAdapterProfileId, setLeadAdapterProfileId] = useState('');
  const [leadModel, setLeadModel] = useState('');
  const [leadCandidateId, setLeadCandidateId] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState('');
  // Project initialization loading state
  const [projectInitializing, setProjectInitializing] = useState(false);
  // Chat channel (Lead ↔ User)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatDraft, setChatDraft] = useState('');
  const [chatSending, setChatSending] = useState(false);
  const chatFeedRef = useRef<HTMLDivElement>(null);
  // Auto-scroll only while the user is already at the bottom of the feed;
  // scrolling up "unsticks" it so reading old messages isn't interrupted.
  const chatStickToBottomRef = useRef(true);
  const [chatJumpVisible, setChatJumpVisible] = useState(false);
  // Bandeja de decisiones: interacción seleccionada en la vista 'inbox'
  const [selectedInteractionId, setSelectedInteractionId] = useState<string | null>(null);
  // Sección activa del panel de configuración (dos ámbitos: proyecto / aplicación)
  const [cfgSection, setCfgSection] = useState<ConfigSection>('proyecto');
  const [orientationMeasurement, setOrientationMeasurement] = useState<OrientationMeasurement | null>(null);
  const [orientationBusy, setOrientationBusy] = useState(false);
  const orientationWorkspaceRef = useRef('');
  const orientationConsentRef = useRef<{ enabled: boolean; sessionId: string | null }>({ enabled: false, sessionId: null });
  const orientationSessionEndPromiseRef = useRef<Promise<unknown> | null>(null);
  const orientationSessionProgressRef = useRef<{
    activeFlows: Set<'inbox' | 'profile_selection' | 'accepted_plan_to_task'>;
    completedAnyFlow: boolean;
    abandonedAnyFlow: boolean;
  }>({ activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false });
  // Workspace files browser
  const [wsFiles, setWsFiles] = useState<Array<{ path: string; size_bytes: number; mime: string }>>([]);
  const [wsSelectedFile, setWsSelectedFile] = useState<string | null>(null);
  const [wsFileContent, setWsFileContent] = useState<string | null>(null);
  const [wsFileLoading, setWsFileLoading] = useState(false);
  // Project list (switcher)
  const [projectList, setProjectList] = useState<Array<{ name: string; path: string; current: boolean }>>([]);
  const [projectListOpen, setProjectListOpen] = useState(false);
  const [loopHealth, setLoopHealth] = useState<LoopHealth | null>(null);
  // In-flight guard: the 20 s baseline and 2 s active-run intervals overlap;
  // skip a poll tick while the previous /api/project/state is still pending.
  const projectStatePollBusy = useRef(false);

  const configurationData = useConfigurationData({
    setGlobalBusy: setLoading,
    reportError: setError,
    reportResult: setLastResult,
    setSelectedProjectAdapterIds,
  });
  const {
    projectsRoot,
    setProjectsRoot,
    settingsConfigured,
    settingsDraft,
    setSettingsDraft,
    adapterProfiles,
    adapterTestModels,
    setAdapterTestModels,
    cliStatus,
    secretProvider,
    setSecretProvider,
    secretValue,
    setSecretValue,
    autonomyMode,
    setAutonomyMode,
    autonomySaving,
    profileState,
    loadAppSettings,
    saveAppSettings,
    loadUserAdapters,
    saveAutonomy,
    loadProjectSkills,
    loadMcpServers,
    saveSecret,
    launchCliLogin,
    testAdapterProfile,
  } = configurationData;

  const selectedIssue = useMemo(
    () => issues.find((issue) => issue.id === selectedIssueId) || issues[0] || null,
    [issues, selectedIssueId],
  );
  const onboardingCompatibilityIssue = useMemo<Issue>(() => ({
    id: 'new-project',
    title: projectName,
    status: 'todo',
    role: 'lead',
    criticality: 'medium',
    metadata_json: JSON.stringify({ profile: newProjectRunProfile, data_class: newProjectDataClass }),
  }), [projectName, newProjectRunProfile, newProjectDataClass]);
  const onboardingLeadOptions = roleModelOptions[
    modelOptionCacheKey(leadAdapterProfileId, 'lead', onboardingCompatibilityIssue)
  ] || [];
  const onboardingLeadBlockReason = onboardingLeadOptions.length > 0
    && onboardingLeadOptions.every((option) => option.selectable === false || option.available === false || option.compatibility?.allowed === false)
    ? onboardingLeadOptions.find((option) => option.compatibility?.allowed === false)?.compatibility?.reason
      || onboardingLeadOptions.find((option) => option.available === false)?.availability_reason
      || 'El perfil no tiene un modelo Lead compatible.'
    : '';

  const selectedComments = comments.filter((comment) => comment.issue_id === selectedIssue?.id);
  const selectedInteractions = interactions.filter((interaction) => interaction.issue_id === selectedIssue?.id);
  const pendingInteractions = interactions.filter((interaction) => interaction.status === 'pending');
  const hasPending = pendingInteractions.length > 0;
  const issuesWithPending = useMemo(
    () => new Set(pendingInteractions.map((i) => i.issue_id).filter(Boolean)),
    [pendingInteractions],
  );
  const doneIssues = issues.filter((issue) => issue.status === 'done').length;
  const activeIssues = issues.filter((issue) => !['done', 'cancelled'].includes(issue.status)).length;
  const latestRun = runs[0] || null;
  // "Despertar" actúa sobre el assignee de la issue seleccionada — hacerlo explícito.
  const wakeTargetId = selectedIssue?.assignee_agent_id || 'role:lead';
  const wakeTargetName = agents.find((a) => a.id === wakeTargetId)?.name
    || wakeTargetId.replace(/^role:/, '').replace(/_/g, ' ');
  const projectDisplayName = workspace ? (workspace.replaceAll('\\', '/').split('/').filter(Boolean).pop() || 'Proyecto') : 'AI Teams';
  const selectedIssueProfile = issueProfile(selectedIssue);
  const { quorum, quorumLoading } = useQuorum({
    workspaceConfigured,
    issueId: selectedIssueId,
    issueProfile: selectedIssueProfile,
    reportError: setError,
  });
  const applyWorkspace = (payload: WorkspacePayload) => {
    const confirmedWorkspace = payload.workspace || '';
    const configured = Boolean(payload.configured && confirmedWorkspace);
    setWorkspace(confirmedWorkspace);
    setWorkspaceDraft(confirmedWorkspace);
    setWorkspaceConfigured(configured);
    setProjectsRoot(payload.projects_root || projectsRoot);
    setWorkspacePath(configured ? confirmedWorkspace : '');
  };

  const resetMissingWorkspace = (payload?: WorkspacePayload) => {
    setWorkspace('');
    setWorkspaceDraft('');
    setWorkspaceConfigured(false);
    setWorkspacePath('');
    setIssues([]);
    setAgents([]);
    setComments([]);
    setInteractions([]);
    setRuns([]);
    setTimelineItems([]);
    setSelectedRun(null);
    setRunEvents([]);
    setPlanDocument(null);
    setError('');
    setLastResult({
      reason: payload?.reason || 'workspace_missing',
      missing_workspace: payload?.missing_workspace || workspace || getWorkspacePath(),
    });
  };

  const loadCatalog = async () => {
    try {
      const response = await apiFetch('/api/tools/catalog');
      if (!response.ok) return;
      const json = (await response.json()) as { catalog?: Record<string, CapabilityEntry> };
      setCapabilityCatalog(json.catalog || {});
    } catch {
      // non-critical — catalog will be empty until available
    }
  };

  const loadPlanDocument = async (issueId: string) => {
    try {
      const response = await apiFetch(`/api/issues/${encodeURIComponent(issueId)}/documents/plan`);
      if (!response.ok) {
        // Fallback: the Lead writes the plan on issue:intake — try that
        if (issueId !== 'issue:intake') {
          const fallback = await apiFetch('/api/issues/issue%3Aintake/documents/plan');
          if (fallback.ok) {
            const json = (await fallback.json()) as { document?: PlanDocument };
            setPlanDocument(json.document || null);
            return;
          }
        }
        setPlanDocument(null);
        return;
      }
      const json = (await response.json()) as { document?: PlanDocument };
      setPlanDocument(json.document || null);
    } catch {
      setPlanDocument(null);
    }
  };

  const loadProjectData = async (issueId = selectedIssueId, typeFilter = timelineTypeFilter) => {
    const params = new URLSearchParams({
      timeline_limit: '300',
      runs_limit: '100',
    });
    if (issueId) params.set('selected_issue_id', issueId);
    if (typeFilter) params.set('timeline_type', typeFilter);
    const response = await apiFetch(`/api/project/state?${params}`);
    const json = (await response.json()) as ProjectStatePayload;
    if (!response.ok) throw new Error(json.detail || `project-state:${response.status}`);

    const nextIssues = json.issues || [];
    const nextSelected = json.selected_issue_id || nextIssues.find((issue) => issue.id === issueId)?.id || nextIssues[0]?.id || '';
    setIssues(nextIssues);
    setAgents(json.agents || []);
    setRuns(json.runs || []);
    setTimelineItems((json.timeline || []).map((item) => ({ ...item, detail: clip(item.detail || '') })));
    setComments(json.comments || []);
    setInteractions(json.interactions || []);
    setPlanDocument(json.plan_document || null);
    if (json.autonomy) setAutonomyMode(json.autonomy);
    setSelectedIssueId(nextSelected);
    void loadChat();
    void loadWsFiles();
    void loadProjectList();
    void loadBudgets();
    void loadCostSummary();
    void loadLoopHealth();
  };

  const loadOrientationMeasurement = async () => {
    try {
      const res = await apiFetch('/api/orientation-measurement');
      if (!res.ok) return;
      const json = (await res.json()) as OrientationMeasurement & { success?: boolean };
      if (json.consent.enabled && !json.consent.current_session_id) {
        const restart = await apiFetch('/api/orientation-measurement/consent', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: true }),
        });
        if (restart.ok) {
          const restarted = (await restart.json()) as { consent?: OrientationMeasurement['consent'] };
          if (restarted.consent) json.consent = restarted.consent;
        }
      }
      setOrientationMeasurement(json);
    } catch { /* medición opcional: nunca bloquea el cockpit */ }
  };

  const changeOrientationConsent = async (enabled: boolean) => {
    if (orientationBusy) return;
    setOrientationBusy(true);
    setError('');
    try {
      const res = await apiFetch('/api/orientation-measurement/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const json = (await res.json()) as { detail?: string };
      if (!res.ok) throw new Error(json.detail || `orientation-consent:${res.status}`);
      if (!enabled) {
        orientationSessionProgressRef.current = { activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false };
      }
      await loadOrientationMeasurement();
    } catch (consentError) {
      setError(consentError instanceof Error ? consentError.message : 'orientation_consent_failed');
    } finally {
      setOrientationBusy(false);
    }
  };

  const eraseOrientationMeasurement = async () => {
    if (orientationBusy || !window.confirm('¿Borrar todas las sesiones y eventos locales de orientación de este proyecto?')) return;
    setOrientationBusy(true);
    setError('');
    try {
      const res = await apiFetch('/api/orientation-measurement', { method: 'DELETE' });
      const json = (await res.json()) as { detail?: string };
      if (!res.ok) throw new Error(json.detail || `orientation-delete:${res.status}`);
      orientationSessionProgressRef.current = { activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false };
      await loadOrientationMeasurement();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'orientation_delete_failed');
    } finally {
      setOrientationBusy(false);
    }
  };

  const recordOrientationEvent = async (
    flow: 'inbox' | 'profile_selection' | 'accepted_plan_to_task',
    event: 'flow_started' | 'flow_completed' | 'flow_abandoned' | 'profile_selected' | 'ui_error',
    profile?: string,
  ) => {
    if (!orientationConsentRef.current.enabled || !orientationConsentRef.current.sessionId) return;
    const progress = orientationSessionProgressRef.current;
    if (event === 'flow_started') progress.activeFlows.add(flow);
    if (event === 'flow_completed') {
      progress.activeFlows.delete(flow);
      progress.completedAnyFlow = true;
    }
    if (event === 'flow_abandoned') {
      progress.activeFlows.delete(flow);
      progress.abandonedAnyFlow = true;
    }
    try {
      const response = await apiFetch('/api/orientation-measurement/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ flow, event, ...(profile ? { profile } : {}) }),
      });
      if (!response.ok) return;
    } catch { /* la telemetría local nunca interrumpe una acción del usuario */ }
  };

  const recordProfileOrientation = async (profile: string) => {
    await recordOrientationEvent('profile_selection', 'profile_selected', profile);
    await recordOrientationEvent('profile_selection', 'flow_completed', profile);
  };

  const waitForLeadInit = async () => {
    setProjectInitializing(true);
    const MAX_POLLS = 45; // 45 × 2 s = 90 s max
    for (let i = 0; i < MAX_POLLS; i++) {
      await new Promise<void>((resolve) => setTimeout(resolve, 2000));
      try {
        const res = await apiFetch('/api/issues/issue%3Aintake');
        if (res.ok) {
          const data = (await res.json()) as { issue?: { status?: string } };
          if (data.issue?.status && data.issue.status !== 'todo') {
            await loadProjectData('issue:intake');
            break;
          }
        }
      } catch {
        // ignore transient errors and keep polling
      }
    }
    setProjectInitializing(false);
  };

  const refresh = async () => {
    setLoading(true);
    setError('');
    try {
      let healthResponse: Response;
      try {
        healthResponse = await apiFetch('/api/health');
      } catch {
        setBackendDown(true);
        return;
      }
      setBackendDown(false);
      const [workspaceResponse] = await Promise.all([
        apiFetch('/api/workspace'),
        loadAppSettings(),
      ]);
      const healthJson = (await healthResponse.json()) as HealthPayload;
      const workspaceJson = (await workspaceResponse.json()) as WorkspacePayload;
      if (!healthResponse.ok) throw new Error(`health:${healthResponse.status}`);
      if (!workspaceResponse.ok) throw new Error(workspaceJson.detail || `workspace:${workspaceResponse.status}`);
      setHealth(healthJson);
      applyWorkspace(workspaceJson);
      void loadCatalog();
      void loadUserAdapters();
      if (workspaceJson.configured) {
        try {
          await loadProjectData();
        } catch (projectError) {
          const message = projectError instanceof Error ? projectError.message : '';
          if (message.includes('unable to open database') || message.includes('Schema not available')) {
            resetMissingWorkspace(workspaceJson);
            return;
          }
          throw projectError;
        }
      }
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : 'refresh_failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Deferir al siguiente task evita una actualización en cascada durante el
    // commit inicial y mantiene `refresh` reutilizable por los reintentos.
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cerrar el modal de configuración de agente con Escape.
  useEffect(() => {
    if (!configModalAgent) return undefined;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        setConfigModalAgent(null);
        setAgentDraft({});
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [configModalAgent]);

  // Backend caído: reintento automático cada 5 s hasta reconectar.
  useEffect(() => {
    if (!backendDown) return undefined;
    const id = setInterval(() => {
      void refresh();
    }, 5_000);
    return () => clearInterval(id);
    // refresh es estable a efectos prácticos; mismo idiom que los demás intervalos
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendDown]);

  // Shared poll entry for both intervals: while a /api/project/state request
  // is still pending, later ticks are dropped instead of stacking requests
  // (the 20 s and 2 s intervals otherwise fire concurrently during runs).
  const pollProjectData = async (issueId: string, typeFilter: TimelineType | '') => {
    if (projectStatePollBusy.current) return;
    projectStatePollBusy.current = true;
    try {
      await loadProjectData(issueId, typeFilter);
    } catch {
      // transient poll errors are ignored; next tick retries
    } finally {
      projectStatePollBusy.current = false;
    }
  };

  // Auto-refresh every 20 s when a project is open (idle baseline)
  useEffect(() => {
    if (!workspaceConfigured) return undefined;
    const id = setInterval(() => {
      void pollProjectData(selectedIssueId, timelineTypeFilter);
    }, 20_000);
    return () => clearInterval(id);
    // deps intentionally limited: restart interval only when project or filter changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceConfigured, selectedIssueId, timelineTypeFilter]);

  // Fast-poll every 2 s while there are active (running/queued) runs.
  // Stops automatically once all runs reach a terminal state.
  const hasActiveRun = runs.some((r) => r.status === 'running' || r.status === 'queued');
  useEffect(() => {
    if (!workspaceConfigured || !hasActiveRun) return undefined;
    const id = setInterval(() => {
      void pollProjectData(selectedIssueId, timelineTypeFilter);
    }, 2_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceConfigured, hasActiveRun, selectedIssueId, timelineTypeFilter]);

  async function loadChat() {
    try {
      const res = await apiFetch('/api/chat?limit=120');
      if (!res.ok) return;
      const json = (await res.json()) as { messages?: ChatMessage[] };
      setChatMessages(json.messages || []);
    } catch { /* ignore */ }
  }

  const scrollChatToBottom = () => {
    const el = chatFeedRef.current;
    // instant: the feed has scroll-behavior smooth in CSS, and a smooth
    // animation gets cancelled by the re-render this click triggers.
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'instant' });
    chatStickToBottomRef.current = true;
    setChatJumpVisible(false);
  };

  const handleChatScroll = () => {
    const el = chatFeedRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    chatStickToBottomRef.current = atBottom;
    setChatJumpVisible(!atBottom);
  };

  // Auto-scroll on new messages ONLY while stuck to the bottom — if the user
  // scrolled up to read something, the feed stays put (the jump button brings
  // them back). Entering the chat tab always starts at the bottom.
  useEffect(() => {
    if (viewMode !== 'chat') return;
    // requestAnimationFrame so the panel is painted before we scroll
    requestAnimationFrame(() => {
      if (chatStickToBottomRef.current) scrollChatToBottom();
    });
  }, [chatMessages, viewMode]);

  useEffect(() => {
    if (viewMode === 'chat') chatStickToBottomRef.current = true;
  }, [viewMode]);

  useEffect(() => {
    orientationConsentRef.current = {
      enabled: Boolean(orientationMeasurement?.consent.enabled),
      sessionId: orientationMeasurement?.consent.current_session_id || null,
    };
  }, [orientationMeasurement]);

  useEffect(() => {
    if (!workspaceConfigured || !workspace || orientationWorkspaceRef.current === workspace) return;
    orientationWorkspaceRef.current = workspace;
    orientationSessionProgressRef.current = { activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false };
    void loadOrientationMeasurement();
    // Se reinicia únicamente al cambiar de proyecto; no en cada poll del cockpit.
  }, [workspaceConfigured, workspace]);

  useEffect(() => {
    const finishObservedSession = () => {
      if (!orientationConsentRef.current.enabled || !orientationConsentRef.current.sessionId) return;
      const progress = orientationSessionProgressRef.current;
      if (progress.activeFlows.size === 0 && !progress.completedAnyFlow && !progress.abandonedAnyFlow) return;
      const status = progress.activeFlows.size > 0 || progress.abandonedAnyFlow ? 'abandoned' : 'completed';
      orientationSessionProgressRef.current = { activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false };
      const ending = apiFetch('/api/orientation-measurement/session/end', {
        method: 'POST',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      }).catch(() => undefined);
      const trackedEnding = ending.finally(() => {
        if (orientationSessionEndPromiseRef.current === trackedEnding) {
          orientationSessionEndPromiseRef.current = null;
        }
      });
      orientationSessionEndPromiseRef.current = trackedEnding;
    };
    const resumeObservedSession = async () => {
      await orientationSessionEndPromiseRef.current;
      orientationSessionProgressRef.current = { activeFlows: new Set(), completedAnyFlow: false, abandonedAnyFlow: false };
      if (orientationWorkspaceRef.current) await loadOrientationMeasurement();
    };
    const resumeObservedSessionListener = () => { void resumeObservedSession(); };
    window.addEventListener('pagehide', finishObservedSession);
    window.addEventListener('pageshow', resumeObservedSessionListener);
    return () => {
      window.removeEventListener('pagehide', finishObservedSession);
      window.removeEventListener('pageshow', resumeObservedSessionListener);
    };
  }, []);

  // Lazy-load project skills + MCP servers only when the Config tab is open.
  useEffect(() => {
    if (viewMode !== 'config' || !workspaceConfigured) return undefined;
    const timer = window.setTimeout(() => {
      void loadProjectSkills();
      void loadMcpServers();
      void loadOrientationMeasurement();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadMcpServers, loadProjectSkills, viewMode, workspaceConfigured]);

  async function loadWsFiles() {
    try {
      const res = await apiFetch('/api/workspace/files');
      if (!res.ok) return;
      const json = (await res.json()) as { files?: Array<{ path: string; size_bytes: number; mime: string }> };
      setWsFiles(json.files || []);
    } catch { /* ignore */ }
  }

  async function loadLoopHealth() {
    try {
      const res = await apiFetch('/api/loop-health');
      if (!res.ok) return;
      const json = (await res.json()) as LoopHealth & { success?: boolean };
      setLoopHealth(json);
    } catch { /* non-critical — ignore */ }
  }

  const loadWsFile = async (path: string) => {
    setWsFileLoading(true);
    setWsSelectedFile(path);
    setWsFileContent(null);
    try {
      const res = await apiFetch(`/api/workspace/files/${encodeURIComponent(path)}`);
      if (!res.ok) { setWsFileContent('(no se puede leer este archivo)'); return; }
      const json = (await res.json()) as { content?: string; truncated?: boolean };
      setWsFileContent((json.content || '') + (json.truncated ? '\n\n… [truncado]' : ''));
    } catch { setWsFileContent('(error al leer el archivo)'); }
    finally { setWsFileLoading(false); }
  };

  async function loadProjectList() {
    try {
      const res = await apiFetch('/api/projects');
      if (!res.ok) return;
      const json = (await res.json()) as { projects?: Array<{ name: string; path: string; current: boolean }> };
      setProjectList(json.projects || []);
    } catch { /* ignore */ }
  }

  async function loadBudgets() {
    try {
      const res = await apiFetch('/api/budget');
      if (!res.ok) return;
      const json = (await res.json()) as { budgets?: BudgetInfo[] };
      setBudgets((json.budgets || []).filter((b) => b.budget_monthly_cents > 0));
    } catch { /* ignore */ }
  }

  async function loadCostSummary() {
    try {
      const res = await apiFetch('/api/costs/summary');
      if (!res.ok) return;
      const json = (await res.json()) as CostSummary & { success?: boolean };
      setCostSummary(json);
    } catch { /* ignore */ }
  }

  const switchProject = async (path: string) => {
    setProjectListOpen(false);
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch('/api/workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      const json = (await res.json()) as WorkspacePayload;
      if (!res.ok) { setError(String(json.detail || 'Error al cambiar de proyecto')); return; }
      applyWorkspace({ ...json, configured: true });
      await loadProjectData(undefined, '');
    } catch (err) {
      setError(String(err));
    } finally { setLoading(false); }
  };

  const sendChatMessage = async () => {
    const body = chatDraft.trim();
    if (!body) return;
    setChatSending(true);
    setChatDraft('');
    // Sending re-sticks the feed so the user sees their own message land.
    chatStickToBottomRef.current = true;
    try {
      const issueId = selectedIssue?.id || 'issue:intake';
      const res = await apiFetch('/api/chat/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body, issue_id: issueId }),
      });
      if (res.ok) {
        await loadChat();
        // Fire-and-forget: the backend /api/chat/message already enqueued the Lead
        // wakeup — we only need to trigger control plane execution here.
        // (Previously a redundant /api/wakeup-requests call here caused the Lead
        // to run twice per chat message — removed.)
        void runControlPlane(null, 1)
          .then(() => Promise.all([
            loadProjectData(selectedIssue?.id ?? selectedIssueId, timelineTypeFilter),
            loadChat(),
          ]))
          .catch(() => {/* background run failure is non-fatal */});
      }
    } catch { /* ignore */ }
    finally { setChatSending(false); }
  };

  // Cache the canonical contextual projection only for the profile currently
  // assigned to each visible member. ModelRoleSelector owns the global list.
  useEffect(() => {
    pendingInteractions.forEach((interaction) => {
      if (interaction.kind !== 'suggest_tasks') return;
      const rawPayload = (interaction as Interaction & { payload?: Record<string, unknown> }).payload;
      const team: ProposedTeamMember[] = (hiringDrafts[interaction.id] ?? (rawPayload?.proposed_team as ProposedTeamMember[])) || [];
      team.forEach((member) => {
        const role = member.role || '';
        const interactionIssue = issues.find((item) => item.id === interaction.issue_id) || selectedIssue;
        const profileId = String(member.adapter_profile_id || member.adapter_config?.profile_id || '');
        if (role && profileId) void fetchRoleModelOptions(profileId, role, interactionIssue);
      });
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInteractions, hiringDrafts, adapterProfiles, issues, selectedIssue]);

  useEffect(() => {
    if (!configModalAgent?.role) return;
    const profileId = String(configModalAgent.adapter_config?.profile_id || '');
    if (profileId) void fetchRoleModelOptions(profileId, configModalAgent.role, selectedIssue);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configModalAgent?.id, adapterProfiles, selectedIssue]);

  useEffect(() => {
    if (leadAdapterProfileId) {
      void fetchRoleModelOptions(leadAdapterProfileId, 'lead', onboardingCompatibilityIssue);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadAdapterProfileId, newProjectRunProfile, newProjectDataClass]);

  const createProject = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/projects/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: projectName,
          initial_task: initialTask,
          objective_kind: newProjectObjectiveKind,
          adapter_profile_ids: selectedProjectAdapterIds,
          lead_adapter_profile_id: leadAdapterProfileId,
          lead_model: leadModel,
          lead_candidate_id: leadCandidateId,
          run_profile: newProjectRunProfile,
          data_class: newProjectDataClass,
        }),
      });
      const json = (await response.json()) as WorkspacePayload;
      if (!response.ok) throw new Error(json.detail || `project:${response.status}`);
      applyWorkspace(json);
      setLastResult(json);
      await loadProjectData('issue:intake');
      // Show loading overlay until the Lead's first run starts (≤30 s with the
      // workspace-aware HeartbeatLoop; falls back gracefully after 90 s).
      void waitForLeadInit();
    } catch (projectError) {
      setError(projectError instanceof Error ? projectError.message : 'project_create_failed');
    } finally {
      setLoading(false);
    }
  };

  const deleteProject = async () => {
    setLoading(true);
    setError('');
    try {
      const body = JSON.stringify({ confirmation: deleteConfirm });
      let response: Response;
      try {
        response = await apiFetch('/api/projects/current/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
        });
      } catch {
        response = await apiFetch('/api/projects/current', {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body,
        });
      }
      const json = (await response.json()) as WorkspacePayload & { deleted?: boolean };
      if (!response.ok) throw new Error(json.detail || `delete:${response.status}`);
      setDeleteConfirm('');
      resetMissingWorkspace({ reason: json.deleted ? 'project_deleted' : 'workspace_missing' });
      await refresh();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : 'project_delete_failed');
    } finally {
      setLoading(false);
    }
  };

  const saveWorkspace = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: workspaceDraft }),
      });
      const json = (await response.json()) as WorkspacePayload;
      if (!response.ok) throw new Error(json.detail || `workspace:${response.status}`);
      applyWorkspace(json);
      setLastResult(json);
      await loadProjectData('issue:intake');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'workspace_save_failed');
    } finally {
      setLoading(false);
    }
  };

  const runControlPlane = async (agentId?: string | null, maxRuns: number = 20) => {
    const runResponse = await apiFetch('/api/control-plane/run-once', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId || undefined, max_runs: maxRuns }),
    });
    const runJson = await runResponse.json();
    if (!runResponse.ok) throw new Error(runJson.detail || `run_once:${runResponse.status}`);
    return runJson;
  };

  const wakeLead = async () => {
    const issueId = selectedIssue?.id || 'issue:intake';
    setLoading(true);
    setError('');
    try {
      const enqueueResponse = await apiFetch('/api/wakeup-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: selectedIssue?.assignee_agent_id || 'role:lead',
          source: 'manual',
          reason: 'manual',
          payload: { requested_from: 'frontend_project_cockpit', issue_id: issueId },
        }),
      });
      const enqueueJson = await enqueueResponse.json();
      if (!enqueueResponse.ok) throw new Error(enqueueJson.detail || `wakeup:${enqueueResponse.status}`);
      const runJson = await runControlPlane();
      setLastResult({ enqueue: enqueueJson, run_once: runJson });
      await loadProjectData(issueId);
    } catch (wakeupError) {
      setError(wakeupError instanceof Error ? wakeupError.message : 'wakeup_failed');
    } finally {
      setLoading(false);
    }
  };

  const addComment = async () => {
    const issueId = selectedIssue?.id;
    if (!issueId || !commentDraft.trim()) return;
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch(`/api/issues/${encodeURIComponent(issueId)}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          body: commentDraft.trim(),
          author_user_id: 'user',
          metadata: { source: 'frontend_project_cockpit' },
        }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `comment:${response.status}`);
      setCommentDraft('');
      setLastResult(json);
      await loadProjectData(issueId);
    } catch (commentError) {
      setError(commentError instanceof Error ? commentError.message : 'comment_failed');
    } finally {
      setLoading(false);
    }
  };

  const createTask = async () => {
    const task = newTaskDraft.trim();
    if (!task) return;
    const observedPlanFlow = Boolean(
      pendingPlanRef
      && orientationSessionProgressRef.current.activeFlows.has('accepted_plan_to_task')
    );
    const title = task.split(/\r?\n/).find((line) => line.trim())?.trim().slice(0, 160) || 'Nueva tarea';
    setLoading(true);
    setError('');
    try {
      const issueResponse = await apiFetch('/api/issues', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          description: task,
          status: 'todo',
          role: 'lead',
          complexity: 'medium',
          objective_kind: newTaskObjectiveKind,
          assignee_agent_id: 'role:lead',
          metadata: {
            source: 'frontend_project_cockpit',
            wake_reason: 'new_task',
            profile: newTaskProfile,
            // El plan aceptado viaja como referencia a su revisión (recibo);
            // el backend lo entrega en cada wake como inherited_plan.
            ...(pendingPlanRef ? { source_plan_revision_id: pendingPlanRef.revisionId } : {}),
          },
        }),
      });
      const issueJson = await issueResponse.json();
      if (!issueResponse.ok) throw new Error(issueJson.detail || `issue:${issueResponse.status}`);
      const issue = issueJson.issue as Issue;

      const commentResponse = await apiFetch(`/api/issues/${encodeURIComponent(issue.id)}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          body: task,
          author_user_id: 'user',
          metadata: { source: 'frontend_new_task' },
        }),
      });
      const commentJson = await commentResponse.json();
      if (!commentResponse.ok) throw new Error(commentJson.detail || `comment:${commentResponse.status}`);

      const wakeupResponse = await apiFetch('/api/wakeup-requests', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: 'role:lead',
          source: 'manual',
          reason: 'new_task',
          payload: {
            issue_id: issue.id,
            wake_reason: 'new_task',
            requested_from: 'frontend_project_cockpit',
          },
          idempotency_key: `new-task:${issue.id}:role:lead`,
        }),
      });
      const wakeupJson = await wakeupResponse.json();
      if (!wakeupResponse.ok) throw new Error(wakeupJson.detail || `wakeup:${wakeupResponse.status}`);

      const runOnceJson = await runControlPlane();
      setNewTaskDraft('');
      setNewTaskObjectiveKind('auto');
      setPendingPlanRef(null);
      setSelectedIssueId(issue.id);
      setViewMode('chat');
      setLastResult({ issue: issueJson, comment: commentJson, wakeup: wakeupJson, run_once: runOnceJson });
      if (observedPlanFlow) {
        void recordOrientationEvent('accepted_plan_to_task', 'flow_completed', newTaskProfile);
      }
      await loadProjectData(issue.id);
    } catch (taskError) {
      if (observedPlanFlow) void recordOrientationEvent('accepted_plan_to_task', 'ui_error', newTaskProfile);
      setError(taskError instanceof Error ? taskError.message : 'task_create_failed');
    } finally {
      setLoading(false);
    }
  };

  const saveAgent = async (agentId: string) => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch(`/api/agents/${encodeURIComponent(agentId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...agentDraft,
          issue_id: selectedIssue?.id || '',
          run_profile: issueCompatibilityContext(selectedIssue).runProfile,
          criticality: issueCompatibilityContext(selectedIssue).criticality,
          data_class: issueCompatibilityContext(selectedIssue).dataClass,
          required_capabilities: (
            agentDraft.capabilities
            || configModalAgent?.capabilities
            || []
          ),
        }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(apiDetailText(json.detail, `agent:${response.status}`));
      setEditingAgentId(null);
      setAgentDraft({});
      setConfigModalAgent(null);
      await loadProjectData(selectedIssueId);
    } catch (agentError) {
      setError(agentError instanceof Error ? agentError.message : 'agent_save_failed');
    } finally {
      setLoading(false);
    }
  };

  const toggleDraftCapability = (cap: string) => {
    setAgentDraft((d) => {
      const current: string[] = Array.isArray(d.capabilities) ? d.capabilities : [];
      const next = current.includes(cap) ? current.filter((c) => c !== cap) : [...current, cap];
      return { ...d, capabilities: next };
    });
  };

  const reconcileTeam = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/agents/reconcile', { method: 'POST' });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `reconcile:${response.status}`);
      setLastResult(json);
      await loadProjectData(selectedIssueId);
    } catch (recErr) {
      setError(recErr instanceof Error ? recErr.message : 'reconcile_failed');
    } finally {
      setLoading(false);
    }
  };

  // Shared agent edit form body — used by both sidebar (renderOrgNode) and team panel (renderAgentCard)
  function agentFormJSX(agent: Agent): React.ReactNode {
    const draftCaps: string[] = Array.isArray(agentDraft.capabilities)
      ? agentDraft.capabilities
      : (agent.capabilities ?? []);
    const catalogKeys = Object.keys(capabilityCatalog);
    const currentProfileId = String(
      (agentDraft.adapter_config as Record<string,unknown>)?.profile_id
      ?? (agent.adapter_config as Record<string,unknown>)?.profile_id
      ?? '',
    );
    const selectedProfile = adapterProfiles.find((p) => p.id === currentProfileId);
    const currentAdapterType = String(agentDraft.adapter_type ?? agent.adapter_type ?? 'manual');
    const adapterDefaultProfile = adapterProfiles.find((p) => p.adapter_type === currentAdapterType);
    const activeModelProfile = selectedProfile ?? adapterDefaultProfile;
    const roleOptionsKey = modelOptionCacheKey(currentProfileId, agent.role || '', selectedIssue);
    const modelOptions = roleModelOptions[roleOptionsKey] ?? activeModelProfile?.model_options ?? [];
    const currentModel = String(
      (agentDraft.adapter_config as Record<string,unknown>)?.model
      ?? (agent.adapter_config as Record<string,unknown>)?.model
      ?? '',
    );
    const currentModelOption = modelOptions.find((option) => option.value === currentModel);
    const assignmentBlocked = currentModelOption?.selectable === false
      || currentModelOption?.available === false
      || currentModelOption?.compatibility?.allowed === false;
    const roleDef = ROLE_CATALOG[agent.role ?? ''];
    return (
      <div className="agent-form-v2">
        {/* ── Identidad ── */}
        <div className="agent-form-section">
          <div className="agent-form-section-title">Identidad</div>
          <div className="agent-form-row">
            <div className="agent-form-field">
              <label className="agent-form-label">
                Nombre <InfoTip tip={FIELD_TIPS.name} />
              </label>
              <input
                className="agent-form-input"
                value={String(agentDraft.name ?? agent.name)}
                onChange={(e) => setAgentDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div className="agent-form-field">
              <label className="agent-form-label">
                Rol <InfoTip tip={roleDef ? `${roleDef.desc} · ${roleDef.when}` : 'Rol del agente en el equipo.'} />
              </label>
              <div className="agent-form-static">
                <code>{agent.role ?? '—'}</code>
                <span className={`tier-badge tier${agentTier(agent.seniority)}`}>{agent.seniority ?? '—'}</span>
              </div>
            </div>
          </div>
        </div>

        {/* ── Adapter ── */}
        <div className="agent-form-section">
          <div className="agent-form-section-title">Adapter</div>

          {/* Selector canónico modelo + adapter, compartido con hiring. */}
          <div className="agent-form-field">
            <label className="agent-form-label">
              Modelo + adapter <InfoTip tip={`${FIELD_TIPS.adapter} ${FIELD_TIPS.model}`} wide />
            </label>
            <ModelRoleSelector
              role={agent.role || ''}
              issueId={selectedIssue?.id || ''}
              profileId={currentProfileId}
              model={currentModel}
              {...issueCompatibilityContext(selectedIssue)}
              requiredCapabilities={draftCaps}
              onChange={({ profileId, model, candidateId }) => {
                const profile = adapterProfiles.find((p) => p.id === profileId);
                setAgentDraft((d) => ({
                  ...d,
                  adapter_type: profile?.adapter_type ?? d.adapter_type ?? agent.adapter_type ?? 'manual',
                  adapter_config: {
                    ...(d.adapter_config || {}),
                    profile_id: profileId,
                    model,
                    selection_intent: {
                      schema_version: 'model_selection_intent_v1',
                      mode: 'owner_explicit',
                      source: 'model_role_selector',
                      candidate_id: candidateId,
                    },
                  },
                }));
              }}
            />

            {/* Derived info: show technical type + channel as read-only chips */}
            {selectedProfile && (
              <div className="adapter-derived-row">
                <span className="adapter-derived-chip">tipo: {selectedProfile.adapter_type}</span>
                <span className="adapter-derived-chip">canal: {selectedProfile.channel}</span>
                {selectedProfile.provider && (
                  <span className="adapter-derived-chip">proveedor: {selectedProfile.provider}</span>
                )}
              </div>
            )}
          </div>

          {/* Estado técnico del perfil; la compatibilidad viene del selector contextual. */}
          <div className="agent-form-field">
            {activeModelProfile?.model_catalog?.status === 'cli_update_required' && (
              <small className="field-warning">
                Codex CLI {activeModelProfile.model_catalog.installed_version || '?'} no puede usar el catálogo {activeModelProfile.model_catalog.catalog_client_version || '?'}; actualiza el CLI y vuelve a probar el adapter.
              </small>
            )}
            {currentModel && (currentModelOption?.selectable === false || currentModelOption?.available === false) && (
              <small className="field-warning">
                El modelo guardado no está disponible: {currentModelOption.availability_reason || 'health no demostrado'}.
              </small>
            )}
            {currentModel && currentModelOption?.compatibility?.allowed === false && (
              <small className="field-warning">
                Asignación bloqueada: {currentModelOption.compatibility.reason || currentModelOption.compatibility.code}.
              </small>
            )}
            {selectedProfile && currentModel && (
              <button
                type="button"
                className="secondary-button"
                disabled={loading}
                onClick={() => void testAdapterProfile(selectedProfile.id, currentModel)}
              >
                Probar este modelo
              </button>
            )}
          </div>
        </div>

        {/* ── Capacidades ── */}
        {catalogKeys.length > 0 && (
          <div className="agent-form-section">
            <div className="agent-form-section-title">
              Capacidades <InfoTip tip={FIELD_TIPS.capabilities} wide />
            </div>
            <div className="cap-chips">
              {catalogKeys.map((cap) => (
                <button
                  key={cap}
                  className={`cap-chip${draftCaps.includes(cap) ? ' active' : ''}`}
                  onClick={() => toggleDraftCapability(cap)}
                  title={capabilityCatalog[cap]?.description}
                  type="button"
                >
                  {capabilityCatalog[cap]?.label ?? cap}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Límites ── */}
        <div className="agent-form-section agent-form-footer">
          <div className="agent-form-field agent-form-field-inline">
            <label className="agent-form-label">
              Budget/mes <InfoTip tip={FIELD_TIPS.budget} />
            </label>
            <div className="agent-form-budget-row">
              <input
                className="agent-form-input agent-form-budget"
                type="number"
                value={agentDraft.budget_monthly_cents ?? agent.budget_monthly_cents ?? 0}
                onChange={(e) => setAgentDraft((d) => ({ ...d, budget_monthly_cents: Number(e.target.value) }))}
              />
              <span className="agent-form-budget-unit">¢/mes</span>
            </div>
          </div>
          <div className="agent-form-actions">
            <button className="secondary-button" onClick={() => { setEditingAgentId(null); setAgentDraft({}); setConfigModalAgent(null); }}>Cancelar</button>
            <button onClick={() => void saveAgent(agent.id)} disabled={loading || assignmentBlocked}>Guardar cambios</button>
          </div>
        </div>
      </div>
    );
  }

  // Card renderer — full team tab version (opens config modal on ⚙)
  function renderAgentCard(agent: Agent): React.ReactNode {
    const tier = agentTier(agent.seniority);
    const model = String((agent.adapter_config as Record<string,unknown>)?.model ?? '');
    const profileId = String((agent.adapter_config as Record<string,unknown>)?.profile_id ?? '');
    const caps = agent.capabilities ?? [];
    const statusClass = agent.status === 'running' ? 'running'
      : agent.status === 'blocked' ? 'blocked'
      : 'idle';
    const roleDef = ROLE_CATALOG[agent.role ?? ''];
    // Resolve adapter label from profile (preferred) or fall back to raw adapter_type
    const profileDef = adapterProfiles.find((p) => p.id === profileId);
    const adapterLabel = profileDef?.label ?? agent.adapter_type ?? 'sin adapter';
    const adapterConnected = profileDef ? profileState(profileDef).connected : null;
    return (
      <div key={agent.id} className={`agent-card tier${tier}`}>
        {/* ── Card header ── */}
        <div className="agent-card-header-row">
          <span className={`agent-status-dot status-${statusClass}`} title={agent.status ?? 'idle'} />
          <span className="agent-card-name">{agent.name}</span>
          <span className={`tier-badge tier${tier}`}>T{tier}</span>
          <button
            className="agent-card-edit-btn"
            onClick={() => { setConfigModalAgent(agent); setAgentDraft({ capabilities: agent.capabilities ?? [] }); }}
            title="Configurar agente"
          >
            ⚙
          </button>
        </div>

        {/* ── Role row ── */}
        <div className="agent-card-role-row">
          <code className="agent-card-role-code">{agent.role ?? '—'}</code>
          {roleDef && <InfoTip tip={roleDef.desc} wide />}
          <span className="agent-card-seniority">{agent.seniority ?? '—'}</span>
        </div>

        {/* ── Adapter summary (label + model) ── */}
        <div className="agent-card-adapter-row">
          {adapterConnected !== null && (
            <span
              className={`adapter-dot${adapterConnected ? ' connected' : ''}`}
              title={adapterConnected ? 'conectado' : 'sin conectar'}
            />
          )}
          <span className="agent-card-adapter-type">{adapterLabel}</span>
          {model && <span className="agent-card-model">{model}</span>}
        </div>

        {/* ── Caps ── */}
        {caps.length > 0 && (
          <div className="agent-card-caps">
            {caps.map((c) => (
              <span key={c} className="cap-chip">{capabilityCatalog[c]?.label ?? c}</span>
            ))}
          </div>
        )}
      </div>
    );
  }

  function hireCatalogRole(roleId: string, roleDef: RoleDef) {
    setCatalogHire({ roleId, roleDef, profileId: '', model: '', candidateId: '' });
  }

  async function confirmCatalogHire() {
    if (!catalogHire?.profileId || !catalogHire.model || !catalogHire.candidateId) return;
    setLoading(true);
    try {
      const profile = adapterProfiles.find((item) => item.id === catalogHire.profileId);
      const context = issueCompatibilityContext(selectedIssue);
      if (catalogHire.roleId === 'role:quorum_auditor_1' || catalogHire.roleId === 'role:quorum_auditor_2') {
        const response = await apiFetch('/api/agents/quorum/reconcile', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            agent_id: catalogHire.roleId,
            profile_id: catalogHire.profileId,
            model: catalogHire.model,
            candidate_id: catalogHire.candidateId,
            issue_id: selectedIssue?.id || '',
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `quorum_hire:${response.status}`);
      } else {
        const lead = agents.find((agent) => agent.role === 'lead' || agent.role === 'role:lead');
        const response = await apiFetch('/api/agents', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            role: catalogHire.roleId.replace(/^role:/, ''),
            name: catalogHire.roleDef.title,
            seniority: catalogHire.roleDef.seniority,
            adapter_type: profile?.adapter_type || 'manual',
            adapter_config: {
              profile_id: catalogHire.profileId,
              model: catalogHire.model,
              selection_intent: {
                schema_version: 'model_selection_intent_v1',
                mode: 'owner_explicit',
                source: 'model_role_selector',
                candidate_id: catalogHire.candidateId,
              },
            },
            supervisor_agent_id: lead?.id || null,
            issue_id: selectedIssue?.id || '',
            run_profile: context.runProfile,
            criticality: context.criticality,
            data_class: context.dataClass,
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `agent_hire:${response.status}`);
      }
      setCatalogHire(null);
      await loadProjectData(selectedIssueId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'hire_failed');
    } finally {
      setLoading(false);
    }
  }

  const resolveInteraction = async (
    interaction: Interaction,
    intent: 'accept' | 'changes_requested' | 'reject',
    note?: string,
  ) => {
    setLoading(true);
    setError('');
    try {
      const hiringTeam = hiringDrafts[interaction.id];
      // ask_user_questions only supports answer/cancel at the API — map the
      // accept/reject buttons onto the vocabulary each kind actually allows.
      const action = interaction.kind === 'ask_user_questions'
        ? (intent === 'accept' ? 'answer' : 'cancel')
        : intent;
      const body: Record<string, unknown> = { action, resolved_by_user_id: 'user' };
      if (intent === 'changes_requested' && interaction.kind === 'suggest_tasks') {
        body.resolution_data = { user_note: note?.trim() || '' };
      } else if (intent === 'accept' && hiringTeam && interaction.kind === 'suggest_tasks') {
        body.resolution_data = { proposed_team: hiringTeam };
      } else if (intent === 'accept' && fallbackSelections[interaction.id]) {
        body.resolution_data = { model_selection: fallbackSelections[interaction.id] };
      } else if (intent === 'accept' && note && note.trim() && interaction.kind !== 'suggest_tasks') {
        // Carry the user's free-text answer so the Lead can read it from result.resolution_data.user_note
        body.resolution_data = { user_note: note.trim() };
      }
      const response = await apiFetch(`/api/interactions/${encodeURIComponent(interaction.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(apiDetailText(json.detail, `interaction:${response.status}`));
      // Clear the note after successful submission
      setInteractionNotes((prev) => { const next = { ...prev }; delete next[interaction.id]; return next; });
      setFallbackSelections((prev) => { const next = { ...prev }; delete next[interaction.id]; return next; });
      const runOnceJson = await runControlPlane();
      setLastResult({ interaction: json, run_once: runOnceJson });
      await loadProjectData(interaction.issue_id || selectedIssueId);
    } catch (interactionError) {
      setError(interactionError instanceof Error ? interactionError.message : 'interaction_failed');
    } finally {
      setLoading(false);
    }
  };

  const toggleProjectAdapter = (profileId: string) => {
    const profile = adapterProfiles.find((item) => item.id === profileId);
    if (!profile || !profileState(profile).selectable) return;
    setSelectedProjectAdapterIds((current) => {
      if (current.includes(profileId)) {
        const next = current.filter((id) => id !== profileId);
        if (leadAdapterProfileId === profileId) {
          setLeadAdapterProfileId('');
          setLeadModel('');
          setLeadCandidateId('');
        }
        return next;
      }
      const next = [...current, profileId];
      return next;
    });
  };

  async function fetchRoleModelOptions(
    profileId: string,
    role: string,
    issue: Issue | null | undefined = selectedIssue,
  ): Promise<RoleModelOption[]> {
    if (!profileId || !role) return [];
    const context = issueCompatibilityContext(issue);
    const key = modelOptionCacheKey(profileId, role, issue);
    if (roleModelOptions[key]) return roleModelOptions[key];
    try {
      const res = await apiFetch('/api/model-catalog/selection', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role,
          issue_id: issue?.id === 'new-project' ? '' : issue?.id || '',
          run_profile: context.runProfile,
          criticality: context.criticality,
          data_class: context.dataClass || 'public',
          required_capabilities: [],
        }),
      });
      if (!res.ok) return [];
      const json = (await res.json()) as {
        default?: { candidate_id?: string | null };
        candidates?: Array<{
          candidate_id: string;
          label?: string;
          identity?: { profile_id?: string; model_id?: string };
          model_metadata?: { tier?: string; price_note?: string };
          states?: Record<string, { value?: boolean }>;
          selection_score?: { score?: number | null };
          contextual_compatibility?: RoleModelOption['compatibility'];
          owner_selectable?: boolean;
          disabled_reason?: string | null;
          selection_reason?: string;
        }>;
      };
      const opts: RoleModelOption[] = (json.candidates || [])
        .filter((candidate) => candidate.identity?.profile_id === profileId)
        .map((candidate) => ({
          value: String(candidate.identity?.model_id || ''),
          label: candidate.label || String(candidate.identity?.model_id || ''),
          recommended: candidate.candidate_id === json.default?.candidate_id,
          fit_reason: candidate.selection_reason,
          role_score: candidate.selection_score?.score ?? undefined,
          tier: candidate.model_metadata?.tier,
          price_note: candidate.model_metadata?.price_note,
          available: candidate.states?.selectable?.value,
          selectable: candidate.owner_selectable === true,
          availability_reason: candidate.disabled_reason || undefined,
          compatibility: candidate.contextual_compatibility,
        }));
      setRoleModelOptions((prev) => ({ ...prev, [key]: opts }));
      return opts;
    } catch {
      return [];
    }
  }

  const updateHiringMemberSelection = (
    interactionId: string,
    team: ProposedTeamMember[],
    idx: number,
    profileId: string,
    model: string,
    candidateId: string,
  ) => {
    const profile = adapterProfiles.find((item) => item.id === profileId);
    const updated = team.map((member, memberIndex) => memberIndex === idx ? {
      ...member,
      adapter_type: profile?.adapter_type || member.adapter_type || 'manual',
      adapter_profile_id: profileId,
      model,
      adapter_config: {
        ...(member.adapter_config || {}),
        profile_id: profileId,
        model,
        selection_intent: {
          schema_version: 'model_selection_intent_v1',
          mode: 'owner_explicit',
          source: 'model_role_selector',
          candidate_id: candidateId,
        },
      },
    } : member);
    setHiringDrafts((drafts) => ({ ...drafts, [interactionId]: updated }));
  };

  const loadRunDetail = async (id: string) => {
    const rid = id.trim();
    if (!rid) return;
    setLoading(true);
    setError('');
    try {
      const [runResponse, eventsResponse] = await Promise.all([
        apiFetch(`/api/runs/${encodeURIComponent(rid)}`),
        apiFetch(`/api/runs/${encodeURIComponent(rid)}/events?limit=200`),
      ]);
      const runJson = (await runResponse.json()) as { run?: Run; detail?: string };
      const eventsJson = (await eventsResponse.json()) as { events?: RunEvent[]; detail?: string };
      if (!runResponse.ok) throw new Error(runJson.detail || `run:${runResponse.status}`);
      setSelectedRun(runJson.run || null);
      setRunEvents(eventsJson.events || []);
      setLastResult(null);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : 'run_lookup_failed');
    } finally {
      setLoading(false);
    }
  };

  // Backend unreachable: honest state instead of the onboarding screen.
  if (backendDown) {
    return (
      <main className="shell start-shell">
        <header className="topbar">
          <div className="topbar-brand">
            <span className="brand-mark">▸</span>
            <span className="brand-name">AI Teams</span>
          </div>
        </header>
        <section className="panel start-panel backend-down-panel">
          <div className="backend-down-icon">
            <AlertCircle size={28} />
          </div>
          <h2 className="backend-down-title">El backend no responde</h2>
          <p className="backend-down-text">
            Tu proyecto y su historial están a salvo en disco. La interfaz no puede
            mostrarlos hasta reconectar con el backend local.
          </p>
          <div className="backend-down-diag">
            <div><span className="diag-bad">✕</span> <code>{API_BASE}</code> — sin respuesta</div>
            <div><span className="diag-hint">→</span> Arranca el IDE con <code>start_ide.bat</code> (backend + frontend)</div>
            <div><span className="diag-hint">→</span> Logs: <code>runtime\ide_logs\backend.err.log</code></div>
          </div>
          <div className="actions">
            <button onClick={() => void refresh()} disabled={loading}>
              <RefreshCcw size={15} className={loading ? 'spin' : ''} />
              Reintentar conexión
            </button>
            <span className="backend-down-auto">Reintentando automáticamente cada 5 s…</span>
          </div>
        </section>
      </main>
    );
  }

  // First-run: no projects root configured yet → show setup screen
  if (!settingsConfigured) {
    return (
      <main className="shell start-shell">
        <header className="topbar">
          <div className="topbar-brand">
            <span className="brand-mark">▸</span>
            <span className="brand-name">AI Teams — Configuración inicial</span>
          </div>
        </header>
        {error ? <div className="banner error">{error}</div> : null}
        <section className="panel start-panel">
          <div className="panel-title">
            <FolderOpen size={18} />
            Carpeta de proyectos
          </div>
          <p className="hint">
            AI Teams guarda cada proyecto como una subcarpeta. Elige la carpeta raíz donde se crearán.
            Puedes cambiarlo más adelante en la pestaña Configuración.
          </p>
          <label>
            Ruta de la carpeta
            <input
              placeholder="Ej: C:\Users\Tu\Proyectos  o  /home/tu/projects"
              value={settingsDraft}
              onChange={(ev) => setSettingsDraft(ev.target.value)}
            />
          </label>
          <div className="actions">
            <button
              onClick={() => void saveAppSettings().then(() => void refresh())}
              disabled={loading || !settingsDraft.trim()}
            >
              Guardar y continuar
            </button>
          </div>
        </section>
      </main>
    );
  }

  if (!workspaceConfigured) {
    return (
      <main className="shell start-shell">
        <header className="topbar">
          <div className="topbar-brand">
            <span className="brand-mark">▸</span>
            <span className="brand-name">AI Teams — Nuevo proyecto</span>
          </div>
          <button className="icon-button" onClick={() => void refresh()} disabled={loading} title="Refrescar">
            <RefreshCcw size={18} className={loading ? 'spin' : ''} />
          </button>
        </header>

        {error ? <div className="banner error">{error}</div> : null}

        <section className="panel start-panel">
          <div className="panel-title">
            <FolderPlus size={18} />
            Primera apertura
          </div>
          <label>
            Nombre del proyecto
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          </label>
          <label>
            Tarea inicial para el Lead
            <textarea
              placeholder="Ej: Construye una app de reporting para..."
              value={initialTask}
              onChange={(event) => setInitialTask(event.target.value)}
            />
          </label>
          <label>
            Tipo de objetivo
            <select
              value={newProjectObjectiveKind}
              onChange={(event) => setNewProjectObjectiveKind(event.target.value as ObjectiveKind)}
            >
              {OBJECTIVE_KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <span className="hint">Controla el equipo, la evidencia y si corresponde ejecutar tests. Puedes dejar que AI Teams lo detecte.</span>
          </label>

          {/* ── Cómo empezar: el plan es la base ── */}
          <div className="start-profile-picker">
            <div className="hiring-header">Cómo empezar</div>
            <div className="profile-selector">
              {[
                PROFILE_OPTIONS.find((p) => p.value === 'lead_quorum')!,
                PROFILE_OPTIONS.find((p) => p.value === 'full_team')!,
                PROFILE_OPTIONS.find((p) => p.value === 'solo_lead')!,
              ].map((p) => (
                <button
                  key={p.value}
                  type="button"
                  className={`profile-chip${newProjectRunProfile === p.value ? ' active' : ''}`}
                  onClick={() => setNewProjectRunProfile(p.value)}
                  title={p.desc}
                >
                  {p.label}
                  {p.value === 'lead_quorum' && <span className="chip-reco">Plan profundo</span>}
                </button>
              ))}
            </div>
            <p className="hint">
              Para objetivos ambiguos o críticos, <strong>Lead + Quorum</strong> hace que seniors independientes
              (idealmente de otro proveedor) auditen el plan del Lead antes de ejecutar nada:
              el proyecto arranca planificando, no improvisando.
            </p>
          </div>
          <label>
            Clasificación de los datos
            <select value={newProjectDataClass} onChange={(event) => setNewProjectDataClass(event.target.value)}>
              <option value="public">Públicos</option>
              <option value="internal">Internos</option>
              <option value="confidential">Confidenciales</option>
              <option value="restricted">Restringidos</option>
            </select>
          </label>
          <p className="hint">
            Los canales gratuitos externos solo se habilitan con una clasificación explícita y nunca para datos confidenciales o restringidos.
          </p>
          <div className="project-adapter-picker">
            <div className="panel-title compact-title">
              <KeyRound size={16} />
              Conexiones del proyecto
            </div>
            <p className="hint">
              Primero conecta al menos un canal. Despues selecciona cuales puede usar este proyecto; el hiring repartira modelos fuertes a seniors y baratos/locales a workers.
            </p>
            <div className="connection-summary">
              <strong>{adapterProfiles.filter((profile) => profileState(profile).connected).length}</strong>
              <span>conectados</span>
              <strong>{selectedProjectAdapterIds.length}</strong>
              <span>seleccionados</span>
            </div>
            {(() => {
              // Diversidad real de proveedor entre las conexiones YA seleccionadas:
              // es lo que determina si el quorum tendrá perspectivas independientes.
              const providers = new Set(
                adapterProfiles
                  .filter((p) => selectedProjectAdapterIds.includes(p.id) && profileState(p).connected)
                  .map((p) => profileState(p).secretProvider || String(p.provider || '').toLowerCase())
                  .filter(Boolean),
              );
              return providers.size >= 2 ? (
                <div className="quorum-ready-note ok">
                  ✓ Quorum multi-proveedor listo — {providers.size} proveedores distintos seleccionados.
                  Los auditores del plan tendrán perspectivas realmente independientes.
                </div>
              ) : (
                <div className="quorum-ready-note">
                  Selecciona conexiones de <strong>al menos 2 proveedores distintos</strong>: el quorum
                  audita el plan con seniors de otro proveedor y evita el sesgo de un solo modelo.
                  Con uno solo funcionará, pero en modo reducido.
                </div>
              );
            })()}
            <label>
              Lead del proyecto
              <ModelRoleSelector
                role="lead"
                profileId={leadAdapterProfileId}
                model={leadModel}
                runProfile={newProjectRunProfile}
                criticality="medium"
                dataClass={newProjectDataClass || 'public'}
                onChange={({ profileId, model, candidateId }) => {
                  setLeadAdapterProfileId(profileId);
                  setLeadModel(model);
                  setLeadCandidateId(candidateId);
                  setSelectedProjectAdapterIds((current) => current.includes(profileId)
                    ? current
                    : [...current, profileId]);
                }}
              />
            </label>
            <p className="hint">
              Este agente será la autoridad Lead y redactará Plan A y Plan B. Podrás cambiar su adapter y modelo después en Equipo; Codex también puede actuar como senior del quorum.
            </p>
            {onboardingLeadBlockReason && (
              <p className="field-warning">Lead bloqueado: {onboardingLeadBlockReason}</p>
            )}
            <div className="adapter-choice-list">
              {adapterProfiles.filter((profile) => profile.status !== 'blocked_by_provider').map((profile) => {
                const state = profileState(profile);
                return (
                  <button
                    key={profile.id}
                    type="button"
                    className={`adapter-choice${selectedProjectAdapterIds.includes(profile.id) ? ' active' : ''}${!state.selectable ? ' disabled' : ''}`}
                    onClick={() => toggleProjectAdapter(profile.id)}
                    title={profile.health?.detail || profile.health?.hint || profile.label}
                    disabled={!state.selectable}
                  >
                    <strong>{profile.label}</strong>
                    <span>{state.label}</span>
                  </button>
                );
              })}
              {!adapterProfiles.length ? <p className="muted">Cargando perfiles de adapter...</p> : null}
            </div>

            {/* ── Test each adapter in setup ── */}
            {adapterProfiles.filter((p) => p.status !== 'blocked_by_provider').length > 0 && (
              <div className="adapter-test-grid">
                <div className="hiring-header" style={{ marginBottom: '0.3rem' }}>Probar conexión</div>
                {adapterProfiles.filter((p) => p.status !== 'blocked_by_provider').map((profile) => {
                  const h = profile.health;
                  const hStatus = h?.status || 'untested';
                  const testModel = adapterTestModels[profile.id]
                    || String(profile.config?.model || profile.model_options?.[0]?.value || '');
                  return (
                    <div key={profile.id} className={`adapter-test-row health-${hStatus}`}>
                      <span className={`adapter-health-dot dot-${hStatus}`} />
                      <span className="adapter-test-label">{profile.label}</span>
                      <small className="adapter-test-detail">
                        {hStatus === 'ok' ? (h?.reason || 'OK') : hStatus === 'installed' ? 'CLI encontrado, sin auth' : hStatus === 'failed' ? (h?.reason || 'error') : 'sin test'}
                      </small>
                      {h?.hint && <small className="adapter-test-hint">{h.hint}</small>}
                      {profile.model_options?.length ? (
                        <select
                          value={testModel}
                          onChange={(event) => setAdapterTestModels((current) => ({
                            ...current,
                            [profile.id]: event.target.value,
                          }))}
                        >
                          {profile.model_options.map((option) => (
                            <option key={option.value} value={option.value}>{option.label}</option>
                          ))}
                        </select>
                      ) : null}
                      <button
                        type="button"
                        className="secondary-button"
                        style={{ fontSize: '0.7rem', minHeight: '28px', padding: '0 8px' }}
                        disabled={loading}
                        onClick={() => void testAdapterProfile(profile.id, testModel)}
                      >
                        Probar
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {/* ── Subscription CLI login ── */}
            {cliStatus.filter((item) => item.login_supported).length > 0 && (
              <div className="connect-more">
                <div className="hiring-header">Conectar suscripcion CLI</div>
                <div className="cli-status-grid">
                  {cliStatus.filter((item) => item.login_supported).map((item) => {
                    // Find matching adapter profile to show auth status
                    const matchedProfile = adapterProfiles.find((p) => p.id.includes(item.id.replace('_subscription','')) || item.id.includes(p.id.replace('_subscription','')));
                    const authOk = matchedProfile?.health?.status === 'ok';
                    return (
                      <div key={item.id} className={`cli-card${item.available ? (authOk ? ' ok authenticated' : ' ok') : ''}`} title={item.login_hint || item.command}>
                        <span>{item.label}</span>
                        <small className="cli-command">
                          {item.available ? (authOk ? 'auth verificada ✓' : (item.login_command || item.command)) : 'CLI no encontrado'}
                        </small>
                        <button
                          type="button"
                          onClick={() => void launchCliLogin(item.id)}
                          disabled={loading || !item.available}
                        >
                          {authOk ? 'Reconectar' : (item.id === 'opencode' ? 'Conectar OpenCode Zen' : 'Login')}
                        </button>
                        <CliSetupGuide item={item} />
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* ── Info: all adapters can write files via structured ops ── */}
            {selectedProjectAdapterIds.length > 0 &&
              !adapterProfiles.some((p) => selectedProjectAdapterIds.includes(p.id) && p.adapter_type === 'subscription_cli') && (
              <div className="banner info" style={{ margin: '0.5rem 0 0' }}>
                Todos los adapters (API incluida) pueden escribir archivos. CLI de suscripción añade modelos de tarifa plana sin coste por token.
              </div>
            )}

            <p className="adapter-security-note">
              Las API keys se guardan en el backend local cifradas con DPAPI en Windows; no quedan persistidas en el navegador.
            </p>
            <div className="secret-row">
              <select value={secretProvider} onChange={(event) => setSecretProvider(event.target.value)}>
                <option value="openai">OpenAI</option>
                <option value="google">Google Gemini</option>
                <option value="anthropic">Anthropic</option>
              </select>
              <input
                type="password"
                placeholder="API key"
                value={secretValue}
                onChange={(event) => setSecretValue(event.target.value)}
              />
              <button onClick={() => void saveSecret()} disabled={loading || !secretValue.trim()}>Guardar</button>
            </div>
          </div>
          <div className="actions">
            <button onClick={() => void createProject()} disabled={loading || !projectName.trim() || selectedProjectAdapterIds.length === 0 || !leadAdapterProfileId || !leadModel || Boolean(onboardingLeadBlockReason)}>
              Crear proyecto
            </button>
          </div>
          <p className="hint">Raiz de proyectos: {projectsRoot || '...'}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="shell app-shell" data-testid="project-cockpit">
      {projectInitializing && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 9999,
          background: 'rgba(13,17,23,0.92)',
          backdropFilter: 'blur(4px)',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          gap: '1.5rem',
        }}>
          <div style={{
            width: 48, height: 48, borderRadius: '50%',
            border: '3px solid var(--border)',
            borderTopColor: 'var(--accent)',
            animation: 'spin 0.8s linear infinite',
          }} />
          <div style={{ textAlign: 'center', color: 'var(--text-bright)' }}>
            <div style={{ fontWeight: 600, fontSize: '1.05rem', marginBottom: '0.35rem' }}>
              Iniciando proyecto…
            </div>
            <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
              El Lead está leyendo la tarea y organizando el equipo
            </div>
          </div>
        </div>
      )}
      <header className="topbar">
        <div className="topbar-brand">
          <span className="brand-mark">▸</span>
          <div className="topbar-project">
            <button
              className="topbar-project-btn"
              onClick={() => { void loadProjectList(); setProjectListOpen((v) => !v); }}
              title={workspace || 'Cambiar de proyecto'}
            >
              <span className="brand-name">{projectDisplayName}</span>
              <span className="topbar-project-caret">▾</span>
            </button>
            {projectListOpen && (
              <div className="project-list-popup topbar-project-popup">
                {projectList.length === 0 ? (
                  <p className="muted" style={{ padding: '8px' }}>Sin proyectos encontrados</p>
                ) : (
                  projectList.map((p) => (
                    <button
                      key={p.path}
                      className={`project-list-item${p.current ? ' current' : ''}`}
                      onClick={() => void switchProject(p.path)}
                      disabled={p.current || loading}
                      title={p.path}
                    >
                      <FolderOpen size={13} />
                      <span className="project-list-name">{p.name}</span>
                      {p.current && <span className="project-list-badge">activo</span>}
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
        <div className="top-actions">
          <button
            className={`secondary-button autonomy-pill${autonomyMode === 'autonomous' ? ' autonomous' : ''}`}
            onClick={() => void saveAutonomy(autonomyMode === 'autonomous' ? 'supervised' : 'autonomous')}
            disabled={autonomySaving || !workspaceConfigured}
            title={autonomyMode === 'autonomous'
              ? 'Autónomo: las escalaciones operativas se auto-resuelven; las de producto te esperan. Clic para pasar a Supervisado.'
              : 'Supervisado: el equipo se detiene en cada escalación hasta que respondas. Clic para pasar a Autónomo.'}
          >
            <span className="autonomy-dot" />
            {autonomyMode === 'autonomous' ? 'Autónomo' : 'Supervisado'}
          </button>
          {hasPending && (
            <button
              className="secondary-button pending-alert-button"
              onClick={() => setViewMode('inbox')}
              title="Hay decisiones pendientes — clic para abrir la Bandeja"
            >
              <Bell size={16} />
              Pendiente
              <span className="notif-badge">{pendingInteractions.length}</span>
            </button>
          )}
          <button
            className="secondary-button"
            onClick={() => void wakeLead()}
            disabled={loading || !selectedIssue}
            title={`Encola un wakeup para ${wakeTargetName} en la issue seleccionada`}
          >
            <Play size={16} />
            Despertar a {wakeTargetName}
          </button>
          {hasActiveRun && (
            <span className="live-indicator" title="Hay una run en progreso — actualizando cada 2 s">
              <span className="live-dot" />
              En vivo
            </span>
          )}
          <button className="icon-button" onClick={() => void refresh()} disabled={loading} title="Refrescar">
            <RefreshCcw size={18} className={loading ? 'spin' : ''} />
          </button>
        </div>
      </header>

      {error ? <div className="banner error">{error}</div> : null}

      <section className="workspace-grid">
        <aside className="nav-column">
          <div className="sidebar-project-header">
            <span className="project-name">{workspace ? shortPath(workspace) : 'Sin proyecto'}</span>
            {hasActiveRun && <span className="live-dot" title="Run en progreso" />}
          </div>

          <div className="sidebar-stats">
            <div className="stat-item"><span>{issues.length}</span><small>Issues</small></div>
            <div className="stat-item"><span>{activeIssues}</span><small>Abiertas</small></div>
            <div className="stat-item"><span>{doneIssues}</span><small>Done</small></div>
            <div className={`stat-item${hasPending ? ' stat-alert' : ''}`}>
              <span>{pendingInteractions.length}</span>
              <small>Pendientes</small>
            </div>
          </div>

          {latestRun && (
            <button
              className="last-run-bar"
              onClick={() => { setRunId(latestRun.id); setViewMode('runs'); }}
            >
              <small>Última run</small>
              <span className={`status-pill status-${latestRun.status}`}>{statusLabel(latestRun.status)}</span>
              <span className="last-run-id">{latestRun.id.slice(-8)}</span>
            </button>
          )}

          {loopHealth?.summary?.requires_attention && (
            <div className="loop-health-banner">
              <div className="loop-health-title">
                <AlertCircle size={13} />
                <span>Atención operativa</span>
              </div>
              {loopHealth.detected_loops.map((entry) => (
                <button
                  key={entry.child_issue_id}
                  className="loop-health-entry loop-health-critical"
                  onClick={() => { setSelectedIssueId(entry.child_issue_id); setViewMode('issue'); }}
                  title={`Saltado ${entry.skip_count} veces sin desbloqueo`}
                >
                  <span className="loop-health-label">{entry.child_title || entry.child_issue_id}</span>
                  <span className="loop-health-badge">{entry.skip_count}×</span>
                </button>
              ))}
              {loopHealth.at_risk.map((entry) => (
                <button
                  key={entry.child_issue_id}
                  className="loop-health-entry loop-health-warn"
                  onClick={() => { setSelectedIssueId(entry.child_issue_id); setViewMode('issue'); }}
                  title={`En riesgo — saltado ${entry.skip_count} veces`}
                >
                  <span className="loop-health-label">{entry.child_title || entry.child_issue_id}</span>
                  <span className="loop-health-badge">{entry.skip_count}×</span>
                </button>
              ))}
              {(loopHealth.orchestrator_evals?.liveness?.stranded_nonterminal_roots || 0) > 0 && (
                <button
                  className="loop-health-entry loop-health-critical"
                  onClick={() => { setIssueFilter('open'); setViewMode('issue'); }}
                  title="Raíces abiertas sin run, wakeup ni interacción pendiente"
                >
                  <span className="loop-health-label">Revisar raíces sin continuación</span>
                  <span className="loop-health-badge">
                    {loopHealth.orchestrator_evals?.liveness?.stranded_nonterminal_roots}
                  </span>
                </button>
              )}
              {((loopHealth.orchestrator_evals?.liveness?.stale_nonterminal_runs || 0) > 0
                || (loopHealth.orchestrator_evals?.liveness?.stale_claimed_or_running_wakeups || 0) > 0) && (
                <button
                  className="loop-health-entry loop-health-warn"
                  onClick={() => setViewMode('runs')}
                  title="Inspeccionar runs y wakeups activos desde hace más de 30 minutos"
                >
                  <span className="loop-health-label">Inspeccionar ejecución estancada</span>
                  <span className="loop-health-badge">
                    {(loopHealth.orchestrator_evals?.liveness?.stale_nonterminal_runs || 0)
                      + (loopHealth.orchestrator_evals?.liveness?.stale_claimed_or_running_wakeups || 0)}
                  </span>
                </button>
              )}
              {loopHealth.orchestrator_evals?.quorum?.available
                && loopHealth.orchestrator_evals.quorum.healthy === false && (
                <button
                  className="loop-health-entry loop-health-quorum"
                  onClick={() => setViewMode('runs')}
                  title="Inspeccionar sesiones, contribuciones y provenance del quorum"
                >
                  <span className="loop-health-label">Auditar quorum inconsistente</span>
                  <span className="loop-health-badge">Quorum</span>
                </button>
              )}
              {(loopHealth.capacity_profiles ?? loopHealth.subscription_quota ?? []).filter((profile) => profile.requires_attention).map((profile) => {
                const apiLimit = profile.api_rate_limits?.find((item) => item.remaining === 0)
                  ?? profile.api_rate_limits?.[0];
                const isApi = profile.quota_kind === 'api_rate_limit';
                return (
                  <button
                    key={`subscription-quota-${profile.profile_id}`}
                    className="loop-health-entry loop-health-warn"
                    onClick={() => setViewMode('runs')}
                    title={isApi
                      ? `El proveedor API agotó ${apiLimit?.dimension?.toUpperCase() ?? 'un límite'}${apiLimit?.reset ? `; reset ${apiLimit.reset}` : ''}`
                      : profile.state === 'exhausted_observed'
                        ? 'El CLI devolvió un límite de uso y no hay una run posterior completada'
                        : 'El umbral operativo de suscripción configurado por el owner está próximo o alcanzado'}
                  >
                    <span className="loop-health-label">
                      {isApi
                        ? 'Rate limit API'
                        : profile.state === 'exhausted_observed' ? 'Cuota de suscripción agotada' : 'Presión de suscripción'}: {profile.label}
                    </span>
                    <span className="loop-health-badge">
                      {isApi && apiLimit
                        ? `${apiLimit.dimension.toUpperCase()} ${apiLimit.remaining ?? '—'}`
                        : profile.state === 'exhausted_observed'
                          ? 'Límite'
                          : profile.forecast.estimated_runs_remaining != null
                            ? `~${profile.forecast.estimated_runs_remaining} runs`
                            : 'Revisar'}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="nav-column-issues">
            <div className="issue-list-header">
              <ListChecks size={14} />
              <span>Issues</span>
            </div>
            {(() => {
              const openCount = issues.filter((i) => !isClosedIssue(i.status)).length;
              const closedCount = issues.length - openCount;
              const filters: Array<{ key: 'all' | 'open' | 'closed'; label: string; count: number }> = [
                { key: 'all', label: 'Todas', count: issues.length },
                { key: 'open', label: 'Abiertas', count: openCount },
                { key: 'closed', label: 'Cerradas', count: closedCount },
              ];
              const visible = issues.filter((i) =>
                issueFilter === 'all' ? true : issueFilter === 'closed' ? isClosedIssue(i.status) : !isClosedIssue(i.status),
              );
              return (
                <>
                  <div className="issue-filter">
                    {filters.map((f) => (
                      <button
                        key={f.key}
                        className={`issue-filter-btn${issueFilter === f.key ? ' active' : ''}`}
                        onClick={() => setIssueFilter(f.key)}
                      >
                        {f.label} <span className="issue-filter-count">{f.count}</span>
                      </button>
                    ))}
                  </div>
                  <div className="issue-list">
                    {visible.map((issue) => {
                      const closed = isClosedIssue(issue.status);
                      return (
                        <button
                          className={`issue-button${issue.id === selectedIssue?.id ? ' active' : ''}${closed ? ' issue-closed' : ' issue-open'}`}
                          key={issue.id}
                          onClick={() => {
                            setSelectedIssueId(issue.id);
                            setViewMode('issue');
                            void loadPlanDocument(issue.id);
                          }}
                        >
                          <div className="issue-title-row">
                            <span className={`issue-state-dot state-${issue.status}`} title={statusLabel(issue.status)} />
                            <span className={closed ? 'issue-title-closed' : undefined}>{issue.title}</span>
                            {issuesWithPending.has(issue.id) && <span className="pending-dot" title="Decisión pendiente" />}
                          </div>
                          <small>
                            <span className={`issue-status-tag tag-${closed ? 'closed' : 'open'}`}>{statusLabel(issue.status)}</span>
                            {' · '}{issue.assignee_agent_id || 'sin owner'}
                            <ProfileBadge profile={issueProfile(issue)} compact />
                          </small>
                          <IssuePipeline issue={issue} />
                        </button>
                      );
                    })}
                    {visible.length === 0 && <p className="issue-empty">No hay issues {issueFilter === 'open' ? 'abiertas' : issueFilter === 'closed' ? 'cerradas' : ''}.</p>}
                  </div>
                </>
              );
            })()}
          </div>

          {budgets.length > 0 && (
            <section className="panel compact-panel budget-panel">
              <div className="panel-title">
                <Activity size={18} />
                Presupuesto
              </div>
              <div className="budget-list">
                {budgets.map((b) => {
                  const pct = b.budget_monthly_cents > 0
                    ? Math.min(100, Math.round((b.spent_cents / b.budget_monthly_cents) * 100))
                    : 0;
                  const stateClass = b.exceeded ? 'budget-exceeded' : b.near_limit ? 'budget-near' : 'budget-ok';
                  return (
                    <div key={b.agent_id} className={`budget-item ${stateClass}`}>
                      <div className="budget-agent-name">{b.agent_name}</div>
                      <div className="budget-bar-track">
                        <div className="budget-bar-fill" style={{ width: `${pct}%` }} />
                      </div>
                      <div className="budget-meta">
                        <span>{(b.spent_cents / 100).toFixed(2)} €</span>
                        <span className="budget-limit">/ {(b.budget_monthly_cents / 100).toFixed(2)} €</span>
                        {b.exceeded && <span className="budget-badge exceeded">Excedido</span>}
                        {!b.exceeded && b.near_limit && <span className="budget-badge near">~{pct}%</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {costSummary && (costSummary.totals.actual_cost_cents > 0 || costSummary.totals.estimated_savings_cents > 0) && (
            <section className="panel compact-panel budget-panel">
              <div className="panel-title">
                <Activity size={18} />
                Coste del proyecto
              </div>
              <div className="budget-list">
                <div className="budget-item budget-ok">
                  <div className="budget-agent-name">Gasto real</div>
                  <div className="budget-meta">
                    <span>{(costSummary.totals.actual_cost_cents / 100).toFixed(2)} €</span>
                    <span className="budget-limit">{costSummary.totals.runs} runs</span>
                  </div>
                </div>
                <div className="budget-item budget-ok">
                  <div className="budget-agent-name">Ahorro estimado vs premium</div>
                  <div className="budget-meta">
                    <span>{(costSummary.totals.estimated_savings_cents / 100).toFixed(2)} €</span>
                  </div>
                </div>
                {costSummary.by_role.filter((entry) => entry.actual_cost_cents > 0).map((entry) => (
                  <div key={entry.role} className="budget-item">
                    <div className="budget-agent-name">{entry.role}</div>
                    <div className="budget-meta">
                      <span>{(entry.actual_cost_cents / 100).toFixed(2)} €</span>
                      <span className="budget-limit">{entry.runs} runs</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Nueva tarea — pinned at the bottom of the sidebar */}
          <div className="sidebar-new-task">
            <div className="sidebar-new-task-header">
              <Plus size={14} />
              <span>Nueva tarea</span>
            </div>
            <textarea
              className="task-input"
              data-testid="new-task-draft"
              placeholder="Describe la tarea para el Lead..."
              value={newTaskDraft}
              onChange={(event) => setNewTaskDraft(event.target.value)}
            />
            <label>
              Tipo de objetivo
              <select
                data-testid="new-task-objective-kind"
                value={newTaskObjectiveKind}
                onChange={(event) => setNewTaskObjectiveKind(event.target.value as ObjectiveKind)}
              >
                {OBJECTIVE_KIND_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            {pendingPlanRef && (
              <div className="attached-plan-chip" data-testid="attached-plan" title={`Revisión ${pendingPlanRef.revisionId} de ${pendingPlanRef.sourceIssueId}`}>
                📋 Plan aceptado adjunto
                <button
                  className="attached-plan-clear"
                  onClick={() => {
                    if (orientationSessionProgressRef.current.activeFlows.has('accepted_plan_to_task')) {
                      void recordOrientationEvent('accepted_plan_to_task', 'flow_abandoned', newTaskProfile);
                    }
                    setPendingPlanRef(null);
                  }}
                  title="Quitar el plan adjunto"
                >
                  ✕
                </button>
              </div>
            )}
            <div className="profile-selector">
              {PROFILE_OPTIONS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  data-testid={`task-profile-${p.value}`}
                  aria-pressed={newTaskProfile === p.value}
                  className={`profile-chip${newTaskProfile === p.value ? ' active' : ''}`}
                  onClick={() => {
                    setNewTaskProfile(p.value);
                    void recordProfileOrientation(p.value);
                  }}
                  title={p.value === 'lead_quorum' ? `${p.desc}. Úsalo para objetivos ambiguos o críticos que justifiquen auditoría senior.` : p.desc}
                >
                  {p.label}
                  {p.value === 'lead_quorum' && <span className="chip-reco chip-reco-mini">★</span>}
                </button>
              ))}
            </div>
            <div className="profile-guidance" data-testid="profile-guidance" aria-live="polite">
              <span><strong>Coste operativo:</strong> {PROFILE_GUIDANCE[newTaskProfile]?.cost}</span>
              <span><strong>Riesgo:</strong> {PROFILE_GUIDANCE[newTaskProfile]?.risk}</span>
            </div>
            {newTaskProfile !== 'lead_quorum' && !pendingPlanRef && (
              <p className="new-task-quorum-hint">
                Si el objetivo es ambiguo o crítico, considera <strong>Lead + Quorum</strong> (★) para auditar el plan antes de ejecutar.
              </p>
            )}
            <button
              className="sidebar-create-btn"
              data-testid="create-task-button"
              onClick={() => void createTask()}
              disabled={loading || !newTaskDraft.trim()}
              title={PROFILE_OPTIONS.find((p) => p.value === newTaskProfile)?.desc}
            >
              <Send size={14} />
              Crear tarea · {PROFILE_BADGES[newTaskProfile]?.label || newTaskProfile}
            </button>
          </div>
        </aside>

        <section className="work-column">
          <nav className="view-tabs" aria-label="Vistas">
            <span className="tab-group-label">Proyecto</span>
            <button className={viewMode === 'chat' ? 'tab active tab-chat' : 'tab tab-chat'} onClick={() => setViewMode('chat')}>
              <MessageSquare size={16} />
              Chat
            </button>
            <button
              data-testid="inbox-tab"
              className={viewMode === 'inbox' ? 'tab active tab-chat' : `tab tab-chat${hasPending ? ' tab-chat-pending' : ''}`}
              onClick={() => {
                setViewMode('inbox');
                void recordOrientationEvent('inbox', 'flow_completed');
              }}
            >
              <Bell size={16} />
              Bandeja
              {hasPending ? <span className="notif-badge">{pendingInteractions.length}</span> : null}
            </button>
            <button className={viewMode === 'timeline' ? 'tab active' : 'tab'} onClick={() => setViewMode('timeline')}>
              <Clock3 size={16} />
              Actividad
            </button>
            <button
              className={viewMode === 'files' ? 'tab active' : 'tab'}
              onClick={() => { setViewMode('files'); void loadWsFiles(); }}
            >
              <FolderOpen size={16} />
              Archivos
              {wsFiles.length > 0 ? <span className="tab-badge">{wsFiles.length}</span> : null}
            </button>
            <button
              className={viewMode === 'team' ? 'tab active' : 'tab'}
              onClick={() => setViewMode('team')}
            >
              <Users size={16} />
              Equipo
              {agents.length > 0 ? <span className="tab-badge">{agents.length}</span> : null}
            </button>
            <button
              data-testid="models-tab"
              className={viewMode === 'models' ? 'tab active tab-models' : 'tab tab-models'}
              onClick={() => setViewMode('models')}
              title="Catálogo universal de modelos y proveedores"
            >
              <Boxes size={16} />
              Modelos
            </button>
            <button
              data-testid="config-tab"
              className={viewMode === 'config' ? 'tab active tab-config' : 'tab tab-config'}
              onClick={() => setViewMode('config')}
              title="Configuración"
            >
              <KeyRound size={16} />
              Config
            </button>
            <span
              className="tab-group-label tab-group-issue"
              title={selectedIssue ? `Vistas de la issue seleccionada: ${selectedIssue.title}` : 'Vistas de la issue seleccionada'}
            >
              Issue{selectedIssue ? ` · ${clip(selectedIssue.title, 24)}` : ''}
            </span>
            <button className={viewMode === 'issue' ? 'tab active' : 'tab'} onClick={() => setViewMode('issue')}>
              <MessageSquare size={16} />
              Detalle
            </button>
            <button data-testid="plan-tab" className={viewMode === 'plan' ? 'tab active' : 'tab'} onClick={() => setViewMode('plan')}>
              <FileText size={16} />
              Plan
              {planDocument ? <span className="tab-badge">v{planDocument.revision_number}</span> : null}
            </button>
            <button className={viewMode === 'runs' ? 'tab active' : 'tab'} onClick={() => setViewMode('runs')}>
              <GitBranch size={16} />
              Runs
              {hasActiveRun ? <span className="tab-badge tab-badge-active" title="Run en progreso" /> : null}
            </button>
          </nav>

          {viewMode === 'timeline' ? (
            <section className="panel timeline-panel">
              <div className="panel-title">
                <Clock3 size={18} />
                Actividad
              </div>
              <div className="filter-chips">
                <button
                  className={timelineTypeFilter === '' ? 'chip active' : 'chip'}
                  onClick={() => { setTimelineTypeFilter(''); void loadProjectData(selectedIssueId, ''); }}
                >
                  Todo
                </button>
                {TIMELINE_TYPES.map((t) => (
                  <button
                    key={t}
                    className={timelineTypeFilter === t ? 'chip active' : 'chip'}
                    onClick={() => { setTimelineTypeFilter(t); void loadProjectData(selectedIssueId, t); }}
                  >
                    {TIMELINE_TYPE_LABELS[t]}
                  </button>
                ))}
              </div>
              <div className="timeline">
                {timelineItems.map((item) => (
                  <button
                    className={`timeline-item type-${item.type}${item.status ? ` status-${item.status}` : ''}`}
                    key={item.id}
                    onClick={() => {
                      if (item.issueId) setSelectedIssueId(item.issueId);
                      if (item.type === 'run') {
                        // timeline item ids for runs are the run id directly
                        const rid = item.id.startsWith('run:') ? item.id.slice(4) : item.id;
                        setRunId(rid);
                        setViewMode('runs');
                        void loadRunDetail(rid);
                      } else {
                        setViewMode('issue');
                      }
                    }}
                  >
                    <time>{formatTime(item.time)}</time>
                    <div>
                      <strong>{item.title}</strong>
                      <p>{item.detail}</p>
                      <span>{item.actor || 'sistema'}{item.status ? ` · ${statusLabel(item.status)}` : ''}</span>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          {viewMode === 'issue' ? (
            <IssuePanel
              issue={selectedIssue}
              profile={selectedIssueProfile}
              objectiveLabel={issueObjectiveKind(selectedIssue)
                ? OBJECTIVE_KIND_LABELS[issueObjectiveKind(selectedIssue) || ''] || null
                : null}
              interactions={selectedInteractions}
              comments={selectedComments}
              commentDraft={commentDraft}
              busy={loading}
              onCommentDraftChange={setCommentDraft}
              onSubmitComment={addComment}
            />
          ) : null}
          {viewMode === 'plan' ? (
            <section className="panel plan-panel">
              <div className="panel-title">
                <FileText size={18} />
                Plan
                {planDocument ? (
                  <span className="muted" style={{ marginLeft: 'auto', fontWeight: 400, fontSize: '0.75rem' }}>
                    rev {planDocument.revision_number} · {formatTime(planDocument.updated_at || planDocument.created_at)}
                  </span>
                ) : null}
              </div>
              <QuorumStepper
                quorum={quorum}
                loading={quorumLoading}
                onCreateExecutionTask={() => {
                  const revisionId = quorum?.session.final_plan_revision_id;
                  if (!revisionId) return;
                  void recordOrientationEvent('accepted_plan_to_task', 'flow_started', 'full_team');
                  setPendingPlanRef({ revisionId, sourceIssueId: quorum.session.issue_id });
                  setNewTaskDraft(
                    `Ejecuta el plan aceptado de "${selectedIssue?.title || quorum.session.issue_id}". `
                    + 'El plan completo viaja adjunto como referencia (inherited_plan).',
                  );
                  setNewTaskProfile('full_team');
                }}
              />
              {planDocument ? (
                <>
                  <h3 style={{ margin: '0 0 0.5rem' }}>{planDocument.title}</h3>
                  {planDocument.plan ? (
                    <div className="plan-body">
                      <h4>Objetivo</h4>
                      <p>{planDocument.plan.objective}</p>
                      <h4>Arquitectura y enfoque</h4>
                      <p>{planDocument.plan.architecture}</p>
                      <h4>Trabajo y accountability</h4>
                      <ul>
                        {planDocument.plan.work_items.map((item) => (
                          <li key={item.id}>
                            <strong>{item.title}</strong> · {item.owner_role} reporta a {item.reports_to}; acepta {item.accepted_by}.{' '}
                            {item.deliverable}
                          </li>
                        ))}
                      </ul>
                      {planDocument.plan.narrative_markdown
                        ? renderMarkdownLite(planDocument.plan.narrative_markdown)
                        : null}
                    </div>
                  ) : (
                    <div className="plan-body">{renderMarkdownLite(planDocument.body)}</div>
                  )}
                </>
              ) : (
                <p className="muted">
                  {selectedIssue
                    ? 'El Lead aún no ha escrito un plan para esta issue.'
                    : 'Selecciona una issue para ver su plan.'}
                </p>
              )}
            </section>
          ) : null}

          {viewMode === 'chat' ? (
            <ChatPanel
              issueTitle={selectedIssue?.title || ''}
              profile={selectedIssueProfile}
              messages={chatMessages}
              feedRef={chatFeedRef}
              onFeedScroll={handleChatScroll}
              jumpVisible={chatJumpVisible}
              draft={chatDraft}
              sending={chatSending}
              onReviewInteraction={(interactionId) => {
                setSelectedInteractionId(interactionId);
                setViewMode('inbox');
              }}
              onJumpToBottom={scrollChatToBottom}
              onDraftChange={setChatDraft}
              onSend={sendChatMessage}
              onRefresh={loadChat}
            />
          ) : null}
          {viewMode === 'inbox' ? (
            <InboxPanel
              interactions={interactions}
              pendingInteractions={pendingInteractions}
              selectedInteractionId={selectedInteractionId}
              onSelect={setSelectedInteractionId}
              renderDetail={(current) => {
                const currentIssue = current ? issues.find((item) => item.id === current.issue_id) : null;
                const isPending = current?.status === 'pending';
                const isHiring = current?.kind === 'suggest_tasks';
                const isQuestion = current?.kind === 'ask_user_questions';
                const currentOutcome = String(current?.result?.outcome || '');
                const payload = (current?.payload || {}) as Record<string, unknown>;
                const isExtension = String(payload.reason || '') === 'extension_install_requested';
                const isModelFallback = String(payload.reason || '') === 'model_fallback_required';
                const proposedTeam: ProposedTeamMember[] = (payload.proposed_team as ProposedTeamMember[]) || [];
                const suggestedIssues = (payload.suggested_issues as Array<Record<string, unknown>>) || [];
                const hiringProfile = String(payload.profile || 'full_team');
                const isDirect = payload.direct_work === true;
                const hiringTeam = current ? (hiringDrafts[current.id] ?? proposedTeam) : proposedTeam;
                const hiringIssue = currentIssue || selectedIssue;
                const hiringBlockReason = hiringTeam.map((member) => {
                  const profileId = String(member.adapter_profile_id || member.adapter_config?.profile_id || '');
                  const model = String(member.model || member.adapter_config?.model || '');
                  const options = roleModelOptions[modelOptionCacheKey(profileId, member.role || '', hiringIssue)] || [];
                  const option = options.find((item) => item.value === model);
                  if (option?.selectable === false || option?.available === false) return option.availability_reason || 'Modelo no verificado';
                  if (option?.compatibility?.allowed === false) return option.compatibility.reason || option.compatibility.code || 'Modelo incompatible';
                  return '';
                }).find(Boolean) || '';
                if (!current) return <p className="muted">Selecciona una decisión de la lista.</p>;
                return (
                  <>
                          <div className="inbox-detail-header">
                            {isPending ? <AlertCircle size={15} /> : <CheckCircle2 size={15} />}
                            <h3>{current.title || current.kind}</h3>
                            {!isPending && (
                              <span className="chat-resolved-badge">
                                {currentOutcome === 'changes_requested' ? 'Cambios solicitados' : statusLabel(current.status)}
                              </span>
                            )}
                            {isHiring && <ProfileBadge profile={hiringProfile in PROFILE_BADGES ? hiringProfile : null} compact />}
                          </div>
                          {currentIssue && (
                            <button
                              className="inbox-detail-issue"
                              onClick={() => { setSelectedIssueId(currentIssue.id); setViewMode('issue'); }}
                              title="Abrir la issue"
                            >
                              Issue: {currentIssue.title}
                            </button>
                          )}
                          {current.summary && <p className="inbox-detail-summary">{current.summary}</p>}

                          {isExtension && (
                            // Aceptar instala código de terceros: el owner debe ver el
                            // comando EXACTO que se ejecutará, no solo el resumen del Lead.
                            <div className="extension-proposal-detail">
                              <div><span className="ext-label">Servidor:</span> <strong>{String(payload.name || '?')}</strong></div>
                              <div><span className="ext-label">Comando:</span> <code>{String(payload.source || '(sin comando — propuesta inválida)')}</code></div>
                              <div>
                                <span className="ext-label">Roles:</span>{' '}
                                {Array.isArray(payload.applies_to_roles) && payload.applies_to_roles.length > 0
                                  ? (payload.applies_to_roles as string[]).join(', ')
                                  : 'sin roles asignados'}
                              </div>
                              {typeof payload.justification === 'string' && payload.justification && (
                                <div><span className="ext-label">Evidencia:</span> {payload.justification}</div>
                              )}
                              <p className="ext-warning">
                                Aceptar autoriza ejecutar este software de terceros en tu máquina (tras verificación de salud). Revisa el comando antes de aceptar.
                              </p>
                            </div>
                          )}

                          {isModelFallback && (
                            <div className="agent-form-field">
                              <label className="agent-form-label">
                                Fallback dentro de {String(payload.profile_id || 'adapter actual')}
                              </label>
                              <ModelRoleSelector
                                role={String(payload.agent_role || '')}
                                issueId={current.issue_id || ''}
                                profileId={fallbackSelections[current.id]?.profileId || String(payload.profile_id || '')}
                                model={fallbackSelections[current.id]?.model || String(payload.proposed_model || '')}
                                restrictProfileId={String(payload.profile_id || '')}
                                {...issueCompatibilityContext(currentIssue)}
                                disabled={!isPending}
                                onChange={({ profileId, model, candidateId }) => setFallbackSelections((prev) => ({
                                  ...prev,
                                  [current.id]: { profileId, model, candidateId },
                                }))}
                              />
                              <small className="field-warning">
                                Recovery no cambia de adapter; para cambiar de canal edita primero el agente en Equipo.
                              </small>
                            </div>
                          )}

                          {isHiring && (
                            <HiringDecisionDetail
                              direct={isDirect}
                              team={hiringTeam}
                              suggestedIssues={suggestedIssues}
                              interactionId={current.id}
                              issueId={hiringIssue?.id || ''}
                              {...issueCompatibilityContext(hiringIssue)}
                              pending={isPending}
                              getRoleOptions={(profileId, role) => roleModelOptions[
                                modelOptionCacheKey(profileId, role, hiringIssue)
                              ]}
                              onSelectionChange={(index, profileId, model, candidateId) => updateHiringMemberSelection(
                                current.id,
                                hiringTeam,
                                index,
                                profileId,
                                model,
                                candidateId,
                              )}
                            />
                          )}

                          {isPending && (
                            <div className="interaction-note-area">
                              <textarea
                                placeholder={isHiring
                                  ? 'Describe los cambios que debe hacer el Lead antes de presentar otra propuesta…'
                                  : 'Escribe tu respuesta... (opcional — si no escribes nada, se enviará solo Aceptar)'}
                                value={interactionNotes[current.id] || ''}
                                onChange={(event) => setInteractionNotes((prev) => ({ ...prev, [current.id]: event.target.value }))}
                                rows={3}
                                disabled={loading}
                              />
                            </div>
                          )}

                          {isPending && isHiring && hiringBlockReason && (
                            <p className="field-warning">No se puede contratar todavía: {hiringBlockReason}</p>
                          )}

                          {isPending && (
                            <div className="actions inbox-actions">
                              <button
                                className="danger-button"
                                onClick={() => void resolveInteraction(current, 'reject')}
                                disabled={loading}
                              >
                                {isQuestion ? 'Descartar' : 'Rechazar'}
                              </button>
                              {isHiring && (
                                <button
                                  className="secondary-button request-changes-button"
                                  onClick={() => void resolveInteraction(current, 'changes_requested', interactionNotes[current.id])}
                                  disabled={loading || !(interactionNotes[current.id] || '').trim()}
                                  title="Devuelve la propuesta al Lead con este feedback"
                                >
                                  Pedir cambios…
                                </button>
                              )}
                              <button
                                onClick={() => void resolveInteraction(current, 'accept', isHiring ? undefined : interactionNotes[current.id])}
                                disabled={loading || (isHiring && Boolean(hiringBlockReason))}
                              >
                                {isQuestion ? 'Responder' : isHiring ? (isDirect ? 'Iniciar (solo Lead)' : 'Contratar equipo') : 'Aceptar'}
                              </button>
                            </div>
                          )}
                          <time className="chat-time">{formatTime(current.created_at)}</time>
                  </>
                );
              }}
            />
          ) : null}

          {viewMode === 'runs' ? (
            <RunsPanel
              runs={runs}
              selectedRun={selectedRun}
              events={runEvents}
              runId={runId}
              busy={loading}
              onRunIdChange={setRunId}
              onSelectRun={async (nextRunId) => {
                setRunId(nextRunId);
                await loadRunDetail(nextRunId);
              }}
            />
          ) : null}
          {viewMode === 'files' ? (
            <section className="panel files-panel">
              <div className="files-layout">
                <div className="files-list-col">
                  <div className="panel-title">
                    <FolderOpen size={18} />
                    Archivos del workspace
                    <button
                      className="secondary-button"
                      style={{ marginLeft: 'auto', fontSize: '0.7rem', padding: '0 8px', minHeight: '28px' }}
                      onClick={() => void loadWsFiles()}
                      title="Actualizar lista"
                    >
                      <RefreshCcw size={13} />
                    </button>
                  </div>
                  {wsFiles.length === 0 ? (
                    <p className="muted">Sin archivos generados aún. El Engineer escribirá aquí.</p>
                  ) : (
                    <div className="ws-file-list">
                      {wsFiles.map((f) => {
                        const isCode = f.mime?.startsWith('text/') || ['application/json', 'application/javascript'].includes(f.mime);
                        return (
                          <button
                            key={f.path}
                            className={`ws-file-row${wsSelectedFile === f.path ? ' active' : ''}`}
                            onClick={() => void loadWsFile(f.path)}
                          >
                            {isCode ? <Code2 size={14} /> : <FileText size={14} />}
                            <span className="ws-file-path">{f.path}</span>
                            <small className="ws-file-size">{f.size_bytes < 1024 ? `${f.size_bytes}B` : `${(f.size_bytes / 1024).toFixed(1)}KB`}</small>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="files-content-col">
                  {wsFileLoading ? (
                    <p className="muted">Cargando...</p>
                  ) : wsFileContent !== null ? (
                    <>
                      <div className="panel-title" style={{ fontSize: '0.78rem' }}>
                        <FileText size={15} />
                        {wsSelectedFile}
                      </div>
                      <pre className="ws-file-content">{wsFileContent}</pre>
                    </>
                  ) : (
                    <p className="muted">Selecciona un archivo para ver su contenido.</p>
                  )}
                </div>
              </div>
            </section>
          ) : null}

          {viewMode === 'config' ? (
            <ConfigurationWorkspace
              projectDisplayName={projectDisplayName}
              section={cfgSection}
              onSectionChange={setCfgSection}
              configuration={configurationData}
              health={health}
              workspace={workspace}
              workspaceDraft={workspaceDraft}
              workspaceConfigured={workspaceConfigured}
              loading={loading}
              onWorkspaceDraftChange={setWorkspaceDraft}
              onSaveWorkspace={saveWorkspace}
              orientationMeasurement={orientationMeasurement}
              orientationBusy={orientationBusy}
              onOrientationConsentChange={changeOrientationConsent}
              onEraseOrientation={eraseOrientationMeasurement}
              deleteConfirmation={deleteConfirm}
              onDeleteConfirmationChange={setDeleteConfirm}
              onDeleteProject={deleteProject}
              lastResult={lastResult}
              onRefresh={refresh}
            />
          ) : null}

          {viewMode === 'team' ? (
            <section className="panel team-panel">
              <div className="panel-title team-panel-title">
                <Users size={18} />
                Organigrama del equipo
                <button
                  className="secondary-button reconcile-btn"
                  onClick={() => void reconcileTeam()}
                  disabled={loading}
                  title="Repara adaptadores y crea agentes Tier 3 faltantes"
                >
                  <RefreshCcw size={14} />
                  Reconciliar
                </button>
              </div>
              {!agents.length ? (
                <p className="muted">Sin agentes todavía. Crea un proyecto para ver el equipo.</p>
              ) : (
                <>
                  {/* ── Active agents by tier ── */}
                  {[1, 2, 3].map((tier) => {
                    const tierAgents = agents.filter((a) => agentTier(a.seniority) === tier);
                    if (!tierAgents.length) return null;
                    const info = TIER_LABELS[tier];
                    return (
                      <div key={tier} className="team-tier-group">
                        <div className="team-tier-header">
                          <span className={`tier-badge tier${tier}`}>Tier {tier}</span>
                          <div className="tier-header-text">
                            <strong>{info.title}</strong>
                            <small>{info.sub}</small>
                          </div>
                          <span className="tier-count">{tierAgents.length}</span>
                        </div>
                        <div className="team-agents-grid">
                          {tierAgents.map((agent) => renderAgentCard(agent))}
                        </div>
                      </div>
                    );
                  })}

                  {/* ── Available (not hired) roles ── */}
                  {(() => {
                    // Normalize: agent.role may be 'lead' or 'role:lead'; catalog keys are 'role:*'
                    const activeRoles = new Set(agents.flatMap((a) => {
                      const r = a.role ?? '';
                      return [a.id, r, r.startsWith('role:') ? r : `role:${r}`];
                    }));
                    const available = Object.entries(ROLE_CATALOG).filter(([roleId]) => !activeRoles.has(roleId));
                    if (!available.length) return null;
                    return (
                      <div className="team-tier-group team-available-group">
                        <div className="team-tier-header">
                          <span className="tier-badge tier-available">Disponibles</span>
                          <div className="tier-header-text">
                            <strong>Roles no contratados</strong>
                            <small>El Lead puede incorporarlos bajo demanda</small>
                          </div>
                          <span className="tier-count">{available.length}</span>
                        </div>
                        <div className="available-roles-grid">
                          {available.map(([roleId, def]) => (
                            <div key={roleId} className={`available-role-card tier${def.tier}`}>
                              <div className="available-role-header">
                                <span className={`tier-badge tier${def.tier}`}>T{def.tier}</span>
                                <span className="available-role-title">{def.title}</span>
                                <InfoTip tip={`${def.responsibilities}\n\nCuándo: ${def.when}`} wide />
                              </div>
                              <p className="available-role-desc">{def.desc}</p>
                              <div className="available-role-when">
                                <span className="available-role-when-label">Cuándo:</span> {def.when}
                              </div>
                              <button
                                className="hire-btn"
                                disabled={loading}
                                onClick={() => void hireCatalogRole(roleId, def)}
                              >
                                + Contratar
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })()}
                </>
              )}

              {/* ── Cost-policy deviation warning ── */}
              {viewMode === 'team' && (loopHealth?.policy_deviations?.length ?? 0) > 0 && (
                <div className="policy-deviation-banner">
                  <AlertCircle size={14} />
                  <span>
                    <strong>Política de costes:</strong>{' '}
                    {(loopHealth?.policy_deviations ?? []).length} rol(es) worker en modelos de pago por token:{' '}
                    {(loopHealth?.policy_deviations ?? []).map((d) => `${d.role} (${d.model})`).join(', ')}.{' '}
                    {(loopHealth?.policy_deviations ?? []).some((d) => d.reason === 'no_zero_cost_channel_connected')
                      ? 'Conecta un canal local (Ollama/LM Studio) o un CLI de suscripción y los workers pasarán a coste 0.'
                      : 'Hay un canal de coste 0 conectado — revisa la selección de adapters de estos agentes.'}
                  </span>
                </div>
              )}

              {/* ── Adapter Profiles panel ── */}
              {viewMode === 'team' && adapterProfiles.length > 0 && (
                <div className="profiles-panel">
                  <div className="profiles-panel-header">
                    <div className="profiles-panel-title">
                      <span className="profiles-panel-title-text">Adapters disponibles</span>
                      <InfoTip
                        wide
                        tip={`Cada adapter combina canal de conexión + credenciales + modelo por defecto. Se generan automáticamente cuando añades una API key o CLI en Config.\n\nEl agente apunta a un adapter. El modelo se puede sobreescribir por agente si quieres uno distinto al default.`}
                      />
                    </div>
                    <p className="profiles-panel-sub">
                      Adapters disponibles en tu instalación. Verde = listo para usar. Gris = falta configurar (añade la API key en Config).
                    </p>
                  </div>
                  <div className="profiles-grid">
                    {adapterProfiles.map((profile) => {
                      const pState = profileState(profile);
                      const model = String(profile.config?.model || '—');
                      const channelLabel: Record<string, string> = {
                        api: 'API',
                        subscription: 'CLI suscripción',
                        local: 'Local / Ollama',
                        manual: 'Manual',
                      };
                      return (
                        <div
                          key={profile.id}
                          className={`profile-card${pState.connected ? ' profile-connected' : ' profile-disconnected'}${profile.status === 'blocked_by_provider' ? ' profile-blocked' : ''}`}
                        >
                          <div className="profile-card-header">
                            <span className={`profile-status-dot${pState.connected ? ' connected' : profile.status === 'blocked_by_provider' ? ' blocked' : ''}`} title={pState.label} />
                            <span className="profile-card-label">{profile.label}</span>
                          </div>
                          <div className="profile-card-meta">
                            <span className="profile-meta-pill">{channelLabel[profile.channel ?? ''] ?? profile.channel ?? '—'}</span>
                            {model !== '—' && <span className="profile-meta-model">{model}</span>}
                          </div>
                          <div className="profile-card-status">{pState.label}</div>
                        </div>
                      );
                    })}
                  </div>
                  <p className="profiles-panel-hint">
                    Para activar más adapters, añade tus API keys en <strong>Config</strong>. Para crear adapters personalizados (modelo propio, CLI alternativo, Ollama) → <strong>próximamente desde la UI</strong>; por ahora edita <code>adapter_profiles.json</code> en la carpeta de configuración (<code>%LOCALAPPDATA%\AI Teams</code> en Windows, <code>~/.config/aiteams</code> en Linux/Mac).
                  </p>
                </div>
              )}
            </section>
          ) : null}

          {viewMode === 'models' ? <ModelCatalog /> : null}
        </section>

      </section>

      {/* ── Agent Config Modal ── */}
      {configModalAgent && (
        <div
          className="modal-overlay"
          onClick={() => { setConfigModalAgent(null); setAgentDraft({}); }}
          role="dialog"
          aria-modal="true"
        >
          <div className="modal-card agent-config-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-header-info">
                <h3 className="modal-title">Configurar agente</h3>
                <div className="modal-subtitle">
                  <span className={`agent-status-dot status-${configModalAgent.status === 'running' ? 'running' : configModalAgent.status === 'blocked' ? 'blocked' : 'idle'}`} />
                  <strong>{configModalAgent.name}</strong>
                  <span className={`tier-badge tier${agentTier(configModalAgent.seniority)}`}>T{agentTier(configModalAgent.seniority)}</span>
                  <code style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{configModalAgent.role}</code>
                </div>
              </div>
              <button
                className="modal-close"
                onClick={() => { setConfigModalAgent(null); setAgentDraft({}); }}
                aria-label="Cerrar"
              >✕</button>
            </div>
            <div className="modal-body">
              {agentFormJSX(configModalAgent)}
            </div>
          </div>
        </div>
      )}

      {catalogHire && (
        <div
          className="modal-overlay"
          onClick={() => setCatalogHire(null)}
          role="dialog"
          aria-modal="true"
          aria-label={`Contratar ${catalogHire.roleDef.title}`}
        >
          <div className="modal-card agent-config-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-header-info">
                <h3 className="modal-title">Contratar {catalogHire.roleDef.title}</h3>
                <div className="modal-subtitle">
                  <span className={`tier-badge tier${catalogHire.roleDef.tier}`}>T{catalogHire.roleDef.tier}</span>
                  <code>{catalogHire.roleId}</code>
                </div>
              </div>
              <button className="modal-close" onClick={() => setCatalogHire(null)} aria-label="Cerrar">✕</button>
            </div>
            <div className="modal-body agent-form-v2">
              <p>{catalogHire.roleDef.desc}</p>
              <div className="agent-form-field">
                <label className="agent-form-label">Modelo + adapter</label>
                <ModelRoleSelector
                  role={catalogHire.roleId}
                  issueId={selectedIssue?.id || ''}
                  profileId={catalogHire.profileId}
                  model={catalogHire.model}
                  {...issueCompatibilityContext(selectedIssue)}
                  onChange={({ profileId, model, candidateId }) => setCatalogHire((current) => current ? ({
                    ...current, profileId, model, candidateId,
                  }) : current)}
                />
              </div>
              <div className="modal-actions">
                <button className="secondary-btn" onClick={() => setCatalogHire(null)}>Cancelar</button>
                <button
                  className="primary-btn"
                  disabled={loading || !catalogHire.profileId || !catalogHire.model}
                  onClick={() => void confirmCatalogHire()}
                >
                  {loading ? 'Contratando…' : 'Confirmar contratación'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
