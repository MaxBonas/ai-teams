import pytest
import json
from pathlib import Path
from aiteam.tool_lock import ToolLockManager

def test_generate_and_read_lockfile(tmp_path: Path):
    manager = ToolLockManager(tmp_path)
    tools = [
        {"name": "github_mcp", "source_type": "npm", "source": "@github/mcp", "version": "1.0.0"}
    ]
    manager.generate_lockfile(tools)
    
    assert manager.lock_file.exists()
    
    lock_data = manager.read_lockfile()
    assert lock_data["version"] == "1.0"
    assert "github_mcp" in lock_data["tools"]
    assert lock_data["tools"]["github_mcp"]["version"] == "1.0.0"
    assert "integrity" in lock_data["tools"]["github_mcp"]

def test_check_drift_missing_in_lockfile(tmp_path: Path):
    manager = ToolLockManager(tmp_path)
    tools = [
        {"name": "github_mcp", "source_type": "npm", "source": "@github/mcp"}
    ]
    manager.generate_lockfile(tools)
    
    # Requesting a new tool not in lockfile
    requested = [
        {"name": "github_mcp", "source_type": "npm", "source": "@github/mcp"},
        {"name": "notion_mcp", "source_type": "npm", "source": "@notion/mcp"}
    ]
    
    drifts = manager.check_drift(requested)
    assert len(drifts) == 1
    assert "notion_mcp is not in lockfile" in drifts[0]

def test_check_drift_source_changed(tmp_path: Path):
    manager = ToolLockManager(tmp_path)
    manager.generate_lockfile([
        {"name": "my_tool", "source_type": "npm", "source": "old_source"}
    ])
    
    requested = [
        {"name": "my_tool", "source_type": "npm", "source": "new_source"}
    ]
    drifts = manager.check_drift(requested)
    assert len(drifts) == 1
    assert "my_tool source changed" in drifts[0]
