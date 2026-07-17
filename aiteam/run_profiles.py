from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SOLO_LEAD = "solo_lead"
LEAD_QUORUM = "lead_quorum"
FULL_TEAM = "full_team"

CANONICAL_RUN_PROFILES = {SOLO_LEAD, LEAD_QUORUM, FULL_TEAM}


@dataclass(frozen=True)
class RunProfileConfig:
    name: str
    lead_first: bool
    uses_quorum: bool
    allows_hiring: bool
    allows_worker_delegation: bool
    requires_review_gate: bool
    requires_qa_gate: bool  # DEPRECATED — always False; Reviewer absorbs static QA; kept for API compat
    default_agent_ids: tuple[str, ...]
    cheap_delegate_roles: tuple[str, ...]
    senior_control_roles: tuple[str, ...]
    phase: str
    completion_artifact: str
    next_profile: str | None


@dataclass(frozen=True)
class AgentBlueprint:
    agent_id: str
    role: str
    name: str
    seniority: str
    capabilities: tuple[str, ...]
    supervisor_agent_id: str | None
    preferred_tier: str
    preferred_channel: str
    assignment_reason: str


@dataclass(frozen=True)
class TeamBlueprintSpec:
    goal_id: str
    profile: str
    rationale: str
    agents: tuple[AgentBlueprint, ...]
    cost_policy: dict[str, Any]
    metadata: dict[str, Any]

    def to_json_payload(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "profile": self.profile,
            "rationale": self.rationale,
            "agents": [
                {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "name": agent.name,
                    "seniority": agent.seniority,
                    "capabilities": list(agent.capabilities),
                    "supervisor_agent_id": agent.supervisor_agent_id,
                    "preferred_tier": agent.preferred_tier,
                    "preferred_channel": agent.preferred_channel,
                    "assignment_reason": agent.assignment_reason,
                }
                for agent in self.agents
            ],
            "cost_policy": self.cost_policy,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class ExecutionProfileSelection:
    profile: str
    source: str
    reason: str
    signals: dict[str, Any]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "source": self.source,
            "reason": self.reason,
            "signals": dict(self.signals),
        }


PROFILE_CONFIGS: dict[str, RunProfileConfig] = {
    SOLO_LEAD: RunProfileConfig(
        name=SOLO_LEAD,
        lead_first=True,
        uses_quorum=False,
        allows_hiring=False,
        allows_worker_delegation=False,
        requires_review_gate=False,
        requires_qa_gate=False,
        default_agent_ids=("role:team_lead",),
        cheap_delegate_roles=(),
        senior_control_roles=("role:team_lead",),
        phase="execution",
        completion_artifact="accepted_delivery",
        next_profile=None,
    ),
    LEAD_QUORUM: RunProfileConfig(
        name=LEAD_QUORUM,
        lead_first=True,
        uses_quorum=True,
        allows_hiring=False,
        allows_worker_delegation=False,
        requires_review_gate=False,
        requires_qa_gate=False,
        default_agent_ids=(
            "role:team_lead",
            "role:quorum_auditor_1",
            "role:quorum_auditor_2",
        ),
        cheap_delegate_roles=(),
        senior_control_roles=(
            "role:team_lead",
            "role:quorum_auditor_1",
            "role:quorum_auditor_2",
        ),
        phase="planning",
        completion_artifact="accepted_plan",
        next_profile=None,
    ),
    FULL_TEAM: RunProfileConfig(
        name=FULL_TEAM,
        lead_first=True,
        uses_quorum=False,
        allows_hiring=True,
        allows_worker_delegation=True,
        requires_review_gate=True,
        requires_qa_gate=False,  # Reviewer absorbs static QA; QA is optional
        default_agent_ids=(
            "role:team_lead",
            "role:engineer",
            "role:reviewer",
            # Tier 3 specialists (file_scout, web_scout, context_curator) are
            # auto-created by ensure_tier3_agents() on every project reconcile —
            # they do not need to appear here to avoid polluting the hiring panel.
        ),
        cheap_delegate_roles=("engineer",),
        senior_control_roles=("team_lead", "reviewer"),
        phase="execution",
        completion_artifact="accepted_delivery",
        next_profile=None,
    ),
}


def normalize_run_profile(raw_profile: Any, *, chat_mode: str = "") -> str:
    value = str(raw_profile or "").strip().lower()
    mode = str(chat_mode or "").strip().lower()
    if value in CANONICAL_RUN_PROFILES:
        return value
    if value in {"direct", "lead_only"} or (not value and mode == "direct"):
        return SOLO_LEAD
    if value in {"planning_quorum", "plan_quorum", "quorum", "architecture_review", "roadmap"}:
        return LEAD_QUORUM
    if value in {"team", "team_advanced", "ai_team_basic", "ai_teams_full", "advanced"}:
        return FULL_TEAM
    return FULL_TEAM


def select_execution_profile(
    *,
    explicit_profile: str | None = None,
    criticality: str | None = None,
    ambiguity: str | None = None,
    independent_verification: bool | None = None,
    parallel_workstreams: int | None = None,
    reversible: bool | None = None,
) -> ExecutionProfileSelection:
    """Choose an execution profile from explicit, auditable signals.

    This deliberately is not a weighted router. Missing signals choose the
    safer team baseline. `lead_quorum` can only arrive as an explicit planning
    override and is never selected for execution.
    """
    raw_explicit = str(explicit_profile or "").strip().lower()
    if raw_explicit and raw_explicit != "auto":
        if raw_explicit not in CANONICAL_RUN_PROFILES:
            raise ValueError(f"unknown run profile: {explicit_profile}")
        return ExecutionProfileSelection(
            profile=raw_explicit,
            source="explicit_override",
            reason="user_selected_profile",
            signals={},
        )

    crit = str(criticality or "").strip().lower()
    amb = str(ambiguity or "").strip().lower()
    streams = int(parallel_workstreams) if parallel_workstreams is not None else None
    signals = {
        "criticality": crit or None,
        "ambiguity": amb or None,
        "independent_verification": independent_verification,
        "parallel_workstreams": streams,
        "reversible": reversible,
    }
    if (
        crit not in {"low", "medium", "high", "critical"}
        or amb not in {"low", "medium", "high"}
        or independent_verification is None
        or streams is None
        or reversible is None
    ):
        return ExecutionProfileSelection(
            profile=FULL_TEAM,
            source="automatic_policy",
            reason="incomplete_signals_use_safe_team_default",
            signals=signals,
        )
    if streams < 1:
        raise ValueError("parallel_workstreams must be >= 1")
    if crit in {"high", "critical"}:
        reason = "high_or_critical_risk_requires_team_controls"
    elif amb == "high":
        reason = "high_ambiguity_requires_separate_planning_and_execution"
    elif independent_verification:
        reason = "independent_verification_requested"
    elif streams >= 2:
        reason = "multiple_parallel_workstreams"
    elif not reversible:
        reason = "irreversible_change_uses_team_default"
    else:
        return ExecutionProfileSelection(
            profile=SOLO_LEAD,
            source="automatic_policy",
            reason="bounded_reversible_single_agent_work",
            signals=signals,
        )
    return ExecutionProfileSelection(
        profile=FULL_TEAM,
        source="automatic_policy",
        reason=reason,
        signals=signals,
    )


def profile_config(raw_profile: Any, *, chat_mode: str = "") -> RunProfileConfig:
    return PROFILE_CONFIGS[normalize_run_profile(raw_profile, chat_mode=chat_mode)]


def build_default_team_blueprint(
    goal_id: str,
    raw_profile: Any,
    *,
    objective: str = "",
    source: str = "default",
) -> TeamBlueprintSpec:
    profile = normalize_run_profile(raw_profile)
    config = PROFILE_CONFIGS[profile]
    agents = tuple(_agent_blueprint(agent_id) for agent_id in config.default_agent_ids)
    return TeamBlueprintSpec(
        goal_id=goal_id,
        profile=profile,
        rationale=_rationale_for(profile, objective),
        agents=agents,
        cost_policy=_cost_policy_for(config),
        metadata={
            "source": source,
            "lead_first": config.lead_first,
            "uses_quorum": config.uses_quorum,
            "allows_hiring": config.allows_hiring,
            "allows_worker_delegation": config.allows_worker_delegation,
            "requires_review_gate": config.requires_review_gate,
            "requires_qa_gate": config.requires_qa_gate,
            "phase": config.phase,
            "completion_artifact": config.completion_artifact,
            "next_profile": config.next_profile,
        },
    )


def _agent_blueprint(agent_id: str) -> AgentBlueprint:
    role_key = agent_id.removeprefix("role:")
    if role_key == "team_lead":
        return AgentBlueprint(
            agent_id=agent_id,
            role="team_lead",
            name="Team Lead",
            seniority="lead",
            capabilities=("planning", "supervision", "hiring", "cost_policy", "architecture"),
            supervisor_agent_id=None,
            preferred_tier="senior_cloud",
            preferred_channel="subscription_or_api",
            assignment_reason="Owns project understanding, decomposition, supervision and final calls.",
        )
    if role_key.startswith("quorum_auditor"):
        return AgentBlueprint(
            agent_id=agent_id,
            role="reviewer",
            name=role_key.replace("_", " ").title(),
            seniority="senior",
            capabilities=("planning_review", "architecture_review", "risk_assessment"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="senior_cloud",
            preferred_channel="subscription_or_api",
            assignment_reason="Senior independent review of the Lead plan before execution.",
        )
    if role_key == "scout":
        return AgentBlueprint(
            agent_id=agent_id,
            role="scout",
            name="Scout",
            seniority="cheap",
            capabilities=("long_read", "context_compression", "simple_research", "mcp_probe"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="budget_api",
            preferred_channel="api_or_local",
            assignment_reason="Cheap context gathering and compression for bounded work.",
        )
    if role_key == "researcher":
        return AgentBlueprint(
            agent_id=agent_id,
            role="researcher",
            name="Researcher",
            seniority="standard",
            capabilities=("research", "analysis", "documentation_read"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="budget_api",
            preferred_channel="api_or_local",
            assignment_reason="Delegated research when the Lead does not need to spend senior context.",
        )
    if role_key == "engineer":
        return AgentBlueprint(
            agent_id=agent_id,
            role="engineer",
            name="Engineer",
            seniority="standard",
            capabilities=("code_change", "implementation", "unit_tests"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="advanced_api",
            preferred_channel="subscription_or_api",
            assignment_reason="Implementation of well-scoped programming tasks under Lead supervision.",
        )
    if role_key == "reviewer":
        return AgentBlueprint(
            agent_id=agent_id,
            role="reviewer",
            name="Reviewer",
            seniority="senior",
            capabilities=("code_review", "risk_assessment", "diff_review"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="advanced_api",
            preferred_channel="subscription_or_api",
            assignment_reason="Quality supervision before work is considered complete.",
        )
    if role_key == "lead_executor":
        return AgentBlueprint(
            agent_id=agent_id,
            role="lead_executor",
            name="Lead Executor",
            seniority="senior",
            capabilities=("code_change", "implementation", "code_review", "research", "synthesis"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="senior_cloud",
            preferred_channel="subscription_or_api",
            assignment_reason="Senior execution arm of the Lead for critical/complex actions.",
        )
    if role_key == "qa":
        # DEPRECATED — QA Tier 2 role has been absorbed by the Reviewer.
        # For runtime test execution, use role='test_runner' (Tier 3) instead.
        # This branch is kept for backward compatibility with existing DB records only.
        import logging as _logging  # noqa: PLC0415
        _logging.getLogger(__name__).warning(
            "_agent_blueprint: role='qa' is deprecated; map to test_runner (Tier 3) for runtime tests."
        )
        return AgentBlueprint(
            agent_id=agent_id,
            role="qa",
            name="QA (deprecated)",
            seniority="standard",
            capabilities=("test", "validation", "repro", "artifact_check"),
            supervisor_agent_id="role:team_lead",
            preferred_tier="budget_api",
            preferred_channel="api_or_local",
            assignment_reason="DEPRECATED — use test_runner for runtime test execution.",
        )
    return AgentBlueprint(
        agent_id=agent_id,
        role=role_key,
        name=role_key.replace("_", " ").title(),
        seniority="standard",
        capabilities=(),
        supervisor_agent_id="role:team_lead",
        preferred_tier="advanced_api",
        preferred_channel="subscription_or_api",
        assignment_reason="Project-specific role proposed by the Lead.",
    )


def _cost_policy_for(config: RunProfileConfig) -> dict[str, Any]:
    return {
        "goal": "minimize_cost_without_quality_loss",
        "lead_policy": "senior_context_for_planning_supervision_and_high_risk_work",
        "delegation_allowed": config.allows_worker_delegation,
        "cheap_delegate_roles": list(config.cheap_delegate_roles),
        "senior_control_roles": list(config.senior_control_roles),
        "record_per_run": [
            "estimated_cost_cents",
            "actual_cost_cents",
            "estimated_savings_cents",
            "delegation_reason",
            "supervisor_run_id",
        ],
    }


def _rationale_for(profile: str, objective: str) -> str:
    suffix = f" Objective: {objective.strip()}" if objective.strip() else ""
    if profile == SOLO_LEAD:
        return "Use one senior Lead for direct, bounded programming work." + suffix
    if profile == LEAD_QUORUM:
        return "Use a senior Lead plus independent senior auditors for planning decisions." + suffix
    return "Use a programming team: Lead plans and supervises, Engineer executes, Reviewer closes with code review + static QA. QA agent is optional for runtime verification." + suffix
