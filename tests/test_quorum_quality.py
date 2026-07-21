from __future__ import annotations

import json

from aiteam.quorum_quality import (
    QUORUM_AUDIT_MARKER,
    evaluate_plan_depth,
    parse_quorum_audit,
    plan_contract_instruction,
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


def test_final_plan_instruction_names_the_field_enforced_by_depth_gate() -> None:
    instruction = plan_contract_instruction(final=True)
    assert "plan.narrative_markdown" in instruction
    assert "al menos 300 palabras" in instruction


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


# ── parse_quorum_audit: bordes de formato ────────────────────────────────────

def test_parse_returns_none_without_marker_or_with_broken_json() -> None:
    assert parse_quorum_audit("informe sin bloque estructurado") is None
    assert parse_quorum_audit(f"{QUORUM_AUDIT_MARKER}\n{{no es json") is None
    # Un array JSON no es un informe válido (se exige objeto).
    assert parse_quorum_audit(f"{QUORUM_AUDIT_MARKER}\n[1, 2]") is None


def test_parse_tolerates_trailing_text_and_uses_last_marker() -> None:
    # raw_decode debe ignorar el texto posterior (p.ej. el AGENT-REPORT).
    body = f"{QUORUM_AUDIT_MARKER}\n{json.dumps(_audit())}\n---AGENT-REPORT---\nresult: done"
    assert parse_quorum_audit(body) is not None
    # Con dos markers gana el último: el modelo puede citar el contrato en prosa.
    quoted = (
        f"El contrato pide {QUORUM_AUDIT_MARKER} en el informe.\n"
        f"{QUORUM_AUDIT_MARKER}\n{json.dumps(_audit())}"
    )
    parsed = parse_quorum_audit(quoted)
    assert parsed is not None and parsed["findings"][0]["id"] == "availability-gap"


# ── validate_quorum_audit: contratos de estructura ───────────────────────────

def test_validate_rejects_missing_audit_and_empty_sections() -> None:
    assert validate_quorum_audit(None) == {
        "valid": False, "errors": ["missing_quorum_audit"], "findings": [],
    }
    audit = _audit()
    audit["strengths"] = ["   "]
    audit["executive_assessment"] = "corto"
    validated = validate_quorum_audit(audit)
    assert validated["valid"] is False
    assert "strengths_required" in validated["errors"]
    assert "executive_assessment_too_shallow" in validated["errors"]


def test_validate_rejects_duplicate_finding_ids_and_bad_severity() -> None:
    audit = _audit()
    duplicate = dict(audit["findings"][0])
    duplicate["severity"] = "catastrophic"
    audit["findings"] = [audit["findings"][0], duplicate]
    validated = validate_quorum_audit(audit)
    assert validated["valid"] is False
    assert "finding_2_id_invalid" in validated["errors"]
    assert "finding_2_severity_invalid" in validated["errors"]


def test_validate_requires_findings_list_with_dict_items() -> None:
    audit = _audit()
    audit["findings"] = []
    assert "findings_required" in validate_quorum_audit(audit)["errors"]
    audit["findings"] = ["no soy un dict"]
    assert "finding_1_invalid" in validate_quorum_audit(audit)["errors"]


# ── evaluate_plan_depth: bordes del presupuesto y cobertura ──────────────────

def test_plan_depth_word_budget_boundary() -> None:
    headings = _deep_plan().split(" detalle")[0]
    exactly_at_budget = headings + " palabra" * (300 - len(headings.split()))
    assert evaluate_plan_depth(exactly_at_budget)["valid"] is True
    below = evaluate_plan_depth(headings)
    assert below["valid"] is False
    assert below["word_count"] < below["min_words"]
    assert below["missing_dimensions"] == []


def test_plan_depth_accepts_english_section_vocabulary() -> None:
    english = (
        "Objective and scope. Current state baseline. Assumptions and constraints. "
        "Architecture approach. Phases dependencies owner. Risk rollback. "
        "Verification evidence. Open questions escalation. Next run handoff. "
    ) + "actionable causal detail " * 310
    assert evaluate_plan_depth(english)["valid"] is True


def test_plan_depth_empty_body_reports_all_dimensions_missing() -> None:
    result = evaluate_plan_depth("")
    assert result["valid"] is False
    assert result["word_count"] == 0
    assert len(result["missing_dimensions"]) == 9
