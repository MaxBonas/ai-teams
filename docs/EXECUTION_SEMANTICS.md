# Execution Semantics

Status: Contrato activo
Fecha: 2026-05-04
Audiencia: Producto e ingeniería

Este documento define cómo AI Teams interpreta issues, runs, wakeups, interacciones y relaciones padre/hijo. Es la fuente de verdad para decisiones de liveness, recovery y ejecución del control plane.

Inspirado en `paperclip/doc/execution-semantics.md`, adaptado al modelo de equipos de programación Lead-first.

---

## 1. Modelo central

AI Teams separa cuatro conceptos que es fácil confundir:

1. **Estructura**: relaciones padre/hijo entre issues
2. **Dependencia**: blockers explícitos
3. **Ownership**: quién es responsable ahora
4. **Ejecución**: si el control plane tiene un camino vivo para avanzar

El sistema funciona bien cuando esos cuatro están separados.

---

## 2. Semántica de asignación

Una issue tiene como máximo un agente asignado (`assignee_agent_id`).

- Si está asignada a un agente, el control plane puede despertar al agente y rastrear runs.
- Si no tiene asignee, no hay expectativa de ejecución automática.
- El Lead es el agente default para issues de planificación.

---

## 3. Semántica de estados

Los estados no son solo etiquetas UI. Implican expectativas de ownership y ejecución.

### `backlog`

Issue no lista para trabajo activo. Sin expectativa de ejecución. Estado seguro de reposo.

### `todo`

Issue accionable pero no reclamada. Puede estar asignada o no. El control plane encolará un wakeup si está asignada y no tiene camino vivo.

### `in_progress`

Trabajo activamente reclamado.

- Requiere assignee.
- Para agentes: estado respaldado por ejecución. No debe quedarse en silencio.
- Para humanos: estado de ownership humano; el control plane no ejecuta heartbeats.

### `in_review`

Ejecución pausada porque el siguiente movimiento pertenece a un revisor o al usuario.

Una issue sana en `in_review` tiene al menos uno de:
- Pending `request_confirmation` o `ask_user_questions` esperando respuesta explícita.
- Assignee humano con ownership claro.
- Run activo o wakeup encolado del agente que procesará la revisión.

Una issue `in_review` sin ninguno de esos caminos es un estado silencioso: debe superficiarse como recovery.

### `blocked`

No puede avanzar hasta que algo externo cambie.

- Uso correcto: esperando otra issue, una decisión humana, o un sistema externo.
- Una cadena de blockers es sana solo cuando su hoja no resuelta tiene camino vivo.

### `done` / `cancelled`

Terminal. No se espera más ejecución.

---

## 4. Checkout y ejecución activa

Checkout es el puente entre ownership y ejecución activa.

- El agente debe hacer checkout antes de trabajar. El checkout devuelve 409 si otro agente tiene el lock. **Nunca reintentar un 409.**
- `checkout_run_id` = quién tiene derechos de ejecución ahora.
- `execution_run_id` = qué run está vivo actualmente.

---

## 5. Padre/hijo vs. blockers

### Padre/hijo (`parent_id`)

Es estructural. Sirve para:
- Desglose de trabajo.
- Contexto de rollup.
- Despertar al padre cuando todos los hijos terminan.

No es dependencia de ejecución por sí solo.

### Blockers

Es semántica de dependencia: "esta issue no puede continuar hasta que esa cambie de estado."

Si un padre espera a un hijo, modela eso con blockers, no solo con parent_id.

---

## 6. Contrato de liveness para issues no terminales

Para issues asignadas a agentes y no terminales, el control plane no debe dejar trabajo en un estado donde nadie es responsable del siguiente movimiento y nada lo va a despertar.

**Un issue es sana cuando se puede responder "¿qué la mueve?" sin reconstruir intent del thread.**

**Un issue está bloqueada/muerta cuando es no-terminal, no tiene camino vivo, no tiene camino de espera explícito, y no tiene recovery.**

### Primitivas de camino válido

1. Run activo vinculado a la issue.
2. Wakeup encolado o continuación que puede llegar al agente responsable.
3. Pending `issue_thread_interaction` esperando respuesta de un respondente específico.
4. Assignee humano explícito.
5. Cadena de blockers cuya hoja no resuelta es ella misma sana.
6. Issue de recovery explícita que nombra el owner y la acción.

### `todo` asignada a agente

Sana si:
- Tiene wakeup encolado.
- Está en reposo intencional tras heartbeat completado, sin evidencia de dispatch interrumpido.
- Ha sido superficiada como bloqueada con recovery path visible.

### `in_progress` asignada a agente

Sana si:
- Hay run activo.
- Hay wakeup de continuación encolado.
- Hay issue de recovery explícita para el camino de ejecución perdido.

### `in_review`

Sana si alguna primitiva válida está activa (ver §3).

---

## 7. Post-run disposition

Cada heartbeat de agente debe terminar en uno de estos estados para la issue que trabajó:

| Estado de issue | Esperado si… |
|---|---|
| `done` / `cancelled` | El trabajo terminó. Terminal. |
| `in_review` + pending interaction | El agente espera decisión del usuario/Lead. |
| `in_progress` + wakeup encolado | El agente necesita otra run para continuar. |
| `in_progress` + blocker explícito | Espera externa nombrada. |
| `done` en hijos, pending en padre | Padre espera consolidación del Lead. |

No dejar `in_progress` sin ninguno de los anteriores. Si se sale sin camino vivo, el reconciliador de liveness re-encolará el wakeup.

---

## 8. Wake payload compacto (`AITEAM_WAKE_PAYLOAD_JSON`)

Al despertar, el agente recibe un JSON compacto con:

- Resumen de la issue (título, descripción, estado, assignee, rol, complejidad)
- Últimos N comentarios en orden cronológico (los más recientes primero en el payload)
- Comment disparador resaltado si `AITEAM_WAKE_COMMENT_ID` está presente
- Pending interactions
- Plan document (cuerpo si existe, clave `plan`)
- `fallback_fetch_needed`: true si el thread es largo y se necesita fetch completo

**Fast path de wake con scope**: si `AITEAM_TASK_ID` y `AITEAM_WAKE_PAYLOAD_JSON` están presentes, el agente no necesita llamar a `/api/issues` para contexto básico. Ir directo a checkout y trabajo. Solo fetch adicional si `fallback_fetch_needed = true` o se necesita contexto más amplio.

---

## 8.5 Configuracion de adapters de usuario

La configuracion operativa de modelos vive en dos capas:

- `agents.adapter_type` y `agents.adapter_config_json`: seleccion por agente, sin secretos inline.
- Configuracion de usuario local: perfiles y secretos fuera del repo, en `%LOCALAPPDATA%/AI Teams` en Windows o equivalente XDG.

Reglas:

- No guardar API keys en SQLite ni en `runtime/agents.json`.
- Las API keys se guardan mediante `/api/user-adapters/secrets` como refs `secret:provider:name`.
- En Windows, el valor se cifra con DPAPI del usuario local antes de escribirse a disco.
- La UI solo muestra refs y estado `has_secret`; nunca devuelve el secreto.
- Los adapters resuelven `profile_id` al arrancar la run, no al crear el agente. Esto permite cambiar comandos/modelos de usuario sin migrar issues.

Perfiles base:

- `codex_subscription`: usa `codex exec` no interactivo.
- `gemini_subscription`: preparado para CLI de Gemini cuando este instalado.
- `claude_subscription_blocked`: visible, pero marcado como bloqueado por proveedor.
- `openai_api`, `gemini_api`, `anthropic_api`: usan refs de secretos.
- `local_qwen_ollama` y `local_gem4_lmstudio`: usan Codex OSS con proveedor local `ollama` o `lmstudio`; el campo `model` puede cambiarse por agente.

Login de suscripciones:

- El cockpit puede llamar a `/api/user-adapters/login` para abrir una ventana local de login (`codex login`, `gemini auth login`, Claude auth).
- El login no pasa por el navegador: se lanza como proceso local del usuario y las credenciales quedan en el storage propio de cada CLI.
- Si el CLI no esta instalado, el endpoint devuelve `404` y el cockpit lo muestra como no disponible.
- En Windows, el backend no invoca `cmd.exe /k "<exe>" ...` directamente. Escribe un launcher `.cmd` en el config dir del usuario y ejecuta ese script para evitar errores de quoting con rutas `WindowsApps` o espacios.

Seguridad de API keys:

- Introducir una API key en el cockpit es aceptable para uso local: viaja desde el navegador al backend local y se cifra en el vault de usuario.
- No es equivalente a un gestor de secretos corporativo: no usarlo si la API esta expuesta en red, si el navegador tiene extensiones no confiables o si `VITE_API_URL` apunta a una maquina remota.
- El navegador no guarda la API key en `localStorage`; solo mantiene el valor temporalmente en memoria mientras se pulsa Guardar.

### Adapters por proyecto y hiring

Al crear un proyecto nuevo se debe seleccionar al menos un adapter disponible. La seleccion se guarda en `.aiteam/project_config.json` y actua como allowlist del proyecto.

Reglas:

- La pantalla inicial muestra primero conexiones: perfiles ya conectados/probados, API keys guardadas y CLIs disponibles para login.
- El usuario puede conectar mas canales desde esa pantalla antes de crear el proyecto: API key o login de suscripcion.
- El Lead inicial se crea con un adapter elegido desde esa allowlist, preferentemente avanzado.
- El hiring dinamico solo propone perfiles/modelos de la allowlist del proyecto.
- Seniors (`lead`, `reviewer`, `quorum_senior`) reciben modelos avanzados cuando existen.
- Workers (`engineer`, `qa`) reciben modelos baratos o locales cuando existen.
- El usuario puede corregir perfil/modelo en el panel de hiring antes de aceptar.
- No se copian secretos al proyecto; los agentes guardan `profile_id` y `model`, y las keys se resuelven desde el vault de usuario en runtime.

---

## 9. Contrato de heartbeat del agente

Cada agente sigue este procedimiento en cada run:

1. **Checkout**: `POST /api/issues/{id}/checkout`. Nunca reintentar 409.
2. **Contexto**: usar `AITEAM_WAKE_PAYLOAD_JSON` primero. Fetch incremental de comments solo si `fallback_fetch_needed`.
3. **Trabajo**: iniciar trabajo concreto en el mismo heartbeat. No parar en un plan salvo que la issue pida planificación explícita.
4. **Progreso durable**: dejar avance en comments o documentos con acción siguiente antes de salir.
5. **Delegación**: usar issues hijas para trabajo paralelo o largo. No hacer polling de agentes, sesiones o procesos.
6. **Si bloqueado**: mover a `blocked` con owner y acción exacta de desbloqueo. Crear interaction si necesita decisión humana.
7. **Update de estado**: comentar al Lead si el rol reporta a alguien. Incluir `source_run_id` en el comment.

---

## 10. Invariantes que no se negocian

Del modelo Paperclip, portados a AI Teams:

1. **Trabajo productivo continúa.** Agentes con acción clara siguen sin que el usuario tenga que despertarlos manualmente.
2. **Solo blockers reales paran el trabajo.** Estados silenciosos (`in_progress` sin run, `in_review` sin participante) se detectan y enrutan.
3. **Sin loops infinitos.** Recovery y continuation loops son acotados y distinguibles de continuación productiva.
4. **Bajo ruido.** Gates proporcionales al riesgo. No approvals/quorum/reviews pesados para trabajo simple.
5. **Delegación económica.** El Lead no ejecuta trabajo rutinario. Workers baratos para tareas simples.

---

## 11. Recovery

El reconciliador de liveness corre en cada tick del heartbeat loop:

- `reconcile_stale_runs`: marca como `failed` runs con `started_at` antiguo sin `finished_at`.
- `reconcile_unqueued_assigned_issues`: re-encola issues asignadas sin camino vivo (idempotente).
- `diagnose_issue(issue_id)`: devuelve diagnóstico de liveness con paths activos y blockers.

El recovery no marca issues como `done` basándose en prosa de comentarios. Solo superficia el estado como recovery visible.
