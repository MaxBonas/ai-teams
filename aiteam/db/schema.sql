PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'done', 'cancelled')),
    source TEXT NOT NULL DEFAULT 'migration',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    seniority TEXT NOT NULL DEFAULT 'standard'
        CHECK (seniority IN ('lead', 'senior', 'standard', 'cheap', 'local')),
    adapter_type TEXT,
    adapter_config_json TEXT NOT NULL DEFAULT '{}',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    budget_monthly_cents INTEGER NOT NULL DEFAULT 0,
    spent_monthly_cents INTEGER NOT NULL DEFAULT 0,
    heartbeat_interval_sec INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'idle', 'running', 'error', 'paused', 'terminated')),
    supervisor_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_blueprints (
    id TEXT PRIMARY KEY,
    goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
    profile TEXT NOT NULL
        CHECK (profile IN ('solo_lead', 'lead_quorum', 'full_team')),
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'approved', 'active', 'superseded', 'cancelled')),
    proposed_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    rationale TEXT,
    cost_policy_json TEXT NOT NULL DEFAULT '{}',
    blueprint_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 0,
    role TEXT,
    complexity TEXT,
    criticality TEXT,
    assignee_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    checkout_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    execution_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    execution_locked_at TEXT,
    identifier TEXT UNIQUE,
    source_task_id TEXT UNIQUE,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issue_dependencies (
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    depends_on_issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'blocks',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (issue_id, depends_on_issue_id)
);

CREATE TABLE IF NOT EXISTS agent_assignments (
    id TEXT PRIMARY KEY,
    blueprint_id TEXT REFERENCES team_blueprints(id) ON DELETE SET NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    assigned_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    assignment_reason TEXT,
    cost_policy_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('proposed', 'active', 'completed', 'cancelled', 'superseded')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wakeup_requests (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'claimed', 'running', 'finished', 'skipped', 'failed', 'cancelled')),
    trigger_detail TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    coalesced_count INTEGER NOT NULL DEFAULT 0,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TEXT,
    finished_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Snapshot durable de cada candidato realmente considerado por el scheduler.
-- Vive antes de runs porque los rechazos no llegan a crear una ejecución.
CREATE TABLE IF NOT EXISTS dispatch_candidate_decisions (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    dispatch_mode TEXT NOT NULL
        CHECK (dispatch_mode IN ('sequential', 'parallel')),
    wakeup_request_id TEXT REFERENCES wakeup_requests(id) ON DELETE SET NULL,
    agent_id TEXT,
    issue_id TEXT,
    root_issue_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    capacity_pool TEXT NOT NULL,
    is_work_slot INTEGER NOT NULL DEFAULT 0 CHECK (is_work_slot IN (0, 1)),
    requested_at TEXT,
    ready_at TEXT,
    considered_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('selected', 'rejected')),
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, wakeup_request_id)
);

CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_wakeup
    ON dispatch_candidate_decisions(wakeup_request_id, considered_at);
CREATE INDEX IF NOT EXISTS idx_dispatch_decisions_batch
    ON dispatch_candidate_decisions(batch_id, considered_at);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    wakeup_request_id TEXT REFERENCES wakeup_requests(id) ON DELETE SET NULL,
    profile TEXT,
    invocation_source TEXT NOT NULL DEFAULT 'manual',
    trigger_detail TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'lost', 'skipped')),
    adapter_type TEXT,
    provider TEXT,
    model TEXT,
    channel TEXT CHECK (channel IS NULL OR channel IN ('subscription', 'api', 'local')),
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    error TEXT,
    error_code TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    context_snapshot_json TEXT NOT NULL DEFAULT '{}',
    session_id_before TEXT,
    session_id_after TEXT,
    liveness_state TEXT,
    liveness_reason TEXT,
    process_pid INTEGER,
    last_output_at TEXT,
    log_ref TEXT,
    log_sha256 TEXT,
    stdout_excerpt TEXT,
    stderr_excerpt TEXT,
    retry_of_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    scheduled_retry_at TEXT,
    process_loss_retry_count INTEGER NOT NULL DEFAULT 0,
    supervisor_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    delegation_reason TEXT,
    complexity TEXT,
    cost_policy_json TEXT NOT NULL DEFAULT '{}',
    estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
    actual_cost_cents INTEGER NOT NULL DEFAULT 0,
    estimated_savings_cents INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The selected adapter profile is historical provenance, not mutable agent
-- configuration.  Keeping it in an additive table lets existing SQLite
-- projects acquire the contract through CREATE TABLE IF NOT EXISTS without an
-- unsafe ALTER of the central runs table.
CREATE TABLE IF NOT EXISTS run_adapter_profiles (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    channel TEXT CHECK (channel IS NULL OR channel IN ('subscription', 'api', 'local')),
    quota_policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issue_comments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    author_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    author_user_id TEXT,
    source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issue_documents (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'markdown',
    body TEXT NOT NULL,
    current_revision_id TEXT,
    revision_number INTEGER NOT NULL DEFAULT 1,
    created_by_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    updated_by_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, key)
);

CREATE TABLE IF NOT EXISTS issue_document_revisions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES issue_documents(id) ON DELETE CASCADE,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'markdown',
    body TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    created_by_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS issue_thread_interactions (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    kind TEXT NOT NULL
        CHECK (kind IN ('suggest_tasks', 'ask_user_questions', 'request_confirmation')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected', 'answered', 'cancelled', 'expired')),
    continuation_policy TEXT NOT NULL DEFAULT 'wake_assignee',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    source_comment_id TEXT REFERENCES issue_comments(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    title TEXT,
    summary TEXT,
    created_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    resolved_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    resolved_by_user_id TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    stream TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cost_events (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    provider TEXT,
    model TEXT,
    channel TEXT CHECK (channel IS NULL OR channel IN ('subscription', 'api', 'local')),
    cost_cents INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_savings_cents INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    actor_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    actor_user_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tool_access (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_facts (
    id TEXT PRIMARY KEY,
    goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    fact TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Structured, provenance-carrying agent reports (validated AGENT-REPORT).
-- One row per report emitted by a run; consumers must only trust rows with
-- valid=1 AND is_assignee=1 (written by the issue's own assignee).
CREATE TABLE IF NOT EXISTS agent_reports (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    agent_role TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    issue_status TEXT,
    next_owner TEXT,
    tech_match TEXT,
    blocker TEXT,
    evidence TEXT,
    valid INTEGER NOT NULL DEFAULT 0,
    is_assignee INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_reports_issue ON agent_reports(issue_id, created_at);

CREATE TABLE IF NOT EXISTS quorum_sessions (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    base_plan_revision_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reviewing'
        CHECK (status IN ('reviewing', 'ready', 'synthesizing', 'accepted', 'degraded', 'failed')),
    requested_contributions INTEGER NOT NULL DEFAULT 2,
    min_valid_contributions INTEGER NOT NULL DEFAULT 2,
    next_profile TEXT NOT NULL DEFAULT 'planning_complete',
    skipped_reason TEXT,
    synthesis_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    final_plan_revision_id TEXT,
    dispositions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, base_plan_revision_id)
);

CREATE TABLE IF NOT EXISTS quorum_contributions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES quorum_sessions(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    ordinal INTEGER NOT NULL,
    provider TEXT,
    model TEXT,
    channel TEXT CHECK (channel IS NULL OR channel IN ('subscription', 'api', 'local')),
    result TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    findings_json TEXT NOT NULL DEFAULT '[]',
    valid INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, agent_id),
    UNIQUE(session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS orientation_sessions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'abandoned', 'revoked')),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT
);

-- Medición local y consentida de los flujos de orientación del cockpit.
-- No admite texto libre, rutas, títulos, IDs de issue ni payload JSON.
CREATE TABLE IF NOT EXISTS orientation_measurement (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    current_session_id TEXT REFERENCES orientation_sessions(id) ON DELETE SET NULL,
    consented_at TEXT,
    revoked_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orientation_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES orientation_sessions(id) ON DELETE CASCADE,
    flow TEXT NOT NULL
        CHECK (flow IN ('inbox', 'profile_selection', 'accepted_plan_to_task')),
    event TEXT NOT NULL
        CHECK (event IN ('flow_started', 'flow_completed', 'flow_abandoned',
                         'profile_selected', 'ui_error')),
    profile TEXT
        CHECK (profile IS NULL OR profile IN ('solo_lead', 'lead_quorum', 'full_team')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Estado durable del asistente de primer uso, proyecto y reparación.
CREATE TABLE IF NOT EXISTS guided_setup_sessions (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    scope TEXT NOT NULL
        CHECK (scope IN ('machine_onboarding', 'project_setup', 'installation_repair')),
    subject_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'blocked', 'passed')),
    current_step TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(schema_version, scope, subject_key)
);

CREATE TABLE IF NOT EXISTS guided_setup_steps (
    session_id TEXT NOT NULL REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN ('not_started', 'in_progress', 'blocked', 'skipped', 'passed')),
    response_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    blocker_code TEXT,
    skip_reason TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, step_key),
    UNIQUE(session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS guided_setup_preparation_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    needs_hash TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    doctor_hash TEXT NOT NULL,
    ready INTEGER NOT NULL CHECK (ready IN (0, 1)),
    blockers_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id, step_key)
        REFERENCES guided_setup_steps(session_id, step_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS guided_setup_project_commit_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    project_target TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Evidencia content-addressed y autorización durable del preflight de proyecto.
-- Las referencias se confinan a la sesión: el navegador nunca aporta contenido
-- ni puede reutilizar evidencia obtenida por otra sesión.
CREATE TABLE IF NOT EXISTS guided_setup_project_preflight_artifacts (
    session_id TEXT NOT NULL
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    reference TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_json TEXT NOT NULL,
    fixture_evidence_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, reference)
);

CREATE TABLE IF NOT EXISTS guided_setup_project_preflight_receipts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES guided_setup_sessions(id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    preflight_hash TEXT NOT NULL,
    execution_plan_hash TEXT NOT NULL,
    execution_receipt_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('go', 'no_go')),
    fixture_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    preflight_json TEXT NOT NULL,
    execution_receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, receipt_hash)
);

-- Snapshot inmutable de una decisión shadow/automática de modelo por rol.
-- Conserva el conjunto completo; no hace de la tabla una segunda fuente de
-- métricas. El hash permite reproducir exactamente la entrada al selector.
CREATE TABLE IF NOT EXISTS model_role_score_snapshots (
    id TEXT PRIMARY KEY,
    selection_scope TEXT NOT NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    canonical_role TEXT NOT NULL,
    score_version TEXT NOT NULL,
    read_model_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    winner_candidate_id TEXT,
    winner_reason TEXT NOT NULL DEFAULT '',
    auto_applied INTEGER NOT NULL DEFAULT 0 CHECK (auto_applied IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(selection_scope, input_hash)
);

-- Histórico de recálculo del catálogo. Solo añade una fila ante cambios
-- materiales o al comenzar un mes; nunca elimina evidencia por antigüedad.
CREATE TABLE IF NOT EXISTS model_catalog_maintenance_snapshots (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    period TEXT NOT NULL,
    source_observed_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    dimension_hashes_json TEXT NOT NULL,
    trigger_reasons_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    trend_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Inteligencia durable de cambios de proveedor. La instancia de máquina usa
-- guided_setup.db; este schema conserva el mismo contrato para portabilidad.
CREATE TABLE IF NOT EXISTS provider_change_snapshots (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    probe_status TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_diffs (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    previous_snapshot_sha256 TEXT NOT NULL,
    current_snapshot_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_events (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    identity_key TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    kind TEXT NOT NULL,
    dimension TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('open', 'acknowledged', 'snoozed', 'resolved')),
    owner TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1
        CHECK (occurrence_count >= 1),
    snoozed_until TEXT,
    acknowledged_at TEXT,
    resolved_at TEXT,
    source_diff_sha256 TEXT NOT NULL,
    change_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_triggers (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'consumed', 'dismissed')),
    affected_scope_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE(event_fingerprint, trigger_type)
);

CREATE TABLE IF NOT EXISTS provider_change_schedules (
    identity_key TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    profile_id TEXT,
    channel_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    component_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    cadence_sec INTEGER NOT NULL CHECK (cadence_sec >= 60),
    base_backoff_sec INTEGER NOT NULL CHECK (base_backoff_sec >= 1),
    max_backoff_sec INTEGER NOT NULL CHECK (max_backoff_sec >= base_backoff_sec),
    jitter_sec INTEGER NOT NULL CHECK (jitter_sec >= 0),
    next_check_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_probe_status TEXT,
    last_snapshot_sha256 TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_failures >= 0),
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_cases (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    trigger_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN (
            'awaiting_confirmation', 'awaiting_classification',
            'awaiting_approval', 'approved', 'awaiting_validation',
            'validation_failed', 'awaiting_recalibration',
            'ready_to_accept', 'accepted', 'rejected', 'reverted'
        )
    ),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    owner TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    impact_json TEXT NOT NULL,
    recommendation_json TEXT NOT NULL,
    guided_commands_json TEXT NOT NULL,
    risk_json TEXT NOT NULL,
    rollback_json TEXT NOT NULL,
    classification_json TEXT,
    approval_json TEXT,
    application_json TEXT,
    validation_json TEXT,
    outcome_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_change_case_history (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES provider_change_cases(id),
    UNIQUE(case_id, sequence)
);

CREATE TABLE IF NOT EXISTS provider_change_evidence_invalidations (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    canonical_role TEXT NOT NULL,
    reason TEXT NOT NULL,
    new_selection_policy TEXT NOT NULL CHECK (
        new_selection_policy IN ('preserve', 'block_affected')
    ),
    status TEXT NOT NULL CHECK (status IN ('active', 'restored')),
    created_at TEXT NOT NULL,
    restored_at TEXT,
    FOREIGN KEY(case_id) REFERENCES provider_change_cases(id),
    UNIQUE(case_id, profile_id, model_id, canonical_role)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wakeup_idempotency
    ON wakeup_requests(agent_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_interaction_idempotency
    ON issue_thread_interactions(issue_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_issues_goal_status ON issues(goal_id, status);
CREATE INDEX IF NOT EXISTS idx_issues_assignee_status ON issues(assignee_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_agent_started ON runs(agent_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_issue_status ON runs(issue_id, status);
CREATE INDEX IF NOT EXISTS idx_run_adapter_profiles_profile
    ON run_adapter_profiles(profile_id, channel, created_at);
CREATE INDEX IF NOT EXISTS idx_wakeup_agent_status ON wakeup_requests(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_run_events_run_seq ON run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_issue_documents_issue_key ON issue_documents(issue_id, key);
CREATE INDEX IF NOT EXISTS idx_issue_document_revisions_doc ON issue_document_revisions(document_id, revision_number);
CREATE INDEX IF NOT EXISTS idx_cost_events_run ON cost_events(run_id);
CREATE INDEX IF NOT EXISTS idx_cost_events_agent_period ON cost_events(agent_id, period);
CREATE INDEX IF NOT EXISTS idx_model_score_snapshots_role
    ON model_role_score_snapshots(canonical_role, created_at);
CREATE INDEX IF NOT EXISTS idx_model_catalog_maintenance_created
    ON model_catalog_maintenance_snapshots(created_at, id);
CREATE INDEX IF NOT EXISTS idx_model_catalog_maintenance_period
    ON model_catalog_maintenance_snapshots(period, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_change_snapshots_identity
    ON provider_change_snapshots(identity_key, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_provider_change_diffs_identity
    ON provider_change_diffs(identity_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_provider_change_events_attention
    ON provider_change_events(status, severity, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_change_events_identity
    ON provider_change_events(identity_key, status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_change_triggers_status
    ON provider_change_triggers(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_provider_change_schedules_due
    ON provider_change_schedules(next_check_at, lease_until, identity_key);
CREATE INDEX IF NOT EXISTS idx_provider_change_cases_status
    ON provider_change_cases(status, severity, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_provider_change_case_history_case
    ON provider_change_case_history(case_id, sequence);
CREATE INDEX IF NOT EXISTS idx_provider_change_invalidations_active
    ON provider_change_evidence_invalidations(
        status, profile_id, model_id, canonical_role
    );
CREATE INDEX IF NOT EXISTS idx_quorum_sessions_issue ON quorum_sessions(issue_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quorum_contributions_session ON quorum_contributions(session_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_orientation_events_session ON orientation_events(session_id, created_at);
