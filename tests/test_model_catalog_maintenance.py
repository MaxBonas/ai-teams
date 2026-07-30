from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from aiteam.db.model_catalog_maintenance import (
    DIMENSIONS,
    list_model_catalog_maintenance,
    reconcile_model_catalog_maintenance,
)


def _read_model() -> dict:
    payload = {
        "schema_version": "model_catalog_read_model_v2",
        "score_version": "model_role_score_v2",
        "observed_at": "2026-07-30T10:00:00+00:00",
        "canonical_roles": ["engineer"],
        "evidence_taxonomy": {"schema_version": "taxonomy_v1"},
        "tier1_authority_contract": {"policy_version": "tier_policy_v1"},
        "evaluation_version_evidence": {"codex_subscription": "0.146.0-alpha.6"},
        "candidates": [
            {
                "candidate_id": "codex_subscription:gpt-5.6-terra",
                "label": "Terra",
                "identity": {
                    "profile_id": "codex_subscription",
                    "model_id": "gpt-5.6-terra",
                    "capacity_pool": "codex",
                },
                "roles_declared": ["engineer"],
                "states": {
                    "configured": {"value": True},
                    "adapter_green": {"value": True},
                    "selectable": {"value": True},
                    "stale": {"value": False},
                },
                "provider_metadata": {
                    "adapter_type": "subscription_cli",
                    "mcp_transport": "governed",
                    "structured_output": "json",
                },
                "model_metadata": {
                    "tier": "tier_2",
                    "capability_band": "strong",
                    "capabilities": ["coding"],
                    "probe_version": "0.146.0-alpha.6",
                    "economy": {"cost_class": "subscription"},
                    "price_note": "flat",
                },
                "roles": [
                    {
                        "canonical_role": "engineer",
                        "compatibility": {"allowed": True, "tools": ["files"]},
                        "evaluation": {
                            "prompt_contract": "engineer_v1",
                            "status": "calibrated",
                        },
                        "provenance": {"judge_contract": "judge_v1"},
                        "runtime_metrics": {"quota_state": "available"},
                        "score_inputs": {"capacity_state": "available"},
                        "score": {
                            "auto_eligible": False,
                            "hard_gates": {"tool_compatibility": True},
                        },
                    }
                ],
            }
        ],
    }
    return _seal(payload)


def _seal(value: dict) -> dict:
    result = deepcopy(value)
    result.pop("content_hash", None)
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result["content_hash"] = hashlib.sha256(encoded).hexdigest()
    return result


def test_maintenance_is_idempotent_and_adds_one_snapshot_per_month(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "catalog.db"
    read_model = _read_model()

    first = reconcile_model_catalog_maintenance(
        db_path,
        read_model,
        observed_at="2026-07-30T10:00:00+00:00",
    )
    same = reconcile_model_catalog_maintenance(
        db_path,
        read_model,
        observed_at="2026-07-31T10:00:00+00:00",
    )
    next_month = reconcile_model_catalog_maintenance(
        db_path,
        read_model,
        observed_at="2026-08-01T10:00:00+00:00",
    )

    assert first["persisted"] is True
    assert first["snapshot"]["trigger_reasons"] == ["initial", "monthly"]
    assert same["persisted"] is False
    assert same["reason"] == "no_change"
    assert next_month["persisted"] is True
    assert next_month["snapshot"]["trigger_reasons"] == ["monthly"]
    history = list_model_catalog_maintenance(db_path)
    assert len(history) == 2
    assert all(row["hash_valid"] is True for row in history)
    assert {row["period"] for row in history} == {"2026-07", "2026-08"}


@pytest.mark.parametrize(
    ("dimension", "mutate"),
    [
        ("model", lambda row: row["candidates"][0].update(label="Terra 2")),
        (
            "cli",
            lambda row: row["candidates"][0]["model_metadata"].update(
                probe_version="0.147.0"
            ),
        ),
        (
            "price",
            lambda row: row["candidates"][0]["model_metadata"]["economy"].update(
                cost_class="api_paid"
            ),
        ),
        (
            "quota",
            lambda row: row["candidates"][0]["roles"][0][
                "runtime_metrics"
            ].update(quota_state="pressured"),
        ),
        (
            "prompt",
            lambda row: row["candidates"][0]["roles"][0]["evaluation"].update(
                prompt_contract="engineer_v2"
            ),
        ),
        (
            "tool",
            lambda row: row["candidates"][0]["roles"][0][
                "compatibility"
            ].update(tools=["files", "shell"]),
        ),
        ("contract", lambda row: row.update(score_version="model_role_score_v3")),
    ],
)
def test_each_governed_dimension_opens_an_event_snapshot(
    tmp_path: Path,
    dimension: str,
    mutate,
) -> None:
    db_path = tmp_path / f"{dimension}.db"
    baseline = _read_model()
    reconcile_model_catalog_maintenance(
        db_path,
        baseline,
        observed_at="2026-07-30T10:00:00+00:00",
    )
    changed = deepcopy(baseline)
    mutate(changed)
    changed = _seal(changed)

    result = reconcile_model_catalog_maintenance(
        db_path,
        changed,
        observed_at="2026-07-30T11:00:00+00:00",
    )

    assert set(DIMENSIONS) == {
        "model",
        "cli",
        "price",
        "quota",
        "prompt",
        "tool",
        "contract",
    }
    assert result["persisted"] is True
    assert f"{dimension}_changed" in result["snapshot"]["trigger_reasons"]
    assert len(list_model_catalog_maintenance(db_path)) == 2


def test_history_preserves_trends_instead_of_deleting_old_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trends.db"
    baseline = _read_model()
    reconcile_model_catalog_maintenance(
        db_path,
        baseline,
        observed_at="2026-07-30T10:00:00+00:00",
    )
    changed = deepcopy(baseline)
    second = deepcopy(changed["candidates"][0])
    second["candidate_id"] = "codex_subscription:gpt-5.6-luna"
    second["identity"]["model_id"] = "gpt-5.6-luna"
    changed["candidates"].append(second)
    changed = _seal(changed)

    result = reconcile_model_catalog_maintenance(
        db_path,
        changed,
        observed_at="2026-07-30T11:00:00+00:00",
    )

    assert result["snapshot"]["trend"]["candidate_count"] == 1
    assert result["snapshot"]["trend"]["role_cell_count"] == 1
    assert len(list_model_catalog_maintenance(db_path)) == 2
