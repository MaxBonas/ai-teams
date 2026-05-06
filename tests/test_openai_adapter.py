from __future__ import annotations

from aiteam.adapters.work_contract import OPENAI_SUBMIT_WORK_SCHEMA


def test_openai_submit_work_schema_is_strict_compatible() -> None:
    item = OPENAI_SUBMIT_WORK_SCHEMA["properties"]["ops"]["items"]
    property_keys = set(item["properties"])

    assert set(item["required"]) == property_keys
    assert item["additionalProperties"] is False
    assert "body" in item["required"]
    assert item["properties"]["body"]["type"] == ["string", "null"]
