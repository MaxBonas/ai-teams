from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.guided_setup_project_commit import (
    materialize_project_proposal,
)


def _profile() -> dict:
    return {
        "id": "codex_subscription",
        "adapter_type": "codex_cli",
        "channel": "subscription",
        "supported_roles": ["team_lead"],
        "config": {},
    }


def _proposal(target: Path, *, mode: str = "create") -> dict:
    source_agent = {
        "agent_id": "role:team_lead",
        "role": "team_lead",
        "name": "Team Lead",
        "seniority": "lead",
        "capabilities": ["planning", "supervision"],
        "supervisor_agent_id": None,
        "preferred_tier": "senior_cloud",
        "preferred_channel": "subscription",
        "assignment_reason": "Owns the project.",
    }
    return {
        "schema_version": "guided_setup_project_proposal_v1",
        "proposal_hash": "c" * 64,
        "project": {
            "mode": mode,
            "name": "Portal",
            "target": str(target),
            "target_exists": mode == "import",
            "target_is_dir": mode == "import",
            "instructions_preview": "Usar TypeScript estricto.",
            "objective": "Crear un portal React",
            "objective_kind": "software",
            "data_class": "internal",
        },
        "profile": {"selected": "solo_lead"},
        "team": {
            "creation_order": ["role:team_lead"],
            "blueprint": {
                "goal_id": "goal:preview",
                "profile": "solo_lead",
                "rationale": "Equipo mínimo.",
                "agents": [source_agent],
                "cost_policy": {"mode": "balanced"},
                "metadata": {"lead_first": True},
            },
            "assignments": [
                {
                    "agent_id": "role:team_lead",
                    "role": "team_lead",
                    "name": "Team Lead",
                    "supervisor_agent_id": None,
                    "assignment_reason": "Owns the project.",
                    "selection_mode": "automatic",
                    "candidate": {
                        "candidate_id": "codex:lead",
                        "profile_id": "codex_subscription",
                        "model_id": "gpt-fixture",
                    },
                    "accountability": {
                        "reports_to": None,
                        "acceptance_owner": "owner",
                        "required_evidence": "agent_report_and_role_contract",
                    },
                }
            ],
        },
        "budget": {"mode": "balanced"},
        "save_gate": {"allowed": True, "blockers": []},
    }


def test_create_materializes_exact_assignment_and_one_atomic_wakeup(
    tmp_path: Path,
) -> None:
    target = tmp_path / "Portal"
    proposal = _proposal(target)

    result = materialize_project_proposal(
        proposal,
        profiles=[_profile()],
        schema_path=SCHEMA_PATH,
    )

    assert result["footprint_verified"] is True
    assert {entry.name for entry in tmp_path.iterdir()} == {"Portal"}
    assert Path(result["database"]).is_file()
    assert (target / ".aiteam" / "instructions.md").read_text(
        encoding="utf-8"
    ) == "Usar TypeScript estricto.\n"
    config = json.loads(
        (target / ".aiteam" / "project_config.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["adapter_profile_ids"] == ["codex_subscription"]
    with sqlite3.connect(result["database"]) as conn:
        agent = conn.execute(
            "SELECT id, role, adapter_type, adapter_config_json FROM agents"
        ).fetchone()
        assert agent[:3] == ("role:lead", "lead", "codex_cli")
        adapter = json.loads(agent[3])
        assert adapter["model"] == "gpt-fixture"
        assert adapter["selection_intent"]["candidate_id"] == "codex:lead"
        assert conn.execute(
            "SELECT status, proposed_by_agent_id FROM team_blueprints"
        ).fetchone() == ("active", "role:lead")
        assert conn.execute(
            "SELECT agent_id, assigned_by_agent_id FROM agent_assignments"
        ).fetchone() == ("role:lead", "role:lead")
        assert conn.execute(
            "SELECT agent_id, status FROM wakeup_requests"
        ).fetchall() == [("role:lead", "queued")]


def test_import_preserves_foreign_files_and_only_adds_aiteam(
    tmp_path: Path,
) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    foreign = target / "package.json"
    foreign.write_text('{"private":true}', encoding="utf-8")

    materialize_project_proposal(
        _proposal(target, mode="import"),
        profiles=[_profile()],
        schema_path=SCHEMA_PATH,
    )

    assert foreign.read_text(encoding="utf-8") == '{"private":true}'
    assert (target / ".aiteam" / "aiteam.db").is_file()
    assert not list(target.glob(".aiteam-staging-*"))


def test_failure_rolls_back_create_and_import_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(
        "aiteam.guided_setup_project_commit._insert_project_state",
        fail,
    )
    create_target = tmp_path / "create-failure"
    with pytest.raises(RuntimeError, match="injected"):
        materialize_project_proposal(
            _proposal(create_target),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )
    assert not create_target.exists()
    assert not list(tmp_path.glob(".aiteam-project-staging-*"))

    import_target = tmp_path / "import-failure"
    import_target.mkdir()
    marker = import_target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="injected"):
        materialize_project_proposal(
            _proposal(import_target, mode="import"),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (import_target / ".aiteam").exists()
    assert not list(import_target.glob(".aiteam-staging-*"))


def test_changed_model_or_existing_runtime_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "Portal"
    changed = _proposal(target)
    with pytest.raises(ValueError, match="model_changed"):
        materialize_project_proposal(
            changed,
            profiles=[{
                **_profile(),
                "model_options": [{"value": "different-model"}],
            }],
            schema_path=SCHEMA_PATH,
        )
    assert not target.exists()

    imported = tmp_path / "existing"
    (imported / ".aiteam").mkdir(parents=True)
    with pytest.raises(FileExistsError, match="runtime_collision"):
        materialize_project_proposal(
            _proposal(imported, mode="import"),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )


def test_create_requires_existing_parent_and_retry_never_allocates_sibling(
    tmp_path: Path,
) -> None:
    missing_parent_target = tmp_path / "missing" / "Portal"
    with pytest.raises(FileNotFoundError, match="parent_missing"):
        materialize_project_proposal(
            _proposal(missing_parent_target),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )
    assert not (tmp_path / "missing").exists()

    target = tmp_path / "Portal"
    materialize_project_proposal(
        _proposal(target),
        profiles=[_profile()],
        schema_path=SCHEMA_PATH,
    )
    with pytest.raises(FileExistsError, match="target_collision"):
        materialize_project_proposal(
            _proposal(target),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )
    assert {entry.name for entry in tmp_path.iterdir()} == {"Portal"}


def test_footprint_guard_fails_without_deleting_unowned_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiteam import guided_setup_project_commit as commit_module

    original_write_runtime = commit_module._write_runtime
    unexpected = tmp_path / "personal-concurrent"

    def write_with_concurrent_entry(*args, **kwargs):
        original_write_runtime(*args, **kwargs)
        unexpected.mkdir()

    monkeypatch.setattr(commit_module, "_write_runtime", write_with_concurrent_entry)

    with pytest.raises(RuntimeError, match="cleanup_failed"):
        materialize_project_proposal(
            _proposal(tmp_path / "Portal"),
            profiles=[_profile()],
            schema_path=SCHEMA_PATH,
        )

    assert unexpected.is_dir()
    assert not (tmp_path / "Portal").exists()
    assert not list(tmp_path.glob(".aiteam-project-staging-*"))
