"""Contrato durable y neutral de proveedor para planes de proyecto."""

from __future__ import annotations

import json
from typing import Any


PLAN_FORMAT = "aiteam.plan.v1+json"
PLAN_SCHEMA_VERSION = 1


def validate_plan_contract(plan: Any) -> dict[str, Any]:
    """Valida estructura y accountability, sin juzgar la prosa del proveedor."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return {"valid": False, "errors": ["plan_must_be_object"]}

    if int(plan.get("schema_version") or 0) != PLAN_SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    for field in ("objective", "architecture"):
        if not str(plan.get(field) or "").strip():
            errors.append(f"{field}_required")
    for field in ("scope", "assumptions", "escalation_conditions", "next_run_risks"):
        if not isinstance(plan.get(field), list):
            errors.append(f"{field}_must_be_list")

    work_items = plan.get("work_items")
    if not isinstance(work_items, list) or not work_items:
        errors.append("work_items_required")
    else:
        for index, item in enumerate(work_items, start=1):
            if not isinstance(item, dict):
                errors.append(f"work_item_{index}_invalid")
                continue
            for field in ("id", "title", "owner_role", "reports_to", "deliverable", "accepted_by"):
                if not str(item.get(field) or "").strip():
                    errors.append(f"work_item_{index}_{field}_required")
            if not isinstance(item.get("evidence"), list) or not item.get("evidence"):
                errors.append(f"work_item_{index}_evidence_required")
            if not isinstance(item.get("dependencies"), list):
                errors.append(f"work_item_{index}_dependencies_must_be_list")

    for collection, required in (
        ("risks", ("risk", "mitigation", "rollback")),
        ("verification", ("criterion", "evidence", "owner_role")),
    ):
        values = plan.get(collection)
        if not isinstance(values, list) or not values:
            errors.append(f"{collection}_required")
            continue
        for index, item in enumerate(values, start=1):
            if not isinstance(item, dict):
                errors.append(f"{collection}_{index}_invalid")
                continue
            for field in required:
                if not str(item.get(field) or "").strip():
                    errors.append(f"{collection}_{index}_{field}_required")

    return {"valid": not errors, "errors": errors}


def encode_plan_contract(plan: dict[str, Any]) -> str:
    result = validate_plan_contract(plan)
    if not result["valid"]:
        raise ValueError("invalid plan contract: " + ", ".join(result["errors"]))
    normalized = dict(plan)
    normalized["schema_version"] = PLAN_SCHEMA_VERSION
    normalized["narrative_markdown"] = str(normalized.get("narrative_markdown") or "")
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def present_plan_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    """Añade la vista estructurada sin alterar el recibo de revisión almacenado."""
    if document is None:
        return None
    out = dict(document)
    if str(out.get("format") or "") != PLAN_FORMAT:
        out["plan"] = None
        out["contract_validation"] = {"valid": False, "errors": ["legacy_unstructured_plan"]}
        return out
    try:
        plan = json.loads(str(out.get("body") or "{}"))
    except (TypeError, json.JSONDecodeError):
        plan = None
    out["plan"] = plan
    out["contract_validation"] = validate_plan_contract(plan)
    return out
