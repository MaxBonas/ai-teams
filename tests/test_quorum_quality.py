from __future__ import annotations

import json

from aiteam.quorum_quality import (
    QUORUM_AUDIT_MARKER,
    evaluate_plan_depth,
    parse_quorum_audit,
    validate_quorum_audit,
)


def _deep_plan() -> str:
    headings = (
        "Objetivo y alcance Estado actual y contexto Supuestos y restricciones "
        "Arquitectura y enfoque Fases dependencias owners Riesgos rollback "
        "Verificación evidencia criterios de aceptación Preguntas abiertas escalado "
        "Continuación siguiente run"
    )
    return headings + " " + "detalle causal verificable " * 310


def _audit() -> dict:
    return {
        "executive_assessment": "Evaluación senior suficientemente profunda sobre el enfoque y sus consecuencias.",
        "strengths": ["La separación de fases debe preservarse."],
        "assumptions_challenged": ["La disponibilidad del proveedor no está demostrada."],
        "findings": [{
            "id": "availability-gap",
            "severity": "high",
            "summary": "La recuperación depende de una disponibilidad que todavía no está demostrada.",
            "reasoning": "Si el proveedor falla durante el corte, la transición queda sin owner ni continuación.",
            "justification": "La ruta durable exige evidencia anterior a cualquier transición irreversible.",
            "recommendation": "Añadir un canario y un gate explícito antes de activar el cambio global.",
            "tradeoffs": "Añade latencia y coste operativo, pero reduce el riesgo de pérdida de servicio.",
        }],
    }


def test_deep_plan_contract_requires_all_dimensions_and_budget() -> None:
    assert evaluate_plan_depth(_deep_plan())["valid"] is True
    shallow = evaluate_plan_depth("Objetivo, fases y riesgos")
    assert shallow["valid"] is False
    assert "current_state" in shallow["missing_dimensions"]


def test_quorum_audit_roundtrip_preserves_structured_findings() -> None:
    body = f"Informe\n{QUORUM_AUDIT_MARKER}\n{json.dumps(_audit())}\n---AGENT-REPORT---"
    validated = validate_quorum_audit(parse_quorum_audit(body))
    assert validated["valid"] is True
    assert validated["findings"][0]["id"] == "availability-gap"


def test_quorum_audit_rejects_shallow_argumentation() -> None:
    audit = _audit()
    audit["findings"][0]["reasoning"] = "porque sí"
    validated = validate_quorum_audit(audit)
    assert validated["valid"] is False
    assert "finding_1_reasoning_too_shallow" in validated["errors"]
