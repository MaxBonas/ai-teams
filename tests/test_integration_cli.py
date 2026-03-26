import pytest
import json
from pathlib import Path

from aiteam.cli import (
    cmd_init,
    cmd_system_check,
    cmd_tool_lock,
    cmd_mcp_status
)

def test_e2e_cli_flow(tmp_path: Path):
    runtime_dir = tmp_path / "runtime"
    
    # 1. Initialize runtime
    cmd_init(runtime_dir)
    assert runtime_dir.exists()
    assert (runtime_dir / "adapters.json").exists()
    
    # 2. Create a dummy catalog
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({
        "tools": [
            {
                "name": "fake_tool",
                "source_type": "npm",
                "source": "fake",
                "version": "1.0.0"
            }
        ]
    }), encoding="utf-8")
    
    # 3. Generate tool lockfile
    cmd_tool_lock(runtime_dir, catalog_path)
    assert (runtime_dir / "tools.lock.json").exists()
    
    # Verify lock content
    lock_data = json.loads((runtime_dir / "tools.lock.json").read_text(encoding="utf-8"))
    assert "fake_tool" in lock_data["tools"]
    
    # 4. MCP Status
    mcp_path = runtime_dir / "mcp_servers.json"
    mcp_path.write_text(json.dumps({"servers": []}), encoding="utf-8")
    cmd_mcp_status(runtime_dir)
    
    # 5. System Check
    # We run it with strict=False to avoid exiting the test runner if health is low
    try:
        cmd_system_check(
            runtime_dir=runtime_dir,
            environment="dev",
            browser_mode="basic",
            doctor_timeout=1,
            strict=False,
            min_skills_coverage=0.0
        )
    except SystemExit as exc:
        pytest.fail(f"System check exited unexpectedly: {exc}")
        
    assert (runtime_dir / "system_check.json").exists()
