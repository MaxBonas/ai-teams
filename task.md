# Estado actual y siguientes pasos

Fecha: `2026-04-02`
Maquina: `MAX-GAMINGPC`
Suite validada: `763 passed`

## Cerrado

- [x] SQLite como persistencia principal para `tasks` y `workflow_state`
- [x] API/UI leyendo SQLite como fuente normal; JSON fuera del camino normal
- [x] Evidence gate corregido para no aceptar diffs ajenos en modo simulado
- [x] Persistencia granular sin overwrite global entre procesos
- [x] Docu base alineada con el estado real validado en gamingpc
- [x] Bootstrap local de `venv/` para reducir roturas entre `MAX-GAMINGPC` y `ORCH-01`
- [x] `runtime/` tratado como estado local por maquina; las plantillas compartidas quedan en `config/*.example.json`
- [x] Flujo corto de reanudacion tras pull: `.\scripts\prepare_dev_env.bat`
- [x] Planning como modo de primera clase: evidence gate de planning + `run_mode` `architecture_review` y `roadmap`
- [x] `mode: "probe"` en chat: ejecuta solo `lead_intake` y devuelve plan sin lanzar fases
- [x] Refactor razonable de `api/main.py`: extraidos `api/chat_*.py`; no seguir troceando salvo necesidad funcional
- [x] La API expone `peer_consultation_summary` en `chat` y `chat/progress` para hacer visible qué roles/proveedores participaron realmente en la deliberación
- [x] Chat y `StatusPanel` muestran peers consultados, providers y diversidad observada de la deliberación
- [x] `FileLockRegistry` endurecido para Windows con retry/backoff al persistir `file_locks.json`; el flake de `replan` en `MAX-GAMINGPC` deja de reproducirse en repetición
- [x] Chat conserva historial de agentes al acabar la run, muestra `provider/model` por agente y expone `Tareas creadas`
- [x] Placeholder gate endurecido: ya no bloquea por la palabra genérica `placeholder`, solo por marcadores claros
- [x] Anthropic restringido en defaults: `claude_pro` y `claude_haiku` quedan solo para `team_lead`
- [x] Nueva vista consultable `Routing` en `StatusPanel` para ver catálogo, primario/fallbacks por rol y blockers del router
- [ ] Robustizar la vista `Routing`: payload estable, blockers exhaustivos, separación explícita entre defaults, override local y estado efectivo
- [ ] Fase editable de `Routing`: asignación por rol de providers, modelos, primario/fallbacks, validación previa, persistencia local segura y reset a defaults
- [ ] Extender la futura vista editable para cubrir reglas por tipo de tarea, límites de coste, capacidades mínimas, canales preferidos y simulación de resolución del router

## Siguiente prioridad tecnica

- [x] Cablear `select_specialists_for_task()` en el orchestrator (`E10-W1`)
- [x] Hacer efectivo el quorum de especialistas / evidence (`E10-W2`)
- [x] Mejorar health-check y auto-repair de MCPs (`E10-W6`)
- [x] Subir cobertura E2E multiagente de la arquitectura de especialistas (`E10-W9`)
- [x] Eliminados los lectores JSON del camino normal de API/UI
- [ ] Limpieza, unificacion y criba de documentacion interna: dejar taxonomia clara de docs activas, referencia e historicas
- [ ] Vigilar durante unos dias que el bootstrap nuevo absorbe bien pulls y cambios entre maquinas
- [x] Revisados artefactos machine-specific fuera de `runtime/`; snapshots locales y logs de frontend ya no viajan por Git
- [x] `B5` completado: `AITEAM_SIM_MODE` es el nombre canónico; `AITEAM_CHAT_DEMO_FAST` queda como fallback de transición
- [ ] Siguiente fase de producto: convertir la vista `Routing` en una vista editable por rol, con primario/fallbacks persistidos como override local seguro

## Riesgos abiertos

- [ ] Queda compatibilidad JSON residual en tests/constructores; ya no gobierna la lectura normal
- [x] `TaskBoard` ya se instancia desde runtime SQLite-first; `tasks.json` queda como snapshot legacy auxiliar
- [ ] La sincronizacion entre `MAX-GAMINGPC` y `ORCH-01` puede reintroducir entornos Python rotos si se sincroniza `venv/`
- [ ] Parte de la documentacion historica sigue presente y puede confundir si se toma como vigente
- [ ] La vista de routing aun no es centro de control completo: falta edición segura, overrides locales, validación previa y simulación explicable del routing
