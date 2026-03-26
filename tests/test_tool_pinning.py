"""Tests for tool version pinning and lockfile management."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from aiteam.tool_lock import ToolLockManager


class TestToolPinning(TestCase):
    """Test suite for tool version pinning."""

    def setUp(self) -> None:
        """Create a temporary runtime directory."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_tool_lock_"))
        self.lock_manager = ToolLockManager(self.temp_dir)

    def tearDown(self) -> None:
        """Clean up."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_tool_lock_created_after_acquire(self) -> None:
        """After tool acquisition, tool_lock.json exists with pinned versions.
        
        Setup:
        - Create lock entry and write to file
        
        Expected:
        - Lock file exists with version and checksum
        """
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            tool_name="github_mcp",
            version="2.0.0",
            source="npm:@modelcontextprotocol/server-github",
        )
        
        self.lock_manager.write_lock(lock_data)
        
        lock_file = self.temp_dir / "tool_lock.json"
        self.assertTrue(lock_file.exists())
        
        lock = json.loads(lock_file.read_text())
        self.assertIn("github_mcp", lock)
        self.assertEqual(lock["github_mcp"]["version"], "2.0.0")
        self.assertIn("checksum", lock["github_mcp"])

    def test_tool_lock_respects_pinned_version(self) -> None:
        """If lock specifies v2.0, use that version (don't upgrade).
        
        Setup:
        - Create lock with v2.0.0
        - Read pinned version
        
        Expected:
        - get_pinned_version returns "2.0.0"
        """
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            tool_name="github_mcp",
            version="2.0.0",
            source="npm:@modelcontextprotocol/server-github",
        )
        self.lock_manager.write_lock(lock_data)
        
        pinned_version = self.lock_manager.get_pinned_version("github_mcp")
        
        self.assertEqual(pinned_version, "2.0.0")

    def test_tool_lock_integrity_check_detects_tampering(self) -> None:
        """Modifying lock file (changing version without checksum) detected.
        
        Setup:
        - Create valid lock
        - Manually corrupt the version field
        
        Expected:
        - verify_lock_integrity detects tampering
        """
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            tool_name="github_mcp",
            version="2.0.0",
            source="npm:@modelcontextprotocol/server-github",
        )
        self.lock_manager.write_lock(lock_data)
        
        # Tamper: modify version without updating checksum
        lock = json.loads(self.lock_manager.lock_path.read_text())
        lock["github_mcp"]["version"] = "2.5.0"  # Change version
        # checksum is now invalid
        self.lock_manager.lock_path.write_text(json.dumps(lock))
        
        is_valid, error = self.lock_manager.verify_lock_integrity()
        
        self.assertFalse(is_valid)
        self.assertIn("checksum", error.lower())

    def test_tool_lock_missing_falls_back_to_latest(self) -> None:
        """No lock file → can acquire latest version, create lock.
        
        Expected:
        - read_lock returns empty dict if file missing
        """
        self.assertFalse(self.lock_manager.lock_path.exists())
        
        lock = self.lock_manager.read_lock()
        
        self.assertEqual(lock, {})

    def test_tool_lock_restored_on_reload(self) -> None:
        """Lock persists across reloads; same tool = same version.
        
        Setup:
        - First manager: create lock
        - Second manager: read same lock
        
        Expected:
        - Both managers see same version
        """
        # First write
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            tool_name="github_mcp",
            version="2.0.0",
            source="npm:@modelcontextprotocol/server-github",
        )
        self.lock_manager.write_lock(lock_data)
        
        first_version = self.lock_manager.get_pinned_version("github_mcp")
        
        # Second manager (simulating reload)
        manager2 = ToolLockManager(self.temp_dir)
        second_version = manager2.get_pinned_version("github_mcp")
        
        self.assertEqual(first_version, second_version)
        self.assertEqual(first_version, "2.0.0")


class TestToolLockOperations(TestCase):
    """Additional tests for lock manager operations."""

    def setUp(self) -> None:
        """Create a temporary runtime directory."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_tool_ops_"))
        self.lock_manager = ToolLockManager(self.temp_dir)

    def tearDown(self) -> None:
        """Clean up."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_list_locked_tools(self) -> None:
        """List all locked tools and versions."""
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            "github_mcp", "2.0.0", "npm:github"
        )
        lock_data["slack_mcp"] = self.lock_manager.create_lock_entry(
            "slack_mcp", "1.5.0", "npm:slack"
        )
        self.lock_manager.write_lock(lock_data)
        
        tools = self.lock_manager.list_locked_tools()
        
        self.assertEqual(tools["github_mcp"], "2.0.0")
        self.assertEqual(tools["slack_mcp"], "1.5.0")

    def test_update_lock_entry(self) -> None:
        """Update a lock entry."""
        # Create initial
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            "github_mcp", "2.0.0", "npm:github"
        )
        self.lock_manager.write_lock(lock_data)
        
        # Update
        self.lock_manager.update_lock_entry(
            "github_mcp", "2.1.0", "npm:github"
        )
        
        pinned = self.lock_manager.get_pinned_version("github_mcp")
        self.assertEqual(pinned, "2.1.0")

    def test_remove_lock_entry(self) -> None:
        """Remove a tool from lock."""
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            "github_mcp", "2.0.0", "npm:github"
        )
        self.lock_manager.write_lock(lock_data)
        
        # Verify exists
        self.assertEqual(self.lock_manager.get_pinned_version("github_mcp"), "2.0.0")
        
        # Remove
        self.lock_manager.remove_lock_entry("github_mcp")
        
        # Verify removed
        self.assertIsNone(self.lock_manager.get_pinned_version("github_mcp"))

    def test_lock_integrity_valid_lock(self) -> None:
        """Valid lock passes integrity check."""
        lock_data = {}
        lock_data["github_mcp"] = self.lock_manager.create_lock_entry(
            "github_mcp", "2.0.0", "npm:github"
        )
        self.lock_manager.write_lock(lock_data)
        
        is_valid, error = self.lock_manager.verify_lock_integrity()
        
        self.assertTrue(is_valid)
        self.assertEqual(error, "")
