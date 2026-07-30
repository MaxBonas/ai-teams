from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_preparation import build_preparation_plan
from aiteam.guided_setup_provider_guidance import (
    SCHEMA_VERSION,
    build_provider_guidance,
)

AUDIT_VERSION = "guided_setup_provider_guidance_audit_v1"


def _needs(*, local: str = "not_wanted") -> dict[str, Any]:
    return build_needs_submission(
        "machine_onboarding",
        {
            "goal": "Crear una aplicación React",
            "objective_kind": "software",
            "languages": ["React"],
            "data_sensitivity": "internal",
            "budget_priority": "prefer_free",
            "subscriptions": ["codex", "antigravity"],
            "api_access": "willing",
            "local_models": local,
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": "solo_lead",
            "external_tools": "optional",
        },
    )


def _inventory() -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "runtimes": [],
        "adapters": [],
    }


def build_audit(repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    guidance = build_provider_guidance(
        build_preparation_plan(_needs(), _inventory())
    )
    providers = {
        row["adapter_id"]: row for row in guidance["providers"]
    }
    actions = [
        action
        for provider in providers.values()
        for action in provider["actions"]
    ]
    checks["requested_channels_only"] = set(providers) == {
        "codex_subscription",
        "antigravity_subscription",
        "opencode_zen_free",
        "personal_api",
    }
    checks["all_actions_are_manual"] = bool(actions) and all(
        action["execution"] == "manual_only" and action["automatic"] is False
        for action in actions
    )
    checks["all_actions_require_confirmation"] = all(
        action["confirmation_required"] is True for action in actions
    )
    checks["action_never_grants_ready"] = all(
        action["completion_grants_ready"] is False for action in actions
    )
    checks["cli_versions_are_explicit"] = all(
        providers[adapter_id]["minimum_version"]
        and providers[adapter_id]["validated_version"]
        for adapter_id in (
            "codex_subscription",
            "antigravity_subscription",
            "opencode_zen_free",
        )
    )
    checks["remote_install_risk_is_visible"] = any(
        action["risk"] == "remote_script_execution"
        for adapter_id in ("codex_subscription", "antigravity_subscription")
        for action in providers[adapter_id]["actions"]
    )
    checks["opencode_free_auth_and_data_policy_are_visible"] = (
        any(
            "API key personal" in note
            for note in providers["opencode_zen_free"]["notes"]
        )
        and any(
            "non_confidential_only" in note
            for note in providers["opencode_zen_free"]["notes"]
        )
    )
    personal_serialized = json.dumps(
        providers["personal_api"],
        ensure_ascii=False,
    )
    checks["personal_api_keeps_secret_reference_only"] = (
        "/api/user-adapters/secrets" in personal_serialized
        and "secret_ref_only" in personal_serialized
        and '"api_key"' not in personal_serialized
        and all(
            action["copyable_command"] is None
            for action in providers["personal_api"]["actions"]
        )
    )
    local_guidance = build_provider_guidance(
        build_preparation_plan(
            _needs(local="willing"),
            _inventory(),
        )
    )
    local_ids = {
        row["adapter_id"] for row in local_guidance["providers"]
    }
    checks["local_guidance_requires_opt_in"] = (
        not {"ollama", "lmstudio"} & set(providers)
        and {"ollama", "lmstudio"} <= local_ids
    )
    router = (
        repo_root / "api" / "routers" / "guided_setup.py"
    ).read_text(encoding="utf-8")
    checks["authenticated_api_returns_guidance"] = all(
        marker in router
        for marker in (
            "build_provider_guidance(plan)",
            '"guidance": guidance',
            "_require_api_auth_request(request)",
        )
    )
    report = {
        "schema_version": AUDIT_VERSION,
        "contract_version": SCHEMA_VERSION,
        "scope": {
            "fixture_only": True,
            "commands_executed": False,
            "global_installations_mutated": False,
            "user_configuration_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "terms_accepted": False,
        },
        "checks": checks,
        "summary": {
            "provider_guidance_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup provider guidance schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup provider guidance coverage drift")
    expected = {
        "provider_guidance_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup provider guidance summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(Path(__file__).resolve().parents[1])
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["provider_guidance_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
