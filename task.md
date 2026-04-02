# Estado actual y siguientes pasos

Fecha: `2026-04-02`
Maquina: `MAX-GAMINGPC`
Suite validada: `823 passed`
Playbook de implementacion: `docs/IMPLEMENTATION_PLAYBOOK.md`

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
- [x] URGENTE-2: prefetch de especialistas con retry corto y degradacion graceful para `context_curator`
- [x] Hardening del catalogo de `Routing`: payload versionado, blockers estables, capacidades y capas `defaults` / `override_local` / `effective`
- [x] Persistencia de overrides locales de routing en backend y API
- [x] Fase editable de `Routing`: asignación por rol de providers, modelos, primario/fallbacks, validación previa, persistencia local segura y reset a defaults
- [ ] Extender la futura vista editable para cubrir reglas por tipo de tarea, límites de coste, capacidades mínimas, canales preferidos y simulación de resolución del router
- [x] Definir e implementar `Plan/Quorum`: Lead soberano con consultores avanzados opcionales para cerrar un plan antes de la run productiva
- [x] Tratar los planes como artefactos visibles del proyecto: persistirlos en `docs/aiteam/` o `planning/`, no como estado opaco del runtime
- [x] Dar soporte a `.aiteam/instructions.md` por proyecto como fuente persistente de instrucciones para el equipo, especialmente para el Lead
- [x] Proyectos externos: separar el estado interno del sistema del árbol del producto; dejar de usar `workspace/runtime` como carpeta visible genérica y migrar hacia una carpeta reservada como `.aiteam/`
- [x] Proyectos externos: distinguir en UI tareas `pending` vs `blocked` vs `carried_over`, con motivo operativo visible (`no_eligible_adapter`, quorum, dependencia, etc.)
- [x] Revisar la auditoria específica de `test_aiteams` y convertirla en fixes priorizados: `docs/TEST_AITEAMS_GAME_AUDIT_2026_04_02.md`

## Bloqueantes inmediatos

- [x] **URGENTE-1 RESUELTO**: 2 tests de integracion LCP fallando: `test_chat_force_gate_integration_reopens_completed_phase` y `test_chat_retry_route_integration_retries_target_with_alternate_adapter`. Causa raiz: `lead_report_*` y `lead_preflight_*` checkpoint tasks se bloqueaban con `specialist_quorum_not_met` porque `context_curator_recommended=True` propagado del workflow state activaba `wants_context_curator` y añadia `context_curator` al roster sin MCP disponible. Fix: `skip_specialist_prefetch: True` en metadata de ambos tipos de checkpoints. Suite: `776 passed`. Fecha: `2026-04-02`.
- [x] **URGENTE-2 RESUELTO**: `context_curator/no_eligible_adapter` degradado a best-effort con retry corto en prefetch para no bloquear la tarea padre cuando la elegibilidad es transitoria. Ver `docs/IMPLEMENTATION_PLAYBOOK.md`.

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
- [x] B7a: Hardening del catalogo de routing (payload versionado, capas separadas, blockers con codigos estables)
- [x] B7b: Persistencia de overrides locales de routing (`routing_overrides.py`, endpoints API, validacion)
- [x] B7c: Frontend editable de routing (modo inspeccion/edicion, guardar/reset, validacion visible)
- [x] B8a: Planes persistidos como `.md` en el proyecto (no solo en runtime)
- [x] B8b: `.aiteam/instructions.md` por proyecto leido e inyectado en prompt del Lead
- [x] B8c: Plan/Quorum (Lead + consultor avanzado para consolidar el plan antes de la run productiva)
- [x] B9a: Cambio de raiz runtime a `.aiteam/` para proyectos externos (migracion automatica)
- [x] B9b: Aislamiento de contexto por project_root en context_curator (namespace por proyecto + filenames cortos con hash para evitar path-length en Windows)
- [x] B9c: Visibilidad de artefactos de producto vs estado interno del sistema (backend `product_artifacts` en `last_chat_run` + seccion dedicada en `StatusPanel`)

Orden de ejecucion recomendado y guia tecnica detallada: `docs/IMPLEMENTATION_PLAYBOOK.md`

## Siguiente bloque — audit fixes + Lead adaptativo

### Audit fixes (C-series) — gaps reales de test_aiteams aun abiertos

Los siguientes tres problemas quedaron demostrados por la auditoria forense y no estan cubiertos por B7-B9. Son mas simples que A1-A5 y tienen impacto directo en la experiencia de proyectos externos.

- [x] **C1**: Delegate tasks creadas lazy — no en bulk al planificar. `deferred_evidence_specs` en metadata de fase padre; `_maybe_spawn_deferred_delegates()` las crea al reclamar la fase. Suite: `823 passed`.
- [x] **C2**: `continuation_policy` en `TeamChatRequest` (`auto` / `clean_retry` / `force_continue`). Estado `ARCHIVED` en `TaskState`. `taskboard.archive_incomplete_tasks()`. Suite: `823 passed`.
- [x] **C3**: `_maybe_deposit_minimal_output()` deposita `PROJECT_PLAN.md` en workspace vacio cuando `lead_intake` completo pero `build` no arranco. Suite: `823 passed`.

Guia tecnica: `docs/IMPLEMENTATION_PLAYBOOK.md` seccion C-series (por anadir).

### Lead adaptativo (A-series) — siguiente bloque principal

Prerequisito: URGENTE-1 resuelto (ya). Orden recomendado: A1 → A3 → A2 → A4 → A5.

- [ ] **A1**: RunHealthReport — bloque estructurado inyectado en `lead_close` con gate rejections, routing errors, recursos ausentes y presupuesto consumido
- [ ] **A3**: `[SKIP_PHASE]` y `[DEGRADE]` en lead_close — Lead acepta entrega parcial o salta fases irrecuperables con diagnostico
- [ ] **A2**: `[PAUSE_FOR_USER]` en lead_close — Lead pausa la run y pregunta al usuario cuando el bloqueo no es resolvible internamente; requiere frontend + endpoint `/resume`
- [ ] **A4**: Briefing de capacidades pre-run — Lead sabe que modelos y MCPs tiene disponibles antes de planificar
- [ ] **A5**: Memoria primaria del Lead por proyecto (`lead_memory.md`) — historial de runs, decisiones, capacidades observadas, inyectado antes de `lead_intake`

Diseno completo en `docs/LEAD_ADAPTIVE_FLOW_VISION.md`.

## Riesgos abiertos

- [ ] Queda compatibilidad JSON residual en tests/constructores; ya no gobierna la lectura normal
- [x] `TaskBoard` ya se instancia desde runtime SQLite-first; `tasks.json` queda como snapshot legacy auxiliar
- [ ] La sincronizacion entre `MAX-GAMINGPC` y `ORCH-01` puede reintroducir entornos Python rotos si se sincroniza `venv/`
- [ ] Parte de la documentacion historica sigue presente y puede confundir si se toma como vigente
- [ ] La vista de routing aun no es centro de control completo: falta edición segura, overrides locales, validación previa y simulación explicable del routing
- [ ] La UI de routing todavia puede crecer en simulacion avanzada e historial, pero el MVP editable seguro ya está operativo
