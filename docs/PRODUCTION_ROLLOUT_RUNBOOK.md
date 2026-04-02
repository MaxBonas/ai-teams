# Production Rollout Runbook (v0)

Objetivo: habilitar despliegue progresivo del AI Team con control de riesgo.

## Fase 0 - Preparacion

- Validar test suite local verde.
- Verificar `runtime/adapters.json` con herramientas sensibles en `enabled=false` por defecto.
- Confirmar presupuesto FinOps diario/mensual.
- Generar/actualizar inventario de herramientas: `inventory-tools`.
- Revisar catalogo MCP/CLI/Skills: `tool-catalog`.
- Sincronizar herramientas requeridas en stage: `tool-sync`.
- Perfil recomendado de arranque: `config/tool_requests.pro.json`.
- Ejecutar `mcp-doctor --enable-healthy` (sin `--enable-sensitive`) antes de pasar a prod.
- Ejecutar `system-check --strict` y guardar reporte `runtime/system_check.json`.
- Crear snapshot de recuperación antes de cambios mayores (`snapshot-create`).

## Fase 1 - Stage controlado

- Entorno: `--environment stage`.
- Carga inicial: 1 proyecto interno, sin acciones irreversibles.
- Generar dashboard operativo: `python -m aiteam.cli dashboard --runtime-dir runtime_stage --environment stage --dashboard-output runtime_stage/dashboard.html`.
- Monitorear KPIs:
  - `task_execution_success_rate`
  - `compliance_violations`
  - `% fallback API`

Salida de fase:

- 3 corridas consecutivas sin incidentes de cumplimiento.
- pass rate de gates >= 85%.

## Fase 2 - Produccion limitada

- Entorno: `--environment prod`.
- Requisito: operaciones sensibles con doble aprobacion (`approved_by` >= 2).
- Limitar a 1 equipo/pipeline por vez.

Salida de fase:

- MTTR de bloqueos en tendencia estable.
- Sin ejecuciones no aprobadas de comandos sensibles.

## Fase 3 - Escalado progresivo

- Incremento semanal de proyectos/tareas bajo supervisión.
- Revisar tuning de ruteo Pro/API cada semana.
- Activar herramientas externas secundarias una por sprint.

## Respuesta a incidentes

1. Pausar nuevas tareas (`run` detenido).
2. Revisar `events.jsonl`, `mailbox.jsonl`, `aiteam.db` y, solo si hace falta compatibilidad legacy, `tasks.json`.
3. Identificar tipo:
   - compliance
   - calidad
   - infraestructura/tooling
4. Aplicar rollback operativo:
   - deshabilitar adapter conflictivo (`enabled=false`)
   - reintentar tareas fallidas con nueva politica

## Checklist de rollback rapido

- [ ] Desactivar adapters externos riesgosos.
- [ ] Reducir `max_api_attempts` temporalmente.
- [ ] Forzar perfil `stage` para nuevas corridas.
- [ ] Ejecutar regression suite antes de reactivar `prod`.

## Baseline de piloto ejecutado

Fecha: 2026-02-20 (runtime `runtime_stage`, entorno `stage`).

Comandos ejecutados:

- `python -m aiteam.cli init --runtime-dir runtime_stage`
- `python -m aiteam.cli adapters --runtime-dir runtime_stage`
- `python -m aiteam.cli demo --runtime-dir runtime_stage --environment stage`
- task QA adicional (`EXT-001`) con capacidad `browser_testing` para validar adapter externo secundario.
- `python -m aiteam.cli status --runtime-dir runtime_stage --environment stage`
- `python -m aiteam.cli pilot-check --runtime-dir runtime_stage --environment stage`

Resultados:

- Tareas completadas: 11/11 (incluyendo gates `review`, `qa`, `security`).
- `task_execution_success_rate`: 100.0%.
- `channels`: `subscription=10`, `api=1` (Pro-first funcionando).
- `providers`: `openai=9`, `anthropic=1`, `custom=1`.
- `compliance_violations`: 0.
- FinOps diario: `$0.003144` / `$10.0`.

Observacion:

- El adapter secundario `android_browser_auditor` fue utilizado con exito en `EXT-001`.

## Baseline MCP/Skills (stage)

Fecha: 2026-02-20 (runtime `runtime_stage_mcp`, entorno `stage`).

Comandos ejecutados:

- `python -m aiteam.cli init --runtime-dir runtime_stage_mcp`
- `python -m aiteam.cli tool-sync --runtime-dir runtime_stage_mcp --tool-request-file config/tool_requests.pro.json --allow-internet`
- `python -m aiteam.cli mcp-doctor --runtime-dir runtime_stage_mcp`
- `python -m aiteam.cli demo --runtime-dir runtime_stage_mcp --environment stage`
- `python -m aiteam.cli skills-coverage --runtime-dir runtime_stage_mcp`
- `python -m aiteam.cli pilot-check --runtime-dir runtime_stage_mcp --environment stage`

Resultados:

- `pilot-check`: pass (100% success / 100% gates / 0 compliance violations).
- `skills-coverage`: 100% (10 guidance events / 10 task_execution).
- MCP health: 2/4 healthy (`context7_mcp`, `github_mcp`), 2 auto-disabled por acquisition fallida.

## Trial inicial en prod (controlado)

Fecha: 2026-02-20 (runtime `runtime_prod`, entorno `prod`).

Acciones ejecutadas:

- `python -m aiteam.cli demo --runtime-dir runtime_prod --environment prod`
- Tarea sensible `PROD-APPROVAL-1` con:
  - `approved_sensitive_ops=true`
  - `approved_by=["lead-1", "security-1"]`
- `python -m aiteam.cli pilot-check --runtime-dir runtime_prod --environment prod`

Resultado del gate:

- `task_success_rate=100.0%`
- `gate_pass_rate=100.0%`
- `pro_share=90.91%`
- `compliance_violations=0`

Conclusiones:

- La doble aprobacion en `prod` funciona y permite operacion sensible controlada.
- El gate `pilot-check` queda validado como criterio de salida para triales.

## Validacion Playwright (entorno stage)

Fecha: 2026-02-20 (runtime `runtime_playwright`, `--browser-mode playwright`).

Resultados:

- `browser_script` ejecutado en demo (`T-004`) con `success=true`.
- Flujo avanzado validado (`BROWSER-ADV-1`) con acciones de assertion + screenshot.
- Evidencia generada: `runtime_playwright/evidence/example-domain.png`.
