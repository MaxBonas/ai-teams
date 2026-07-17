"""Eval determinista de retención semántica para resúmenes causales.

La rúbrica permanece fuera del contexto del curador. Cada criterio representa
una decisión, restricción, riesgo, evidencia u owner que podría cambiar la
siguiente acción del Lead. No juzga estilo: comprueba anclas explícitas y el
presupuesto de compresión.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def evaluate_summary(source: str, summary: str, rubric: dict[str, Any]) -> dict[str, Any]:
    source_chars = len(source)
    summary_chars = len(summary)
    criteria: list[dict[str, Any]] = []
    required_total = 0
    required_retained = 0
    optional_total = 0
    optional_retained = 0
    for item in rubric.get("criteria", []):
        patterns = [str(pattern) for pattern in item.get("patterns", [])]
        minimum = int(item.get("min_matches", 1))
        matched = [
            pattern for pattern in patterns
            if re.search(pattern, summary, flags=re.IGNORECASE | re.DOTALL)
        ]
        retained = len(matched) >= minimum
        required = bool(item.get("required", True))
        if required:
            required_total += 1
            required_retained += int(retained)
        else:
            optional_total += 1
            optional_retained += int(retained)
        criteria.append({
            "id": str(item.get("id") or "unknown"),
            "kind": str(item.get("kind") or "decision"),
            "required": required,
            "retained": retained,
            "matched": matched,
        })

    ratio = summary_chars / source_chars if source_chars else 0.0
    max_ratio = float(rubric.get("max_compression_ratio", 0.30))
    required_rate = required_retained / required_total if required_total else 1.0
    return {
        "benchmark": "context_summary_semantic_retention",
        "rubric_id": rubric.get("id"),
        "source_chars": source_chars,
        "summary_chars": summary_chars,
        "compression_ratio": round(ratio, 4),
        "max_compression_ratio": max_ratio,
        "within_budget": ratio <= max_ratio,
        "required_retained": required_retained,
        "required_total": required_total,
        "required_retention_rate": round(required_rate, 4),
        "optional_retained": optional_retained,
        "optional_total": optional_total,
        "semantic_gate_passed": required_rate == 1.0,
        "accepted": ratio <= max_ratio and required_rate == 1.0,
        "criteria": criteria,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_summary(
        args.source.read_text(encoding="utf-8"),
        args.summary.read_text(encoding="utf-8"),
        json.loads(args.rubric.read_text(encoding="utf-8")),
    )
    serialized = json.dumps(report, indent=2, ensure_ascii=False)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
