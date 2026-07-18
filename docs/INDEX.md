# Indice de documentacion viva

Actualizado: `2026-07-18`

Este repo fue limpiado de documentacion legacy. El pasado queda en Git y las piezas antiguas con valor quedan aisladas en `legacy_rescue/`.

## Fuentes de verdad

| Documento | Uso |
|---|---|
| `MIGRATION_PAPERCLIP.md` | Plan rector de reconstruccion Paperclip-like sobre SQLite. |
| `PAPERCLIP_GUIDE.md` | Guia practica para consultar Paperclip y adaptar sus patrones sin perder identidad AI Teams. |
| `ORCHESTRATION.md` | Fuente canónica de routing, delegación, verificación, contexto, liveness y economía multi-LLM. |
| `ORCHESTRATION_SOURCES.md` | Registro fechado de fuentes primarias, calidad, cifras y limitaciones. |
| `PROMPT_HANDOFF_QUORUM_PROFUNDO.md` | Prompt reutilizable para continuar el contrato Lead-owned de quorum profundo. |
| `../task.md` | Backlog vivo y estado de fases. |
| `../HANDOFF.md` | Punto de entrada para continuar una sesion. |
| `../AGENTS.md` | Instrucciones para agentes de desarrollo. |
| `HISTORY.md` | Historial condensado, no backlog. |
| `legacy_rescue/README.md` | Indice de piezas legacy rescatadas como referencia, no fuente viva. |

## Regla

Si una decision no esta en estos documentos, en el codigo o en tests activos, tratarla como no vigente. Los snapshots de `legacy_rescue/source_snapshots/` solo sirven para portar ideas a v2 con tests nuevos.

La limpieza documental pendiente está registrada en `../task.md`: debe reconciliar fuentes vivas, skills y prompts activos, validar enlaces y retirar referencias operativas obsoletas sin convertir snapshots históricos en autoridad.
