from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.project_artifact_quarantine import (
    QuarantineError,
    apply_quarantine,
    restore_quarantine,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cuarentena/restaura un batch aprobado; no soporta purga."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--manifest", type=Path, required=True)
    apply_parser.add_argument("--quarantine-root", type=Path, required=True)
    apply_parser.add_argument("--approve-manifest-sha256", required=True)
    apply_parser.add_argument("--approve-batch-sha256", required=True)
    apply_parser.add_argument("--active-workspace", type=Path)
    apply_parser.add_argument("--registry-workspace", type=Path, action="append", default=[])
    apply_parser.add_argument("--workers", type=int, default=8)
    apply_parser.add_argument("--git-timeout", type=float, default=4.0)
    apply_parser.add_argument("--max-files", type=int, default=200_000)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--batch-dir", type=Path, required=True)
    restore_parser.add_argument("--approve-batch-sha256", required=True)

    args = parser.parse_args()
    try:
        if args.command == "apply":
            journal = apply_quarantine(
                args.manifest,
                args.quarantine_root,
                approved_manifest_sha256=args.approve_manifest_sha256,
                approved_batch_sha256=args.approve_batch_sha256,
                active_workspace=args.active_workspace,
                registry_workspaces=tuple(args.registry_workspace),
                workers=args.workers,
                git_timeout_seconds=args.git_timeout,
                max_files_per_folder=args.max_files,
            )
        else:
            journal = restore_quarantine(
                args.batch_dir,
                approved_batch_sha256=args.approve_batch_sha256,
            )
    except (OSError, QuarantineError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "purge_authorized": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": journal["schema_version"],
                "batch_id": journal["batch_id"],
                "state": journal["state"],
                "item_count": len(journal["items"]),
                "journal_sha256": journal["journal_sha256"],
                "purge_authorized": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
