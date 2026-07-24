import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiteam.model_catalog_read_model import (
    MODEL_CATALOG_READ_MODEL_VERSION,
    audit_model_catalog_read_model,
    build_model_catalog_read_model,
    collect_model_runtime_observations,
    load_durable_catalog_versions,
)
from aiteam.policies import CANONICAL_ROLES


OBSERVED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _role(read_model: dict, name: str) -> dict:
    return next(
        row
        for row in read_model["candidates"][0]["roles"]
        if row["canonical_role"] == name
    )


def _runtime_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "project.sqlite"
    schema = Path("aiteam/db/schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema)
        conn.execute(
            """INSERT INTO agents
               (id, role, name, adapter_type, adapter_config_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                "agent-1",
                "software_engineer",
                "Engineer",
                "openai_api",
                json.dumps({"profile_id": "openai_api", "model": "model-a"}),
            ),
        )
        for index, status in enumerate(("completed", "completed", "failed"), start=1):
            run_id = f"run-{index}"
            conn.execute(
                """INSERT INTO runs
                   (id, agent_id, status, provider, model, channel, started_at,
                    finished_at, usage_json, actual_cost_cents)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    "agent-1",
                    status,
                    "openai",
                    "model-a",
                    "api",
                    f"2026-07-22T12:0{index}:00+00:00",
                    f"2026-07-22T12:0{index}:02+00:00",
                    json.dumps(
                        {"input_tokens": 100 * index, "output_tokens": 10 * index}
                    ),
                    index,
                ),
            )
            conn.execute(
                """INSERT INTO run_adapter_profiles
                   (run_id, profile_id, provider, model, channel)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, "openai_api", "openai", "model-a", "api"),
            )
            conn.execute(
                """INSERT INTO cost_events
                   (id, run_id, agent_id, provider, model, channel, cost_cents,
                    input_tokens, output_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"cost-{index}",
                    run_id,
                    "agent-1",
                    "openai",
                    "model-a",
                    "api",
                    index,
                    100 * index,
                    10 * index,
                ),
            )
    return db_path


def _profile() -> dict:
    option = {
        "value": "model-a",
        "label": "Model A",
        "tier": "standard",
        "caps": ["coding", "reasoning", "synthesis"],
        "best_for": ["engineer"],
        "selectable": True,
        "verification_status": "verified",
        "automatic": True,
    }
    return {
        "id": "openai_api",
        "label": "Profile One",
        "provider": "openai",
        "channel": "api",
        "adapter_type": "openai_api",
        "connected": True,
        "health": {"status": "ok", "version": "responses-v1"},
        "workspace_mode": "write",
        "structured_output": "json_schema",
        "mcp_transport": "none",
        "supported_roles": ["engineer"],
        "model_options": [option],
    }


def _evaluation_report() -> dict:
    return {
        "rows": [
            {
                "profile_id": "openai_api",
                "model": "model-a",
                "roles": [
                    {
                        "role": "engineer",
                        "status": "calibrated",
                        "evidence_receipts": ["receipt.json"],
                        "stale_reasons": [],
                    }
                ],
            }
        ]
    }


def _normalized_metrics() -> dict:
    return {
        ("openai_api", "model-a", "engineer"): {
            "components": {
                "quality": {"value": 90, "source": "hidden_suite"},
                "capability": {"value": 85, "source": "tool_fixture"},
                "reliability": {"value": 80, "source": "comparable_runs"},
                "economy": {
                    "value": 70,
                    "source": "accepted_task_cost_normalizer",
                    "basis": "api_cost_per_accepted_task",
                    "comparison_group": "fixture-v1",
                    "burden": 3,
                },
                "speed": {
                    "value": 60,
                    "source": "e2e_latency_normalizer",
                    "comparison_group": "fixture-v1",
                    "latency_ms": 2000,
                },
            },
            "evidence": {
                "status": "calibrated",
                "kind": "exact_role_canary",
                "classes": ["behavioral_deterministic"],
                "seeds": 3,
                "cases": 2,
                "case_families": ["tenant_boundary", "concurrency"],
                "case_family_count": 2,
                "case_diversity": "multi_family",
                "required_tools": ["repo_write"],
                "covered_tools": ["repo_write"],
                "fresh": True,
                "provider_version": "responses-v1",
                "evaluated_at": "2026-07-22",
                "goodhart_risk": "low",
                "unmeasured_constructs": ["novel_projects"],
            },
        }
    }


def test_runtime_collector_joins_profile_role_usage_cost_and_duration(
    tmp_path: Path,
) -> None:
    report = collect_model_runtime_observations([_runtime_db(tmp_path)])

    assert report["diagnostics"] == []
    assert len(report["database_sources"]) == 1
    assert len(report["historical_models"]) == 1
    row = report["role_metrics"][0]
    assert (row["profile_id"], row["model"], row["role"]) == (
        "openai_api",
        "model-a",
        "engineer",
    )
    assert row["run_count"] == 3
    assert row["completed_count"] == 2
    assert row["failed_count"] == 1
    assert row["median_duration_ms"] == 2000
    assert row["input_tokens"] == 600
    assert row["output_tokens"] == 60
    assert row["cost_cents"] == 6


def test_runtime_collector_degrades_for_missing_and_legacy_db(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.sqlite"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE something_else (id TEXT)")

    report = collect_model_runtime_observations([tmp_path / "missing.sqlite", legacy])

    assert {item["code"] for item in report["diagnostics"]} == {
        "database_missing",
        "runs_table_missing",
    }


def test_runtime_collector_deduplicates_equivalent_database_paths(
    tmp_path: Path,
) -> None:
    db_path = _runtime_db(tmp_path)

    report = collect_model_runtime_observations(
        [db_path, db_path.parent / "." / db_path.name]
    )

    assert len(report["database_sources"]) == 1
    assert report["role_metrics"][0]["run_count"] == 3
    assert [item["code"] for item in report["diagnostics"]] == [
        "database_duplicate_ignored"
    ]


def test_read_model_composes_normalized_score_and_provenance(tmp_path: Path) -> None:
    profile = _profile()
    runtime = collect_model_runtime_observations([_runtime_db(tmp_path)])
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        runtime_report=runtime,
        normalized_metrics=_normalized_metrics(),
        observed_at=OBSERVED_AT,
    )

    assert read_model["schema_version"] == MODEL_CATALOG_READ_MODEL_VERSION
    assert read_model["rollout"] == "shadow_only"
    assert len(read_model["content_hash"]) == 64
    candidate = read_model["candidates"][0]
    assert candidate["model_metadata"]["tier"] == "standard"
    assert candidate["provider_metadata"]["label"] == "Profile One"
    assert tuple(row["canonical_role"] for row in candidate["roles"]) == CANONICAL_ROLES
    role = _role(read_model, "engineer")
    assert role["canonical_role"] == "engineer"
    assert role["score"]["score"] == 80.75
    assert role["score"]["auto_eligible"] is True
    assert role["score_inputs"]["evidence"]["status"] == "calibrated"
    assert role["provenance"]["evaluation_receipts"] == ["receipt.json"]
    assert role["provenance"]["runtime_run_ids"] == ["run-1", "run-2", "run-3"]
    assert read_model["normalized_metrics"]["pair_count"] == 1
    assert read_model["normalized_metrics"]["pairs"] == [
        {
            "profile_id": "openai_api",
            "model": "model-a",
            "canonical_role": "engineer",
            "version": "caller_supplied",
            "evidence_kind": "exact_role_canary",
            "case_diversity": "multi_family",
        }
    ]
    assert audit_model_catalog_read_model(read_model)["ok"] is True


def test_stale_evidence_keeps_historical_case_diversity_but_cannot_promote(
    tmp_path: Path,
) -> None:
    profile = _profile()
    evaluation = _evaluation_report()
    role_evidence = evaluation["rows"][0]["roles"][0]
    role_evidence["status"] = "partial"
    role_evidence["stale_reasons"] = ["provider_version_changed"]
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=evaluation,
        runtime_report=collect_model_runtime_observations([_runtime_db(tmp_path)]),
        normalized_metrics=_normalized_metrics(),
        observed_at=OBSERVED_AT,
    )

    role = _role(read_model, "engineer")
    hard_gates = role["score_inputs"]["hard_gates"]
    assert hard_gates["calibrated"] is False
    assert hard_gates["fresh"] is False
    assert hard_gates["case_diversity"] is True
    assert role["score"]["auto_eligible"] is False
    assert audit_model_catalog_read_model(read_model)["ok"] is True


def test_raw_runtime_metrics_are_not_misrepresented_as_normalized_score(
    tmp_path: Path,
) -> None:
    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        runtime_report=collect_model_runtime_observations([_runtime_db(tmp_path)]),
        observed_at=OBSERVED_AT,
    )
    role = _role(read_model, "engineer")

    assert role["runtime_metrics"]["median_duration_ms"] == 2000
    assert role["score"]["breakdown"]["speed"]["value"] is None
    assert role["score"]["breakdown"]["economy"]["value"] is None
    assert role["score"]["score"] is None
    assert role["score"]["auto_eligible"] is False
    audit = audit_model_catalog_read_model(read_model)
    assert audit["ok"] is True
    assert audit["automatic_candidate_count"] == 0


def test_local_channel_gets_known_zero_external_cost_economy() -> None:
    profile = _profile()
    profile.update(
        {
            "id": "local_fixture",
            "provider": "ollama",
            "channel": "local",
            "adapter_type": "subscription_cli",
        }
    )
    report = _evaluation_report()
    report["rows"][0]["profile_id"] = "local_fixture"
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"local_fixture": profile["model_options"]},
        evaluation_report=report,
        observed_at=OBSERVED_AT,
    )

    economy = _role(read_model, "engineer")["score"]["breakdown"][
        "economy"
    ]
    assert economy["status"] == "known"
    assert economy["value"] == 100
    assert economy["basis"] == "zero_external_cost"
    assert economy["burden"] == 0
    assert economy["reason"] == "local_zero_external_cost_and_quota"


def test_durable_catalog_version_fallback_requires_fresh_passing_receipt(
    tmp_path: Path,
) -> None:
    receipt_dir = tmp_path / "benchmarks" / "results" / "model_catalog_drift"
    receipt_dir.mkdir(parents=True)
    payload = {
        "benchmark": "model_catalog_drift_audit",
        "observed_at": "2026-07-23T10:00:00+00:00",
        "ok": True,
        "gates": {"inventory": True, "coverage": True},
        "catalogs": [
            {
                "profile_id": "antigravity_subscription",
                "cli_version": "1.1.5",
                "coverage_ok": True,
                "status": "current",
            }
        ],
        "codex_catalog": {
            "installed_version": "0.145.0",
            "coverage_ok": True,
            "status": "current",
        },
    }
    path = receipt_dir / "model-catalog-drift-2026-07-23.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = load_durable_catalog_versions(
        repo_root=tmp_path,
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )

    assert report["profiles"]["codex_subscription"]["version"] == "0.145.0"
    assert report["profiles"]["antigravity_subscription"]["version"] == "1.1.5"
    assert report["profiles"]["codex_subscription"]["source"].startswith(
        "drift_receipt:"
    )

    payload["gates"]["coverage"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")
    rejected = load_durable_catalog_versions(
        repo_root=tmp_path,
        observed_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    assert rejected["profiles"] == {}
    assert rejected["diagnostics"] == [
        "receipt_not_authoritative:model-catalog-drift-2026-07-23.json"
    ]


def test_auditor_detects_hash_and_consumer_divergence() -> None:
    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        observed_at=OBSERVED_AT,
    )
    read_model["observed_at"] = "tampered"

    audit = audit_model_catalog_read_model(
        read_model, consumer_candidate_ids={"team": ["unexpected"]}
    )

    assert audit["ok"] is False
    assert {item["code"] for item in audit["failures"]} == {
        "content_hash_mismatch",
        "consumer_catalog_divergence",
    }


def test_auditor_detects_declared_model_and_role_removed_even_with_rehashed_payload() -> (
    None
):
    import hashlib

    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        observed_at=OBSERVED_AT,
    )
    read_model["candidates"] = []
    canonical = json.dumps(
        {key: value for key, value in read_model.items() if key != "content_hash"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    read_model["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    audit = audit_model_catalog_read_model(read_model)

    assert {item["code"] for item in audit["failures"]} == {
        "declared_profile_missing",
        "declared_model_missing",
        "declared_role_missing",
    }


def test_auditor_recomputes_role_input_hash_and_score_after_outer_rehash() -> None:
    import hashlib

    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        normalized_metrics=_normalized_metrics(),
        observed_at=OBSERVED_AT,
    )
    role = _role(read_model, "engineer")
    role["score_inputs"]["components"]["quality"]["value"] = 1
    role["score"]["score"] = 99
    canonical = json.dumps(
        {key: value for key, value in read_model.items() if key != "content_hash"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    read_model["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    audit = audit_model_catalog_read_model(read_model)

    assert {item["code"] for item in audit["failures"]} == {
        "role_input_hash_mismatch",
        "role_score_mismatch",
    }


def test_matrix_explains_incompatible_cells_without_score() -> None:
    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        normalized_metrics=_normalized_metrics(),
        observed_at=OBSERVED_AT,
    )

    deterministic = _role(read_model, "test_runner")

    assert deterministic["compatibility"]["code"] == "deterministic_role"
    assert deterministic["evaluation"]["status"] == "incompatible"
    assert deterministic["evaluation"]["reason_code"] == "deterministic_role"
    assert deterministic["evaluation"]["score_permitted"] is False
    assert deterministic["score"]["score"] is None


def test_compatible_role_is_manual_until_explicitly_nominated() -> None:
    profile = _profile()
    profile["supported_roles"] = []
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        observed_at=OBSERVED_AT,
    )

    worker = _role(read_model, "worker")

    assert worker["compatibility"]["allowed"] is True
    assert worker["automatic_selection"] == {
        "model_policy_enabled": True,
        "role_nominated": False,
        "eligible_by_policy": False,
        "nomination_source": "model_option.best_for",
    }
    assert worker["score_inputs"]["hard_gates"]["automatic_policy"] is False
    assert worker["evaluation"]["status"] == "compatible_not_nominated"
    assert worker["evaluation"]["next_action"] is None


def test_partial_exact_evidence_is_not_scheduled_again_without_change() -> None:
    profile = _profile()
    report = _evaluation_report()
    role = report["rows"][0]["roles"][0]
    role["status"] = "partial"
    role["evidence_receipts"] = ["three-seed-partial.json"]
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=report,
        observed_at=OBSERVED_AT,
    )

    engineer = _role(read_model, "engineer")

    assert engineer["evaluation"]["next_action"] == "no_rerun_until_material_change"
    assert engineer["score_inputs"]["hard_gates"]["automatic_policy"] is True
    assert engineer["score_inputs"]["hard_gates"]["calibrated"] is False
    assert audit_model_catalog_read_model(read_model)["ok"] is True


def test_deferred_negative_diagnostic_preserves_material_change_contract() -> None:
    profile = _profile()
    report = _evaluation_report()
    role = report["rows"][0]["roles"][0]
    role.update(
        {
            "status": "deferred_until_material_change",
            "next_action": "no_rerun_until_material_change",
            "rerun_policy": "material_change_only",
            "material_change_triggers": ["provider_or_cli_version_changed"],
            "diagnostic_receipts": ["negative-seed-1.json"],
        }
    )
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=report,
        observed_at=OBSERVED_AT,
    )

    engineer = _role(read_model, "engineer")

    assert engineer["evaluation"]["status"] == (
        "deferred_until_material_change"
    )
    assert engineer["evaluation"]["next_action"] == (
        "no_rerun_until_material_change"
    )
    assert engineer["evaluation"]["material_change_triggers"] == [
        "provider_or_cli_version_changed"
    ]
    assert engineer["score"]["score"] is None
    assert audit_model_catalog_read_model(read_model)["ok"] is True


def test_auditor_rejects_operational_automatic_pair_without_exact_evidence() -> None:
    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report={"rows": []},
        observed_at=OBSERVED_AT,
    )

    audit = audit_model_catalog_read_model(read_model)

    assert "automatic_operational_evidence_missing" in {
        item["code"] for item in audit["failures"]
    }


def test_auditor_rejects_incomplete_canonical_role_matrix() -> None:
    import hashlib

    profile = _profile()
    read_model = build_model_catalog_read_model(
        profiles=[profile],
        declared_options_by_profile={"openai_api": profile["model_options"]},
        evaluation_report=_evaluation_report(),
        observed_at=OBSERVED_AT,
    )
    read_model["candidates"][0]["roles"].pop()
    canonical = json.dumps(
        {key: value for key, value in read_model.items() if key != "content_hash"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    read_model["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    audit = audit_model_catalog_read_model(read_model)

    assert "candidate_role_matrix_incomplete" in {
        item["code"] for item in audit["failures"]
    }
