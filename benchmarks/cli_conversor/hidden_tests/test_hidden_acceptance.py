"""Suite OCULTA de aceptación — ningún brazo del benchmark la ve al trabajar.

Se copia a <workspace>/.bench_hidden/ y se ejecuta contra el entregable: es el
juez objetivo del A/B (equipo vs codex solo). Deriva EXCLUSIVAMENTE de la spec
publicada en goal.md — si un test de aquí no se deduce de la spec, es un bug
del benchmark, no del candidato.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WS = Path(__file__).resolve().parent.parent


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WS / "conversor.py"), *args],
        capture_output=True, text=True, cwd=str(WS), timeout=30,
    )


def test_km_a_millas() -> None:
    r = run_cli("km-mi", "5")
    assert r.returncode == 0, r.stderr
    assert abs(float(r.stdout.strip()) - 3.11) < 0.011


def test_millas_a_km() -> None:
    r = run_cli("mi-km", "3.106855")
    assert r.returncode == 0, r.stderr
    assert abs(float(r.stdout.strip()) - 5.00) < 0.011


def test_celsius_a_fahrenheit() -> None:
    r = run_cli("c-f", "100")
    assert r.returncode == 0, r.stderr
    assert abs(float(r.stdout.strip()) - 212.00) < 0.011


def test_fahrenheit_a_celsius_negativo() -> None:
    r = run_cli("f-c", "-40")
    assert r.returncode == 0, r.stderr
    assert abs(float(r.stdout.strip()) - (-40.00)) < 0.011


def test_kg_a_libras() -> None:
    r = run_cli("kg-lb", "1")
    assert r.returncode == 0, r.stderr
    assert abs(float(r.stdout.strip()) - 2.20) < 0.011


def test_salida_dos_decimales() -> None:
    r = run_cli("km-mi", "5")
    out = r.stdout.strip()
    assert "." in out and len(out.split(".")[1]) == 2, f"formato de 2 decimales: {out!r}"


def test_tipo_desconocido_exit_2() -> None:
    # Sin esta guarda, un workspace VACÍO pasaba este test gratis: python
    # devuelve exit 2 + stderr cuando el script no existe.
    assert (WS / "conversor.py").exists(), "no hay entregable"
    r = run_cli("yd-m", "1")
    assert r.returncode == 2
    assert r.stderr.strip(), "debe explicar el error por stderr"


def test_valor_no_numerico_exit_2() -> None:
    assert (WS / "conversor.py").exists(), "no hay entregable"
    r = run_cli("km-mi", "abc")
    assert r.returncode == 2
    assert r.stderr.strip()


def test_funcion_importable() -> None:
    sys.path.insert(0, str(WS))
    try:
        from conversor import convertir  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert abs(convertir("km-mi", 5.0) - 3.106855) < 0.01
    assert abs(convertir("c-f", 0.0) - 32.0) < 0.01
