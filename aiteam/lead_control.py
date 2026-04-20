from __future__ import annotations

from dataclasses import dataclass, field
import re

from aiteam.types import Complexity, Criticality
from aiteam.workflow_planner import PhaseSpec, default_phases, parse_workflow_plan


@dataclass(frozen=True)
class LeadEarlyExit:
    state: str
    justification: str


@dataclass(frozen=True)
class LeadDirectiveEvent:
    directive: str
    payload: object | None = None
    skipped: list[str] = field(default_factory=list)
    phase_id: str = ""
    role: str = ""
    objective: str = ""
    extension: int = 0
    new_round_budget: int = 0

    def to_event_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"directive": self.directive}
        if self.payload is not None:
            payload["payload"] = self.payload
        if self.skipped:
            payload["skipped"] = self.skipped
        if self.phase_id:
            payload["phase_id"] = self.phase_id
        if self.role:
            payload["role"] = self.role
        if self.objective:
            payload["objective"] = self.objective
        if self.extension > 0:
            payload["extension"] = self.extension
        if self.new_round_budget > 0:
            payload["new_round_budget"] = self.new_round_budget
        return payload


@dataclass(frozen=True)
class DelegateDirectiveRequest:
    intent: str
    query: str
    wait_policy: str = "all"
    delegate_budget: int = 3


@dataclass(frozen=True)
class LeadIntakeResolution:
    cleaned_output: str
    directives: dict
    phases: list[PhaseSpec]
    complexity: Complexity
    criticality: Criticality
    round_budget: int
    early_exit: LeadEarlyExit | None = None
    events: list[LeadDirectiveEvent] = field(default_factory=list)
    plan_source: str = "none"


_SELECTIVE_LCP_PATTERNS: dict[str, str] = {
    "ADVISORY_MODE": r'\[ADVISORY_MODE:\s*"[^"]+"\]',
    "PAUSE_FOR_USER": r'\[PAUSE_FOR_USER:\s*"[^"]+"\]',
    "SKIP_PHASE": r'\[SKIP_PHASE:\s*"[^"]+"(?:\s+reason="[^"]*")?\]',
    "DEGRADE": r'\[DEGRADE:\s*scope="(?:minimal|partial)"(?:\s+reason="[^"]*")?\]',
    "ESCALATE": r"\[ESCALATE:\s*[^\]]+\]",
    "EXTEND_BUDGET": r"\[EXTEND_BUDGET:\s*\+\d+\]",
    "SET_BUDGET": r"\[SET_BUDGET:\s*\d+\]",
    "RETRY_ROUTE": r'\[RETRY_ROUTE:\s*"[^"]+"\]',
    "DELEGATE": r'\[DELEGATE:\s*"(.+?)"\]',
    "DELEGATE_REPO_SCAN": r'\[DELEGATE_REPO_SCAN:\s*"(.+?)"\]',
    "DELEGATE_BROWSER_REPRO": r'\[DELEGATE_BROWSER_REPRO:\s*"(.+?)"\]',
    "DELEGATE_LSP_IMPACT": r'\[DELEGATE_LSP_IMPACT:\s*"(.+?)"\]',
    "DELEGATE_TEST_RUN": r'\[DELEGATE_TEST_RUN:\s*"(.+?)"\]',
    "DELEGATE_MCP_PROBE": r'\[DELEGATE_MCP_PROBE:\s*"(.+?)"\]',
    "WAIT_POLICY": r"\[WAIT_POLICY:\s*(all|best_effort|quorum)\s*\]",
    "DELEGATE_BUDGET": r"\[DELEGATE_BUDGET:\s*\+?\d+\s*\]",
}

_VALID_RUN_MODES: frozenset[str] = frozenset(
    {"planning_only", "team_decision", "architecture_review", "roadmap"}
)


def extract_lcp_directives(text: str) -> dict:
    """Parsea las directivas LCP emitidas por el Team Lead."""
    result: dict = {}
    t = str(text or "")

    direct_answer_match = re.search(
        r'\[DIRECT_ANSWER(?:\s*:\s*"(.+?)")?\]',
        t,
        re.DOTALL | re.IGNORECASE,
    )
    if direct_answer_match:
        result["direct_answer"] = True
        direct_answer_text = str(direct_answer_match.group(1) or "").strip()
        if direct_answer_text:
            result["direct_answer_text"] = direct_answer_text

    if re.search(r"\[REPLAN\]", t, re.IGNORECASE):
        result["replan"] = True

    m = re.search(r'\[ADVISORY_MODE:\s*"(.+?)"\]', t, re.DOTALL | re.IGNORECASE)
    if m:
        result["advisory_mode"] = m.group(1).strip()

    m = re.search(r'\[PAUSE_FOR_USER:\s*"(.+?)"\]', t, re.DOTALL | re.IGNORECASE)
    if m:
        result["pause_for_user"] = m.group(1).strip()

    m = re.search(
        r'\[SKIP_PHASE:\s*"([^"]+)"(?:\s+reason="([^"]*)")?\]',
        t,
        re.IGNORECASE,
    )
    if m:
        result["skip_phase"] = {
            "phase_id": m.group(1).strip(),
            "reason": str(m.group(2) or "").strip(),
        }

    m = re.search(
        r'\[DEGRADE:\s*scope="(minimal|partial)"(?:\s+reason="([^"]*)")?\]',
        t,
        re.IGNORECASE,
    )
    if m:
        result["degrade"] = {
            "scope": m.group(1).strip().lower(),
            "reason": str(m.group(2) or "").strip(),
        }

    m = re.search(r'\[REJECT:\s*"(.+?)"\]', t, re.DOTALL | re.IGNORECASE)
    if m:
        result["reject"] = m.group(1).strip()

    m = re.search(r'\[ABORT_PHASES:\s*"(.+?)"\]', t, re.DOTALL | re.IGNORECASE)
    if m:
        result["abort_phases"] = m.group(1).strip()

    m = re.search(r"\[ESCALATE:\s*([^\]]+)\]", t, re.IGNORECASE)
    if m:
        escalate: dict = {}
        payload = m.group(1)
        cm = re.search(r"complexity=(\w+)", payload, re.IGNORECASE)
        if cm:
            escalate["complexity"] = cm.group(1).lower().replace("-", "_")
        crit_m = re.search(r"criticality=(\w+)", payload, re.IGNORECASE)
        if crit_m:
            escalate["criticality"] = crit_m.group(1).lower()
        if escalate:
            result["escalate"] = escalate

    m = re.search(r'\[SKIP:\s*"([^"]+)"\]', t, re.IGNORECASE)
    if m:
        phase_ids = [p.strip() for p in m.group(1).split() if p.strip()]
        if phase_ids:
            result["skip"] = phase_ids

    m = re.search(r'\[ADD_PHASE:\s*(\w+)\s+"([^"]+)"\]', t, re.IGNORECASE)
    if m:
        result["add_phase"] = {
            "role": m.group(1).upper(),
            "objective": m.group(2).strip(),
        }

    m = re.search(r"\[EXTEND_BUDGET:\s*\+(\d+)\]", t, re.IGNORECASE)
    if m:
        try:
            result["extend_budget"] = int(m.group(1))
        except ValueError:
            pass

    m = re.search(r"\[SET_BUDGET:\s*(\d+)\]", t, re.IGNORECASE)
    if m:
        try:
            result["set_budget"] = int(m.group(1))
        except ValueError:
            pass

    m = re.search(r'\[FORCE_GATE:\s*"([^"]+)"\]', t, re.IGNORECASE)
    if m:
        result["force_gate"] = m.group(1).strip()

    m = re.search(r'\[RETRY_ROUTE:\s*"([^"]+)"\]', t, re.IGNORECASE)
    if m:
        result["retry_route"] = m.group(1).strip()

    m = re.search(r"\[RUN_MODE:\s*([a-z_]+)\s*\]", t, re.IGNORECASE)
    if m:
        run_mode = m.group(1).lower()
        if run_mode in _VALID_RUN_MODES:
            result["run_mode"] = run_mode

    return result


def strip_lcp_directives(text: str) -> str:
    """Elimina directivas LCP y WORKFLOW_PLAN del output visible al usuario."""
    t = str(text or "")
    t = re.sub(
        r"\[WORKFLOW_PLAN\].*?\[/WORKFLOW_PLAN\]",
        "",
        t,
        flags=re.DOTALL | re.IGNORECASE,
    )
    t = re.sub(
        r"\[EVIDENCE_PLAN\].*?\[/EVIDENCE_PLAN\]",
        "",
        t,
        flags=re.DOTALL | re.IGNORECASE,
    )
    lcp_pattern = (
        r"\["
        r"(?:DIRECT_ANSWER|REJECT|ABORT_PHASES|ADVISORY_MODE|PAUSE_FOR_USER|SKIP_PHASE|DEGRADE|ESCALATE|SKIP|ADD_PHASE"
        r"|EXTEND_BUDGET|SET_BUDGET|CLARIFY|FORCE_GATE|RETRY_ROUTE|REPLAN|DELEGATE"
        r"|DELEGATE_REPO_SCAN|DELEGATE_BROWSER_REPRO|DELEGATE_LSP_IMPACT"
        r"|DELEGATE_TEST_RUN|DELEGATE_MCP_PROBE|WAIT_POLICY|DELEGATE_BUDGET)"
        r"[^\]]*\]"
    )
    t = re.sub(lcp_pattern, "", t, flags=re.IGNORECASE)
    t = re.sub(r"\[RUN_MODE:\s*[^\]]+\]", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_selected_lcp_directives(
    text: str,
    directives: list[str] | tuple[str, ...],
) -> str:
    """Elimina solo un subconjunto de directivas LCP del texto."""

    cleaned = str(text or "")
    for directive in directives:
        pattern = _SELECTIVE_LCP_PATTERNS.get(str(directive or "").upper())
        if not pattern:
            continue
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def iter_lead_checkpoint_directives(
    phase_outputs: dict[str, str],
    *,
    include_lead_intake: bool = False,
    reverse: bool = True,
) -> list[tuple[str, str, dict]]:
    """Devuelve checkpoints `lead_*` con sus directivas ya parseadas."""

    items = list((phase_outputs or {}).items())
    if reverse:
        items = list(reversed(items))
    collected: list[tuple[str, str, dict]] = []
    for phase_name, output in items:
        normalized_phase = str(phase_name or "").strip()
        if normalized_phase != "lead_close" and not normalized_phase.startswith("lead_"):
            continue
        if not include_lead_intake and normalized_phase == "lead_intake":
            continue
        text = str(output or "")
        collected.append((normalized_phase, text, extract_lcp_directives(text)))
    return collected


def _preset_phases_for_run_mode(run_mode: str) -> list[PhaseSpec]:
    """Presets de corrida para el Lead cuando no quiere un build tradicional."""

    if run_mode == "planning_only":
        return [
            PhaseSpec(
                phase_id="discovery",
                role="RESEARCHER",
                objective=(
                    "Recopila contexto, restricciones, dependencias y supuestos "
                    "relevantes para planificar la solicitud del usuario."
                ),
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="plan",
                role="REVIEWER",
                objective=(
                    "Propone un plan de trabajo concreto con tradeoffs, orden recomendado, "
                    "riesgos y decisiones pendientes, sin ejecutar cambios."
                ),
                depends_on=["discovery"],
            ),
        ]
    if run_mode == "team_decision":
        return [
            PhaseSpec(
                phase_id="discovery",
                role="RESEARCHER",
                objective=(
                    "Recopila el contexto tecnico y resume los hechos confirmados "
                    "necesarios para tomar una decision de equipo."
                ),
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="review_options",
                role="REVIEWER",
                objective=(
                    "Plantea opciones de decision, tradeoffs y objeciones tecnicas "
                    "sobre la solicitud, sin implementar cambios."
                ),
                depends_on=["discovery"],
            ),
            PhaseSpec(
                phase_id="qa_risks",
                role="QA",
                objective=(
                    "Valida riesgos operativos, criterios de aceptacion y condiciones "
                    "para una decision responsable del equipo."
                ),
                depends_on=["review_options"],
            ),
        ]
    if run_mode == "architecture_review":
        return [
            PhaseSpec(
                phase_id="discovery",
                role="RESEARCHER",
                objective=(
                    "Recopila requisitos, restricciones, tradeoffs y contexto tecnico "
                    "del sistema para sustentar una revision de arquitectura."
                ),
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="architecture_options",
                role="REVIEWER",
                objective=(
                    "Analiza opciones de arquitectura, compara tradeoffs y recomienda "
                    "la direccion tecnica mas defendible."
                ),
                depends_on=["discovery"],
            ),
            PhaseSpec(
                phase_id="adr_document",
                role="REVIEWER",
                objective=(
                    "Produce un ADR en Markdown con decision, contexto, opciones "
                    "consideradas, tradeoffs y consecuencias."
                ),
                depends_on=["architecture_options"],
            ),
        ]
    if run_mode == "roadmap":
        return [
            PhaseSpec(
                phase_id="discovery",
                role="RESEARCHER",
                objective=(
                    "Recopila objetivos, dependencias, restricciones y contexto "
                    "necesario para proponer un roadmap viable."
                ),
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="roadmap_prioritization",
                role="REVIEWER",
                objective=(
                    "Prioriza features, estima complejidad y define una secuencia "
                    "recomendada de entrega con tradeoffs claros."
                ),
                depends_on=["discovery"],
            ),
            PhaseSpec(
                phase_id="roadmap_document",
                role="REVIEWER",
                objective=(
                    "Produce un documento Markdown con roadmap priorizado, complejidad, "
                    "dependencias y secuencia recomendada."
                ),
                depends_on=["roadmap_prioritization"],
            ),
        ]
    return []


def extract_clarify_directive(text: str) -> str | None:
    m = re.search(r'\[CLARIFY:\s*"(.+?)"\]', str(text or ""), re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def extract_delegate_directive(text: str) -> str | None:
    request = extract_delegate_request(text)
    if request is None:
        return None
    return request.query


def extract_delegate_request(text: str) -> DelegateDirectiveRequest | None:
    raw = str(text or "")
    patterns = [
        ("delegate_repo_scan", r'\[DELEGATE_REPO_SCAN:\s*"(.+?)"\]'),
        ("delegate_browser_repro", r'\[DELEGATE_BROWSER_REPRO:\s*"(.+?)"\]'),
        ("delegate_lsp_impact", r'\[DELEGATE_LSP_IMPACT:\s*"(.+?)"\]'),
        ("delegate_test_run", r'\[DELEGATE_TEST_RUN:\s*"(.+?)"\]'),
        ("delegate_mcp_probe", r'\[DELEGATE_MCP_PROBE:\s*"(.+?)"\]'),
        ("delegate", r'\[DELEGATE:\s*"(.+?)"\]'),
    ]
    for intent, pattern in patterns:
        m = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        if not m:
            continue
        wait_policy = "all"
        wait_match = re.search(
            r"\[WAIT_POLICY:\s*(all|best_effort|quorum)\s*\]",
            raw,
            re.IGNORECASE,
        )
        if wait_match:
            wait_policy = wait_match.group(1).strip().lower()
        delegate_budget = 3
        budget_match = re.search(
            r"\[DELEGATE_BUDGET:\s*\+?(\d+)\s*\]",
            raw,
            re.IGNORECASE,
        )
        if budget_match:
            try:
                delegate_budget = max(1, int(budget_match.group(1)))
            except ValueError:
                delegate_budget = 3
        return DelegateDirectiveRequest(
            intent=intent,
            query=m.group(1).strip(),
            wait_policy=wait_policy,
            delegate_budget=delegate_budget,
        )
    return None


def extract_evidence_plan(text: str) -> dict[str, dict[str, object]]:
    raw = str(text or "")
    block_match = re.search(
        r"\[EVIDENCE_PLAN\](.*?)\[/EVIDENCE_PLAN\]",
        raw,
        re.DOTALL | re.IGNORECASE,
    )
    if not block_match:
        return {}

    valid_intents = {
        "delegate",
        "delegate_repo_scan",
        "delegate_browser_repro",
        "delegate_lsp_impact",
        "delegate_test_run",
        "delegate_mcp_probe",
    }
    plan: dict[str, dict[str, object]] = {}
    current_phase = ""
    for raw_line in block_match.group(1).splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "phase_id":
            current_phase = value
            if current_phase:
                plan.setdefault(
                    current_phase,
                    {
                        "delegate_intents": [],
                        "wait_policy": "all",
                        "delegate_budget": 3,
                    },
                )
            continue
        if not current_phase:
            continue
        entry = plan.setdefault(
            current_phase,
            {
                "delegate_intents": [],
                "wait_policy": "all",
                "delegate_budget": 3,
            },
        )
        if key in {"delegate", "intent"}:
            intent = value.strip().lower()
            if intent in valid_intents:
                intents = list(entry.get("delegate_intents", []) or [])
                intents.append(intent)
                entry["delegate_intents"] = intents
        elif key == "wait_policy":
            policy = value.strip().lower()
            if policy in {"all", "best_effort", "quorum"}:
                entry["wait_policy"] = policy
        elif key == "delegate_budget":
            try:
                entry["delegate_budget"] = max(1, int(value))
            except ValueError:
                pass

    normalized: dict[str, dict[str, object]] = {}
    for phase_id, entry in plan.items():
        intents = [
            str(item).strip().lower()
            for item in list(entry.get("delegate_intents", []) or [])
            if str(item).strip().lower() in valid_intents
        ]
        if not phase_id or not intents:
            continue
        normalized[phase_id] = {
            "delegate_intents": intents,
            "wait_policy": str(entry.get("wait_policy", "all") or "all").strip().lower(),
            "delegate_budget": max(1, int(entry.get("delegate_budget", 3))),
        }
    return normalized


def resolve_lead_intake(
    *,
    lead_output: str,
    chat_mode: str,
    complexity: Complexity,
    criticality: Criticality,
    round_budget: int,
    forbid_direct_answer: bool = False,
) -> LeadIntakeResolution:
    """Resuelve el control inicial del Lead antes de crear fases dinamicas."""
    directives = extract_lcp_directives(lead_output)
    cleaned_output = strip_lcp_directives(lead_output)
    round_budget_initial = round_budget
    events: list[LeadDirectiveEvent] = []

    if directives.get("reject"):
        events.append(
            LeadDirectiveEvent(directive="reject", payload=directives["reject"])
        )
        return LeadIntakeResolution(
            cleaned_output=cleaned_output,
            directives=directives,
            phases=[],
            complexity=complexity,
            criticality=criticality,
            round_budget=round_budget,
            early_exit=LeadEarlyExit(
                state="rejected",
                justification=f"Lead rechazó la petición: {directives['reject']}",
            ),
            events=events,
        )

    if directives.get("abort_phases"):
        events.append(
            LeadDirectiveEvent(
                directive="abort_phases",
                payload=directives["abort_phases"],
            )
        )
        return LeadIntakeResolution(
            cleaned_output=cleaned_output,
            directives=directives,
            phases=[],
            complexity=complexity,
            criticality=criticality,
            round_budget=round_budget,
            early_exit=LeadEarlyExit(
                state="completed",
                justification="Lead abortó fases: respuesta disponible sin agentes.",
            ),
            events=events,
        )

    if directives.get("advisory_mode"):
        events.append(
            LeadDirectiveEvent(
                directive="advisory_mode",
                payload=directives["advisory_mode"],
            )
        )
        return LeadIntakeResolution(
            cleaned_output=cleaned_output,
            directives=directives,
            phases=[],
            complexity=complexity,
            criticality=criticality,
            round_budget=round_budget,
            early_exit=LeadEarlyExit(
                state="completed",
                justification="Lead cerró en advisory mode sin lanzar fases.",
            ),
            events=events,
        )

    if directives.get("direct_answer") and forbid_direct_answer:
        directives["direct_answer"] = False
        events.append(
            LeadDirectiveEvent(
                directive="direct_answer_blocked",
                payload="continuation_pending_phases",
            )
        )

    if directives.get("direct_answer"):
        events.append(LeadDirectiveEvent(directive="direct_answer"))
        return LeadIntakeResolution(
            cleaned_output=cleaned_output,
            directives=directives,
            phases=[],
            complexity=complexity,
            criticality=criticality,
            round_budget=round_budget,
            early_exit=LeadEarlyExit(
                state="completed",
                justification="Lead respondió directamente sin lanzar fases.",
            ),
            events=events,
        )

    if directives.get("escalate"):
        esc = directives["escalate"]
        valid_complexity = {"low", "medium", "high", "very_high"}
        valid_criticality = {"low", "medium", "high", "critical"}
        complexity_changed = False
        if "complexity" in esc and esc["complexity"] in valid_complexity:
            complexity = Complexity(esc["complexity"])
            complexity_changed = True
        if "criticality" in esc and esc["criticality"] in valid_criticality:
            criticality = Criticality(esc["criticality"])
        if complexity_changed:
            boost = {"high": 1.5, "very_high": 2.0}.get(complexity.value, 1.0)
            if boost > 1.0:
                round_budget = min(
                    int(round_budget * boost),
                    round_budget_initial * 3,
                )
        events.append(
            LeadDirectiveEvent(
                directive="escalate",
                payload=esc,
                new_round_budget=round_budget,
            )
        )

    explicit_plan = parse_workflow_plan(lead_output)
    run_mode = directives.get("run_mode", "")
    if explicit_plan is not None:
        phases = explicit_plan
        plan_source = "explicit_workflow_plan"
    elif run_mode:
        phases = _preset_phases_for_run_mode(run_mode) or default_phases(chat_mode)
        plan_source = f"run_mode:{run_mode}"
        events.append(
            LeadDirectiveEvent(
                directive="run_mode",
                payload=run_mode,
            )
        )
    else:
        phases = default_phases(chat_mode)
        plan_source = "default"

    if directives.get("skip"):
        skip_set = set(directives["skip"])
        skipped = [p.phase_id for p in phases if p.phase_id in skip_set]
        phases = [p for p in phases if p.phase_id not in skip_set]
        if skipped:
            events.append(
                LeadDirectiveEvent(directive="skip", skipped=skipped)
            )

    if directives.get("add_phase"):
        ap = directives["add_phase"]
        ap_role = ap.get("role", "ENGINEER")
        ap_obj = ap.get("objective", "")
        valid_ap_roles = {"RESEARCHER", "ENGINEER", "REVIEWER", "QA"}
        if ap_role in valid_ap_roles and ap_obj:
            ap_phase_id = f"extra_{ap_role.lower()}"
            existing_ids = {p.phase_id for p in phases}
            ap_counter = 0
            while ap_phase_id in existing_ids:
                ap_counter += 1
                ap_phase_id = f"extra_{ap_role.lower()}_{ap_counter}"
            ap_depends = [phases[-1].phase_id] if phases else []
            phases.append(
                PhaseSpec(
                    phase_id=ap_phase_id,
                    role=ap_role,
                    objective=ap_obj,
                    depends_on=ap_depends,
                )
            )
            events.append(
                LeadDirectiveEvent(
                    directive="add_phase",
                    phase_id=ap_phase_id,
                    role=ap_role,
                    objective=ap_obj[:120],
                )
            )

    if directives.get("extend_budget"):
        extension = int(directives["extend_budget"])
        round_budget = min(round_budget + extension, round_budget_initial * 3)
        events.append(
            LeadDirectiveEvent(
                directive="extend_budget",
                extension=extension,
                new_round_budget=round_budget,
            )
        )

    if directives.get("set_budget"):
        round_budget = max(1, min(int(directives["set_budget"]), round_budget_initial * 3))
        events.append(
            LeadDirectiveEvent(
                directive="set_budget",
                payload=round_budget,
                new_round_budget=round_budget,
            )
        )

    return LeadIntakeResolution(
        cleaned_output=cleaned_output,
        directives=directives,
        phases=phases,
        complexity=complexity,
        criticality=criticality,
        round_budget=round_budget,
        early_exit=None,
        events=events,
        plan_source=plan_source,
    )
