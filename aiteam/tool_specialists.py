from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from aiteam.tool_inventory import (
    derive_target_capabilities,
    normalize_lsp_targets,
    normalize_skill_targets,
    normalize_tool_capabilities,
)
from aiteam.types import Complexity, Criticality, Role


TOOL_SPECIALIST_CONTRACT_VERSION = "tool_specialist_v1"
SPECIALIST_REPORT_VERSION = "specialist_report_v1"


@dataclass(frozen=True)
class ToolSpecialistProfile:
    name: str
    label: str
    owner_role: Role
    summary: str
    tool_families: list[str]
    preferred_capabilities: list[str]
    default_tier: str


@dataclass(frozen=True)
class SpecialistReport:
    specialist: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    recommendation: str = ""
    confidence: float = 0.0
    provider: str = ""
    model: str = ""
    toolset_used: list[str] = field(default_factory=list)
    tokens_used: int = 0
    raw_output_preview: str = ""
    report_version: str = SPECIALIST_REPORT_VERSION
    validation_status: str = "valid"
    validation_errors: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "specialist": self.specialist,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "artifacts": list(self.artifacts),
            "risks": list(self.risks),
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "provider": self.provider,
            "model": self.model,
            "toolset_used": list(self.toolset_used),
            "tokens_used": self.tokens_used,
            "raw_output_preview": self.raw_output_preview,
            "report_version": self.report_version,
            "validation_status": self.validation_status,
            "validation_errors": list(self.validation_errors),
        }

    @staticmethod
    def from_metadata(payload: dict[str, Any] | None) -> "SpecialistReport":
        raw = payload if isinstance(payload, dict) else {}
        confidence_raw = raw.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except Exception:
            confidence = 0.0
        tokens_raw = raw.get("tokens_used", 0)
        try:
            tokens_used = max(0, int(tokens_raw or 0))
        except Exception:
            tokens_used = 0
        return _validate_specialist_report(
            SpecialistReport(
                specialist=str(raw.get("specialist", "") or "").strip().lower(),
                summary=str(raw.get("summary", "") or "").strip(),
                evidence=_coerce_report_list(raw.get("evidence", [])),
                artifacts=_coerce_report_list(raw.get("artifacts", [])),
                risks=_coerce_report_list(raw.get("risks", [])),
                recommendation=str(raw.get("recommendation", "") or "").strip(),
                confidence=confidence,
                provider=str(raw.get("provider", "") or "").strip(),
                model=str(raw.get("model", "") or "").strip(),
                toolset_used=_coerce_report_list(raw.get("toolset_used", [])),
                tokens_used=tokens_used,
                raw_output_preview=str(raw.get("raw_output_preview", "") or "").strip()[:500],
                report_version=str(raw.get("report_version", SPECIALIST_REPORT_VERSION) or SPECIALIST_REPORT_VERSION).strip(),
                validation_status=str(raw.get("validation_status", "valid") or "valid").strip(),
                validation_errors=_coerce_report_list(raw.get("validation_errors", [])),
            )
        )


TOOL_SPECIALISTS: dict[str, ToolSpecialistProfile] = {
    "repo_scout": ToolSpecialistProfile(
        name="repo_scout",
        label="Repo Scout",
        owner_role=Role.SCOUT,
        summary="Inspecciona repositorio, archivos, git y contexto local; resume hechos compactos.",
        tool_families=["repo", "cli"],
        preferred_capabilities=["repo_read"],
        default_tier="budget_api",
    ),
    "context_curator": ToolSpecialistProfile(
        name="context_curator",
        label="Context Curator",
        owner_role=Role.SCOUT,
        summary="Compacta contexto vivo del proyecto y convierte historial ruidoso en memoria util por capas.",
        tool_families=["repo", "memory", "summary"],
        preferred_capabilities=["repo_read"],
        default_tier="budget_api",
    ),
    "lsp_navigator": ToolSpecialistProfile(
        name="lsp_navigator",
        label="LSP Navigator",
        owner_role=Role.RESEARCHER,
        summary="Navega simbolos, referencias y diagnosticos semanticos; devuelve impacto y hotspots.",
        tool_families=["lsp"],
        preferred_capabilities=["lsp_symbols", "lsp_references"],
        default_tier="budget_api",
    ),
    "browser_operator": ToolSpecialistProfile(
        name="browser_operator",
        label="Browser Operator",
        owner_role=Role.QA,
        summary="Opera navegador, Playwright o MCP de UI; reproduce pasos y devuelve evidencia.",
        tool_families=["browser", "mcp"],
        preferred_capabilities=["browser_nav", "browser_test"],
        default_tier="budget_api",
    ),
    "test_runner": ToolSpecialistProfile(
        name="test_runner",
        label="Test Runner",
        owner_role=Role.QA,
        summary="Ejecuta checks, tests y validaciones automatizadas; resume resultados y regresiones.",
        tool_families=["execution", "cli"],
        preferred_capabilities=["test_execute", "build_execute"],
        default_tier="budget_api",
    ),
    "mcp_operator": ToolSpecialistProfile(
        name="mcp_operator",
        label="MCP Operator",
        owner_role=Role.SCOUT,
        summary="Opera MCPs o integraciones externas y devuelve resultados estructurados y compactos.",
        tool_families=["mcp"],
        preferred_capabilities=["external_mcp"],
        default_tier="budget_api",
    ),
    "skill_worker": ToolSpecialistProfile(
        name="skill_worker",
        label="Skill Worker",
        owner_role=Role.SCOUT,
        summary="Ejecuta playbooks/skills concretos y sintetiza el resultado operativo.",
        tool_families=["skill"],
        preferred_capabilities=["skill_run"],
        default_tier="budget_api",
    ),
}


def specialist_profile(name: str) -> ToolSpecialistProfile | None:
    key = str(name or "").strip().lower()
    if not key:
        return None
    return TOOL_SPECIALISTS.get(key)


def _coerce_report_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    lines = [line.strip("-* \t") for line in text.splitlines()]
    output = [line for line in lines if line]
    if output:
        return output
    parts = [item.strip() for item in text.split(",")]
    return [item for item in parts if item]


def _extract_section(text: str, label: str) -> str:
    pattern = re.compile(
        rf"(?im)^\s*{re.escape(label)}\s*:\s*(.*?)(?=^\s*[A-Za-z_][A-Za-z0-9_ ]*\s*:|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def _dedupe_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def _validate_specialist_report(report: SpecialistReport) -> SpecialistReport:
    errors: list[str] = []
    specialist = str(report.specialist or "").strip().lower()
    if not specialist:
        errors.append("missing_specialist")
    summary = str(report.summary or "").strip()
    if not summary:
        summary = str(report.raw_output_preview or "").strip()
    if not summary:
        errors.append("missing_summary")
    recommendation = str(report.recommendation or "").strip()
    confidence = max(0.0, min(1.0, float(report.confidence or 0.0)))
    tokens_used = max(0, int(report.tokens_used or 0))
    validation_status = "valid" if not errors else "invalid"
    return SpecialistReport(
        specialist=specialist,
        summary=summary,
        evidence=_dedupe_preserve(list(report.evidence or [])),
        artifacts=_dedupe_preserve(list(report.artifacts or [])),
        risks=_dedupe_preserve(list(report.risks or [])),
        recommendation=recommendation,
        confidence=confidence,
        provider=str(report.provider or "").strip(),
        model=str(report.model or "").strip(),
        toolset_used=_dedupe_preserve(list(report.toolset_used or [])),
        tokens_used=tokens_used,
        raw_output_preview=str(report.raw_output_preview or "").strip()[:500],
        report_version=str(report.report_version or SPECIALIST_REPORT_VERSION).strip() or SPECIALIST_REPORT_VERSION,
        validation_status=validation_status,
        validation_errors=errors,
    )


def parse_specialist_report(
    raw_output: str,
    *,
    specialist: str,
    provider: str = "",
    model: str = "",
    toolset_used: list[str] | None = None,
    tokens_used: int = 0,
) -> SpecialistReport:
    text = str(raw_output or "").strip()
    preview = text[:500]
    normalized_specialist = str(specialist or "").strip().lower()
    default_toolset = [str(item).strip() for item in list(toolset_used or []) if str(item).strip()]

    if not text:
        return _validate_specialist_report(SpecialistReport(
            specialist=normalized_specialist,
            summary="",
            provider=str(provider or "").strip(),
            model=str(model or "").strip(),
            toolset_used=default_toolset,
            tokens_used=max(0, int(tokens_used or 0)),
            raw_output_preview="",
        ))

    parsed_payload: dict[str, Any] | None = None
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            parsed_payload = loaded
    except Exception:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            try:
                loaded = json.loads(str(fenced.group(1) or ""))
                if isinstance(loaded, dict):
                    parsed_payload = loaded
            except Exception:
                parsed_payload = None

    if parsed_payload is not None:
        confidence_raw = parsed_payload.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except Exception:
            confidence = 0.0
        report_toolset = _coerce_report_list(parsed_payload.get("toolset_used", default_toolset))
        if not report_toolset:
            report_toolset = default_toolset
        return _validate_specialist_report(SpecialistReport(
            specialist=str(parsed_payload.get("specialist", normalized_specialist) or normalized_specialist).strip().lower(),
            summary=str(parsed_payload.get("summary", "") or "").strip(),
            evidence=_coerce_report_list(parsed_payload.get("evidence", [])),
            artifacts=_coerce_report_list(parsed_payload.get("artifacts", [])),
            risks=_coerce_report_list(parsed_payload.get("risks", [])),
            recommendation=str(parsed_payload.get("recommendation", "") or "").strip(),
            confidence=confidence,
            provider=str(parsed_payload.get("provider", provider) or provider).strip(),
            model=str(parsed_payload.get("model", model) or model).strip(),
            toolset_used=report_toolset,
            tokens_used=max(0, int(parsed_payload.get("tokens_used", tokens_used) or 0)),
            raw_output_preview=preview,
        ))

    summary = _extract_section(text, "summary")
    evidence = _coerce_report_list(_extract_section(text, "evidence"))
    artifacts = _coerce_report_list(_extract_section(text, "artifacts"))
    risks = _coerce_report_list(_extract_section(text, "risks"))
    recommendation = _extract_section(text, "recommendation")
    confidence_text = _extract_section(text, "confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence_text))) if confidence_text else 0.0
    except Exception:
        confidence = 0.0

    if not summary:
        summary = preview

    return _validate_specialist_report(SpecialistReport(
        specialist=normalized_specialist,
        summary=summary,
        evidence=evidence,
        artifacts=artifacts,
        risks=risks,
        recommendation=recommendation,
        confidence=confidence,
        provider=str(provider or "").strip(),
        model=str(model or "").strip(),
        toolset_used=default_toolset,
        tokens_used=max(0, int(tokens_used or 0)),
        raw_output_preview=preview,
    ))


def infer_tool_specialist(
    *,
    role: Role,
    required_capabilities: list[str] | set[str] | tuple[str, ...],
    metadata: dict[str, Any] | None = None,
) -> str:
    meta = metadata or {}
    explicit = str(meta.get("tool_specialist", "") or "").strip().lower()
    if explicit in TOOL_SPECIALISTS:
        return explicit
    if bool(meta.get("context_curator_requested")):
        return "context_curator"
    if (
        bool(meta.get("context_curator_recommended"))
        or bool(meta.get("context_pressure_high"))
    ) and role != Role.SCOUT:
        return "context_curator"
    rewired = str(meta.get("tool_rewiring_preferred_specialist", "") or "").strip().lower()
    if rewired in TOOL_SPECIALISTS:
        return rewired
    if normalize_lsp_targets(meta.get("lsp_targets", [])):
        return "lsp_navigator"
    if normalize_skill_targets(meta.get("skill_targets", [])):
        return "skill_worker"

    normalized = normalize_tool_capabilities(required_capabilities)
    if role == Role.SCOUT and "repo_read" in normalized:
        return "repo_scout"
    if {"lsp_symbols", "lsp_references"} & set(normalized):
        return "lsp_navigator"
    if {"browser_nav", "browser_test"} & set(normalized):
        return "browser_operator"
    if {"test_execute", "build_execute"} & set(normalized):
        return "test_runner"
    if "external_mcp" in normalized:
        return "mcp_operator"
    if "skill_run" in normalized:
        return "skill_worker"
    return ""


def replacement_specialists_from_metadata(metadata: dict[str, Any] | None) -> list[str]:
    meta = metadata or {}
    replacements = [
        str(item or "").strip().lower()
        for item in list(meta.get("tool_rewiring_candidates", []) or [])
        if str(item or "").strip()
    ]
    selected: list[str] = []
    for name in replacements:
        specialist = ""
        if name.endswith("_skill"):
            specialist = "skill_worker"
        elif any(token in name for token in ("playwright", "browser", "puppeteer")):
            specialist = "browser_operator"
        elif any(token in name for token in ("test", "pytest", "jest", "vitest")):
            specialist = "test_runner"
        elif any(token in name for token in ("lsp", "symbol", "reference")):
            specialist = "lsp_navigator"
        elif any(token in name for token in ("repo", "git")):
            specialist = "repo_scout"
        if specialist and specialist not in selected:
            selected.append(specialist)
    return selected


def build_tool_specialist_metadata(
    *,
    specialist: str,
    required_capabilities: list[str] | set[str] | tuple[str, ...],
    reason: str = "",
    skill_targets: list[str] | set[str] | tuple[str, ...] | None = None,
    lsp_targets: list[str] | set[str] | tuple[str, ...] | None = None,
) -> dict[str, object]:
    profile = specialist_profile(specialist)
    if profile is None:
        return {}
    normalized_targets = derive_target_capabilities(
        skill_targets=skill_targets,
        lsp_targets=lsp_targets,
    )
    return {
        "tool_specialist": profile.name,
        "tool_specialist_label": profile.label,
        "tool_specialist_contract_version": TOOL_SPECIALIST_CONTRACT_VERSION,
        "tool_specialist_reason": str(reason or "").strip(),
        "tool_specialist_tool_families": list(profile.tool_families),
        "tool_specialist_preferred_capabilities": sorted(
            {
                *normalize_tool_capabilities(required_capabilities),
                *normalized_targets,
            }
        ),
        "tool_specialist_default_tier": profile.default_tier,
        "tool_specialist_decision_scope": "operate_tools_and_report_only",
        "tool_specialist_economic_routing": True,
        "tool_specialist_skill_targets": normalize_skill_targets(skill_targets),
        "tool_specialist_lsp_targets": normalize_lsp_targets(lsp_targets),
    }


@dataclass(frozen=True)
class SpecialistRoster:
    """Roster de especialistas óptimo para delegar una tarea.

    El Lead usa este roster para decidir qué operadores baratos consultar antes
    de ejecutar la tarea principal. Cada especialista produce un informe compacto
    (findings, evidence, risks, recommendation) que el Lead lee antes de actuar.
    """

    specialists: list[str]       # nombres ordenados (más crítico primero)
    quorum_required: int         # mínimo de informes para proceder
    quorum_mode: str             # "all" | "majority" | "any"
    reasoning: str               # por qué se seleccionaron estos especialistas
    economics: dict[str, str]    # specialist → tier estimado (budget_api, etc.)

    def is_empty(self) -> bool:
        return len(self.specialists) == 0

    def to_metadata(self) -> dict[str, Any]:
        return {
            "specialist_roster": self.specialists,
            "specialist_roster_quorum_required": self.quorum_required,
            "specialist_roster_quorum_mode": self.quorum_mode,
            "specialist_roster_reasoning": self.reasoning,
            "specialist_roster_economics": self.economics,
        }


def _prioritize_specialist(
    specialists: list[str],
    specialist_name: str,
) -> list[str]:
    normalized = str(specialist_name or "").strip().lower()
    if not normalized or normalized not in specialists:
        return specialists
    return [normalized] + [item for item in specialists if item != normalized]


def select_specialists_for_task(
    *,
    role: Role,
    required_capabilities: list[str] | set[str] | tuple[str, ...],
    complexity: Complexity = Complexity.MEDIUM,
    criticality: Criticality = Criticality.MEDIUM,
    skill_targets: list[str] | set[str] | tuple[str, ...] | None = None,
    lsp_targets: list[str] | set[str] | tuple[str, ...] | None = None,
    available_mcp_servers: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SpecialistRoster:
    """Compone el roster óptimo de especialistas baratos para una tarea.

    Dada la tarea, determina qué operadores baratos (scouts/QA/researchers)
    deben reportar primero para que el Lead decida con contexto sin gastar
    tokens propios en inspecciones pesadas.

    Reglas de composición:
    - Siempre arranca desde infer_tool_specialist() como candidato base
    - Si hay lsp_targets → añadir lsp_navigator
    - Si hay skill_targets → añadir skill_worker
    - Si complejidad HIGH+ y rol ENGINEER/RESEARCHER → añadir repo_scout para contexto
    - Si available_mcp_servers no está vacío y capabilities incluyen external_mcp → añadir mcp_operator
    - Si capabilities incluyen browser → añadir browser_operator
    - Si capabilities incluyen test/build → añadir test_runner
    - Quorum = "all" si criticality HIGH, "majority" si 3+ specialists, "any" si 1
    - Máximo 3 especialistas por roster (economía)
    """
    meta = metadata or {}
    roster_override = meta.get("specialist_roster")
    if roster_override and isinstance(roster_override, list):
        # Roster explícito en metadata → respetar sin inferencia
        profiles = [s for s in roster_override if s in TOOL_SPECIALISTS]
        if profiles:
            return _build_roster(
                specialists=profiles,
                criticality=criticality,
                reasoning="explicit_roster_from_metadata",
            )

    normalized_caps = set(normalize_tool_capabilities(required_capabilities))
    norm_lsp = normalize_lsp_targets(lsp_targets)
    norm_skills = normalize_skill_targets(skill_targets)
    is_high_complexity = complexity.value in ("high",)
    is_high_criticality = criticality.value in ("high",)
    has_mcp = bool(available_mcp_servers)
    suppress_mcp = bool(meta.get("tool_rewiring_suppress_mcp_operator", False))
    replacement_specialists = replacement_specialists_from_metadata(meta)
    context_curator_priority = (
        bool(meta.get("context_compaction_priority_boost", False))
        or int(meta.get("estimated_context_tokens_saved", 0) or 0) >= 300
        or str(meta.get("context_compaction_value_level", "") or "").strip().lower()
        in {"medium", "high"}
    )

    selected: list[str] = []
    reasons: list[str] = []

    # ── 1. Especialista base por capabilities / role ───────────────────────
    base = infer_tool_specialist(
        role=role,
        required_capabilities=required_capabilities,
        metadata=metadata,
    )
    # Suprimir mcp_operator del base si available_mcp_servers está explícitamente vacío
    if base == "mcp_operator" and available_mcp_servers is not None and not has_mcp:
        base = ""
    if base == "mcp_operator" and suppress_mcp and replacement_specialists:
        base = replacement_specialists[0]
        reasons.append("mcp_rewired_to_replacement_specialist")
    if base:
        selected.append(base)
        reasons.append(f"base:{base}")

    # ── 1.b Replacements explícitos desde catalogo/rewiring ───────────────
    for specialist_name in replacement_specialists:
        if specialist_name not in selected:
            selected.append(specialist_name)
            reasons.append(f"replacement:{specialist_name}")

    # ── 2. LSP navigator cuando hay targets semánticos ─────────────────────
    if norm_lsp and "lsp_navigator" not in selected:
        selected.append("lsp_navigator")
        reasons.append("lsp_targets_present")

    # ── 3. Skill worker cuando hay skill_targets ───────────────────────────
    if norm_skills and "skill_worker" not in selected:
        selected.append("skill_worker")
        reasons.append("skill_targets_present")

    if bool(meta.get("context_curator_requested")) and "context_curator" not in selected:
        selected.append("context_curator")
        reasons.append("context_curator_requested")
    elif (
        role != Role.SCOUT
        and (
            bool(meta.get("context_curator_recommended"))
            or bool(meta.get("context_pressure_high"))
        )
        and "context_curator" not in selected
    ):
        selected.append("context_curator")
        pressure_level = str(meta.get("context_pressure_level", "") or "").strip().lower()
        reasons.append(
            f"context_pressure_requires_curator:{pressure_level or 'recommended'}"
        )
    elif (
        bool(meta.get("continuation_requested"))
        and role == Role.TEAM_LEAD
        and "context_curator" not in selected
    ):
        selected.append("context_curator")
        reasons.append("continuation_needs_context_curator")

    # ── 4. Repo scout para contexto en tareas complejas de build ──────────
    if (
        is_high_complexity
        and role in (Role.ENGINEER, Role.RESEARCHER)
        and "repo_scout" not in selected
        and not norm_lsp  # lsp_navigator ya cubre contexto semántico
    ):
        selected.append("repo_scout")
        reasons.append("high_complexity_needs_repo_context")

    # ── 5. MCP operator cuando hay servers disponibles y caps lo piden ────
    if (
        has_mcp
        and ("external_mcp" in normalized_caps or meta.get("requires_mcp"))
        and not suppress_mcp
        and "mcp_operator" not in selected
    ):
        selected.append("mcp_operator")
        reasons.append("mcp_servers_available_and_required")

    # ── 6. Browser operator ───────────────────────────────────────────────
    if (
        {"browser_nav", "browser_test"} & normalized_caps
        and "browser_operator" not in selected
    ):
        selected.append("browser_operator")
        reasons.append("browser_capabilities_required")

    # ── 7. Test runner ────────────────────────────────────────────────────
    if (
        {"test_execute", "build_execute"} & normalized_caps
        and "test_runner" not in selected
    ):
        selected.append("test_runner")
        reasons.append("test_or_build_required")

    # ── Truncar a máximo 3 para economía ──────────────────────────────────
    # Prioridad: base > lsp/skill > repo_scout > mcp/browser/test
    if context_curator_priority and "context_curator" in selected:
        selected = _prioritize_specialist(selected, "context_curator")
        reasons.append("context_curator_prioritized_by_compaction_value")
    selected = selected[:3]

    return _build_roster(
        specialists=selected,
        criticality=criticality,
        reasoning="; ".join(reasons) if reasons else "no_specialist_inferred",
    )


def _build_roster(
    *,
    specialists: list[str],
    criticality: Criticality,
    reasoning: str,
) -> SpecialistRoster:
    """Construye el SpecialistRoster con quorum y economics."""
    n = len(specialists)
    is_high_criticality = criticality.value in ("high",)

    if n == 0:
        return SpecialistRoster(
            specialists=[],
            quorum_required=0,
            quorum_mode="any",
            reasoning=reasoning,
            economics={},
        )

    # Quorum: HIGH criticality → todos; 3 specialists → mayoría; resto → any
    if is_high_criticality:
        quorum_required = n
        quorum_mode = "all"
    elif n >= 3:
        quorum_required = 2
        quorum_mode = "majority"
    else:
        quorum_required = 1
        quorum_mode = "any"

    economics = {
        s: TOOL_SPECIALISTS[s].default_tier
        for s in specialists
        if s in TOOL_SPECIALISTS
    }

    return SpecialistRoster(
        specialists=specialists,
        quorum_required=quorum_required,
        quorum_mode=quorum_mode,
        reasoning=reasoning,
        economics=economics,
    )


def specialist_system_prompt_block(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    specialist_name = str(metadata.get("tool_specialist", "") or "").strip().lower()
    profile = specialist_profile(specialist_name)
    if profile is None:
        return ""

    reason = str(metadata.get("tool_specialist_reason", "") or "").strip()
    capabilities = [
        str(item or "").strip()
        for item in list(metadata.get("tool_specialist_preferred_capabilities", []) or [])
        if str(item or "").strip()
    ]
    tool_families = [
        str(item or "").strip()
        for item in list(metadata.get("tool_specialist_tool_families", []) or [])
        if str(item or "").strip()
    ]
    skill_targets = [
        str(item or "").strip()
        for item in list(metadata.get("tool_specialist_skill_targets", []) or [])
        if str(item or "").strip()
    ]
    lsp_targets = [
        str(item or "").strip()
        for item in list(metadata.get("tool_specialist_lsp_targets", []) or [])
        if str(item or "").strip()
    ]
    target_lines = []
    if skill_targets:
        target_lines.append(f"Skills objetivo: {', '.join(skill_targets)}.")
    if lsp_targets:
        target_lines.append(f"Objetivos LSP: {', '.join(lsp_targets)}.")
    targets_block = ""
    if target_lines:
        targets_block = f"{' '.join(target_lines)}\n"
    return (
        f"Especializacion activa: {profile.label} ({profile.name}).\n"
        f"Funcion: {profile.summary}\n"
        f"Familias de herramientas: {', '.join(tool_families) or ', '.join(profile.tool_families)}.\n"
        f"Capacidades preferentes: {', '.join(capabilities) or ', '.join(profile.preferred_capabilities)}.\n"
        f"{targets_block}"
        "Regla operativa: usa herramientas, skills, MCPs o CLIs de forma economica y devuelve un informe compacto.\n"
        "No arbitres producto ni estrategia general; recomienda y reporta evidencia para que el Lead decida.\n"
        "Devuelve preferentemente JSON con este schema: "
        '{"summary":"","evidence":[],"artifacts":[],"risks":[],"recommendation":"","confidence":0.0}.\n'
        "Si no usas JSON, respeta como minimo estas secciones: summary, evidence, artifacts, risks, recommendation.\n"
        f"Motivo de especializacion: {reason or 'tarea con alto componente de tool use.'}"
    )
