# Investigación — gaps de runtime y visibilidad en proyectos externos

Fecha: `2026-04-02`
Proyecto investigado: `C:\Users\she__\Documents\Antigravity Projects\test_aiteams`
Objetivo investigado: pedir al sistema que cree un videojuego original entre agentes

## Resumen ejecutivo

La investigación confirma tres problemas reales de producto:

1. el sistema sí crea tareas, pero la UI todavía no explica con suficiente claridad cuáles están bloqueadas por diseño y cuáles están bloqueadas por fallo operativo
2. la corrida analizada no avanzó a creación de archivos de producto porque quedó bloqueada muy pronto en `plan_research`, y luego fue clasificada como `simulated` por no producir pasos de ejecución ni artefactos
3. el `runtime/` de un proyecto externo está guardando demasiado estado operativo del sistema y además puede mezclar contexto de otros roots dentro del mismo `runtime/`

Esto no es solo deuda estética. Es una fricción real para usar AI Teams en proyectos externos.

## Evidencia observada en `test_aiteams`

### 1. No se crearon archivos de producto fuera de `runtime/`

En el árbol del proyecto investigado:

- `C:\Users\she__\Documents\Antigravity Projects\test_aiteams`

solo existe:

- `runtime/`

No aparecieron archivos de juego, assets, HTML, JS, docs ni prototipos fuera de ese directorio.

Conclusión:

- la corrida no llegó a ejecutar trabajo de producto real sobre el workspace

### 2. Las tareas sí se crean

En `mailbox.jsonl` y `events.jsonl` del proyecto aparecen tareas creadas como:

- `lead_intake`
- `plan_research`
- `build`
- `review`
- `qa`
- `lead_close`
- múltiples delegadas `delegate_*`

Conclusión:

- el problema no es "no crea tareas"
- el problema es que después muchas quedan pendientes o bloqueadas, y eso no se explica bien en la UI

### 3. Causa funcional inmediata del bloqueo en esta run

La corrida `CHAT-28015BB0` avanza así:

1. completa los scouts iniciales
2. completa `lead_intake`
3. crea el nuevo plan y sus tareas
4. intenta empezar `plan_research`
5. falla el prefetch del especialista `context_curator`
6. el quorum no se cumple
7. el resto del flujo queda bloqueado

Eventos relevantes observados:

- `specialist_prefetch_failed`
  - `task_id=CHAT-28015BB0::plan_research`
  - `specialist=context_curator`
  - `reason=no_eligible_adapter`
- `specialist_quorum_result`
  - `quorum_met=false`
  - `missing_specialists=["context_curator"]`
- `chat_execution_mode_assessed`
  - `execution_mode=simulated`
  - `execution_steps=0`
  - `artifact_created=0`
  - `artifact_modified=0`
- `chat_policy_signal`
  - `signal=evidence_gate_failed`
  - `failures=["build:not_completed", "review:not_completed", "qa:not_completed"]`

Conclusión:

- las tareas pendientes no son principalmente "inútiles"
- en esta corrida son mayormente consecuencia de un bloqueo temprano real
- el sistema crea un plan grande, pero al quedarse sin ruta elegible para `context_curator` en `plan_research`, ya no llega a producir trabajo de build real

## Qué significan hoy muchas tareas pendientes

En proyectos externos nuevos hay dos fuentes de acumulación:

### A. Delegación estructural del plan

El Lead crea no solo fases principales, sino tareas de evidencia/delegación:

- `delegate_plan_research_repo_scout_0`
- `delegate_build_test_runner_0`
- `delegate_build_lsp_navigator_2`
- `delegate_qa_repo_scout_1`
- etc.

Eso es intencional.

### B. Continuaciones de chats anteriores

En `CHAT-28015BB0` además se heredó trabajo pendiente de `CHAT-1F789CCB`.

El propio evento `chat_plan_created` incluye:

- `continuation_requested=true`
- `continuation_of=CHAT-1F789CCB`
- `continuation_snapshot=...pending...`

Conclusión:

- el número bruto de tareas pendientes no basta para saber si la corrida está sana
- la UI debería distinguir mejor:
  - pendiente normal
  - pendiente heredada
  - bloqueada por dependencia
  - bloqueada por error operativo

## Problema de diseño de `runtime/` en proyectos externos

### Comportamiento actual

Hoy el backend usa:

- `workspace / "runtime"`

como base local del sistema para ese workspace.

Eso hace que en un proyecto externo aparezcan dentro de su árbol:

- `aiteam.db`
- `events.jsonl`
- `mailbox.jsonl`
- `file_locks.json`
- `mcp_servers.json`
- `memory/`
- `sessions/`
- `sandboxes/`
- `context/`

todo dentro de:

- `C:\Users\she__\Documents\Antigravity Projects\test_aiteams\runtime`

### Por qué esto es un problema de UX y arquitectura

Para el usuario, `test_aiteams` es el proyecto del videojuego.

Pero hoy su raíz contiene también:

- estado operativo del orquestador
- sesiones internas de agentes
- sandboxes de ejecución
- contexto curado del sistema
- mailbox y eventos
- configuración runtime del sistema

Eso tiene varios efectos malos:

1. ensucia visualmente el proyecto
2. hacía difícil distinguir "archivos del producto" de "estado interno del sistema"
3. transmitía la impresión de que el proyecto no avanza, porque lo único visible eran archivos del sistema
4. complica backup, versionado y limpieza del proyecto externo

## Contaminación cruzada de contexto

Dentro de:

- `test_aiteams/runtime/context/projects/`

aparecen dos contextos de proyecto:

- `C_Users_she___Documents_Antigravity_Projects_test_aiteams.json`
- `C_Users_she___Documents_Antigravity_Projects_Ai_Teams.json`

Es decir:

- el runtime del proyecto externo está guardando también contexto del repo del propio sistema `Ai_Teams`

La frontera conceptual correcta debería ser:

- el runtime del proyecto externo guarda solo estado de ese proyecto
- el runtime del sistema guarda contexto del propio sistema

## Qué debería pasar a largo plazo

### Objetivo funcional

El usuario debe poder abrir un proyecto externo y ver dos zonas claramente separadas:

1. archivos del proyecto en sí
2. estado interno de AI Teams

### Dirección recomendada

La dirección más razonable es mover el estado interno visible a una carpeta claramente reconocible del sistema,
en vez de usar `runtime/` genérico en la raíz del proyecto.

Propuesta de diseño:

- usar `.aiteam/` o `_aiteam/` como carpeta reservada del sistema dentro del proyecto

Ejemplo:

- `test_aiteams/.aiteam/`
  - `aiteam.db`
  - `events.jsonl`
  - `mailbox.jsonl`
  - `memory/`
  - `sessions/`
  - `sandboxes/`
  - `context/`
  - `provider_ops.json`
  - `mcp_servers.json`

Y dejar la raíz del proyecto para:

- `index.html`
- `src/`
- `assets/`
- `docs/`
- etc.

### Recomendación pragmática

Primero resolver así:

- cambiar `workspace / "runtime"` por `workspace / ".aiteam"`
- mantener el estado local por proyecto, pero en una carpeta claramente separada
- migrar gradualmente lectores de UI/API a esa nueva ruta

## Gaps concretos a cerrar

### G1 — Explicabilidad de tareas pendientes

La UI debería mostrar por tarea:

- `pending`
- `blocked_by_dependency`
- `blocked_by_quorum`
- `blocked_by_no_eligible_adapter`
- `carried_over_from_previous_run`

No basta con un contador bruto.

### G2 — Superficie clara para artefactos de producto

Estado actual: resuelto en B9c.

La UI ya puede responder:

- qué archivos de producto se crearon
- qué archivos de producto se modificaron
- si no hubo artefactos de producto, decirlo de forma explícita

Queda como posible mejora futura enriquecer la trazabilidad por fase, pero la separación básica producto/runtime ya no es deuda abierta.

### G3 — Separación física del estado del sistema

Estado actual: resuelto en B9a.

El runtime del proyecto externo ya va a una carpeta reservada del sistema:

- preferiblemente `.aiteam/`

### G4 — Aislamiento de contexto por root

El contexto curado de un proyecto externo no debería guardar entradas de otro root dentro del mismo store local del proyecto.

## Conclusión

La corrida investigada no estaba "quieta".

Lo que ocurrió fue:

- sí creó plan y tareas
- sí hizo scouts y `lead_intake`
- pero se bloqueó pronto en `plan_research`
- no llegó a ejecutar pasos reales de build
- no produjo artefactos de producto
- y en aquel momento el único rastro visible en el proyecto era el `runtime/` del sistema

Ese caso confirmó una deuda de producto importante, hoy ya parcialmente cerrada:

- AI Teams ya separa el runtime externo en `.aiteam/`
- AI Teams ya muestra `product_artifacts` por separado en `last_chat_run` y `StatusPanel`
- la deuda viva pasa a ser explicar mejor tareas pendientes/bloqueadas/heredadas

La siguiente mejora estructural correcta no es solo más UI:

- es hacer que las tareas pendientes expliquen claramente si están esperando, bloqueadas o heredadas
- y aterrizar esa semántica en el caso real de `test_aiteams`
