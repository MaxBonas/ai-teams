"""Read model shadow del catálogo universal y scoring por rol.

Compone configuración, catálogo, compatibilidad, evaluación y telemetría sin
convertir métricas crudas no comparables en puntuaciones. No modifica routing.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from aiteam.model_catalog_projection import build_model_catalog_identity_projection
from aiteam.model_compatibility import compatibility_decision
from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage
from aiteam.model_role_scoring import MODEL_ROLE_SCORE_VERSION, score_model_role
from aiteam.policies import canonical_role
from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.user_config import (
    ROLE_CAPABILITY_PROFILES,
    load_adapter_profiles,
    model_options,
    profile_is_connected,
)


MODEL_CATALOG_READ_MODEL_VERSION = "model_catalog_read_model_v1"


def collect_model_runtime_observations(db_paths: Iterable[Path]) -> dict[str, Any]:
    """Lee runs/costes de SQLite modernas o parciales sin mutarlas."""
    historical: dict[tuple[str, str, str], dict[str, Any]] = {}
    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    database_sources: list[dict[str, Any]] = []

    for raw_path in db_paths:
        db_path = Path(raw_path)
        database_id = _database_id(db_path)
        if not db_path.is_file():
            diagnostics.append(
                {
                    "database_id": database_id,
                    "code": "database_missing",
                    "detail": db_path.name,
                }
            )
            continue
        try:
            conn = sqlite3.connect(
                f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True
            )
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            diagnostics.append(
                {
                    "database_id": database_id,
                    "code": "database_open_failed",
                    "detail": str(exc),
                }
            )
            continue
        try:
            tables = _table_names(conn)
            if "runs" not in tables:
                diagnostics.append(
                    {
                        "database_id": database_id,
                        "code": "runs_table_missing",
                        "detail": db_path.name,
                    }
                )
                continue
            agents = {
                str(row["id"]): dict(row) for row in _rows(conn, "agents", tables)
            }
            issues = {
                str(row["id"]): dict(row) for row in _rows(conn, "issues", tables)
            }
            profiles = {
                str(row["run_id"]): dict(row)
                for row in _rows(conn, "run_adapter_profiles", tables)
            }
            costs: dict[str, dict[str, int]] = defaultdict(
                lambda: {"cost_cents": 0, "input_tokens": 0, "output_tokens": 0}
            )
            for raw_cost in _rows(conn, "cost_events", tables):
                cost = dict(raw_cost)
                run_id = str(cost.get("run_id") or "")
                if run_id:
                    costs[run_id]["cost_cents"] += _safe_int(cost.get("cost_cents"))
                    costs[run_id]["input_tokens"] += _safe_int(cost.get("input_tokens"))
                    costs[run_id]["output_tokens"] += _safe_int(
                        cost.get("output_tokens")
                    )

            run_count = 0
            for raw_run in _rows(conn, "runs", tables):
                run = dict(raw_run)
                run_id = str(run.get("id") or "")
                agent = agents.get(str(run.get("agent_id") or ""), {})
                issue = issues.get(str(run.get("issue_id") or ""), {})
                run_profile = profiles.get(run_id, {})
                adapter_config = _json_object(agent.get("adapter_config_json"))
                profile_id = str(
                    run_profile.get("profile_id")
                    or adapter_config.get("profile_id")
                    or ""
                ).strip()
                model = str(
                    run_profile.get("model")
                    or run.get("model")
                    or adapter_config.get("model")
                    or ""
                ).strip()
                if not profile_id or not model:
                    continue
                provider = str(
                    run_profile.get("provider") or run.get("provider") or "unknown"
                )
                channel = str(
                    run_profile.get("channel") or run.get("channel") or "unknown"
                )
                role = canonical_role(
                    str(agent.get("role") or issue.get("role") or "unknown")
                )
                observed_at = str(
                    run.get("finished_at")
                    or run.get("started_at")
                    or run.get("created_at")
                    or ""
                )
                historical_key = (profile_id, model, database_id)
                existing = historical.get(historical_key)
                if existing is None or observed_at < str(
                    existing.get("observed_at") or observed_at
                ):
                    historical[historical_key] = {
                        "profile_id": profile_id,
                        "model": model,
                        "provider": provider,
                        "channel": channel,
                        "source": f"sqlite:{database_id}:runs",
                        "observed_at": observed_at,
                    }

                key = (profile_id, model, role)
                aggregate = aggregates.setdefault(
                    key,
                    {
                        "profile_id": profile_id,
                        "model": model,
                        "role": role,
                        "run_count": 0,
                        "completed_count": 0,
                        "failed_count": 0,
                        "status_counts": {},
                        "duration_ms_samples": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_cents": 0,
                        "run_ids": [],
                        "database_ids": set(),
                    },
                )
                status = str(run.get("status") or "unknown")
                aggregate["run_count"] += 1
                aggregate["completed_count"] += int(status == "completed")
                aggregate["failed_count"] += int(
                    status in {"failed", "lost", "cancelled"}
                )
                aggregate["status_counts"][status] = (
                    aggregate["status_counts"].get(status, 0) + 1
                )
                duration_ms = _duration_ms(
                    run.get("started_at"), run.get("finished_at")
                )
                if duration_ms is not None:
                    aggregate["duration_ms_samples"].append(duration_ms)
                usage = _json_object(run.get("usage_json"))
                cost = costs.get(run_id, {})
                aggregate["input_tokens"] += _safe_int(
                    cost.get("input_tokens")
                    or usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                )
                aggregate["output_tokens"] += _safe_int(
                    cost.get("output_tokens")
                    or usage.get("output_tokens")
                    or usage.get("completion_tokens")
                )
                aggregate["cost_cents"] += _safe_int(
                    cost.get("cost_cents") or run.get("actual_cost_cents")
                )
                aggregate["run_ids"].append(run_id)
                aggregate["database_ids"].add(database_id)
                run_count += 1
            database_sources.append(
                {
                    "database_id": database_id,
                    "name": db_path.name,
                    "runs_observed": run_count,
                }
            )
        except sqlite3.Error as exc:
            diagnostics.append(
                {
                    "database_id": database_id,
                    "code": "database_query_failed",
                    "detail": str(exc),
                }
            )
        finally:
            conn.close()

    runtime_rows: list[dict[str, Any]] = []
    for key in sorted(aggregates):
        row = aggregates[key]
        samples = row.pop("duration_ms_samples")
        row["median_duration_ms"] = (
            round(float(median(samples)), 4) if samples else None
        )
        row["database_ids"] = sorted(row["database_ids"])
        row["run_ids"] = sorted(row["run_ids"])
        runtime_rows.append(row)
    return {
        "schema_version": "model_runtime_observations_v1",
        "database_sources": sorted(
            database_sources, key=lambda item: item["database_id"]
        ),
        "historical_models": sorted(
            historical.values(),
            key=lambda item: (item["profile_id"], item["model"], item["source"]),
        ),
        "role_metrics": runtime_rows,
        "diagnostics": diagnostics,
    }


def build_model_catalog_read_model(
    *,
    profiles: Sequence[Mapping[str, Any]],
    declared_options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
    evaluation_report: Mapping[str, Any],
    runtime_report: Mapping[str, Any] | None = None,
    normalized_metrics: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
    discovered_models: Iterable[Mapping[str, Any]] = (),
    excluded_profile_ids: Iterable[str] = (),
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Compone candidatos y filas por rol; solo usa scores explícitamente normalizados."""
    timestamp = _iso_timestamp(observed_at)
    runtime = dict(runtime_report or {})
    identity_projection = build_model_catalog_identity_projection(
        profiles=profiles,
        declared_options_by_profile=declared_options_by_profile,
        discovered_models=discovered_models,
        historical_models=runtime.get("historical_models") or (),
        observed_at=timestamp,
    )
    profile_by_id = {str(item.get("id") or ""): dict(item) for item in profiles}
    option_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for profile_id, options in declared_options_by_profile.items():
        for option in options:
            model = str(option.get("value") or option.get("model") or "")
            if model:
                option_by_key[(profile_id, model)] = dict(option)
    for profile_id, profile in profile_by_id.items():
        for option in profile.get("model_options") or ():
            if isinstance(option, Mapping):
                model = str(option.get("value") or option.get("model") or "")
                if model:
                    option_by_key[(profile_id, model)] = {
                        **option_by_key.get((profile_id, model), {}),
                        **dict(option),
                    }

    evaluation = _evaluation_lookup(evaluation_report)
    runtime_metrics = {
        (
            str(row["profile_id"]),
            str(row["model"]),
            canonical_role(str(row["role"])),
        ): dict(row)
        for row in runtime.get("role_metrics") or ()
    }
    metric_inputs = normalized_metrics or {}
    candidates: list[dict[str, Any]] = []
    for candidate in identity_projection["candidates"]:
        identity = candidate["identity"]
        profile_id = str(identity["profile_id"])
        model = str(identity["model_id"])
        profile = profile_by_id.get(profile_id, {"id": profile_id})
        option = option_by_key.get(
            (profile_id, model), {"value": model, "selectable": False}
        )
        roles = set(candidate.get("roles_declared") or ())
        roles.update(key[2] for key in evaluation if key[:2] == (profile_id, model))
        roles.update(
            key[2] for key in runtime_metrics if key[:2] == (profile_id, model)
        )
        role_rows: list[dict[str, Any]] = []
        for role in sorted(role for role in roles if role and role != "unknown"):
            key = (profile_id, model, role)
            evaluation_row = evaluation.get(key, {})
            runtime_row = runtime_metrics.get(key, {})
            supplied = dict(metric_inputs.get(key) or {})
            compatibility = compatibility_decision(
                profile=profile,
                model=option,
                role=role,
                criticality="medium",
                data_class="public",
                required_capabilities=default_capabilities_for_role(role),
                role_profile=ROLE_CAPABILITY_PROFILES.get(role, {}),
                candidate_models=list(option_by_key.values()),
            )
            evidence = _evidence_input(evaluation_row, supplied.get("evidence"))
            components = _component_inputs(
                runtime_row=runtime_row,
                supplied=supplied.get("components"),
                channel=str(identity.get("channel") or "unknown"),
            )
            hard_gates = _hard_gate_inputs(
                candidate=candidate,
                compatibility=compatibility,
                evaluation=evaluation_row,
            )
            score = score_model_role(
                candidate=candidate,
                role=role,
                components=components,
                evidence=evidence,
                hard_gates=hard_gates,
            )
            provenance = {
                "evaluation_receipts": sorted(
                    str(item) for item in evaluation_row.get("evidence_receipts") or ()
                ),
                "diagnostic_receipts": sorted(
                    str(item)
                    for item in evaluation_row.get("diagnostic_receipts") or ()
                ),
                "runtime_database_ids": sorted(runtime_row.get("database_ids") or ()),
                "runtime_run_ids": sorted(runtime_row.get("run_ids") or ()),
                "metric_sources": sorted(
                    {
                        str(item.get("source") or "unknown")
                        for item in components.values()
                        if isinstance(item, Mapping)
                    }
                ),
            }
            role_rows.append(
                {
                    "canonical_role": role,
                    "compatibility": compatibility,
                    "evaluation": evaluation_row,
                    "runtime_metrics": runtime_row,
                    "score": score,
                    "provenance": provenance,
                    "input_hash": _sha256(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "role": role,
                            "components": components,
                            "evidence": evidence,
                            "hard_gates": hard_gates,
                        }
                    ),
                }
            )
        candidates.append(
            {
                **candidate,
                "provider_metadata": {
                    "label": profile.get("label"),
                    "adapter_type": profile.get("adapter_type"),
                    "status": profile.get("status"),
                    "data_policy": profile.get("data_policy"),
                    "privacy_note": profile.get("privacy_note"),
                    "workspace_mode": profile.get("workspace_mode"),
                    "mcp_transport": profile.get("mcp_transport"),
                    "structured_output": profile.get("structured_output"),
                },
                "model_metadata": {
                    "tier": option.get("tier"),
                    "capability_band": option.get("capability_band"),
                    "capabilities": sorted(
                        str(item) for item in option.get("caps") or ()
                    ),
                    "economy": option.get("economy"),
                    "speed_class": option.get("speed_class"),
                    "speed_source": option.get("speed_source"),
                    "context_window_tokens": option.get("context_window_tokens"),
                    "price_note": option.get("price_note"),
                },
                "roles": role_rows,
            }
        )

    declared_models = sorted(
        {
            (str(profile_id), str(option.get("value") or option.get("model") or ""))
            for profile_id, options in declared_options_by_profile.items()
            for option in options
            if str(option.get("value") or option.get("model") or "")
        }
    )
    declared_role_keys = sorted(
        {
            (
                str(profile_id),
                str(option.get("value") or option.get("model") or ""),
                canonical_role(str(role)),
            )
            for profile_id, options in declared_options_by_profile.items()
            for option in options
            for role in option.get("best_for") or ()
            if str(option.get("value") or option.get("model") or "")
            and canonical_role(str(role))
        }
    )

    payload = {
        "schema_version": MODEL_CATALOG_READ_MODEL_VERSION,
        "score_version": MODEL_ROLE_SCORE_VERSION,
        "rollout": "shadow_only",
        "observed_at": timestamp,
        "declared_profile_ids": sorted(profile_by_id),
        "declared_models": [
            {"profile_id": profile_id, "model": model}
            for profile_id, model in declared_models
        ],
        "declared_role_keys": [
            {"profile_id": profile_id, "model": model, "canonical_role": role}
            for profile_id, model, role in declared_role_keys
        ],
        "excluded_profile_ids": sorted({str(item) for item in excluded_profile_ids}),
        "runtime": {
            "database_sources": runtime.get("database_sources") or [],
            "diagnostics": runtime.get("diagnostics") or [],
        },
        "candidates": sorted(candidates, key=lambda item: item["candidate_id"]),
    }
    payload["content_hash"] = _sha256(payload)
    return payload


def build_current_model_catalog_read_model(
    *,
    db_paths: Iterable[Path] = (),
    observed_at: datetime | str | None = None,
    repo_root: Path | None = None,
    normalized_metrics: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Entry point local; no red y ningún canario vivo."""
    timestamp = _iso_timestamp(observed_at)
    profiles = []
    discoveries: list[dict[str, Any]] = []
    observed_versions: dict[str, str | None] = {}
    for raw_profile in load_adapter_profiles():
        profile = dict(raw_profile)
        profile["connected"] = profile_is_connected(profile)
        profiles.append(profile)
        profile_id = str(profile.get("id") or "")
        health = (
            profile.get("health") if isinstance(profile.get("health"), Mapping) else {}
        )
        observed_versions[profile_id] = str(health.get("version") or "") or None
        for model in health.get("catalog_models") or ():
            discoveries.append(
                {
                    "profile_id": profile_id,
                    "model": str(model),
                    "source": str(health.get("catalog_source") or "adapter_health"),
                    "observed_at": str(health.get("catalog_checked_at") or timestamp),
                    "provider_version": observed_versions[profile_id],
                }
            )
    report = audit_model_evaluation_coverage(
        observed_at=datetime.fromisoformat(timestamp),
        observed_versions=observed_versions,
        repo_root=repo_root,
    )
    return build_model_catalog_read_model(
        profiles=profiles,
        declared_options_by_profile=model_options(),
        evaluation_report=report,
        runtime_report=collect_model_runtime_observations(db_paths),
        normalized_metrics=normalized_metrics,
        discovered_models=discoveries,
        observed_at=timestamp,
    )


def audit_model_catalog_read_model(
    read_model: Mapping[str, Any],
    *,
    consumer_candidate_ids: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Detecta omisiones, scores irreproducibles y divergencia de consumidores."""
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    candidates = list(read_model.get("candidates") or ())
    ids = [str(item.get("candidate_id") or "") for item in candidates]
    if len(ids) != len(set(ids)):
        failures.append({"code": "duplicate_candidate_id"})
    if read_model.get("schema_version") != MODEL_CATALOG_READ_MODEL_VERSION:
        failures.append({"code": "read_model_version_mismatch"})
    expected_hash = _sha256(
        {k: v for k, v in read_model.items() if k != "content_hash"}
    )
    if read_model.get("content_hash") != expected_hash:
        failures.append({"code": "content_hash_mismatch"})

    profile_ids = {
        str(item.get("identity", {}).get("profile_id") or "") for item in candidates
    }
    excluded = {str(item) for item in read_model.get("excluded_profile_ids") or ()}
    for profile_id in read_model.get("declared_profile_ids") or ():
        if profile_id not in profile_ids and profile_id not in excluded:
            failures.append(
                {"code": "declared_profile_missing", "profile_id": profile_id}
            )

    candidate_keys = {
        (
            str(item.get("identity", {}).get("profile_id") or ""),
            str(item.get("identity", {}).get("model_id") or ""),
        )
        for item in candidates
    }
    for declared in read_model.get("declared_models") or ():
        key = (str(declared.get("profile_id") or ""), str(declared.get("model") or ""))
        if key not in candidate_keys:
            failures.append(
                {
                    "code": "declared_model_missing",
                    "profile_id": key[0],
                    "model": key[1],
                }
            )

    role_keys = {
        (
            str(candidate.get("identity", {}).get("profile_id") or ""),
            str(candidate.get("identity", {}).get("model_id") or ""),
            str(role.get("canonical_role") or ""),
        )
        for candidate in candidates
        for role in candidate.get("roles") or ()
    }
    for declared in read_model.get("declared_role_keys") or ():
        key = (
            str(declared.get("profile_id") or ""),
            str(declared.get("model") or ""),
            str(declared.get("canonical_role") or ""),
        )
        if key not in role_keys:
            failures.append(
                {
                    "code": "declared_role_missing",
                    "profile_id": key[0],
                    "model": key[1],
                    "canonical_role": key[2],
                }
            )

    automatic_candidates = 0
    for candidate in candidates:
        if not candidate.get("roles"):
            warnings.append(
                {
                    "code": "candidate_without_role_evaluation",
                    "candidate_id": candidate.get("candidate_id"),
                }
            )
        for role_row in candidate.get("roles") or ():
            score = role_row.get("score") or {}
            if score.get("auto_eligible") is True:
                automatic_candidates += 1
                if score.get("score") is None:
                    failures.append(
                        {
                            "code": "automatic_candidate_without_score",
                            "input_hash": role_row.get("input_hash"),
                        }
                    )
                provenance = role_row.get("provenance") or {}
                if not any(provenance.values()):
                    failures.append(
                        {
                            "code": "automatic_candidate_without_provenance",
                            "input_hash": role_row.get("input_hash"),
                        }
                    )
            for name, component in (score.get("breakdown") or {}).items():
                if (
                    component.get("status") == "known"
                    and str(component.get("source") or "") == "unknown"
                ):
                    failures.append(
                        {
                            "code": "known_metric_without_source",
                            "component": name,
                            "input_hash": role_row.get("input_hash"),
                        }
                    )
            evaluation = role_row.get("evaluation") or {}
            if evaluation.get("stale_reasons"):
                warnings.append(
                    {
                        "code": "evaluation_stale",
                        "input_hash": role_row.get("input_hash"),
                        "reasons": evaluation["stale_reasons"],
                    }
                )

    canonical_ids = set(ids)
    for consumer, raw_ids in (consumer_candidate_ids or {}).items():
        consumer_ids = {str(item) for item in raw_ids}
        if consumer_ids != canonical_ids:
            failures.append(
                {
                    "code": "consumer_catalog_divergence",
                    "consumer": consumer,
                    "missing": sorted(canonical_ids - consumer_ids),
                    "unexpected": sorted(consumer_ids - canonical_ids),
                }
            )
    return {
        "schema_version": "model_catalog_read_model_audit_v1",
        "read_model_hash": read_model.get("content_hash"),
        "candidate_count": len(candidates),
        "role_score_count": sum(len(item.get("roles") or ()) for item in candidates),
        "automatic_candidate_count": automatic_candidates,
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


def _evaluation_lookup(
    report: Mapping[str, Any],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for model_row in report.get("rows") or ():
        profile_id = str(model_row.get("profile_id") or "")
        model = str(model_row.get("model") or "")
        for role_row in model_row.get("roles") or ():
            role = canonical_role(str(role_row.get("role") or ""))
            if profile_id and model and role:
                result[(profile_id, model, role)] = dict(role_row)
    return result


def _evidence_input(evaluation: Mapping[str, Any], supplied: Any) -> dict[str, Any]:
    override = dict(supplied) if isinstance(supplied, Mapping) else {}
    status = str(evaluation.get("status") or "untested")
    receipts = list(evaluation.get("evidence_receipts") or ())
    return {
        "status": status,
        "classes": list(evaluation.get("evidence_classes") or ["unknown"]),
        "seeds": _safe_int(evaluation.get("seeds")),
        "cases": _safe_int(evaluation.get("cases")),
        "required_tools": list(evaluation.get("required_tools") or ()),
        "covered_tools": list(evaluation.get("covered_tools") or ()),
        "fresh": status == "calibrated" and not evaluation.get("stale_reasons"),
        "provider_version": evaluation.get("provider_version"),
        "evaluated_at": evaluation.get("evaluated_at"),
        "receipts": receipts,
        "unmeasured_constructs": list(evaluation.get("unmeasured_constructs") or ()),
        "goodhart_risk": str(evaluation.get("goodhart_risk") or "unknown"),
        **override,
    }


def _component_inputs(
    *, runtime_row: Mapping[str, Any], supplied: Any, channel: str
) -> dict[str, dict[str, Any]]:
    supplied_components = dict(supplied) if isinstance(supplied, Mapping) else {}
    basis = {
        "api": "api_cost_per_accepted_task",
        "subscription": "subscription_quota_pressure",
        "local": "local_resource_throughput",
        "free_gateway": "gateway_capacity_pressure",
    }.get(channel, "unknown")
    run_count = _safe_int(runtime_row.get("run_count"))
    reliability = (
        round(100 * _safe_int(runtime_row.get("completed_count")) / run_count, 4)
        if run_count >= 3
        else None
    )
    defaults = {
        "quality": {
            "value": None,
            "reason": "normalized_quality_missing",
            "source": "evaluation",
        },
        "capability": {
            "value": None,
            "reason": "normalized_headroom_missing",
            "source": "compatibility",
        },
        "reliability": {
            "value": reliability,
            "reason": "exact_pair_role_runtime_completion_rate"
            if reliability is not None
            else "minimum_three_runs_required",
            "source": "sqlite_runs",
            "sample_count": run_count,
        },
        "economy": {
            "value": None,
            "reason": "raw_channel_metric_not_normalized",
            "source": "sqlite_cost_events",
            "basis": basis,
            "raw_cost_cents": runtime_row.get("cost_cents"),
            "comparison_group": None,
        },
        "speed": {
            "value": None,
            "reason": "raw_latency_not_normalized",
            "source": "sqlite_runs",
            "latency_ms": runtime_row.get("median_duration_ms"),
            "comparison_group": None,
        },
    }
    return {
        name: {**value, **dict(supplied_components.get(name) or {})}
        for name, value in defaults.items()
    }


def _hard_gate_inputs(
    *,
    candidate: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    states = candidate.get("states") or {}
    compatibility_allowed = compatibility.get("allowed") is True
    code = str(compatibility.get("code") or "unknown")

    def state(name: str) -> Any:
        return (states.get(name) or {}).get("value")

    def domain_gate(codes: set[str]) -> bool | None:
        if compatibility_allowed:
            return True
        if code in codes:
            return False
        return None

    gates = {
        "configured": state("configured"),
        "adapter_green": state("adapter_green"),
        "model_verified": state("model_verified"),
        "selectable": state("selectable"),
        "compatible": compatibility_allowed,
        "automatic_policy": (
            True
            if state("manual_only") is False
            else False
            if state("manual_only") is True
            else None
        ),
        "calibrated": str(evaluation.get("status") or "") == "calibrated",
        "fresh": str(evaluation.get("status") or "") == "calibrated"
        and not evaluation.get("stale_reasons"),
        "privacy": domain_gate(
            {"data_classification_required", "confidential_data_forbidden"}
        ),
        "tools": domain_gate({"external_mcp_unsupported", "model_capability_missing"}),
        "workspace": domain_gate(
            {"workspace_read_required", "workspace_write_required"}
        ),
        "structured_output": domain_gate(
            {"structured_output_required", "structured_output_insufficient"}
        ),
        "capacity_available": False if state("blocked") else state("model_verified"),
    }
    return gates


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _rows(conn: sqlite3.Connection, table: str, tables: set[str]) -> list[sqlite3.Row]:
    if table not in tables:
        return []
    return conn.execute(f'SELECT * FROM "{table}"').fetchall()


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _duration_ms(started_at: Any, finished_at: Any) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (finish - start).total_seconds() * 1000.0)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _database_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso_timestamp(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    if value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        normalized = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()
