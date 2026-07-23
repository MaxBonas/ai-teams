from __future__ import annotations

from copy import deepcopy
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api.routers import model_catalog as catalog_router
from api.routers import user_adapters as adapters_router
from aiteam.model_catalog_api import (
    filter_catalog_candidates,
    rank_catalog_candidates_for_role,
    summarize_catalog_providers,
)
from aiteam.model_role_scoring import MODEL_ROLE_SCORE_VERSION


def _score(candidate_id: str, value: float, *, eligible: bool, reason: str) -> dict:
    return {
        "score_version": MODEL_ROLE_SCORE_VERSION,
        "candidate_id": candidate_id,
        "canonical_role": "reviewer",
        "score": value,
        "score_range": {"minimum": value, "maximum": value},
        "auto_eligible": eligible,
        "auto_ineligible_reasons": [] if eligible else [reason],
        "confidence": {
            "value": 100 if eligible else 40,
            "evidence_value": 100 if eligible else 40,
            "evidence_rank": 100 if eligible else 40,
        },
        "breakdown": {"quality": {"value": value}},
        "tie_break": {
            "evidence_rank": 100 if eligible else 40,
            "quality": value,
            "economy_comparison_group": None,
            "economic_burden": None,
            "speed_comparison_group": None,
            "latency_ms": None,
        },
        "hard_gates": {},
        "rollout": "shadow_only",
    }


def _candidate(
    candidate_id: str,
    *,
    profile_id: str,
    model: str,
    provider: str,
    channel: str,
    tier: str,
    score: float,
    eligible: bool,
    compatibility_code: str = "allowed",
) -> dict:
    allowed = compatibility_code == "allowed"
    role_score = _score(
        candidate_id, score, eligible=eligible, reason="gate:calibrated:no"
    )
    return {
        "candidate_id": candidate_id,
        "identity": {
            "profile_id": profile_id,
            "model_id": model,
            "provider_org": provider,
            "model_vendor": provider,
            "channel": channel,
            "capacity_pool": profile_id,
        },
        "states": {
            "catalogued": {"value": True},
            "configured": {"value": profile_id == "p1"},
            "adapter_green": {"value": profile_id == "p1"},
            "selectable": {"value": allowed},
            "blocked": {"value": not allowed},
        },
        "provider_metadata": {
            "data_policy": "non_confidential_only"
            if profile_id == "p2"
            else "owner_account",
            "privacy_note": "fixture privacy",
        },
        "model_metadata": {"tier": tier},
        "roles": [
            {
                "canonical_role": "reviewer",
                "compatibility": {
                    "allowed": allowed,
                    "code": compatibility_code,
                    "reason": compatibility_code,
                },
                "evaluation": {"status": "calibrated" if eligible else "partial"},
                "runtime_metrics": {"sample_count": 3},
                "score": role_score,
                "provenance": {"evaluation_receipts": [f"{candidate_id}.json"]},
                "input_hash": f"hash-{candidate_id}",
            }
        ],
    }


def _read_model() -> dict:
    return {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": MODEL_ROLE_SCORE_VERSION,
        "content_hash": "fixture-hash",
        "observed_at": "2026-07-22T00:00:00+00:00",
        "rollout": "shadow_only",
        "runtime": {"database_sources": [], "diagnostics": []},
        "candidates": [
            _candidate(
                "candidate-b",
                profile_id="p2",
                model="model-b",
                provider="provider-b",
                channel="api",
                tier="premium",
                score=99,
                eligible=False,
                compatibility_code="model_tier_insufficient",
            ),
            _candidate(
                "candidate-a",
                profile_id="p1",
                model="model-a",
                provider="provider-a",
                channel="subscription",
                tier="standard",
                score=80,
                eligible=True,
            ),
        ],
    }


def test_role_projection_orders_by_canonical_score_and_preserves_deny_reason() -> None:
    ranked = rank_catalog_candidates_for_role(_read_model(), "code_reviewer")

    assert [item["candidate_id"] for item in ranked] == ["candidate-a", "candidate-b"]
    assert ranked[0]["selection_reason"] == "auto_eligible_shadow_only"
    assert ranked[1]["selection_reason"] == "compatibility:model_tier_insufficient"
    assert ranked[1]["role_evaluation"]["runtime_metrics"]["sample_count"] == 3


def test_catalog_filters_and_provider_summary_keep_operational_channels_separate() -> (
    None
):
    filtered = filter_catalog_candidates(
        _read_model(), provider="provider-a", channel="subscription", configured=True
    )
    providers = summarize_catalog_providers(filtered)

    assert [item["candidate_id"] for item in filtered] == ["candidate-a"]
    assert providers == [
        {
            "profile_id": "p1",
            "provider": "provider-a",
            "channel": "subscription",
            "capacity_pool": "p1",
            "model_count": 1,
            "configured_count": 1,
            "green_count": 1,
            "selectable_count": 1,
            "blocked_count": 0,
            "data_policy": "owner_account",
            "privacy_note": "fixture privacy",
            "economy_classes": [],
        }
    ]
    blocked = filter_catalog_candidates(_read_model(), state="blocked")
    assert [item["candidate_id"] for item in blocked] == ["candidate-b"]


def test_model_catalog_endpoints_publish_openapi_filters_order_and_reasons(
    monkeypatch,
) -> None:
    monkeypatch.setattr(catalog_router, "_read_model", lambda: deepcopy(_read_model()))
    client = TestClient(app)

    inventory = client.get(
        "/api/model-catalog",
        params={"provider": "provider-a", "configured": "true"},
    )
    ranked = client.get("/api/model-catalog/candidates", params={"role": "reviewer"})
    invalid = client.get("/api/model-catalog", params={"state": "invented"})
    schema = client.get("/openapi.json").json()

    assert inventory.status_code == 200
    assert inventory.json()["counts"] == {"candidates": 1, "providers": 1}
    assert ranked.status_code == 200
    assert ranked.json()["compatibility_context"] == {
        "run_profile": None,
        "criticality": "medium",
        "data_class": "public",
        "required_capabilities": "role_defaults",
        "projection": "base_role_score",
        "contextual_endpoint": "POST /api/model-catalog/selection",
    }
    assert [item["candidate_id"] for item in ranked.json()["candidates"]] == [
        "candidate-a",
        "candidate-b",
    ]
    assert ranked.json()["candidates"][1]["selection_reason"] == (
        "compatibility:model_tier_insufficient"
    )
    assert invalid.status_code == 422
    assert schema["paths"]["/api/model-catalog"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("ModelCatalogResponse")
    candidate_schema = schema["components"]["schemas"]["CatalogCandidate"]
    assert {"candidate_id", "identity", "states", "model_metadata"} <= set(
        candidate_schema["required"]
    )


def test_contextual_selection_endpoint_is_explicitly_shadow_only(monkeypatch) -> None:
    import aiteam.compatibility_service as compatibility_service
    import aiteam.model_selection_context as selection_context
    import aiteam.user_config as user_config

    monkeypatch.setattr(catalog_router, "_read_model", lambda: deepcopy(_read_model()))
    monkeypatch.setattr(user_config, "load_adapter_profiles", lambda: [
        {
            "id": "p1", "status": "active", "data_policy": "owner_account",
            "workspace_mode": "read", "structured_output": "json_schema",
        },
        {
            "id": "p2", "status": "active", "data_policy": "non_confidential_only",
            "workspace_mode": "read", "structured_output": "json_schema",
        },
    ])
    monkeypatch.setattr(user_config, "model_options", lambda: {
        "p1": [{
            "value": "model-a", "selectable": True, "tier": "standard",
            "allowed_roles": ["reviewer"], "caps": ["reasoning", "synthesis", "repo_read"],
            "structured_output": "json_schema",
        }],
        "p2": [{
            "value": "model-b", "selectable": True, "tier": "premium",
            "allowed_roles": ["reviewer"], "caps": ["reasoning", "synthesis", "repo_read"],
            "structured_output": "json_schema",
        }],
    })
    monkeypatch.setattr(selection_context, "model_selection_runtime_context", lambda *_args, **_kwargs: {
        "capacity_by_profile": {
            profile_id: {
                "profile_id": profile_id,
                "state": "metered",
                "source": "fixture",
                "window_hours": 168,
                "forecast": {
                    "status": "forecast_available", "source": "test_proven_available",
                    "unit": "runs", "limit": 100, "utilization": 0.1,
                },
            }
            for profile_id in ("p1", "p2")
        },
        "budget": {"status": "unbounded", "source": "fixture"},
    })
    client = TestClient(app)

    response = client.post("/api/model-catalog/selection", json={
        "role": "reviewer", "data_class": "confidential",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["rollout"] == "shadow_only"
    assert payload["default"]["candidate_id"] == "candidate-a"
    assert payload["candidates"][1]["contextual_compatibility"]["code"] == (
        "confidential_data_forbidden"
    )

    monkeypatch.setattr(compatibility_service, "issue_compatibility_context", lambda *_args: {
        "run_profile": "full_team",
        "criticality": "high",
        "data_class": "public",
        "required_capabilities": ["external_mcp"],
    })
    tool_response = client.post("/api/model-catalog/selection", json={
        "role": "reviewer", "issue_id": "issue:tool", "data_class": "public",
    })
    assert tool_response.status_code == 200
    tool_payload = tool_response.json()
    assert "external_mcp" in tool_payload["context"]["required_capabilities"]
    assert tool_payload["default"]["candidate_id"] is None
    assert all(
        item["contextual_compatibility"]["code"] == "external_mcp_unsupported"
        for item in tool_payload["candidates"]
    )


def test_shadow_default_endpoint_persists_decision_without_applying(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(Path("aiteam/db/schema.sql").read_text(encoding="utf-8"))
    monkeypatch.setattr(catalog_router, "resolve_runtime_dir", lambda *_args: tmp_path)
    projection = {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": MODEL_ROLE_SCORE_VERSION,
        "canonical_role": "reviewer",
        "default": {"candidate_id": "candidate-a"},
        "candidates": [{
            "candidate_id": "candidate-a",
            "identity": {"profile_id": "p1", "model_id": "model-a"},
            "rank": 1,
            "selection_reason": "highest_auto_eligible",
            "selection_score": {
                "score": 90,
                "auto_eligible": True,
                "hard_gates": {"calibrated": {"passed": True}},
            },
        }],
    }
    monkeypatch.setattr(
        catalog_router,
        "contextual_model_selection",
        lambda *_args, **_kwargs: deepcopy(projection),
    )
    client = TestClient(app)
    request = {
        "role": "reviewer",
        "selection_scope": "shadow:test:reviewer",
        "current_profile_id": "p1",
        "current_model": "model-a",
    }

    first = client.post("/api/model-catalog/selection/shadow", json=request)
    second = client.post("/api/model-catalog/selection/shadow", json=request)

    assert first.status_code == 200
    payload = first.json()
    assert payload["rollout"] == "shadow_only"
    assert payload["divergence"] == "matches_current"
    assert payload["assignment_changed"] is False
    assert payload["snapshot"]["auto_applied"] is False
    assert payload["snapshot"]["hash_valid"] is True
    assert second.json()["snapshot"]["id"] == payload["snapshot"]["id"]


def test_legacy_profile_endpoint_delegates_order_and_identity_without_losing_fields(
    monkeypatch,
) -> None:
    read_model = _read_model()
    profile = {
        "id": "p1",
        "model_options": [
            {"value": "model-z", "available": True},
            {"value": "model-a", "available": True},
        ],
    }
    monkeypatch.setattr(adapters_router, "load_adapter_profiles", lambda: [profile])
    monkeypatch.setattr(
        adapters_router,
        "model_options_for_role",
        lambda *_args, **_kwargs: [
            {"value": "model-z", "role_score": 999},
            {"value": "model-a", "role_score": 1},
        ],
    )
    monkeypatch.setattr(
        adapters_router, "get_current_model_catalog", lambda **_kwargs: read_model
    )
    monkeypatch.setattr(
        adapters_router,
        "compatibility_decision",
        lambda **_kwargs: {"allowed": True, "code": "allowed"},
    )
    client = TestClient(app)

    response = client.get(
        "/api/user-adapters/models",
        params={"profile_id": "p1", "role": "reviewer"},
    )

    assert response.status_code == 200
    options = response.json()["options"]
    assert [item["value"] for item in options] == ["model-a", "model-z"]
    assert options[0]["catalog_candidate_id"] == "candidate-a"
    assert options[0]["model_role_score"]["score"] == 80
    assert options[0]["role_score"] == 1
    assert options[1]["selection_reason"] == "role_score_missing"
