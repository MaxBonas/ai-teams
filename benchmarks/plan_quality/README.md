# Benchmark de calidad de planes `lead_quorum`

Mide el valor incremental del quorum sobre el mismo caso y la misma ejecución:

1. Plan A producido por el Lead y congelado como `base_plan_revision_id`.
2. Dos auditorías independientes con provider/modelo/run/coste persistidos.
3. Plan B sintetizado y aceptado como `final_plan_revision_id`.
4. Scoring A/B con una rúbrica que los modelos no reciben.

No se compara con `full_team`: `lead_quorum` solo planifica. Tampoco se usa un
LLM como juez único. El scorer determinista mide cobertura verificable y hard
gates; una futura evaluación semántica ciega puede añadirse como señal separada,
no como sustituto.

Casos v1: migración SQLite online, autorización multi-tenant y failover de
proveedores. Cubren datos, seguridad y arquitectura operativa.

Ejemplo de run real (consume tokens):

```powershell
.\scripts\python_local.bat scripts\benchmark_quorum_plans.py `
  --goal benchmarks\plan_quality\goals\sqlite_online_migration.md `
  --rubric benchmarks\plan_quality\sqlite_online_migration.json `
  --output benchmarks\results\quorum-sqlite-seed-1.json
```

El comando falla si el Plan B conserva hard gates incumplidos o regresa frente
al Plan A. Los resultados de varias semillas deben analizarse como distribución;
una sola mejora no valida el quorum.
