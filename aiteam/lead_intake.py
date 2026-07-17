from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.action_routing import pick_role_for_routing, route_action
from aiteam.db.dependencies import sync_default_child_dependencies
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.hiring_economics import log_hiring_decision
from aiteam.project_adapters import apply_adapter_policy_to_member
from aiteam.user_config import ROLE_CAPABILITY_PROFILES
from aiteam.run_profiles import (
    LEAD_QUORUM,
    SOLO_LEAD,
    AgentBlueprint,
    normalize_run_profile,
    profile_config,
    build_default_team_blueprint,
)

logger = logging.getLogger(__name__)

# Roles that are NOT subject to action routing override (they run at Lead tier
# regardless of the criticality/complexity matrix).
_LEAD_TIER_ROLES = frozenset({"lead", "team_lead", "lead_executor"})


def _issue_profile(issue: dict[str, Any]) -> str:
    """Extract the run profile from issue metadata, defaulting to full_team."""
    metadata: dict[str, Any] = {}
    raw = issue.get("metadata_json") or issue.get("metadata") or {}
    if isinstance(raw, str):
        try:
            metadata = json.loads(raw)
        except Exception:
            pass
    elif isinstance(raw, dict):
        metadata = raw
    return normalize_run_profile(metadata.get("profile") or "")


def build_team_proposal(
    issue: dict[str, Any],
    *,
    profile: str | None = None,
    adapter_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the first structured Lead proposal for a fresh project.

    Profile is read from the issue metadata (key ``profile``) or the explicit
    ``profile`` argument. Defaults to ``full_team``.

    ``solo_lead``   — no team hiring; Lead works alone; returns a minimal
                      proposal flagged as direct_work so the executor skips the
                      ``suggest_tasks`` interaction.

    ``lead_quorum`` — Lead + two senior quorum auditors review the plan before
                      execution begins.

    ``full_team``   — Lead + engineer + reviewer + qa (default).
    """
    effective_profile = normalize_run_profile(profile or _issue_profile(issue))
    title = str(issue.get("title") or "Proyecto").strip()
    description = str(issue.get("description") or title).strip()
    parent_issue_id = str(issue.get("id") or "issue:intake").strip() or "issue:intake"

    blueprint = build_default_team_blueprint(
        goal_id=parent_issue_id,
        raw_profile=effective_profile,
        objective=description,
        source="lead_intake",
    )
    pconf = profile_config(effective_profile)

    proposed_team: list[dict[str, Any]] = []
    used_quorum_providers: set[str] = set()
    for agent in blueprint.agents:
        if agent.role in {"team_lead", "lead"}:
            continue
        candidates = adapter_profiles or []
        if effective_profile == LEAD_QUORUM:
            diverse = [
                item for item in candidates
                if str(item.get("provider") or item.get("id") or "") not in used_quorum_providers
            ]
            candidates = diverse or candidates
        member = _blueprint_to_member(agent, adapter_profiles=candidates)
        if effective_profile == LEAD_QUORUM:
            selected_id = str(member.get("adapter_profile_id") or "")
            selected = next((item for item in (adapter_profiles or []) if str(item.get("id") or "") == selected_id), {})
            used_quorum_providers.add(str(selected.get("provider") or selected_id))
        proposed_team.append(member)

    suggested_issues = _suggested_issues_for_profile(effective_profile, parent_issue_id, proposed_team)

    accountability = _accountability_for_profile(effective_profile)

    return {
        "version": 1,
        "profile": effective_profile,
        "direct_work": effective_profile == SOLO_LEAD,  # executor reads this to skip suggest_tasks
        "goal": {
            "issue_id": issue.get("id"),
            "title": title,
            "description": description,
        },
        "accountability": accountability,
        "proposed_team": proposed_team,
        "suggested_issues": suggested_issues,
        "cost_policy": {
            "profile": effective_profile,
            "principle": blueprint.rationale,
            "allows_hiring": pconf.allows_hiring,
            "allows_worker_delegation": pconf.allows_worker_delegation,
            "requires_review_gate": pconf.requires_review_gate,
            "requires_qa_gate": pconf.requires_qa_gate,
            "delegation_taxonomy": [
                "planning",
                "well_scoped_code_change",
                "long_read",
                "context_compression",
                "web_research",
                "mcp_simple",
                "risk_review",
                "acceptance_verification",
                "high_risk_escalation",
            ],
        },
    }


_LEAD_ID_ALIASES = {"role:team_lead", "role:lead", "team_lead", "lead"}


def _normalize_lead_id(agent_id: str | None) -> str | None:
    """Map any variant of the Lead agent ID to the canonical 'role:lead'."""
    if not agent_id:
        return None
    return "role:lead" if agent_id in _LEAD_ID_ALIASES else agent_id


def _blueprint_to_member(agent: AgentBlueprint, *, adapter_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    member = {
        "id": agent.agent_id,
        "role": agent.role,
        "name": agent.name,
        "seniority": agent.seniority,
        "adapter_type": "role_builtin",  # default; user can change in hiring panel
        "adapter_config": {},
        "budget_monthly_cents": 0,
        "capabilities": list(agent.capabilities),
        "supervisor_agent_id": _normalize_lead_id(agent.supervisor_agent_id),
        "rationale": agent.assignment_reason,
    }
    return apply_adapter_policy_to_member(member, adapter_profiles)


def _accountability_for_profile(profile: str) -> list[dict[str, Any]]:
    if profile == SOLO_LEAD:
        return []
    if profile == LEAD_QUORUM:
        return [
            {"from": "quorum_auditor_1", "to": "lead", "reason": "independent plan review"},
            {"from": "quorum_auditor_2", "to": "lead", "reason": "independent plan review"},
        ]
    return [
        {"from": "engineer", "to": "lead", "reason": "implementation progress, blockers, and risk notes"},
        {"from": "reviewer", "to": "lead", "reason": "code review + static QA before done"},
    ]


def _suggested_issues_for_profile(
    profile: str,
    parent_issue_id: str,
    proposed_team: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def child_id(suffix: str) -> str:
        return f"{parent_issue_id}:{suffix}"

    if profile == SOLO_LEAD:
        # Lead works alone — just one planning+execution issue for itself
        return [
            {
                "id": child_id("work"),
                "title": "Ejecutar tarea directamente",
                "description": "El Lead ejecuta la tarea completo sin delegar. Reportará progreso en el thread.",
                "role": "lead",
                "assignee_agent_id": "role:lead",
                "complexity": "medium",
                "priority": 100,
                "delegation_type": "direct_work",
                "cost_tier": "lead",
                "report_to": None,
                "reviewed_by": None,
                "evidence_required": ["resultado y cambios realizados", "criterio de cierre cumplido"],
                "risk_checks": ["alcance ambiguo", "supuestos no declarados"],
            }
        ]

    if profile == LEAD_QUORUM:
        # Plan A already lives on the root issue. Only independent auditor
        # children are needed; a second Lead planning child duplicates work and
        # receives the wrong sibling/parent context.
        return [
            {
                "id": child_id(f"quorum_{i+1}"),
                "title": f"Revisión de quorum {i+1}",
                "description": "Auditor senior revisa el plan del Lead de forma independiente antes de ejecutar.",
                "role": m["role"],
                "assignee_agent_id": m["id"],
                "complexity": "medium",
                "priority": 90 - i * 5,
                "delegation_type": "risk_review",
                "cost_tier": "senior",
                "report_to": "role:lead",
                "reviewed_by": "role:lead",
                "evidence_required": [
                    "veredicto: approved / changes_requested / blocked",
                    "riesgos identificados",
                    "cambios requeridos si los hay",
                ],
                "risk_checks": ["plan demasiado ambicioso", "dependencias no modeladas"],
            }
            for i, m in enumerate(proposed_team)
        ]

    # full_team — plan + engineer + reviewer.
    # QA is NOT created by default: the Reviewer absorbs static QA.
    # The LLM Lead may add a QA issue explicitly if runtime verification is needed
    # and a CLI adapter is available to execute tests.
    role_to_agent = {m["role"]: m["id"] for m in proposed_team}
    return [
        {
            "id": child_id("plan"),
            "title": "Planificar flujo, riesgos y delegaciones",
            "description": (
                "Convertir el objetivo en un plan de trabajo detallado: fases, dependencias, "
                "delegaciones, riesgos, y condiciones de revisión."
            ),
            "role": "lead",
            "assignee_agent_id": "role:lead",
            "complexity": "high",
            "criticality": "medium",
            "action_type": "synthesis",
            "priority": 100,
            "delegation_type": "planning",
            "cost_tier": "lead",
            "report_to": None,
            "reviewed_by": "user_or_light_review",
            "evidence_required": [
                "objetivo y criterio de cierre",
                "sub-issues con owner y complejidad",
                "riesgos de esta run y de la siguiente",
                "condiciones de escalado",
            ],
            "risk_checks": ["alcance ambiguo", "dependencias no modeladas", "delegaciones sin owner o sin evidencia"],
        },
        {
            "id": child_id("build"),
            "title": "Implementar primera entrega ejecutable",
            "description": (
                "Construir el primer vertical funcional siguiendo el plan del Lead. "
                "Reportar supuestos, decisiones y bloqueos."
            ),
            "role": "engineer",
            "assignee_agent_id": role_to_agent.get("engineer", "role:engineer"),
            "complexity": "medium",
            "criticality": "medium",
            "action_type": "code",
            "priority": 70,
            "delegation_type": "well_scoped_code_change",
            "cost_tier": "standard_worker",
            "report_to": "role:lead",
            "reviewed_by": role_to_agent.get("reviewer", "role:reviewer"),
            "evidence_required": [
                "resumen de cambios",
                "archivos o modulos tocados",
                "pruebas ejecutadas o razon de no ejecutarlas",
                "riesgos pendientes para review",
            ],
            "risk_checks": ["scope creep", "cambios sin prueba", "supuestos no comunicados al Lead"],
        },
        {
            "id": child_id("review"),
            "title": "Revisar riesgos, diseño, QA estático y regresiones",
            "description": (
                "Revisar el plan y la implementación. Cubrir code review y QA estático: "
                "correctness, edge cases, error handling y riesgos de la siguiente run. "
                "Listar items no verificables en runtime para que el Lead decida si se necesita QA."
            ),
            "role": "reviewer",
            "assignee_agent_id": role_to_agent.get("reviewer", "role:reviewer"),
            "complexity": "medium",
            "criticality": "medium",
            "action_type": "review",
            "priority": 50,
            "delegation_type": "risk_review",
            "cost_tier": "senior",
            "report_to": "role:lead",
            "reviewed_by": "role:lead",
            "evidence_required": [
                "veredicto approved/changes_requested/blocked",
                "riesgos concretos con referencias a código",
                "resultado QA estático (happy path, edge cases, error handling)",
                "items no verificables en runtime (listados explícitamente)",
                "cambios requeridos antes de cerrar",
            ],
            "risk_checks": ["regresiones", "diseño frágil", "falta de criterios de cierre", "QA insuficiente"],
        },
    ]


_API_ONLY_ADAPTER_TYPES = {"openai_api", "anthropic_api", "gemini_api", "anthropic_sonnet"}
_FILE_WRITING_ROLES = {"engineer", "qa", "worker"}


def format_team_proposal(proposal: dict[str, Any]) -> str:
    profile = proposal.get("profile", "full_team")
    team = proposal.get("proposed_team") or []
    issues = proposal.get("suggested_issues") or []
    direct_work = proposal.get("direct_work", False)

    lines = [
        "Propuesta inicial del Lead",
        "",
        f"Perfil: {profile}",
    ]

    if direct_work:
        lines += [
            "",
            "Modo solo_lead: el Lead ejecuta la tarea directamente sin contratar equipo.",
            "Crearé la issue de trabajo y empezaré inmediatamente.",
        ]
    else:
        if team:
            lines += ["", "Equipo propuesto:"]
            for member in team:
                role = member.get("role", "")
                adapter = member.get("adapter_type", "role_builtin")
                model = (member.get("adapter_config") or {}).get("model") or ""
                role_cap = ROLE_CAPABILITY_PROFILES.get(role.lower(), {})
                cap_note = role_cap.get("note", "")
                # Build a concise model annotation
                model_tag = f"modelo: {model}" if model else "modelo: automático (config.toml)"
                ws_tag = " · ⚠️ requiere CLI" if role_cap.get("requires_workspace") and adapter in _API_ONLY_ADAPTER_TYPES else ""
                lines.append(
                    f"- {member['name']} ({role}, {member.get('seniority','standard')})"
                    f" — {adapter} · {model_tag}{ws_tag}"
                    f"\n  {cap_note}"
                    f"\n  {member.get('rationale','')}"
                )
        lines += ["", "Issues que se crearán:"]
        for issue in issues:
            assignee = issue.get("assignee_agent_id") or issue.get("role") or "?"
            evidence = ", ".join(issue.get("evidence_required") or [])
            lines.append(
                f"- [{issue.get('delegation_type','work')}] {issue['title']}"
                f"\n  → {assignee} | evidencia: {evidence}"
            )
        lines += ["", "Acepta para crear el equipo y las issues. Rechaza para ajustar instrucciones."]

        # ── API-only adapter warning ─────────────────────────────────────────
        api_only_warnings = [
            member
            for member in team
            if member.get("role", "").lower() in _FILE_WRITING_ROLES
            and member.get("adapter_type", "").lower() in _API_ONLY_ADAPTER_TYPES
        ]
        if api_only_warnings:
            lines += [
                "",
                "⚠️  ADVERTENCIA — Adapter API-only detectado en rol de implementación:",
            ]
            for member in api_only_warnings:
                lines.append(
                    f"   · {member['name']} ({member['role']}) usa `{member.get('adapter_type')}` "
                    f"que NO puede escribir archivos en el workspace."
                )
            lines += [
                "   El engineer necesita un adapter CLI/local (subscription_cli, Codex CLI, Gemini CLI u Ollama)",
                "   para producir evidencia de implementación. Si aceptas con este adapter el engineer",
                "   quedará bloqueado inmediatamente con liveness_reason=api_only_engineer_no_workspace_changes.",
                "   Cambia el adapter antes de aceptar o ajusta el proyecto para usar subscription_cli.",
            ]

    return "\n".join(lines)


def apply_accepted_team_proposal(
    db_path: Path,
    *,
    parent_issue_id: str,
    proposal: dict[str, Any],
    source_run_id: str,
) -> dict[str, Any]:
    created_agents: list[str] = []
    created_issues: list[str] = []
    with contextlib.closing(_connect(db_path)) as conn:
        for member in proposal.get("proposed_team") or []:
            row = conn.execute(
                """
                INSERT INTO agents (
                    id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json,
                    budget_monthly_cents, supervisor_agent_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    role = excluded.role,
                    name = excluded.name,
                    seniority = excluded.seniority,
                    adapter_type = excluded.adapter_type,
                    adapter_config_json = excluded.adapter_config_json,
                    capabilities_json = excluded.capabilities_json,
                    budget_monthly_cents = excluded.budget_monthly_cents,
                    supervisor_agent_id = excluded.supervisor_agent_id,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    member["id"],
                    member["role"],
                    member["name"],
                    member.get("seniority") or "standard",
                    member.get("adapter_type") or "manual",
                    json.dumps(member.get("adapter_config") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(member.get("capabilities") or [], ensure_ascii=False),
                    int(member.get("budget_monthly_cents") or 0),
                    member.get("supervisor_agent_id"),
                    json.dumps({"source": "lead_intake", "rationale": member.get("rationale")}, ensure_ascii=False),
                ),
            ).fetchone()
            if row:
                created_agents.append(row["id"])

        parent = conn.execute("SELECT goal_id FROM issues WHERE id = ?", (parent_issue_id,)).fetchone()
        goal_id = parent["goal_id"] if parent else None
        for item in proposal.get("suggested_issues") or []:
            # ── Action routing override ───────────────────────────────────────
            # When the item carries criticality + action_type, run route_action
            # and potentially upgrade the role (e.g. engineer → lead_executor
            # when criticality=critical).  Lead-tier roles are never overridden.
            effective_role = item.get("role") or "engineer"
            effective_assignee = item.get("assignee_agent_id")
            action_type = item.get("action_type")
            criticality = item.get("criticality", "medium")
            complexity = item.get("complexity", "medium")
            if action_type and effective_role not in _LEAD_TIER_ROLES:
                try:
                    routing = route_action(
                        criticality=criticality,
                        complexity=complexity,
                        action_type=action_type,
                    )
                    routed_role = pick_role_for_routing(routing, action_type)
                    if routed_role != effective_role:
                        logger.info(
                            "lead_intake: routing override %s → %s"
                            " (criticality=%s, complexity=%s, action_type=%s, issue=%s)",
                            effective_role, routed_role,
                            criticality, complexity, action_type, item["id"],
                        )
                        effective_role = routed_role
                        effective_assignee = f"role:{routed_role}"
                        # Ensure the overridden agent row exists (FK constraint).
                        # Use INSERT OR IGNORE — if the agent already exists the
                        # row stays untouched; if it is brand-new a minimal record
                        # is created that liveness reconciliation will enrich later.
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO agents
                                (id, role, name, seniority, adapter_type)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                effective_assignee,
                                routed_role,
                                routed_role.replace("_", " ").title(),
                                "senior",
                                "lead_builtin",
                            ),
                        )
                except Exception:
                    logger.warning(
                        "lead_intake: route_action failed for item %s — keeping original role",
                        item["id"], exc_info=True,
                    )

            row = conn.execute(
                """
                INSERT OR IGNORE INTO issues (
                    id, parent_id, goal_id, title, description, status, priority,
                    role, complexity, criticality, assignee_agent_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    item["id"],
                    parent_issue_id,
                    goal_id,
                    item["title"],
                    item.get("description"),
                    int(item.get("priority") or 0),
                    effective_role,
                    complexity,
                    criticality,
                    effective_assignee,
                    json.dumps(
                        {
                            "source": "lead_intake",
                            "source_run_id": source_run_id,
                            "action_type": action_type,
                            "routing_criticality": criticality,
                            "delegation_type": item.get("delegation_type"),
                            "cost_tier": item.get("cost_tier"),
                            "report_to": item.get("report_to"),
                            "reviewed_by": item.get("reviewed_by"),
                            "evidence_required": item.get("evidence_required") or [],
                            "risk_checks": item.get("risk_checks") or [],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ).fetchone()
            if row:
                created_issues.append(row["id"])
                if effective_assignee:
                    enqueue_wakeup(
                        db_path,
                        agent_id=effective_assignee,
                        source="assignment",
                        reason="assignment",
                        trigger_detail=f"accepted_proposal:{parent_issue_id}",
                        payload={
                            "issue_id": item["id"],
                            "wake_reason": "assignment",
                            "delegation_reason": item.get("description") or item.get("title"),
                            "delegation_type": item.get("delegation_type"),
                            "action_type": action_type,
                            "complexity": complexity,
                            "criticality": criticality,
                            "cost_tier": item.get("cost_tier"),
                            "report_to": item.get("report_to"),
                            "reviewed_by": item.get("reviewed_by"),
                            "evidence_required": item.get("evidence_required") or [],
                        },
                        idempotency_key=f"assignment:{item['id']}:{effective_assignee}",
                    )

        conn.execute(
            """
            UPDATE issues
            SET status = 'in_progress',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND status IN ('todo', 'backlog')
            """,
            (parent_issue_id,),
        )

    sync_default_child_dependencies(db_path, parent_issue_id=parent_issue_id)

    # Audit each adapter assignment with its economics (cost policy, A2).
    created_agent_ids = set(created_agents)
    deviating: list[dict[str, Any]] = []
    for member in proposal.get("proposed_team") or []:
        member_id = str(member.get("id") or "")
        if member_id in created_agent_ids:
            decision = log_hiring_decision(
                db_path,
                agent_id=member_id,
                role=str(member.get("role") or ""),
                adapter_type=str(member.get("adapter_type") or "manual"),
                adapter_config=member.get("adapter_config") or {},
                adapter_profile_id=member.get("adapter_profile_id"),
                source="lead_intake",
                run_id=source_run_id or None,
            )
            if decision.get("policy_deviation"):
                deviating.append(decision)

    # One-time cost-policy warning in the intake thread (A3).
    if deviating:
        _warn_cost_policy_deviation(db_path, parent_issue_id=parent_issue_id, decisions=deviating)

    return {"created_agents": created_agents, "created_issues": created_issues}


def _warn_cost_policy_deviation(
    db_path: Path,
    *,
    parent_issue_id: str,
    decisions: list[dict[str, Any]],
) -> None:
    """Post a single system comment when workers land on per-token models."""
    from aiteam.db.comments import create_comment

    with contextlib.closing(_connect(db_path)) as conn:
        already = conn.execute(
            """
            SELECT COUNT(*) FROM activity_log
            WHERE action = 'cost_policy.warning' AND target_id = ?
            """,
            (parent_issue_id,),
        ).fetchone()
    if already and int(already[0]) > 0:
        return
    total_per_cycle = sum(int(d.get("estimated_cost_cents") or 0) for d in decisions)
    roles = ", ".join(
        f"{d.get('role')} → {d.get('model')} (~{d.get('estimated_cost_cents')}¢/run)" for d in decisions
    )
    no_channel = any(d.get("policy_deviation") == "no_zero_cost_channel_connected" for d in decisions)
    hint = (
        "Conecta un canal local (Ollama/LM Studio) o una suscripción CLI y los workers pasarán a coste 0."
        if no_channel
        else "Hay un canal de coste 0 conectado pero el scoring eligió premium — revisa la selección de adapters."
    )
    try:
        create_comment(
            db_path,
            issue_id=parent_issue_id,
            author_agent_id=None,
            body=(
                f"⚠ Política de costes: hay roles worker en modelos de pago por token ({roles}). "
                f"Sobrecoste estimado ~{total_per_cycle}¢ por ronda de runs. {hint}"
            ),
            metadata={"source": "cost_policy_warning"},
        )
    except Exception:
        logger.warning("cost_policy warning comment failed for %s", parent_issue_id, exc_info=True)
    try:
        from aiteam.db.activity_log import log_activity

        log_activity(
            db_path,
            action="cost_policy.warning",
            target_type="issue",
            target_id=parent_issue_id,
            actor_agent_id=None,
            payload={"decisions": decisions},
        )
    except Exception:
        logger.warning("cost_policy warning activity failed for %s", parent_issue_id, exc_info=True)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
