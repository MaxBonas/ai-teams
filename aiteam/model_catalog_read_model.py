"""Read model shadow del catálogo universal y scoring por rol.

Compone configuración, catálogo, compatibilidad, evaluación y telemetría sin
convertir métricas crudas no comparables en puntuaciones. No modifica routing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from aiteam.model_catalog_projection import build_model_catalog_identity_projection
from aiteam.model_compatibility import compatibility_decision
from aiteam.model_evidence_taxonomy import (
    GENERAL_CAPABILITY_BENCHMARK,
    evidence_taxonomy_contract,
    exact_evidence_kind,
)
from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage
from aiteam.model_normalized_metrics import normalized_metrics_from_evaluation
from aiteam.model_role_scoring import MODEL_ROLE_SCORE_VERSION, score_model_role
from aiteam.policies import CANONICAL_ROLES, canonical_role, role_status, role_tier
from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.user_config import (
    ROLE_CAPABILITY_PROFILES,
    load_adapter_profiles,
    model_options,
    observed_profile_cli_version,
    profile_is_connected,
)


MODEL_CATALOG_READ_MODEL_VERSION = "model_catalog_read_model_v1"


def collect_model_runtime_observations(db_paths: Iterable[Path]) -> dict[str, Any]:
    """Lee runs/costes de SQLite modernas o parciales sin mutarlas."""
    historical: dict[tuple[str, str, str], dict[str, Any]] = {}
    aggregates: dict[tuple[str, str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    database_sources: list[dict[str, Any]] = []

    unique_paths: dict[str, Path] = {}
    duplicate_counts: dict[str, int] = defaultdict(int)
    for raw_path in db_paths:
        db_path = Path(raw_path).resolve()
        path_key = os.path.normcase(str(db_path))
        if path_key in unique_paths:
            duplicate_counts[path_key] += 1
        else:
            unique_paths[path_key] = db_path
    for path_key, count in sorted(duplicate_counts.items()):
        db_path = unique_paths[path_key]
        diagnostics.append(
            {
                "database_id": _database_id(db_path),
                "code": "database_duplicate_ignored",
                "detail": f"{db_path.name}:{count}",
            }
        )

    for path_key in sorted(unique_paths):
        db_path = unique_paths[path_key]
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
        "diagnostics": sorted(
            diagnostics,
            key=lambda item: (
                str(item.get("database_id") or ""),
                str(item.get("code") or ""),
                str(item.get("detail") or ""),
            ),
        ),
    }


def build_model_catalog_read_model(
    *,
    profiles: Sequence[Mapping[str, Any]],
    declared_options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
    evaluation_report: Mapping[str, Any],
    runtime_report: Mapping[str, Any] | None = None,
    normalized_metrics: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None,
    evaluation_version_evidence: Mapping[str, Mapping[str, Any]] | None = None,
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
    options_by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (profile_id, _model), option in option_by_key.items():
        options_by_profile[profile_id].append(option)

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
        role_rows: list[dict[str, Any]] = []
        for role in CANONICAL_ROLES:
            key = (profile_id, model, role)
            runtime_row = runtime_metrics.get(key, {})
            supplied = dict(metric_inputs.get(key) or {})
            model_automatic_policy = (
                (candidate.get("states") or {})
                .get("manual_only", {})
                .get("value")
                is False
            )
            role_nominated = role in set(candidate.get("roles_declared") or ())
            automatic_role_policy = model_automatic_policy and role_nominated
            compatibility = compatibility_decision(
                profile=profile,
                model=option,
                role=role,
                criticality="medium",
                data_class="public",
                required_capabilities=default_capabilities_for_role(role),
                role_profile=ROLE_CAPABILITY_PROFILES.get(role, {}),
                candidate_models=options_by_profile.get(profile_id, ()),
            )
            evaluation_row = _project_evaluation_cell(
                evaluation=evaluation.get(key),
                compatibility=compatibility,
                automatic_policy=automatic_role_policy,
                role=role,
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
                automatic_policy=automatic_role_policy,
                evidence=evidence,
            )
            score = score_model_role(
                candidate=candidate,
                role=role,
                components=components,
                evidence=evidence,
                hard_gates=hard_gates,
            )
            score_inputs = {
                "components": components,
                "evidence": evidence,
                "hard_gates": hard_gates,
                "normalization": (
                    dict(supplied.get("normalization") or {})
                    if not supplied
                    else {
                        "version": "caller_supplied",
                        "scope": "exact_profile_model_role",
                        **dict(supplied.get("normalization") or {}),
                    }
                ),
            }
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
                    "automatic_selection": {
                        "model_policy_enabled": model_automatic_policy,
                        "role_nominated": role_nominated,
                        "eligible_by_policy": automatic_role_policy,
                        "nomination_source": "model_option.best_for",
                    },
                    "runtime_metrics": runtime_row,
                    "score": score,
                    "score_inputs": score_inputs,
                    "provenance": provenance,
                    "input_hash": _sha256(
                        {
                            "candidate_id": candidate["candidate_id"],
                            "role": role,
                            **score_inputs,
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
                    "capability_basis": option.get("capability_basis"),
                    "probe_status": option.get("probe_status"),
                    "probe_reason": option.get("probe_reason"),
                    "probe_version": option.get("probe_version"),
                    "probe_evaluated_at": option.get("probe_evaluated_at"),
                    "probe_receipts": list(option.get("probe_receipts") or ()),
                    "general_capability_benchmark": {
                        "kind": GENERAL_CAPABILITY_BENCHMARK,
                        "value": option.get("research_score"),
                        "source": (
                            "declared_model_option.research_score"
                            if option.get("research_score") is not None
                            else None
                        ),
                        "normalized": False,
                        "role_score_usage": "forbidden_until_comparable_and_proven",
                    },
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
        "evidence_taxonomy": evidence_taxonomy_contract(),
        "canonical_roles": [
            {
                "canonical_role": role,
                "status": role_status(role),
                "tier": role_tier(role),
            }
            for role in CANONICAL_ROLES
        ],
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
        "normalized_metrics": {
            "pair_count": len(metric_inputs),
            "pairs": [
                {
                    "profile_id": profile_id,
                    "model": model,
                    "canonical_role": role,
                    "version": str(
                        (
                            metric_inputs[(profile_id, model, role)].get(
                                "normalization"
                            )
                            or {}
                        ).get("version")
                        or "caller_supplied"
                    ),
                    "evidence_kind": str(
                        (
                            metric_inputs[(profile_id, model, role)].get("evidence")
                            or {}
                        ).get("kind")
                        or "unknown"
                    ),
                    "case_diversity": str(
                        (
                            metric_inputs[(profile_id, model, role)].get("evidence")
                            or {}
                        ).get("case_diversity")
                        or "unknown"
                    ),
                }
                for profile_id, model, role in sorted(metric_inputs)
            ],
        },
        "evaluation_version_evidence": {
            str(profile_id): dict(evidence)
            for profile_id, evidence in sorted(
                (evaluation_version_evidence or {}).items()
            )
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
    version_evidence: dict[str, dict[str, Any]] = {}
    for raw_profile in load_adapter_profiles():
        profile = dict(raw_profile)
        profile["connected"] = profile_is_connected(profile)
        profiles.append(profile)
        profile_id = str(profile.get("id") or "")
        health = (
            profile.get("health") if isinstance(profile.get("health"), Mapping) else {}
        )
        live_version = observed_profile_cli_version(profile)
        observed_versions[profile_id] = (
            live_version or str(health.get("version") or "") or None
        )
        if observed_versions[profile_id]:
            version_evidence[profile_id] = {
                "version": observed_versions[profile_id],
                "source": "live_cli_version" if live_version else "adapter_health",
            }
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
    durable_versions = load_durable_catalog_versions(
        repo_root=repo_root or Path(__file__).resolve().parent.parent,
        observed_at=datetime.fromisoformat(timestamp),
    )
    for profile_id, evidence in durable_versions["profiles"].items():
        if not observed_versions.get(profile_id):
            observed_versions[profile_id] = str(evidence["version"])
            version_evidence[profile_id] = dict(evidence)
    report = audit_model_evaluation_coverage(
        observed_at=datetime.fromisoformat(timestamp),
        observed_versions=observed_versions,
        repo_root=repo_root,
    )
    metric_inputs = normalized_metrics
    if metric_inputs is None:
        metric_inputs = normalized_metrics_from_evaluation(report)["metrics"]
    return build_model_catalog_read_model(
        profiles=profiles,
        declared_options_by_profile=model_options(),
        evaluation_report=report,
        runtime_report=collect_model_runtime_observations(db_paths),
        normalized_metrics=metric_inputs,
        evaluation_version_evidence=version_evidence,
        discovered_models=discoveries,
        observed_at=timestamp,
    )


def load_durable_catalog_versions(
    *, repo_root: Path, observed_at: datetime
) -> dict[str, Any]:
    """Carga el último inventario autenticado fresco; falla cerrado."""
    directory = (
        repo_root / "benchmarks" / "results" / "model_catalog_drift"
    )
    diagnostics: list[str] = []
    for path in sorted(
        directory.glob("model-catalog-drift-*.json"), reverse=True
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            recorded_at = datetime.fromisoformat(
                str(payload.get("observed_at") or "").replace("Z", "+00:00")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            diagnostics.append(f"invalid_receipt:{path.name}")
            continue
        reference = (
            observed_at
            if observed_at.tzinfo
            else observed_at.replace(tzinfo=timezone.utc)
        )
        candidate_time = (
            recorded_at
            if recorded_at.tzinfo
            else recorded_at.replace(tzinfo=timezone.utc)
        )
        age_days = (reference.astimezone(timezone.utc) - candidate_time.astimezone(
            timezone.utc
        )).total_seconds() / 86400
        gates = payload.get("gates") or {}
        if (
            payload.get("benchmark") != "model_catalog_drift_audit"
            or payload.get("ok") is not True
            or not gates
            or any(value is not True for value in gates.values())
            or age_days < 0
            or age_days > 30
        ):
            diagnostics.append(f"receipt_not_authoritative:{path.name}")
            continue
        relative = path.relative_to(repo_root).as_posix()
        profiles: dict[str, dict[str, Any]] = {}
        for catalog in payload.get("catalogs") or ():
            profile_id = str(catalog.get("profile_id") or "")
            version = str(catalog.get("cli_version") or "")
            if (
                profile_id
                and version
                and catalog.get("coverage_ok") is True
                and catalog.get("status") == "current"
            ):
                profiles[profile_id] = {
                    "version": version,
                    "source": f"drift_receipt:{relative}",
                    "observed_at": candidate_time.isoformat(),
                }
        codex = payload.get("codex_catalog") or {}
        codex_version = str(codex.get("installed_version") or "")
        if (
            codex_version
            and codex.get("coverage_ok") is True
            and codex.get("status") == "current"
        ):
            profiles["codex_subscription"] = {
                "version": codex_version,
                "source": f"drift_receipt:{relative}",
                "observed_at": candidate_time.isoformat(),
            }
        return {
            "profiles": profiles,
            "receipt": relative,
            "diagnostics": diagnostics,
        }
    return {"profiles": {}, "receipt": None, "diagnostics": diagnostics}


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
    if read_model.get("evidence_taxonomy") != evidence_taxonomy_contract():
        failures.append({"code": "evidence_taxonomy_contract_mismatch"})
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
    declared_canonical_roles = tuple(
        str(item.get("canonical_role") or "")
        for item in read_model.get("canonical_roles") or ()
    )
    if declared_canonical_roles != CANONICAL_ROLES:
        failures.append(
            {
                "code": "canonical_role_contract_mismatch",
                "expected": list(CANONICAL_ROLES),
                "observed": list(declared_canonical_roles),
            }
        )
    normalized_declared = {
        (
            str(item.get("profile_id") or ""),
            str(item.get("model") or ""),
            canonical_role(str(item.get("canonical_role") or "")),
        )
        for item in (read_model.get("normalized_metrics") or {}).get("pairs") or ()
    }
    normalized_observed = {
        (
            str(candidate.get("identity", {}).get("profile_id") or ""),
            str(candidate.get("identity", {}).get("model_id") or ""),
            canonical_role(str(role.get("canonical_role") or "")),
        )
        for candidate in candidates
        for role in candidate.get("roles") or ()
        if (role.get("score_inputs") or {}).get("normalization")
    }
    if normalized_declared != normalized_observed:
        failures.append(
            {
                "code": "normalized_metric_registry_divergence",
                "missing": sorted(normalized_declared - normalized_observed),
                "unexpected": sorted(normalized_observed - normalized_declared),
            }
        )
    if (read_model.get("normalized_metrics") or {}).get("pair_count") != len(
        normalized_declared
    ):
        failures.append({"code": "normalized_metric_pair_count_mismatch"})
    for profile_id, evidence in (
        read_model.get("evaluation_version_evidence") or {}
    ).items():
        if not str(profile_id) or not str((evidence or {}).get("version") or ""):
            failures.append({"code": "evaluation_version_evidence_invalid"})
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
        observed_roles = tuple(
            str(row.get("canonical_role") or "")
            for row in candidate.get("roles") or ()
        )
        if observed_roles != CANONICAL_ROLES:
            failures.append(
                {
                    "code": "candidate_role_matrix_incomplete",
                    "candidate_id": candidate.get("candidate_id"),
                    "missing": sorted(set(CANONICAL_ROLES) - set(observed_roles)),
                    "unexpected": sorted(set(observed_roles) - set(CANONICAL_ROLES)),
                }
            )
        for role_row in candidate.get("roles") or ():
            score = role_row.get("score") or {}
            compatibility = role_row.get("compatibility") or {}
            evaluation = role_row.get("evaluation") or {}
            score_inputs = role_row.get("score_inputs")
            if compatibility.get("allowed") is not True and score.get("score") is not None:
                failures.append(
                    {
                        "code": "incompatible_role_scored",
                        "candidate_id": candidate.get("candidate_id"),
                        "canonical_role": role_row.get("canonical_role"),
                    }
                )
            automatic_policy = (
                (score_inputs or {}).get("hard_gates", {}).get("automatic_policy")
                if isinstance(score_inputs, Mapping)
                else None
            )
            expected_automatic_policy = (
                (candidate.get("states") or {})
                .get("manual_only", {})
                .get("value")
                is False
                and str(role_row.get("canonical_role") or "")
                in set(candidate.get("roles_declared") or ())
            )
            if automatic_policy is not expected_automatic_policy:
                failures.append(
                    {
                        "code": "automatic_role_policy_mismatch",
                        "candidate_id": candidate.get("candidate_id"),
                        "canonical_role": role_row.get("canonical_role"),
                    }
                )
            if (
                compatibility.get("allowed") is True
                and automatic_policy is True
                and str(evaluation.get("status") or "") != "calibrated"
                and str(evaluation.get("next_action") or "")
                not in {
                    "run_exact_canary",
                    "run_exact_tool_fixture",
                    "no_rerun_until_material_change",
                }
            ):
                failures.append(
                    {
                        "code": "automatic_role_evaluation_debt_missing",
                        "candidate_id": candidate.get("candidate_id"),
                        "canonical_role": role_row.get("canonical_role"),
                    }
                )
            operational_pre_evidence = all(
                (score_inputs or {}).get("hard_gates", {}).get(gate) is True
                for gate in (
                    "configured",
                    "adapter_green",
                    "model_verified",
                    "selectable",
                    "compatible",
                    "automatic_policy",
                    "privacy",
                    "tools",
                    "workspace",
                    "structured_output",
                )
            )
            if (
                operational_pre_evidence
                and str(evaluation.get("status") or "") != "calibrated"
                and not evaluation.get("evidence_receipts")
            ):
                failures.append(
                    {
                        "code": "automatic_operational_evidence_missing",
                        "candidate_id": candidate.get("candidate_id"),
                        "canonical_role": role_row.get("canonical_role"),
                    }
                )
            if not isinstance(score_inputs, Mapping):
                failures.append(
                    {
                        "code": "role_score_inputs_missing",
                        "input_hash": role_row.get("input_hash"),
                    }
                )
            else:
                expected_kind = exact_evidence_kind(
                    str(role_row.get("canonical_role") or "")
                )
                evidence_input = score_inputs.get("evidence") or {}
                normalization = score_inputs.get("normalization") or {}
                if normalization:
                    if evidence_input.get("kind") != expected_kind:
                        failures.append(
                            {
                                "code": "exact_evidence_kind_mismatch",
                                "candidate_id": candidate.get("candidate_id"),
                                "canonical_role": role_row.get("canonical_role"),
                            }
                        )
                    families = {
                        str(item)
                        for item in evidence_input.get("case_families") or ()
                        if str(item)
                    }
                    expected_diversity = len(families) >= 2
                    if (
                        (score_inputs.get("hard_gates") or {}).get(
                            "case_diversity"
                        )
                        is not expected_diversity
                    ):
                        failures.append(
                            {
                                "code": "case_diversity_gate_mismatch",
                                "candidate_id": candidate.get("candidate_id"),
                                "canonical_role": role_row.get("canonical_role"),
                            }
                        )
                for component in (score_inputs.get("components") or {}).values():
                    if isinstance(component, Mapping) and str(
                        component.get("source") or ""
                    ).startswith(GENERAL_CAPABILITY_BENCHMARK):
                        failures.append(
                            {
                                "code": "general_benchmark_leaked_into_role_score",
                                "candidate_id": candidate.get("candidate_id"),
                                "canonical_role": role_row.get("canonical_role"),
                            }
                        )
                role_input = {
                    "candidate_id": candidate.get("candidate_id"),
                    "role": role_row.get("canonical_role"),
                    "components": score_inputs.get("components") or {},
                    "evidence": score_inputs.get("evidence") or {},
                    "hard_gates": score_inputs.get("hard_gates") or {},
                    "normalization": score_inputs.get("normalization") or {},
                }
                if role_row.get("input_hash") != _sha256(role_input):
                    failures.append(
                        {
                            "code": "role_input_hash_mismatch",
                            "input_hash": role_row.get("input_hash"),
                        }
                    )
                try:
                    expected_score = score_model_role(
                        candidate=candidate,
                        role=str(role_row.get("canonical_role") or ""),
                        components=role_input["components"],
                        evidence=role_input["evidence"],
                        hard_gates=role_input["hard_gates"],
                    )
                except (TypeError, ValueError) as exc:
                    failures.append(
                        {
                            "code": "role_score_recompute_failed",
                            "input_hash": role_row.get("input_hash"),
                            "detail": str(exc),
                        }
                    )
                else:
                    if score != expected_score:
                        failures.append(
                            {
                                "code": "role_score_mismatch",
                                "input_hash": role_row.get("input_hash"),
                            }
                        )
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
    for diagnostic in report.get("diagnostics") or ():
        profile_id = str(diagnostic.get("profile_id") or "")
        model = str(diagnostic.get("model") or "")
        role = canonical_role(str(diagnostic.get("role") or ""))
        if not profile_id or not model or not role:
            continue
        row = result.setdefault(
            (profile_id, model, role),
            {"role": role, "status": None, "evidence_receipts": []},
        )
        row.setdefault("diagnostic_reason", diagnostic.get("reason"))
        row.setdefault(
            "diagnostic_receipts",
            list(diagnostic.get("receipts") or ()),
        )
        row.setdefault(
            "diagnostic_validation_errors",
            list(diagnostic.get("validation_errors") or ()),
        )
        row.setdefault(
            "diagnostic_stale_reasons",
            list(diagnostic.get("stale_reasons") or ()),
        )
        row.setdefault("rerun_policy", diagnostic.get("rerun_policy"))
        row.setdefault(
            "material_change_triggers",
            list(diagnostic.get("material_change_triggers") or ()),
        )
        row.setdefault("evaluated_at", diagnostic.get("evaluated_at"))
        row.setdefault("provider_version", diagnostic.get("provider_version"))
    return result


def _project_evaluation_cell(
    *,
    evaluation: Mapping[str, Any] | None,
    compatibility: Mapping[str, Any],
    automatic_policy: bool,
    role: str,
) -> dict[str, Any]:
    """Hace explícita la evidencia o deuda exacta sin extrapolar entre roles."""
    if compatibility.get("allowed") is not True:
        prior = dict(evaluation) if isinstance(evaluation, Mapping) else {}
        return {
            "status": "incompatible",
            "scope": "exact_profile_model_role",
            "reason_code": str(compatibility.get("code") or "unknown"),
            "reason": str(compatibility.get("reason") or ""),
            "score_permitted": False,
            "next_action": None,
            "prior_evaluation_status": prior.get("status"),
            "evidence_receipts": list(prior.get("evidence_receipts") or ()),
            "diagnostic_reason": prior.get("diagnostic_reason"),
            "diagnostic_receipts": list(prior.get("diagnostic_receipts") or ()),
            "diagnostic_validation_errors": list(
                prior.get("diagnostic_validation_errors") or ()
            ),
            "diagnostic_stale_reasons": list(
                prior.get("diagnostic_stale_reasons") or ()
            ),
            "rerun_policy": prior.get("rerun_policy"),
            "material_change_triggers": list(
                prior.get("material_change_triggers") or ()
            ),
            "evaluated_at": prior.get("evaluated_at"),
            "provider_version": prior.get("provider_version"),
        }
    if isinstance(evaluation, Mapping):
        projected = dict(evaluation)
        projected.setdefault("scope", "exact_profile_model_role")
        projected.setdefault("score_permitted", projected.get("status") == "calibrated")
        if (
            compatibility.get("allowed") is True
            and automatic_policy
            and str(projected.get("status") or "") != "calibrated"
        ):
            has_exact_receipt = bool(projected.get("evidence_receipts"))
            projected.setdefault(
                "next_action",
                (
                    "no_rerun_until_material_change"
                    if has_exact_receipt
                    and str(projected.get("status") or "")
                    in {"partial", "failed"}
                    else "run_exact_tool_fixture"
                    if role == "mcp_operator"
                    else "run_exact_canary"
                ),
            )
        return projected

    if not automatic_policy:
        return {
            "status": "compatible_not_nominated",
            "scope": "exact_profile_model_role",
            "reason_code": "role_not_nominated_for_automatic_selection",
            "score_permitted": False,
            "next_action": None,
            "evidence_receipts": [],
        }
    return {
        "status": (
            "requires_tool_fixture" if role == "mcp_operator" else "requires_canary"
        ),
        "scope": "exact_profile_model_role",
        "reason_code": "exact_role_evidence_missing",
        "score_permitted": False,
        "next_action": (
            "run_exact_tool_fixture"
            if role == "mcp_operator"
            else "run_exact_canary"
        ),
        "evidence_receipts": [],
    }


def _evidence_input(evaluation: Mapping[str, Any], supplied: Any) -> dict[str, Any]:
    override = dict(supplied) if isinstance(supplied, Mapping) else {}
    status = str(evaluation.get("status") or "untested")
    receipts = list(evaluation.get("evidence_receipts") or ())
    return {
        "status": status,
        "kind": evaluation.get("kind"),
        "classes": list(evaluation.get("evidence_classes") or ["unknown"]),
        "seeds": _safe_int(evaluation.get("seeds")),
        "cases": _safe_int(evaluation.get("cases")),
        "case_families": list(evaluation.get("case_families") or ()),
        "case_family_count": _safe_int(evaluation.get("case_family_count")),
        "case_diversity": str(evaluation.get("case_diversity") or "unknown"),
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
        "local": "zero_external_cost",
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
            "value": 100.0 if channel == "local" else None,
            "reason": (
                "local_zero_external_cost_and_quota"
                if channel == "local"
                else "raw_channel_metric_not_normalized"
            ),
            "source": (
                "channel_economy_policy"
                if channel == "local"
                else "sqlite_cost_events"
            ),
            "basis": basis,
            "raw_cost_cents": runtime_row.get("cost_cents"),
            "comparison_group": (
                "zero_external_cost:local" if channel == "local" else None
            ),
            "burden": 0.0 if channel == "local" else None,
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
    automatic_policy: bool,
    evidence: Mapping[str, Any],
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

    case_families = {
        str(item)
        for item in evidence.get("case_families") or ()
        if str(item)
    }
    gates = {
        "configured": state("configured"),
        "adapter_green": state("adapter_green"),
        "model_verified": state("model_verified"),
        "selectable": state("selectable"),
        "compatible": compatibility_allowed,
        "automatic_policy": automatic_policy,
        "calibrated": str(evaluation.get("status") or "") == "calibrated",
        "fresh": str(evaluation.get("status") or "") == "calibrated"
        and not evaluation.get("stale_reasons"),
        # La frescura gobierna promoción, no borra la diversidad histórica
        # de una métrica exacta que sigue visible para diagnóstico.
        "case_diversity": (
            len(case_families) >= 2 if case_families else None
        ),
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
    canonical_path = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(canonical_path.encode("utf-8")).hexdigest()[:16]


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
