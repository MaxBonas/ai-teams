"""Tests for Learning Registry - failures, insights, and learnings capture."""

import tempfile
from pathlib import Path
from unittest import TestCase

from aiteam.learning_registry import LearningRegistry


class TestLearningRegistry(TestCase):
    """Test suite for learning registry."""

    def setUp(self) -> None:
        """Create temporary registry."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_learning_"))
        self.registry = LearningRegistry(self.temp_dir)

    def tearDown(self) -> None:
        """Clean up."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_record_project_failure(self) -> None:
        """Record a project failure for learning."""
        self.registry.record_project_failure(
            title="Atomic write race condition",
            error_message="File corruption detected",
            what_happened="Process 1 and 2 wrote simultaneously",
            why_it_happened="No locking mechanism",
            impact="Lost finops data for 1 hour, audit trail inconsistent",
            how_to_prevent="Implement atomic write pattern (write-to-temp + rename)",
            project_id="sprint-1",
            tags=["persistence", "critical"],
        )

        records = self.registry.read_all()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "project_failure")
        self.assertEqual(records[0]["title"], "Atomic write race condition")
        self.assertIn("persistence", records[0]["tags"])

    def test_record_system_insight(self) -> None:
        """Record a system insight."""
        self.registry.record_system_insight(
            title="Percentile metrics reveal latency patterns",
            observation="p95 latency jumped 40% while p50 stayed stable",
            implication="We have tail-latency problems affecting 5% of requests",
            suggested_action="Profile slow paths, add caching, optimize query patterns",
            tags=["performance", "metrics"],
        )

        records = self.registry.read_all()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "system_insight")
        self.assertIn("performance", records[0]["tags"])

    def test_record_team_learning(self) -> None:
        """Record team learning."""
        self.registry.record_team_learning(
            title="Exponential backoff beats linear retry",
            what_we_learned="Exponential backoff (1s, 2s, 4s) reduces server load by 60% vs linear (1s, 1s, 1s)",
            how_we_discovered_it="Implemented retry logic, measured incident impact",
            how_to_apply="Use exponential backoff for all network retries going forward",
            team_member="@infra-lead",
            tags=["resilience", "performance"],
        )

        records = self.registry.read_all()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "team_learning")
        self.assertEqual(records[0]["owner"], "@infra-lead")

    def test_record_user_feedback(self) -> None:
        """Record user feedback."""
        self.registry.record_user_feedback(
            title="Onboarding takes 5 days, should be 2 hours",
            feedback="New engineers struggle with architecture documentation and need constant help",
            context="Observed during 3 recent hires; they all said same thing",
            opportunity="Create onboarding bot/NotebookLM integration to accelerate ramp-up",
            from_user="@product-lead",
        )

        records = self.registry.read_all()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["category"], "user_feedback")
        self.assertEqual(records[0]["owner"], "@product-lead")

    def test_read_by_category(self) -> None:
        """Query learnings by category."""
        self.registry.record_project_failure(
            title="Failure 1",
            error_message="Error",
            what_happened="X",
            why_it_happened="Y",
            impact="Z",
            how_to_prevent="A",
        )
        self.registry.record_system_insight(
            title="Insight 1",
            observation="O",
            implication="I",
            suggested_action="S",
        )

        failures = self.registry.read_by_category("project_failure")
        insights = self.registry.read_by_category("system_insight")

        self.assertEqual(len(failures), 1)
        self.assertEqual(len(insights), 1)

    def test_read_by_tag(self) -> None:
        """Query learnings by tag."""
        self.registry.record_project_failure(
            title="Failure with persistence tag",
            error_message="E",
            what_happened="W",
            why_it_happened="Y",
            impact="I",
            how_to_prevent="H",
            tags=["persistence"],
        )
        self.registry.record_system_insight(
            title="Insight with performance tag",
            observation="O",
            implication="I",
            suggested_action="S",
            tags=["performance"],
        )

        persistence_items = self.registry.read_by_tag("persistence")
        performance_items = self.registry.read_by_tag("performance")

        self.assertEqual(len(persistence_items), 1)
        self.assertEqual(len(performance_items), 1)

    def test_mark_addressed(self) -> None:
        """Mark a learning as addressed."""
        self.registry.record_project_failure(
            title="Test failure",
            error_message="E",
            what_happened="W",
            why_it_happened="Y",
            impact="I",
            how_to_prevent="H",
        )

        # Initially open
        records = self.registry.read_open_items()
        self.assertEqual(len(records), 1)

        # Mark as addressed
        self.registry.mark_addressed("Test failure")

        # Should no longer be in open items
        records = self.registry.read_open_items()
        self.assertEqual(len(records), 0)

    def test_summary_statistics(self) -> None:
        """Generate summary statistics."""
        self.registry.record_project_failure(
            title="F1",
            error_message="E",
            what_happened="W",
            why_it_happened="Y",
            impact="I",
            how_to_prevent="H",
            tags=["persistence", "critical"],
        )
        self.registry.record_system_insight(
            title="S1",
            observation="O",
            implication="I",
            suggested_action="S",
            tags=["performance"],
        )
        self.registry.record_team_learning(
            title="T1",
            what_we_learned="W",
            how_we_discovered_it="D",
            how_to_apply="A",
            tags=["resilience"],
        )

        summary = self.registry.summary()

        self.assertEqual(summary["total_records"], 3)
        self.assertEqual(summary["by_category"]["project_failure"], 1)
        self.assertEqual(summary["by_category"]["system_insight"], 1)
        self.assertEqual(summary["by_category"]["team_learning"], 1)
        self.assertEqual(summary["by_tag"]["persistence"], 1)
        self.assertEqual(summary["by_tag"]["performance"], 1)
        self.assertEqual(summary["open_count"], 3)

    def test_export_markdown(self) -> None:
        """Export learnings as markdown."""
        self.registry.record_project_failure(
            title="Critical failure",
            error_message="File corruption",
            what_happened="Race condition",
            why_it_happened="No locking",
            impact="Data loss",
            how_to_prevent="Add atomic writes",
        )

        md = self.registry.export_markdown()

        self.assertIn("# Learning Registry Export", md)
        self.assertIn("Critical failure", md)
        self.assertIn("PROJECT_FAILURE", md)
        self.assertIn("**Total Learnings**: 1", md)

    def test_init_accepts_ledger_file_path(self) -> None:
        ledger_path = self.temp_dir / "custom_learning_registry.jsonl"
        registry = LearningRegistry(ledger_path)
        registry.record_team_learning(
            title="File path init",
            what_we_learned="Works",
            how_we_discovered_it="Unit test",
            how_to_apply="Keep compatibility",
        )
        records = registry.read_all()
        self.assertEqual(len(records), 1)

    def test_init_handles_legacy_directory_at_ledger_path(self) -> None:
        legacy_dir = self.temp_dir / "learning_registry.jsonl"
        if legacy_dir.exists() and legacy_dir.is_file():
            legacy_dir.unlink()
        legacy_dir.mkdir(parents=True, exist_ok=True)
        registry = LearningRegistry(self.temp_dir)
        registry.record_user_feedback(
            title="Legacy path",
            feedback="Recovered",
            context="Migration",
            opportunity="No data loss",
        )
        records = registry.read_all()
        self.assertEqual(len(records), 1)
