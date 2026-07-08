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
