"""Ejecuta la matriz hermética compuesta de portabilidad de proyecto K.8.6.1."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.ecosystem_validation import validate_ecosystem_fixtures
from aiteam.project_artifact_audit import AuditOptions, audit_project_root
from aiteam.project_hygiene import (
    observe_project_hygiene,
    validate_project_hygiene,
)
from aiteam.provider_cli_update_acceptance import (
    build_provider_cli_update_acceptance,
    validate_provider_cli_update_acceptance,
)
from scripts.accept_guided_setup_adapter_repair import (
    build_acceptance as build_adapter_repair_acceptance,
)
from scripts.accept_guided_setup_adapter_repair import (
    validate_acceptance as validate_adapter_repair_acceptance,
)
from scripts.audit_guided_setup_project_acceptance import (
    build_audit as build_project_acceptance,
)
from scripts.audit_guided_setup_project_acceptance import (
    validate_audit as validate_project_acceptance,
)
from scripts.audit_guided_setup_project_preflight import (
    build_audit as build_preflight_acceptance,
)
from scripts.audit_guided_setup_project_preflight import (
    validate_audit as validate_preflight_acceptance,
)

SCHEMA_VERSION = "project_portability_acceptance_v1"
EXPECTED_CHECKS = frozenset(
    {
        "adapter_repair_matrix_ready",
        "clean_update_contract_equivalent",
        "guided_project_commit_is_atomic_and_resumable",
        "mixed_root_is_read_only_and_conservative",
        "non_programming_project_avoids_test_loops",
        "react_typescript_fixture_passes",
        "receipt_is_redacted_and_secret_free",
        "symlink_or_reparse_is_not_followed",
        "zero_automatic_cleanup_lifecycle",
    }
)
_SENTINEL_SECRET = "fixture-private-token-must-not-leak"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_DATE": "2026-07-30T12:00:00Z",
            "GIT_COMMITTER_DATE": "2026-07-30T12:00:00Z",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
        env=env,
    )


def _create_project(path: Path, *, legacy: bool = False) -> None:
    dotdir = path / ".aiteam"
    dotdir.mkdir(parents=True)
    with closing(sqlite3.connect(dotdir / "aiteam.db")) as conn:
        if legacy:
            conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload TEXT)")
            conn.commit()
            return
        conn.executescript(
            """
            CREATE TABLE goals (id TEXT PRIMARY KEY, source TEXT, created_at TEXT);
            CREATE TABLE agents (id TEXT PRIMARY KEY);
            CREATE TABLE issues (id TEXT PRIMARY KEY);
            CREATE TABLE runs (id TEXT PRIMARY KEY);
            INSERT INTO goals (id, source, created_at)
            VALUES ('fixture-goal', 'guided_setup', '2026-07-30T00:00:00Z');
            """
        )
        conn.commit()


def _create_git_project(
    path: Path,
    *,
    dirty: bool = False,
    remote: bool = False,
) -> None:
    _create_project(path)
    for args in (
        ("init", "-q"),
        ("config", "user.name", "AI Teams Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("add", "."),
        ("commit", "-q", "-m", "fixture"),
    ):
        result = _run_git(path, *args)
        if result.returncode != 0:
            raise RuntimeError(f"git_fixture_failed:{args[0]}")
    if dirty:
        (path / "local-work.txt").write_text("keep", encoding="utf-8")
    if remote:
        result = _run_git(
            path,
            "remote",
            "add",
            "origin",
            f"https://user:{_SENTINEL_SECRET}@example.com/private/repo.git",
        )
        if result.returncode != 0:
            raise RuntimeError("git_fixture_failed:remote")


def _create_directory_link(link: Path, target: Path) -> str:
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("fixture_reparse_point_unavailable")
    return "junction"


def _unlink_directory_link(link: Path, kind: str) -> None:
    if kind == "junction":
        os.rmdir(link)
    else:
        link.unlink()


def _entry(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        row for row in report["entries"] if row["relative_path"] == name
    )


def _build_mixed_root_evidence() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="aiteam-portability-") as raw:
        base = Path(raw)
        root = base / "mixed-projects"
        root.mkdir()

        personal = root / "Cliente Personal 2"
        personal.mkdir()
        personal_marker = personal / "contrato-privado.txt"
        personal_marker.write_text(_SENTINEL_SECRET, encoding="utf-8")

        disposable = root / "Demo 40"
        _create_project(disposable, legacy=True)
        clean_git = root / "Demo 41"
        _create_git_project(clean_git)
        dirty_git = root / "Demo 42"
        _create_git_project(dirty_git, dirty=True)
        remote_git = root / "Demo 43"
        _create_git_project(remote_git, remote=True)
        corrupt = root / "Demo 44"
        (corrupt / ".aiteam").mkdir(parents=True)
        (corrupt / ".aiteam" / "aiteam.db").write_bytes(b"not-sqlite")
        staging = root / ".aiteam-project-staging-interrupted"
        staging.mkdir()
        (staging / "journal.tmp").write_text("interrupted", encoding="utf-8")

        outside = base / "outside-protected"
        _create_project(outside)
        link = root / "Demo 88"
        link_kind = _create_directory_link(link, outside)
        try:
            personal_before = personal_marker.read_bytes()
            outside_before = (outside / ".aiteam" / "aiteam.db").read_bytes()
            hygiene = observe_project_hygiene(root, configured=True)
            validate_project_hygiene(hygiene)
            audit = audit_project_root(
                root,
                options=AuditOptions(workers=2),
            )
            personal_after = personal_marker.read_bytes()
            outside_after = (outside / ".aiteam" / "aiteam.db").read_bytes()
        finally:
            _unlink_directory_link(link, link_kind)

    classifications = Counter(
        str(row["classification"]) for row in audit["entries"]
    )
    remote_entry = _entry(audit, remote_git.name)
    link_entry = _entry(audit, "Demo 88")
    return {
        "audit_schema_version": audit["schema_version"],
        "hygiene_schema_version": hygiene["schema_version"],
        "classification_counts": dict(sorted(classifications.items())),
        "personal_protected": (
            _entry(audit, personal.name)["classification"] == "personal_protected"
        ),
        "disposable_is_candidate_only": (
            _entry(audit, disposable.name)["classification"]
            == "aiteam_disposable_candidate"
            and audit["safety"]["cleanup_authorized"] is False
        ),
        "clean_git_observed": (
            _entry(audit, clean_git.name)["evidence"]["git"]["state"]
            == "observed"
        ),
        "dirty_git_preserved": (
            _entry(audit, dirty_git.name)["classification"]
            == "aiteam_preserve_or_migrate"
        ),
        "remote_git_preserved_and_redacted": (
            remote_entry["classification"] == "aiteam_preserve_or_migrate"
            and remote_entry["evidence"]["git"]["remote_hosts"]
            == ["example.com"]
        ),
        "corrupt_db_requires_owner_review": (
            _entry(audit, corrupt.name)["classification"]
            == "ambiguous_owner_review_required"
        ),
        "interrupted_staging_detected": (
            hygiene["counts"]["staging_leftovers"] == 1
        ),
        "reparse_not_followed": (
            link_entry["classification"] == "ambiguous_owner_review_required"
            and link_entry["evidence"]["reparse_point"] is True
            and link_entry["evidence"]["database"]["state"]
            == "not_inspected_reparse_point"
        ),
        "protected_bytes_unchanged": (
            personal_before == personal_after and outside_before == outside_after
        ),
        "project_writes_performed": audit["safety"]["project_writes_performed"],
        "cleanup_authorized": audit["safety"]["cleanup_authorized"],
        "lifecycle": hygiene["lifecycle"],
        "link_fixture_kind": link_kind,
    }


def _source_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report["schema_version"],
        "summary": report["summary"],
        "sha256": _hash(
            {
                "schema_version": report["schema_version"],
                "scope": report.get("scope"),
                "checks": report.get("checks"),
                "scenarios": report.get("scenarios"),
                "summary": report["summary"],
            }
        ),
    }


def build_acceptance(repo_root: Path) -> dict[str, Any]:
    project = build_project_acceptance(repo_root)
    validate_project_acceptance(project)
    preflight = build_preflight_acceptance(repo_root)
    validate_preflight_acceptance(preflight)
    adapters = build_adapter_repair_acceptance(repo_root)
    validate_adapter_repair_acceptance(adapters)
    update = build_provider_cli_update_acceptance()
    validate_provider_cli_update_acceptance(update)
    mixed_root = _build_mixed_root_evidence()
    web = validate_ecosystem_fixtures(
        selected_case_ids=("web_vite_react_typescript",),
    )

    sources = {
        "adapter_repair": _source_summary(adapters),
        "guided_project": _source_summary(project),
        "project_preflight": _source_summary(preflight),
        "provider_update": _source_summary(update),
        "react_typescript": {
            "schema_version": web["schema_version"],
            "case_ids": [row["id"] for row in web["cases"]],
            "statuses": [row["status"] for row in web["cases"]],
            "action_ids": [
                action["command_id"]
                for row in web["cases"]
                for action in row["actions"]
            ],
        },
        "mixed_root": mixed_root,
    }
    serialized_sources = json.dumps(
        sources,
        ensure_ascii=False,
        sort_keys=True,
    )
    checks = {
        "adapter_repair_matrix_ready": (
            adapters["summary"]["repair_acceptance_ready"] is True
        ),
        "clean_update_contract_equivalent": (
            update["summary"]["promotion_ready"] is True
        ),
        "guided_project_commit_is_atomic_and_resumable": (
            project["summary"]["project_setup_acceptance_ready"] is True
            and project["checks"][
                "intermediate_failure_rolls_back_create_and_import"
            ]
            and project["checks"][
                "commit_receipt_is_idempotent_and_conflict_safe"
            ]
            and project["checks"]["session_resume_keeps_identity_and_revision"]
        ),
        "mixed_root_is_read_only_and_conservative": (
            mixed_root["personal_protected"]
            and mixed_root["disposable_is_candidate_only"]
            and mixed_root["clean_git_observed"]
            and mixed_root["dirty_git_preserved"]
            and mixed_root["remote_git_preserved_and_redacted"]
            and mixed_root["corrupt_db_requires_owner_review"]
            and mixed_root["interrupted_staging_detected"]
            and mixed_root["protected_bytes_unchanged"]
            and mixed_root["project_writes_performed"] == 0
            and mixed_root["cleanup_authorized"] is False
        ),
        "non_programming_project_avoids_test_loops": (
            project["checks"][
                "new_research_project_is_lead_only_without_test_loop"
            ]
            and preflight["checks"]["research_avoids_software_tests"]
            and preflight["checks"]["operations_avoids_software_tests"]
        ),
        "react_typescript_fixture_passes": (
            web["summary"]["total"] == 1
            and web["summary"]["passed"] == 1
            and sources["react_typescript"]["action_ids"]
            == ["npm_build", "npm_test", "npm_lint", "npm_typecheck"]
        ),
        "receipt_is_redacted_and_secret_free": (
            _SENTINEL_SECRET not in serialized_sources
            and str(Path.home()) not in serialized_sources
            and "private/repo" not in serialized_sources
        ),
        "symlink_or_reparse_is_not_followed": mixed_root[
            "reparse_not_followed"
        ],
        "zero_automatic_cleanup_lifecycle": mixed_root["lifecycle"]
        == {
            "automatic_cleanup_installed": False,
            "startup_cleanup_installed": False,
            "ttl_cleanup_installed": False,
            "doctor_can_mutate": False,
        },
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "fixtures_only": True,
            "real_projects_root_read_only": True,
            "user_projects_mutated": False,
            "global_installations_mutated": False,
            "cleanup_jobs_installed": False,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
            "paths_emitted": False,
        },
        "checks": checks,
        "sources": sources,
        "summary": {
            "portability_acceptance_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    report["evidence_hash"] = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report["scope"],
            "checks": report["checks"],
            "sources": report["sources"],
        }
    )
    validate_acceptance(report)
    return report


def validate_acceptance(report: dict[str, Any]) -> None:
    if set(report) != {
        "schema_version",
        "scope",
        "checks",
        "sources",
        "summary",
        "evidence_hash",
    }:
        raise ValueError("project portability acceptance fields drift")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("project portability acceptance schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("project portability acceptance matrix drift")
    if any(not isinstance(value, bool) for value in checks.values()):
        raise ValueError("project portability acceptance check type drift")
    expected_summary = {
        "portability_acceptance_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("project portability acceptance summary drift")
    expected_hash = _hash(
        {
            "schema_version": report["schema_version"],
            "scope": report.get("scope"),
            "checks": checks,
            "sources": report.get("sources"),
        }
    )
    if report.get("evidence_hash") != expected_hash:
        raise ValueError("project portability acceptance evidence drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_acceptance(REPO_ROOT)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return (
        0
        if report["summary"]["portability_acceptance_ready"]
        or not args.strict
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
