from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aiteam.guided_setup_project_preflight_executor import (
    execute_project_preflight_plan as execute_preflight_plan,
)
from api.main import app
from api.routers.guided_setup import (
    _observe_project_path,
    _resolve_project_identity,
)


def _coverage_candidate(
    role: str,
    *,
    profile_id: str,
    perspective: str,
    pool: str,
) -> dict:
    return {
        "candidate_id": f"{profile_id}:{role}",
        "identity": {
            "profile_id": profile_id,
            "model_id": f"model-{role}",
            "provider_org": perspective,
            "channel": "subscription",
            "perspective_key": perspective,
            "capacity_pool": pool,
        },
        "model_metadata": {
            "tier": "premium",
            "caps": ["reasoning"],
        },
        "rank": 1,
        "selection_reason": "fixture",
        "owner_selectable": True,
        "contextual_compatibility": {
            "allowed": True,
            "code": "compatible",
        },
        "selection_score": {
            "score": 90,
            "auto_eligible": True,
            "hard_gates": {"adapter_green": {"passed": True}},
        },
    }


def _coverage_selection(role: str) -> dict:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": [
            _coverage_candidate(
                role,
                profile_id="codex_subscription",
                perspective="openai",
                pool="codex",
            ),
            _coverage_candidate(
                role,
                profile_id="unprepared_subscription",
                perspective="other",
                pool="other",
            ),
        ],
    }


def _answers() -> dict:
    return {
        "goal": "Crear una aplicación React",
        "objective_kind": "software",
        "languages": ["React", "TypeScript"],
        "data_sensitivity": "internal",
        "budget_priority": "balanced",
        "subscriptions": ["codex"],
        "api_access": "not_willing",
        "local_models": "not_wanted",
        "autonomy": "supervised",
        "criticality": "medium",
        "team_preference": "solo_lead",
        "external_tools": "optional",
    }


def test_guided_setup_api_create_resume_transition_and_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    client = TestClient(app, raise_server_exceptions=True)

    contract = client.get("/api/guided-setup/contract/machine_onboarding")
    assert contract.status_code == 200
    assert contract.json()["contract"]["schema_version"] == "guided_setup_v1"

    created = client.post(
        "/api/guided-setup/sessions",
        json={
            "scope": "machine_onboarding",
            "subject_key": "machine",
            "metadata": {"entrypoint": "first_run"},
        },
    )
    assert created.status_code == 200
    session = created.json()["session"]
    resumed = client.post(
        "/api/guided-setup/sessions",
        json={
            "scope": "machine_onboarding",
            "subject_key": "machine",
        },
    ).json()["session"]
    assert resumed["id"] == session["id"]

    started = client.patch(
        f"/api/guided-setup/sessions/{session['id']}/steps/welcome",
        json={
            "status": "in_progress",
            "expected_revision": session["revision"],
        },
    )
    assert started.status_code == 200
    current = started.json()["session"]
    stale = client.patch(
        f"/api/guided-setup/sessions/{session['id']}/steps/welcome",
        json={
            "status": "passed",
            "expected_revision": session["revision"],
        },
    )
    assert stale.status_code == 409
    reset = client.post(
        f"/api/guided-setup/sessions/{session['id']}/reset",
        json={"expected_revision": current["revision"], "confirm": True},
    )
    assert reset.status_code == 200
    assert reset.json()["session"]["steps"][0]["status"] == "not_started"


def test_guided_setup_api_rejects_extra_and_secret_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    client = TestClient(app, raise_server_exceptions=True)
    extra = client.post(
        "/api/guided-setup/sessions",
        json={
            "scope": "machine_onboarding",
            "subject_key": "machine",
            "unexpected": True,
        },
    )
    assert extra.status_code == 422
    session = client.post(
        "/api/guided-setup/sessions",
        json={"scope": "machine_onboarding", "subject_key": "machine"},
    ).json()["session"]
    secret = client.patch(
        f"/api/guided-setup/sessions/{session['id']}/steps/welcome",
        json={
            "status": "in_progress",
            "expected_revision": session["revision"],
            "response": {"access_token": "forbidden"},
        },
    )
    assert secret.status_code == 422


def test_guided_setup_needs_api_contract_assessment_and_validation() -> None:
    client = TestClient(app, raise_server_exceptions=True)
    contract = client.get(
        "/api/guided-setup/needs-contract/project_setup",
    )
    assert contract.status_code == 200
    assert contract.json()["questionnaire"]["schema_version"] == (
        "guided_setup_needs_v1"
    )

    partial = client.post(
        "/api/guided-setup/needs-assessment",
        json={
            "scope": "project_setup",
            "answers": {"goal": "Crear una aplicación"},
        },
    )
    assert partial.status_code == 200
    assert partial.json()["submission"]["assessment"]["complete"] is False

    complete = client.post(
        "/api/guided-setup/needs-assessment",
        json={"scope": "project_setup", "answers": _answers()},
    )
    assert complete.status_code == 200
    assert len(complete.json()["submission"]["assessment_hash"]) == 64

    invalid = client.post(
        "/api/guided-setup/needs-assessment",
        json={
            "scope": "project_setup",
            "answers": {**_answers(), "criticality": "extreme"},
        },
    )
    assert invalid.status_code == 422


def test_guided_setup_preparation_api_builds_and_persists_server_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setattr(
        "api.routers.guided_setup.build_machine_inventory",
        lambda **_kwargs: {
            "schema_version": "machine_doctor_v1",
            "scope": {
                "read_only": True,
                "secrets_read": False,
                "credentials_probed": False,
            },
            "runtimes": [
                {
                    "id": "python",
                    "requirement": "required",
                    "ready": True,
                    "installed": True,
                    "version": "3.12.10",
                    "minimum_version": "3.10",
                }
            ],
            "adapters": [],
        },
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.load_adapter_profiles",
        list,
    )
    client = TestClient(app, raise_server_exceptions=True)
    session = client.post(
        "/api/guided-setup/sessions",
        json={"scope": "machine_onboarding", "subject_key": "machine"},
    ).json()["session"]
    needs = client.post(
        "/api/guided-setup/needs-assessment",
        json={"scope": "machine_onboarding", "answers": _answers()},
    ).json()["submission"]
    for step_key, response in (
        ("welcome", {}),
        ("projects_root", {"path": "C:/fixture"}),
        ("needs_profile", needs),
    ):
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "in_progress",
                "expected_revision": session["revision"],
            },
        ).json()["session"]
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "passed",
                "expected_revision": session["revision"],
                "response": response,
            },
        ).json()["session"]

    result = client.post(
        f"/api/guided-setup/sessions/{session['id']}/preparation",
        json={"expected_revision": session["revision"]},
    )
    assert result.status_code == 200
    payload = result.json()
    assert payload["receipt"]["schema_version"] == (
        "guided_setup_preparation_receipt_v1"
    )
    assert payload["receipt"]["ready"] is False
    assert "runtimes" not in payload["receipt"]
    assert payload["guidance"]["policy"]["execution"] == "manual_only"
    assert payload["guidance"]["policy"]["automatic_install"] is False
    adapter_step = next(
        row
        for row in payload["session"]["steps"]
        if row["key"] == "adapter_setup"
    )
    assert adapter_step["status"] == "in_progress"
    assert adapter_step["response"]["preparation_receipt_ref"].startswith(
        "sha256:"
    )

    forged = client.post(
        f"/api/guided-setup/sessions/{session['id']}/preparation",
        json={
            "expected_revision": payload["session"]["revision"],
            "provider_evidence": {"codex_subscription": {"catalog": "passed"}},
        },
    )
    assert forged.status_code == 422


def test_guided_setup_coverage_api_uses_canonical_context_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    profiles = [
        {
            "id": "codex_subscription",
            "channel": "subscription",
        },
        {
            "id": "unprepared_subscription",
            "channel": "subscription",
        },
    ]
    monkeypatch.setattr(
        "api.routers.guided_setup.load_adapter_profiles",
        lambda: profiles,
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.build_machine_inventory",
        lambda **_kwargs: {
            "schema_version": "machine_doctor_v1",
            "scope": {
                "read_only": True,
                "secrets_read": False,
                "credentials_probed": False,
            },
            "runtimes": [
                {
                    "id": "python",
                    "requirement": "required",
                    "ready": True,
                    "installed": True,
                    "version": "3.12.10",
                    "minimum_version": "3.10",
                }
            ],
            "adapters": [
                {
                    "id": "codex_subscription",
                    "cli": {"installed": True, "version": "999.0.0"},
                    "authentication_status": "authenticated",
                    "health_status": "ok",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.build_canonical_provider_evidence",
        lambda *_args, **_kwargs: {
            "stage_evidence": {
                "codex_subscription": {
                    "authentication": "passed",
                    "catalog": "passed",
                    "health": "passed",
                    "contract": "passed",
                }
            }
        },
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.get_current_model_catalog",
        lambda **kwargs: {
            "schema_version": "model_catalog_read_model_v2",
            "content_hash": "fixture-catalog-hash",
            "candidates": [],
            "_requested_db_paths": kwargs["db_paths"],
        },
    )
    selection_calls = []

    def fake_contextual_selection(db_path: Path, **kwargs) -> dict:
        selection_calls.append({"db_path": db_path, **kwargs})
        return _coverage_selection(kwargs["role"])

    monkeypatch.setattr(
        "api.routers.guided_setup.contextual_model_selection",
        fake_contextual_selection,
    )
    client = TestClient(app, raise_server_exceptions=True)
    session = client.post(
        "/api/guided-setup/sessions",
        json={"scope": "machine_onboarding", "subject_key": "coverage-machine"},
    ).json()["session"]
    needs = client.post(
        "/api/guided-setup/needs-assessment",
        json={"scope": "machine_onboarding", "answers": _answers()},
    ).json()["submission"]
    for step_key, response in (
        ("welcome", {}),
        ("projects_root", {"path": "C:/fixture"}),
        ("needs_profile", needs),
    ):
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "in_progress",
                "expected_revision": session["revision"],
            },
        ).json()["session"]
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "passed",
                "expected_revision": session["revision"],
                "response": response,
            },
        ).json()["session"]

    revision_before = session["revision"]
    result = client.post(
        f"/api/guided-setup/sessions/{session['id']}/coverage",
        json={"expected_revision": revision_before},
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["coverage"]["profiles"]["solo_lead"]["ready"] is True
    assert payload["coverage"]["profiles"]["lead_quorum"]["ready"] is False
    assert payload["coverage"]["roles"]["team_lead"]["eligible_count"] == 1
    assert payload["recommendations"]["next_action"]["code"] == (
        "expand_quorum_diversity"
    )
    assert payload["preparation"]["ready_adapter_ids"] == [
        "codex_subscription"
    ]
    assert payload["selection_context"] == {
        "source": "contextual_model_selection",
        "catalog_content_hash": "fixture-catalog-hash",
        "run_profile": "solo_lead",
        "criticality": "medium",
        "data_class": "internal",
        "required_capabilities": [],
    }
    assert payload["mutation_policy"] == {
        "defaults_changed": False,
        "project_created": False,
        "preparation_persisted": False,
    }
    assert {row["role"] for row in selection_calls} == {
        "engineer",
        "quorum_auditor",
        "reviewer",
        "team_lead",
        "worker",
    }
    assert all(
        row["profiles"] is profiles
        and row["read_model"]["content_hash"] == "fixture-catalog-hash"
        for row in selection_calls
    )
    current = client.get(
        f"/api/guided-setup/sessions/{session['id']}"
    ).json()["session"]
    assert current["revision"] == revision_before
    adapter_step = next(
        row for row in current["steps"] if row["key"] == "adapter_setup"
    )
    assert adapter_step["status"] == "not_started"

    stale = client.post(
        f"/api/guided-setup/sessions/{session['id']}/coverage",
        json={"expected_revision": revision_before - 1},
    )
    assert stale.status_code == 409

    forged = client.post(
        f"/api/guided-setup/sessions/{session['id']}/coverage",
        json={
            "expected_revision": revision_before,
            "provider_evidence": {
                "codex_subscription": {"catalog": "passed"}
            },
        },
    )
    assert forged.status_code == 422


def test_guided_setup_project_proposal_is_server_side_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "user-config"
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))
    profiles = [{
        "id": "codex_subscription",
        "channel": "subscription",
        "adapter_type": "codex_cli",
        "config": {},
    }]
    monkeypatch.setattr(
        "api.routers.guided_setup.load_adapter_profiles",
        lambda: profiles,
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.build_machine_inventory",
        lambda **_kwargs: {
            "schema_version": "machine_doctor_v1",
            "scope": {
                "read_only": True,
                "secrets_read": False,
                "credentials_probed": False,
            },
            "runtimes": [
                {
                    "id": "python",
                    "requirement": "required",
                    "ready": True,
                    "installed": True,
                    "version": "3.12.10",
                    "minimum_version": "3.10",
                }
            ],
            "adapters": [
                {
                    "id": "codex_subscription",
                    "cli": {"installed": True, "version": "999.0.0"},
                    "authentication_status": "authenticated",
                    "health_status": "ok",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.build_canonical_provider_evidence",
        lambda *_args, **_kwargs: {
            "stage_evidence": {
                "codex_subscription": {
                    "authentication": "passed",
                    "catalog": "passed",
                    "health": "passed",
                    "contract": "passed",
                }
            }
        },
    )
    monkeypatch.setattr(
        "api.routers.guided_setup.get_current_model_catalog",
        lambda **_kwargs: {
            "schema_version": "model_catalog_read_model_v2",
            "content_hash": "project-catalog-hash",
            "candidates": [],
        },
    )
    selection_calls = []

    def fake_selection(_db_path: Path, **kwargs) -> dict:
        selection_calls.append(kwargs)
        role = kwargs["role"]
        return {
            "selection_version": "model_selection_v1",
            "canonical_role": role,
            "candidates": [
                _coverage_candidate(
                    role,
                    profile_id="codex_subscription",
                    perspective="openai",
                    pool=f"codex-{role}",
                )
            ],
        }

    monkeypatch.setattr(
        "api.routers.guided_setup.contextual_model_selection",
        fake_selection,
    )
    detected_targets = []

    def fake_detection(target: Path) -> dict:
        detected_targets.append(target)
        return {
            "schema_version": "ecosystem_registry_v1",
            "workspace_observed": False,
            "scan_truncated": False,
            "files_observed": 0,
            "ecosystems": [],
            "detected_ids": [],
            "support_claims": [],
            "commands_executed": False,
            "installation_performed": False,
            "mutated": False,
        }

    monkeypatch.setattr(
        "api.routers.guided_setup.detect_project_ecosystems",
        fake_detection,
    )
    client = TestClient(app, raise_server_exceptions=True)
    session = client.post(
        "/api/guided-setup/sessions",
        json={"scope": "project_setup", "subject_key": "project:portal"},
    ).json()["session"]
    project_answers = {
        **_answers(),
        "team_preference": "full_team",
    }
    needs = client.post(
        "/api/guided-setup/needs-assessment",
        json={"scope": "project_setup", "answers": project_answers},
    ).json()["submission"]
    for step_key, response in (
        (
            "project_identity",
            {"mode": "create", "name": "Portal", "path": ""},
        ),
        ("objective_profile", needs),
    ):
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "in_progress",
                "expected_revision": session["revision"],
            },
        ).json()["session"]
        session = client.patch(
            f"/api/guided-setup/sessions/{session['id']}/steps/{step_key}",
            json={
                "status": "passed",
                "expected_revision": session["revision"],
                "response": response,
            },
        ).json()["session"]

    revision_before = session["revision"]
    target = projects_root / "Portal"
    assert target.exists() is False
    response = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-proposal",
        json={
            "expected_revision": revision_before,
            "instructions": "Priorizar accesibilidad.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["proposal"]["save_gate"]["allowed"] is True
    assert payload["proposal"]["team"]["creation_order"] == [
        "role:team_lead",
        "role:engineer",
        "role:reviewer",
    ]
    assert payload["proposal"]["project"]["instructions_preview"] == (
        "Priorizar accesibilidad."
    )
    assert payload["selection_context"]["catalog_content_hash"] == (
        "project-catalog-hash"
    )
    assert {row["role"] for row in selection_calls} == {
        "engineer",
        "quorum_auditor",
        "reviewer",
        "team_lead",
        "worker",
    }
    assert detected_targets == [target.resolve()]
    assert target.exists() is False
    assert projects_root.exists() is False
    current = client.get(
        f"/api/guided-setup/sessions/{session['id']}"
    ).json()["session"]
    assert current["revision"] == revision_before
    assert payload["mutation_policy"] == {
        "filesystem_mutated": False,
        "database_mutated": False,
        "project_created": False,
        "agents_created": False,
        "wakeups_created": False,
    }

    projects_root.mkdir()
    preflight_body = {
        "expected_revision": revision_before,
        "instructions": "Priorizar accesibilidad.",
        "proposal_hash": payload["proposal"]["proposal_hash"],
    }
    preflight = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json=preflight_body,
    )
    assert preflight.status_code == 200
    preflight_payload = preflight.json()
    assert preflight_payload["preflight"]["summary"]["status"] == "no_go"
    assert [
        row["id"] for row in preflight_payload["execution_plan"]["actions"]
    ] == ["local_fixture"]
    assert [
        (row["gate"], row["code"], row["next_action"])
        for row in preflight_payload["preflight"]["summary"]["blockers"]
    ] == [
        (
            "proportional_fixture",
            "software_fixture_required",
            "run_proportional_fixture",
        )
    ]
    assert preflight_payload["server_evidence"][
        "proposal_source"
    ] == "recomposed_from_session"
    assert preflight_payload["server_evidence"][
        "path_source"
    ] == "server_filesystem_observation"
    assert preflight_payload["mutation_policy"] == {
        "read_only_observation": True,
        "filesystem_mutated": False,
        "database_mutated": False,
        "tests_executed": False,
        "remote_probes_executed": False,
        "inference_attempted": False,
        "quota_consumed": False,
        "version_commands_may_execute": True,
    }
    assert target.exists() is False
    premature_commit_body = {
        "expected_revision": revision_before,
        "instructions": "Priorizar accesibilidad.",
        "proposal_hash": payload["proposal"]["proposal_hash"],
        "confirm": True,
    }
    premature_commit = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-commit",
        json=premature_commit_body,
    )
    assert premature_commit.status_code == 409
    assert premature_commit.json()["detail"] == (
        "guided_setup_project_preflight_receipt_required"
    )
    assert target.exists() is False

    execution_body = {
        **preflight_body,
        "preflight_hash": preflight_payload["preflight"]["preflight_hash"],
        "execution_plan_hash": preflight_payload["execution_plan"]["plan_hash"],
        "confirm_local_fixture": False,
        "confirm_remote_probe": False,
        "acknowledge_possible_quota": False,
    }
    missing_consent = client.post(
        (
            f"/api/guided-setup/sessions/{session['id']}"
            "/project-preflight-execute"
        ),
        json=execution_body,
    )
    assert missing_consent.status_code == 422
    assert missing_consent.json()["detail"] == (
        "guided_setup_local_fixture_consent_required"
    )
    assert target.exists() is False

    execution_calls = []

    def fake_execution(plan: dict, **kwargs) -> dict:
        execution_calls.append({"plan": plan, **kwargs})
        return execute_preflight_plan(
            plan,
            plan_hash=kwargs["plan_hash"],
            confirm_local_fixture=kwargs["confirm_local_fixture"],
            confirm_remote_probe=kwargs["confirm_remote_probe"],
            acknowledge_possible_quota=kwargs[
                "acknowledge_possible_quota"
            ],
            fixture_runner=lambda case_id, _timeout: {
                "schema_version": "ecosystem_validation_receipt_v1",
                "cases": [{"id": case_id, "status": "passed"}],
                "summary": {"total": 1, "passed": 1},
            },
            remote_probe_runner=kwargs["remote_probe_runner"],
        )

    monkeypatch.setattr(
        "api.routers.guided_setup.execute_project_preflight_plan",
        fake_execution,
    )
    executed = client.post(
        (
            f"/api/guided-setup/sessions/{session['id']}"
            "/project-preflight-execute"
        ),
        json={**execution_body, "confirm_local_fixture": True},
    )
    assert executed.status_code == 200
    executed_payload = executed.json()
    assert len(execution_calls) == 1
    assert execution_calls[0]["plan_hash"] == (
        preflight_payload["execution_plan"]["plan_hash"]
    )
    assert execution_calls[0]["confirm_local_fixture"] is True
    assert executed_payload["post_execution_preflight"]["summary"][
        "status"
    ] == "go"
    assert executed_payload["post_execution_preflight"]["summary"][
        "enter_project_allowed"
    ] is False
    assert executed_payload["persistence"]["persisted"] is True
    assert executed_payload["persistence"]["idempotent_replay"] is False
    assert executed_payload["persistence"]["required_before_commit"] is False
    assert executed_payload["persistence"]["durable_receipt"]["status"] == "go"
    fixture_refs = executed_payload["persistence"]["durable_receipt"][
        "fixture_evidence_refs"
    ]
    resolved_preflight = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json={
            **preflight_body,
            "fixture_evidence_refs": fixture_refs,
        },
    )
    assert resolved_preflight.status_code == 200
    assert resolved_preflight.json()["preflight"]["summary"]["status"] == "go"

    replayed_execution = client.post(
        (
            f"/api/guided-setup/sessions/{session['id']}"
            "/project-preflight-execute"
        ),
        json=execution_body,
    )
    assert replayed_execution.status_code == 200
    assert replayed_execution.json()["persistence"][
        "idempotent_replay"
    ] is True
    assert len(execution_calls) == 1
    assert target.exists() is False
    current_after_execution = client.get(
        f"/api/guided-setup/sessions/{session['id']}"
    ).json()["session"]
    assert current_after_execution["revision"] == revision_before

    stale_execution = client.post(
        (
            f"/api/guided-setup/sessions/{session['id']}"
            "/project-preflight-execute"
        ),
        json={
            **execution_body,
            "preflight_hash": "f" * 64,
            "confirm_local_fixture": True,
        },
    )
    assert stale_execution.status_code == 409
    assert stale_execution.json()["detail"] == (
        "guided_setup_project_preflight_stale"
    )

    forged_execution = client.post(
        (
            f"/api/guided-setup/sessions/{session['id']}"
            "/project-preflight-execute"
        ),
        json={
            **execution_body,
            "confirm_local_fixture": True,
            "receipt": {"status": "passed"},
        },
    )
    assert forged_execution.status_code == 422

    forged_preflight = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json={
            **preflight_body,
            "inventory": {"adapters": []},
            "path_observation": {"confined_to_projects_root": True},
            "fixture_evidence": [{"status": "passed"}],
        },
    )
    assert forged_preflight.status_code == 422

    unknown_fixture = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json={
            **preflight_body,
            "fixture_evidence_refs": ["sha256:" + ("b" * 64)],
        },
    )
    assert unknown_fixture.status_code == 422
    assert unknown_fixture.json()["detail"] == (
        "guided_setup_project_fixture_evidence_not_persisted"
    )

    invalid_fixture = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json={
            **preflight_body,
            "fixture_evidence_refs": ["../../forged.json"],
        },
    )
    assert invalid_fixture.status_code == 422
    assert invalid_fixture.json()["detail"] == (
        "guided_setup_project_fixture_evidence_ref_invalid"
    )

    stale_preflight = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-preflight",
        json={**preflight_body, "proposal_hash": "f" * 64},
    )
    assert stale_preflight.status_code == 409
    assert stale_preflight.json()["detail"] == (
        "guided_setup_project_proposal_stale"
    )

    research_session = client.post(
        "/api/guided-setup/sessions",
        json={
            "scope": "project_setup",
            "subject_key": "project:research-fixture",
        },
    ).json()["session"]
    research_needs = client.post(
        "/api/guided-setup/needs-assessment",
        json={
            "scope": "project_setup",
            "answers": {
                **_answers(),
                "goal": "Analizar una empresa de limpieza",
                "objective_kind": "research",
                "languages": ["unknown"],
            },
        },
    ).json()["submission"]
    for step_key, response_body in (
        (
            "project_identity",
            {"mode": "create", "name": "Research", "path": ""},
        ),
        ("objective_profile", research_needs),
    ):
        research_session = client.patch(
            (
                "/api/guided-setup/sessions/"
                f"{research_session['id']}/steps/{step_key}"
            ),
            json={
                "status": "in_progress",
                "expected_revision": research_session["revision"],
            },
        ).json()["session"]
        research_session = client.patch(
            (
                "/api/guided-setup/sessions/"
                f"{research_session['id']}/steps/{step_key}"
            ),
            json={
                "status": "passed",
                "expected_revision": research_session["revision"],
                "response": response_body,
            },
        ).json()["session"]
    research_proposal = client.post(
        (
            "/api/guided-setup/sessions/"
            f"{research_session['id']}/project-proposal"
        ),
        json={"expected_revision": research_session["revision"]},
    ).json()["proposal"]
    cross_session_evidence = client.post(
        (
            "/api/guided-setup/sessions/"
            f"{research_session['id']}/project-preflight"
        ),
        json={
            "expected_revision": research_session["revision"],
            "proposal_hash": research_proposal["proposal_hash"],
            "fixture_evidence_refs": fixture_refs,
        },
    )
    assert cross_session_evidence.status_code == 422
    assert cross_session_evidence.json()["detail"] == (
        "guided_setup_project_fixture_evidence_not_persisted"
    )
    research_preflight = client.post(
        (
            "/api/guided-setup/sessions/"
            f"{research_session['id']}/project-preflight"
        ),
        json={
            "expected_revision": research_session["revision"],
            "proposal_hash": research_proposal["proposal_hash"],
        },
    )
    assert research_preflight.status_code == 200
    research_result = research_preflight.json()["preflight"]
    assert research_result["summary"]["status"] == "go"
    assert research_result["summary"]["commit_allowed"] is True
    assert research_result["summary"]["enter_project_allowed"] is False
    assert research_result["fixture_policy"]["kind"] == (
        "research_evidence_contract"
    )
    assert research_result["scope"]["tests_executed"] is False

    stale_hash = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-commit",
        json={
            "expected_revision": revision_before,
            "instructions": "Priorizar accesibilidad.",
            "proposal_hash": "f" * 64,
            "confirm": True,
        },
    )
    assert stale_hash.status_code == 409
    assert stale_hash.json()["detail"] == (
        "guided_setup_project_proposal_stale"
    )
    assert target.exists() is False

    commit_body = premature_commit_body
    guided_db = config_dir / "guided_setup.db"
    with sqlite3.connect(guided_db) as conn:
        original_content = conn.execute(
            """
            SELECT content_json
            FROM guided_setup_project_preflight_artifacts
            WHERE session_id = ? AND reference = ?
            """,
            (session["id"], fixture_refs[0]),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE guided_setup_project_preflight_artifacts
            SET content_json = '{}'
            WHERE session_id = ? AND reference = ?
            """,
            (session["id"], fixture_refs[0]),
        )
    corrupt_evidence = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-commit",
        json=commit_body,
    )
    assert corrupt_evidence.status_code == 409
    assert corrupt_evidence.json()["detail"] == (
        "guided_setup_project_fixture_evidence_corrupt"
    )
    assert target.exists() is False
    with sqlite3.connect(guided_db) as conn:
        conn.execute(
            """
            UPDATE guided_setup_project_preflight_artifacts
            SET content_json = ?
            WHERE session_id = ? AND reference = ?
            """,
            (original_content, session["id"], fixture_refs[0]),
        )
    committed = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-commit",
        json=commit_body,
    )
    assert committed.status_code == 200
    committed_payload = committed.json()
    assert committed_payload["idempotent_replay"] is False
    assert Path(committed_payload["result"]["database"]).is_file()
    with sqlite3.connect(committed_payload["result"]["database"]) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM agents"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status = 'queued'"
        ).fetchone()[0] == 1

    replay = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-commit",
        json=commit_body,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    stale = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-proposal",
        json={"expected_revision": revision_before - 1},
    )
    assert stale.status_code == 409
    forged = client.post(
        f"/api/guided-setup/sessions/{session['id']}/project-proposal",
        json={
            "expected_revision": revision_before,
            "inventory": {"adapters": []},
        },
    )
    assert forged.status_code == 422


def test_guided_setup_project_identity_is_confined_to_projects_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_root = tmp_path / "projects"
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))

    inside = _resolve_project_identity(
        {"mode": "create", "name": "Portal", "path": ""}
    )
    assert Path(inside["target"]) == (projects_root / "Portal").resolve()
    projects_root.mkdir()
    create_observation = _observe_project_path(inside)
    assert create_observation == {
        "schema_version": "guided_setup_project_path_observation_v1",
        "mode": "create",
        "target_exists": False,
        "target_is_dir": False,
        "target_readable": False,
        "target_writable": False,
        "parent_exists": True,
        "parent_writable": True,
        "confined_to_projects_root": True,
    }

    imported = projects_root / "Imported"
    imported.mkdir()
    import_identity = _resolve_project_identity(
        {
            "mode": "import",
            "name": "Imported",
            "path": str(imported),
        }
    )
    import_observation = _observe_project_path(import_identity)
    assert import_observation["target_exists"] is True
    assert import_observation["target_is_dir"] is True
    assert import_observation["target_readable"] is True
    assert import_observation["target_writable"] is True
    assert import_observation["confined_to_projects_root"] is True

    with pytest.raises(ValueError, match="outside_projects_root"):
        _resolve_project_identity(
            {
                "mode": "import",
                "name": "Outside",
                "path": str(tmp_path.parent / "outside"),
            }
        )

    with pytest.raises(ValueError, match="outside_projects_root"):
        _resolve_project_identity(
            {
                "mode": "import",
                "name": "Root",
                "path": str(projects_root),
            }
        )
