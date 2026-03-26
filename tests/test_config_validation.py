import json
import pytest
from pathlib import Path
from aiteam.config_schema import validate_config

def test_routing_policy_valid_schema(tmp_path: Path):
    p = tmp_path / "routing_policy.json"
    p.write_text('{"pro_first": true}')
    ok, _ = validate_config(p, "routing_policy")
    assert ok

def test_tool_catalog_validation(tmp_path: Path):
    p = tmp_path / "tool_catalog.json"
    p.write_text('{"tools": [{"name": "foo", "source_type": "npm", "source": "bar"}]}')
    ok, _ = validate_config(p, "tool_catalog")
    assert ok

def test_routing_policy_invalid_schema_rejected(tmp_path: Path):
    p = tmp_path / "tool_catalog_err.json"
    # Missing 'source'
    p.write_text('{"tools": [{"name": "foo", "source_type": "npm"}]}')
    ok, msg = validate_config(p, "tool_catalog")
    assert not ok
    assert "Missing required field: 'source'" in msg

def test_schema_error_message_helpful(tmp_path: Path):
    p = tmp_path / "skills.json"
    p.write_text('{"skills": [{"desc": "foo"}]}')
    ok, msg = validate_config(p, "skills_library")
    assert not ok
    assert "Missing required field: 'name'" in msg
