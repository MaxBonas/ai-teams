# CLI conversor — primera medición

Fecha: `2026-07-16`

Resultado fuente: `2026-07-16-seed-1.json`.

## Configuración

- Mismo goal público y misma suite oculta para ambos brazos.
- Equipo: perfil único `codex_subscription`, composición `full_team` creada por AI Teams.
- Solo: una ejecución `codex exec`, modelo `gpt-5.4`.
- Canal flat-rate; `cost_cents=0` no significa consumo nulo, por eso se comparan tokens.

## Resultado

| Métrica | Full team | Codex solo | Relación team/solo |
|---|---:|---:|---:|
| Tests ocultos | 9/9 | 9/9 | misma calidad observable |
| Tiempo | 924,6 s | 242,3 s | 3,82× |
| Input tokens | 1.091.201 | 329.782 | 3,31× |
| Output tokens | 24.960 | 4.557 | 5,48× |
| Runs | 24 | 1 | 24× |
| Ruff issues | 1 | 1 | igual |

## Lectura

En este caso pequeño, `full_team` no aporta mejora de aceptación y sí añade un coste grande de coordinación. El resultado refuta cualquier hipótesis general de que el equipo completo sea mejor por defecto. No permite concluir que nunca aporte valor en tareas complejas.

La auditoría de la DB atribuye la mayor parte del input al Engineer: 750.890 tokens en cuatro runs. También aparecen nueve runs del Lead, seis del `test_runner`, varias rondas `lead_directive`/`child_report` y dos continuaciones sobre issues terminales.

## Limitación descubierta

La versión usada del harness llamaba `HeartbeatLoop.run_once()`, que drena toda la cola. Por eso `max_ticks=20` y `max_minutes=12` no limitaron las 24 runs: el brazo tardó 924,6 s dentro de un solo tick.

Después de registrar este resultado, el harness se cambió a despacho acotado `HeartbeatScheduler → RunExecutor`, una run por iteración. Las próximas semillas deben declarar `harness_version=2` y no agregarse estadísticamente con esta como si fueran idénticas; esta semilla se conserva como baseline exploratorio de producción-loop.

## Decisión

No gastar dos repeticiones adicionales con el harness v1. Antes de repetir:

1. verificar el cap real de runs/tiempo con v2;
2. añadir evals SQL de coordinación;
3. investigar la repetición Engineer/Lead/Test Runner;
4. comparar después `solo`, `solo+review` y equipo reducido antes de otro `full_team`.
