from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import api.main as main_mod
import api.routers.workspace as workspace_mod
import aiteam.project_adapters as project_adapters
import aiteam.model_selection_context as selection_context_mod
import aiteam.model_selection_intent as selection_intent_mod
from api.routers.workspace import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.user_config import model_options, record_model_health


@pytest.fixture(autouse=True)
def _verified_api_models(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path / "projects"))
    for profile_id in ("openai_api", "anthropic_api"):
        for option in model_options().get(profile_id, []):
            record_model_health(
                profile_id, str(option["value"]), available=True, reason="workspace fixture"
            )
    original_selection = selection_context_mod.contextual_model_selection

    def authority_enabled_selection(*args, **kwargs):
        result = original_selection(*args, **kwargs)
        role = str(kwargs.get("role") or "")
        lane = {
            "lead": "lead_ready",
            "team_lead": "tier1_support",
            "lead_executor": "tier1_support",
            "architect": "tier1_support",
            "quorum_auditor": "quorum_ready",
        }.get(role)
        if lane is None:
            return result
        for candidate in result.get("candidates") or ():
            candidate["tier1_authority"] = {
                "policy_version": "tier_role_coverage_v1",
                "lane": lane,
                "status": "enabled",
                "enabled": True,
                "reason_code": "workspace_fixture",
            }
            candidate["tier1_authority_gate"] = {
                "applicable": True,
                "allowed": True,
                "policy_version": "tier_role_coverage_v1",
                "lane": lane,
                "code": "tier1_authority_verified",
                "reason": "workspace fixture",
            }
            compatibility = candidate.get("contextual_compatibility") or {}
            preference = candidate.get("owner_preference") or {}
            selectable = (
                ((candidate.get("states") or {}).get("selectable") or {}).get(
                    "value"
                )
                is True
            )
            candidate["owner_selectable"] = bool(
                compatibility.get("allowed") is True
                and preference.get("state") != "archived"
                and selectable
            )
            score = candidate.get("selection_score") or {}
            gates = score.get("hard_gates") or {}
            if "tier1_authority" in gates:
                gates["tier1_authority"] = {
                    "passed": True,
                    "reason": "tier1_authority_verified",
                    "source": "tier_role_coverage_v1",
                }
            score["auto_ineligible_reasons"] = [
                reason
                for reason in score.get("auto_ineligible_reasons") or ()
                if "tier1_authority" not in str(reason)
            ]
            # Esta suite prueba Workspace, no recalibra modelos. La proyección
            # hermética declara expresamente la elegibilidad automática para
            # que conexión/health nunca se confundan con calibración real.
            score["auto_eligible"] = True
            candidate["selection_score"] = score
        return result

    monkeypatch.setattr(
        selection_context_mod,
        "contextual_model_selection",
        authority_enabled_selection,
    )
    monkeypatch.setattr(
        selection_intent_mod,
        "contextual_model_selection",
        authority_enabled_selection,
    )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _full_client() -> TestClient:
    """Client with both workspace and agents routers (for reconcile tests)."""
    from api.main import app
    return TestClient(app)


def test_bootstrap_lead_uses_governed_default_only_without_owner_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    profile = {"id": "profile-a", "adapter_type": "subscription_cli"}
    monkeypatch.setattr(workspace_mod, "project_profiles", lambda runtime_dir: [profile])
    observed: dict[str, object] = {}

    def projection(*args, **kwargs):
        observed.update(kwargs)
        return {
            "schema_version": "model_catalog_read_model_v1",
            "score_version": "model_role_score_v1",
            "canonical_role": "lead",
            "default": {"candidate_id": "candidate:a"},
            "candidates": [{
                "candidate_id": "candidate:a",
                "identity": {"profile_id": "profile-a", "model_id": "model-a"},
                "rank": 1,
                "selection_reason": "hermetic_bootstrap_canary",
                "selection_score": {
                    "score_version": "model_role_score_v1",
                    "score": 90,
                    "auto_eligible": True,
                    "hard_gates": {"calibrated": {"passed": True}},
                },
                "owner_selectable": True,
                "tier1_authority": {
                    "policy_version": "tier_role_coverage_v1",
                    "lane": "lead_ready",
                    "status": "enabled",
                    "enabled": True,
                },
            }],
        }

    monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "auto")
    monkeypatch.setattr(
        "aiteam.model_selection_context.contextual_model_selection", projection
    )

    workspace_mod._initialize_project_runtime(
        project, run_profile="solo_lead", data_class="confidential"
    )

    assert observed["run_profile"] == "solo_lead"
    assert observed["data_class"] == "confidential"
    db = project / ".aiteam" / "aiteam.db"
    with sqlite3.connect(db) as conn:
        adapter_type, raw = conn.execute(
            "SELECT adapter_type, adapter_config_json FROM agents WHERE id='role:lead'"
        ).fetchone()
        snapshot = conn.execute(
            "SELECT selection_scope, auto_applied FROM model_role_score_snapshots"
        ).fetchone()
    assert adapter_type == "subscription_cli"
    assert json.loads(raw)["selection_intent"]["mode"] == "default"
    assert snapshot == ("bootstrap:new-agent:role:lead", 1)


def test_bootstrap_lead_auto_without_winner_aborts_before_agent_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        workspace_mod,
        "project_profiles",
        lambda runtime_dir: [{"id": "profile-a", "adapter_type": "subscription_cli"}],
    )
    monkeypatch.setattr(
        workspace_mod,
        "choose_adapter_for_new_slot",
        lambda *args, **kwargs: project_adapters._unresolved_model_default(
            "no_auto_eligible_candidate"
        ),
    )

    with pytest.raises(ValueError, match="No auto-eligible Lead model"):
        workspace_mod._initialize_project_runtime(project, run_profile="solo_lead")

    db = project / ".aiteam" / "aiteam.db"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT 1 FROM agents WHERE id='role:lead'").fetchone() is None


def test_workspace_endpoint_clears_deleted_workspace(tmp_path: Path) -> None:
    deleted = tmp_path / "deleted-project"
    previous = get_current_workspace()
    set_current_workspace(deleted)
    try:
        response = _client().get("/api/workspace")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["workspace"] == ""
    assert payload["reason"] == "workspace_missing"


def test_workspace_endpoint_reports_missing_project_db(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    response = _client().get("/api/workspace", headers={"x-aiteam-workspace": str(project)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is False
    assert payload["workspace"] == ""
    assert payload["reason"] == "workspace_db_missing"


def test_set_workspace_never_creates_or_initializes_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    missing = projects_root / "Missing"
    uninitialized = projects_root / "Personal"
    uninitialized.mkdir()
    marker = uninitialized / "keep.txt"
    marker.write_text("personal", encoding="utf-8")
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))

    missing_response = _client().post(
        "/api/workspace",
        json={"path": str(missing)},
    )
    uninitialized_response = _client().post(
        "/api/workspace",
        json={"path": str(uninitialized)},
    )

    assert missing_response.status_code == 404
    assert uninitialized_response.status_code == 409
    assert not missing.exists()
    assert marker.read_text(encoding="utf-8") == "personal"
    assert not (uninitialized / ".aiteam").exists()


def test_legacy_project_creation_route_is_absent_and_side_effect_free(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post("/api/projects/new", json={"name": "Demo"})
    finally:
        set_current_workspace(previous)

    assert response.status_code == 404
    assert not (tmp_path / "projects").exists()


def test_reconcile_endpoint_is_idempotent_and_returns_repaired(tmp_path: Path, monkeypatch) -> None:
    """POST /api/agents/reconcile remains idempotent for an initialized fixture."""
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        workspace_path = tmp_path / "projects" / "Reconcile"
        workspace_path.mkdir(parents=True)
        project_adapters.write_project_adapter_policy(
            workspace_path / ".aiteam",
            profile_ids=["openai_api"],
        )
        workspace_mod._initialize_project_runtime(
            workspace_path,
            lead_adapter_profile_id="openai_api",
            lead_model=str(model_options()["openai_api"][0]["value"]),
        )
        set_current_workspace(workspace_path)

        client = _full_client()
        # First call: agents already bootstrapped — repaired list may be empty or small
        r1 = client.post("/api/agents/reconcile")
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["success"] is True
        assert isinstance(body1["repaired"], list)

        # Second call: fully idempotent — nothing new to repair
        r2 = client.post("/api/agents/reconcile")
        assert r2.status_code == 200
        assert r2.json()["repaired"] == []

        # All minimum agents must still be present
        db_path = workspace_path / ".aiteam" / "aiteam.db"
        with sqlite3.connect(str(db_path)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert {"role:lead", "role:file_scout", "role:web_scout", "role:context_curator"} <= ids
    finally:
        set_current_workspace(previous)


def test_delete_current_project_requires_delete_confirmation(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path))
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        bad = _client().request("DELETE", "/api/projects/current", json={"confirmation": "delete"})
        ok = _client().request("DELETE", "/api/projects/current", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    assert bad.status_code == 400
    assert ok.status_code == 200
    assert ok.json()["configured"] is False
    assert not project.exists()


def test_delete_current_project_post_fallback(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path))
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        response = _client().post("/api/projects/current/delete", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert not project.exists()


def test_delete_current_project_never_moves_locked_folder_to_tombstone(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path))

    def fake_rmtree(path: Path) -> None:
        raise PermissionError(f"locked:{Path(path).name}")

    monkeypatch.setattr(workspace_mod, "_rmtree_project_tree", fake_rmtree)
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        response = _client().post("/api/projects/current/delete", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    assert response.status_code == 423
    assert "will not rename it or schedule cleanup" in response.json()["detail"]
    assert project.exists()
    assert not list(tmp_path.glob(".aiteam-deleted-*"))


def test_pytest_run_never_deletes_real_persisted_workspace(tmp_path):
    """Regresión del 2026-07-15: correr la suite borraba el
    runtime/current_workspace.json REAL — clear_persisted_workspace no tenía
    el guard de persistencia-deshabilitada que sí tienen persist y load."""
    from api.utils import _workspace_state_path, clear_persisted_workspace

    state_path = _workspace_state_path()
    existed_before = state_path.exists()
    payload_before = state_path.read_text(encoding="utf-8") if existed_before else None

    clear_persisted_workspace()  # bajo pytest debe ser un no-op

    assert state_path.exists() == existed_before
    if payload_before is not None:
        assert state_path.read_text(encoding="utf-8") == payload_before
