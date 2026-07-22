"""Busca un trigger vivo de contención paralelizable en SQLite retenidas.

Es read-only y no ejecuta modelos. Una señal sólo cuenta si procede de snapshots
exactos, varias raíces y pools, espera paralelizable positiva y ningún adapter
hermético. Las fuentes aproximadas se conservan como diagnóstico, nunca como
autorización para abrir el A/B vivo.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_parallel_channels import audit_database  # noqa: E402

_EPHEMERAL_DIRECTORY_NAMES = {".git", ".test_tmp", ".venv", "node_modules", "venv", "__pycache__"}


def _ephemeral_directory(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _EPHEMERAL_DIRECTORY_NAMES
        or lowered.startswith("pytest-cache-files-")
        or lowered.startswith("tmp")
    )


def _display_path(path: Path) -> str:
    try:
        value = path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        value = path.resolve()
    return str(value).replace("\\", "/")


def discover_databases(
    root: Path,
) -> tuple[list[Path], list[dict[str, str]], list[str]]:
    """Enumera DB sin abortar por temporales de tests inaccesibles."""
    found: list[Path] = []
    errors: list[dict[str, str]] = []
    pruned: list[str] = []

    def _on_error(error: OSError) -> None:
        errors.append({
            "path": str(getattr(error, "filename", "") or "").replace("\\", "/"),
            "error": type(error).__name__,
        })

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=_on_error):
        kept: list[str] = []
        for dirname in dirnames:
            if _ephemeral_directory(dirname):
                pruned.append(_display_path(Path(dirpath) / dirname))
            else:
                kept.append(dirname)
        dirnames[:] = kept
        for filename in filenames:
            if filename.lower().endswith(".db"):
                found.append(Path(dirpath) / filename)
    return sorted(found, key=lambda item: str(item).lower()), errors, sorted(pruned)


def _source_kind(path: Path) -> str:
    text = str(path).replace("\\", "/").lower()
    if "parallel-heartbeat-hermetic" in text:
        return "hermetic"
    if "/live-" in text or "live-run-profile" in text:
        return "live_canary"
    if "/bench/" in text:
        return "benchmark"
    return "runtime_project"


def _contains_hermetic_adapters(path: Path) -> bool:
    try:
        with sqlite3.connect(str(path)) as conn:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "runs" not in tables:
                return False
            return bool(conn.execute(
                "SELECT 1 FROM runs WHERE adapter_type LIKE 'hermetic-%' LIMIT 1"
            ).fetchone())
    except sqlite3.Error:
        return False


def evaluate_trigger_source(
    source: dict[str, Any],
    *,
    contains_hermetic_adapters: bool = False,
) -> list[str]:
    """Razones estables por las que una fuente no satisface el trigger vivo."""
    exclusions: list[str] = []
    if source.get("evidence_quality") != "exact":
        exclusions.append("evidence_not_exact")
    if int(source.get("root_count") or 0) < 2:
        exclusions.append("fewer_than_two_roots")
    if int(source.get("capacity_pool_count") or 0) < 2:
        exclusions.append("fewer_than_two_capacity_pools")
    if (
        int(source.get("parallelizable_wait_runs") or 0) <= 0
        or float(source.get("parallelizable_wait_seconds") or 0) <= 0
    ):
        exclusions.append("no_positive_parallelizable_wait")
    if source.get("source_kind") == "hermetic" or contains_hermetic_adapters:
        exclusions.append("synthetic_hermetic_source")
    return exclusions


def audit_trigger_inventory(root: Path) -> dict[str, Any]:
    databases, discovery_errors, pruned_directories = discover_databases(root)
    sources: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for path in databases:
        if not path.exists() or path.stat().st_size == 0:
            invalid.append({"database": _display_path(path), "error": "empty_database"})
            continue
        try:
            audited = audit_database(path)
        except (sqlite3.Error, ValueError) as exc:
            invalid.append({
                "database": _display_path(path),
                "error": type(exc).__name__,
            })
            continue
        compact = {
            "database": _display_path(path),
            "source_kind": _source_kind(path),
            "evidence_quality": audited["evidence_quality"],
            "recorded_runs": audited["recorded_runs"],
            "timed_runs": audited["timed_runs"],
            "root_count": audited["root_count"],
            "capacity_pool_count": audited["capacity_pool_count"],
            "parallelizable_wait_runs": audited["parallelizable_wait_runs"],
            "parallelizable_wait_seconds": audited["parallelizable_wait_seconds"],
            "candidate_snapshot_batches": audited["dispatch_evidence"][
                "candidate_snapshot_batches"
            ],
            "selected_run_coverage_ratio": audited["dispatch_evidence"][
                "selected_run_coverage_ratio"
            ],
        }
        compact["exclusions"] = evaluate_trigger_source(
            compact,
            contains_hermetic_adapters=_contains_hermetic_adapters(path),
        )
        compact["trigger_candidate"] = not compact["exclusions"]
        sources.append(compact)

    candidates = [item for item in sources if item["trigger_candidate"]]
    quality_counts = {
        quality: sum(item["evidence_quality"] == quality for item in sources)
        for quality in ("exact", "partial_exact", "approximate")
    }
    source_kind_counts = {
        kind: sum(item["source_kind"] == kind for item in sources)
        for kind in ("runtime_project", "live_canary", "benchmark", "hermetic")
    }
    return {
        "schema_version": 1,
        "audit": "parallel_live_trigger_inventory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": _display_path(root),
        "contract": {
            "read_only": True,
            "models_or_network_used": False,
            "required_evidence_quality": "exact",
            "minimum_roots": 2,
            "minimum_capacity_pools": 2,
            "positive_parallelizable_wait_required": True,
            "hermetic_sources_allowed": False,
            "fail_closed_cli_option": "--require-trigger",
        },
        "summary": {
            "discovered_database_count": len(databases),
            "audited_database_count": len(sources),
            "invalid_database_count": len(invalid),
            "discovery_error_count": len(discovery_errors),
            "pruned_ephemeral_directory_count": len(pruned_directories),
            "quality_counts": quality_counts,
            "source_kind_counts": source_kind_counts,
            "trigger_candidate_count": len(candidates),
        },
        "trigger_candidates": candidates,
        "sources": sources,
        "invalid_databases": invalid,
        "discovery_errors": discovery_errors,
        "conclusion": {
            "live_contention_trigger_satisfied": bool(candidates),
            "live_ab_allowed": bool(candidates),
            "default_change_allowed": False,
            "decision": "open_live_ab" if candidates else "wait_for_natural_contention",
            "reason": (
                "existe provenance exacta multi-raíz/multi-pool con espera paralelizable positiva"
                if candidates
                else "ninguna SQLite real retenida satisface todavía el contrato exacto del trigger"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "runtime")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-trigger",
        action="store_true",
        help="Devuelve exit 1 si no existe candidato; útil como gate previo al A/B vivo.",
    )
    args = parser.parse_args()
    report = audit_trigger_inventory(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return int(bool(args.require_trigger) and not report["conclusion"]["live_ab_allowed"])


if __name__ == "__main__":
    raise SystemExit(main())
