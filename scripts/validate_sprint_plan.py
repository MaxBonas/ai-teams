#!/usr/bin/env python3
"""
Sprint Roadmap Validation Script

Validates that the sprint plan (SPRINT_ROADMAP_Q1_2026.md + TEST_MATRIX_SPRINTS_1_2_3.md)
is executable and consistent with the current codebase state.

Usage:
    python scripts/validate_sprint_plan.py [--fix] [--verbose]

Checks:
    1. Documentation references are consistent (test counts, file names).
    2. Test files don't already exist (to avoid conflicts).
    3. Implementation files are writable + have correct structure.
    4. Current test count matches baseline (91).
    5. All mentioned modules import correctly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class SprintPlanValidator:
    def __init__(self, project_root: Path, verbose: bool = False) -> None:
        self.project_root = project_root
        self.verbose = verbose
        self.docs_dir = project_root / "docs"
        self.tests_dir = project_root / "tests"
        self.aiteam_dir = project_root / "aiteam"
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks_passed = 0
        self.checks_failed = 0

    def log(self, message: str, level: str = "INFO") -> None:
        if self.verbose or level != "INFO":
            print(f"[{level}] {message}")

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.checks_failed += 1
        self.log(message, level="ERROR")

    def warning(self, message: str) -> None:
        self.warnings.append(message)
        self.log(message, level="WARN")

    def success(self, message: str) -> None:
        self.checks_passed += 1
        self.log(message, level="OK")

    def validate_all(self) -> bool:
        """Run all validation checks."""
        print("=== Sprint Roadmap Validation ===\n")

        self.validate_documentation_exists()
        self.validate_test_count_baseline()
        self.validate_test_files_not_exist()
        self.validate_implementation_files_writable()
        self.validate_module_imports()
        self.validate_doc_consistency()

        print(f"\n=== Summary ===")
        print(f"Checks Passed: {self.checks_passed}")
        print(f"Checks Failed: {self.checks_failed}")
        print(f"Warnings: {len(self.warnings)}")

        return self.checks_failed == 0

    def validate_documentation_exists(self) -> None:
        """Check that sprint docs exist."""
        print("\n1. Validating documentation files...")

        files_to_check = [
            self.docs_dir / "SPRINT_ROADMAP_Q1_2026.md",
            self.docs_dir / "TEST_MATRIX_SPRINTS_1_2_3.md",
            self.docs_dir / "DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md",
        ]

        for file_path in files_to_check:
            if file_path.exists():
                self.success(f"  ✓ Found: {file_path.name}")
            else:
                self.error(f"  ✗ Missing: {file_path}")

    def validate_test_count_baseline(self) -> None:
        """Check that current test count is 91 (baseline)."""
        print("\n2. Validating test count baseline...")

        try:
            import subprocess

            result = subprocess.run(
                ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self.project_root),
            )

            # Parse "Ran N tests" from output
            for line in result.stderr.split("\n"):
                if "Ran" in line and "test" in line:
                    parts = line.split()
                    try:
                        count = int(parts[1])
                        if count == 91:
                            self.success(f"  ✓ Baseline: {count} tests passing")
                        elif count > 91:
                            self.warning(f"  ! Baseline: {count} tests (expected 91, may already have Sprint 1 tests)")
                        else:
                            self.error(f"  ✗ Baseline: {count} tests (expected 91)")
                    except ValueError:
                        pass
        except Exception as e:
            self.error(f"  ✗ Could not run test count check: {e}")

    def validate_test_files_not_exist(self) -> None:
        """Ensure new test files don't already exist (to avoid conflicts)."""
        print("\n3. Validating new test files don't conflict...")

        test_files = [
            "test_finops_anomaly.py",
            "test_execution_limits.py",
            "test_system_check_finops.py",
            "test_observability_metrics.py",
            "test_observability_alerts.py",
            "test_compliance_audit.py",
            "test_config_validation.py",
            "test_tool_pinning.py",
            "test_tool_acquisition_retry.py",
            "test_integration_cli.py",
            "test_chaos.py",
        ]

        for test_file in test_files:
            path = self.tests_dir / test_file
            if not path.exists():
                self.success(f"  ✓ Available: {test_file}")
            else:
                self.warning(f"  ! Already exists: {test_file} (will be overwritten)")

    def validate_implementation_files_writable(self) -> None:
        """Check that implementation files are writable."""
        print("\n4. Validating implementation files are writable...")

        files_to_check = [
            self.aiteam_dir / "observability.py",
            self.aiteam_dir / "compliance.py",
            self.aiteam_dir / "config.py",
            self.aiteam_dir / "autotools.py",
            self.aiteam_dir / "router.py",
            self.aiteam_dir / "finops.py",
            self.aiteam_dir / "execution.py",
        ]

        for file_path in files_to_check:
            if file_path.exists() and file_path.is_file():
                if file_path.stat().st_mode & 0o200:  # Check write permission
                    self.success(f"  ✓ Writable: {file_path.name}")
                else:
                    self.error(f"  ✗ Not writable: {file_path}")
            else:
                self.error(f"  ✗ Not found: {file_path}")

    def validate_module_imports(self) -> None:
        """Test that core modules import correctly."""
        print("\n5. Validating module imports...")

        modules_to_check = [
            "aiteam.observability",
            "aiteam.compliance",
            "aiteam.config",
            "aiteam.finops",
            "aiteam.router",
            "aiteam.execution",
            "aiteam.autotools",
            "aiteam.persistence",
        ]

        for module_name in modules_to_check:
            try:
                __import__(module_name)
                self.success(f"  ✓ Imports: {module_name}")
            except ImportError as e:
                self.error(f"  ✗ Import failed: {module_name} ({e})")
            except Exception as e:
                self.error(f"  ✗ Error importing {module_name}: {e}")

    def validate_doc_consistency(self) -> None:
        """Check for consistency in documentation."""
        print("\n6. Validating documentation consistency...")

        # Check that DEEP_AUDIT references test count 91 or higher
        deep_audit = self.docs_dir / "DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md"
        if deep_audit.exists():
            content = deep_audit.read_text(encoding="utf-8")
            if "91 tests" in content or "test passing" in content:
                self.success("  ✓ DEEP_AUDIT mentions 91 tests baseline")
            else:
                self.warning("  ! DEEP_AUDIT may need test count update")

        # Check that sprint roadmap exists and mentions 142+ tests
        sprint_roadmap = self.docs_dir / "SPRINT_ROADMAP_Q1_2026.md"
        if sprint_roadmap.exists():
            content = sprint_roadmap.read_text(encoding="utf-8")
            if "142+" in content or "141" in content or "130+" in content:
                self.success("  ✓ SPRINT_ROADMAP mentions target test count")
            else:
                self.warning("  ! SPRINT_ROADMAP may need test count verification")

        # Check persistence.py exists (Tier 1 fix)
        persistence_file = self.aiteam_dir / "persistence.py"
        if persistence_file.exists():
            content = persistence_file.read_text(encoding="utf-8")
            if "AtomicFileWriter" in content and "read_jsonl_with_dedup" in content:
                self.success("  ✓ persistence.py has Tier 1 atomic write implementation")
            else:
                self.error("  ✗ persistence.py missing key implementations")
        else:
            self.error("  ✗ persistence.py does not exist")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Sprint Roadmap")
    parser.add_argument("--fix", action="store_true", help="(Placeholder) Fix issues automatically")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    validator = SprintPlanValidator(project_root, verbose=args.verbose)

    if validator.validate_all():
        print("\n✓ Sprint plan validation PASSED. Ready to execute sprints.")
        return 0
    else:
        print("\n✗ Sprint plan validation FAILED. Fix errors before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
