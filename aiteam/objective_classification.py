from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = "objective_classification_v1"
SOFTWARE = "software"
RESEARCH = "research"
OPERATIONS = "operations"
# Alias de código para call sites que aplican la misma frontera a research.
# El valor durable es siempre "research"; "non_code" solo se acepta como input.
NON_CODE = RESEARCH
MIXED = "mixed"
OBJECTIVE_KINDS = {SOFTWARE, RESEARCH, OPERATIONS, MIXED}
NON_PROGRAMMING_KINDS = frozenset({RESEARCH, OPERATIONS})
PROGRAMMING_ROLES = frozenset(
    {
        "engineer",
        "software_engineer",
        "lead_executor",
        "test_designer",
        "test_runner",
        "qa",
        "qa_engineer",
    }
)

_NON_CODE_PHRASES = (
    "analisis de necesidades",
    "estudio de empresa",
    "estudio empresarial",
    "estudio de mercado",
    "investigacion de mercado",
    "plan de negocio",
    "revision bibliografica",
    "informe de viabilidad",
    "business study",
    "business analysis",
    "market research",
    "needs assessment",
    "policy memo",
    "literature review",
)
_NON_CODE_TERMS = frozenset(
    {
        "encuesta",
        "encuestas",
        "cuestionario",
        "cuestionarios",
        "entrevista",
        "entrevistas",
        "formulario",
        "formularios",
        "informe",
        "informes",
        "investigacion",
        "mercado",
        "necesidades",
        "diagnostico",
        "benchmark",
    }
)
_SOFTWARE_PHRASES = (
    "aplicacion web",
    "aplicacion movil",
    "base de datos",
    "codigo fuente",
    "command line",
    "web app",
    "mobile app",
    "source code",
)
_SOFTWARE_TERMS = frozenset(
    {
        "api",
        "backend",
        "frontend",
        "cli",
        "codigo",
        "programar",
        "software",
        "endpoint",
        "docker",
        "typescript",
        "javascript",
        "python",
        "react",
        "sqlite",
        "automatizar",
        "implementa",
        "implementar",
    }
)
_OPERATIONS_PHRASES = (
    "procedimiento operativo",
    "manual operativo",
    "plan de operaciones",
    "proceso de trabajo",
    "operating procedure",
    "operations manual",
    "incident runbook",
)
_OPERATIONS_TERMS = frozenset(
    {
        "runbook",
        "procedimiento",
        "procedimientos",
        "operaciones",
        "operativo",
        "operativa",
        "checklist",
        "protocolo",
        "protocolos",
    }
)


@dataclass(frozen=True)
class ObjectiveClassification:
    kind: str
    source: str
    reasons: tuple[str, ...]

    def to_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "source": self.source,
            "reasons": list(self.reasons),
        }


def classify_objective(
    title: str,
    description: str = "",
    *,
    explicit_kind: str | None = None,
) -> ObjectiveClassification:
    requested = str(explicit_kind or "").strip().lower()
    if requested == "non_code":
        requested = RESEARCH
    if requested and requested != "auto":
        if requested not in OBJECTIVE_KINDS:
            raise ValueError(f"unknown objective kind: {explicit_kind}")
        return ObjectiveClassification(
            kind=requested,
            source="owner_explicit",
            reasons=("owner_selected_kind",),
        )

    text = _normalize(f"{title}\n{description}")
    words = set(re.findall(r"[a-z0-9_+#.-]+", text))
    non_reasons = [phrase for phrase in _NON_CODE_PHRASES if phrase in text]
    non_reasons.extend(sorted(words & _NON_CODE_TERMS))
    software_reasons = [phrase for phrase in _SOFTWARE_PHRASES if phrase in text]
    software_reasons.extend(sorted(words & _SOFTWARE_TERMS))
    operations_reasons = [phrase for phrase in _OPERATIONS_PHRASES if phrase in text]
    operations_reasons.extend(sorted(words & _OPERATIONS_TERMS))
    non_score = sum(3 if " " in item else 1 for item in non_reasons)
    software_score = sum(3 if " " in item else 1 for item in software_reasons)
    operations_score = sum(
        3 if " " in item else 1 for item in operations_reasons
    )

    if software_score >= 2 and (non_score >= 2 or operations_score >= 2):
        kind = MIXED
        reasons = tuple(
            [f"non_code:{item}" for item in non_reasons[:4]]
            + [f"operations:{item}" for item in operations_reasons[:4]]
            + [f"software:{item}" for item in software_reasons[:4]]
        )
    elif operations_score >= 2 and software_score == 0 and operations_score > non_score:
        kind = OPERATIONS
        reasons = tuple(f"operations:{item}" for item in operations_reasons[:6])
    elif non_score >= 2 and software_score == 0:
        kind = RESEARCH
        reasons = tuple(f"non_code:{item}" for item in non_reasons[:6])
    else:
        # Ambiguity remains on the existing software path. This avoids silently
        # removing engineering from a build request with weak wording.
        kind = SOFTWARE
        reasons = tuple(f"software:{item}" for item in software_reasons[:6]) or (
            "ambiguous_safe_software_default",
        )
    return ObjectiveClassification(
        kind=kind,
        source="deterministic_signals",
        reasons=reasons,
    )


def classification_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> ObjectiveClassification | None:
    raw = (metadata or {}).get("objective_classification")
    if not isinstance(raw, Mapping):
        return None
    if raw.get("schema_version") != SCHEMA_VERSION:
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    source = str(raw.get("source") or "").strip()
    reasons = tuple(str(item) for item in (raw.get("reasons") or ()) if str(item))
    if kind not in OBJECTIVE_KINDS or source not in {
        "owner_explicit",
        "deterministic_signals",
        "inherited",
    }:
        return None
    return ObjectiveClassification(kind=kind, source=source, reasons=reasons)


def objective_kind_from_issue(issue: Mapping[str, Any]) -> str:
    metadata = issue.get("metadata")
    if not isinstance(metadata, Mapping):
        import json

        try:
            metadata = json.loads(str(issue.get("metadata_json") or "{}"))
        except (TypeError, ValueError):
            metadata = {}
    persisted = classification_from_metadata(metadata)
    if persisted is not None:
        return persisted.kind
    return classify_objective(
        str(issue.get("title") or ""),
        str(issue.get("description") or ""),
    ).kind


def inherited_classification(kind: str) -> dict[str, Any]:
    if kind not in OBJECTIVE_KINDS:
        raise ValueError(f"unknown objective kind: {kind}")
    return ObjectiveClassification(
        kind=kind,
        source="inherited",
        reasons=("inherited_from_parent_issue",),
    ).to_metadata()


def objective_contract(kind: str) -> dict[str, Any]:
    if kind not in OBJECTIVE_KINDS:
        raise ValueError(f"unknown objective kind: {kind}")
    if kind in NON_PROGRAMMING_KINDS:
        return {
            "schema_version": "objective_execution_contract_v1",
            "kind": kind,
            "programming_roles_allowed": False,
            "tests_required": False,
            "acceptance_evidence": (
                [
                    "source_coverage_and_dates",
                    "questions_mapped_to_decisions",
                    "assumptions_and_calculations",
                    "decision_ready_document",
                ]
                if kind == RESEARCH
                else [
                    "procedure_steps_and_owner",
                    "inputs_outputs_and_controls",
                    "risks_and_escalation",
                    "operationally_usable_document",
                ]
            ),
        }
    if kind == MIXED:
        return {
            "schema_version": "objective_execution_contract_v1",
            "kind": kind,
            "programming_roles_allowed": "software_subissues_only",
            "tests_required": "software_subissues_only",
            "acceptance_evidence": [
                "documentary_evidence_for_non_code_subissues",
                "runtime_evidence_for_software_subissues",
            ],
        }
    return {
        "schema_version": "objective_execution_contract_v1",
        "kind": kind,
        "programming_roles_allowed": True,
        "tests_required": "when_test_signals_exist",
        "acceptance_evidence": ["implementation_receipt", "proportional_quality_gates"],
    }


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))
