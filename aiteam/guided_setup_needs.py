"""Entrevista adaptativa y determinista del asistente guiado."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from aiteam.objective_classification import classify_objective

SCHEMA_VERSION = "guided_setup_needs_v1"
SCOPES = frozenset({"machine_onboarding", "project_setup"})
_ENUMS = {
    "objective_kind": ("software", "research", "operations", "mixed", "unknown"),
    "data_sensitivity": ("public", "internal", "confidential", "restricted", "unknown"),
    "budget_priority": ("zero_cost", "prefer_free", "balanced", "quality_first", "unknown"),
    "api_access": ("existing", "willing", "not_willing", "unknown"),
    "local_models": ("available", "willing", "not_wanted", "unknown"),
    "autonomy": ("supervised", "balanced", "autonomous", "unknown"),
    "criticality": ("low", "medium", "high", "critical", "unknown"),
    "team_preference": ("solo_lead", "lead_quorum", "full_team", "unknown"),
    "external_tools": ("none", "optional", "required", "unknown"),
}
_SUBSCRIPTIONS = frozenset(
    {"codex", "antigravity", "claude", "none", "other", "unknown"}
)
_LABELS = {
    "software": "Software",
    "research": "Investigación o estudio",
    "operations": "Operaciones o procedimientos",
    "mixed": "Mixto",
    "unknown": "No lo sé todavía",
    "public": "Públicos",
    "internal": "Internos",
    "confidential": "Confidenciales",
    "restricted": "Restringidos",
    "zero_cost": "Solo opciones sin coste",
    "prefer_free": "Priorizar opciones gratuitas",
    "balanced": "Equilibrio",
    "quality_first": "Máxima calidad",
    "existing": "Ya tengo claves",
    "willing": "Puedo configurarlo",
    "not_willing": "No quiero usarlo",
    "available": "Ya tengo modelos locales",
    "not_wanted": "No quiero modelos locales",
    "supervised": "Supervisado",
    "autonomous": "Más autónomo",
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Crítica",
    "solo_lead": "Solo Lead",
    "lead_quorum": "Lead + quorum",
    "full_team": "Equipo completo",
    "none": "Ninguna",
    "optional": "Opcionales",
    "required": "Necesarias",
    "codex": "Codex / ChatGPT",
    "antigravity": "Antigravity / Google",
    "claude": "Claude",
    "other": "Otra",
}
_QUESTIONS = (
    {
        "id": "goal",
        "kind": "long_text",
        "prompt": "¿Qué quieres conseguir principalmente?",
        "help": "Una frase concreta permite adaptar proyecto, equipo y pruebas.",
        "required": True,
    },
    {
        "id": "objective_kind",
        "kind": "single_choice",
        "prompt": "¿Qué tipo de resultado esperas?",
        "help": "Evita aplicar tests de software a estudios o procedimientos.",
        "required": True,
    },
    {
        "id": "languages",
        "kind": "tag_list",
        "prompt": "¿Qué lenguajes o stacks usarás?",
        "help": "Puedes indicar unknown si todavía no lo sabes.",
        "required": True,
        "visible_for": ("software", "mixed", "unknown"),
    },
    {
        "id": "data_sensitivity",
        "kind": "single_choice",
        "prompt": "¿Qué sensibilidad tienen los datos?",
        "help": "Filtra canales y herramientas antes de enviar contenido.",
        "required": True,
    },
    {
        "id": "budget_priority",
        "kind": "single_choice",
        "prompt": "¿Qué priorizas en coste y cuota?",
        "help": "Local, suscripción y API tienen economías diferentes.",
        "required": True,
    },
    {
        "id": "subscriptions",
        "kind": "multi_choice",
        "prompt": "¿Qué suscripciones ya tienes disponibles?",
        "help": "Solo se sugerirán logins de servicios que quieras usar.",
        "required": True,
    },
    {
        "id": "api_access",
        "kind": "single_choice",
        "prompt": "¿Quieres usar APIs con claves personales?",
        "help": "AI Teams guarda referencias; nunca muestra la clave.",
        "required": True,
    },
    {
        "id": "local_models",
        "kind": "single_choice",
        "prompt": "¿Quieres usar modelos locales?",
        "help": "Ollama y LM Studio son opcionales, nunca requisitos.",
        "required": True,
    },
    {
        "id": "autonomy",
        "kind": "single_choice",
        "prompt": "¿Cuánta autonomía quieres conceder?",
        "help": "Podrás cambiarla después por proyecto.",
        "required": True,
    },
    {
        "id": "criticality",
        "kind": "single_choice",
        "prompt": "¿Qué criticidad tiene el trabajo?",
        "help": "La criticidad gobierna revisión, quorum y confirmaciones.",
        "required": True,
    },
    {
        "id": "team_preference",
        "kind": "single_choice",
        "prompt": "¿Qué tamaño de equipo prefieres?",
        "help": "La recomendación final puede degradar si falta cobertura real.",
        "required": True,
    },
    {
        "id": "external_tools",
        "kind": "single_choice",
        "prompt": "¿Necesitarás herramientas externas o MCP?",
        "help": "Solo adapters con transporte gobernado podrán cubrirlas.",
        "required": True,
    },
)


def needs_questionnaire(
    scope: str,
    answers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    clean_scope = _scope(scope)
    current = dict(answers or {})
    objective = str(current.get("objective_kind") or "unknown")
    questions = []
    for raw in _QUESTIONS:
        visible_for = raw.get("visible_for")
        visible = visible_for is None or objective in visible_for
        question = {
            key: value
            for key, value in raw.items()
            if key != "visible_for"
        }
        question["visible"] = visible
        if raw["id"] in _ENUMS:
            question["options"] = [
                {"value": value, "label": _LABELS[value]}
                for value in _ENUMS[raw["id"]]
            ]
        elif raw["id"] == "subscriptions":
            question["options"] = [
                {"value": value, "label": _LABELS[value]}
                for value in ("codex", "antigravity", "claude", "none", "other", "unknown")
            ]
        recommended, reason = _recommend_answer(raw["id"], current)
        question["recommended"] = recommended
        question["recommendation_reason"] = reason
        questions.append(question)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": clean_scope,
        "questions": questions,
    }


def build_needs_submission(
    scope: str,
    answers: Mapping[str, Any],
) -> dict[str, Any]:
    clean_scope = _scope(scope)
    clean_answers = _normalize_answers(answers)
    contract = needs_questionnaire(clean_scope, clean_answers)
    visible = {
        row["id"]
        for row in contract["questions"]
        if row["visible"] is True
    }
    required = {
        row["id"]
        for row in contract["questions"]
        if row["visible"] is True and row["required"] is True
    }
    missing = sorted(
        question_id
        for question_id in required
        if not _answered(question_id, clean_answers.get(question_id))
    )
    unknowns = sorted(
        key
        for key, value in clean_answers.items()
        if key in visible
        and (
            value == "unknown"
            or isinstance(value, list)
            and "unknown" in value
        )
    )
    objective = _objective_recommendation(clean_answers)
    run_profile, profile_reasons = _run_profile(clean_answers)
    channels, channel_reasons = _channel_strategy(clean_answers)
    assessment = {
        "complete": not missing,
        "missing_required": missing,
        "unknown_answers": unknowns,
        "objective": objective,
        "recommended_run_profile": run_profile,
        "run_profile_reasons": profile_reasons,
        "channel_strategy": channels,
        "channel_reasons": channel_reasons,
        "warnings": _warnings(clean_answers, unknowns),
        "next_action": "review_recommendations" if not missing else "complete_required_answers",
    }
    sealed = {
        "schema_version": SCHEMA_VERSION,
        "scope": clean_scope,
        "answers": clean_answers,
        "assessment": assessment,
    }
    sealed["assessment_hash"] = _hash(sealed)
    return sealed


def validate_needs_submission(
    value: Mapping[str, Any],
    *,
    scope: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("guided_setup_needs_submission_required")
    rebuilt = build_needs_submission(scope, value.get("answers") or {})
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("guided_setup_needs_schema_mismatch")
    if value.get("scope") != scope:
        raise ValueError("guided_setup_needs_scope_mismatch")
    if value.get("assessment_hash") != rebuilt["assessment_hash"]:
        raise ValueError("guided_setup_needs_hash_mismatch")
    if value.get("assessment") != rebuilt["assessment"]:
        raise ValueError("guided_setup_needs_assessment_mismatch")
    if rebuilt["assessment"]["complete"] is not True:
        raise ValueError("guided_setup_needs_incomplete")
    return rebuilt


def _normalize_answers(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {row["id"] for row in _QUESTIONS}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("guided_setup_needs_unknown_answer")
    result: dict[str, Any] = {}
    if "goal" in value:
        goal = str(value.get("goal") or "").strip()
        if goal and not 3 <= len(goal) <= 2000:
            raise ValueError("guided_setup_needs_goal_invalid")
        result["goal"] = goal
    for key, options in _ENUMS.items():
        if key not in value:
            continue
        answer = str(value.get(key) or "").strip()
        if answer not in options:
            raise ValueError(f"guided_setup_needs_{key}_invalid")
        result[key] = answer
    if "subscriptions" in value:
        raw = value.get("subscriptions")
        if not isinstance(raw, list):
            raise ValueError("guided_setup_needs_subscriptions_invalid")
        subscriptions = sorted({str(item).strip() for item in raw})
        if not subscriptions or not set(subscriptions) <= _SUBSCRIPTIONS:
            raise ValueError("guided_setup_needs_subscriptions_invalid")
        if "none" in subscriptions and len(subscriptions) > 1:
            raise ValueError("guided_setup_needs_subscriptions_conflict")
        result["subscriptions"] = subscriptions
    if "languages" in value:
        raw = value.get("languages")
        if not isinstance(raw, list):
            raise ValueError("guided_setup_needs_languages_invalid")
        languages = sorted({str(item).strip() for item in raw if str(item).strip()})
        if (
            not languages
            or len(languages) > 20
            or any(not re.fullmatch(r"[A-Za-z0-9_+#.\-/]{1,40}", item) for item in languages)
            or "unknown" in languages
            and len(languages) > 1
        ):
            raise ValueError("guided_setup_needs_languages_invalid")
        result["languages"] = languages
    return result


def _objective_recommendation(answers: Mapping[str, Any]) -> dict[str, Any]:
    explicit = str(answers.get("objective_kind") or "unknown")
    goal = str(answers.get("goal") or "")
    if explicit != "unknown":
        return {
            "kind": explicit,
            "source": "owner_explicit",
            "requires_confirmation": False,
            "reasons": ["owner_selected_kind"],
        }
    classified = classify_objective(goal)
    return {
        "kind": classified.kind,
        "source": "deterministic_suggestion",
        "requires_confirmation": True,
        "reasons": list(classified.reasons) or ["insufficient_signal"],
    }


def _run_profile(answers: Mapping[str, Any]) -> tuple[str, list[str]]:
    preferred = str(answers.get("team_preference") or "unknown")
    criticality = str(answers.get("criticality") or "unknown")
    if preferred == "full_team":
        return "full_team", ["owner_prefers_full_team"]
    if preferred == "lead_quorum" or criticality in {"high", "critical"}:
        return "lead_quorum", [
            "owner_prefers_quorum"
            if preferred == "lead_quorum"
            else "high_criticality_recommends_quorum"
        ]
    return "solo_lead", ["minimum_lead_capable_start"]


def _channel_strategy(
    answers: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    rows = [{"kind": "lead_capable", "priority": "required"}]
    reasons = ["at_least_one_lead_capable_channel"]
    subscriptions = set(answers.get("subscriptions") or ())
    if subscriptions and not subscriptions <= {"none", "unknown"}:
        rows.append({"kind": "subscription", "priority": "recommended"})
        reasons.append("owner_has_subscription")
    if answers.get("api_access") in {"existing", "willing"}:
        rows.append({"kind": "api", "priority": "recommended"})
        reasons.append("owner_accepts_api")
    if answers.get("local_models") in {"available", "willing"}:
        rows.append({"kind": "local", "priority": "optional"})
        reasons.append("owner_opted_into_local")
    if answers.get("budget_priority") in {"zero_cost", "prefer_free"}:
        rows.append({"kind": "free_economy", "priority": "recommended"})
        reasons.append("owner_prioritizes_free_capacity")
    return rows, reasons


def _warnings(answers: Mapping[str, Any], unknowns: list[str]) -> list[str]:
    warnings = [f"unknown:{item}" for item in unknowns]
    if answers.get("data_sensitivity") in {"confidential", "restricted"}:
        warnings.append("sensitive_data_requires_private_compatible_channels")
    if answers.get("external_tools") == "required":
        warnings.append("external_mcp_requires_governed_transport")
    if answers.get("api_access") == "not_willing" and set(
        answers.get("subscriptions") or ()
    ) <= {"none", "unknown"}:
        warnings.append("no_lead_channel_declared_yet")
    return warnings


def _recommend_answer(
    question_id: str,
    answers: Mapping[str, Any],
) -> tuple[Any, str]:
    defaults = {
        "objective_kind": ("unknown", "confirmar_tipo_antes_de_crear_equipo"),
        "languages": (["unknown"], "detectar_stack_si_existe_proyecto"),
        "data_sensitivity": ("unknown", "no_asumir_clasificacion_de_datos"),
        "budget_priority": ("balanced", "equilibrar_calidad_coste_y_cuota"),
        "subscriptions": (["unknown"], "declarar_solo_cuentas_disponibles"),
        "api_access": ("unknown", "no_pedir_claves_sin_consentimiento"),
        "local_models": ("not_wanted", "runtime_local_es_opcional"),
        "autonomy": ("supervised", "empezar_con_control_explicito"),
        "criticality": ("medium", "gates_proporcionales_por_defecto"),
        "team_preference": ("solo_lead", "ruta_minima_lead_capable"),
        "external_tools": ("optional", "habilitar_solo_si_el_proyecto_lo_necesita"),
        "goal": ("", "describir_resultado_en_lenguaje_natural"),
    }
    return defaults[question_id]


def _answered(question_id: str, value: Any) -> bool:
    if question_id in {"languages", "subscriptions"}:
        return isinstance(value, list) and bool(value)
    return bool(str(value or "").strip())


def _scope(value: str) -> str:
    clean = str(value or "").strip()
    if clean not in SCOPES:
        raise ValueError("guided_setup_needs_scope_not_allowed")
    return clean


def _hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
