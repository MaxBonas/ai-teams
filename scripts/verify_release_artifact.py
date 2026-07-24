from __future__ import annotations

import argparse
import json
from pathlib import Path

from aiteam.release_artifact import ReleaseArtifactError, verify_release_artifact


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verifica checksum externo, contenido y manifiesto de un ZIP."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--require-promotable", action="store_true")
    args = parser.parse_args()
    try:
        result = verify_release_artifact(
            args.archive,
            checksum_path=args.checksum,
            require_promotable=args.require_promotable,
        )
    except ReleaseArtifactError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result.as_dict()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
