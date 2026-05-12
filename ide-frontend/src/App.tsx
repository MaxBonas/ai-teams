import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ThreadView } from './components/ThreadView';
import {
  Activity,
  AlertCircle,
  Bell,
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
import { apiFetch, getWorkspacePath, setWorkspacePath } from './lib/api';

interface HealthPayload {
  status?: string;
  mode?: string;
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

interface Issue {
  id: string;
  parent_id?: string | null;
  title: string;
  description?: string | null;
  status: string;
  role?: string | null;
  complexity?: string | null;
  criticality?: string | null;
  assignee_agent_id?: string | null;
  priority?: number;
  created_at?: string;
}

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

interface ProposedTeamMember {
  id: string;
  role: string;
  name: string;
  seniority?: string;
  adapter_type?: string;
  adapter_config?: Record<string, unknown>;
  adapter_profile_id?: string;
  model?: string;
  rationale?: string;
  supervisor_agent_id?: string | null;
}

interface CapabilityEntry {
  description: string;
  tool_family: string;
  label: string;
}

interface AdapterProfile {
  id: string;
  label: string;
  adapter_type: string;
  channel?: string;
  provider?: string;
  status?: string;
  config?: Record<string, unknown>;
  model_options?: Array<{ value: string; label: string }>;
  health?: AdapterHealth;
}

interface RoleModelOption {
  value: string;
  label: string;
  recommended?: boolean;
  fit_reason?: string;
  role_score?: number;
  tier?: string;
  price_note?: string;
}

interface CliStatus {
  id: string;
  label: string;
  command: string;
  available: boolean;
  login_supported?: boolean;
  login_hint?: string;
  login_command?: string;
  alternate_login_commands?: string[];
}

interface AdapterHealth {
  status: 'ok' | 'installed' | 'failed' | 'untested' | string;
  checked_at?: string;
  reason?: string;
  detail?: string;
  hint?: string;
}

interface SecretInfo {
  ref: string;
  provider: string;
  name: string;
  has_secret: boolean;
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

// ── InfoTip component ────────────────────────────────────────────────────────
function InfoTip({ tip, wide }: { tip: string; wide?: boolean }) {
  return (
    <span className={`info-tip${wide ? ' info-tip-wide' : ''}`}>
      <svg className="info-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.3"/>
        <path d="M8 7.5v3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        <circle cx="8" cy="5.5" r="0.75" fill="currentColor"/>
      </svg>
      <span className="info-tooltip" role="tooltip">{tip}</span>
    </span>
  );
}

// Adapter types registered in the backend (aiteam/adapters/registry.py build_default_registry).
// 'manual' exists but no human-facing notification/task system is implemented yet → disabled.
const ADAPTER_OPTIONS: Array<{ value: string; label: string; disabled?: boolean; note?: string }> = [
  { value: 'role_builtin',      label: 'Builtin simulado — sin llamada LLM real' },
  { value: 'anthropic_sonnet',  label: 'Claude Sonnet 4.5 · API Anthropic' },
  { value: 'anthropic_api',     label: 'Claude Opus 4.5 · API Anthropic' },
  { value: 'openai_api',        label: 'GPT-4.1 · API OpenAI' },
  { value: 'gemini_api',        label: 'Gemini 2.5 Flash · API Google' },
  { value: 'subscription_cli',  label: 'CLI de suscripción (Codex / Claude Code / Gemini CLI)' },
  {
    value: 'manual', disabled: true,
    label: '👤 Manual / humano — próximamente',
    note: 'El agente queda asignado a un humano que recibe la tarea y reporta via comentarios. Requiere sistema de notificación y vista de tarea humana aún no implementados.',
  },
];

const PROFILE_OPTIONS = [
  { value: 'full_team', label: 'Equipo completo', desc: 'Lead + Engineer + Reviewer' },
  { value: 'lead_quorum', label: 'Lead + Quorum', desc: 'Lead con auditores senior para planificación' },
  { value: 'solo_lead', label: 'Solo Lead', desc: 'Lead ejecuta directamente sin contratar' },
];

interface Comment {
  id: string;
  issue_id: string;
  body: string;
  author_agent_id?: string | null;
  author_user_id?: string | null;
  source_run_id?: string | null;
  created_at?: string;
}

interface Interaction {
  id: string;
  issue_id: string;
  kind: string;
  status: string;
  title?: string | null;
  summary?: string | null;
  created_by_agent_id?: string | null;
  resolved_by_user_id?: string | null;
  created_at?: string;
  resolved_at?: string | null;
}

interface Run {
  id: string;
  agent_id: string;
  issue_id?: string | null;
  status: string;
  invocation_source?: string;
  error?: string | null;
  error_code?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  actual_cost_cents?: number | null;
  result?: Record<string, unknown> | null;
  usage?: Record<string, unknown> | null;
}

interface RunEvent {
  id: string;
  run_id: string;
  event_type: string;
  seq: number;
  stream?: string | null;
  payload?: Record<string, unknown> | null;
  created_at?: string;
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
}

type TimelineType = 'issue' | 'comment' | 'interaction' | 'run' | 'activity' | 'cost' | 'tool';
const TIMELINE_TYPES: TimelineType[] = ['issue', 'comment', 'interaction', 'run', 'activity', 'cost', 'tool'];

type ViewMode = 'timeline' | 'issue' | 'plan' | 'runs' | 'chat' | 'files' | 'team' | 'config';

interface ChatMessage {
  id: string;
  source_id: string;
  item_type: 'message' | 'interaction';
  sender: 'user' | 'agent';
  author: string;
  body: string;
  title: string | null;
  summary: string | null;
  kind: string | null;
  interaction_status: string | null;
  payload: Record<string, unknown>;
  issue_id: string;
  source_run_id: string | null;
  created_at: string;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function renderMarkdownLite(markdown: string): React.ReactNode[] {
  const lines = markdown.split(/\r?\n/);
  const nodes: React.ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    nodes.push(<p key={`p-${nodes.length}`}>{renderInlineMarkdown(paragraph.join(' '))}</p>);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    const Tag = list.ordered ? 'ol' : 'ul';
    nodes.push(
      <Tag key={`list-${nodes.length}`}>
        {list.items.map((item, index) => <li key={index}>{renderInlineMarkdown(item)}</li>)}
      </Tag>,
    );
    list = null;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }
    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length + 1, 4);
      const content = renderInlineMarkdown(heading[2]);
      if (level === 2) nodes.push(<h2 key={`h-${nodes.length}`}>{content}</h2>);
      else if (level === 3) nodes.push(<h3 key={`h-${nodes.length}`}>{content}</h3>);
      else nodes.push(<h4 key={`h-${nodes.length}`}>{content}</h4>);
      return;
    }
    const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      const item = (ordered || unordered)?.[1] || '';
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push(item);
      return;
    }
    flushList();
    paragraph.push(trimmed);
  });
  flushParagraph();
  flushList();
  return nodes;
}

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('`')) {
      nodes.push(<code key={nodes.length}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(<strong key={nodes.length}>{token.slice(2, -2)}</strong>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    accepted: 'aceptado',
    blocked: 'bloqueado',
    cancelled: 'cancelado',
    completed: 'completado',
    done: 'done',
    failed: 'fallido',
    in_progress: 'en progreso',
    pending: 'pendiente',
    queued: 'en cola',
    rejected: 'rechazado',
    running: 'ejecutando',
    skipped: 'sin trabajo',
    todo: 'todo',
  };
  return labels[status] || status.replaceAll('_', ' ');
}

function shortPath(path: string): string {
  const parts = path.replaceAll('\\', '/').split('/');
  return parts.slice(-2).join('/');
}

function parseTime(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value.includes('T') ? value : value.replace(' ', 'T'));
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatTime(value?: string | null): string {
  const parsed = parseTime(value);
  if (!parsed) return '-';
  return new Date(parsed).toLocaleString([], {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function clip(text: string, max = 220): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > max ? `${normalized.slice(0, max - 1)}...` : normalized;
}

export default function App() {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [workspace, setWorkspace] = useState(getWorkspacePath());
  const [workspaceConfigured, setWorkspaceConfigured] = useState(false);
  const [projectsRoot, setProjectsRoot] = useState('');
  // Application-level settings (projects root, etc.)
  const [settingsConfigured, setSettingsConfigured] = useState(true); // optimistic until loaded
  const [settingsDraft, setSettingsDraft] = useState('');
  const [workspaceDraft, setWorkspaceDraft] = useState(getWorkspacePath());
  const [projectName, setProjectName] = useState('Nuevo Proyecto AI Teams');
  const [initialTask, setInitialTask] = useState('');
  const [issues, setIssues] = useState<Issue[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [budgets, setBudgets] = useState<BudgetInfo[]>([]);
  const [comments, setComments] = useState<Comment[]>([]);
  const [interactions, setInteractions] = useState<Interaction[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [timelineItems, setTimelineItems] = useState<TimelineItem[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState('issue:intake');
  const [viewMode, setViewMode] = useState<ViewMode>('chat');
  const [planDocument, setPlanDocument] = useState<PlanDocument | null>(null);
  const [timelineTypeFilter, setTimelineTypeFilter] = useState<TimelineType | ''>('');
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
  // Agent config inline edit (sidebar)
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [agentDraft, setAgentDraft] = useState<Partial<Agent>>({});
  // Agent config modal (team panel)
  const [configModalAgent, setConfigModalAgent] = useState<Agent | null>(null);
  // Hiring panel — editable team proposal per pending suggest_tasks interaction
  const [hiringDrafts, setHiringDrafts] = useState<Record<string, ProposedTeamMember[]>>({});
  // Free-text note per request_confirmation interaction (cleared after submit)
  const [interactionNotes, setInteractionNotes] = useState<Record<string, string>>({});
  // Tool capability catalog
  const [capabilityCatalog, setCapabilityCatalog] = useState<Record<string, CapabilityEntry>>({});
  const [adapterProfiles, setAdapterProfiles] = useState<AdapterProfile[]>([]);
  const [roleModelOptions, setRoleModelOptions] = useState<Record<string, RoleModelOption[]>>({});
  const [cliStatus, setCliStatus] = useState<CliStatus[]>([]);
  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [secretProvider, setSecretProvider] = useState('openai');
  const [secretValue, setSecretValue] = useState('');
  const [selectedProjectAdapterIds, setSelectedProjectAdapterIds] = useState<string[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  // Project initialization loading state
  const [projectInitializing, setProjectInitializing] = useState(false);
  // Chat channel (Lead ↔ User)
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatDraft, setChatDraft] = useState('');
  const [chatSending, setChatSending] = useState(false);
  const chatFeedRef = useRef<HTMLDivElement>(null);
  // Workspace files browser
  const [wsFiles, setWsFiles] = useState<Array<{ path: string; size_bytes: number; mime: string }>>([]);
  const [wsSelectedFile, setWsSelectedFile] = useState<string | null>(null);
  const [wsFileContent, setWsFileContent] = useState<string | null>(null);
  const [wsFileLoading, setWsFileLoading] = useState(false);
  // Project list (switcher)
  const [projectList, setProjectList] = useState<Array<{ name: string; path: string; current: boolean }>>([]);
  const [projectListOpen, setProjectListOpen] = useState(false);

  const selectedIssue = useMemo(
    () => issues.find((issue) => issue.id === selectedIssueId) || issues[0] || null,
    [issues, selectedIssueId],
  );

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
  const profileState = (profile: AdapterProfile) => {
    const provider = String(profile.provider || '').toLowerCase();
    const secretProvider = provider.includes('google') || provider.includes('gemini')
      ? 'google'
      : provider.includes('anthropic') || provider.includes('claude')
        ? 'anthropic'
        : provider.includes('openai') || provider.includes('codex')
          ? 'openai'
          : provider;
    const hasSecret = Boolean(secretProvider && secrets.some((secret) => secret.provider === secretProvider && secret.has_secret));
    const healthStatus = String(profile.health?.status || 'untested');
    const connected = healthStatus === 'ok' || (profile.channel === 'api' && hasSecret);
    const selectable = profile.status !== 'blocked_by_provider' && connected;
    const label = connected
      ? (healthStatus === 'ok' ? 'conectado y probado' : 'API key guardada')
      : healthStatus === 'installed'
        ? 'CLI instalado; login sin verificar'
        : profile.status === 'blocked_by_provider'
          ? 'bloqueado por proveedor'
          : profile.channel === 'api'
            ? 'falta API key'
            : 'sin conectar';
    return { connected, selectable, label, secretProvider };
  };

  const loadAppSettings = async () => {
    try {
      const res = await apiFetch('/api/settings');
      if (!res.ok) return;
      const data = (await res.json()) as { configured?: boolean; projects_root?: string; projects_root_effective?: string };
      const configured = Boolean(data.configured);
      setSettingsConfigured(configured);
      const effective = data.projects_root_effective || data.projects_root || '';
      setProjectsRoot(effective);
      if (!settingsDraft) setSettingsDraft(data.projects_root || '');
    } catch {
      // settings unavailable — treat as configured so we don't block the UI
      setSettingsConfigured(true);
    }
  };

  const saveAppSettings = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await apiFetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ projects_root: settingsDraft.trim() }),
      });
      if (!res.ok) {
        const err = (await res.json()) as { detail?: string };
        throw new Error(err.detail || `settings:${res.status}`);
      }
      const data = (await res.json()) as { configured?: boolean; projects_root_effective?: string };
      setSettingsConfigured(Boolean(data.configured));
      setProjectsRoot(data.projects_root_effective || settingsDraft.trim());
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'save_settings_failed');
    } finally {
      setLoading(false);
    }
  };

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

  const loadUserAdapters = async () => {
    try {
      const response = await apiFetch('/api/user-adapters');
      if (!response.ok) return;
      const json = (await response.json()) as {
        profiles?: AdapterProfile[];
        cli_status?: CliStatus[];
        secrets?: SecretInfo[];
      };
      const profiles = json.profiles || [];
      const nextSecrets = json.secrets || [];
      setAdapterProfiles(profiles);
      setSelectedProjectAdapterIds((current) => {
        if (current.length > 0) return current;
        const preferred = profiles
          .filter((profile) => profile.status !== 'blocked_by_provider')
          .filter((profile) => {
            const provider = String(profile.provider || '').toLowerCase();
            const secretProvider = provider.includes('google') || provider.includes('gemini')
              ? 'google'
              : provider.includes('anthropic') || provider.includes('claude')
                ? 'anthropic'
                : provider.includes('openai') || provider.includes('codex')
                  ? 'openai'
                  : provider;
            const hasSecret = nextSecrets.some((secret) => secret.provider === secretProvider && secret.has_secret);
            return String(profile.health?.status || '') === 'ok' || (profile.channel === 'api' && hasSecret);
          })
          .slice(0, 2)
          .map((profile) => profile.id);
        return preferred;
      });
      setCliStatus(json.cli_status || []);
      setSecrets(nextSecrets);
    } catch {
      // non-critical local setup panel
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

  const loadIssueThreads = async (nextIssues: Issue[]) => {
    const settled = await Promise.all(
      nextIssues.map(async (issue) => {
        const [commentsResponse, interactionsResponse] = await Promise.all([
          apiFetch(`/api/issues/${encodeURIComponent(issue.id)}/comments`),
          apiFetch(`/api/issues/${encodeURIComponent(issue.id)}/interactions`),
        ]);
        const commentsJson = (await commentsResponse.json()) as { comments?: Comment[]; detail?: string };
        const interactionsJson = (await interactionsResponse.json()) as { interactions?: Interaction[]; detail?: string };
        if (!commentsResponse.ok) throw new Error(commentsJson.detail || `comments:${commentsResponse.status}`);
        if (!interactionsResponse.ok) throw new Error(interactionsJson.detail || `interactions:${interactionsResponse.status}`);
        return {
          comments: (commentsJson.comments || []).map((comment) => ({ ...comment, issue_id: comment.issue_id || issue.id })),
          interactions: (interactionsJson.interactions || []).map((interaction) => ({
            ...interaction,
            issue_id: interaction.issue_id || issue.id,
          })),
        };
      }),
    );
    setComments(settled.flatMap((entry) => entry.comments));
    setInteractions(settled.flatMap((entry) => entry.interactions));
  };

  const loadProjectData = async (issueId = selectedIssueId, typeFilter = timelineTypeFilter) => {
    const timelineParams = new URLSearchParams({ limit: '300', order: 'desc' });
    if (typeFilter) timelineParams.set('type', typeFilter);
    const [issuesResponse, agentsResponse, runsResponse, timelineResponse] = await Promise.all([
      apiFetch('/api/issues'),
      apiFetch('/api/agents'),
      apiFetch('/api/runs?limit=100'),
      apiFetch(`/api/timeline?${timelineParams}`),
    ]);
    const issuesJson = (await issuesResponse.json()) as { issues?: Issue[]; detail?: string };
    const agentsJson = (await agentsResponse.json()) as { agents?: Agent[]; detail?: string };
    const runsJson = (await runsResponse.json()) as { runs?: Run[]; detail?: string };
    const timelineJson = (await timelineResponse.json()) as { items?: TimelineItem[]; detail?: string };
    if (!issuesResponse.ok) throw new Error(issuesJson.detail || `issues:${issuesResponse.status}`);
    if (!agentsResponse.ok) throw new Error(agentsJson.detail || `agents:${agentsResponse.status}`);
    if (!runsResponse.ok) throw new Error(runsJson.detail || `runs:${runsResponse.status}`);
    if (!timelineResponse.ok) throw new Error(timelineJson.detail || `timeline:${timelineResponse.status}`);

    const nextIssues = issuesJson.issues || [];
    const nextSelected = nextIssues.find((issue) => issue.id === issueId)?.id || nextIssues[0]?.id || '';
    setIssues(nextIssues);
    setAgents(agentsJson.agents || []);
    setRuns(runsJson.runs || []);
    setTimelineItems((timelineJson.items || []).map((item) => ({ ...item, detail: clip(item.detail || '') })));
    setSelectedIssueId(nextSelected);
    await loadIssueThreads(nextIssues);
    if (nextSelected) await loadPlanDocument(nextSelected);
    void loadChat();
    void loadWsFiles();
    void loadProjectList();
    void loadBudgets();
  };

  /**
   * Poll issue:intake every 2 s until the Lead has started running (status ≠ 'todo').
   * Shows the projectInitializing overlay during the wait.
   * Times out after 90 s so the user is never permanently blocked.
   */
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
      const [healthResponse, workspaceResponse] = await Promise.all([
        apiFetch('/api/health'),
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
    void refresh();
  }, []);

  // Auto-refresh every 20 s when a project is open (idle baseline)
  useEffect(() => {
    if (!workspaceConfigured) return undefined;
    const id = setInterval(() => {
      void loadProjectData(selectedIssueId, timelineTypeFilter).catch(() => {});
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
      void loadProjectData(selectedIssueId, timelineTypeFilter).catch(() => {});
    }, 2_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceConfigured, hasActiveRun, selectedIssueId, timelineTypeFilter]);

  const loadChat = async () => {
    try {
      const res = await apiFetch('/api/chat?limit=120');
      if (!res.ok) return;
      const json = (await res.json()) as { messages?: ChatMessage[] };
      setChatMessages(json.messages || []);
    } catch { /* ignore */ }
  };

  // Auto-scroll chat feed to bottom whenever messages change or chat tab becomes active
  useEffect(() => {
    if (viewMode !== 'chat') return;
    // requestAnimationFrame so the panel is painted before we scroll
    requestAnimationFrame(() => {
      const el = chatFeedRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }, [chatMessages, viewMode]);

  const loadWsFiles = async () => {
    try {
      const res = await apiFetch('/api/workspace/files');
      if (!res.ok) return;
      const json = (await res.json()) as { files?: Array<{ path: string; size_bytes: number; mime: string }> };
      setWsFiles(json.files || []);
    } catch { /* ignore */ }
  };

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

  const loadProjectList = async () => {
    try {
      const res = await apiFetch('/api/projects');
      if (!res.ok) return;
      const json = (await res.json()) as { projects?: Array<{ name: string; path: string; current: boolean }> };
      setProjectList(json.projects || []);
    } catch { /* ignore */ }
  };

  const loadBudgets = async () => {
    try {
      const res = await apiFetch('/api/budget');
      if (!res.ok) return;
      const json = (await res.json()) as { budgets?: BudgetInfo[] };
      setBudgets((json.budgets || []).filter((b) => b.budget_monthly_cents > 0));
    } catch { /* ignore */ }
  };

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

  // Pre-fetch role-aware model options for every member visible in hiring panels
  useEffect(() => {
    pendingInteractions.forEach((interaction) => {
      if (interaction.kind !== 'suggest_tasks') return;
      const rawPayload = (interaction as Interaction & { payload?: Record<string, unknown> }).payload;
      const team: ProposedTeamMember[] = (hiringDrafts[interaction.id] ?? (rawPayload?.proposed_team as ProposedTeamMember[])) || [];
      team.forEach((member) => {
        const profileId = String(member.adapter_profile_id || (member.adapter_config as Record<string,string> | undefined)?.profile_id || '');
        const role = member.role || '';
        if (profileId && role) void fetchRoleModelOptions(profileId, role);
      });
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingInteractions, hiringDrafts]);

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
          adapter_profile_ids: selectedProjectAdapterIds,
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
          assignee_agent_id: 'role:lead',
          metadata: { source: 'frontend_project_cockpit', wake_reason: 'new_task', profile: newTaskProfile },
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
      setSelectedIssueId(issue.id);
      setViewMode('chat');
      setLastResult({ issue: issueJson, comment: commentJson, wakeup: wakeupJson, run_once: runOnceJson });
      await loadProjectData(issue.id);
    } catch (taskError) {
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
        body: JSON.stringify(agentDraft),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `agent:${response.status}`);
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

  const saveSecret = async () => {
    if (!secretValue.trim()) return;
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/user-adapters/secrets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: secretProvider, name: 'default', secret: secretValue.trim() }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `secret:${response.status}`);
      setSecretValue('');
      setLastResult(json);
      await loadUserAdapters();
    } catch (secretError) {
      setError(secretError instanceof Error ? secretError.message : 'secret_save_failed');
    } finally {
      setLoading(false);
    }
  };

  const launchCliLogin = async (cliId: string) => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/user-adapters/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cli_id: cliId }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `login:${response.status}`);
      setLastResult(json);
      await loadUserAdapters();
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : 'subscription_login_failed');
    } finally {
      setLoading(false);
    }
  };

  const testAdapterProfile = async (profileId: string) => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch('/api/user-adapters/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profileId }),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `adapter-test:${response.status}`);
      setLastResult(json);
      await loadUserAdapters();
    } catch (testError) {
      setError(testError instanceof Error ? testError.message : 'adapter_test_failed');
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
    const modelOptions = activeModelProfile?.model_options ?? [];
    const currentModel = String(
      (agentDraft.adapter_config as Record<string,unknown>)?.model
      ?? (agent.adapter_config as Record<string,unknown>)?.model
      ?? '',
    );
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

          {/* Single adapter selector */}
          <div className="agent-form-field">
            <label className="agent-form-label">
              Adapter <InfoTip tip={FIELD_TIPS.adapter} wide />
            </label>
            <select
              className="agent-form-input"
              value={currentProfileId}
              onChange={(e) => {
                const val = e.target.value;
                const profile = adapterProfiles.find((p) => p.id === val);
                setAgentDraft((d) => ({
                  ...d,
                  adapter_type: profile?.adapter_type ?? d.adapter_type ?? agent.adapter_type ?? 'manual',
                  adapter_config: { ...(d.adapter_config || {}), profile_id: val, model: '' },
                }));
              }}
            >
              <option value="">— Sin adapter (sin ejecución automática)</option>
              {adapterProfiles.map((profile) => {
                const pState = profileState(profile);
                const statusPrefix = pState.connected ? '● ' : '○ ';
                const statusSuffix = !pState.connected ? ` — ${pState.label}` : '';
                return (
                  <option key={profile.id} value={profile.id}>
                    {statusPrefix}{profile.label}{statusSuffix}
                  </option>
                );
              })}
              <option value="" disabled>──────────────────────────</option>
              <option value="__custom__" disabled>⚙ Adapter personalizado — próximamente</option>
            </select>

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

          {/* Model override */}
          <div className="agent-form-field">
            <label className="agent-form-label">
              Modelo <InfoTip tip={FIELD_TIPS.model} wide />
            </label>
            <select
              className="agent-form-input"
              value={currentModel || String(activeModelProfile?.config?.model || '')}
              onChange={(e) => setAgentDraft((d) => ({
                ...d,
                adapter_config: { ...(d.adapter_config || {}), model: e.target.value },
              }))}
            >
              <option value="">Default del adapter</option>
              {modelOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
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
            <button onClick={() => void saveAgent(agent.id)} disabled={loading}>Guardar cambios</button>
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

  async function hireCatalogRole(roleId: string, roleDef: RoleDef) {
    setLoading(true);
    try {
      await apiFetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: `Contrata un agente con rol \`${roleId}\` (${roleDef.title}). ${roleDef.desc} Crea el issue correspondiente y asígnalo al agente apropiado.`,
          profile: 'full_team',
        }),
      });
      await loadProjectData(selectedIssueId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'hire_failed');
    } finally {
      setLoading(false);
    }
  }

  const resolveInteraction = async (interaction: Interaction, action: 'accept' | 'reject', note?: string) => {
    setLoading(true);
    setError('');
    try {
      const hiringTeam = hiringDrafts[interaction.id];
      const body: Record<string, unknown> = { action, resolved_by_user_id: 'user' };
      if (action === 'accept' && hiringTeam && interaction.kind === 'suggest_tasks') {
        body.resolution_data = { proposed_team: hiringTeam };
      } else if (action === 'accept' && note && note.trim() && interaction.kind === 'request_confirmation') {
        // Carry the user's free-text answer so the Lead can read it from result.resolution_data.user_note
        body.resolution_data = { user_note: note.trim() };
      }
      const response = await apiFetch(`/api/interactions/${encodeURIComponent(interaction.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.detail || `interaction:${response.status}`);
      // Clear the note after successful submission
      if (interaction.kind === 'request_confirmation') {
        setInteractionNotes((prev) => { const next = { ...prev }; delete next[interaction.id]; return next; });
      }
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
    setSelectedProjectAdapterIds((current) =>
      current.includes(profileId)
        ? current.filter((id) => id !== profileId)
        : [...current, profileId],
    );
  };

  const fetchRoleModelOptions = async (profileId: string, role: string): Promise<RoleModelOption[]> => {
    if (!profileId || !role) return [];
    const key = `${profileId}:${role}`;
    if (roleModelOptions[key]) return roleModelOptions[key];
    try {
      const res = await apiFetch(`/api/user-adapters/models?profile_id=${encodeURIComponent(profileId)}&role=${encodeURIComponent(role)}`);
      if (!res.ok) return [];
      const json = (await res.json()) as { options?: RoleModelOption[] };
      const opts = json.options || [];
      setRoleModelOptions((prev) => ({ ...prev, [key]: opts }));
      return opts;
    } catch {
      return [];
    }
  };

  const updateHiringMemberProfile = async (interactionId: string, team: ProposedTeamMember[], idx: number, profileId: string) => {
    const profile = adapterProfiles.find((p) => p.id === profileId);
    const role = team[idx]?.role || '';
    const opts = await fetchRoleModelOptions(profileId, role);
    const defaultModel = String(opts[0]?.value || profile?.config?.model || profile?.model_options?.[0]?.value || '');
    const updated = team.map((member, i) => i === idx ? {
      ...member,
      adapter_type: profile?.adapter_type || member.adapter_type || 'manual',
      adapter_profile_id: profileId,
      adapter_config: { ...(member.adapter_config || {}), profile_id: profileId, ...(defaultModel ? { model: defaultModel } : {}) },
      model: defaultModel,
    } : member);
    setHiringDrafts((d) => ({ ...d, [interactionId]: updated }));
  };

  const updateHiringMemberModel = (interactionId: string, team: ProposedTeamMember[], idx: number, model: string) => {
    const updated = team.map((member, i) => i === idx ? {
      ...member,
      model,
      adapter_config: { ...(member.adapter_config || {}), model },
    } : member);
    setHiringDrafts((d) => ({ ...d, [interactionId]: updated }));
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
                  return (
                    <div key={profile.id} className={`adapter-test-row health-${hStatus}`}>
                      <span className={`adapter-health-dot dot-${hStatus}`} />
                      <span className="adapter-test-label">{profile.label}</span>
                      <small className="adapter-test-detail">
                        {hStatus === 'ok' ? (h?.reason || 'OK') : hStatus === 'installed' ? 'CLI encontrado, sin auth' : hStatus === 'failed' ? (h?.reason || 'error') : 'sin test'}
                      </small>
                      {h?.hint && <small className="adapter-test-hint">{h.hint}</small>}
                      <button
                        type="button"
                        className="secondary-button"
                        style={{ fontSize: '0.7rem', minHeight: '28px', padding: '0 8px' }}
                        disabled={loading}
                        onClick={() => void testAdapterProfile(profile.id)}
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
                          {authOk ? 'Re-login' : 'Login'}
                        </button>
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
            <button onClick={() => void createProject()} disabled={loading || !projectName.trim() || selectedProjectAdapterIds.length === 0}>
              Crear proyecto
            </button>
          </div>
          <p className="hint">Raiz de proyectos: {projectsRoot || '...'}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="shell app-shell">
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
          <span className="brand-name">{workspace ? shortPath(workspace) : 'AI Teams'}</span>
        </div>
        <div className="top-actions">
          {hasPending && (
            <button
              className="secondary-button pending-alert-button"
              onClick={() => setViewMode('chat')}
              title="Hay decisiones pendientes"
            >
              <Bell size={16} />
              Pendiente
              <span className="notif-badge">{pendingInteractions.length}</span>
            </button>
          )}
          <button className="secondary-button" onClick={() => void wakeLead()} disabled={loading || !selectedIssue}>
            <Play size={16} />
            Despertar
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

          <div className="nav-column-issues">
            <div className="issue-list-header">
              <ListChecks size={14} />
              <span>Issues</span>
            </div>
            <div className="issue-list">
              {issues.map((issue) => (
                <button
                  className={issue.id === selectedIssue?.id ? 'issue-button active' : 'issue-button'}
                  key={issue.id}
                  onClick={() => {
                    setSelectedIssueId(issue.id);
                    setViewMode('issue');
                    void loadPlanDocument(issue.id);
                  }}
                >
                  <div className="issue-title-row">
                    <span>{issue.title}</span>
                    {issuesWithPending.has(issue.id) && <span className="pending-dot" title="Decisión pendiente" />}
                  </div>
                  <small>{statusLabel(issue.status)} · {issue.assignee_agent_id || 'sin owner'}</small>
                </button>
              ))}
            </div>
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

          {/* Nueva tarea — pinned at the bottom of the sidebar */}
          <div className="sidebar-new-task">
            <div className="sidebar-new-task-header">
              <Plus size={14} />
              <span>Nueva tarea</span>
            </div>
            <textarea
              className="task-input"
              placeholder="Describe la tarea para el Lead..."
              value={newTaskDraft}
              onChange={(event) => setNewTaskDraft(event.target.value)}
            />
            <div className="profile-selector">
              {PROFILE_OPTIONS.map((p) => (
                <button
                  key={p.value}
                  className={`profile-chip${newTaskProfile === p.value ? ' active' : ''}`}
                  onClick={() => setNewTaskProfile(p.value)}
                  title={p.desc}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <button
              className="sidebar-create-btn"
              onClick={() => void createTask()}
              disabled={loading || !newTaskDraft.trim()}
            >
              <Send size={14} />
              Crear tarea
            </button>
          </div>
        </aside>

        <section className="work-column">
          <nav className="view-tabs" aria-label="Vistas">
            <button className={viewMode === 'chat' ? 'tab active tab-chat' : `tab tab-chat${hasPending ? ' tab-chat-pending' : ''}`} onClick={() => setViewMode('chat')}>
              {hasPending ? <Bell size={16} /> : <MessageSquare size={16} />}
              Chat
              {hasPending ? <span className="notif-badge">{pendingInteractions.length}</span> : null}
            </button>
            <button className={viewMode === 'timeline' ? 'tab active' : 'tab'} onClick={() => setViewMode('timeline')}>
              <Clock3 size={16} />
              Timeline
            </button>
            <button className={viewMode === 'issue' ? 'tab active' : 'tab'} onClick={() => setViewMode('issue')}>
              <MessageSquare size={16} />
              Issue
            </button>
            <button className={viewMode === 'plan' ? 'tab active' : 'tab'} onClick={() => setViewMode('plan')}>
              <FileText size={16} />
              Plan
              {planDocument ? <span className="tab-badge">v{planDocument.revision_number}</span> : null}
            </button>
            <button className={viewMode === 'runs' ? 'tab active' : 'tab'} onClick={() => setViewMode('runs')}>
              <GitBranch size={16} />
              Runs
              {hasActiveRun ? <span className="tab-badge tab-badge-active" title="Run en progreso" /> : null}
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
              className={viewMode === 'config' ? 'tab active tab-config' : 'tab tab-config'}
              onClick={() => setViewMode('config')}
              title="Configuración"
            >
              <KeyRound size={16} />
              Config
            </button>
          </nav>

          {viewMode === 'timeline' ? (
            <section className="panel timeline-panel">
              <div className="panel-title">
                <Clock3 size={18} />
                Timeline
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
                    {t}
                  </button>
                ))}
              </div>
              <div className="timeline">
                {timelineItems.map((item) => (
                  <button
                    className={`timeline-item type-${item.type}`}
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
            <section className="panel issue-panel">
              {selectedIssue ? (
                <>
                  <div className="issue-header">
                    <div>
                      <h2>{selectedIssue.title}</h2>
                      <p>{selectedIssue.description || selectedIssue.title}</p>
                    </div>
                    <span className={`status-pill status-${selectedIssue.status}`}>{statusLabel(selectedIssue.status)}</span>
                  </div>
                  <div className="issue-meta">
                    <span>Owner: {selectedIssue.assignee_agent_id || 'sin asignar'}</span>
                    <span>Rol: {selectedIssue.role || '-'}</span>
                    <span>Complejidad: {selectedIssue.complexity || '-'}</span>
                    <span>Creada: {formatTime(selectedIssue.created_at)}</span>
                  </div>

                  {selectedInteractions.length ? (
                    <div className="inline-interactions">
                      {selectedInteractions.map((interaction) => (
                        <span key={interaction.id}>{interaction.title || interaction.kind}: {statusLabel(interaction.status)}</span>
                      ))}
                    </div>
                  ) : null}

                  <div className="thread">
                    {selectedIssue ? (
                      <ThreadView
                        issueId={selectedIssue.id}
                        preloadedComments={selectedComments}
                      />
                    ) : (
                      <p className="muted">Sin comentarios.</p>
                    )}
                  </div>

                  <div className="composer">
                    <textarea
                      placeholder="Añade contexto o una instruccion..."
                      value={commentDraft}
                      onChange={(event) => setCommentDraft(event.target.value)}
                    />
                    <button onClick={() => void addComment()} disabled={loading || !commentDraft.trim()}>
                      Enviar
                    </button>
                  </div>
                </>
              ) : (
                <p className="muted">Sin issue seleccionada.</p>
              )}
            </section>
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
              {planDocument ? (
                <>
                  <h3 style={{ margin: '0 0 0.5rem' }}>{planDocument.title}</h3>
                  <div className="plan-body">{renderMarkdownLite(planDocument.body)}</div>
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
            <section className={`panel chat-panel-main${hasPending ? ' has-pending' : ''}`}>
              {hasPending && (
                <div className="pending-banner">
                  <AlertCircle size={16} />
                  <strong>{pendingInteractions.length} decisión{pendingInteractions.length > 1 ? 'es' : ''} pendiente{pendingInteractions.length > 1 ? 's' : ''}</strong>
                  <span>El Lead espera tu respuesta</span>
                </div>
              )}
              <div className="chat-feed chat-feed-main" ref={chatFeedRef}>
                {chatMessages.length === 0 && (
                  <p className="muted chat-empty">Sin mensajes aún. Escribe algo al Lead o despiértalo para empezar.</p>
                )}
                {chatMessages.map((msg) => {
                  if (msg.item_type === 'interaction') {
                    const isPending = msg.interaction_status === 'pending';
                    const matchedInteraction = interactions.find((i) => i.id === msg.source_id);
                    if (!matchedInteraction && !isPending) return null;
                    const isHiring = msg.kind === 'suggest_tasks';
                    const rawPayload = msg.payload;
                    const proposedTeam: ProposedTeamMember[] = (rawPayload?.proposed_team as ProposedTeamMember[]) || [];
                    const suggestedIssues = (rawPayload?.suggested_issues as Array<Record<string,unknown>>) || [];
                    const hiringProfile = String(rawPayload?.profile || 'full_team');
                    const isDirect = rawPayload?.direct_work === true;
                    const hiringTeam = matchedInteraction ? (hiringDrafts[matchedInteraction.id] ?? proposedTeam) : proposedTeam;
                    return (
                      <div key={msg.id} className={`chat-interaction-card${isPending ? ' pending' : ' resolved'}`}>
                        <div className="chat-card-header">
                          {isPending ? <AlertCircle size={13} /> : <CheckCircle2 size={13} />}
                          <strong>{msg.title || msg.kind}</strong>
                          {!isPending && <span className="chat-resolved-badge">{msg.interaction_status}</span>}
                        </div>
                        <p className="chat-card-body">{msg.body}</p>
                        {isPending && matchedInteraction && isHiring && (
                          <div className="hiring-panel">
                            <div className="hiring-badge">Perfil: {hiringProfile}</div>
                            {isDirect ? (
                              <p className="hiring-direct">Solo Lead — ejecutará directamente sin contratar equipo.</p>
                            ) : (
                              <>
                                {hiringTeam.length > 0 && (
                                  <>
                                    <div className="hiring-header">Equipo — ajusta el adapter antes de contratar:</div>
                                    {hiringTeam.map((member, idx) => (
                                      <div className="hiring-member" key={member.id}>
                                        <span className="hiring-role">{member.role}</span>
                                        <span className="hiring-name">{member.name}</span>
                                        <select
                                          value={String(member.adapter_profile_id || (member.adapter_config || {}).profile_id || '')}
                                          onChange={(e) => void updateHiringMemberProfile(matchedInteraction.id, hiringTeam, idx, e.target.value)}
                                        >
                                          <option value="">Sin perfil</option>
                                          {adapterProfiles.filter((p) => p.status !== 'blocked_by_provider').map((profile) => (
                                            <option key={profile.id} value={profile.id}>{profile.label}</option>
                                          ))}
                                        </select>
                                        {(() => {
                                          const pId = String(member.adapter_profile_id || (member.adapter_config || {}).profile_id || '');
                                          const roleKey = `${pId}:${member.role || ''}`;
                                          const roleOpts = roleModelOptions[roleKey];
                                          const flatOpts = adapterProfiles.find((p) => p.id === pId)?.model_options || [];
                                          const displayOpts: RoleModelOption[] = roleOpts ?? flatOpts;
                                          const topRec = roleOpts?.find((o) => o.recommended);
                                          return (
                                            <>
                                              <select
                                                value={String(member.model || (member.adapter_config || {}).model || '')}
                                                onChange={(e) => updateHiringMemberModel(matchedInteraction.id, hiringTeam, idx, e.target.value)}
                                              >
                                                <option value="">Modelo default</option>
                                                {displayOpts.map((option) => (
                                                  <option key={option.value} value={option.value}>
                                                    {option.recommended ? '★ ' : ''}{option.label}{option.price_note ? ` (${option.price_note})` : ''}
                                                  </option>
                                                ))}
                                              </select>
                                              {topRec?.fit_reason && (
                                                <div className="hiring-model-hint" style={{ gridColumn: '3 / -1' }}>{topRec.fit_reason}</div>
                                              )}
                                            </>
                                          );
                                        })()}
                                      </div>
                                    ))}
                                  </>
                                )}
                                {suggestedIssues.length > 0 && (
                                  <>
                                    <div className="hiring-header" style={{ marginTop: '0.5rem' }}>Issues que se crearán:</div>
                                    {suggestedIssues.map((iss) => (
                                      <div className="hiring-issue" key={String(iss.id)}>
                                        <span className="hiring-delegation">{String(iss.delegation_type || 'work')}</span>
                                        <span className="hiring-issue-title">{String(iss.title || '')}</span>
                                        <span className="hiring-assignee">→ {String(iss.assignee_agent_id || iss.role || '?')}</span>
                                      </div>
                                    ))}
                                  </>
                                )}
                              </>
                            )}
                          </div>
                        )}
                        {isPending && matchedInteraction && !isHiring && (
                          <div className="interaction-note-area">
                            <textarea
                              placeholder="Escribe tu respuesta al Lead... (opcional — si no escribes nada, se enviará solo Aceptar)"
                              value={interactionNotes[matchedInteraction.id] || ''}
                              onChange={(e) => setInteractionNotes((prev) => ({ ...prev, [matchedInteraction.id]: e.target.value }))}
                              rows={3}
                              disabled={loading}
                            />
                          </div>
                        )}
                        {isPending && matchedInteraction && (
                          <div className="actions">
                            <button
                              onClick={() => void resolveInteraction(
                                matchedInteraction, 'accept',
                                isHiring ? undefined : interactionNotes[matchedInteraction.id]
                              )}
                              disabled={loading}
                            >
                              {isHiring ? (isDirect ? 'Iniciar (solo Lead)' : 'Contratar equipo') : 'Aceptar'}
                            </button>
                            <button className="danger-button" onClick={() => void resolveInteraction(matchedInteraction, 'reject')} disabled={loading}>
                              Rechazar
                            </button>
                          </div>
                        )}
                        <time className="chat-time">{formatTime(msg.created_at)}</time>
                      </div>
                    );
                  }
                  const isUser = msg.sender === 'user';
                  // Derive a readable display name from the author field (e.g. "role:engineer" → "Engineer")
                  const authorLabel = isUser
                    ? 'Tú'
                    : (msg.author || '')
                        .replace(/^role:/, '')
                        .replace(/_/g, ' ')
                        .replace(/\b\w/g, (c) => c.toUpperCase()) || 'Agente';
                  return (
                    <div key={msg.id} className={`chat-bubble${isUser ? ' user' : ' agent'}`}>
                      <div className="chat-bubble-meta">
                        <span className="chat-author">{authorLabel}</span>
                        <time className="chat-time">{formatTime(msg.created_at)}</time>
                      </div>
                      <div className="chat-bubble-body">{msg.body}</div>
                    </div>
                  );
                })}
              </div>
              <div className="chat-input-row chat-input-row-main">
                <input
                  type="text"
                  className="chat-input"
                  placeholder="Escribe al Lead... (Enter para enviar)"
                  value={chatDraft}
                  onChange={(e) => setChatDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void sendChatMessage(); } }}
                  disabled={chatSending}
                />
                <button
                  className="chat-send-btn"
                  onClick={() => void sendChatMessage()}
                  disabled={chatSending || !chatDraft.trim()}
                  title="Enviar"
                >
                  <Send size={15} />
                </button>
                <button
                  className="secondary-button"
                  onClick={() => void loadChat()}
                  title="Actualizar"
                  type="button"
                  style={{ padding: '0 10px' }}
                >
                  <RefreshCcw size={14} />
                </button>
              </div>
            </section>
          ) : null}

          {viewMode === 'runs' ? (
            <section className="panel runs-panel">
              <div className="runs-layout">
                <div className="run-list-col">
                  <div className="panel-title">
                    <GitBranch size={18} />
                    Runs
                  </div>
                  <div className="run-table">
                    {runs.map((run) => (
                      <button
                        className={`run-row${selectedRun?.id === run.id ? ' active' : ''} status-bg-${run.status}`}
                        key={run.id}
                        onClick={() => { setRunId(run.id); void loadRunDetail(run.id); }}
                      >
                        <div className="run-row-header">
                          <span className={`status-pill status-${run.status}`}>{statusLabel(run.status)}</span>
                          <span className="run-time">{formatTime(run.started_at || run.created_at)}</span>
                        </div>
                        <span className="run-id-label">{run.id.slice(-12)}</span>
                        <small>{run.agent_id}</small>
                      </button>
                    ))}
                  </div>
                  <div className="form-row compact-row">
                    <input placeholder="run id completo" value={runId} onChange={(event) => setRunId(event.target.value)} />
                    <button onClick={() => void loadRunDetail(runId)} disabled={loading || !runId.trim()}>Ver</button>
                  </div>
                </div>

                {selectedRun ? (
                  <div className="run-detail-col">
                    <div className="panel-title">
                      <Activity size={18} />
                      Detalle
                    </div>
                    <dl className="run-meta">
                      <dt>ID</dt><dd>{selectedRun.id}</dd>
                      <dt>Agente</dt><dd>{selectedRun.agent_id}</dd>
                      <dt>Issue</dt><dd>{selectedRun.issue_id || '-'}</dd>
                      <dt>Estado</dt>
                      <dd><span className={`status-pill status-${selectedRun.status}`}>{statusLabel(selectedRun.status)}</span></dd>
                      {selectedRun.error ? <><dt>Error</dt><dd className="run-error">{selectedRun.error}{selectedRun.error_code ? ` (${selectedRun.error_code})` : ''}</dd></> : null}
                      {selectedRun.actual_cost_cents ? <><dt>Coste</dt><dd>{selectedRun.actual_cost_cents}¢</dd></> : null}
                      <dt>Inicio</dt><dd>{formatTime(selectedRun.started_at || selectedRun.created_at)}</dd>
                      <dt>Fin</dt><dd>{formatTime(selectedRun.finished_at) || '-'}</dd>
                    </dl>
                    {runEvents.length > 0 ? (
                      <div className="run-events">
                        <div className="run-events-header">Eventos ({runEvents.length})</div>
                        {runEvents.map((ev) => {
                          // file_ops: show a nice file list instead of raw JSON
                          if (ev.event_type === 'file_ops' && ev.payload) {
                            const ops = (ev.payload.ops as Array<{ op: string; path: string }> | undefined) || [];
                            return (
                              <div className="run-event run-event-fileops stream-tool" key={ev.id}>
                                <span className="ev-seq">#{ev.seq}</span>
                                <span className="ev-type ev-type-fileops">
                                  <FolderOpen size={13} style={{ marginRight: 4, verticalAlign: 'middle' }} />
                                  file_ops ({ops.length})
                                </span>
                                {ops.length > 0 && (
                                  <ul className="ev-fileops-list">
                                    {ops.map((op, i) => (
                                      <li key={i} className={`ev-fileop ev-fileop-${op.op}`}>
                                        <span className="ev-fileop-badge">{op.op}</span>
                                        <span className="ev-fileop-path">{op.path}</span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                <time>{formatTime(ev.created_at)}</time>
                              </div>
                            );
                          }
                          const text = ev.payload?.text
                            ? String(ev.payload.text).slice(0, 300)
                            : ev.payload
                              ? JSON.stringify(ev.payload).slice(0, 200)
                              : '';
                          return (
                            <div className={`run-event stream-${ev.stream || 'none'}`} key={ev.id}>
                              <span className="ev-seq">#{ev.seq}</span>
                              <span className="ev-type">{ev.event_type}{ev.stream ? `/${ev.stream}` : ''}</span>
                              {text ? <p className="ev-body">{text}</p> : null}
                              <time>{formatTime(ev.created_at)}</time>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="muted">Sin eventos registrados.</p>
                    )}
                  </div>
                ) : (
                  <div className="run-detail-col muted-center">
                    <p className="muted">Selecciona una run para ver su detalle y eventos.</p>
                  </div>
                )}
              </div>
            </section>
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
            <section className="panel config-panel">
              <div className="panel-title">
                <KeyRound size={18} />
                Configuración
              </div>

              {/* ── 1. Espacio de trabajo ────────────────────────────────── */}
              <div className="config-section">
                <h3 className="config-section-title">
                  Espacio de trabajo
                  <InfoTip tip="Gestiona dónde se guardan los proyectos y cuál está activo ahora mismo." wide />
                </h3>

                {/* Carpeta raíz */}
                <div className="config-subsection">
                  <div className="config-subsection-label">
                    Carpeta raíz de proyectos
                    <InfoTip tip="Todos los proyectos se crean como subcarpetas aquí. Cambiarla no mueve proyectos existentes. También configurable con AITEAM_PROJECTS_ROOT en .env (tiene prioridad)." wide />
                  </div>
                  <div className="config-field-row">
                    <input
                      className="config-path-input"
                      value={settingsDraft || projectsRoot}
                      onChange={(ev) => setSettingsDraft(ev.target.value)}
                      placeholder="Ruta absoluta de la carpeta de proyectos"
                    />
                    <button
                      className="config-inline-btn"
                      onClick={() => void saveAppSettings().then(() => void refresh())}
                      disabled={loading || !(settingsDraft || '').trim()}
                    >
                      Guardar
                    </button>
                  </div>
                  {projectsRoot && (
                    <p className="config-hint">Efectiva: <code>{projectsRoot}</code></p>
                  )}
                </div>

                {/* Proyecto activo */}
                <div className="config-subsection">
                  <div className="config-subsection-label">
                    Proyecto activo
                    <InfoTip tip="El proyecto que el backend tiene abierto ahora. Cada proyecto tiene su propia base de datos SQLite." wide />
                  </div>
                  <dl className="config-dl config-dl-compact">
                    <dt>Estado</dt>
                    <dd>
                      <span className={`cfg-status-chip${health?.status === 'ok' ? ' ok' : ''}`}>
                        {health?.status || '—'}
                      </span>
                    </dd>
                    <dt>Modo</dt><dd>{health?.mode || '—'}</dd>
                    <dt>Ruta</dt><dd className="config-path">{workspace || '—'}</dd>
                  </dl>
                  <div className="project-switcher">
                    <button
                      className="secondary-button project-switch-btn"
                      onClick={() => { void loadProjectList(); setProjectListOpen((v) => !v); }}
                    >
                      <FolderOpen size={14} />
                      Cambiar proyecto
                    </button>
                    {projectListOpen && (
                      <div className="project-list-popup">
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
                  <details className="config-advanced">
                    <summary>Abrir ruta manualmente (avanzado)</summary>
                    <div className="config-field-row" style={{ marginTop: '8px' }}>
                      <input
                        value={workspaceDraft}
                        onChange={(event) => setWorkspaceDraft(event.target.value)}
                        placeholder="Ruta absoluta al proyecto"
                      />
                      <button className="config-inline-btn" onClick={() => void saveWorkspace()} disabled={loading}>
                        Aplicar
                      </button>
                    </div>
                  </details>
                </div>
              </div>

              {/* ── 2. Credenciales y Adapters ────────────────────────────── */}
              <div className="config-section">
                <h3 className="config-section-title">
                  Credenciales y Adapters
                  <InfoTip tip="Las API keys se envían al backend local y se cifran en vault. No se guardan en el navegador ni en localStorage." wide />
                </h3>

                {/* API Keys */}
                <div className="config-subsection">
                  <div className="config-subsection-label">
                    API Keys
                    <InfoTip tip="Cada key activa los adapters de API directa de ese proveedor. Una vez guardada solo se indica si existe — no se puede leer de vuelta." wide />
                  </div>
                  <div className="api-key-rows">
                    {([
                      { id: 'openai',    label: 'OpenAI',         desc: 'GPT-4.1, o1, o3…' },
                      { id: 'google',    label: 'Google Gemini',  desc: 'Gemini 2.5 Flash / Pro…' },
                      { id: 'anthropic', label: 'Anthropic',      desc: 'Claude Sonnet / Opus 4.5' },
                    ] as const).map((prov) => {
                      const saved = secrets.some((s) => s.provider === prov.id && s.has_secret);
                      const isActive = secretProvider === prov.id;
                      return (
                        <div key={prov.id} className={`api-key-row${saved ? ' key-saved' : ''}`}>
                          <div className="api-key-row-meta">
                            <span className={`api-key-dot${saved ? ' saved' : ''}`} />
                            <span className="api-key-label">{prov.label}</span>
                            <span className="api-key-models">{prov.desc}</span>
                            <span className={`api-key-badge${saved ? ' ok' : ''}`}>
                              {saved ? 'key guardada ✓' : 'sin key'}
                            </span>
                          </div>
                          <div className="api-key-row-input">
                            <input
                              type="password"
                              placeholder={saved ? '●●●●●●  (guardada — pega nueva para actualizar)' : 'sk-…  Pega tu API key aquí'}
                              value={isActive ? secretValue : ''}
                              onFocus={() => setSecretProvider(prov.id)}
                              onChange={(e) => { setSecretProvider(prov.id); setSecretValue(e.target.value); }}
                            />
                            <button
                              className="config-inline-btn"
                              disabled={loading || !isActive || !secretValue.trim()}
                              onClick={() => void saveSecret()}
                            >
                              {saved ? 'Actualizar' : 'Guardar'}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* CLIs de suscripción */}
                <div className="config-subsection">
                  <div className="config-subsection-label">
                    CLIs de suscripción
                    <InfoTip tip="Si tienes una suscripción activa a ChatGPT Plus, Claude Pro o Gemini Advanced, el CLI correspondiente puede ejecutar agentes sin consumir créditos de API. Requiere instalar el CLI y hacer login en tu cuenta." wide />
                  </div>
                  <div className="cli-status-grid">
                    {cliStatus.map((item) => (
                      <div key={item.id} className={`cli-card${item.available ? ' ok' : ''}`}>
                        <div className="cli-card-header">
                          <span className={`cli-dot${item.available ? ' ok' : ''}`} />
                          <span className="cli-card-label">{item.label}</span>
                          <span className="cli-card-avail">{item.available ? 'disponible' : 'no instalado'}</span>
                        </div>
                        <code className="cli-command">{item.login_command || item.command}</code>
                        {item.login_supported && (
                          <button
                            type="button"
                            onClick={() => void launchCliLogin(item.id)}
                            disabled={loading || !item.available}
                            title={item.login_hint}
                          >
                            Login
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Estado de adapters */}
                <div className="config-subsection">
                  <div className="config-subsection-label">
                    Estado de adapters
                    <InfoTip tip="Prueba un adapter para verificar que su credencial es válida y la llamada funciona. El resultado se guarda y actualiza los indicadores de conexión en toda la UI." wide />
                  </div>
                  <div className="adapter-test-list">
                    {adapterProfiles.map((profile) => {
                      const pState = profileState(profile);
                      const healthStatus = profile.health?.status || 'untested';
                      return (
                        <div
                          key={profile.id}
                          className={`adapter-test-row${pState.connected ? ' connected' : ''}${profile.status === 'blocked_by_provider' ? ' blocked' : ''}`}
                        >
                          <span className={`adapter-row-dot${pState.connected ? ' connected' : profile.status === 'blocked_by_provider' ? ' blocked' : ''}`} />
                          <div className="adapter-test-info">
                            <span className="adapter-test-label">{profile.label}</span>
                            <span className="adapter-test-meta">{profile.adapter_type} · {profile.channel}</span>
                          </div>
                          <span className={`adapter-test-status hs-${healthStatus}`}>
                            {profile.status === 'blocked_by_provider' ? 'bloqueado por proveedor'
                              : healthStatus === 'ok'    ? `funcional${profile.health?.reason ? ` · ${profile.health.reason}` : ''}`
                              : healthStatus === 'failed'    ? `falló: ${profile.health?.reason || 'test'}`
                              : healthStatus === 'installed' ? 'instalado, auth sin verificar'
                              : 'sin probar'}
                          </span>
                          <button
                            className="secondary-button"
                            type="button"
                            disabled={loading || profile.status === 'blocked_by_provider'}
                            onClick={() => void testAdapterProfile(profile.id)}
                          >
                            Probar
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* ── 3. Sistema ───────────────────────────────────────────── */}
              <div className="config-section">
                <h3 className="config-section-title">Sistema</h3>
                <dl className="config-dl config-dl-compact">
                  <dt>Backend</dt><dd><code>{window.location.origin}</code></dd>
                  <dt>Modo</dt><dd>{health?.mode || '—'}</dd>
                  <dt>Var. entorno</dt>
                  <dd>
                    <code>AITEAM_PROJECTS_ROOT</code> en <code>.env</code> sobreescribe la carpeta raíz guardada.
                    <InfoTip tip="Si defines AITEAM_PROJECTS_ROOT en el archivo .env del proyecto, tiene prioridad sobre lo que configures en esta pantalla. Útil para CI/CD o instalaciones sin UI." wide />
                  </dd>
                </dl>
              </div>

              {/* ── 4. Zona de peligro ───────────────────────────────────── */}
              <div className="config-section danger-config-section">
                <h3 className="config-section-title danger-config-title">
                  Zona de peligro
                  <InfoTip tip="Las acciones de esta sección son irreversibles. No hay papelera de reciclaje: el proyecto se borra permanentemente del disco." wide />
                </h3>
                <div className="danger-zone">
                  <div className="danger-zone-desc">
                    <strong>Eliminar proyecto actual</strong>
                    <p>
                      Borra la carpeta completa del proyecto{workspace ? `: ${shortPath(workspace)}` : ''}.
                      Esta acción no se puede deshacer.
                    </p>
                  </div>
                  <div className="delete-row">
                    <input
                      value={deleteConfirm}
                      onChange={(event) => setDeleteConfirm(event.target.value)}
                      placeholder="Escribe DELETE para confirmar"
                    />
                    <button
                      className="danger-button"
                      onClick={() => void deleteProject()}
                      disabled={loading || deleteConfirm !== 'DELETE'}
                    >
                      Eliminar proyecto
                    </button>
                  </div>
                </div>
              </div>

              {/* ── Debug: última acción (colapsable) ───────────────────── */}
              {lastResult ? (
                <details className="config-section config-debug">
                  <summary>Última acción — debug</summary>
                  <pre className="last-result-body">{pretty(lastResult).slice(0, 1200)}</pre>
                </details>
              ) : null}
            </section>
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
                      return [r, r.startsWith('role:') ? r : `role:${r}`];
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
                    Para activar más adapters, añade tus API keys en <strong>Config</strong>. Para crear adapters personalizados (modelo propio, CLI alternativo, Ollama) → <strong>próximamente desde la UI</strong>; por ahora edita <code>~/.config/aiteams/adapter_profiles.json</code>.
                  </p>
                </div>
              )}
            </section>
          ) : null}
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
    </main>
  );
}
