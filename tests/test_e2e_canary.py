"""El canario e2e corre con la suite: si el flujo completo de orquestación
deja de converger (delegación → file_ops → dependencias → test_runner builtin
→ gate → cierre), esto falla en CI antes de tocar un proyecto real."""
from __future__ import annotations

from pathlib import Path

from scripts.e2e_canary import run_canary


def test_e2e_canary_converges(tmp_path: Path) -> None:
    report = run_canary(tmp_path)

    assert report["ok"], f"canario roto: {report['checks']}"
    assert report["ticks"] <= 3, "convergencia degradada: antes bastaba 1 tick"
