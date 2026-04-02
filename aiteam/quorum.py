from __future__ import annotations

from dataclasses import asdict, dataclass, field

from aiteam.types import Complexity, Criticality, Role, RoutingRequest


PLANNING_QUORUM_RUN_MODES: frozenset[str] = frozenset(
    {"planning_only", "architecture_review", "roadmap"}
)


@dataclass(frozen=True)
class QuorumConsultantPlan:
    adapter: str = ""
    provider: str = ""
    model: str = ""
    output: str = ""
    status: str = "consulted"
    reason: str = ""


@dataclass(frozen=True)
class QuorumResult:
    requested: bool = False
    applied: bool = False
    skipped_reason: str = ""
    lead_adapter: str = ""
    lead_provider: str = ""
    lead_model: str = ""
    final_adapter: str = ""
    final_provider: str = ""
    final_model: str = ""
    consultant_plans: list[QuorumConsultantPlan] = field(default_factory=list)
    final_plan: str = ""

    def to_metadata(self) -> dict[str, object]:
        payload = asdict(self)
        payload["consultant_count"] = len(self.consultant_plans)
        return payload


def should_apply_planning_quorum(*, requested: bool, run_mode: str) -> bool:
    normalized = str(run_mode or "").strip().lower()
    return bool(requested) and normalized in PLANNING_QUORUM_RUN_MODES


def selected_adapter_name(decision) -> str:
    for attempt in reversed(list(getattr(decision, "attempts", []) or [])):
        raw = str(attempt or "").strip()
        if ":ok" not in raw:
            continue
        parts = raw.split(":")
        if parts:
            return parts[0].strip()
    return ""


def _consultant_prompt(*, base_prompt: str, lead_output: str) -> str:
    return (
        f"{base_prompt}\n\n"
        "Modo quorum: consultor independiente.\n"
        "Ya existe una propuesta inicial del Lead. No la copies sin pensar.\n"
        "Analiza la misma solicitud de forma independiente y devuelve tu mejor plan.\n\n"
        "## Propuesta inicial del Lead\n"
        f"{lead_output.strip() or '_sin propuesta_'}"
    )


def _final_consolidation_prompt(
    *,
    message: str,
    lead_output: str,
    consultant_plans: list[QuorumConsultantPlan],
) -> str:
    consultant_sections: list[str] = []
    for index, consultant in enumerate(consultant_plans, start=1):
        consultant_sections.append(
            "\n".join(
                [
                    f"### Consultor {index}",
                    f"- adapter: {consultant.adapter or 'unknown'}",
                    f"- provider: {consultant.provider or 'unknown'}",
                    f"- model: {consultant.model or 'unknown'}",
                    f"- status: {consultant.status or 'consulted'}",
                    "",
                    consultant.output.strip() or "_sin salida_",
                ]
            )
        )

    return (
        "Eres Team Lead senior. Consolida el plan final tras una consulta de quorum.\n"
        "Modo quorum: consolidacion final del Lead.\n"
        "Tienes la ultima palabra. Puedes aceptar, matizar o descartar aportes.\n"
        "Mantén el formato normal del Lead, incluyendo [RUN_MODE] y [WORKFLOW_PLAN] si aplica.\n\n"
        "## Solicitud original\n"
        f"{str(message or '').strip() or '_sin solicitud_'}\n\n"
        "## Plan inicial del Lead\n"
        f"{lead_output.strip() or '_sin plan inicial_'}\n\n"
        "## Aportes de consultores\n"
        f"{chr(10).join(consultant_sections) if consultant_sections else '_sin consultores_'}"
    )


def run_planning_quorum(
    *,
    router,
    task_root: str,
    message: str,
    base_prompt: str,
    lead_output: str,
    lead_adapter: str = "",
    lead_provider: str = "",
    lead_model: str = "",
    complexity: Complexity,
    criticality: Criticality,
    environment: str = "dev",
) -> QuorumResult:
    consultant_request = RoutingRequest(
        role=Role.TEAM_LEAD,
        complexity=complexity,
        criticality=criticality,
        required_capabilities={"reasoning"},
        excluded_adapters={lead_adapter} if lead_adapter else set(),
        environment=environment,
    )
    consultant_decision = router.route_and_invoke(
        consultant_request,
        _consultant_prompt(base_prompt=base_prompt, lead_output=lead_output),
        task_id=f"{task_root}::lead_quorum_consultant",
    )
    if not consultant_decision.success:
        return QuorumResult(
            requested=True,
            applied=False,
            skipped_reason=f"consultant_{consultant_decision.reason}",
            lead_adapter=lead_adapter,
            lead_provider=lead_provider,
            lead_model=lead_model,
        )

    consultant_plan = QuorumConsultantPlan(
        adapter=selected_adapter_name(consultant_decision),
        provider=str(consultant_decision.provider or ""),
        model=str(consultant_decision.model or ""),
        output=str(consultant_decision.response.content or ""),
        status="consulted",
    )

    final_request = RoutingRequest(
        role=Role.TEAM_LEAD,
        complexity=complexity,
        criticality=criticality,
        required_capabilities={"reasoning"},
        excluded_adapters={consultant_plan.adapter} if consultant_plan.adapter else set(),
        environment=environment,
    )
    final_decision = router.route_and_invoke(
        final_request,
        _final_consolidation_prompt(
            message=message,
            lead_output=lead_output,
            consultant_plans=[consultant_plan],
        ),
        task_id=f"{task_root}::lead_quorum_final",
    )
    if not final_decision.success:
        return QuorumResult(
            requested=True,
            applied=False,
            skipped_reason=f"final_{final_decision.reason}",
            lead_adapter=lead_adapter,
            lead_provider=lead_provider,
            lead_model=lead_model,
            consultant_plans=[consultant_plan],
        )

    return QuorumResult(
        requested=True,
        applied=True,
        lead_adapter=lead_adapter,
        lead_provider=lead_provider,
        lead_model=lead_model,
        final_adapter=selected_adapter_name(final_decision),
        final_provider=str(final_decision.provider or ""),
        final_model=str(final_decision.model or ""),
        consultant_plans=[consultant_plan],
        final_plan=str(final_decision.response.content or ""),
    )
