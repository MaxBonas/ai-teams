from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from aiteam.types import Complexity, Criticality, Role, RoutingRequest


PLANNING_QUORUM_RUN_MODES: frozenset[str] = frozenset(
    {"planning_only", "architecture_review", "roadmap"}
)

# Numero de auditores por quorum (min 1, max 4). Configurable via env.
_DEFAULT_CONSULTANT_COUNT = 2


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
    """Decide si el quorum debe ejecutarse en la fase de planificacion del Lead.

    Modos de activacion (en orden de precedencia):
      1. AITEAM_AUTO_QUORUM=1  →  siempre activo, independiente de run_mode o payload.
      2. payload.quorum=True + run_mode en PLANNING_QUORUM_RUN_MODES  →  activacion explicita legacy.
    """
    auto = os.getenv("AITEAM_AUTO_QUORUM", "0").strip().lower() in {"1", "true", "yes", "on"}
    if auto:
        return True
    normalized = str(run_mode or "").strip().lower()
    return bool(requested) and normalized in PLANNING_QUORUM_RUN_MODES


def _quorum_consultant_count() -> int:
    """Lee AITEAM_QUORUM_CONSULTANT_COUNT (default 2, rango 1-4)."""
    raw = os.getenv(
        "AITEAM_QUORUM_CONSULTANT_COUNT", str(_DEFAULT_CONSULTANT_COUNT)
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_CONSULTANT_COUNT
    return max(1, min(value, 4))


def selected_adapter_name(decision) -> str:
    for attempt in reversed(list(getattr(decision, "attempts", []) or [])):
        raw = str(attempt or "").strip()
        if ":ok" not in raw:
            continue
        parts = raw.split(":")
        if parts:
            return parts[0].strip()
    return ""


# ── Prompts ───────────────────────────────────────────────────────────────────


def _auditor_prompt(
    *,
    base_prompt: str,
    lead_output: str,
    auditor_index: int,
    auditor_total: int,
    previous_auditors: list[QuorumConsultantPlan],
) -> str:
    """Prompt de cada auditor en el quorum.

    El primer auditor trabaja en solitario sobre el plan del Lead.
    Los auditores posteriores ven ademas los aportes de los auditores anteriores:
    esto simula la 'reunion de seniors', donde cada participante construye sobre
    lo ya dicho en lugar de repetir los mismos argumentos.
    """
    position_label = f"auditor {auditor_index} de {auditor_total}"

    previous_block = ""
    if previous_auditors:
        lines = [
            "## Aportes de auditores anteriores",
            "Consideralos para enriquecer tu analisis — puedes coincidir, discrepar o ampliar.",
            "",
        ]
        for idx, prev in enumerate(previous_auditors, start=1):
            lines.append(f"### Auditor {idx} ({prev.provider} / {prev.model})")
            lines.append(prev.output.strip() or "_sin salida_")
            lines.append("")
        previous_block = "\n" + "\n".join(lines)

    return (
        f"{base_prompt}\n\n"
        "Modo quorum: consultor independiente.\n"
        f"## Quorum de planificacion — {position_label}\n"
        "Eres un consultor senior independiente convocado para auditar la propuesta del Lead ejecutivo.\n"
        "Recibiras el contexto completo del proyecto y el plan inicial del Lead.\n"
        "Tu mision: evaluarlo con criterio propio. Puedes validar, mejorar o contradecir.\n"
        "Si detectas riesgos, lagunas, o ves un enfoque mejor, explicitalos con precision.\n"
        "No copies ni repitas el plan sin anadir valor.\n\n"
        "## Plan inicial del Lead ejecutivo\n"
        f"{lead_output.strip() or '_sin propuesta_'}"
        f"{previous_block}"
    )


def _consolidation_prompt(
    *,
    message: str,
    lead_output: str,
    auditor_plans: list[QuorumConsultantPlan],
) -> str:
    """Prompt de consolidacion del Lead ejecutivo.

    El Lead lee todos los aportes del quorum, los sopesa con su propio juicio
    y emite el plan definitivo. Debe justificar sus decisiones:
    que acepta, que matiza y que descarta de cada auditor, y por que.
    Tiene la ultima palabra.
    """
    auditor_sections: list[str] = []
    for idx, plan in enumerate(auditor_plans, start=1):
        auditor_sections.append(
            f"### Auditor {idx} ({plan.provider} / {plan.model})\n"
            + (plan.output.strip() or "_sin salida_")
        )

    auditor_block = (
        "\n\n---\n\n".join(auditor_sections)
        if auditor_sections
        else "_sin auditores_"
    )

    return (
        "Eres Team Lead ejecutivo. El quorum de auditores ha concluido.\n"
        "Modo quorum: consolidacion final del Lead.\n"
        "Has escuchado a tus consultores seniors. Ahora tomas la decision final.\n\n"
        "Instrucciones:\n"
        "1. Revisa tu plan inicial y los aportes de cada auditor.\n"
        "2. Para cada aporte significativo, indica si lo aceptas, matizas o descartas, y por que.\n"
        "3. Emite el plan definitivo incorporando lo que consideres valioso.\n"
        "4. Mantén el formato estandar del Lead: [RUN_MODE], [WORKFLOW_PLAN] y demas directivas.\n"
        "Tienes la ultima palabra. El flujo de ejecucion continua con tu plan consolidado.\n\n"
        "## Solicitud original\n"
        f"{str(message or '').strip() or '_sin solicitud_'}\n\n"
        "## Tu plan inicial\n"
        f"{lead_output.strip() or '_sin plan inicial_'}\n\n"
        f"## Aportes del quorum ({len(auditor_plans)} auditor(es))\n"
        f"{auditor_block}"
    )


# ── Ejecucion del quorum ───────────────────────────────────────────────────────


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
    """Ejecuta el quorum de planificacion del Lead ejecutivo.

    Flujo deliberativo (una sola vez por run):

      Fase 1 — Auditoria encadenada:
        Auditor 1  →  ve plan del Lead, evalua independientemente.
        Auditor 2  →  ve plan del Lead + aporte de Auditor 1, sintetiza y enriquece.
        (hasta N auditores, configurado via AITEAM_QUORUM_CONSULTANT_COUNT)

        Cada auditor usa un provider diferente (exclusion acumulada) para garantizar
        perspectivas diversas. Si no hay suficientes providers disponibles, el quorum
        continua con los auditores que pudo obtener.

      Fase 2 — Consolidacion ejecutiva:
        El Lead ejecutivo recibe su plan inicial + todos los aportes del quorum.
        Justifica sus decisiones (acepta / matiza / descarta) y emite el plan definitivo.
        Tiene la ultima palabra. El flujo de ejecucion continua con este plan.

    Coste: 1 llamada por auditor + 1 llamada de consolidacion = N+1 llamadas extra.
    Se ejecuta una sola vez por run, en la fase de planificacion del Lead.
    """
    consultant_count = _quorum_consultant_count()

    # ── Fase 1: Auditores encadenados ────────────────────────────────────────
    auditor_plans: list[QuorumConsultantPlan] = []
    excluded: set[str] = {lead_adapter} if lead_adapter else set()

    for i in range(consultant_count):
        auditor_request = RoutingRequest(
            role=Role.TEAM_LEAD,
            complexity=complexity,
            criticality=criticality,
            required_capabilities={"reasoning"},
            excluded_adapters=excluded.copy(),
            environment=environment,
        )
        auditor_decision = router.route_and_invoke(
            auditor_request,
            _auditor_prompt(
                base_prompt=base_prompt,
                lead_output=lead_output,
                auditor_index=i + 1,
                auditor_total=consultant_count,
                # Cada auditor ve los aportes de los anteriores (efecto reunion)
                previous_auditors=list(auditor_plans),
            ),
            task_id=f"{task_root}::lead_quorum_auditor_{i + 1}",
        )

        if not auditor_decision.success:
            # No quedan providers disponibles — detenemos la fase de auditoria
            break

        auditor_adapter = selected_adapter_name(auditor_decision)
        auditor_plans.append(
            QuorumConsultantPlan(
                adapter=auditor_adapter,
                provider=str(auditor_decision.provider or ""),
                model=str(auditor_decision.model or ""),
                output=str(auditor_decision.response.content or ""),
                status="consulted",
            )
        )

        # Acumulamos exclusion para garantizar diversidad de provider
        if auditor_adapter:
            excluded.add(auditor_adapter)

    if not auditor_plans:
        return QuorumResult(
            requested=True,
            applied=False,
            skipped_reason="no_auditor_available",
            lead_adapter=lead_adapter,
            lead_provider=lead_provider,
            lead_model=lead_model,
        )

    # ── Fase 2: Consolidacion del Lead ejecutivo ──────────────────────────────
    # El Lead no puede usar el adapter de ningun auditor — garantiza perspectiva propia.
    auditor_adapters = {p.adapter for p in auditor_plans if p.adapter}
    final_request = RoutingRequest(
        role=Role.TEAM_LEAD,
        complexity=complexity,
        criticality=criticality,
        required_capabilities={"reasoning"},
        excluded_adapters=auditor_adapters,
        environment=environment,
    )
    final_decision = router.route_and_invoke(
        final_request,
        _consolidation_prompt(
            message=message,
            lead_output=lead_output,
            auditor_plans=auditor_plans,
        ),
        task_id=f"{task_root}::lead_quorum_final",
    )

    if not final_decision.success:
        return QuorumResult(
            requested=True,
            applied=False,
            skipped_reason=f"consolidation_{final_decision.reason}",
            lead_adapter=lead_adapter,
            lead_provider=lead_provider,
            lead_model=lead_model,
            consultant_plans=auditor_plans,
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
        consultant_plans=auditor_plans,
        final_plan=str(final_decision.response.content or ""),
    )
