from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.audit_windows_clean_room_matrix import build_matrix


def _receipt(kind: str) -> dict:
    revision = "a" * 40
    return {
        "schema_version": "windows_clean_room_acceptance_v1",
        "ok": True,
        "independent_machine": True,
        "promotion_allowed": True,
        "source": {
            "revision": revision,
            "harness_sha256": "b" * 64,
            "working_tree_dirty": False,
        },
        "installation_state": {
            "kind": kind,
            "pre_update_revision": (
                "c" * 40 if kind == "existing_checkout_updated" else None
            ),
            "updated_to_revision": revision,
            "contract_ready": True,
        },
        "fixture": {
            "commit_schema_version": "guided_setup_project_commit_v1",
            "footprint_verified": True,
            "retry_collision_blocked": True,
        },
        "update_acceptance": {"project_unchanged": True},
        "database_rollback": {"footprint_restored": True},
        "installation_lifecycle": {
            "scheduled_tasks_installed": False,
            "services_installed": False,
            "startup_entries_installed": False,
        },
        "steps": [{"ok": True}],
    }


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_matrix_requires_both_sha_bound_independent_scenarios(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean.json"
    updated = tmp_path / "updated.json"
    _write(clean, _receipt("clean_clone"))
    _write(updated, _receipt("existing_checkout_updated"))

    report = build_matrix(clean, updated)

    assert report["summary"] == {
        "matrix_ready": True,
        "check_count": 9,
        "passed_count": 9,
    }


def test_local_or_dirty_receipt_cannot_close_matrix(tmp_path: Path) -> None:
    clean_payload = _receipt("clean_clone")
    dirty = deepcopy(_receipt("existing_checkout_updated"))
    dirty["source"]["working_tree_dirty"] = True
    dirty["independent_machine"] = False
    dirty["promotion_allowed"] = False
    clean = tmp_path / "clean.json"
    updated = tmp_path / "updated.json"
    _write(clean, clean_payload)
    _write(updated, dirty)

    report = build_matrix(clean, updated)

    assert report["summary"]["matrix_ready"] is False
    assert report["checks"]["both_independent_and_promotable"] is False
    assert report["checks"]["both_checkouts_clean"] is False


def test_workflow_builds_and_seals_clean_and_updated_installations() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "windows-clean-room.yml"
    ).read_text(encoding="utf-8")

    assert "- clean-clone" in workflow
    assert "- existing-checkout-updated" in workflow
    assert "fetch-depth: 2" in workflow
    assert "prepare_dev_env.bat" in workflow
    assert "--pre-update-revision" in workflow
    assert "audit_windows_clean_room_matrix.py" in workflow
    assert "merge-multiple: true" in workflow
