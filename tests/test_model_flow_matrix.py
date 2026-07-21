from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from api.routers import user_adapters as adapters_router
from aiteam.model_flow_matrix import audit_builtin_model_flows, prepared_builtin_profiles
from aiteam.user_config import load_adapter_profiles


def test_every_builtin_model_has_hermetic_positive_and_negative_coverage() -> None:
    report = audit_builtin_model_flows()

    assert report["ok"] is True, report["failures"]
    assert report["profile_count"] == 12
    assert report["model_count"] == 47
    assert report["positive_cell_count"] > report["model_count"]
    assert report["negative_cell_count"] > report["model_count"]


def test_models_get_and_compatibility_post_return_same_decision_for_every_model(
    monkeypatch,
) -> None:
    profiles = prepared_builtin_profiles()
    by_profile = {str(profile["id"]): profile for profile in profiles}
    monkeypatch.setattr(adapters_router, "load_adapter_profiles", lambda: profiles)
    monkeypatch.setattr(
        adapters_router,
        "model_options_for_role",
        lambda profile_id, _role, executable_only=False: by_profile[profile_id]["model_options"],
    )
    client = TestClient(app, raise_server_exceptions=True)

    for profile in profiles:
        for model in profile["model_options"]:
            role = next(iter(model.get("best_for") or []), "file_scout")
            query = {
                "profile_id": profile["id"],
                "role": role,
                "run_profile": "full_team",
                "criticality": "medium",
                "data_class": "public",
            }
            get_response = client.get("/api/user-adapters/models", params=query)
            post_response = client.post(
                "/api/user-adapters/compatibility",
                json={**query, "model": model["value"], "required_capabilities": []},
            )

            assert get_response.status_code == 200
            assert post_response.status_code == 200
            get_option = next(
                item for item in get_response.json()["options"]
                if item["value"] == model["value"]
            )
            assert get_option["compatibility"] == post_response.json()["compatibility"]


def test_onboarding_probe_promotes_each_api_model_independently(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))

    def fake_catalog(profile):
        return {
            "status": "current",
            "source": f"fixture:{profile['id']}",
            "models": [item["value"] for item in profile.get("model_options") or []],
        }

    def fake_test(profile, *, model=None):
        return {
            "status": "ok",
            "reason": "live_test_completed",
            "detail": "fixture contract passed",
            "tested_model": model,
        }

    monkeypatch.setattr(adapters_router, "_discover_api_catalog", fake_catalog)
    monkeypatch.setattr(adapters_router, "_test_profile", fake_test)
    client = TestClient(app, raise_server_exceptions=True)
    api_profiles = [
        profile for profile in load_adapter_profiles()
        if profile.get("channel") == "api"
    ]

    for profile in api_profiles:
        profile_id = profile["id"]
        expected = [item["value"] for item in profile["model_options"]]
        for index, model in enumerate(expected):
            response = client.post(
                "/api/user-adapters/test",
                json={"profile_id": profile_id, "model": model},
            )
            assert response.status_code == 200
            assert response.json()["tested_model"] == model
            refreshed = next(
                item for item in load_adapter_profiles() if item["id"] == profile_id
            )
            by_model = {item["value"]: item for item in refreshed["model_options"]}
            assert by_model[model]["selectable"] is True
            for untested in expected[index + 1:]:
                assert by_model[untested]["selectable"] is False
