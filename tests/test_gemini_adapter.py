"""Gemini API adapter: el responseSchema debe ser compatible con Gemini
(subconjunto de OpenAPI 3.0), no JSON Schema completo."""
from __future__ import annotations

from aiteam.adapters.gemini_adapter import _to_gemini_schema
from aiteam.adapters.work_contract import SUBMIT_WORK_SCHEMA


def _has_key(node, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_has_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_has_key(item, key) for item in node)
    return False


def test_sanitizer_strips_additional_properties_recursively() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "string"}}},
            }
        },
    }

    sanitized = _to_gemini_schema(schema)

    assert not _has_key(sanitized, "additionalProperties")
    assert sanitized["properties"]["items"]["items"]["properties"]["x"]["type"] == "string"


def test_submit_work_schema_is_gemini_compatible_after_sanitizing() -> None:
    """El bug real en vivo: Gemini rechazó SUBMIT_WORK_SCHEMA tal cual con
    'Unknown name additionalProperties... Cannot find field'."""
    assert _has_key(SUBMIT_WORK_SCHEMA, "additionalProperties"), (
        "si esto falla, alguien ya quitó additionalProperties del schema compartido "
        "y el sanitizador (aunque inofensivo) ya no hace falta aquí"
    )

    sanitized = _to_gemini_schema(SUBMIT_WORK_SCHEMA)

    assert not _has_key(sanitized, "additionalProperties")
    assert sanitized["required"] == ["ops", "status", "summary"]


def test_sanitizer_does_not_mutate_the_original_schema() -> None:
    _to_gemini_schema(SUBMIT_WORK_SCHEMA)

    assert "additionalProperties" in SUBMIT_WORK_SCHEMA, (
        "el sanitizador no debe mutar el schema compartido con el adapter OpenAI"
    )
