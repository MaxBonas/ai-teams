"""Scoring del benchmark A/B (offline: cero LLM, cero red).

Valida también la equidad de la suite oculta: una implementación correcta de
la spec de goal.md debe puntuar perfecto; una rota debe puntuar mal.
"""
from __future__ import annotations

from pathlib import Path

from scripts.benchmark_vs_codex import score_workspace

HIDDEN = Path(__file__).resolve().parent.parent / "benchmarks" / "cli_conversor" / "hidden_tests"

_CORRECT = '''\
import sys

_FACTORS = {
    "km-mi": lambda v: v * 0.621371,
    "mi-km": lambda v: v / 0.621371,
    "c-f": lambda v: v * 9 / 5 + 32,
    "f-c": lambda v: (v - 32) * 5 / 9,
    "kg-lb": lambda v: v * 2.2046226218,
    "lb-kg": lambda v: v / 2.2046226218,
}


def convertir(tipo: str, valor: float) -> float:
    if tipo not in _FACTORS:
        raise ValueError(f"tipo desconocido: {tipo}")
    return _FACTORS[tipo](valor)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in _FACTORS:
        print("uso: conversor.py <tipo> <valor>", file=sys.stderr)
        return 2
    try:
        valor = float(sys.argv[2])
    except ValueError:
        print("valor no numerico", file=sys.stderr)
        return 2
    print(f"{convertir(sys.argv[1], valor):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def test_correct_implementation_scores_perfect(tmp_path: Path) -> None:
    (tmp_path / "conversor.py").write_text(_CORRECT, encoding="utf-8")

    score = score_workspace(tmp_path, HIDDEN)

    assert score["hidden_exit"] == 0, "una implementación correcta de la spec debe pasar la suite oculta"
    assert score["hidden_failed"] == 0 and score["hidden_errors"] == 0
    assert score["hidden_passed"] >= 9
    assert score["ruff_issues"] is not None
    assert "conversor.py" in score["deliverable_files"]


def test_broken_implementation_scores_failures(tmp_path: Path) -> None:
    (tmp_path / "conversor.py").write_text(
        "import sys\nprint('42')\n", encoding="utf-8"
    )

    score = score_workspace(tmp_path, HIDDEN)

    assert score["hidden_exit"] != 0
    assert score["hidden_failed"] + score["hidden_errors"] > 0


def test_empty_workspace_scores_zero_passed(tmp_path: Path) -> None:
    score = score_workspace(tmp_path, HIDDEN)

    assert score["hidden_passed"] == 0
    assert score["hidden_exit"] != 0
