import json
from pathlib import Path

def validate_dict(data: dict, required_keys: list[str]) -> tuple[bool, str]:
    for key in required_keys:
        if key not in data:
            return False, f"Missing required field: '{key}'"
    return True, "ok"

def validate_config(file_path: Path, schema_type: str) -> tuple[bool, str]:
    if not file_path.exists():
        return True, "ok"
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON: {exc}"
        
    if schema_type == "routing_policy":
        if not isinstance(data, dict):
            return False, "Root must be an object (dict)"
        return True, "ok"
        
    elif schema_type == "tool_catalog":
        if not isinstance(data, dict) or "tools" not in data:
            return False, "Root must contain 'tools' array"
        sources = data["tools"]
        if not isinstance(sources, list):
            return False, "'tools' must be an array"
        for i, item in enumerate(sources):
            if not isinstance(item, dict):
                return False, f"Item at index {i} is not an object"
            ok, err = validate_dict(item, ["name", "source_type", "source"])
            if not ok:
                return False, f"Item at index {i}: {err}"
                
    elif schema_type == "skills_library":
        if not isinstance(data, dict) or "skills" not in data:
            return False, "Root must contain 'skills' array"
        skills = data["skills"]
        if not isinstance(skills, list):
            return False, "'skills' must be an array"
        for i, item in enumerate(skills):
            if not isinstance(item, dict):
                return False, f"Item at index {i} is not an object"
            ok, err = validate_dict(item, ["name"])
            if not ok:
                return False, f"Item at index {i}: {err}"
                
    return True, "ok"
