"""CLI tests for learning registry command behavior."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase


class TestLearningCLI(TestCase):
    def setUp(self) -> None:
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="test_learning_cli_"))

    def tearDown(self) -> None:
        import shutil

        if self.runtime_dir.exists():
            shutil.rmtree(self.runtime_dir)

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python", "-m", "aiteam.cli", "--runtime-dir", str(self.runtime_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_learning_list_subcommand_style_supported(self) -> None:
        result = self._run("learning", "list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No learnings recorded yet", result.stdout)

    def test_learning_record_and_summary(self) -> None:
        record = self._run(
            "learning",
            "record-failure",
            "--learning-title",
            "API timeout",
            "--learning-description",
            "rate limit",
            "--learning-tags",
            "network,critical",
        )
        self.assertEqual(record.returncode, 0)
        self.assertIn("recorded", record.stdout.lower())

        summary = self._run("learning", "summary")
        self.assertEqual(summary.returncode, 0)
        self.assertIn("Total Learnings: 1", summary.stdout)

    def test_learning_list_filters_by_tag_and_status_alias_flags(self) -> None:
        self._run(
            "learning",
            "record-insight",
            "--learning-title",
            "Retry works",
            "--learning-description",
            "observed better behavior",
            "--learning-tags",
            "reliability",
        )
        filtered = self._run("learning", "list", "--status", "open", "--tag", "reliability")
        self.assertEqual(filtered.returncode, 0)
        self.assertIn("Retry works", filtered.stdout)
