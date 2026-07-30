from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_project_portability_acceptance import (
    build_acceptance,
    validate_acceptance,
)


def test_project_portability_acceptance_is_green_and_redacted() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    report = build_acceptance(repo_root)

    assert report["summary"] == {
        "portability_acceptance_ready": True,
        "check_count": 9,
        "passed_count": 9,
    }
    assert report["scope"] == {
        "fixtures_only": True,
        "real_projects_root_read_only": True,
        "user_projects_mutated": False,
        "global_installations_mutated": False,
        "cleanup_jobs_installed": False,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "remote_quota_consumed": False,
        "paths_emitted": False,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "fixture-private-token-must-not-leak" not in serialized
    assert str(Path.home()) not in serialized
    assert "private/repo" not in serialized


def test_project_portability_acceptance_rejects_tampering() -> None:
    report = build_acceptance(Path(__file__).resolve().parents[1])
    tampered = deepcopy(report)
    tampered["checks"]["mixed_root_is_read_only_and_conservative"] = False

    with pytest.raises(ValueError, match="summary drift"):
        validate_acceptance(tampered)


def test_project_portability_acceptance_cli_writes_strict_receipt(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output = tmp_path / "portability.json"

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "audit_project_portability_acceptance.py"),
            "--strict",
            "--output",
            str(output),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["summary"]["portability_acceptance_ready"] is True
    validate_acceptance(receipt)
