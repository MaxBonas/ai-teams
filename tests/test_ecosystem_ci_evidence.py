from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_ecosystem_ci_receipts import (
    EXPECTED_CASES,
    audit_receipts,
)


def _write_receipt(
    root: Path,
    *,
    name: str,
    system: str,
    revision: str,
    case_ids: set[str],
) -> None:
    cases = [{"id": case_id, "status": "passed"} for case_id in sorted(case_ids)]
    payload = {
        "schema_version": "ecosystem_validation_receipt_v1",
        "provenance": {
            "os": system,
            "architecture": "x86_64",
            "source_revision": revision,
            "working_tree_dirty": False,
        },
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": len(cases),
            "failed": 0,
            "blocked": 0,
            "planned": 0,
        },
        "support_claim": False,
    }
    (root / f"{name}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _complete_matrix(root: Path, revision: str) -> None:
    grouped = {
        "python_pytest",
        "javascript_npm",
        "monorepo_python",
        "monorepo_javascript",
        "web_vite_react_typescript",
    }
    singles = EXPECTED_CASES - grouped
    for system in ("windows", "linux", "macos"):
        _write_receipt(
            root,
            name=f"{system}-base",
            system=system,
            revision=revision,
            case_ids=grouped,
        )
        for case_id in singles:
            _write_receipt(
                root,
                name=f"{system}-{case_id}",
                system=system,
                revision=revision,
                case_ids={case_id},
            )


def test_ecosystem_ci_audit_accepts_exact_complete_matrix(tmp_path: Path) -> None:
    revision = "a" * 40
    _complete_matrix(tmp_path, revision)

    result = audit_receipts(tmp_path, expected_revision=revision)

    assert result["ok"] is True
    assert result["observed_receipts"] == 18
    assert result["observed_cells"] == 30
    assert result["missing_cells"] == []
    assert all(len(item["sha256"]) == 64 for item in result["sources"])
    assert result["support_claim"] is False


def test_ecosystem_ci_audit_fails_closed_for_missing_or_foreign_sha(
    tmp_path: Path,
) -> None:
    revision = "a" * 40
    _complete_matrix(tmp_path, revision)
    next(tmp_path.glob("windows-*.json")).unlink()
    foreign = next(tmp_path.glob("linux-*.json"))
    payload = json.loads(foreign.read_text(encoding="utf-8"))
    payload["provenance"]["source_revision"] = "b" * 40
    foreign.write_text(json.dumps(payload), encoding="utf-8")

    result = audit_receipts(tmp_path, expected_revision=revision)

    assert result["ok"] is False
    assert result["observed_receipts"] == 17
    assert result["missing_cells"]
    assert any("revisión distinta" in error for error in result["errors"])
