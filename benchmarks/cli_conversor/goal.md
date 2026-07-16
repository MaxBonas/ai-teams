# Benchmark: CLI conversor de unidades

Construye en Python 3.12 (solo librería estándar) una CLI `conversor.py` en la
raíz del workspace que convierta unidades.

## Interfaz exacta (la evaluación depende de ella)

- Uso: `python conversor.py <tipo> <valor>`
- Tipos soportados: `km-mi`, `mi-km`, `c-f`, `f-c`, `kg-lb`, `lb-kg`
- Salida en éxito: SOLO el número convertido con 2 decimales, por stdout
  (ejemplo: `python conversor.py km-mi 5` imprime `3.11`), exit code 0.
- Entrada inválida (tipo desconocido o valor no numérico): mensaje por stderr
  y exit code 2.
- Además debe existir una función importable `convertir(tipo: str, valor: float) -> float`
  en `conversor.py` que devuelva el valor convertido (sin redondear).

## Entregables

1. `conversor.py` con la interfaz de arriba.
2. Suite pytest en `tests/` que cubra cada conversión y los casos de error,
   ejecutada en verde (exit 0 real) por un test_runner.
3. `README.md` con un ejemplo de cada comando.

Alcance deliberadamente acotado: nada fuera de esta lista.
