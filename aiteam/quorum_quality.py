"""Contratos deterministas de profundidad para planificación ``lead_quorum``."""

from __future__ import annotations

import json
import re
from typing import Any


QUORUM_AUDIT_MARKER = "---QUORUM-AUDIT---"

PLAN_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "objective_scope": (r"objetiv", r"objective", r"alcance", r"scope"),
    "current_state": (r"estado actual", r"current state", r"contexto", r"baseline"),
    "assumptions_constraints": (r"supuest", r"assumption", r"restric", r"constraint"),
    "architecture_approach": (r"arquitect", r"architecture", r"enfoque", r"approach"),
    "phases_dependencies_owners": (r"fase", r"phase", r"dependenc", r"owner", r"responsable"),
    "risks_rollback": (r"riesgo", r"risk", r"rollback", r"revers"),
    "verification_evidence": (r"verific", r"evidencia", r"evidence", r"criteri.{0,20}acept"),
    "open_questions_escalation": (r"pregunta", r"open question", r"escal", r"bloque"),
    "continuation": (r"siguiente run", r"continuaci", r"next run", r"wakeup", r"handoff"),
}


def evaluate_plan_depth(body: str, *, min_words: int = 300) -> dict[str, Any]:
    """Evalúa cobertura estructural; no intenta juzgar la calidad abierta del plan."""
    text = str(body or "").strip()
    normalized = text.lower()
    word_count = len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))
    matched = {
        dimension: any(re.search(pattern, normalized, flags=re.DOTALL) for pattern in patterns)
        for dimension, patterns in PLAN_DIMENSIONS.items()
    }
    missing = [dimension for dimension, present in matched.items() if not present]
    return {
        "valid": word_count >= min_words and not missing,
        "word_count": word_count,
        "min_words": min_words,
        "dimensions": matched,
        "missing_dimensions": missing,
    }


def plan_contract_instruction(*, final: bool = False) -> str:
    label = "Plan B final" if final else "Plan A inicial"
    return (
        f"El {label} debe ser profundo, accionable y autosuficiente (mínimo 300 palabras) e incluir "
        "secciones explícitas para: objetivo y alcance; estado actual/contexto; supuestos y restricciones; "
        "arquitectura o enfoque con alternativas descartadas; fases, dependencias y owners; riesgos, modos "
        "de fallo y rollback; criterios de aceptación, verificación y evidencia; preguntas abiertas, bloqueos "
        "y escalado; y continuación concreta de la siguiente run."
    )


def quorum_audit_contract_instruction() -> str:
    return (
        "Entrega una auditoría senior profunda dirigida al Lead real del proyecto. Reconoce qué debe "
        "preservarse, cuestiona supuestos y argumenta cada cambio con consecuencias y trade-offs. Antes del "
        "AGENT-REPORT incluye exactamente este bloque JSON:\n"
        f"{QUORUM_AUDIT_MARKER}\n"
        '{"executive_assessment":"evaluación razonada",'
        '"strengths":["decisión correcta que debe preservarse"],'
        '"assumptions_challenged":["supuesto y por qué importa"],'
        '"findings":[{"id":"estable-y-unico","severity":"high|medium|low",'
        '"summary":"hallazgo concreto","reasoning":"cadena causal y consecuencias",'
        '"justification":"evidencia o principio aplicable","recommendation":"cambio accionable",'
        '"tradeoffs":"costes y alternativas"}]}\n'
        "No dialogues con otros auditores ni sintetices el plan: entregas tu informe al Lead."
    )


def parse_quorum_audit(body: str) -> dict[str, Any] | None:
    text = str(body or "")
    marker = text.rfind(QUORUM_AUDIT_MARKER)
    if marker < 0:
        return None
    tail = text[marker + len(QUORUM_AUDIT_MARKER):].lstrip()
    try:
        parsed, _ = json.JSONDecoder().raw_decode(tail)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_quorum_audit(audit: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(audit, dict):
        return {"valid": False, "errors": ["missing_quorum_audit"], "findings": []}
    executive = str(audit.get("executive_assessment") or "").strip()
    strengths = audit.get("strengths")
    findings = audit.get("findings")
    if len(executive) < 40:
        errors.append("executive_assessment_too_shallow")
    if not isinstance(strengths, list) or not any(str(item).strip() for item in strengths):
        errors.append("strengths_required")
    clean_findings: list[dict[str, Any]] = []
    if not isinstance(findings, list) or not findings:
        errors.append("findings_required")
    else:
        seen: set[str] = set()
        for index, item in enumerate(findings, start=1):
            if not isinstance(item, dict):
                errors.append(f"finding_{index}_invalid")
                continue
            finding_id = str(item.get("id") or "").strip()
            severity = str(item.get("severity") or "").strip().lower()
            required = {
                key: str(item.get(key) or "").strip()
                for key in ("summary", "reasoning", "justification", "recommendation", "tradeoffs")
            }
            if not finding_id or finding_id in seen:
                errors.append(f"finding_{index}_id_invalid")
            seen.add(finding_id)
            if severity not in {"high", "medium", "low"}:
                errors.append(f"finding_{index}_severity_invalid")
            for key, value in required.items():
                if len(value) < 20:
                    errors.append(f"finding_{index}_{key}_too_shallow")
            clean_findings.append({"id": finding_id, "severity": severity, **required})
    return {"valid": not errors, "errors": errors, "findings": clean_findings}
