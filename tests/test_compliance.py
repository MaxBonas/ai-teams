import unittest

from aiteam.compliance import ComplianceGuard, CompliancePolicy


class ComplianceTests(unittest.TestCase):
    def test_redacts_common_secret_patterns(self) -> None:
        guard = ComplianceGuard()
        text = "api_key=abc123 token: mytoken sk-1234567890ABCDEF"
        redacted = guard.redact_text(text)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("mytoken", redacted)
        self.assertNotIn("sk-1234567890ABCDEF", redacted)
        self.assertIn("<redacted>", redacted)

    def test_sensitive_plan_requires_approval(self) -> None:
        guard = ComplianceGuard()
        allowed, reason, steps = guard.validate_execution_plan(
            plan=[{"type": "cmd", "command": "echo publish playstore"}],
            task_metadata={},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "sensitive_commands_require_approval")
        self.assertTrue(steps)

    def test_sensitive_plan_allowed_with_approval(self) -> None:
        guard = ComplianceGuard()
        allowed, reason, steps = guard.validate_execution_plan(
            plan=[{"type": "cmd", "command": "echo publish playstore"}],
            task_metadata={"approved_sensitive_ops": True},
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "approved")
        self.assertTrue(steps)

    def test_prod_requires_two_approvers(self) -> None:
        guard = ComplianceGuard(policy=CompliancePolicy(environment="prod"))
        allowed, reason, _ = guard.validate_execution_plan(
            plan=[{"type": "cmd", "command": "echo publish playstore"}],
            task_metadata={"approved_sensitive_ops": True, "approved_by": ["lead-1"]},
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "insufficient_approvers_required_2")

    def test_prod_allows_sensitive_plan_with_two_approvers(self) -> None:
        guard = ComplianceGuard(policy=CompliancePolicy(environment="prod"))
        allowed, reason, _ = guard.validate_execution_plan(
            plan=[{"type": "cmd", "command": "echo publish playstore"}],
            task_metadata={
                "approved_sensitive_ops": True,
                "approved_by": ["lead-1", "security-1"],
            },
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "approved")

    def test_approved_adapters_ignored_without_approval(self) -> None:
        guard = ComplianceGuard(policy=CompliancePolicy(environment="prod"))
        adapters = guard.approved_adapters({"approved_adapters": ["playstore_publisher"]})
        self.assertFalse(adapters)


if __name__ == "__main__":
    unittest.main()
