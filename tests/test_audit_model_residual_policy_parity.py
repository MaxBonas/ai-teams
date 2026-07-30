from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.audit_model_residual_policy_parity import build_audit, validate_audit
from tests.test_audit_model_residual_policy import _fixture


def test_parity_audit_closes_all_surfaces() -> None:
    read_model, preferences = _fixture()
    pending = read_model["candidates"][1]
    pending["owner_preference"] = {
        "state": "low",
        "reason": "reconciled",
        "source": "user_machine",
    }
    preferences["preferences"].append(
        {
            "profile_id": "profile-b",
            "model_id": "shared-model",
            "state": "low",
            "reason": "reconciled",
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    )
    report = build_audit(
        read_model=read_model,
        preferences=preferences,
        coverage={"maintenance_backlog": []},
        automation={"ok": True, "failure_count": 0},
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert report["summary"] == {
        "parity_ready": True,
        "check_count": 10,
        "passed_count": 10,
    }
    assert report["scope"]["inference_attempted"] is False


def test_parity_validator_rejects_tampering() -> None:
    read_model, preferences = _fixture()
    for candidate in read_model["candidates"]:
        if candidate["owner_preference"]["source"] == "default":
            candidate["owner_preference"]["source"] = "user_machine"
            candidate["owner_preference"]["state"] = "low"
            preferences["preferences"].append(
                {
                    "profile_id": candidate["identity"]["profile_id"],
                    "model_id": candidate["identity"]["model_id"],
                    "state": "low",
                    "reason": "fixture",
                    "updated_at": "2026-07-30T12:00:00+00:00",
                }
            )
    report = build_audit(
        read_model=read_model,
        preferences=preferences,
        coverage={"maintenance_backlog": []},
        automation={"ok": True},
        repo_root=Path(__file__).resolve().parents[1],
    )
    tampered = deepcopy(report)
    tampered["summary"]["passed_count"] = 0

    with pytest.raises(ValueError, match="summary drift"):
        validate_audit(tampered)


def test_parity_auditor_cli_is_directly_executable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "audit_model_residual_policy_parity.py"),
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
