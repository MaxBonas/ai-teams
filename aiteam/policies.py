"""Declarative role & flow policies for AI Teams (role enforcement, fase 5).

Single inspectable home for the rules that keep the hierarchy honest:
who belongs to which tier, what ops each tier may emit, which issue-status
transitions workers may perform, and the breaker thresholds/windows. The
executor, work contract and adapter-policy modules import from here — a rule
change is one edit in one file, reviewable at a glance and covered by tests.

This module is intentionally a LEAF: stdlib imports only, no aiteam imports.
Pure data plus tiny env-reading helpers (AgentSpec-style externalized rules).
"""

from __future__ import annotations

import os

# ── Tiers y roles ─────────────────────────────────────────────────────────────

LEAD_TIER_ROLES = frozenset({"lead", "team_lead", "lead_executor"})

TIER2_ROLES = frozenset({"engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "worker", "test_designer"})

TIER3_ROLES = frozenset({"file_scout", "web_scout", "context_curator", "test_runner"})

# Hiring policy tiers (model selection): strong models for seniors,
# cheap/local for juniors.
SENIOR_ROLES = frozenset({"lead", "team_lead", "reviewer", "quorum_senior", "quorum_auditor", "architect"})
JUNIOR_ROLES = frozenset({"engineer", "test_runner", "worker", "file_scout", "web_scout", "context_curator", "test_designer"})

# Roles that must never edit workspace files: they delegate (Lead) or report
# (scouts/curator). Enforced via CLI read-only sandbox, the preventive
# file_ops gate, and the role.violation audit.
NON_EDITING_ROLES = frozenset({
    "lead", "team_lead", "file_scout", "web_scout", "context_curator",
    "quorum_auditor", "quorum_senior",
})

# ── Quorum de planificación ──────────────────────────────────────────────────
# El quorum reduce riesgo de decisiones críticas y ambiguas; no es un equipo
# de ejecución ni un multiplicador universal de agentes. Dos revisiones son
# el objetivo canónico; si el equipo aceptado solo contiene un senior externo
# al Lead, una revisión válida sigue permitiendo síntesis y queda observable
# como quorum reducido. El máximo evita fan-out/coste sin límite.
QUORUM_ABSOLUTE_MIN_VALID_CONTRIBUTIONS = 1
QUORUM_MIN_VALID_CONTRIBUTIONS = 2
QUORUM_MAX_CONTRIBUTIONS = 4
QUORUM_MAX_SYNTHESIS_ATTEMPTS = 2

# Adapter types that call a remote LLM in-process (as opposed to CLI/builtin).
LLM_ADAPTER_TYPES = frozenset({"anthropic_api", "anthropic_sonnet", "openai_api", "gemini_api"})

# ── Fallos de infraestructura (no de producto) ─────────────────────────────────
# Un run con uno de estos error_code falló en el transporte del proveedor o en
# el entorno del CLI, NO por una mala decisión del agente. Fuente única para:
# el backoff de reintento (liveness), el que no cuenten como lead.unblock_skipped,
# y la métrica de salud del router (provider_router_health) — una tasa alta de
# estos por proveedor significa "el proveedor está fallando", no "el equipo es malo".
INFRA_ERROR_CODES = frozenset({
    "api_error",                     # 429 / timeout / 5xx del proveedor (http_retry agotado)
    "subscription_cli_not_found",    # binario del CLI ausente / config rota
    "subscription_cli_nonzero_exit", # el CLI salió !=0 (auth, modelo rechazado…)
    "subscription_cli_usage_limit",  # cuota de suscripción agotada; reintento inmediato inútil
    "subscription_cli_timeout",      # el CLI no terminó en su ventana
    "subscription_cli_error",        # excepción lanzando el CLI
    "subscription_cli_parse_error",  # salida del CLI ilegible
    "liveness_timeout",              # el proceso se colgó y el liveness lo mató
})


def payload_delta_enabled() -> bool:
    """Dieta de contexto: si el workspace NO cambió desde la última run
    completada del mismo agente sobre la misma issue, los cuerpos de archivo
    no se re-inyectan en el wake (solo la lista de paths + nota).

    El overhead de coordinación sin retorno es lo que hace a un equipo más
    caro que un agente único: el engineer de CLI Tareas consumió 487K tokens
    de entrada en 2 runs re-recibiendo un workspace que no había cambiado.
    Apagar con ``AITEAM_PAYLOAD_DELTA=0``.
    """
    import os
    return os.environ.get("AITEAM_PAYLOAD_DELTA", "").strip().lower() not in {"0", "false", "no"}


def adversarial_qa_mode() -> str:
    """Pase adversarial post-implementación: 'high' (default) | 'always' | 'off'.

    Un agente único nunca se ataca a sí mismo con otra distribución de sesgos;
    el equipo sí puede. En criticidad alta (o siempre, con 'always') se
    materializa un QA cuyo contrato es aportar SOLO tests que fallen — si tras
    intentarlo no encuentra fallo, aprueba con evidencia de qué intentó.
    Env: ``AITEAM_ADVERSARIAL_QA``.
    """
    import os
    raw = os.environ.get("AITEAM_ADVERSARIAL_QA", "").strip().lower()
    return raw if raw in {"high", "always", "off"} else "high"


def cross_provider_review_enforced() -> bool:
    """Review cross-provider VINCULANTE en issues high/critical (default ON).

    Un juez de la misma familia que el generador favorece sus salidas (~10% de
    win rate extra medido) — la mitigación primaria es juez de otra familia.
    `_separation_of_duties_line` ya lo señala; en criticidad alta además se
    fuerza, re-apuntando el reviewer a otro proveedor conectado si existe.
    Apagar con ``AITEAM_CROSS_PROVIDER_REVIEW=0``.
    """
    import os
    return os.environ.get("AITEAM_CROSS_PROVIDER_REVIEW", "").strip().lower() not in {"0", "false", "no"}


# ── Paralelismo por canal (opt-in) ─────────────────────────────────────────────
# El heartbeat es secuencial por diseño; con el flag activo puede ejecutar en
# paralelo runs de PROVEEDORES distintos bajo restricciones estrictas. La
# restricción crítica es el workspace compartido: dos runs que editan o
# verifican archivos a la vez corrompen la atribución de deltas (snapshot
# before/after) y la evidencia de review/tests. Por eso el batch admite COMO
# MÁXIMO UN rol de "slot de trabajo" (edita o verifica el workspace); los
# roles de lectura tolerante (lead-tier, scouts) pueden acompañarlo.
WORK_SLOT_ROLES = frozenset({
    "engineer", "software_engineer", "worker", "qa", "qa_engineer",
    "reviewer", "code_reviewer", "test_runner", "test_designer",
})


def independent_tests_enabled() -> bool:
    """Tests de aceptación escritos por un agente DISTINTO del implementador.

    La ventaja estructural del equipo sobre un agente único: el engineer que
    escribe sus propios tests produce una suite que confirma lo que el código
    hace, no lo que la spec pide (mismo sesgo que un soloista). Con esto, al
    delegar trabajo de engineering se materializa un `test_designer` hermano
    que escribe la suite SOLO desde la especificación, en paralelo, y el
    test_runner ejecuta ambas. Apagar con ``AITEAM_INDEPENDENT_TESTS=0``.
    """
    import os
    return os.environ.get("AITEAM_INDEPENDENT_TESTS", "").strip().lower() not in {"0", "false", "no"}


def parallel_channels_enabled() -> bool:
    """Flag opt-in del dispatch paralelo por canal (default OFF)."""
    import os
    return os.environ.get("AITEAM_PARALLEL_CHANNELS", "").strip().lower() in {"1", "true", "yes"}


def parallel_batch_max() -> int:
    """Tamaño máximo del batch paralelo (default 3, mín 2)."""
    import os
    raw = os.environ.get("AITEAM_PARALLEL_MAX", "").strip()
    try:
        value = int(raw) if raw else 3
    except ValueError:
        return 3
    return max(2, value)


def daily_cost_cap_cents() -> int:
    """Techo duro de gasto real (céntimos) por día natural UTC para TODO el
    proyecto. A diferencia del cost_breaker de gasto-sin-progreso (por subárbol,
    se resetea al progresar), este es un límite global independiente del
    progreso: es la mitigación del *cascade pile-up* (un runaway que sigue
    haciendo micro-cambios evade el breaker de progreso pero debe topar aquí).

    El canal de suscripción registra 0 céntimos, así que este cap solo muerde
    el gasto real por-token (API) — que es exactamente lo que protege dinero.
    Env ``AITEAM_DAILY_COST_CAP_CENTS`` (default 0 = desactivado, opt-in).
    """
    import os
    raw = os.environ.get("AITEAM_DAILY_COST_CAP_CENTS", "").strip()
    try:
        value = int(raw) if raw else 0
    except ValueError:
        return 0
    return value if value > 0 else 0


def provider_escalation_threshold() -> float:
    """Fracción de runs de infra por encima de la cual un proveedor se marca
    'unhealthy' en el router. A diferencia de una cascada de calidad (banda sana
    5-50%), aquí escalar = recuperarse de un fallo: 0% es lo ideal y alto es malo.
    Env-tunable: ``AITEAM_PROVIDER_ESCALATION_THRESHOLD`` (default 0.25).
    """
    import os
    raw = os.environ.get("AITEAM_PROVIDER_ESCALATION_THRESHOLD", "").strip()
    try:
        value = float(raw) if raw else 0.25
    except ValueError:
        return 0.25
    return value if 0.0 < value <= 1.0 else 0.25

# ── Waiver de verificación runtime ─────────────────────────────────────────────
# Contrato entre el quality gate de tests y las escalaciones al usuario: cuando
# el entorno no puede ejecutar la suite, el Lead (o el propio sistema, tras
# denegaciones repetidas) escala una request_confirmation cuyo payload lleva
# este reason. Si el usuario la ACEPTA, el gate test_runner_exit_zero_required
# queda dispensado para esa issue — sin esto, la decisión explícita del usuario
# ("cierra sin pytest") era ignorada en silencio y el proyecto quedaba en
# deadlock (visto en vivo en el proyecto CLI Notas, 2026-07-15).
RUNTIME_VERIFICATION_WAIVER_REASON = "runtime_verification_unavailable"

# ── Directorios de ruido a excluir en cualquier escaneo del workspace ──────────
# Auto-generados por herramientas/VCS — nunca código del proyecto. Antes vivía
# triplicado (workspace_evidence.py, executor.py, api/routers/workspace.py) sin
# entradas de Unity, lo que hacía que un package.json dentro de
# Library/PackageCache/ (cache interna de Unity, no un proyecto Node) disparase
# el quality gate "hay tests en el workspace" indefinidamente — issue:intake
# nunca podía cerrarse porque no existe (ni puede existir) un test_runner real
# para una suite que no existe. También inflaba el listado de archivos y el
# presupuesto de workspace_files con cientos de ficheros de caché irrelevantes.
WORKSPACE_NOISE_DIRS = frozenset({
    # control de versiones
    ".git", ".hg", ".svn",
    # control plane de AI Teams
    ".aiteam",
    # Python
    "__pycache__", ".venv", "venv", ".pytest_cache",
    # JS/Node
    "node_modules", "dist", "build", ".next",
    # Unity — carpetas auto-generadas, nunca fuente del proyecto
    "Library", "Temp", "Logs", "Obj", "obj", "UserSettings", "Build", "Builds",
    ".vs",
})

# ── Matriz RBAC de ops ────────────────────────────────────────────────────────
# DENYLIST per tier, enforced in code regardless of what the prompt said:
#   Tier 1 — full vocabulary (orchestrates).
#   Tier 2 — works and reports; never hires, directs siblings or rewrites the plan.
#   Tier 3 — reads and reports only.

OPS_FORBIDDEN_FOR_TIER3 = frozenset({
    "create_issue",
    "create_interaction",
    "update_plan",
    "update_child_issue",
    "write_file",
    "append_file",
    "delete_file",
    "accept_quorum_synthesis",
})

OPS_FORBIDDEN_FOR_TIER2 = frozenset({
    "create_issue",
    "update_plan",
    "update_child_issue",
    "accept_quorum_synthesis",
})

_CONTEXT_CURATOR_EXCLUSIVE_OPS = frozenset({"append_context_summary"})


def forbidden_ops_for_role(role: str) -> frozenset[str]:
    role_key = str(role or "").strip().lower()
    exclusive = frozenset() if role_key == "context_curator" else _CONTEXT_CURATOR_EXCLUSIVE_OPS
    if role_key in {"quorum_auditor", "quorum_senior"}:
        return OPS_FORBIDDEN_FOR_TIER3 | exclusive
    if role_key in TIER3_ROLES:
        return OPS_FORBIDDEN_FOR_TIER3 | exclusive
    if role_key in TIER2_ROLES:
        return OPS_FORBIDDEN_FOR_TIER2 | exclusive
    return exclusive


# ── Máquina de estados de issue por rol ──────────────────────────────────────
# Target statuses a worker (non-lead) may set on its OWN issue. `todo` and
# `backlog` excluded: self-requeue is loop fuel — only the Lead re-queues.
# `cancelled` allowed: liveness honours it as a deliberate terminal declaration.

WORKER_ALLOWED_TARGET_STATUSES = frozenset({"in_progress", "in_review", "done", "blocked", "cancelled"})
TERMINAL_ISSUE_STATUSES = frozenset({"done", "cancelled"})

# ── Breakers y ventanas (env-tunable) ────────────────────────────────────────

CIRCUIT_BREAKER_SKIP_THRESHOLD = 3     # lead.unblock_skipped antes de escalar
MAX_TIMEOUT_RETRIES = 2                # reintentos con prompt reducido tras timeout
DELEGATION_CHURN_WINDOW_HOURS = 6
DELEGATION_CHURN_ROLES = frozenset({"engineer", "software_engineer", "reviewer", "code_reviewer"})


def cost_breaker_threshold_cents() -> int:
    """Spend per subtree without workspace progress before escalating.

    AITEAM_COST_BREAKER_CENTS; 0 (or negative) disables. Default 300.
    """
    return _env_int("AITEAM_COST_BREAKER_CENTS", 300)


def delegation_churn_limit() -> int:
    """Same-role children under one parent per window before escalating.

    AITEAM_DELEGATION_CHURN_LIMIT; 0 (or negative) disables. Default 8.
    """
    return _env_int("AITEAM_DELEGATION_CHURN_LIMIT", 8)


def cost_policy_enforced() -> bool:
    """Hard cost-policy enforcement (Tier 3 never bills per-token while a
    connected zero-cost channel exists). AITEAM_ENFORCE_COST_POLICY."""
    return _env_flag("AITEAM_ENFORCE_COST_POLICY")


def workspace_files_budget_bytes() -> int:
    """Total bytes of file CONTENT injected as workspace_files into a wake
    payload (every file always appears with path+size; content stops here).
    AITEAM_WS_FILES_BUDGET_BYTES; default 49152 (48 KB)."""
    return _env_int("AITEAM_WS_FILES_BUDGET_BYTES", 49152)


def workspace_file_max_bytes() -> int:
    """Per-file content cap inside workspace_files.
    AITEAM_WS_FILE_MAX_BYTES; default 8192."""
    return _env_int("AITEAM_WS_FILE_MAX_BYTES", 8192)


def rereview_limit() -> int:
    """Completed runs on one reviewer/QA issue before another wake escalates
    to the user instead of executing (the Lead can otherwise re-wake the
    reviewer indefinitely without changing anything about its evidence).
    AITEAM_REREVIEW_LIMIT; 0 (or negative) disables. Default 4."""
    return _env_int("AITEAM_REREVIEW_LIMIT", 4)


# ── Política de autonomía (P5) ───────────────────────────────────────────────
# supervised  — every escalation waits for the user (default).
# autonomous  — OPERATIONAL escalations self-resolve with their safe default,
#               once per (issue, reason); a repeat of the same escalation means
#               the default didn't work and promotes to the user. PRODUCT
#               decisions (cycle close, scope, team approval) always wait.

AUTONOMY_SUPERVISED = "supervised"
AUTONOMY_AUTONOMOUS = "autonomous"
AUTONOMY_MODES = frozenset({AUTONOMY_SUPERVISED, AUTONOMY_AUTONOMOUS})

# reason → safe default action. Anything NOT in this map is a product decision.
OPERATIONAL_INTERACTION_DEFAULTS: dict[str, str] = {
    "lead_engineer_loop_detected": "accept",    # one more guided attempt
    "reviewer_fix_cycle_limit": "accept",       # new engineer with full spec
    "delegation_churn_limit": "accept",         # one more bounded round
    "cost_breaker_tripped": "accept",           # reset counter, keep going
    "child_blocked_requires_action": "accept",  # lead final attempt
    "lead_wants_file_read": "accept",           # harmless context injection
    "subtree_stalled": "accept",                # wake supervisor to unblock
    "rereview_limit_reached": "accept",         # one more authorised review round
    "parent_closed_child_open": "accept",       # wake lead to close the gap
}


# Installing an MCP server runs third-party code — this reason is
# DELIBERATELY absent from OPERATIONAL_INTERACTION_DEFAULTS above: autonomy
# must never auto-accept it, regardless of project autonomy mode. See
# DESIGN_SELF_EXTENSION.md §1: "instalar herramientas = ejecutar código de
# terceros" — always a product decision for the human owner.
EXTENSION_PROPOSAL_REASON = "extension_install_requested"


def operational_interaction_default(reason: str) -> str | None:
    """Safe default action for an operational escalation, or None if the
    reason is a product decision that must always reach the user."""
    return OPERATIONAL_INTERACTION_DEFAULTS.get(str(reason or "").strip().lower())


def default_autonomy() -> str:
    """Machine-wide default autonomy (AITEAM_AUTONOMY); project config wins."""
    raw = os.environ.get("AITEAM_AUTONOMY", "").strip().lower()
    return raw if raw in AUTONOMY_MODES else AUTONOMY_SUPERVISED


def interaction_ttl_minutes() -> int:
    """In supervised mode, operational escalations older than this take their
    safe default instead of freezing the subtree. AITEAM_INTERACTION_TTL_MINUTES;
    0 (default) disables expiration."""
    return _env_int("AITEAM_INTERACTION_TTL_MINUTES", 0)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}
