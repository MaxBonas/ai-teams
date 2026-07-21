"""Print the hermetic built-in model-flow matrix as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_flow_matrix import audit_builtin_model_flows  # noqa: E402


def main() -> int:
    report = audit_builtin_model_flows()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
