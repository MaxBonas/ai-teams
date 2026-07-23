"""Persiste shadow vivo por rol y decide conservadoramente el rollout M.7.4."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_default_rollout import evaluate_model_default  # noqa: E402


PROMOTION_ROLES = (
    "lead",
    "team_lead",
    "lead_executor",
    "architect",
    "quorum_auditor",
    "engineer",
    "reviewer",
    "qa",
    "test_designer",
    "mcp_operator",
    "worker",
    "file_scout",
    "web_scout",
    "context_curator",
)
FAIL_CLOSED_CASES = (
    "adapter_red",
    "incompatibility",
    "price_unknown",
    "quota_pressure",
    "stale",
)


def audit_live_promotion(
    db_path: Path,
    *,
    roles: tuple[str, ...] = PROMOTION_ROLES,
    projection_by_role: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persiste snapshots shadow reales; nunca crea ni modifica agentes."""
    _ensure_project_schema(db_path)
    rows: list[dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    observations: Counter[str] = Counter()
    for role in roles:
        projection = (
            projection_by_role.get(role)
            if projection_by_role is not None
            else None
        )
        decision = evaluate_model_default(
            db_path,
            selection_scope=f"m7.4:live-shadow:{role}",
            role=role,
            projection=projection,
            rollout="shadow",
            new_slot=True,
        )
        snapshot = decision["snapshot"]
        candidates = snapshot.get("candidates") or []
        for candidate in candidates:
            score = candidate.get("selection_score") or {}
            blockers.update(str(item) for item in score.get("auto_ineligible_reasons") or ())
            states = candidate.get("states") or {}
            green = (states.get("adapter_green") or {}).get("value")
            observations[f"adapter_green:{green}"] += 1
            capacity_state = str(
                (candidate.get("capacity_evidence") or {}).get("state")
                or "capacity_unknown"
            )
            observations[f"capacity:{capacity_state}"] += 1
            economy = (candidate.get("model_metadata") or {}).get("economy") or {}
            declared_known = (
                economy.get("cost_class") not in {None, "", "unknown"}
                and economy.get("input_cents_per_mtok") is not None
                and economy.get("output_cents_per_mtok") is not None
            )
            observations[f"declared_economy_known:{declared_known}"] += 1
            normalized_known = (
                ((score.get("breakdown") or {}).get("economy") or {}).get("status")
                == "known"
            )
            observations[f"normalized_economy_known:{normalized_known}"] += 1
        rows.append(
            {
                "role": role,
                "snapshot_id": snapshot.get("id"),
                "input_hash": snapshot.get("input_hash"),
                "hash_valid": snapshot.get("hash_valid") is True,
                "candidate_count": len(candidates),
                "auto_eligible_count": sum(
                    candidate.get("auto_eligible") is True
                    for candidate in candidates
                ),
                "winner_candidate_id": decision.get("winner_candidate_id"),
                "winner_reason": decision.get("winner_reason"),
                "assignment_changed": decision.get("assignment_changed") is True,
                "auto_applied": snapshot.get("auto_applied") is True,
            }
        )
    all_roles_have_winner = bool(rows) and all(
        row["winner_candidate_id"] for row in rows
    )
    gates = {
        "shadow_persisted_per_role": len(rows) == len(roles),
        "snapshot_hashes_valid": all(row["hash_valid"] for row in rows),
        "shadow_never_changed_assignment": not any(
            row["assignment_changed"] for row in rows
        ),
        "shadow_never_auto_applied": not any(row["auto_applied"] for row in rows),
        "all_roles_have_auto_winner": all_roles_have_winner,
    }
    return {
        "roles": rows,
        "blockers": [
            {"reason": reason, "occurrences": count}
            for reason, count in blockers.most_common()
        ],
        "live_observations": {
            key: value for key, value in sorted(observations.items())
        },
        "gates": gates,
        "auto_ready": all(gates.values()),
    }


def audit_fail_closed_matrix(db_path: Path) -> dict[str, Any]:
    """Ejercita la revalidación de autoridad con proyecciones adversariales."""
    rows: list[dict[str, Any]] = []
    _ensure_project_schema(db_path)
    for case in FAIL_CLOSED_CASES:
        projection = _projection(auto_eligible=False, blocker=case)
        decision = evaluate_model_default(
            db_path,
            selection_scope=f"m7.4:negative:{case}",
            role="reviewer",
            projection=projection,
            rollout="auto",
            new_slot=True,
        )
        rows.append(_negative_row(case, decision))

    tie = evaluate_model_default(
        db_path,
        selection_scope="m7.4:negative:tie",
        role="reviewer",
        projection=_tie_projection(),
        rollout="auto",
        new_slot=True,
    )
    rows.append(_negative_row("tie", tie))

    override = evaluate_model_default(
        db_path,
        selection_scope="m7.4:negative:override",
        role="reviewer",
        current_profile_id="owner-profile",
        current_model="owner-model",
        projection=_projection(auto_eligible=True),
        rollout="auto",
        new_slot=True,
    )
    rows.append(
        {
            **_negative_row("owner_override", override),
            "preserved_current": override.get("divergence")
            == "different_from_current",
        }
    )
    return {
        "database": _portable_receipt_path(db_path),
        "cases": rows,
        "all_fail_closed": all(
            row["assignment_changed"] is False
            and row["auto_applied"] is False
            and row["hash_valid"] is True
            for row in rows
        )
        and rows[-1]["preserved_current"] is True,
    }


def build_report(db_path: Path) -> dict[str, Any]:
    live = audit_live_promotion(db_path)
    enforcement = audit_fail_closed_matrix(
        db_path.parent / "model-default-enforcement.sqlite"
    )
    auto_allowed = live["auto_ready"] and enforcement["all_fail_closed"]
    return {
        "schema_version": 1,
        "benchmark": "model_default_live_promotion_audit",
        "observed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "database": _portable_receipt_path(db_path),
        "live_shadow": live,
        "fail_closed_enforcement": enforcement,
        "decision": {
            "recommended_rollout": "auto" if auto_allowed else "recommend",
            "auto_allowed": auto_allowed,
            "default_change_allowed": False,
            "reason": (
                "all_live_roles_and_negative_gates_pass"
                if auto_allowed
                else "retain_recommend_until_every_role_has_live_auto_winner"
            ),
            "rollback": "AITEAM_MODEL_DEFAULT_ROLLOUT=shadow",
        },
        "ok": (
            live["gates"]["shadow_persisted_per_role"]
            and live["gates"]["snapshot_hashes_valid"]
            and live["gates"]["shadow_never_changed_assignment"]
            and live["gates"]["shadow_never_auto_applied"]
            and enforcement["all_fail_closed"]
        ),
    }


def _projection(*, auto_eligible: bool, blocker: str = "") -> dict[str, Any]:
    candidate_id = "fixture-profile::fixture-model"
    return {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": "model_role_score_v2",
        "canonical_role": "reviewer",
        "default": {"candidate_id": candidate_id},
        "candidates": [
            {
                "candidate_id": candidate_id,
                "identity": {
                    "profile_id": "fixture-profile",
                    "model_id": "fixture-model",
                },
                "selection_reason": "adversarial_projection",
                "selection_score": {
                    "score_version": "model_role_score_v2",
                    "score": 90 if auto_eligible else None,
                    "auto_eligible": auto_eligible,
                    "auto_ineligible_reasons": (
                        [] if auto_eligible else [f"gate:{blocker}:blocked"]
                    ),
                    "hard_gates": {
                        blocker or "all": {
                            "passed": auto_eligible,
                            "reason": blocker or "all_passed",
                        }
                    },
                },
            }
        ],
    }


def _tie_projection() -> dict[str, Any]:
    projection = _projection(auto_eligible=True)
    first = projection["candidates"][0]
    second = json.loads(json.dumps(first))
    second["candidate_id"] = "fixture-profile::fixture-model-b"
    second["identity"]["model_id"] = "fixture-model-b"
    projection["candidates"].append(second)
    projection["default"] = {
        "candidate_id": None,
        "reason": "unresolved_exact_tie",
    }
    return projection


def _negative_row(case: str, decision: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = decision["snapshot"]
    return {
        "case": case,
        "winner_candidate_id": decision.get("winner_candidate_id"),
        "assignment_changed": decision.get("assignment_changed") is True,
        "auto_applied": snapshot.get("auto_applied") is True,
        "hash_valid": snapshot.get("hash_valid") is True,
    }


def _ensure_project_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema = (REPO_ROOT / "aiteam" / "db" / "schema.sql").read_text(
        encoding="utf-8"
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema)


def _portable_receipt_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
        return f"external-db:{resolved.name}:{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "runtime" / "aiteam.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / "benchmarks"
        / "results"
        / "model_calibration"
        / "model-default-live-promotion-2026-07-23.json",
    )
    args = parser.parse_args()
    report = build_report(args.db.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["decision"], ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
