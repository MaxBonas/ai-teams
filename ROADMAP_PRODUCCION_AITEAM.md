# Roadmap Produccion AI Teams

> Documento historico / supersedido. No usar como backlog activo.
> Para rollout y estado vigente: `docs/PRODUCTION_ROLLOUT_RUNBOOK.md`, `task.md`, `docs/TASKS_2026_03_28.md`.

Este documento guarda el roadmap completo (41 tareas) para llevar el flujo de AI Teams a produccion real, con evidencia verificable y sin salidas simuladas disfrazadas.

## Estado actual (iterativo)

- Enfoque: avanzar seguro, iteracion por iteracion.
- Iteracion activa: enforcement de evidencia real + trazabilidad de modo (`simulated`/`hybrid`/`live`).
- Completado en esta iteracion:
  - Exponer modo de ejecucion en API/UI.
  - Correlacionar `routing_decision` con `task_id`.
  - Evidence gate inicial para build/review/qa.

## Principios de Go-Live

1. No hay `completed` de produccion sin evidencia real.
2. Placeholders no cuentan como resultado valido.
3. Todo evento debe ser auditable por `task_id` y `run`.
4. UI debe mostrar claramente si el run fue simulado o real.

## Backlog maestro (41 tareas)

### A. Runtime y modo de ejecucion

- [ ] R01 Definir modos de ejecucion (`simulated`/`live`/`hybrid`) y contrato por modo.
- [ ] R02 Agregar bandera global para forzar `live` en prod y bloquear `simulated`.
- [ ] R03 Implementar healthcheck de adapters live con diagnostico por proveedor.
- [x] R04 Exponer en API/UI el modo efectivo por corrida y por tarea.
- [x] R05 Marcar/contar outputs placeholder y ratio por corrida.

### B. Evidencia y gates de calidad

- [ ] R06 Bloquear cierre `completed` sin evidencia real en implementacion.
- [ ] R07 Definir evidencia minima por fase (`build`/`review`/`qa`) con reglas verificables.
- [x] R08 Rechazar outputs placeholder como resultado final valido.
- [x] R09 Requerir `execution_plan` en build y mapearlo a `execution_step` obligatorio.
- [ ] R10 Validar calidad de `execution_plan` (comandos seguros/workdir/timeout).

### C. Observabilidad y correlacion

- [x] R11 Incluir `task_id` en `routing_decision` y correlacion completa por run.
- [ ] R12 Medir duracion real por fase y tiempo total por run.
- [ ] R13 Registrar diff de archivos por fase (creados/modificados/eliminados).
- [ ] R14 Persistir resumen de evidencia por run (steps/comandos/archivos/pruebas/fallos).
- [ ] R15 Versionar eventos y validar esquema de ingestion.

### D. UX operativa del IDE

- [x] R16 Mostrar badge prominente `SIMULATED`/`LIVE` en chat/overview/timeline/conversation.
- [ ] R17 Mostrar barra de evidencia real (steps, archivos, pruebas, errores).
- [ ] R18 Mejorar timeline operador con filtros por fase/severidad/evidencia/modo.
- [ ] R19 Mostrar mensajes de rechazo accionables cuando falle el evidence gate.
- [ ] R20 Mostrar diferencia bootstrap vs build real en la UI.

### E. Flujo de juego (caso critico actual)

- [x] R21 Separar bootstrap inicial de juego y limitar bootstrap a primera corrida.
- [ ] R22 Exigir en iteraciones de juego cambios provenientes de build (no solo bootstrap).
- [ ] R23 Definir slices de iteracion del juego (gameplay/UX/balance/QA).
- [ ] R24 Validar checks minimos del juego por iteracion (arranque, loop, controles, game over).
- [ ] R25 Bloquear continuidad si no hay evidencia real en la iteracion anterior.

### F. Ejecucion real y herramientas

- [ ] R26 Integrar retries inteligentes + handoff con trazabilidad de causa.
- [ ] R27 Fortalecer integracion MCP/skills con verificacion de disponibilidad y fallback.
- [ ] R28 Agregar cache/backoff para proveedores live (cuotas/timeouts).
- [ ] R29 Reforzar command policy para produccion (lista blanca estricta por paso).
- [ ] R30 Endurecer sandbox/workdir para evitar escapes.

### G. Seguridad y compliance

- [ ] R31 Politica de secretos y redaccion uniforme en logs/mailbox/memory/events.
- [ ] R32 Auditoria de comandos bloqueados y razones por corrida.
- [ ] R33 Aprobaciones explicitas para herramientas sensibles.
- [ ] R34 Reporte de cumplimiento por run (security/compliance summary).
- [ ] R35 Runbook de incidentes de seguridad operativa.

### H. Pruebas y confiabilidad

- [ ] R36 Suite E2E con adapters fake/live y datasets de regresion.
- [ ] R37 Pruebas de contrato API para progress/timeline/state.
- [ ] R38 Pruebas UI para modo, evidencia y estados de paneles.
- [ ] R39 Pruebas de rendimiento (latencia p50/p95, throughput, costo por run).
- [ ] R40 Definir SLO/SLA operativos y alertas (degradacion/sin evidencia).
- [ ] R41 Checklist de hardening final + criterio de Go-Live y sign-off.

## Plan por iteraciones (resumen)

### Iteracion 1 (actual)

- Modo de ejecucion visible y auditable.
- Correlacion de routing con `task_id`.
- Evidence gate inicial para bloquear corridas sin evidencia valida.

### Iteracion 2

- Requerir `execution_plan` real para build.
- Requerir `execution_step` minimo para cerrar build.
- Badge global `SIMULATED/LIVE` en todo el IDE.

### Iteracion 3

- Post-build checks reales (`tests`/`lint`/`build`) y scoring con evidencia.
- Timeline operador enriquecido (fase, evidencia, fallos).

### Iteracion 4

- Endurecimiento de seguridad/compliance + runbooks.
- Suite E2E y pruebas de performance.

### Iteracion 5

- Staging/canary/prod rollout con SLO/SLA y alertas.
- Go-Live checklist y sign-off tecnico.

## Definicion de Done (por iteracion)

- Backend compilado sin errores.
- Tests de regresion clave pasando.
- Frontend build correcto.
- Evidencia en runtime verificable y legible para usuario.
