"""Probes neutrales y diffs semánticos de cambios de proveedor.

Los readers específicos se inyectan como callbacks read-only. Este módulo
normaliza únicamente campos permitidos, conserva fallos de observación como
``unknown`` y no instala, autentica, actualiza ni concede routing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from aiteam.provider_change_intelligence import (
    load_provider_change_contract,
)

SNAPSHOT_SCHEMA = "provider_change_snapshot_v1"
DIFF_SCHEMA = "provider_change_diff_v1"
PROBE_STATUSES = frozenset(
    {"observed", "offline", "rate_limited", "auth_required", "failed"}
)
COMPATIBILITY_STATES = frozenset(
    {"compatible", "incompatible", "unknown", "not_applicable"}
)
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:api_?key|token|password|secret|credential|authorization)(?:$|_)",
    re.IGNORECASE,
)
_SEMVER = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?![0-9A-Za-z.-])"
)
_MODEL_FIELDS = (
    "aliases",
    "context",
    "tools",
    "structured_output",
    "price",
    "quota",
    "lifecycle",
)
_DECISION_ORDER = {
    "none": 0,
    "newer_available": 1,
    "update_recommended": 2,
    "update_required": 3,
    "blocked": 4,
    "unknown": 5,
}


def run_read_only_probe(
    component: Mapping[str, Any],
    reader: Callable[[], Mapping[str, Any]],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Ejecuta un reader inyectado una vez y normaliza su salida exacta."""
    try:
        observation = reader()
    except PermissionError:
        observation = {"status": "auth_required"}
    except TimeoutError:
        observation = {"status": "offline"}
    except OSError:
        observation = {"status": "failed"}
    if not isinstance(observation, Mapping):
        raise TypeError("provider probe reader must return an object")
    return build_provider_snapshot(
        component,
        observation,
        observed_at=observed_at,
    )


def build_provider_snapshot(
    component: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    contract = load_provider_change_contract()
    timestamp = _timestamp(observed_at)
    status = str(observation.get("status") or "").strip()
    if status not in PROBE_STATUSES:
        raise ValueError("provider probe status drift")
    allowed = {
        "status",
        "installed_version",
        "latest_known_version",
        "compatibility",
        "lifecycle",
        "dimensions",
        "source_overrides",
    }
    unexpected = set(observation) - allowed
    if unexpected:
        raise ValueError(
            f"provider probe contains unsupported fields: {sorted(unexpected)}"
        )
    _reject_secret_keys(observation)
    facts = deepcopy(component.get("facts"))
    if not isinstance(facts, dict):
        raise TypeError("provider component facts must be an object")
    compatibility = _compatibility(observation.get("compatibility"))
    lifecycle = _lifecycle(observation.get("lifecycle"))
    dimensions = observation.get("dimensions") or {}
    if not isinstance(dimensions, Mapping):
        raise TypeError("provider probe dimensions must be an object")
    allowed_dimensions = set(component.get("dimensions") or [])
    if not set(dimensions) <= allowed_dimensions:
        raise ValueError("provider probe dimension is outside its surface")

    if status == "observed":
        _observe_version(
            facts,
            "installed_version",
            observation.get("installed_version"),
            timestamp=timestamp,
            overrides=observation.get("source_overrides"),
            contract=contract,
        )
        _observe_version(
            facts,
            "latest_known_version",
            observation.get("latest_known_version"),
            timestamp=timestamp,
            overrides=observation.get("source_overrides"),
            contract=contract,
        )
    else:
        dimensions = {}
        compatibility = {
            "installed": "unknown",
            "latest_known": "unknown",
        }
        lifecycle = {
            "installed": "unknown",
            "latest_known": "unknown",
        }
        for fact_name in ("installed_version", "latest_known_version"):
            facts[fact_name] = {
                **facts[fact_name],
                "state": "unknown",
                "value": None,
                "reason": status,
                "source": {
                    **facts[fact_name]["source"],
                    "observed_at": None,
                },
            }

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "observed_at": timestamp,
        "probe_status": status,
        "identity": {
            key: component.get(key)
            for key in (
                "scope_id",
                "profile_id",
                "channel_id",
                "provider_id",
                "component_id",
                "surface",
            )
        },
        "facts": facts,
        "compatibility": compatibility,
        "lifecycle": lifecycle,
        "dimensions": _canonical_json(dimensions),
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "routing_authority_granted": False,
            "update_attempted": False,
        },
    }
    snapshot["snapshot_sha256"] = _digest(snapshot)
    validate_provider_snapshot(snapshot, component=component)
    return snapshot


def compare_provider_snapshots(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    validate_provider_snapshot(previous)
    validate_provider_snapshot(current)
    if previous["identity"] != current["identity"]:
        raise ValueError("provider snapshot identities differ")
    changes: list[dict[str, Any]] = []
    decision = "none"

    if current["probe_status"] != "observed":
        changes.append(
            _change(
                "observation_unavailable",
                "probe_status",
                previous["probe_status"],
                current["probe_status"],
                severity="warning",
                actionability="observe_later",
            )
        )
        decision = "unknown"
    else:
        version_changes, version_decision = _version_diff(previous, current)
        changes.extend(version_changes)
        decision = _max_decision(decision, version_decision)
        dimension_changes, dimension_decision = _dimension_diff(
            previous, current
        )
        changes.extend(dimension_changes)
        decision = _max_decision(decision, dimension_decision)

    report = {
        "schema_version": DIFF_SCHEMA,
        "identity": deepcopy(current["identity"]),
        "previous_snapshot_sha256": previous["snapshot_sha256"],
        "current_snapshot_sha256": current["snapshot_sha256"],
        "changes": changes,
        "summary": {
            "status": (
                "unknown"
                if decision == "unknown"
                else "changed"
                if changes
                else "no_change"
            ),
            "decision": decision,
            "change_count": len(changes),
            "calibration_impacted": any(
                row["calibration_impact"] for row in changes
            ),
            "routing_change_allowed": False,
            "automatic_update_allowed": False,
        },
    }
    report["diff_sha256"] = _digest(report)
    validate_provider_diff(report)
    return report


def validate_provider_snapshot(
    snapshot: Mapping[str, Any],
    *,
    component: Mapping[str, Any] | None = None,
) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ValueError("provider snapshot schema drift")
    _timestamp(snapshot.get("observed_at"))
    if snapshot.get("probe_status") not in PROBE_STATUSES:
        raise ValueError("provider snapshot status drift")
    if snapshot.get("scope") != {
        "read_only": True,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "routing_authority_granted": False,
        "update_attempted": False,
    }:
        raise ValueError("provider snapshot scope drift")
    identity = snapshot.get("identity")
    if not isinstance(identity, Mapping):
        raise TypeError("provider snapshot identity must be an object")
    required_identity = {
        "scope_id",
        "profile_id",
        "channel_id",
        "provider_id",
        "component_id",
        "surface",
    }
    if set(identity) != required_identity:
        raise ValueError("provider snapshot identity fields drift")
    if component is not None:
        expected = {key: component.get(key) for key in required_identity}
        if dict(identity) != expected:
            raise ValueError("provider snapshot component identity drift")
        if not set(snapshot.get("dimensions") or {}) <= set(
            component.get("dimensions") or []
        ):
            raise ValueError("provider snapshot dimensions drift")
    if set(snapshot.get("facts") or {}) != {
        "installed_version",
        "supported_version",
        "latest_known_version",
    }:
        raise ValueError("provider snapshot fact coverage drift")
    contract = load_provider_change_contract()
    for fact_name, fact in snapshot["facts"].items():
        _validate_snapshot_fact(fact_name, fact, contract)
    _compatibility(snapshot.get("compatibility"))
    _lifecycle(snapshot.get("lifecycle"))
    _reject_secret_keys(snapshot)
    expected = _digest(
        {
            key: deepcopy(value)
            for key, value in snapshot.items()
            if key != "snapshot_sha256"
        }
    )
    if snapshot.get("snapshot_sha256") != expected:
        raise ValueError("provider snapshot digest drift")


def validate_provider_diff(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != DIFF_SCHEMA:
        raise ValueError("provider diff schema drift")
    summary = report.get("summary")
    changes = report.get("changes")
    if not isinstance(summary, Mapping) or not isinstance(changes, list):
        raise TypeError("provider diff summary/changes drift")
    if summary.get("change_count") != len(changes):
        raise ValueError("provider diff change count drift")
    if summary.get("routing_change_allowed") is not False:
        raise ValueError("provider diff cannot grant routing")
    if summary.get("automatic_update_allowed") is not False:
        raise ValueError("provider diff cannot update automatically")
    if summary.get("decision") not in _DECISION_ORDER:
        raise ValueError("provider diff decision drift")
    expected = _digest(
        {key: deepcopy(value) for key, value in report.items() if key != "diff_sha256"}
    )
    if report.get("diff_sha256") != expected:
        raise ValueError("provider diff digest drift")


def _version_diff(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    changes: list[dict[str, Any]] = []
    decision = "none"
    before_installed = _fact_value(previous, "installed_version")
    installed = _fact_value(current, "installed_version")
    before_latest = _fact_value(previous, "latest_known_version")
    latest = _fact_value(current, "latest_known_version")
    supported = _fact_value(current, "supported_version")
    if installed != before_installed:
        installed_order = _compare_versions(installed, before_installed)
        decision = _max_decision(decision, "newer_available")
        changes.append(
            _change(
                "installed_upgraded"
                if installed_order == 1
                else "installed_downgraded"
                if installed_order == -1
                else "installed_version_changed",
                "installed_version",
                before_installed,
                installed,
                severity="warning",
                actionability="compatibility_review",
                calibration_impact=True,
            )
        )
    if latest != before_latest:
        changes.append(
            _change(
                "release_changed",
                "latest_known_version",
                before_latest,
                latest,
                severity="info",
                actionability="review_release",
            )
        )
    if (
        installed
        and installed != before_installed
        and _is_prerelease(installed)
    ):
        changes.append(
            _change(
                "prerelease_installed",
                "installed_version",
                None,
                installed,
                severity="warning",
                actionability="compatibility_review",
                calibration_impact=True,
            )
        )
    installed_compat = current["compatibility"]["installed"]
    latest_compat = current["compatibility"]["latest_known"]
    installed_lifecycle = current["lifecycle"]["installed"]
    latest_lifecycle = current["lifecycle"]["latest_known"]
    if installed_compat == "incompatible" or installed_lifecycle == "retired":
        decision = "blocked"
        changes.append(
            _change(
                "installed_incompatible"
                if installed_compat == "incompatible"
                else "installed_retired",
                "installed_version",
                installed,
                supported,
                severity="critical",
                actionability="block_and_remediate",
                calibration_impact=True,
            )
        )
    elif installed_lifecycle == "deprecated":
        decision = "update_required"
        changes.append(
            _change(
                "installed_deprecated",
                "lifecycle",
                "current",
                "deprecated",
                severity="error",
                actionability="plan_update",
                calibration_impact=True,
            )
        )
    release_order = _compare_versions(latest, installed)
    newer = release_order == 1
    if newer:
        if latest_compat == "compatible":
            next_decision = (
                "update_required"
                if latest_lifecycle == "required"
                else "update_recommended"
            )
        else:
            next_decision = "newer_available"
        decision = _max_decision(decision, next_decision)
        changes.append(
            _change(
                next_decision,
                "latest_known_version",
                installed,
                latest,
                severity=(
                    "error"
                    if next_decision == "update_required"
                    else "warning"
                    if next_decision == "update_recommended"
                    else "info"
                ),
                actionability=(
                    "plan_update"
                    if next_decision != "newer_available"
                    else "compatibility_review"
                ),
                calibration_impact=next_decision != "newer_available",
            )
        )
    elif latest and installed and latest != installed and release_order is None:
        decision = _max_decision(decision, "newer_available")
        changes.append(
            _change(
                "release_order_unknown",
                "latest_known_version",
                installed,
                latest,
                severity="info",
                actionability="compatibility_review",
            )
        )
    return _dedupe_changes(changes), decision


def _dimension_diff(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    surface = str(current["identity"]["surface"])
    before = previous.get("dimensions") or {}
    after = current.get("dimensions") or {}
    if surface == "model_catalog":
        return _model_catalog_diff(before, after)
    changes: list[dict[str, Any]] = []
    decision = "none"
    for dimension in sorted(set(before) | set(after)):
        if _canonical_json(before.get(dimension)) == _canonical_json(
            after.get(dimension)
        ):
            continue
        severity, actionability, candidate = _dimension_policy(
            surface, dimension
        )
        changes.append(
            _change(
                f"{surface}_{dimension}_changed",
                dimension,
                before.get(dimension),
                after.get(dimension),
                severity=severity,
                actionability=actionability,
                calibration_impact=dimension
                in {
                    "protocol_version",
                    "capabilities",
                    "tools",
                    "api_version",
                    "endpoint",
                    "auth_schema",
                    "request_schema",
                    "response_schema",
                    "translation_contract",
                },
            )
        )
        decision = _max_decision(decision, candidate)
    return changes, decision


def _model_catalog_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    old_models = _models_by_id(before.get("model_id"))
    new_models = _models_by_id(after.get("model_id"))
    changes: list[dict[str, Any]] = []
    renamed_old: set[str] = set()
    renamed_new: set[str] = set()
    for new_id, model in new_models.items():
        aliases = set(model.get("aliases") or [])
        for old_id in sorted(set(old_models) & aliases):
            if old_id != new_id and old_id not in new_models:
                renamed_old.add(old_id)
                renamed_new.add(new_id)
                changes.append(
                    _change(
                        "model_renamed",
                        "model_id",
                        old_id,
                        new_id,
                        severity="warning",
                        actionability="owner_classification",
                        calibration_impact=True,
                    )
                )
    for model_id in sorted(set(new_models) - set(old_models) - renamed_new):
        changes.append(
            _change(
                "model_added",
                "model_id",
                None,
                model_id,
                severity="info",
                actionability="owner_unclassified",
            )
        )
    for model_id in sorted(set(old_models) - set(new_models) - renamed_old):
        changes.append(
            _change(
                "model_removed",
                "model_id",
                model_id,
                None,
                severity="critical",
                actionability="block_new_selection",
                calibration_impact=True,
            )
        )
    for model_id in sorted(set(old_models) & set(new_models)):
        old = old_models[model_id]
        new = new_models[model_id]
        for field in _MODEL_FIELDS:
            if _canonical_json(old.get(field)) == _canonical_json(
                new.get(field)
            ):
                continue
            changes.append(
                _change(
                    f"model_{field}_changed",
                    field,
                    old.get(field),
                    new.get(field),
                    severity=(
                        "warning"
                        if field in {"aliases", "price", "quota"}
                        else "error"
                    ),
                    actionability=(
                        "refresh_economics"
                        if field in {"price", "quota"}
                        else "compatibility_review"
                    ),
                    calibration_impact=field
                    in {
                        "context",
                        "tools",
                        "structured_output",
                        "lifecycle",
                    },
                )
            )
    decision = "none"
    compatibility_kinds = {
        "model_removed",
        "model_context_changed",
        "model_tools_changed",
        "model_structured_output_changed",
        "model_lifecycle_changed",
    }
    if any(row["kind"] in compatibility_kinds for row in changes):
        decision = "blocked"
    elif changes:
        decision = "newer_available"
    return changes, decision


def _observe_version(
    facts: dict[str, Any],
    fact_name: str,
    raw_value: Any,
    *,
    timestamp: str,
    overrides: Any,
    contract: Mapping[str, Any],
) -> None:
    value = str(raw_value or "").strip()
    if not value:
        facts[fact_name] = {
            **facts[fact_name],
            "state": "unknown",
            "value": None,
            "reason": "not_reported",
            "source": {
                **facts[fact_name]["source"],
                "observed_at": None,
            },
        }
        return
    override_rows = overrides if isinstance(overrides, Mapping) else {}
    override = override_rows.get(fact_name)
    override = override if isinstance(override, Mapping) else {}
    source = {
        **facts[fact_name]["source"],
        **{
            key: override[key]
            for key in ("kind", "reference", "official")
            if key in override
        },
        "observed_at": timestamp,
    }
    if source["kind"] not in contract["facts"][fact_name]["allowed_sources"]:
        raise ValueError(f"source {source['kind']} cannot establish {fact_name}")
    facts[fact_name] = {
        "state": "known",
        "value": value,
        "reason": None,
        "source": source,
    }


def _validate_snapshot_fact(
    fact_name: str,
    fact: Any,
    contract: Mapping[str, Any],
) -> None:
    if not isinstance(fact, Mapping):
        raise TypeError("provider snapshot fact must be an object")
    if set(fact) != {"state", "value", "reason", "source"}:
        raise ValueError("provider snapshot fact fields drift")
    state = str(fact["state"])
    value = fact["value"]
    if state not in contract["fact_states"]:
        raise ValueError("provider snapshot fact state drift")
    if (state == "known") != bool(str(value or "").strip()):
        raise ValueError("provider snapshot known/value invariant failed")
    source = fact["source"]
    if not isinstance(source, Mapping):
        raise TypeError("provider snapshot source must be an object")
    if set(source) != {"kind", "reference", "official", "observed_at"}:
        raise ValueError("provider snapshot source fields drift")
    kind = str(source["kind"])
    if kind not in contract["facts"][fact_name]["allowed_sources"]:
        raise ValueError(f"source {kind} cannot establish {fact_name}")
    if not str(source["reference"] or "").strip():
        raise ValueError("provider snapshot source reference is required")
    requires_official = contract["source_kinds"][kind]["requires_official"]
    if bool(source["official"]) is not bool(requires_official):
        raise ValueError("provider snapshot source authority drift")
    if state == "known":
        _timestamp(source["observed_at"])
    elif source["observed_at"] is not None:
        raise ValueError("unknown provider fact cannot claim observation")


def _compatibility(value: Any) -> dict[str, str]:
    rows = value if isinstance(value, Mapping) else {}
    result = {
        "installed": str(rows.get("installed") or "unknown"),
        "latest_known": str(rows.get("latest_known") or "unknown"),
    }
    if not set(result.values()) <= COMPATIBILITY_STATES:
        raise ValueError("provider compatibility state drift")
    return result


def _lifecycle(value: Any) -> dict[str, str]:
    rows = value if isinstance(value, Mapping) else {}
    allowed = {"active", "deprecated", "retired", "required", "unknown"}
    result = {
        "installed": str(rows.get("installed") or "unknown"),
        "latest_known": str(rows.get("latest_known") or "unknown"),
    }
    if not set(result.values()) <= allowed:
        raise ValueError("provider lifecycle state drift")
    return result


def _models_by_id(value: Any) -> dict[str, dict[str, Any]]:
    if value in (None, []):
        return {}
    if not isinstance(value, list):
        raise TypeError("model_id dimension must be a list")
    rows: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, Mapping):
            raise TypeError("model catalog row must be an object")
        model_id = str(raw.get("id") or "").strip()
        if not model_id or model_id in rows:
            raise ValueError("model catalog identities must be unique")
        unsupported = set(raw) - {"id", *_MODEL_FIELDS}
        if unsupported:
            raise ValueError("model catalog contains unsupported metadata")
        rows[model_id] = _canonical_json(dict(raw))
    return rows


def _dimension_policy(
    surface: str, dimension: str
) -> tuple[str, str, str]:
    if surface == "mcp_server" and dimension in {
        "protocol_version",
        "server_info",
        "capabilities",
        "tools",
    }:
        return "error", "compatibility_review", "blocked"
    if surface == "sdk_api" and dimension in {
        "api_version",
        "endpoint",
        "auth_schema",
        "request_schema",
        "response_schema",
    }:
        return "error", "compatibility_review", "blocked"
    if surface == "internal_adapter":
        return "warning", "run_contract_tests", "update_required"
    return "info", "review_change", "newer_available"


def _change(
    kind: str,
    dimension: str,
    before: Any,
    after: Any,
    *,
    severity: str,
    actionability: str,
    calibration_impact: bool = False,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "dimension": dimension,
        "before": _canonical_json(before),
        "after": _canonical_json(after),
        "severity": severity,
        "actionability": actionability,
        "calibration_impact": calibration_impact,
    }


def _fact_value(snapshot: Mapping[str, Any], name: str) -> str | None:
    value = snapshot["facts"][name].get("value")
    return str(value) if value is not None else None


def _compare_versions(
    left: str | None, right: str | None
) -> int | None:
    left_parsed = _parse_version(left)
    right_parsed = _parse_version(right)
    if left_parsed is None or right_parsed is None:
        return None
    if left_parsed == right_parsed:
        return 0
    return 1 if left_parsed > right_parsed else -1


def _parse_version(value: str | None) -> tuple[int, int, int, int, str] | None:
    match = _SEMVER.search(str(value or ""))
    if not match:
        return None
    prerelease = match.group(4)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        prerelease or "",
    )


def _is_prerelease(value: str) -> bool:
    parsed = _SEMVER.search(value)
    return bool(parsed and parsed.group(4))


def _max_decision(left: str, right: str) -> str:
    return left if _DECISION_ORDER[left] >= _DECISION_ORDER[right] else right


def _dedupe_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = _digest(row)
        if fingerprint not in seen:
            seen.add(fingerprint)
            output.append(row)
    return output


def _timestamp(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("provider observation timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider observation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError("provider observation contains a secret field")
            _reject_secret_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_secret_keys(nested)


def _canonical_json(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
