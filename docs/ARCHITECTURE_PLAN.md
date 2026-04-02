# Plan de arquitectura — AI Team Hybrid Orchestrator

Fecha: `2026-04-01` — actualizado `2026-04-02`
Estado del sistema: `763 passed`

**Contexto de uso**: el sistema se usa tanto para desarrollarse a sí mismo (dogfooding) como para proyectos externos de programación y planificación. Ese doble uso determina el orden de prioridades.

Este documento recoge las fricciones arquitectónicas reales del sistema y un plan de acción priorizado.
No es backlog de features. Es un mapa de riesgo técnico con orden de ejecución justificado.

---

## Estado actual del sistema

### Lo que funciona bien y no tocar

- Flujo de orquestación: `lead_intake → dynamic phases → lead_close`
- `TaskBoard` con SQLite como backend; JSON fuera del camino normal
- Quality gates y evidence gates en `api/chat_quality.py` y `aiteam/orchestrator.py`
- Router pro-first con fallback a API
- FinOps, compliance y memory como módulos cohesionados
- Bootstrap de dos máquinas con `scripts/prepare_dev_env.bat`
- Suite de tests: `pytest` como fuente de verdad

### Lo que se acaba de limpiar (2026-04-01)

Se eliminaron del codebase:
- `_is_game_request`, `_is_game_followup_request`, `_materialize_game_iteration`: bootstrap hardcodeado de juego de demo que inyectaba archivos en el workspace antes de que el orquestador actuara.
- `AITEAM_CHAT_DEMO_FAST` en la capa de endpoint/quality: bypasseaba evidence gates y cambiaba etiquetas de output.
- `demo_fast_chat_active` en `ChatPolicyInput`: campo de política que eliminaba penalización de productividad.
- `require_followup_artifact_delta` en `_evaluate_phase_evidence_gate`: gate específico del flujo de juego.

Lo que se mantuvo intencionalmente:
- `AITEAM_SIM_MODE` en los adapters (`aiteam/adapters/api.py`, `aiteam/adapters/subscription.py`) y en el orchestrator: es el mecanismo legítimo de modo simulado que genera respuestas `[DEMO]` cuando no hay API keys. `AITEAM_CHAT_DEMO_FAST` queda solo como fallback de transición.

---

## Fricciones reales — ordenadas por impacto

### F1 — `aiteam/orchestrator.py` (5137 líneas): frontera de mantenimiento

**Problema**: El orchestrator es el punto donde convergen demasiados comportamientos:
- Gestión de blackboard (`workflow_state`)
- Ciclo de ejecución de tareas y retry
- Evaluación de evidencia (`_verify_task_evidence`)
- Quality assessment de output LLM (`_assess_output_quality`)
- Construcción de contextos de gate (`_build_gate_evidence_context`)
- Comunicación con el lead (`_run_lead_task`, `_run_specialist_task`)
- Compliance y guardrails

**Impacto**: añadir cualquier feature nueva (nuevo tipo de gate, nuevo flujo de retry, nuevo rol) requiere modificar un archivo de 5137 líneas con alta interdependencia interna. El riesgo de regresión es alto y difícil de acotar.

**Dirección de resolución (sin hacer ahora)**:
Extraer `_verify_task_evidence` + `_assess_output_quality` + `_build_gate_evidence_context` a `aiteam/evidence_gate.py`. Son ~300 líneas cohesionadas que no dependen del estado interno del orchestrator más allá de `task` y `workspace`. Esto reduce el orchestrator a ~4800 líneas y da un módulo testable de forma aislada.

**Cuándo hacerlo**: cuando necesites tocar el evidence gate por otra razón. No como refactor proactivo.

---

### F2 — `aiteam/cli.py` (3061 líneas): lógica de negocio en capa CLI

**Problema**: `cli.py` contiene lógica de orquestación, setup y bootstrapping que no pertenece a la capa de comandos. Dificulta reutilizar esa lógica desde el API sin pasar por el CLI.

**Impacto**: bajo en el desarrollo diario, pero si se quiere añadir un endpoint nuevo que haga lo que hace un comando CLI, hay que duplicar o importar desde CLI (antipatrón).

**Dirección de resolución**:
Extraer las funciones de setup/bootstrap a `aiteam/project.py` o `aiteam/setup.py`. El CLI queda como thin wrapper de llamadas a ese módulo.

**Cuándo hacerlo**: cuando se añada un nuevo endpoint que necesite lógica actualmente en `cli.py`.

---

### F3 — `api/main.py` (3446 líneas): capas mezcladas

**Problema**: `api/main.py` tiene tres capas mezcladas con distinto nivel de abstracción:
1. State machine del streaming SSE — necesariamente en el endpoint, difícil de mover
2. Helpers genéricos (`_workspace_artifact_snapshot`, `_workspace_artifact_diff`) — podrían vivir en `api/utils.py`
3. Lógica de negocio del chat (pero ya mayoritariamente extraída a `api/chat_*.py`)

**Impacto**: bajo. El refactor ya está en un punto razonable. El riesgo de seguir extrayendo supera el beneficio.

**Dirección de resolución**:
Mover `_workspace_artifact_snapshot` y `_workspace_artifact_diff` a `api/utils.py`. Son 40 líneas independientes del estado de la request. El `SimplePTY` y `_build_resume_stream` se quedan donde están.

**Cuándo hacerlo**: solo si se necesita usar `_workspace_artifact_snapshot` desde otro módulo. No como refactor proactivo.

---

### F4 — Modo simulado: claridad conceptual ✅ resuelto

**Problema original**: el sistema tenia tres conceptos distintos que usaban la misma variable de entorno (`AITEAM_CHAT_DEMO_FAST`) con propósitos distintos:
1. Adapters generan respuestas `[DEMO]` (fake responses para dev sin API keys) — legítimo
2. Orchestrator: bypass del check `require_execution_plan` en modo demo — aceptable
3. Endpoint/quality: bypass de evidence gates y cambio de etiquetas — **esto se eliminó**

El concepto 1 y 2 ya se consolidaron bajo `AITEAM_SIM_MODE`, que deja claro que es el modo de desarrollo sin LLM real, no un "modo demo de presentación".

**Impacto**: confusión conceptual al leer el código. No afecta el comportamiento.

**Resolución aplicada**:
`AITEAM_SIM_MODE` es ya el nombre canónico en adapters y orchestrator. `AITEAM_CHAT_DEMO_FAST` se sigue leyendo como fallback de transición para no romper entornos o tests viejos.

---

### F5 — `SqliteStore` instanciado en cada `_save_workflow_state` del orchestrator

**Problema**: el orchestrator crea un `SqliteStore` nuevo en cada `_load_workflow_state` y `_save_workflow_state`, corriendo `_init_db()` (3 `CREATE TABLE IF NOT EXISTS`) en cada llamada. El `TaskBoard` usa `self._store` persistente, que es el patrón correcto.

**Impacto**: overhead de I/O en runs largos. No es un bug.

**Dirección de resolución**:
Añadir `self._sqlite_store: SqliteStore | None = None` al orchestrator y un método `_get_sqlite_store()` que lo inicialice en lazy fashion.

**Cuándo hacerlo**: si se detecta latencia en runs largos con muchas fases. No antes.

---

---

## Plan de trabajo priorizando uso real

### B1 — Extraer evidence gate a `aiteam/evidence_gate.py` ✅ COMPLETADO

**Por qué ahora**: si se usa el sistema para desarrollar este mismo repo, el agente Engineer
va a editar `orchestrator.py` (5137 líneas). El evidence gate (~460 líneas en líneas 3160-3620)
es la zona que más se va a modificar al añadir capacidades. Extraerla la hace editable de
forma aislada y directamente testeable.

**Qué se movió**:
- `_verify_task_evidence`, `_assess_output_quality`, `_build_gate_evidence_context`, `_summarize_git_diff`, `_detect_conversational_task` + constantes
- `aiteam/evidence_gate.py` con 35 tests directos en `tests/test_evidence_gate.py`
- `aiteam/orchestrator.py` delega a las funciones importadas — bajó ~430 líneas

---

### B2 — Planning como modo de primera clase

**Estado**: ✅ completado `2026-04-01`

**Por qué**: `planning_only` y `team_decision` ya existen pero el evidence gate para estas
corridas cae en el camino "conversational" que solo valida longitud del output (>400 chars).
Para planning serio (arquitectura, roadmaps, decisiones técnicas), eso no es suficiente.

**Dos partes**:

**B2a — Evidence gate de planning**:
Añadir validación específica: ¿se creó al menos un `.md` estructurado en el workspace del
proyecto? ¿El output del lead_close tiene secciones reconocibles? Distinto al gate de build
(que verifica git diff) y al gate conversacional (que verifica longitud).

**B2b — Nuevos modos de run_mode para el Lead**:
- `architecture_review`: discovery + análisis de opciones + documento ADR
- `roadmap`: priorización de features + estimación de complejidad + secuencia recomendada
Se activan igual que ahora: el Lead emite `[RUN_MODE: architecture_review]` en su output.

**Cuándo**: después de B1. Hacer antes del primer uso de planificación en proyecto externo.

---

### B3 — Modo `probe` (dry-run de lead_intake)

**Estado**: ✅ completado `2026-04-01`

**Por qué**: cuando se empieza con un proyecto externo nuevo, no hay forma económica de
verificar que el sistema tiene el contexto correcto antes de lanzar un run completo.

**Qué hace**: un parámetro `mode: "probe"` en `TeamChatRequest` ejecuta solo `lead_intake`
con el modelo más barato, no extiende rounds, no aplica evidence gate, y devuelve el plan
de fases que el Lead habría generado sin ejecutarlo.

**Cuándo**: cuando haya un proyecto externo real que probear. No antes.

---

### B4 — Visibilidad de decisiones autónomas en el frontend ✅ COMPLETADO

**Por qué**: el Lead puede extender rounds, replanificar fases, activar advisory mode.
Esos eventos existen en el SSE y el operator timeline pero no están destacados visualmente.
En dogfooding esto importa: si el sistema extiende 15 rounds por su cuenta, quieres saberlo.

**Qué se añade**: bloque en StatusPanel con decisiones autónomas en tiempo real:
- Rounds extendidos (cuántos y por qué ronda)
- Fases replanificadas
- Advisory mode activo
- Waiting for user

Todos estos datos ya están en los eventos. Es trabajo de frontend puro.

**Qué se añadió**:
- `_latest_chat_run_summary` en `api/routers/aiteam.py` extrae `advisory_mode`, `advisory_reason`, `auto_extended_rounds`, `lead_budget_extended`, `lead_budget_extension` de los eventos
- `StatusPanel.tsx` muestra bloque "Lead — decisiones autónomas" cuando hay datos
- Estilos en `team.css`: `.status-lead-decisions`, `.status-lead-decision--warn/--info`

---

### B5 — Renombrar `AITEAM_CHAT_DEMO_FAST` → `AITEAM_SIM_MODE` ✅ COMPLETADO

**Qué quedó**:
- `AITEAM_SIM_MODE` como nombre canónico del modo simulado
- fallback a `AITEAM_CHAT_DEMO_FAST` durante la transición
- cobertura para adapters y orchestrator

---

### B6 — Vista consultable del routing multimodelo ✅ COMPLETADO

**Por qué**: ajustar coste, soberanía del Lead y asignación por rol era demasiado opaco.
Hasta ahora había que leer policy, model catalog, adapters efectivos y `provider_ops` por separado.

**Qué se añadió**:

- endpoint `GET /api/aiteam/routing/catalog`
- pestaña `Routing` en `StatusPanel`
- catálogo consultable con:
  - providers
  - adapters
  - primario efectivo por rol
  - fallbacks efectivos por rol
  - blockers (`role_targets`, `team_lead_guard`, `adapter_unavailable`, etc.)
  - separación entre **configurado** y **efectivo**

**Decisión importante**:
se hizo primero como vista de lectura. La fase editable queda para después, cuando haya un modelo de persistencia local seguro para overrides por rol.

### B7 — Vista completa y editable de configuración de routing

**Estado**: pendiente

**Por qué**: el MVP consultable ya resuelve opacidad, pero todavía no da gobierno real.
Si el objetivo es controlar coste, calidad, soberanía del Lead y reparto por rol sin tocar JSON a mano,
la siguiente fase debe convertir `Routing` en una superficie operativa completa.

**Objetivo de producto**:
que un operador pueda ver y editar, en un solo lugar:

- providers por rol
- modelos por rol
- primario y fallbacks
- canales preferidos
- restricciones por coste
- capacidades mínimas
- estado efectivo en esta máquina
- overrides locales seguros

**Alcance recomendado**:

**B7a — Hardening del catálogo actual**:
- payload versionado y estable
- separación explícita entre defaults, override local y efectivo
- blockers exhaustivos y explicables
- exposición de `channel`, `tier`, `cost_class`, `tool_support`, `stream_support`, etc.

**B7b — Edición segura por rol**:
- reordenar providers por rol
- reordenar modelos por rol
- definir primario y fallbacks
- guardar override local
- reset a defaults

**B7c — Validación y simulación**:
- impedir guardar una policy inválida
- preview de diff
- simulador de resolución: "si lanzo Engineer ahora, qué ruta elegiría y por qué"

**B7d — Extensión por tipo de tarea**:
- distinguir `lead_intake`, `planning`, `engineering`, `review`, `qa`, `delegate`, `probe`
- permitir routing distinto aunque el rol sea el mismo

**Condición importante**:
la vista editable no debe escribir sobre `aiteam/config.py`.
Debe operar sobre overrides locales seguros y reversibles.

---

### Orden de ejecución

```
B1 (evidence_gate.py)      ✅ completado
    +
B4 (frontend decisiones)   ✅ completado — TypeScript clean
    ↓
B2 (planning first-class)  ✅ completado — evidence gate planning + run_modes nuevos
    ↓
B3 (probe mode)            ✅ completado — lead_intake only, sin evidence gate
    ↓
E10-W1 / E10-W2 / E10-W6 / E10-W9   ← W1/W2/W6/W9 completados; siguiente prioridad real: cerrar deuda legacy, continuidad entre maquinas y limpieza documental
    ↓
B5 (renombre sim mode)     ✅ completado con retrocompatibilidad
    ↓
B6 (routing consultable)   ✅ completado — catálogo, primario/fallbacks y blockers visibles
    ↓
B7 (routing editable)      ← siguiente fase natural del control multimodelo
```

---

## Principios de parada

**No refactorizar si**:
- El cambio no tiene un test que falle o un bug que lo justifique
- El módulo a tocar no necesita modificarse por otra razón
- La extracción produce un módulo de <50 líneas que solo se usa en un lugar

**Sí refactorizar si**:
- Se va a añadir lógica nueva a un módulo que ya está en el límite de legibilidad
- Un bug requiere modificar código en una zona que sería más segura si estuviera separada
- Un test nuevo requiere importar desde un módulo que actualmente está demasiado acoplado

---

## Próxima acción recomendada

**E10-W1**: completado. El orchestrator ya aplica `specialist_roster_preferred_tool_tier`
al `RoutingRequest` principal, cerrando el wiring entre roster, prefetch y routing.

**E10-W2**: completado. El quorum ya no cuenta respuestas debiles como si fueran
evidencia valida: solo cuentan informes `valid` con señal operativa minima.

**E10-W6**: completado. `MCPServerManager` ya tenia auto-repair y retry de salud,
pero el orchestrator seguia pidiendo `list_healthy(retry_unhealthy=False)` antes
de calcular el roster. Ahora usa `list_healthy()` y el retry vuelve efectiva la
seleccion de `mcp_operator` cuando hay `external_mcp`.

**E10-W9**: completado. La cobertura E2E multiagente ya valida delegacion,
quorum, `REPLAN` parcial preservando fases iniciadas y `FORCE_GATE`, con
8 tests activos en `tests/test_e2e_multiagent.py`.

**B5** ya queda cerrado: `AITEAM_SIM_MODE` manda y `AITEAM_CHAT_DEMO_FAST`
solo queda como fallback de transición.
