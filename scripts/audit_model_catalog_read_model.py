"""Materializa y audita el read model shadow del catálogo sin canarios vivos."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiteam.model_catalog_read_model import (
    audit_model_catalog_read_model,
    build_current_model_catalog_read_model,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Construye el catálogo/scoring shadow desde configuración y SQLite explícitas."
    )
    parser.add_argument(
        "--db", action="append", default=[], help="SQLite de proyecto; repetible"
    )
    parser.add_argument("--output", type=Path, help="Ruta JSON opcional para el recibo")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Guarda metadata, habilitaciones Tier 1 y auditoría sin las 17 celdas completas.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Imprime el recibo completo"
    )
    args = parser.parse_args(argv)

    observed_at = datetime.now(timezone.utc)
    read_model = build_current_model_catalog_read_model(
        db_paths=[Path(item) for item in args.db],
        observed_at=observed_at,
        repo_root=ROOT,
    )
    audit = audit_model_catalog_read_model(read_model)
    tier1_enabled = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "profile_id": (candidate.get("identity") or {}).get("profile_id"),
            "model": (candidate.get("identity") or {}).get("model_id"),
            "canonical_role": role.get("canonical_role"),
            "authority": role.get("tier1_authority"),
        }
        for candidate in read_model.get("candidates") or ()
        for role in candidate.get("roles") or ()
        if (role.get("tier1_authority") or {}).get("enabled") is True
    ]
    receipt = {
        "schema_version": "model_catalog_read_model_receipt_v1",
        "observed_at": observed_at.isoformat(),
        "read_model": (
            {
                "schema_version": read_model.get("schema_version"),
                "score_version": read_model.get("score_version"),
                "content_hash": read_model.get("content_hash"),
                "tier1_authority_contract": read_model.get(
                    "tier1_authority_contract"
                ),
                "candidate_count": len(read_model.get("candidates") or ()),
                "role_score_count": sum(
                    len(candidate.get("roles") or ())
                    for candidate in read_model.get("candidates") or ()
                ),
                "tier1_enabled": tier1_enabled,
            }
            if args.compact
            else read_model
        ),
        "audit": audit,
    }
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    if args.json:
        print(encoded)
    else:
        print(
            json.dumps(
                {
                    "ok": audit["ok"],
                    "candidates": audit["candidate_count"],
                    "role_scores": audit["role_score_count"],
                    "automatic_candidates": audit["automatic_candidate_count"],
                    "failures": audit["failures"],
                    "warnings": len(audit["warnings"]),
                    "content_hash": read_model["content_hash"],
                    "output": str(args.output) if args.output else None,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
