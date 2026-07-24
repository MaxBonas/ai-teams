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
from api.routers.workspace import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.user_config import model_options, record_model_health
from aiteam.model_catalog_service import get_current_model_catalog


@pytest.fixture(autouse=True)
def _verified_api_models(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path / "projects"))
    for profile_id in ("openai_api", "anthropic_api"):
        for option in model_options().get(profile_id, []):
            record_model_health(
                profile_id, str(option["value"]), available=True, reason="workspace fixture"
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


def test_create_project_requires_adapter_profile(tmp_path: Path, monkeypatch) -> None:
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

    assert response.status_code == 400
    assert "adapter" in response.json()["detail"]


def test_create_project_auto_without_lead_winner_returns_422_and_removes_partial_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "Ai_Teams"
    projects_root = tmp_path / "projects"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(projects_root))
    monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "auto")
    monkeypatch.setattr(
        workspace_mod,
        "choose_adapter_for_new_slot",
        lambda *args, **kwargs: project_adapters._unresolved_model_default(
            "no_auto_eligible_candidate"
        ),
    )
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "NoWinner", "adapter_profile_ids": ["openai_api"]},
        )
    finally:
        set_current_workspace(previous)

    assert response.status_code == 422
    assert "No auto-eligible Lead model" in response.json()["detail"]
    assert not (projects_root / "NoWinner").exists()


def test_create_project_warns_when_no_selected_adapter_connected(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "Demo", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    assert payload["adapter_warning"]
    assert "credenciales" in payload["adapter_warning"]
    # The warning is also visible in the intake thread as a system comment.
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = 'issue:intake' AND body LIKE '%credenciales%'"
        ).fetchone()
    assert row[0] == 1


def test_create_project_blocks_unconnected_when_required(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setenv("AITEAM_REQUIRE_CONNECTED_ADAPTER", "1")
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "Demo", "adapter_profile_ids": ["openai_api"]},
        )
    finally:
        set_current_workspace(previous)

    assert response.status_code == 400
    assert "credenciales" in response.json()["detail"]


def test_create_project_no_warning_with_stored_secret(tmp_path: Path, monkeypatch) -> None:
    from aiteam.user_config import store_secret

    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="openai", name="default", secret="sk-test")
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "Demo", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    assert payload["adapter_warning"] is None


def test_create_project_bootstraps_lead_with_selected_adapter(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "Demo", "initial_task": "Build it", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT adapter_type, adapter_config_json FROM agents WHERE id = 'role:lead'").fetchone()
    assert row[0] == "openai_api"
    assert '"profile_id": "openai_api"' in row[1]


def test_create_project_persists_lead_quorum_and_bootstraps_auditors(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "Quorum",
                "initial_task": "Diseña el plan crítico",
                "adapter_profile_ids": ["openai_api"],
                "run_profile": "lead_quorum",
            },
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    assert payload["run_profile"] == "lead_quorum"
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        goal_metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM goals WHERE id = 'goal:intake'"
        ).fetchone()[0])
        issue_metadata = json.loads(conn.execute(
            "SELECT metadata_json FROM issues WHERE id = 'issue:intake'"
        ).fetchone()[0])
        auditor_ids = {
            row[0]
            for row in conn.execute(
                "SELECT id FROM agents WHERE role = 'quorum_auditor'"
            ).fetchall()
        }
        wake_payload = json.loads(conn.execute(
            "SELECT payload_json FROM wakeup_requests WHERE idempotency_key = ?",
            ("bootstrap:issue:intake:role:lead",),
        ).fetchone()[0])

    assert goal_metadata["profile"] == "lead_quorum"
    assert issue_metadata["profile"] == "lead_quorum"
    assert wake_payload["profile"] == "lead_quorum"
    assert auditor_ids == {"role:quorum_auditor_1", "role:quorum_auditor_2"}


def test_create_solo_lead_project_bootstraps_only_the_lead(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "Solo",
                "adapter_profile_ids": ["openai_api"],
                "run_profile": "solo_lead",
                "initial_task": (
                    "Estudio para una empresa de limpieza: crear formularios "
                    "para analizar sus necesidades y operaciones."
                ),
            },
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        agents = conn.execute("SELECT id, role FROM agents ORDER BY id").fetchall()
        metadata = json.loads(
            conn.execute(
                "SELECT metadata_json FROM issues WHERE id='issue:intake'"
            ).fetchone()[0]
        )
    assert agents == [("role:lead", "lead")]
    assert metadata["objective_classification"]["kind"] == "research"


def test_create_project_uses_user_selected_lead_profile(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health(
        "codex_subscription", "gpt-5.6-sol",
        available=True, reason="test runtime inventory",
    )
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    candidate_id = next(
        item["candidate_id"]
        for item in get_current_model_catalog(db_paths=())["candidates"]
        if item["identity"]["profile_id"] == "anthropic_api"
        and item["identity"]["model_id"] == "claude-opus-4-8"
    )
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "AnthropicLead",
                "adapter_profile_ids": ["codex_subscription", "anthropic_api"],
                "lead_adapter_profile_id": "anthropic_api",
                "lead_model": "claude-opus-4-8",
                "lead_candidate_id": candidate_id,
                "run_profile": "lead_quorum",
            },
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        lead = conn.execute(
            "SELECT adapter_config_json, metadata_json FROM agents WHERE id='role:lead'"
        ).fetchone()
        codex_auditor = conn.execute(
            """
            SELECT id FROM agents
            WHERE role='quorum_auditor'
              AND json_extract(adapter_config_json, '$.profile_id')='codex_subscription'
            """
        ).fetchone()

    lead_config = json.loads(lead["adapter_config_json"])
    assert lead_config["profile_id"] == "anthropic_api"
    assert lead_config["model"] == "claude-opus-4-8"
    assert lead_config["selection_intent"] == {
        "schema_version": "model_selection_intent_v1",
        "mode": "owner_explicit",
        "source": "onboarding_model_role_selector",
        "candidate_id": candidate_id,
    }
    assert json.loads(lead["metadata_json"])["selected_by_user"] is True
    assert codex_auditor is not None


def test_create_project_rejects_forged_lead_candidate_id(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "ForgedCandidate",
                "adapter_profile_ids": ["anthropic_api"],
                "lead_adapter_profile_id": "anthropic_api",
                "lead_model": "claude-opus-4-8",
                "lead_candidate_id": "model-candidate:forged",
                "run_profile": "solo_lead",
            },
        )
    finally:
        set_current_workspace(previous)

    assert response.status_code == 422
    assert "candidate_id does not match" in response.json()["detail"]
    assert not (source_root / "ForgedCandidate").exists()


def test_create_project_rejects_lead_profile_outside_project_connections(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "InvalidLead",
                "adapter_profile_ids": ["anthropic_api"],
                "lead_adapter_profile_id": "codex_subscription",
            },
        )
    finally:
        set_current_workspace(previous)

    assert response.status_code == 400
    assert not (source_root / "InvalidLead").exists()


def test_create_project_rejects_unknown_run_profile(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={
                "name": "Invalid",
                "adapter_profile_ids": ["openai_api"],
                "run_profile": "maximum_magic",
            },
        )
    finally:
        set_current_workspace(previous)

    assert response.status_code == 422
    assert not (source_root / "Invalid").exists()


def test_create_project_bootstraps_minimum_org_chart(tmp_path: Path, monkeypatch) -> None:
    """Project creation must immediately create the full minimum org chart.

    Minimum roster:
      Tier 1 — role:lead
      Tier 3 — role:file_scout, role:web_scout, role:context_curator
    All must exist in the DB right after /api/projects/new, before any executor run.
    """
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        response = _client().post(
            "/api/projects/new",
            json={"name": "OrgChart", "adapter_profile_ids": ["openai_api"]},
        )
        payload = response.json()
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    db_path = Path(payload["workspace"]) / ".aiteam" / "aiteam.db"
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}

    MINIMUM_AGENTS = {
        "role:lead",
        "role:file_scout",
        "role:web_scout",
        "role:context_curator",
    }
    assert MINIMUM_AGENTS <= ids, f"Missing agents: {MINIMUM_AGENTS - ids}"


def test_reconcile_endpoint_is_idempotent_and_returns_repaired(tmp_path: Path, monkeypatch) -> None:
    """POST /api/agents/reconcile must be callable after project creation and be idempotent."""
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setattr(main_mod, "PROJECT_ROOT", source_root)
    previous = get_current_workspace()
    set_current_workspace(source_root)
    try:
        # Create project (sets current workspace to new project dir)
        resp = _client().post(
            "/api/projects/new",
            json={"name": "Reconcile", "adapter_profile_ids": ["openai_api"]},
        )
        assert resp.status_code == 200
        workspace_path = Path(resp.json()["workspace"])
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


def test_delete_current_project_moves_locked_folder_to_tombstone(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "Ai_Teams"
    source_root.mkdir()
    project = tmp_path / "Demo"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    (runtime / "aiteam.db").write_text("", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "PROJECT_ROOT", source_root)
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path))

    def fake_rmtree(path: Path) -> None:
        if Path(path) == project:
            raise PermissionError("locked")
        raise PermissionError("still locked")

    monkeypatch.setattr(workspace_mod, "_rmtree_project_tree", fake_rmtree)
    previous = get_current_workspace()
    set_current_workspace(project)
    try:
        response = _client().post("/api/projects/current/delete", json={"confirmation": "DELETE"})
    finally:
        set_current_workspace(previous)

    payload = response.json()
    assert response.status_code == 200
    assert payload["deleted"] is True
    assert payload["cleanup_pending"] is True
    assert payload["reason"] == "moved_to_tombstone"
    assert not project.exists()
    assert Path(payload["cleanup_path"]).exists()


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
