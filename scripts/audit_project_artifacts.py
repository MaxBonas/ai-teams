from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.project_artifact_audit import (
    AuditOptions,
    audit_project_root,
    write_audit_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventario read-only y redactado de artefactos de proyecto AI Teams."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--active-workspace", type=Path)
    parser.add_argument("--registry-workspace", type=Path, action="append", default=[])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--git-timeout", type=float, default=4.0)
    parser.add_argument("--max-files", type=int, default=200_000)
    parser.add_argument("--probe-process-handles", action="store_true")
    args = parser.parse_args()

    options = AuditOptions(
        git_timeout_seconds=args.git_timeout,
        workers=args.workers,
        max_files_per_folder=args.max_files,
        probe_process_handles=args.probe_process_handles,
    )
    report = audit_project_root(
        args.root,
        active_workspace=args.active_workspace,
        registry_workspaces=args.registry_workspace,
        options=options,
    )
    write_audit_receipt(report, args.output, audited_root=args.root)
    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": report["schema_version"],
                "summary": report["summary"],
                "content_sha256": report["content_sha256"],
                "cleanup_authorized": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
