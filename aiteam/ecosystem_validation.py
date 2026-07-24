from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.ecosystem_registry import (
    ACTION_IDS,
    detect_project_ecosystems,
    plan_ecosystem_command,
)
from aiteam.platform_runtime import architecture_id, platform_id, run_command

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCHEMA_VERSION = "ecosystem_fixture_v1"
RECEIPT_SCHEMA_VERSION = "ecosystem_validation_receipt_v1"
CAPABILITY_GAP_SCHEMA_VERSION = "capability_gap_v1"
DEFAULT_FIXTURES_ROOT = ROOT / "tests" / "fixtures" / "ecosystems"


def load_ecosystem_fixtures(root: Path | None = None) -> tuple[dict[str, Any], ...]:
    fixture_root = Path(root or DEFAULT_FIXTURES_ROOT)
    fixtures: list[dict[str, Any]] = []
    if not fixture_root.is_dir():
        return ()
    for manifest_path in sorted(fixture_root.glob("*/fixture.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_fixture(payload)
        fixtures.append(
            {
                **payload,
                "_source_dir": manifest_path.parent,
            }
        )
    fixture_ids = [str(item["fixture_id"]) for item in fixtures]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise ValueError("ecosystem fixture ids must be unique")
    return tuple(fixtures)


def validate_ecosystem_fixtures(
    *,
    fixtures_root: Path | None = None,
    selected_case_ids: Iterable[str] = (),
    execute: bool = True,
    runtime_overrides: Mapping[str, Mapping[str, str]] | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    selected = {str(item).strip() for item in selected_case_ids if str(item).strip()}
    overrides = runtime_overrides or {}
    case_results: list[dict[str, Any]] = []
    started = time.monotonic()
    observed_case_ids: set[str] = set()

    for fixture in load_ecosystem_fixtures(fixtures_root):
        source_dir = Path(fixture["_source_dir"])
        for case in fixture["cases"]:
            case_id = str(case["id"])
            if selected and case_id not in selected:
                continue
            observed_case_ids.add(case_id)
            case_results.append(
                _validate_case(
                    fixture,
                    case,
                    source_dir=source_dir,
                    execute=execute,
                    runtime_overrides=overrides.get(str(case["ecosystem_id"]), {}),
                )
            )

    missing = sorted(selected - observed_case_ids)
    if missing:
        raise ValueError(f"unknown ecosystem fixture cases: {', '.join(missing)}")
    counts = {
        status: sum(1 for item in case_results if item["status"] == status)
        for status in ("passed", "failed", "blocked", "planned")
    }
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "os": platform_id(),
            "architecture": architecture_id(),
            "source_revision": source_revision or _source_revision(),
            "working_tree_dirty": _working_tree_dirty(),
            "execution_mode": "descriptor_authorized_fixture" if execute else "dry_run",
        },
        "cases": case_results,
        "summary": {
            "total": len(case_results),
            **counts,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
        "support_claim": False,
        "promotion_policy": (
            "A passed cell is evidence eligible for review; this receipt never "
            "promotes registry support automatically."
        ),
    }


def write_validation_receipt(receipt: Mapping[str, Any], path: Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def required_cases_satisfied(
    receipt: Mapping[str, Any],
    required_case_ids: Iterable[str],
) -> tuple[bool, list[str]]:
    by_id = {str(item["id"]): item for item in receipt.get("cases") or ()}
    failures = [
        case_id
        for case_id in sorted({str(item) for item in required_case_ids})
        if by_id.get(case_id, {}).get("status") != "passed"
    ]
    return not failures, failures


def _validate_case(
    fixture: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    source_dir: Path,
    execute: bool,
    runtime_overrides: Mapping[str, str],
) -> dict[str, Any]:
    case_id = str(case["id"])
    ecosystem_id = str(case["ecosystem_id"])
    with tempfile.TemporaryDirectory(prefix="aiteam-ecosystem-") as temp_dir:
        workspace = Path(temp_dir) / f"fixture {case_id} ñ"
        shutil.copytree(
            source_dir,
            workspace,
            ignore=shutil.ignore_patterns("fixture.json"),
        )
        detection = detect_project_ecosystems(workspace)
        expected = set(fixture["expected_detected"])
        detected = set(detection["detected_ids"])
        if not expected.issubset(detected):
            return _case_result(
                case,
                status="failed",
                actions=[],
                reason="detection_mismatch",
                capability_gaps=[
                    _capability_gap(
                        ecosystem_id,
                        "detect",
                        None,
                        f"missing_detection:{','.join(sorted(expected - detected))}",
                    )
                ],
            )

        action_results: list[dict[str, Any]] = []
        completed_actions: set[str] = set()
        effective_overrides = dict(runtime_overrides)
        if ecosystem_id == "python":
            effective_overrides.setdefault("python", sys.executable)
        for action in case["actions"]:
            action_results.append(
                _validate_action(
                    workspace,
                    ecosystem_id=ecosystem_id,
                    action=action,
                    execute=execute,
                    runtime_overrides=effective_overrides,
                    completed_actions=completed_actions,
                )
            )
            if action_results[-1]["status"] in {"passed", "planned"}:
                completed_actions.add(str(action["id"]))
        statuses = {item["status"] for item in action_results}
        if "failed" in statuses:
            status = "failed"
        elif "blocked" in statuses:
            status = "blocked"
        elif not execute or "planned" in statuses:
            status = "planned"
        else:
            status = "passed"
        gaps = [
            item["capability_gap"]
            for item in action_results
            if item.get("capability_gap")
        ]
        return _case_result(
            case,
            status=status,
            actions=action_results,
            reason="all_actions_passed" if status == "passed" else f"case_{status}",
            capability_gaps=gaps,
        )


def _validate_action(
    workspace: Path,
    *,
    ecosystem_id: str,
    action: Mapping[str, Any],
    execute: bool,
    runtime_overrides: Mapping[str, str],
    completed_actions: Iterable[str],
) -> dict[str, Any]:
    action_id = str(action["id"])
    plan = plan_ecosystem_command(
        workspace,
        ecosystem_id=ecosystem_id,
        action_id=action_id,
        granted_capabilities=("build_execute", "test_execute"),
        authorized=True,
        include_planned=True,
        completed_actions=completed_actions,
        runtime_overrides=runtime_overrides,
    )
    if not plan["allowed"]:
        gap = dict(
            plan.get("capability_gap")
            or _capability_gap(
                ecosystem_id,
                action_id,
                plan.get("command_id"),
                str(plan["reason"]),
            )
        )
        return {
            "action": action_id,
            "status": "blocked",
            "command_id": plan.get("command_id"),
            "reason": plan["reason"],
            "capability_gap": gap,
            "support_claim": False,
        }
    if not execute:
        return {
            "action": action_id,
            "status": "planned",
            "command_id": plan["command_id"],
            "reason": "dry_run",
            "support_claim": False,
        }

    cwd = _resolve_workspace_cwd(workspace, str(plan["cwd"]))
    if cwd is None:
        return {
            "action": action_id,
            "status": "failed",
            "command_id": plan["command_id"],
            "reason": "cwd_escaped_workspace",
            "support_claim": False,
        }
    child_env = dict(os.environ)
    child_env.update({str(key): str(value) for key, value in plan["env"].items()})
    runtime = _runtime_receipt(plan, workspace=workspace)
    if not runtime["healthy"]:
        reason = f"runtime_probe_failed:{runtime['id']}"
        return {
            "action": action_id,
            "status": "blocked",
            "command_id": plan["command_id"],
            "reason": reason,
            "runtime": runtime,
            "capability_gap": _capability_gap(
                ecosystem_id,
                action_id,
                plan["command_id"],
                reason,
            ),
            "support_claim": False,
        }
    started = time.monotonic()
    try:
        completed = run_command(
            plan["argv"],
            cwd=cwd,
            env=child_env,
            timeout=int(plan["timeout_seconds"]),
        )
    except subprocess.TimeoutExpired:
        return {
            "action": action_id,
            "status": "failed",
            "command_id": plan["command_id"],
            "reason": "timeout",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "support_claim": False,
        }
    duration_ms = int((time.monotonic() - started) * 1000)
    output = _redact_output(
        f"{completed.stdout}\n{completed.stderr}",
        workspace=workspace,
    )
    artifacts = {
        pattern: sorted(
            path.relative_to(workspace).as_posix() for path in workspace.glob(pattern)
        )[:20]
        for pattern in action["artifacts"]
    }
    missing_artifacts = sorted(
        pattern for pattern, matches in artifacts.items() if not matches
    )
    passed = completed.returncode == 0 and not missing_artifacts
    return {
        "action": action_id,
        "status": "passed" if passed else "failed",
        "command_id": plan["command_id"],
        "reason": (
            "exit_zero_and_artifacts_present"
            if passed
            else (
                f"missing_artifacts:{','.join(missing_artifacts)}"
                if completed.returncode == 0
                else f"exit_code:{completed.returncode}"
            )
        ),
        "exit_code": completed.returncode,
        "duration_ms": duration_ms,
        "runtime": runtime,
        "artifacts": artifacts,
        "output_excerpt": output[:1200],
        "support_claim": False,
        "eligible_for_promotion_review": passed,
    }


def _resolve_workspace_cwd(workspace: Path, planned_cwd: str) -> Path | None:
    resolved_workspace = workspace.resolve()
    cwd = (resolved_workspace / planned_cwd).resolve()
    try:
        cwd.relative_to(resolved_workspace)
    except ValueError:
        return None
    return cwd


def _case_result(
    case: Mapping[str, Any],
    *,
    status: str,
    actions: list[dict[str, Any]],
    reason: str,
    capability_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": str(case["id"]),
        "ecosystem_id": str(case["ecosystem_id"]),
        "status": status,
        "reason": reason,
        "actions": actions,
        "capability_gaps": capability_gaps,
        "support_claim": False,
    }


def _capability_gap(
    ecosystem_id: str,
    action_id: str,
    descriptor_id: str | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": CAPABILITY_GAP_SCHEMA_VERSION,
        "ecosystem_id": ecosystem_id,
        "action_id": action_id,
        "descriptor_id": descriptor_id,
        "owner": "machine_environment_owner",
        "reason": reason,
        "next_action": (
            "Install or configure the descriptor runtime outside AI Teams, "
            "then rerun this fixture. AI Teams does not install it automatically."
        ),
    }


def _validate_fixture(payload: Mapping[str, Any]) -> None:
    expected = {"schema_version", "fixture_id", "expected_detected", "cases"}
    if set(payload) != expected:
        raise ValueError("ecosystem fixture fields drift")
    if payload.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported ecosystem fixture")
    if not str(payload.get("fixture_id") or "").strip():
        raise ValueError("ecosystem fixture id required")
    if (
        not isinstance(payload.get("expected_detected"), list)
        or not payload["expected_detected"]
    ):
        raise ValueError("ecosystem fixture detections required")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("ecosystem fixture cases required")
    case_ids: set[str] = set()
    for case in cases:
        if set(case) != {"id", "ecosystem_id", "actions"}:
            raise ValueError("ecosystem fixture case fields drift")
        case_id = str(case.get("id") or "")
        if not case_id or case_id in case_ids:
            raise ValueError("ecosystem fixture case id invalid")
        case_ids.add(case_id)
        actions = case.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("ecosystem fixture case actions required")
        for action in actions:
            if set(action) != {"id", "artifacts"}:
                raise ValueError("ecosystem fixture action fields drift")
            if action.get("id") not in ACTION_IDS:
                raise ValueError("ecosystem fixture action invalid")
            if not isinstance(action.get("artifacts"), list):
                raise TypeError("ecosystem fixture artifacts invalid")


def _source_revision() -> str:
    from_env = str(os.environ.get("GITHUB_SHA") or "").strip()
    if from_env:
        return from_env
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _working_tree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _runtime_receipt(plan: Mapping[str, Any], *, workspace: Path) -> dict[str, Any]:
    executable = str(plan["argv"][0])
    try:
        result = run_command(
            [executable, *plan.get("runtime_version_args", ())],
            cwd=workspace,
            timeout=15,
        )
        version = _redact_output(
            f"{result.stdout}\n{result.stderr}",
            workspace=workspace,
        )
        version = next(
            (line.strip() for line in version.splitlines() if line.strip()),
            "version_output_empty",
        )[:500]
        exit_code: int | None = result.returncode
    except (OSError, subprocess.SubprocessError):
        version = "version_probe_failed"
        exit_code = None
    return {
        "id": str(plan.get("runtime_id") or ""),
        "executable": Path(executable).name,
        "version_excerpt": version,
        "probe_exit_code": exit_code,
        "healthy": exit_code == 0,
    }


def _redact_output(value: str, *, workspace: Path) -> str:
    redacted = str(value or "").replace(str(workspace), "<workspace>")
    home = str(Path.home())
    if home:
        redacted = redacted.replace(home, "<home>")
    redacted = re.sub(
        r"(?i)\b[A-Z]:\\[^\r\n]*",
        "<absolute-path>",
        redacted,
    )
    redacted = re.sub(
        r"(?<![:\w])/(?:[^/\s]+/)+[^ \r\n]*",
        "<absolute-path>",
        redacted,
    )
    return redacted.strip()
