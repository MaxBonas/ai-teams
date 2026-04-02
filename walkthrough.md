# Walkthrough tecnico — estabilizacion 2026-04-02

Maquina validada: `MAX-GAMINGPC`
Estado validado actual: `763 passed`

## Que se cerro en esta fase

### 1. Migracion operativa a SQLite

- `aiteam/sqlite_store.py` centraliza la persistencia de `tasks` y `workflow_state`.
- `aiteam/taskboard.py` dejo de depender de snapshots JSON como fuente primaria.
- `api/main.py`, `api/utils.py` y `api/routers/aiteam.py` leen ya solo `runtime/aiteam.db` en el camino normal.
- `aiteam/orchestrator.py` ya crea `TaskBoard` desde `runtime/aiteam.db` y reutiliza un `SqliteStore` persistente para `workflow_state`.
- Los JSON legacy quedan solo como compatibilidad residual de tests/constructores, no como fallback normal de la API.

### 2. Fix del evidence engine

- `_verify_task_evidence()` en `aiteam/orchestrator.py` ahora clasifica primero salida simulada y modo no-live.
- Un `git diff` o `git status` ajeno ya no puede validar una ejecucion mock.
- Se recuperaron los tests de orquestador que fallaban por esta regresion.

### 3. Fin de lost updates entre procesos

- La persistencia de tareas paso de overwrite por snapshot completo a upserts y deletes granulares.
- `workflow_state` ahora se persiste por `task_root`.
- Dos procesos ya no deberian pisarse el estado entre si al guardar cambios independientes.

### 4. Estabilizacion de tests y compatibilidad

- Se mantuvo snapshot JSON legacy para tests y consumidores no migrados.
- `tests/test_api_aiteam_state.py` cubre lectura SQLite desde API/UI.
- `tests/test_parallel_taskboard.py` cubre concurrencia real sobre `aiteam.db`.
- `tests/test_finops_anomaly.py` se ajusto para no romper en cambio de mes.

### 5. Planning y probe como modos de primera clase

- `aiteam/evidence_gate.py` ahora exige Markdown estructurado en el workspace para corridas de planning (`planning_only`, `architecture_review`, `roadmap`).
- `aiteam/lead_control.py` ya soporta `architecture_review` y `roadmap` como `run_mode` validos del Lead.
- `api/main.py` ya soporta `mode: "probe"` para ejecutar solo `lead_intake` y devolver `planned_phases` sin lanzar fases dinamicas.

### 6. E10-W1 cerrado: roster de especialistas conectado al routing real

- `aiteam/orchestrator.py` ya usa `specialist_roster_applied.specialist_roster_preferred_tool_tier` al construir el `RoutingRequest` principal.
- El roster deja de ser solo observabilidad/prefetch y pasa a gobernar tambien el tier economico de la tarea principal.
- `tests/test_orchestrator.py` cubre que el tier del roster tiene prioridad sobre `tool_specialist_default_tier`.

### 7. E10-W2 cerrado: quorum cuenta solo informes con señal minima

- `aiteam/orchestrator.py` ya no cuenta cualquier respuesta parseada como quorum satisfecho.
- Un informe de especialista debe ser `valid` y aportar señal operativa minima: evidencia/artefactos/riesgos/recomendacion o un resumen sustancial.
- `specialist_quorum_result` ahora expone tambien `invalid_specialists`.
- `tests/test_orchestrator.py` cubre el caso de un especialista que responde pero no aporta evidencia util y, por tanto, no satisface quorum.

### 8. Peer consultation visible en la superficie API

- El orquestador ya persiste `consulted_providers` y `peer_diversity_observed` junto con la justificación de decisión.
- `api/main.py` y `api/chat_observability.py` ya exponen `peer_consultation_summary` tanto en `TeamChatResponse` como en `chat/progress`.
- La deliberación entre pares deja de ser una caja negra: ahora la UI/API pueden ver qué roles y qué familias de proveedor participaron realmente.

### 9. Peer consultation visible tambien en la UI

- `ide-frontend/src/components/TeamChat.tsx` ya muestra peers consultados, providers y diversidad observada en la barra de progreso y en el inspector de decisión.
- `ide-frontend/src/components/StatusPanel.tsx` ya refleja ese mismo resumen para la última corrida, sin depender de deducciones a partir del texto libre.
- `api/routers/aiteam.py` ahora inyecta `peer_consultation_summary` en `last_chat_run`, reutilizando el mismo resumen SQLite-first que usa el flujo principal del chat.

### 10. Hardening de `file_locks.json` en Windows

- `aiteam/runtime.py` ahora hace `fsync` y retry/backoff corto al reemplazar `runtime/file_locks.json`.
- El flake de `test_chat_replan_integration_rebuilds_pending_plan` no venía del flujo de `replan`, sino de un `[WinError 5]` al persistir locks temporales dentro del repo en `MAX-GAMINGPC`.
- Tras el hardening, el test de `replan` pasa 10/10 veces en repetición sobre el mismo entorno local.

### 11. Visibilidad real de la run en el chat

- `TeamChat` ya no limpia las lanes al terminar una run.
- El chat ahora rehidrata `chat/progress` al cargar una conversación previa y muestra:
  - historial de agentes
  - `provider/model` por agente
  - tareas creadas por la run (`phase`, `scout`, `delegate`)
- Esto cierra el gap principal de producto detectado en `test_aiteams`: la run ya no desaparece visualmente al acabar.

### 12. Placeholder gate endurecido con prudencia

- El placeholder gate del orchestrator se manteniene, pero deja de bloquear por la palabra genérica `placeholder`.
- Ahora solo bloquea marcadores claros (`TODO`, `FIXME`, `insert code here`, `placeholder text`, etc.).
- La motivación fue un fallo real en `lead_intake` de una run legítima, donde el gate había tumbado la corrida antes de que el sistema pudiera ejecutar sus fases.

### 13. Anthropic restringido al Team Lead por defecto

- `aiteam/config.py` ya no prioriza Anthropic para `researcher`, `engineer`, `reviewer` ni `qa`.
- `aiteam/cli.py` ahora limita `claude_pro` y `claude_haiku` a `role_targets={"team_lead"}`.
- El objetivo no era prohibir Anthropic en abstracto, sino dejar de usarlo como opción normal para roles donde había alternativas suficientes más baratas.

### 14. Nueva vista consultable de routing

- `api/routers/aiteam.py` expone `/api/aiteam/routing/catalog`.
- `StatusPanel` añade pestaña `Routing`, con:
  - providers registrados
  - adapters registrados
  - primario/fallbacks por rol
  - blockers por adapter
  - diferencia entre configurado y efectivo
- Esta vista se hizo primero como lectura para dejar visible el estado real del router antes de abrir una fase editable.
- Queda explícitamente como `MVP consultable`, no como solución final de gobierno del routing.
- La siguiente fase no es "maquillaje de UI", sino convertirla en una vista muy completa y editable que permita:
  - asignar providers por rol
  - asignar modelos por rol
  - definir primario y fallbacks
  - persistir overrides locales seguros
  - validar antes de guardar
  - distinguir defaults del repo, override local y estado efectivo en esta máquina
  - simular por qué el router resolvería una ruta concreta

### 15. Funciones recientes que conviene robustizar antes de abrir la edición

- `/api/aiteam/routing/catalog` necesita un contrato más estable para soportar edición futura sin romper frontend.
- `RoutingCatalogPanel` aún es principalmente diagnóstico; necesita filtros, drilldown y comparación entre configuración y uso real.
- La nueva visibilidad de historial de agentes y `task_summaries` ya es útil, pero aún conviene reforzar su relación con el catálogo de routing para poder responder "qué se configuró" frente a "qué se usó".
- El placeholder gate ya quedó endurecido con prudencia, pero sigue siendo una heurística que debe mantenerse acotada a marcadores claros y no volver a crecer como detector bruto de lenguaje.
- La restricción de Anthropic al `team_lead` ya está aplicada por defecto, pero la futura vista editable debe preservar ese control como política visible y modificable con validación.

### 16. Gap detectado en proyectos externos: `runtime/` visible y mezcla de estado del sistema

La investigación sobre `test_aiteams` confirmó un problema de producto distinto al routing:

- el proyecto externo no contiene todavía archivos de producto fuera de `runtime/`
- el sistema sí crea tareas y delegaciones
- pero si la run se bloquea pronto, lo único visible en la raíz del proyecto es el estado interno del sistema

Además, el runtime local del proyecto externo hoy agrupa:

- SQLite
- eventos
- mailbox
- memoria de agentes
- sesiones
- sandboxes
- contexto curado

todo dentro de `workspace/runtime/`.

Esto es funcionalmente cómodo para el sistema, pero malo para la UX del usuario del proyecto externo.

La dirección correcta documentada queda así:

- dejar de tratar `runtime/` como carpeta visible genérica del proyecto externo
- migrar hacia una carpeta reservada del sistema, preferiblemente `.aiteam/`
- separar con claridad archivos del producto frente a estado interno del orquestador
- impedir que el store local del proyecto externo mezcle contexto de otros roots del sistema

Documento de referencia de esta investigación:

- `docs/EXTERNAL_PROJECT_RUNTIME_GAPS.md`

## Estado del entorno en gamingpc

- La suite completa valida en esta maquina: `763 passed`.
- Se anadio `scripts/ensure_local_venv.ps1` para validar o recrear `venv/` automaticamente.
- Se anadio `scripts/ensure_local_runtime.ps1` para rehidratar `runtime/` local desde plantillas compartidas.
- `scripts/ensure_local_runtime.ps1` ahora refresca plantillas compartidas sin pisar overrides locales detectados.
- Se anadieron `scripts/python_local.bat` y `scripts/pytest_local.bat` como wrappers estables para Python y tests.
- Se anadio `scripts/prepare_dev_env.bat` como comando rapido de `post-pull`.
- `start_ide.bat` ya intenta reparar el `venv` antes de arrancar servicios.

## Regla de trabajo entre maquinas

- Git comparte codigo y plantillas.
- Cada maquina mantiene su propio `runtime/`, `venv/` y `node_modules/`.
- Despues de cambiar de maquina: `git pull` y luego `.\scripts\prepare_dev_env.bat`.

## Deuda viva

- `api/main.py` sigue siendo un archivo grande, pero ya no es la prioridad principal; el siguiente frente real vuelve a ser cerrar deuda legacy JSON, vigilar continuidad entre maquinas y limpiar/unificar documentacion interna.
- `E10-W6` ya queda cerrado: el orchestrator usa `mcp_manager.list_healthy()` con retry habilitado antes de calcular el roster, y eso vuelve efectiva la seleccion de `mcp_operator` cuando hay capacidad `external_mcp`.
- `E10-W9` ya queda cerrado: `tests/test_e2e_multiagent.py` cubre delegacion, quorum, replan y force_gate con 8 tests activos y sin `skip`.
- Quedan documentos historicos que deben leerse como referencia, no como fuente de verdad.
- Aun existe compatibilidad JSON residual en tests/constructores; la lectura normal de la API ya no depende de ella.
- La vista `Routing` ya resuelve opacidad, pero todavía no resuelve gobierno completo: falta edición segura por rol y por tipo de tarea.
