"""Audita la evidencia visual durable del asistente de proyecto."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_VERSION = "guided_setup_project_visual_acceptance_audit_v1"
MANIFEST_VERSION = "guided_setup_project_visual_evidence_v1"
EXPECTED_CHECKS = frozenset({
    "visual_state_matrix_complete",
    "screenshots_are_content_hashed",
    "authority_hashes_are_bound_per_state",
    "pending_cannot_enter",
    "durable_no_go_cannot_enter",
    "durable_no_go_routes_to_resources",
    "durable_go_requires_complete_hash_chain",
    "post_execution_preflight_is_projected",
    "tampered_go_fails_closed",
    "accessibility_is_rechecked_after_execution",
})
EXPECTED_STATES = {
    "pending-desktop": ("preflight-desktop.png", 1280, 720),
    "pending-tablet": ("preflight-tablet.png", 768, 1024),
    "pending-mobile": ("preflight-mobile.png", 390, 844),
    "pending-reflow-320": ("preflight-reflow-320.png", 320, 800),
    "durable-no-go": ("preflight-no-go.png", 1280, 900),
    "durable-go": ("preflight-go.png", 1280, 900),
}
SOURCE_PATHS = (
    "ide-frontend/e2e/project-preflight.spec.ts",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectPreflightPanel.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectPreflightPanel.test.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/ProjectSetupWizard.tsx",
    "ide-frontend/src/components/ProjectSetupWizard/projectPreflightApi.ts",
    "scripts/audit_guided_setup_project_visual_acceptance.py",
    "tests/test_audit_guided_setup_project_visual_acceptance.py",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_visual_manifest(
    manifest: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    if manifest.get("schema_version") != MANIFEST_VERSION:
        raise ValueError("visual evidence schema drift")
    screenshots = manifest.get("screenshots")
    if not isinstance(screenshots, list) or len(screenshots) != len(EXPECTED_STATES):
        raise ValueError("visual evidence matrix drift")
    sealed = {
        "schema_version": manifest["schema_version"],
        "screenshots": screenshots,
    }
    if manifest.get("evidence_sha256") != _hash(sealed):
        raise ValueError("visual evidence manifest hash drift")

    states: set[str] = set()
    files: set[str] = set()
    root = artifact_root.resolve()
    for row in screenshots:
        if not isinstance(row, dict):
            raise TypeError("visual evidence row drift")
        state = row.get("state")
        if state not in EXPECTED_STATES or state in states:
            raise ValueError("visual evidence state drift")
        states.add(state)
        expected_file, expected_width, expected_height = EXPECTED_STATES[state]
        file_name = row.get("file")
        if (
            file_name != expected_file
            or file_name in files
            or Path(file_name).name != file_name
        ):
            raise ValueError("visual evidence file drift")
        files.add(file_name)
        viewport = row.get("viewport")
        if viewport != {"width": expected_width, "height": expected_height}:
            raise ValueError("visual evidence viewport drift")
        screenshot_path = (root / file_name).resolve()
        if screenshot_path.parent != root or not screenshot_path.is_file():
            raise ValueError("visual evidence artifact missing")
        if (
            not SHA256_RE.fullmatch(str(row.get("sha256", "")))
            or row["sha256"] != _file_hash(screenshot_path)
        ):
            raise ValueError("visual evidence screenshot hash drift")

        authority = row.get("authority_hashes")
        if not isinstance(authority, dict):
            raise TypeError("visual evidence authority drift")
        for key in ("proposal_hash", "preflight_hash", "execution_plan_hash"):
            if not SHA256_RE.fullmatch(str(authority.get(key, ""))):
                raise ValueError("visual evidence authority hash drift")
        durable = state.startswith("durable-")
        for key in ("execution_receipt_hash", "durable_receipt_hash"):
            value = authority.get(key)
            if durable:
                if not SHA256_RE.fullmatch(str(value or "")):
                    raise ValueError("visual evidence durable hash drift")
            elif value is not None:
                raise ValueError("pending visual evidence claims durable authority")

    if states != set(EXPECTED_STATES):
        raise ValueError("visual evidence state matrix drift")
    return {
        "schema_version": MANIFEST_VERSION,
        "evidence_sha256": manifest["evidence_sha256"],
        "states": sorted(states),
        "screenshots": [
            {
                "file": row["file"],
                "state": row["state"],
                "sha256": row["sha256"],
                "authority_hashes": row["authority_hashes"],
            }
            for row in screenshots
        ],
    }


def build_audit(repo_root: Path, visual_manifest: Path) -> dict[str, Any]:
    sources = {
        relative: (repo_root / relative).read_text(encoding="utf-8")
        for relative in SOURCE_PATHS
    }
    e2e = sources[SOURCE_PATHS[0]]
    panel = sources[SOURCE_PATHS[1]]
    panel_tests = sources[SOURCE_PATHS[2]]
    wizard = sources[SOURCE_PATHS[3]]
    preflight_api = sources[SOURCE_PATHS[4]]
    manifest = json.loads(visual_manifest.read_text(encoding="utf-8"))
    visual_evidence = validate_visual_manifest(manifest, visual_manifest.parent)

    checks = {
        "visual_state_matrix_complete": all(
            state in e2e for state in EXPECTED_STATES
        ),
        "screenshots_are_content_hashed": all(
            marker in e2e
            for marker in (
                "captureEvidence",
                "sha256(await readFile(path))",
                "guided_setup_project_visual_evidence_v1",
            )
        ),
        "authority_hashes_are_bound_per_state": all(
            marker in e2e
            for marker in (
                "proposal_hash",
                "preflight_hash",
                "execution_plan_hash",
                "execution_receipt_hash",
                "durable_receipt_hash",
            )
        ),
        "pending_cannot_enter": (
            "Entrar al proyecto" in e2e
            and "toHaveCount(0)" in e2e
        ),
        "durable_no_go_cannot_enter": all(
            marker in e2e
            for marker in (
                "NO-GO · intento sellado",
                "durable-no-go",
                "name: 'Entrar al proyecto'",
            )
        ),
        "durable_no_go_routes_to_resources": all(
            marker in e2e
            for marker in (
                "name: 'Revisar recursos'",
                "Pendiente de receipt",
                "executionCount).toBe(2)",
            )
        ),
        "durable_go_requires_complete_hash_chain": all(
            marker in preflight_api
            for marker in (
                "durable.preflight_hash === execution.post_execution_preflight.preflight_hash",
                "durable.execution_plan_hash === preview.execution_plan.plan_hash",
                "durable.execution_receipt_hash === execution.receipt.receipt_hash",
            )
        ) and "preflightExecutionAuthorizesCommit(" in wizard,
        "post_execution_preflight_is_projected": (
            "execution?.post_execution_preflight ?? preview.preflight" in panel
        ),
        "tampered_go_fails_closed": all(
            marker in panel_tests
            for marker in (
                "fails closed when any durable receipt hash link is inconsistent",
                "Receipt inconsistente",
                "La cadena de hashes del receipt no coincide.",
            )
        ),
        "accessibility_is_rechecked_after_execution": all(
            marker in e2e
            for marker in (
                "Receipt durable NO-GO",
                "Receipt durable GO",
                "expectNoAccessibilityViolations",
            )
        ),
    }
    evidence = {
        "source_hashes": {
            relative: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for relative, content in sources.items()
        },
        "visual": visual_evidence,
        "authority_boundary": (
            "React solo habilita entrada cuando plan, execution receipt y "
            "preflight posterior coinciden con el receipt durable GO."
        ),
    }
    summary = {
        "ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
        "visual_state_count": len(visual_evidence["states"]),
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
        "summary": summary,
    }
    report["report_hash"] = _hash(report)
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("visual acceptance audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("visual acceptance audit matrix drift")
    evidence = report.get("evidence")
    if (
        not isinstance(evidence, dict)
        or report.get("evidence_hash") != _hash(evidence)
    ):
        raise ValueError("visual acceptance evidence drift")
    expected_summary = {
        "ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
        "visual_state_count": len(evidence["visual"]["states"]),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("visual acceptance summary drift")
    sealed = {key: value for key, value in report.items() if key != "report_hash"}
    if report.get("report_hash") != _hash(sealed):
        raise ValueError("visual acceptance report hash drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visual-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(REPO_ROOT, args.visual_manifest)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
