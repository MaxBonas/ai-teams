"""Evalúa el selector de perfil contra casos etiquetados, sin ejecutar LLMs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.run_profiles import FULL_TEAM, SOLO_LEAD, select_execution_profile  # noqa: E402

DEFAULT_CASES = REPO_ROOT / "benchmarks" / "profile_selector_cases.json"


def evaluate_cases(path: Path = DEFAULT_CASES) -> dict[str, Any]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("profile selector cases must be a non-empty list")
    results: list[dict[str, Any]] = []
    unsafe_solo = 0
    overteam = 0
    correct = 0
    for case in cases:
        expected = str(case.get("expected") or "")
        if expected not in {SOLO_LEAD, FULL_TEAM}:
            raise ValueError(f"case {case.get('id')} has invalid expected profile: {expected}")
        selected = select_execution_profile(
            criticality=case.get("criticality"),
            ambiguity=case.get("ambiguity"),
            independent_verification=case.get("independent_verification"),
            parallel_workstreams=case.get("parallel_workstreams"),
            reversible=case.get("reversible"),
        )
        is_correct = selected.profile == expected
        correct += int(is_correct)
        unsafe_solo += int(expected == FULL_TEAM and selected.profile == SOLO_LEAD)
        overteam += int(expected == SOLO_LEAD and selected.profile == FULL_TEAM)
        results.append(
            {
                "id": case.get("id"),
                "expected": expected,
                "selected": selected.profile,
                "reason": selected.reason,
                "correct": is_correct,
            }
        )
    total = len(results)
    return {
        "cases": total,
        "correct": correct,
        "accuracy": round(correct / total, 4),
        "unsafe_solo": unsafe_solo,
        "overteam": overteam,
        "passes_safety_gate": unsafe_solo == 0,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = evaluate_cases(args.cases)
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["passes_safety_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
