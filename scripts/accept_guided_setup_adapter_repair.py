from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.db.guided_setup import (
    create_or_resume_setup,
    record_setup_preparation,
    transition_setup_step,
)
from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_provider_evidence import (
    build_canonical_provider_evidence,
)

SCHEMA_VERSION = "guided_setup_adapter_repair_acceptance_v1"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _needs(
    *,
    subscriptions: list[str] | None = None,
    api_access: str = "not_willing",
    local_models: str = "not_wanted",
) -> dict[str, Any]:
    return build_needs_submission(
        "machine_onboarding",
        {
            "goal": "Preparar AI Teams para un proyecto",
            "objective_kind": "software",
            "languages": ["TypeScript"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": subscriptions or ["codex"],
            "api_access": api_access,
            "local_models": local_models,
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": "solo_lead",
            "external_tools": "optional",
        },
    )


def _inventory(
    *,
    runtime_ready: bool = True,
    adapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [
            {
                "id": "python",
                "requirement": "required",
                "ready": runtime_ready,
                "installed": runtime_ready,
                "version": "3.12.10" if runtime_ready else None,
                "minimum_version": "3.10",
            }
        ],
        "adapters": adapters or [],
    }


def _observation(
    profile_id: str,
    *,
    version: str | None,
    auth: str = "authenticated",
    health: str = "ok",
) -> dict[str, Any]:
    return {
        "id": profile_id,
        "cli": (
            {"installed": True, "version": version}
            if version
            else None
        ),
        "authentication_status": auth,
        "health_status": health,
    }


def _profile(
    profile_id: str,
    *,
    channel: str = "subscription",
    health: str = "ok",
    catalog: str = "current",
    probe_version: str = "0.146.0-alpha.6",
    api_version: str | None = None,
) -> dict[str, Any]:
    return {
        "id": profile_id,
        "channel": channel,
        "config": {"api_version": api_version} if api_version else {},
        "structured_output": "json_schema",
        "health": {
            "status": health,
            "checked_at": NOW.isoformat(),
        },
        "model_catalog": {
            "status": catalog,
            "source": "fixture",
            "count": 1 if catalog == "current" else 0,
            "checked_at": NOW.isoformat(),
        },
        "model_options": [
            {
                "value": "fixture-lead-model",
                "best_for": ["lead"],
                "structured_output": "json_schema",
                "probe_status": "completed",
                "probe_version": probe_version,
                "probe_evaluated_at": NOW.isoformat(),
                "probe_receipts": ["receipts/fixture-lead.json"],
            }
        ],
    }


def _canonical_plan(
    needs: dict[str, Any],
    inventory: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    selected_api_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    initial = build_preparation_plan(
        needs,
        inventory,
        selected_api_profiles=selected_api_profiles,
    )
    projection = build_canonical_provider_evidence(
        initial,
        inventory,
        profiles,
        observed_at=NOW,
    )
    return build_preparation_plan(
        needs,
        inventory,
        provider_evidence=projection["stage_evidence"],
        selected_api_profiles=selected_api_profiles,
    )


def build_acceptance(repo_root: Path) -> dict[str, Any]:
    scenarios: dict[str, dict[str, Any]] = {}

    clean = build_preparation_plan(
        _needs(),
        _inventory(runtime_ready=False),
    )
    scenarios["clean_machine"] = _scenario(
        clean["summary"]["ready"] is False
        and "runtime:python" in clean["summary"]["blockers"],
        state="blocked",
        action="install_runtime_and_choose_lead_channel",
    )

    partial_inventory = _inventory(
        adapters=[
            _observation(
                "codex_subscription",
                version="codex-cli 0.146.0-alpha.6",
                auth="not_checked",
                health="installed",
            )
        ]
    )
    partial = build_preparation_plan(_needs(), partial_inventory)
    scenarios["partial_installation"] = _scenario(
        partial["adapters"][0]["stages"]["installation"] == "passed"
        and partial["adapters"][0]["stages"]["authentication"] == "not_checked"
        and partial["summary"]["ready"] is False,
        state="unverified",
        action="authenticate_and_verify",
    )

    outdated = build_preparation_plan(
        _needs(),
        _inventory(
            adapters=[
                _observation(
                    "codex_subscription",
                    version="codex-cli 0.120.0",
                )
            ]
        ),
    )
    scenarios["outdated_cli"] = _scenario(
        outdated["adapters"][0]["stages"]["version"] == "failed",
        state="blocked",
        action="update_cli",
    )

    auth_inventory = _inventory(
        adapters=[
            _observation(
                "codex_subscription",
                version="codex-cli 0.146.0-alpha.6",
                auth="not_authenticated",
                health="failed",
            )
        ]
    )
    auth_plan = _canonical_plan(
        _needs(),
        auth_inventory,
        [_profile("codex_subscription", health="failed")],
    )
    scenarios["authentication_missing"] = _scenario(
        auth_plan["adapters"][0]["stages"]["authentication"] == "failed",
        state="blocked",
        action="human_login",
    )

    catalog_inventory = _inventory(
        adapters=[
            _observation(
                "codex_subscription",
                version="codex-cli 0.146.0-alpha.6",
            )
        ]
    )
    catalog_plan = _canonical_plan(
        _needs(),
        catalog_inventory,
        [_profile("codex_subscription", catalog="unavailable")],
    )
    scenarios["catalog_incompatible"] = _scenario(
        catalog_plan["adapters"][0]["stages"]["catalog"] == "not_checked"
        and catalog_plan["summary"]["ready"] is False,
        state="unverified",
        action="refresh_catalog",
    )

    api_profile = _profile(
        "openai_api",
        channel="api",
        probe_version="v1",
        api_version="v1",
    )
    api_inventory = _inventory(
        adapters=[
            _observation(
                "openai_api",
                version=None,
                auth="authenticated",
                health="ok",
            )
        ]
    )
    api_plan = _canonical_plan(
        _needs(subscriptions=["none"], api_access="existing"),
        api_inventory,
        [api_profile],
        selected_api_profiles=[api_profile],
    )
    scenarios["valid_personal_api"] = _scenario(
        api_plan["summary"]["ready"] is True
        and api_plan["lead_channel"]["ready_adapter_ids"] == ["openai_api"],
        state="ready",
        action="continue",
    )

    offline_profile = _profile(
        "openai_api",
        channel="api",
        health="failed",
        catalog="rate_limited",
        probe_version="v1",
        api_version="v1",
    )
    offline_plan = _canonical_plan(
        _needs(subscriptions=["none"], api_access="existing"),
        api_inventory,
        [offline_profile],
        selected_api_profiles=[offline_profile],
    )
    scenarios["offline_or_rate_limited"] = _scenario(
        offline_plan["adapters"][0]["stages"]["health"] == "failed"
        and offline_plan["adapters"][0]["stages"]["catalog"] == "not_checked",
        state="blocked",
        action="retry_without_loop",
    )

    without_local = build_preparation_plan(_needs(), _inventory())
    with_local = build_preparation_plan(
        _needs(local_models="willing"),
        _inventory(),
    )
    scenarios["local_opt_in"] = _scenario(
        without_local["summary"]["optional_local_present"] is False
        and with_local["summary"]["optional_local_present"] is True
        and all(
            row["requirement"] == "optional"
            for row in with_local["adapters"]
            if row["id"] in {"ollama", "lmstudio"}
        ),
        state="optional",
        action="owner_choice",
    )

    with tempfile.TemporaryDirectory(prefix="aiteam-repair-resume-") as raw:
        db = Path(raw) / "guided_setup.db"
        session = create_or_resume_setup(
            db,
            scope="machine_onboarding",
            subject_key="acceptance-machine",
        )
        for key, response in (
            ("welcome", {}),
            ("projects_root", {"path": "C:/fixture"}),
            ("needs_profile", _needs()),
        ):
            session = transition_setup_step(
                db,
                session["id"],
                key,
                status="in_progress",
                expected_revision=session["revision"],
            )
            session = transition_setup_step(
                db,
                session["id"],
                key,
                status="passed",
                expected_revision=session["revision"],
                response=response,
            )
        persisted = record_setup_preparation(
            db,
            session["id"],
            expected_revision=session["revision"],
            plan=partial,
            inventory=partial_inventory,
        )
        resumed = create_or_resume_setup(
            db,
            scope="machine_onboarding",
            subject_key="acceptance-machine",
        )
    scenarios["durable_resume"] = _scenario(
        resumed["id"] == session["id"]
        and resumed["revision"] == persisted["session"]["revision"]
        and next(
            row for row in resumed["steps"] if row["key"] == "adapter_setup"
        )["evidence"]["plan_hash"]
        == persisted["receipt"]["plan_hash"],
        state="resumed",
        action="continue_without_reinstall",
    )

    router = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    scenarios["client_boundary"] = _scenario(
        "selected_api_profile_ids" in router
        and "provider_evidence:" not in router.split(
            "class PreparationRunRequest", 1
        )[1].split("@router", 1)[0],
        state="server_authoritative",
        action="ids_only",
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "fixture_only": True,
            "commands_executed": False,
            "global_installations_mutated": False,
            "user_configuration_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
            "terms_accepted": False,
        },
        "scenarios": scenarios,
        "summary": {
            "repair_acceptance_ready": all(
                row["passed"] for row in scenarios.values()
            ),
            "scenario_count": len(scenarios),
            "passed_count": sum(
                row["passed"] is True for row in scenarios.values()
            ),
        },
    }
    validate_acceptance(report)
    return report


def _scenario(passed: bool, *, state: str, action: str) -> dict[str, Any]:
    return {"passed": bool(passed), "state": state, "next_action": action}


def validate_acceptance(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("guided setup repair acceptance schema drift")
    scenarios = report.get("scenarios")
    if not isinstance(scenarios, dict) or len(scenarios) != 10:
        raise ValueError("guided setup repair acceptance coverage drift")
    expected = {
        "repair_acceptance_ready": all(
            row["passed"] for row in scenarios.values()
        ),
        "scenario_count": len(scenarios),
        "passed_count": sum(
            row["passed"] is True for row in scenarios.values()
        ),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup repair acceptance summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_acceptance(Path(__file__).resolve().parents[1])
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["repair_acceptance_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
