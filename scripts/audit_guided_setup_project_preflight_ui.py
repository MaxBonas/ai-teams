"""Audita el contrato UI del preflight durable de configuración de proyecto."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_VERSION = "guided_setup_project_preflight_ui_audit_v1"
EXPECTED_CHECKS = frozenset({
    "proposal_loads_server_preflight",
    "execution_uses_server_plan",
    "commit_requires_durable_go",
    "local_fixture_requires_consent",
    "remote_probe_and_quota_are_separate",
    "research_mode_declares_zero_software_runs",
    "durable_no_go_routes_to_resources",
    "stale_preflight_invalidates_preview",
    "http_409_and_429_remain_distinguishable",
    "accessibility_and_responsive_contract_present",
})
SOURCE_PATHS = (
    "ide-frontend/src/components/ProjectSetupWizard/ProjectSetupWizard.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectPreflightPanel.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/projectPreflightApi.ts",
    "ide-frontend/src/components/ProjectSetupWizard/projectSetupApi.ts",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectPreflightPanel.css",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectPreflightPanel.test.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectSetupWizard.test.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/projectSetupApi.test.ts",
    "ide-frontend/e2e/project-preflight.spec.ts",
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _contains(source: str, *needles: str) -> bool:
    return all(needle in source for needle in needles)


def build_audit(repo_root: Path) -> dict[str, Any]:
    sources = {
        relative: (repo_root / relative).read_text(encoding="utf-8")
        for relative in SOURCE_PATHS
    }
    wizard = sources[SOURCE_PATHS[0]]
    panel = sources[SOURCE_PATHS[1]]
    preflight_api = sources[SOURCE_PATHS[2]]
    setup_api = sources[SOURCE_PATHS[3]]
    css = sources[SOURCE_PATHS[4]]
    panel_tests = sources[SOURCE_PATHS[5]]
    wizard_tests = sources[SOURCE_PATHS[6]]
    api_tests = sources[SOURCE_PATHS[7]]
    e2e_test = sources[SOURCE_PATHS[8]]

    checks = {
        "proposal_loads_server_preflight": _contains(
            wizard,
            "loadProjectPreflight(",
            "built.proposalResponse.proposal.proposal_hash",
            "setPreflightResponse(preflight)",
        ),
        "execution_uses_server_plan": _contains(
            preflight_api,
            "/project-preflight-execute",
            "execution_plan_hash: preview.execution_plan.plan_hash",
            "preflight_hash: preview.preflight.preflight_hash",
        ),
        "commit_requires_durable_go": _contains(
            wizard,
            "preflightExecutionAuthorizesCommit(preflightResponse, preflightExecution)",
        ) and _contains(
            panel,
            "preflightExecutionAuthorizesCommit(preview, execution)",
            "Entrar al proyecto",
        ) and _contains(
            preflight_api,
            "durable.preflight_hash === execution.post_execution_preflight.preflight_hash",
            "durable.execution_plan_hash === preview.execution_plan.plan_hash",
            "durable.execution_receipt_hash === execution.receipt.receipt_hash",
        ),
        "local_fixture_requires_consent": _contains(
            preflight_api,
            "confirm_local_fixture",
        ) and _contains(
            panel,
            "consent.localFixture",
            "Autorizo el fixture local",
        ),
        "remote_probe_and_quota_are_separate": _contains(
            preflight_api,
            "confirm_remote_probe",
            "acknowledge_possible_quota",
        ) and _contains(
            panel,
            "consent.remoteProbe",
            "consent.quota",
        ) and _contains(
            panel_tests,
            "requires both remote and quota consent",
        ),
        "research_mode_declares_zero_software_runs": _contains(
            panel,
            "Sin pruebas de software",
            "No ejecutará tests ni llamadas remotas",
        ) and _contains(
            panel_tests,
            "seals research without presenting software tests",
        ),
        "durable_no_go_routes_to_resources": _contains(
            panel,
            "Revisar recursos",
            "onReviewResources",
        ) and _contains(
            panel_tests,
            "keeps a durable no-go sealed",
        ),
        "stale_preflight_invalidates_preview": wizard.count(
            "reason.status === 409"
        ) >= 2 and _contains(
            wizard,
            "invalidateProposal();",
            "El preflight quedó obsoleto",
            "La autorización ya no coincide con la máquina",
        ),
        "http_409_and_429_remain_distinguishable": _contains(
            setup_api,
            "readonly status: number",
            "response.status",
        ) and _contains(
            api_tests,
            "[409,",
            "[429,",
            "preserves status %i",
            "offline transport failure",
        ),
        "accessibility_and_responsive_contract_present": _contains(
            panel,
            'aria-live="polite"',
            'role="alert"',
        ) and _contains(
            css,
            "@media (max-width: 820px)",
            "grid-template-columns: 1fr;",
        ) and _contains(
            wizard_tests,
            "Entrar al proyecto",
        ) and _contains(
            e2e_test,
            "document.querySelectorAll<HTMLElement>('body *')",
            "browserErrors",
        ),
    }
    evidence = {
        "source_hashes": {
            relative: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative, content in sources.items()
        },
        "behavioral_test_files": [
            SOURCE_PATHS[5],
            SOURCE_PATHS[6],
            SOURCE_PATHS[7],
            SOURCE_PATHS[8],
        ],
        "authority_boundary": (
            "La UI proyecta preflight, plan y receipt del servidor; "
            "no puntúa ni autoriza modelos."
        ),
    }
    report = {
        "schema_version": AUDIT_VERSION,
        "scope": {
            "source_files_mutated": False,
            "user_projects_mutated": False,
            "remote_calls": False,
            "inference_attempted": False,
            "quota_consumed": False,
        },
        "checks": checks,
        "evidence": evidence,
        "evidence_hash": _hash(evidence),
        "summary": {
            "ui_contract_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup preflight UI audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("guided setup preflight UI audit matrix drift")
    evidence = report.get("evidence")
    if (
        not isinstance(evidence, dict)
        or report.get("evidence_hash") != _hash(evidence)
    ):
        raise ValueError("guided setup preflight UI audit evidence drift")
    expected_summary = {
        "ui_contract_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("guided setup preflight UI audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(REPO_ROOT)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    ready = report["summary"]["ui_contract_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
