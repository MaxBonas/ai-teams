# Índice y autoridad documental

Actualizado: `2026-07-23`

Este índice distingue contratos activos e historial. Objetivos, pendientes y
orden de ejecución viven juntos en `../task.md`; no existen planes paralelos.

## Fuentes de verdad activas

| Documento | Uso |
|---|---|
| `MIGRATION_PAPERCLIP.md` | Plan rector de reconstruccion Paperclip-like sobre SQLite. |
| `INSTALLATION_AND_INTEGRATION.md` | Instalación, traslado entre máquinas, soporte de plataformas y protocolo de integración para personas/IA. |
| `PAPERCLIP_GUIDE.md` | Guia practica para consultar Paperclip y adaptar sus patrones sin perder identidad AI Teams. |
| `EXECUTION_SEMANTICS.md` | Contrato de issues, runs, wakeups, interactions, relaciones padre/hijo y liveness. |
| `ORCHESTRATION.md` | Fuente canónica de routing, delegación, verificación, contexto, liveness y economía multi-LLM. |
| `ORCHESTRATION_SOURCES.md` | Registro fechado de fuentes primarias, calidad, cifras y limitaciones. |
| `MODELOS_GRATUITOS_OPENCODE.md` | Evaluación, tiers, gobierno y contrato de integración de OpenCode Zen Free. |
| `RUN_PROBLEMS_REGISTRY.md` | Registro operativo de fallos observados y mitigaciones aplicadas. |
| `FRONTEND_ORIENTATION_STUDY.md` | Protocolo humano prerregistrado para Bandeja, perfiles y plan aceptado → tarea. |
| `PROMPT_HANDOFF_QUORUM_PROFUNDO.md` | Prompt reutilizable para continuar el contrato Lead-owned de quorum profundo. |
| `../task.md` | Backlog vivo y estado de fases. |
| `../HANDOFF.md` | Punto de entrada para continuar una sesion. |
| `../AGENTS.md` | Instrucciones para agentes de desarrollo. |

## Historial consolidado

No usar estos archivos para determinar el estado actual ni reabrir trabajo sin
contrastarlos con `../task.md`, código y tests.

| Documento | Uso histórico |
|---|---|
| `HISTORY.md` | Decisiones, diseños, migraciones y planes cerrados consolidados. |
| `legacy_rescue/README.md` | Índice de snapshots legacy rescatados como referencia. |

## Regla

Si una decisión no está en las fuentes activas, en el código o en tests activos,
tratarla como no vigente. Ante conflicto, prevalecen `AGENTS.md`, `task.md` y
`HANDOFF.md`, en ese orden. Los snapshots de `legacy_rescue/source_snapshots/`
solo sirven para portar ideas a v2 con tests nuevos.

La limpieza documental es mantenimiento continuo. Toda reconciliación material
debe registrarse en `../task.md`; no se mantiene un segundo backlog dentro de este
índice.
