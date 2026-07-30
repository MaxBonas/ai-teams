"""Auditoría hermética del workflow owner-gated P0.N.4."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.db.provider_change_workflows import (  # noqa: E402
    ProviderChangeConflictError,
    evidence_is_invalidated,
    list_active_provider_change_invalidations,
    reconcile_provider_change_cases,
    transition_provider_change_case,
)
from aiteam.db.provider_changes import (  # noqa: E402
    list_pending_provider_triggers,
    reconcile_provider_snapshot,
)
from aiteam.provider_change_detection import (  # noqa: E402
    build_provider_snapshot,
)
from aiteam.provider_change_intelligence import (  # noqa: E402
    build_provider_change_inventory,
)

NOW = datetime(2026, 7, 30, 12, tzinfo=timezone.utc)


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="aiteam-provider-workflow-audit-"
    ) as raw_dir:
        root = Path(raw_dir)
        accepted_db = root / "accepted.db"
        case = _seed_case(accepted_db)
        materialized = {
            "has_diff": bool(case["diff"]),
            "has_impact": bool(case["impact"]),
            "has_recommendation": bool(case["recommendation"]),
            "has_commands": bool(case["guided_commands"]),
            "has_risk": bool(case["risk"]),
            "has_rollback": bool(case["rollback"]),
        }
        conflict_closed = False
        case = _transition(accepted_db, case, "confirm")
        try:
            transition_provider_change_case(
                accepted_db,
                case["id"],
                action="classify",
                expected_revision=1,
                actor="owner",
                payload=_classification(),
            )
        except ProviderChangeConflictError:
            conflict_closed = True
        application_before_approval_closed = False
        try:
            transition_provider_change_case(
                accepted_db,
                case["id"],
                action="record_application",
                expected_revision=case["revision"],
                actor="owner",
                payload={
                    "kind": "catalog_updated",
                    "summary": "No debe aceptarse todavía.",
                },
            )
        except ValueError:
            application_before_approval_closed = True
        case = _transition(
            accepted_db,
            case,
            "classify",
            payload=_classification(),
        )
        case = _transition(
            accepted_db,
            case,
            "approve",
            payload={"note": "Owner approval"},
        )
        active = list_active_provider_change_invalidations(accepted_db)
        exact_scope = bool(
            evidence_is_invalidated(
                active,
                profile_id="codex_subscription",
                model_id="gpt-5.6-sol",
                role="lead",
            )
        ) and not evidence_is_invalidated(
            active,
            profile_id="codex_subscription",
            model_id="gpt-5.6-sol",
            role="reviewer",
        )
        case = _transition(
            accepted_db,
            case,
            "record_application",
            payload={
                "kind": "catalog_updated",
                "summary": "Actualización manual registrada.",
                "evidence_receipts": ["receipts/application.json"],
            },
        )
        workflow_never_executes = (
            case["application"]["executed_by_workflow"] is False
            and all(
                item["execution"] in {"manual_only", "guided_in_product"}
                for item in case["guided_commands"]
            )
        )
        case = _transition(
            accepted_db,
            case,
            "record_validation",
            payload={
                "result": "passed",
                "doctor": "passed",
                "probe": "passed",
                "summary": "Doctor y probe verdes.",
                "evidence_receipts": ["receipts/validation.json"],
            },
        )
        case = _transition(
            accepted_db,
            case,
            "record_recalibration",
            payload={
                "result": "passed",
                "summary": "Calibración proporcional válida.",
                "evidence_receipts": ["receipts/calibration.json"],
            },
        )
        case = _transition(accepted_db, case, "accept")
        acceptance_closed = (
            case["status"] == "accepted"
            and not list_active_provider_change_invalidations(accepted_db)
            and not list_pending_provider_triggers(accepted_db)
        )

        reverted_db = root / "reverted.db"
        reverted = _seed_case(reverted_db)
        reverted = _transition(reverted_db, reverted, "confirm")
        reverted = _transition(
            reverted_db,
            reverted,
            "classify",
            payload=_classification(),
        )
        reverted = _transition(
            reverted_db,
            reverted,
            "approve",
        )
        reverted = _transition(
            reverted_db,
            reverted,
            "revert",
            payload={
                "reason": "Rollback manual verificado.",
                "evidence_receipts": ["receipts/rollback.json"],
            },
        )
        rollback_closed = (
            reverted["status"] == "reverted"
            and not list_active_provider_change_invalidations(reverted_db)
            and len(list_pending_provider_triggers(reverted_db)) == 1
        )

    checks = {
        "case_materializes_complete_evidence": all(materialized.values()),
        "optimistic_revision_fails_closed": conflict_closed,
        "approval_precedes_application": application_before_approval_closed,
        "invalidation_scope_is_exact": exact_scope,
        "workflow_never_executes_commands": workflow_never_executes,
        "validation_and_recalibration_are_separate": (
            any(row["action"] == "record_validation" for row in case["history"])
            and any(
                row["action"] == "record_recalibration"
                for row in case["history"]
            )
        ),
        "acceptance_consumes_and_restores": acceptance_closed,
        "rollback_restores_and_reopens_trigger": rollback_closed,
        "history_is_monotonic": (
            [row["sequence"] for row in case["history"]]
            == list(range(1, len(case["history"]) + 1))
        ),
    }
    return {
        "schema_version": "provider_change_workflow_audit_v1",
        "contract_schema_version": "provider_change_workflow_v1",
        "counts": {
            "checks": len(checks),
            "accepted_history_events": len(case["history"]),
            "materialized_fields": len(materialized),
        },
        "checks": checks,
        "summary": {
            "passed": sum(checks.values()),
            "total": len(checks),
            "workflow_ready": all(checks.values()),
        },
        "scope": {
            "temporary_sqlite_only": True,
            "network_attempted": False,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "commands_executed": False,
            "updates_executed": False,
            "routing_mutated": False,
        },
    }


def _seed_case(db_path: Path) -> dict[str, Any]:
    component = next(
        row
        for row in build_provider_change_inventory()["components"]
        if row["surface"] == "model_catalog"
        and row["profile_id"] == "codex_subscription"
    )
    reconcile_provider_snapshot(db_path, _snapshot(component, 1000, NOW))
    reconcile_provider_snapshot(
        db_path,
        _snapshot(component, 2000, NOW + timedelta(hours=1)),
    )
    return reconcile_provider_change_cases(
        db_path,
        now=NOW + timedelta(hours=2),
    )[0]


def _snapshot(
    component: dict[str, Any],
    context: int,
    observed_at: datetime,
) -> dict[str, Any]:
    return build_provider_snapshot(
        component,
        {
            "status": "observed",
            "installed_version": "0.146.0-alpha.6",
            "latest_known_version": "0.146.0-alpha.6",
            "dimensions": {
                "model_id": [
                    {
                        "id": "gpt-5.6-sol",
                        "aliases": [],
                        "context": context,
                        "tools": True,
                        "structured_output": True,
                        "price": "subscription",
                        "quota": "subscription",
                        "lifecycle": "active",
                    }
                ]
            },
        },
        observed_at=observed_at.isoformat(),
    )


def _classification() -> dict[str, Any]:
    return {
        "impact_level": "material",
        "requires_recalibration": True,
        "rationale": "El contexto cambió y afecta la evidencia Lead exacta.",
        "impact": {
            "profile_ids": ["codex_subscription"],
            "model_ids": ["gpt-5.6-sol"],
            "roles": ["lead"],
            "all_models": False,
            "all_roles": False,
            "new_selection_policy": "block_affected",
            "existing_assignment_policy": "preserve_and_notify",
        },
    }


def _transition(
    db_path: Path,
    case: dict[str, Any],
    action: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return transition_provider_change_case(
        db_path,
        case["id"],
        action=action,
        expected_revision=int(case["revision"]),
        actor="owner",
        payload=payload,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita el workflow reversible provider-change."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_report()
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    ready = report["summary"]["workflow_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
