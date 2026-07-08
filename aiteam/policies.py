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

TIER2_ROLES = frozenset({"engineer", "software_engineer", "reviewer", "code_reviewer", "qa", "worker"})

TIER3_ROLES = frozenset({"file_scout", "web_scout", "context_curator", "test_runner"})

# Hiring policy tiers (model selection): strong models for seniors,
# cheap/local for juniors.
SENIOR_ROLES = frozenset({"lead", "team_lead", "reviewer", "quorum_senior", "quorum_auditor", "architect"})
JUNIOR_ROLES = frozenset({"engineer", "test_runner", "worker", "file_scout", "web_scout", "context_curator"})

# Roles that must never edit workspace files: they delegate (Lead) or report
# (scouts/curator). Enforced via CLI read-only sandbox, the preventive
# file_ops gate, and the role.violation audit.
NON_EDITING_ROLES = frozenset({"lead", "team_lead", "file_scout", "web_scout", "context_curator"})

# Adapter types that call a remote LLM in-process (as opposed to CLI/builtin).
LLM_ADAPTER_TYPES = frozenset({"anthropic_api", "anthropic_sonnet", "openai_api", "gemini_api"})

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
})

OPS_FORBIDDEN_FOR_TIER2 = frozenset({
    "create_issue",
    "update_plan",
    "update_child_issue",
})


def forbidden_ops_for_role(role: str) -> frozenset[str]:
    role_key = str(role or "").strip().lower()
    if role_key in TIER3_ROLES:
        return OPS_FORBIDDEN_FOR_TIER3
    if role_key in TIER2_ROLES:
        return OPS_FORBIDDEN_FOR_TIER2
    return frozenset()


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
}


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
