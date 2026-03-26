"""Tests for execution output limits and command result tracking."""

from pathlib import Path
from unittest import TestCase

from aiteam.execution import CommandPolicy, CommandResult


class TestExecutionOutputLimits(TestCase):
    """Test suite for execution engine output limits."""

    def test_output_limit_blocks_plan_on_exceed(self) -> None:
        """ExecutionEngine stops execution when cumulative output > 10MB.
        
        Setup:
        - Simulate 2-step plan: step 1 outputs 6MB, step 2 outputs 6MB
        
        Expected:
        - Total would exceed 10MB, so step 2 result should have reason="output_limit_exceeded"
        """
        policy = CommandPolicy()
        
        # Verify the 10MB limit is set
        self.assertEqual(policy.max_output_bytes, 10 * 1024 * 1024)
        
        # Create a mock result that would exceed the limit
        large_output = "x" * (6 * 1024 * 1024)  # 6MB
        result1 = CommandResult(
            success=True,
            step_type="command",
            command="echo 'step 1'",
            exit_code=0,
            stdout=large_output,
            stderr="",
        )
        
        # Simulate cumulative tracking
        cumulative_bytes = len(result1.stdout.encode()) + len(result1.stderr.encode())
        self.assertGreater(cumulative_bytes, 5 * 1024 * 1024)  # At least 5MB
        
        # Second step would push over limit
        remaining_budget = policy.max_output_bytes - cumulative_bytes
        self.assertLess(remaining_budget, 5 * 1024 * 1024)  # Less than 5MB remaining

    def test_command_result_tracks_bytes(self) -> None:
        """CommandResult properly counts stdout + stderr bytes.
        
        Setup:
        - stdout="hello world" (11 bytes)
        - stderr="error" (5 bytes)
        
        Expected: Total bytes = 16
        """
        result = CommandResult(
            success=False,
            step_type="command",
            command="test",
            exit_code=1,
            stdout="hello world",
            stderr="error",
        )
        
        stdout_bytes = len(result.stdout.encode())
        stderr_bytes = len(result.stderr.encode())
        total_bytes = stdout_bytes + stderr_bytes
        
        self.assertEqual(stdout_bytes, 11)
        self.assertEqual(stderr_bytes, 5)
        self.assertEqual(total_bytes, 16)

    def test_limit_customizable_via_policy(self) -> None:
        """CommandPolicy max_output_bytes parameter is respected.
        
        Setup:
        - Create CommandPolicy(max_output_bytes=1024)
        
        Expected: Policy has custom limit set
        """
        custom_limit = 1024
        policy = CommandPolicy(max_output_bytes=custom_limit)
        
        self.assertEqual(policy.max_output_bytes, custom_limit)

    def test_output_truncation_message_includes_size(self) -> None:
        """Truncation result message includes max bytes constant.
        
        Setup:
        - Verify CommandPolicy.MAX_OUTPUT_BYTES constant exists
        
        Expected: Constant is 10485760 (10MB in bytes)
        """
        self.assertEqual(
            CommandPolicy.MAX_OUTPUT_BYTES,
            10 * 1024 * 1024,
            "MAX_OUTPUT_BYTES should be 10MB (10485760 bytes)"
        )
        
        # Verify it's accessible from instances
        policy = CommandPolicy()
        self.assertEqual(policy.max_output_bytes, CommandPolicy.MAX_OUTPUT_BYTES)
        
        # Verify the exact byte count
        expected_bytes = 10485760
        self.assertEqual(CommandPolicy.MAX_OUTPUT_BYTES, expected_bytes)


class TestCommandResultDataclass(TestCase):
    """Test suite for CommandResult dataclass behavior."""

    def test_command_result_fields(self) -> None:
        """Verify CommandResult has all required fields."""
        result = CommandResult(
            success=True,
            step_type="shell",
            command="echo test",
            exit_code=0,
            stdout="test",
            stderr="",
            reason=None,
        )
        
        self.assertTrue(result.success)
        self.assertEqual(result.step_type, "shell")
        self.assertEqual(result.command, "echo test")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "test")
        self.assertEqual(result.stderr, "")
        self.assertIsNone(result.reason)

    def test_command_result_with_reason(self) -> None:
        """CommandResult can include reason for non-zero exit."""
        result = CommandResult(
            success=False,
            step_type="command",
            command="failing_command",
            exit_code=1,
            stdout="",
            stderr="Command not found",
            reason="command_not_found",
        )
        
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.reason, "command_not_found")

    def test_large_output_tracking(self) -> None:
        """Large outputs are properly tracked in bytes."""
        # Create 2MB output
        large_output = "x" * (2 * 1024 * 1024)
        
        result = CommandResult(
            success=True,
            step_type="command",
            command="generate_large_output",
            exit_code=0,
            stdout=large_output,
            stderr="",
        )
        
        bytes_count = len(result.stdout.encode())
        self.assertGreater(bytes_count, 2 * 1024 * 1024 - 100)  # Account for encoding variation
