from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from aiteam.db.model_catalog_maintenance import (
    DIMENSIONS,
    reconcile_model_catalog_maintenance,
)
from aiteam.model_catalog_read_model import (
    build_current_model_catalog_read_model,
)

SCHEMA_VERSION = "model_catalog_maintenance_audit_v1"


def build_audit(read_model: dict[str, Any]) -> dict[str, Any]:
    mutations: dict[str, Callable[[dict[str, Any]], None]] = {
        "model": lambda row: row["candidates"][0].update(
            label=f"{row['candidates'][0].get('label', '')}:audit"
        ),
        "cli": lambda row: row.setdefault(
            "evaluation_version_evidence",
            {},
        ).update(audit_cli="999.0.0"),
        "price": lambda row: row["candidates"][0][
            "model_metadata"
        ].update(price_note="maintenance-audit-price"),
        "quota": lambda row: row["candidates"][0]["roles"][0].setdefault(
            "runtime_metrics",
            {},
        ).update(quota_state="maintenance-audit"),
        "prompt": lambda row: row["candidates"][0]["roles"][0].setdefault(
            "evaluation",
            {},
        ).update(prompt_contract="maintenance-audit"),
        "tool": lambda row: row["candidates"][0]["roles"][0].setdefault(
            "compatibility",
            {},
        ).update(tool_contract="maintenance-audit"),
        "contract": lambda row: row.update(
            score_version=f"{row.get('score_version', '')}:audit"
        ),
    }
    dimension_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="aiteam-catalog-maintenance-") as raw:
        root = Path(raw)
        cadence_db = root / "cadence.db"
        first = reconcile_model_catalog_maintenance(
            cadence_db,
            read_model,
            observed_at="2026-07-30T10:00:00+00:00",
        )
        same = reconcile_model_catalog_maintenance(
            cadence_db,
            read_model,
            observed_at="2026-07-31T10:00:00+00:00",
        )
        monthly = reconcile_model_catalog_maintenance(
            cadence_db,
            read_model,
            observed_at="2026-08-01T10:00:00+00:00",
        )
        for dimension in DIMENSIONS:
            db_path = root / f"{dimension}.db"
            reconcile_model_catalog_maintenance(
                db_path,
                read_model,
                observed_at="2026-07-30T10:00:00+00:00",
            )
            changed = deepcopy(read_model)
            mutations[dimension](changed)
            changed = _seal(changed)
            result = reconcile_model_catalog_maintenance(
                db_path,
                changed,
                observed_at="2026-07-30T11:00:00+00:00",
            )
            reasons = list(result["snapshot"]["trigger_reasons"])
            dimension_rows.append(
                {
                    "dimension": dimension,
                    "persisted": result["persisted"],
                    "trigger_observed": f"{dimension}_changed" in reasons,
                    "hash_valid": result["snapshot"]["hash_valid"],
                }
            )
    report = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "schema_version": read_model.get("schema_version"),
            "score_version": read_model.get("score_version"),
            "content_hash": read_model.get("content_hash"),
            "candidate_count": len(read_model.get("candidates") or ()),
        },
        "scope": {
            "fixture_database_only": True,
            "global_installations_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "paths_emitted": False,
            "retention": "append_only_no_age_deletion",
        },
        "cadence": {
            "initial_persisted": first["persisted"],
            "same_month_idempotent": same["persisted"] is False,
            "next_month_persisted": monthly["persisted"],
            "monthly_trigger_observed": (
                monthly["snapshot"]["trigger_reasons"] == ["monthly"]
            ),
            "hashes_valid": (
                first["snapshot"]["hash_valid"]
                and monthly["snapshot"]["hash_valid"]
            ),
        },
        "dimensions": dimension_rows,
        "summary": {
            "cadence_ready": all(
                (
                    first["persisted"],
                    same["persisted"] is False,
                    monthly["persisted"],
                    monthly["snapshot"]["trigger_reasons"] == ["monthly"],
                )
            ),
            "dimensions_ready": all(
                row["persisted"]
                and row["trigger_observed"]
                and row["hash_valid"]
                for row in dimension_rows
            ),
        },
    }
    report["summary"]["promotion_ready"] = all(report["summary"].values())
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("model catalog maintenance audit schema drift")
    if report.get("scope") != {
        "fixture_database_only": True,
        "global_installations_mutated": False,
        "secrets_read": False,
        "inference_attempted": False,
        "paths_emitted": False,
        "retention": "append_only_no_age_deletion",
    }:
        raise ValueError("model catalog maintenance audit scope drift")
    rows = report.get("dimensions")
    if not isinstance(rows, list) or {row.get("dimension") for row in rows} != set(
        DIMENSIONS
    ):
        raise ValueError("model catalog maintenance dimension coverage drift")
    summary = report.get("summary")
    if not isinstance(summary, dict) or summary.get("promotion_ready") is not (
        summary.get("cadence_ready") is True
        and summary.get("dimensions_ready") is True
    ):
        raise ValueError("model catalog maintenance audit summary drift")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result.pop("content_hash", None)
    payload = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    result["content_hash"] = hashlib.sha256(payload).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audita triggers y cadencia durable del mantenimiento de modelos "
            "sin inferencias ni acceso a secretos."
        )
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    report = build_audit(build_current_model_catalog_read_model(db_paths=()))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["promotion_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
