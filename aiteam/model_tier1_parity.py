"""Auditoría transversal de autoridad Tier 1.

La autoridad se compara como dato canónico; este módulo no la reconstruye desde
tier, nombre, ``best_for`` ni score.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiteam.db.model_score_snapshots import persist_model_role_score_snapshot
from aiteam.model_catalog_api import (
    filter_catalog_candidates,
    summarize_tier1_authority,
)
from aiteam.model_catalog_read_model import audit_model_catalog_read_model
from aiteam.model_selection import build_contextual_model_selection
from aiteam.model_tier_coverage import (
    TIER_1_ROLE_TO_LANE,
    TIER_COVERAGE_POLICY_VERSION,
    tier1_authority_gate,
)

PARITY_AUDIT_VERSION = "tier1_authority_parity_audit_v1"


def audit_tier1_authority_parity(
    read_model: Mapping[str, Any],
    *,
    profiles: Iterable[Mapping[str, Any]],
    options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
    repo_root: Path,
) -> dict[str, Any]:
    """Compara read model, API, UI, selector, snapshots y casos negativos."""
    failures: list[dict[str, Any]] = []
    profile_rows = [deepcopy(dict(row)) for row in profiles]
    option_rows = {
        str(profile_id): [deepcopy(dict(row)) for row in rows]
        for profile_id, rows in options_by_profile.items()
    }
    canonical = _authority_map(read_model.get("candidates") or ())

    read_model_audit = audit_model_catalog_read_model(read_model)
    if not read_model_audit["ok"]:
        _failure(
            failures,
            "read_model",
            "read_model_integrity_failed",
            details=read_model_audit["failures"],
        )

    api_candidates = filter_catalog_candidates(read_model)
    api_authority = _authority_map(api_candidates)
    _compare_maps(failures, "api", canonical, api_authority)
    api_filters = _audit_api_filters(read_model, canonical, failures)
    api_coverage = _audit_api_coverage(api_candidates, canonical, failures)

    selection = _audit_selection(
        read_model,
        canonical,
        profiles=profile_rows,
        options_by_profile=option_rows,
        failures=failures,
    )
    snapshots = _audit_snapshots(canonical, repo_root=repo_root, failures=failures)
    negative_matrix = _audit_negative_matrix(
        read_model,
        profiles=profile_rows,
        options_by_profile=option_rows,
        failures=failures,
    )
    ui = _audit_ui_contract(repo_root, failures=failures)

    return {
        "schema_version": PARITY_AUDIT_VERSION,
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "read_model": {
            "schema_version": read_model.get("schema_version"),
            "content_hash": read_model.get("content_hash"),
            "candidate_count": len(read_model.get("candidates") or ()),
            "authority_cell_count": len(canonical),
            "audit_ok": read_model_audit["ok"],
        },
        "surfaces": {
            "api_projection": {
                "checked_cells": len(canonical),
                "filter_cases": api_filters,
                "coverage_roles": api_coverage,
            },
            "contextual_selection": selection,
            "snapshots": snapshots,
            "frontend_contract": ui,
        },
        "negative_matrix": negative_matrix,
        "failure_count": len(failures),
        "failures": failures,
        "ok": not failures,
    }


def _authority_map(
    candidates: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        for role in candidate.get("roles") or ():
            role_key = str(role.get("canonical_role") or "")
            if candidate_id and role_key in TIER_1_ROLE_TO_LANE:
                output[(candidate_id, role_key)] = deepcopy(
                    dict(role.get("tier1_authority") or {})
                )
    return output


def _compare_maps(
    failures: list[dict[str, Any]],
    surface: str,
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    actual: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            _failure(
                failures,
                surface,
                "tier1_authority_divergence",
                candidate_id=key[0],
                role=key[1],
            )


def _audit_api_filters(
    read_model: Mapping[str, Any],
    canonical: Mapping[tuple[str, str], Mapping[str, Any]],
    failures: list[dict[str, Any]],
) -> int:
    cases = 0
    for lane in sorted(set(TIER_1_ROLE_TO_LANE.values())):
        expected = {
            candidate_id
            for (candidate_id, _role), authority in canonical.items()
            if authority.get("lane") == lane and authority.get("enabled") is True
        }
        actual = {
            str(candidate.get("candidate_id") or "")
            for candidate in filter_catalog_candidates(
                read_model, tier1_authority=lane
            )
        }
        cases += 1
        if actual != expected:
            _failure(
                failures,
                "api",
                "authority_filter_divergence",
                lane=lane,
                expected=sorted(expected),
                actual=sorted(actual),
            )
    return cases


def _audit_api_coverage(
    candidates: list[Mapping[str, Any]],
    canonical: Mapping[tuple[str, str], Mapping[str, Any]],
    failures: list[dict[str, Any]],
) -> int:
    summary = summarize_tier1_authority(candidates)
    rows = {
        str(row.get("canonical_role") or ""): row
        for row in summary.get("roles") or ()
    }
    for role in TIER_1_ROLE_TO_LANE:
        expected = sum(
            authority.get("enabled") is True
            for (candidate_id, role_key), authority in canonical.items()
            if candidate_id and role_key == role
        )
        actual = int((rows.get(role) or {}).get("enabled_count") or 0)
        if actual != expected:
            _failure(
                failures,
                "api",
                "authority_coverage_divergence",
                role=role,
                expected=expected,
                actual=actual,
            )
    return len(TIER_1_ROLE_TO_LANE)


def _audit_selection(
    read_model: Mapping[str, Any],
    canonical: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    profiles: list[dict[str, Any]],
    options_by_profile: dict[str, list[dict[str, Any]]],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    checked = 0
    active = _active_candidate_ids(read_model, options_by_profile)
    for role in TIER_1_ROLE_TO_LANE:
        projection = build_contextual_model_selection(
            read_model,
            role=role,
            profiles=profiles,
            options_by_profile=options_by_profile,
        )
        rows = {
            str(row.get("candidate_id") or ""): row
            for row in projection.get("candidates") or ()
        }
        for (candidate_id, role_key), authority in canonical.items():
            if role_key != role or candidate_id not in active:
                continue
            row = rows.get(candidate_id)
            checked += 1
            if row is None:
                _failure(
                    failures,
                    "selection",
                    "active_candidate_missing",
                    candidate_id=candidate_id,
                    role=role,
                )
                continue
            expected = tier1_authority_gate(role=role, authority=authority)
            actual = row.get("tier1_authority_gate") or {}
            hard_gate = (
                (row.get("selection_score") or {})
                .get("hard_gates", {})
                .get("tier1_authority", {})
            )
            if (
                row.get("tier1_authority") != authority
                or actual.get("allowed") is not expected["allowed"]
                or actual.get("code") != expected["code"]
                or hard_gate.get("passed") is not expected["allowed"]
            ):
                _failure(
                    failures,
                    "selection",
                    "authority_gate_divergence",
                    candidate_id=candidate_id,
                    role=role,
                )
            if expected["allowed"] is False and (
                row.get("owner_selectable") is True
                or (row.get("selection_score") or {}).get("auto_eligible") is True
            ):
                _failure(
                    failures,
                    "selection",
                    "denied_authority_became_selectable",
                    candidate_id=candidate_id,
                    role=role,
                )
    return {"checked_active_cells": checked, "roles": len(TIER_1_ROLE_TO_LANE)}


def _audit_snapshots(
    canonical: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    repo_root: Path,
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    checked = 0
    with tempfile.TemporaryDirectory(prefix="aiteam-tier1-parity-") as temp:
        db_path = Path(temp) / "parity.sqlite"
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.executescript(
                (repo_root / "aiteam" / "db" / "schema.sql").read_text(
                    encoding="utf-8"
                )
            )
        for role, lane in TIER_1_ROLE_TO_LANE.items():
            enabled = next(
                (
                    (candidate_id, authority)
                    for (candidate_id, role_key), authority in canonical.items()
                    if role_key == role and authority.get("enabled") is True
                ),
                None,
            )
            if enabled is None:
                continue
            candidate_id, authority = enabled
            candidate = {
                "candidate_id": candidate_id,
                "canonical_role": role,
                "score_version": "model_role_score_v2",
                "auto_eligible": True,
                "tier1_authority": deepcopy(dict(authority)),
            }
            try:
                persist_model_role_score_snapshot(
                    db_path,
                    selection_scope=f"parity:{role}",
                    canonical_role=role,
                    score_version="model_role_score_v2",
                    read_model_version=str(PARITY_AUDIT_VERSION),
                    candidates=[candidate],
                    winner_candidate_id=candidate_id,
                    auto_applied=True,
                )
                checked += 1
            except (ValueError, sqlite3.Error) as exc:
                _failure(
                    failures,
                    "snapshots",
                    "enabled_authority_rejected",
                    role=role,
                    lane=lane,
                    error=str(exc),
                )
            for mutation, mutated_authority in (
                ("missing", None),
                (
                    "wrong_lane",
                    {
                        **authority,
                        "lane": (
                            "quorum_ready"
                            if lane != "quorum_ready"
                            else "lead_ready"
                        ),
                    },
                ),
                (
                    "old_policy",
                    {**authority, "policy_version": "legacy_policy"},
                ),
            ):
                invalid = {**candidate}
                if mutated_authority is None:
                    invalid.pop("tier1_authority", None)
                else:
                    invalid["tier1_authority"] = mutated_authority
                try:
                    persist_model_role_score_snapshot(
                        db_path,
                        selection_scope=f"parity:{role}:{mutation}",
                        canonical_role=role,
                        score_version="model_role_score_v2",
                        read_model_version=str(PARITY_AUDIT_VERSION),
                        candidates=[invalid],
                        winner_candidate_id=candidate_id,
                        auto_applied=True,
                    )
                except ValueError:
                    checked += 1
                else:
                    _failure(
                        failures,
                        "snapshots",
                        "invalid_authority_auto_applied",
                        role=role,
                        mutation=mutation,
                    )
    return {"checked_decisions": checked}


def _audit_negative_matrix(
    read_model: Mapping[str, Any],
    *,
    profiles: list[dict[str, Any]],
    options_by_profile: dict[str, list[dict[str, Any]]],
    failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seed = _enabled_seed(read_model, options_by_profile)
    if seed is None:
        _failure(failures, "negative_matrix", "no_enabled_seed")
        return []
    candidate, role = seed
    cases: list[
        tuple[str, dict[str, Any], list[dict[str, Any]], bool]
    ] = []

    wrong_lane = deepcopy(candidate)
    wrong_lane_role = _role_row(wrong_lane, role)
    wrong_lane_role["score"]["score"] = 100
    wrong_lane_role["tier1_authority"]["lane"] = (
        "quorum_ready" if role != "quorum_auditor" else "lead_ready"
    )
    cases.append(("high_score_wrong_lane", wrong_lane, profiles, True))

    stale = deepcopy(candidate)
    stale_authority = _role_row(stale, role)["tier1_authority"]
    stale_authority.update(
        {
            "enabled": False,
            "status": "blocked",
            "reason_code": "evaluation_stale",
            "stale_reasons": ["provider_version_changed"],
        }
    )
    cases.append(("stale_evidence", stale, profiles, True))

    archived = deepcopy(candidate)
    archived["owner_preference"] = {
        "state": "archived",
        "reason": "parity_fixture",
        "source": "audit",
    }
    cases.append(("owner_archived", archived, profiles, True))

    red_candidate = deepcopy(candidate)
    red_candidate["states"]["configured"]["value"] = False
    red_candidate["states"]["adapter_green"]["value"] = False
    red_score = _role_row(red_candidate, role)["score"]
    red_score["auto_eligible"] = False
    red_score.setdefault("hard_gates", {})["adapter_green"] = {
        "passed": False,
        "reason": "adapter_health_not_green",
        "source": "parity_fixture",
    }
    red_score.setdefault("auto_ineligible_reasons", []).append(
        "adapter_health_not_green"
    )
    red_profiles = deepcopy(profiles)
    profile_id = str((candidate.get("identity") or {}).get("profile_id") or "")
    red_profile = next(
        (row for row in red_profiles if str(row.get("id") or "") == profile_id),
        None,
    )
    if red_profile is not None:
        red_profile["health"] = {"status": "error", "reason": "parity_fixture"}
        red_profile["available"] = False
    cases.append(("adapter_red", red_candidate, red_profiles, False))

    output = []
    for name, mutated, case_profiles, owner_must_be_blocked in cases:
        fixture = deepcopy(dict(read_model))
        fixture["candidates"] = [mutated]
        projection = build_contextual_model_selection(
            fixture,
            role=role,
            profiles=case_profiles,
            options_by_profile=options_by_profile,
        )
        row = next(iter(projection.get("candidates") or ()), {})
        passed = (
            bool(row)
            and (row.get("selection_score") or {}).get("auto_eligible") is False
            and (
                row.get("owner_selectable") is False
                if owner_must_be_blocked
                else row.get("requires_configuration") is True
            )
        )
        output.append({"case": name, "passed": passed})
        if not passed:
            _failure(
                failures,
                "negative_matrix",
                "negative_case_bypassed",
                case=name,
                role=role,
            )
    return output


def _audit_ui_contract(
    repo_root: Path, *, failures: list[dict[str, Any]]
) -> dict[str, Any]:
    files = {
        "catalog_types": repo_root
        / "ide-frontend"
        / "src"
        / "components"
        / "ModelCatalog"
        / "catalogTypes.ts",
        "authority_component": repo_root
        / "ide-frontend"
        / "src"
        / "components"
        / "ModelCatalog"
        / "Tier1Authority.tsx",
        "role_selector": repo_root
        / "ide-frontend"
        / "src"
        / "components"
        / "ModelRoleSelector"
        / "ModelRoleSelector.tsx",
    }
    try:
        sources = {
            name: path.read_text(encoding="utf-8") for name, path in files.items()
        }
    except OSError as exc:
        _failure(failures, "frontend", "frontend_contract_unreadable", error=str(exc))
        return {"checks": 0}
    checks = {
        "typed_authority": "tier1_authority?: Tier1Authority"
        in sources["catalog_types"],
        "authority_component_uses_backend_field": "authority.enabled"
        in sources["authority_component"],
        "authority_component_no_score_inference": all(
            token not in sources["authority_component"]
            for token in ("best_for", "model_metadata", "selection_score")
        ),
        "role_selector_uses_owner_selectable": "disabled={!candidate.owner_selectable"
        in sources["role_selector"],
        "role_selector_uses_contextual_endpoint": "/api/model-catalog/selection"
        in sources["role_selector"],
    }
    for name, passed in checks.items():
        if not passed:
            _failure(failures, "frontend", "frontend_contract_failed", check=name)
    return {"checks": len(checks), "passed": sum(checks.values())}


def _active_candidate_ids(
    read_model: Mapping[str, Any],
    options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
) -> set[str]:
    active_identities = {
        (str(profile_id), str(option.get("value") or ""))
        for profile_id, options in options_by_profile.items()
        for option in options
        if str(option.get("value") or "")
    }
    return {
        str(candidate.get("candidate_id") or "")
        for candidate in read_model.get("candidates") or ()
        if (
            str((candidate.get("identity") or {}).get("profile_id") or ""),
            str((candidate.get("identity") or {}).get("model_id") or ""),
        )
        in active_identities
    }


def _enabled_seed(
    read_model: Mapping[str, Any],
    options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[dict[str, Any], str] | None:
    active = _active_candidate_ids(read_model, options_by_profile)
    for raw_candidate in read_model.get("candidates") or ():
        candidate = deepcopy(dict(raw_candidate))
        if str(candidate.get("candidate_id") or "") not in active:
            continue
        for role in candidate.get("roles") or ():
            if (
                role.get("canonical_role") in TIER_1_ROLE_TO_LANE
                and (role.get("tier1_authority") or {}).get("enabled") is True
            ):
                return candidate, str(role["canonical_role"])
    return None


def _role_row(candidate: dict[str, Any], role: str) -> dict[str, Any]:
    return next(
        row
        for row in candidate.get("roles") or ()
        if row.get("canonical_role") == role
    )


def _failure(
    failures: list[dict[str, Any]], surface: str, code: str, **details: Any
) -> None:
    failures.append(
        {"surface": surface, "code": code, **json.loads(json.dumps(details, default=str))}
    )
