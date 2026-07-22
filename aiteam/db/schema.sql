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
CREATE INDEX IF NOT EXISTS idx_quorum_sessions_issue ON quorum_sessions(issue_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quorum_contributions_session ON quorum_contributions(session_id, ordinal);
