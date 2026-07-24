from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_SYSTEMS = {"windows", "linux", "macos"}
EXPECTED_CASES = {
    "python_pytest",
    "javascript_npm",
    "monorepo_python",
    "monorepo_javascript",
    "java_maven_junit",
    "dotnet_xunit",
    "go_builtin",
    "rust_cargo",
    "c_cpp_cmake",
}
EXPECTED_RECEIPT_COUNT = 18


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_receipts(receipts_root: Path, *, expected_revision: str) -> dict[str, Any]:
    errors: list[str] = []
    cells: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    revision_valid = bool(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_revision))
    if not revision_valid:
        errors.append("expected_revision no es un SHA Git lowercase")

    receipt_paths = sorted(Path(receipts_root).rglob("*.json"))
    if len(receipt_paths) != EXPECTED_RECEIPT_COUNT:
        errors.append(
            f"se esperaban {EXPECTED_RECEIPT_COUNT} receipts y llegaron "
            f"{len(receipt_paths)}"
        )

    seen_cells: set[tuple[str, str]] = set()
    for index, path in enumerate(receipt_paths, start=1):
        source_id = f"receipt-{index:02d}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{source_id}: JSON inválido: {exc}")
            continue
        provenance = payload.get("provenance", {})
        system = provenance.get("os")
        architecture = provenance.get("architecture")
        revision = provenance.get("source_revision")
        cases = payload.get("cases")
        summary = payload.get("summary", {})
        sources.append(
            {
                "id": source_id,
                "sha256": _sha256(path),
                "os": system,
                "architecture": architecture,
                "source_revision": revision,
            }
        )
        if payload.get("schema_version") != "ecosystem_validation_receipt_v1":
            errors.append(f"{source_id}: schema_version inesperada")
        if system not in EXPECTED_SYSTEMS:
            errors.append(f"{source_id}: OS inesperado {system!r}")
        if not isinstance(architecture, str) or not architecture:
            errors.append(f"{source_id}: arquitectura ausente")
        if revision != expected_revision:
            errors.append(f"{source_id}: revisión distinta del SHA esperado")
        if provenance.get("working_tree_dirty") is not False:
            errors.append(f"{source_id}: worktree no limpio")
        if payload.get("support_claim") is not False:
            errors.append(f"{source_id}: support_claim debe permanecer false")
        if not isinstance(cases, list) or not cases:
            errors.append(f"{source_id}: no contiene casos")
            continue
        if summary.get("total") != len(cases) or summary.get("passed") != len(cases):
            errors.append(f"{source_id}: el resumen no declara todos los casos passed")
        if any(summary.get(status) != 0 for status in ("failed", "blocked", "planned")):
            errors.append(f"{source_id}: el resumen contiene estados no aprobados")
        for case in cases:
            case_id = case.get("id")
            if case_id not in EXPECTED_CASES:
                errors.append(f"{source_id}: caso inesperado {case_id!r}")
                continue
            if case.get("status") != "passed":
                errors.append(f"{source_id}: {case_id} no pasó")
            cell = (str(system), str(case_id))
            if cell in seen_cells:
                errors.append(f"{source_id}: celda duplicada {system}/{case_id}")
                continue
            seen_cells.add(cell)
            cells.append({"os": str(system), "case_id": str(case_id)})

    expected_cells = {
        (system, case_id) for system in EXPECTED_SYSTEMS for case_id in EXPECTED_CASES
    }
    missing_cells = sorted(expected_cells - seen_cells)
    unexpected_cells = sorted(seen_cells - expected_cells)
    if missing_cells:
        errors.append(f"faltan {len(missing_cells)} celdas requeridas")
    if unexpected_cells:
        errors.append(f"sobran {len(unexpected_cells)} celdas no contratadas")

    return {
        "schema_version": "ecosystem_ci_evidence_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expected_revision": expected_revision,
        "expected_receipts": EXPECTED_RECEIPT_COUNT,
        "observed_receipts": len(receipt_paths),
        "expected_cells": len(expected_cells),
        "observed_cells": len(seen_cells),
        "missing_cells": [
            {"os": system, "case_id": case_id} for system, case_id in missing_cells
        ],
        "unexpected_cells": [
            {"os": system, "case_id": case_id} for system, case_id in unexpected_cells
        ],
        "sources": sources,
        "cells": sorted(cells, key=lambda item: (item["os"], item["case_id"])),
        "errors": errors,
        "support_claim": False,
        "ok": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita receipts de la matriz polyglot y los liga a un SHA."
    )
    parser.add_argument("--receipts-root", type=Path, required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = audit_receipts(
        args.receipts_root.resolve(),
        expected_revision=args.expected_revision,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
