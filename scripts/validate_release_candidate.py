from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.release_descriptor import (
    ReleaseDescriptorError,
    validate_release_descriptor,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida versión, tag, notas y rollback antes de publicar."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--require-tag", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        descriptor = validate_release_descriptor(
            root, args.version, require_tag=args.require_tag
        )
    except ReleaseDescriptorError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {"ok": True, **descriptor.as_dict()},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
