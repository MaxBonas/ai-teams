# Run Problems Registry

Registro de problemas detectados en runs reales del proyecto. Cada entrada documenta el run ID (si aplica), síntomas, causa raíz, solución aplicada, y estado.

---

## Formato de entrada

```
### RUN-<id> · <estado>
**Detectado:** <fecha>  
**Run ID(s):** `<id>`  
**Proyecto:** <proyecto>  
**Síntomas:** <qué se observó>  
**Causa raíz:** <por qué falló>  
**Fix aplicado:** <qué se cambió>  
**Verificación:** <tests / evidencia del fix>
```

---

## Problemas abiertos

### RUN-015 · ABIERTO — OpenCode SDK rechaza JSON Schema con texto válido

**Detectado:** 2026-07-22
**Run ID(s):** `opencode-server-sdk-resilience-v1-deepseek.json`
**Proyecto:** canario de resiliencia OpenCode server/SDK
**Síntomas:** tras un aborto real y recuperación correcta, DeepSeek devuelve
`{"status":"completed","marker":"OPENCODE_SDK_RECOVERY_OK"}`, pero la respuesta
del servidor incluye `StructuredOutputError: Model did not produce structured
output`, `retries=0` y no expone un resultado estructurado aceptado.
**Causa raíz:** no determinada, pero ya no queda acotada a DeepSeek. El mismo
schema falla en DeepSeek, Laguna, MiMo, Nemotron y North bajo OpenCode/SDK
1.18.4. Algunos modelos producen el objeto textual válido de forma no estable;
ninguno rellena `info.structured`. La frontera común es Zen/OpenCode, no el
parser de AI Teams.
**Mitigación vigente:** mantener `serve`/SDK fuera de producción y no convertir
el texto válido en éxito cuando el proveedor declara error estructurado. El CLI
efímero continúa como transporte estable.
**Verificación:** el canario inicial observa `busy`, aborto, recovery y cleanup.
`opencode-session-isolation-v1.json` compara los cinco modelos y confirma 0/5
schemas aceptados; en paralelo pasa 3/3 semillas de override/aislamiento con
seis sesiones únicas. Repetir solo tras cambio de versión o contrato proveedor.

### RUN-012 · ABIERTO — OpenCode Zen Free no alcanza estabilidad durable de reviewer

**Detectado:** 2026-07-21
**Run ID(s):** recibos `opencode-durable-review-v1-*.json`
**Proyecto:** canario aislado de calibración P0.2
**Síntomas:** DeepSeek completa reject→fix→approve en seed 1 pero no aprueba la
corrección en seeds 2–3; Nemotron produce `subscription_cli_parse_error`; MiMo no
materializa un rechazo durable. North fue denegado correctamente antes de
inferencia porque reviewer no pertenece a sus roles autorizados. Laguna termina
0/3: dos parse errors y un timeout al aprobar después de un rechazo correcto.
**Causa raíz:** variabilidad de contrato/calidad por modelo gratuito. No existe
evidencia de un fallo común del scheduler: las runs y wakeups terminan sin
claims residuales, y el mismo transporte pasa el screening corto.
**Mitigación vigente:** ningún modelo se promociona ni amplía routing automático.
Laguna queda visible/manual y `requires_probe`; DeepSeek, Nemotron y MiMo siguen
manual-only para reviewer.
**Verificación pendiente:** repetir solo después de un cambio de modelo,
versión del CLI o transporte server/SDK; comparar de nuevo contra Flash High.
La sonda diagnóstica Nemotron seed 101 confirma el nuevo recibo: conserva una
llamada, 25.633 tokens y excerpts/resultados aunque el gate durable falle.
La matriz Laguna vs DeepSeek completa seis muestras y conserva
`default_change_allowed=false`.

---

---

## Problemas resueltos

> Nota de vigencia (`2026-07-21`): las entradas de mayo que describen los
> adapters API como estructuralmente incapaces de escribir pertenecen al
> runtime anterior. Los adapters API actuales emiten `file_ops` estructurados y
> `RunExecutor` los materializa antes de medir el delta, bajo RBAC. Sus lecciones
> de liveness siguen siendo válidas: un rol de implementación que no produce
> cambios no puede cerrar solo con texto. OpenCode Zen sí continúa read-only.

### RUN-017 · RESUELTO — El canario no podía probar un modelo nuevo fuera del catálogo declarado

**Detectado:** 2026-07-22
**Run ID(s):** primeros intentos Laguna seeds 1–3
**Proyecto:** calibración durable OpenCode Zen
**Síntomas:** `record_model_health(..., available=True)` no evitaba
`model_not_catalogued`; las tres runs fallaban antes de inferencia.
**Causa raíz:** el health solo verifica opciones ya declaradas. El preflight
fail-closed actuaba correctamente, pero Laguna aún vivía solo en discovery/docs.
**Fix aplicado:** declarar Laguna visible con `automatic=false` y
`requires_probe=true`, roles provisionales de review/QA y datos no
confidenciales. No se cambió el default ni se amplió hiring automático.
**Verificación:** el test exige `catalogued/selectable=false` antes del probe y
`verified/selectable=true` después; las tres repeticiones reales alcanzan el
provider y producen recibos de calidad/liveness, no fallos de preflight.

### RUN-016 · RESUELTO — Endpoint experimental omitía tools MCP conectadas

**Detectado:** 2026-07-22
**Run ID(s):** `opencode-server-faults-v1-deepseek.json`
**Proyecto:** canario de health MCP OpenCode server
**Síntomas:** `/mcp` devolvía `connected`, el proceso fixture estaba vivo y la
configuración contenía la allowlist exacta, pero `/experimental/tool/ids` y
`/experimental/tool` solo enumeraban built-ins. El primer juez marcó health MCP
como fallido al esperar `canary_health_read` en esos endpoints.
**Causa raíz:** supuesto incorrecto del harness sobre la proyección de tools en
OpenCode 1.18.4. Esos endpoints no demostraron el inventario MCP observado y no
pueden usarse como health de la integración.
**Fix aplicado:** el fixture durable registra el intercambio stdio real. El
gate exige `initialize`, `tools/list`, ambos nombres declarados, `/mcp=connected`,
wildcard deny, allow exacto y ausencia de allow para la tool no aprobada.
**Verificación:** el canario final pasa todos los gates y confirma reap del
proceso MCP y de `opencode.exe serve`; no quedan procesos residuales.

### RUN-014 · RESUELTO — Teardown de OpenCode server terminaba el shim, no el hijo

**Detectado:** 2026-07-21
**Run ID(s):** `opencode-transport-ab-v1-deepseek.json`
**Proyecto:** canario de transporte OpenCode
**Síntomas:** el harness declaraba teardown correcto, pero quedaban dos
`opencode.exe serve` escuchando en loopback después de terminar sus procesos
`.cmd` padres.
**Causa raíz:** `Popen(opencode.cmd)` controlaba el shim de Windows; terminarlo
no terminaba el binario nativo hijo.
**Fix aplicado:** resolver y lanzar directamente
`node_modules/opencode-ai/bin/opencode.exe`, terminar ese PID en `finally` y
verificar que el puerto quede cerrado antes de aceptar el gate. Los dos procesos
residuales creados por los canarios fueron identificados por ruta+command line y
terminados; OpenCode Desktop no se tocó.
**Verificación:** A/B repetido 3×2, `server_teardown_guaranteed=true` y cero
procesos npm `opencode.exe serve` tras finalizar.

### RUN-013 · RESUELTO — El probe de PID de pytest podía terminar suites vivas en Windows

**Detectado:** 2026-07-21
**Run ID(s):** auditoría concurrente local
**Proyecto:** suite de desarrollo de AI Teams
**Síntomas:** suites paralelas cerraban abruptamente su stdout; un árbol stale
bloqueado podía abortar `pytest_configure`; todas las suites compartían además
`.pytest-user-config-tmp`.
**Causa raíz:** `os.kill(pid, 0)` se usaba como probe POSIX. En Windows,
`os.kill` delega señales no CTRL a `TerminateProcess`, por lo que comprobar una
sesión viva podía finalizarla. La eliminación stale propagaba `PermissionError`
y el user config no estaba particionado por sesión.
**Fix aplicado:** probe no destructivo mediante
`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess`;
workspace y user config usan el mismo `session-<pid>-<uuid>`; sesiones vivas se
omiten y locks stale se conservan con warning. El cleanup externo procesa cada
root por sesiones y el wrapper estable deja de usar `include_live=True`.
**Verificación:** cinco tests específicos, wrappers normal/estable y dos suites
concurrentes reales (`9 passed` y `5 passed`) con exit code cero.

### RUN-011 · RESUELTO — OpenCode consumía el mensaje como segundo `--file`

**Detectado:** 2026-07-21
**Run ID(s):** screening OpenCode Zen Free
**Proyecto:** canario aislado de calibración P0.2
**Síntomas:** el CLI terminaba antes de inferencia con `File not found: Follow
the attached AI Teams contract...`. Tras corregir el orden, los modelos podían
responder pero omitían intermitentemente claves top-level del contrato.
**Causa raíz:** `opencode run --file` acepta varios valores y absorbía el
argumento posicional situado después. El relay inicial tampoco repetía las tres
claves obligatorias del objeto `submit_work`.
**Fix aplicado:** colocar primero el mensaje y después `--file`; exigir en el
relay exactamente `status`, `summary` y `ops`, sin Markdown. El parser continúa
fail-closed y no repara respuestas inválidas.
**Verificación:** screening real válido con Nemotron, DeepSeek, MiMo, North y
Laguna; `tests/test_subscription_cli_adapter.py` protege orden, ausencia de
`--auto`, archivo temporal y policy read-only.

### RUN-001 · RESUELTO — Agente API-only escapa detección por evitar verbos de implementación

**Detectado:** 2026-05-04  
**Run ID(s):** `4885c3dd` y similares  
**Proyecto:** Test Proyecto AI Teams / capa 2  
**Síntomas:**  
- El agente OpenAI API produjo output del estilo "Entrega de prototipo con archivos README.md e index.html." sin usar verbos que coincidieran con la regex `_IMPLEMENTATION_CLAIM_RE`.
- La run terminó sin bloquear la issue, sin generar workspace evidence, y sin continuación.
- El issue quedó en estado ambiguo — el agente afirmó haber entregado pero no hay cambios en el workspace.

**Causa raíz:**  
La función `_evaluate_workspace_evidence` en `executor.py` usaba una regex de verbos de implementación (`_IMPLEMENTATION_CLAIM_RE`) como una de tres condiciones OR para activar la verificación de evidencia. Cualquier salida que evitara esos verbos (usando "entrega", "prototipo", nombres de archivos directos, etc.) escapaba el filtro por completo, incluso si el adapter era API-only y estructuralmente incapaz de escribir archivos.

**Fix aplicado (2026-05-04):**  
- Eliminada `_IMPLEMENTATION_CLAIM_RE` y todo uso de regex para decisiones de finalización.
- Creado `aiteam/run_liveness.py` con:
  - `RunEvidence` — taxonomía estructurada de evidencia (comments, doc revisions, activity events, tool grants, workspace files)
  - `classify_run_liveness()` — clasificador puro sin regex
  - `collect_run_evidence()` — colector de evidencia que lee DB post-run
- Refactorizado `executor.py`:
  - Flujo: write comment → apply actions → collect evidence → classify → apply liveness overrides
  - Adapters API-only + rol engineer + sin workspace changes → **blocked** inmediato (independientemente del texto de output)
  - Continuaciones solo para `plan_only` / `empty_response`, máximo 2 intentos
- Actualizado `tests/test_run_executor.py`: 3 tests ajustados para nuevas assertions
- Creado `tests/test_run_liveness.py`: 51 nuevos tests del clasificador puro

**Verificación:**  
```
269 passed in 64.76s
```
Tests nuevos incluyen el caso específico:
- `test_api_only_engineer_blocks_even_with_implementation_verbs_in_output` — verifica que ni con "Implementado" en output escapa el bloqueo
- `test_api_only_engineer_blocks_even_without_any_output` — verifica el bloqueo estructural puro

---

### RUN-002 · RESUELTO — Continuaciones ilimitadas para agentes sin evidencia (max=1 insuficiente)

**Detectado:** 2026-05-04  
**Proyecto:** Arquitectura interna (no run específica)  
**Síntomas:**  
- El sistema solo permitía 1 intento de continuación (`max_continuation_attempts=1`).
- La razón del wakeup de continuación era `"workspace_evidence_required"` — semánticamente incorrecto para casos donde el agente simplemente no produjo output útil.
- Un agente que falla en producir evidencia dos veces queda bloqueado sin explicación clara.

**Causa raíz:**  
El diseño original de `_enqueue_liveness_continuation` fue conservador (1 intento) y usaba terminología centrada en workspace ("workspace_evidence_required") que no capturaba todos los casos de plan_only / empty_response.

**Fix aplicado (2026-05-04):**  
- `MAX_CONTINUATION_ATTEMPTS = 2` (alineado con Paperclip)
- Razón de wakeup cambiada a `"liveness_continuation"`
- Idempotency key: `liveness_continuation:{issue_id}:{source_run_id}:{liveness_state}:{next_attempt}`
- Al agotar intentos → `blocked` con comentario explicativo + `notify_supervisor`

**Verificación:**  
- `test_plan_only_first_attempt_is_continuable`
- `test_plan_only_second_attempt_is_still_continuable`  
- `test_plan_only_exhausted_at_max_is_blocked`
- `test_liveness_continuation_blocks_after_max_attempts_without_workspace_changes`

---

### RUN-003 · RESUELTO — Engineer API-only ejecutó 9 runs sin workspace changes; sistema antiguo bloqueó tarde

**Detectado:** 2026-05-05  
**Run ID(s):** `acff3337`, `65efb49a`, `25afe004`, `1753ef54`, `0f48191d`, `47851c3e`, `4885c3dd`, `15599c6d`, `5c88f0fb` (9 runs consecutivas)  
**Proyecto:** Test Proyecto AI Teams  
**Síntomas:**  
- El `role:engineer` con `adapter_type=openai_api` ejecutó 9 runs sobre `d723b8e5` ("Implementar prototipo jugable de Cartógrafo de Ecos").
- Todas las runs afirmaron haber creado `README.md` e `index.html`, pero el workspace del proyecto externo no contiene ningún archivo fuera de `.aiteam/`.
- El sistema antiguo detectó `needs_workspace_evidence` en la run `1753ef54` pero **no bloqueó**: el agente continuó 4 runs más como `completed`.
- Solo la run `5c88f0fb` fue bloqueada correctamente (con la vieja regex), pero para entonces ya había 8 runs fantasma en la DB.
- La issue quedó en `blocked` con diagnóstico correcto pero sin ruta de recuperación automática.

**Causa raíz:**  
1. **Sistema antiguo incompleto:** `_IMPLEMENTATION_CLAIM_RE` produjo `needs_workspace_evidence` en run `1753ef54`, pero ese estado no impedía nuevas runs — solo encolaba un wakeup de revisión que volvía a dejar correr al mismo agente con el mismo adapter.
2. **Adapter incorrecto persistente:** El `role:engineer` nunca fue reasignado a un adapter CLI. El sistema bloqueaba pero no rotaba automáticamente el adapter.
3. **Sin límite de intentos API-only:** El diseño antiguo no tenía el bloqueo inmediato para API-only engineers — el nuevo clasificador bloquea en run 1.

**Estado actual del proyecto externo:**  
- Issue `d723b8e5`: `blocked`, sin archivos reales en workspace.  
- Issue `issue:intake`: `in_progress`, Lead en loop de skip (ver RUN-004).  
- **Recomendación:** Resetear proyecto externo. El plan del Lead es válido y reutilizable; la issue de implementación debe recrearse limpia con un adapter CLI asignado.

**Fix sistémico aplicado (2026-05-05 — RUN-001):**  
El nuevo `classify_run_liveness()` bloquea en run 1 para API-only engineers sin workspace changes. No requiere regex. Este escenario no puede repetirse con el código actual.

**Verificación pendiente:** Resetear proyecto y confirmar que run 1 del engineer ya bloquea con `liveness_reason = "api_only_engineer_no_workspace_changes"`.

**Cierre (2026-07-06):** La pieza que faltaba — "ruta de recuperación automática" — está implementada: `_attempt_adapter_recovery` (executor). Cuando una issue agota continuaciones sin evidencia de workspace, el executor busca otro adapter **conectado y distinto** en el allowlist del proyecto, cambia el adapter del agente, reabre la issue en `todo` y despierta al agente — máximo 1 vez por issue (auditado con `issue.adapter_recovery`). Complementa el upgrade automático de `reconcile_project_agent_policy` (que no reabría issues ya bloqueadas). Tests: `test_adapter_recovery_reopens_exhausted_issue_with_alternative_adapter`, `test_adapter_recovery_noop_without_alternative_adapter`.

---

### RUN-004 · RESUELTO — Lead en loop de skip por falta de notificación ante engineer bloqueado

**Detectado:** 2026-05-05  
**Run ID(s):** `9bec8e8f`, `b76c4bed`, `a797a48c`, `f1b994c0`, `67ef65e7` (últimas 5 runs del lead)  
**Proyecto:** Test Proyecto AI Teams  
**Síntomas:**  
- El `role:lead` en `issue:intake` repite el mismo skip en cada wakeup manual: *"el plan ya existe, trabajo delegado, sin reportes QA ni interacciones pendientes"*.
- El `role:reviewer` tiene wakeups con `status=skipped` — no tiene nada real que revisar porque el engineer nunca produjo archivos.
- El lead nunca detecta que el engineer está `blocked` y nunca toma una acción correctiva (reasignar adapter, escalar al usuario).
- El sistema queda en un dead-lock silencioso: Lead espera QA → QA skippea (sin entregable) → Engineer bloqueado → Lead no reacciona.

**Causa raíz:**  
1. **Falta de propagación del bloqueo del engineer al lead:** Cuando `liveness_state=blocked` se establece en una issue hija, no hay un wakeup automático al supervisor (lead) con contexto suficiente para que tome acción. El `notify_supervisor` se registra como acción pero el wakeup generado llega con payload mínimo (`child_report`) sin señal de bloqueo.
2. **Lead prompt no diferencia `child_report` + `blocked` de `child_report` + `done`:** El lead recibió un wakeup `child_report` (run `8a4e4428`) pero solo anotó "espero evidencia de QA" en lugar de escalar. El contexto del wakeup no transmitía que la issue hija estaba `blocked`.
3. **Sin timeout de escalación:** No existe un mecanismo que detecte "issue hija lleva N días `blocked` sin acción del supervisor" y fuerce un wakeup de escalación.

**Impacto:**  
- Issue `issue:intake` permanece `in_progress` indefinidamente.  
- Cada wakeup manual del lead consume una run sin producir valor.  
- Sin intervención humana, el proyecto no avanza.

**Fixes requeridos:**  
1. **Payload del `notify_supervisor` wakeup:** Incluir `blocked_issue_id`, `blocked_reason`, `adapter_type` del agente bloqueado para que el lead pueda tomar decisión informada.  
2. **Guard en el prompt del lead:** Detectar cuando un `child_report` viene de una issue con `status=blocked` y actuar (escalar al usuario, cambiar adapter, crear sub-issue de reasignación).  
3. **Escalación por timeout:** El scheduler de heartbeat debería detectar issues hijas `blocked` con supervisor `in_progress` y forzar un wakeup de escalación tras N minutos.

**Fix aplicado (2026-05-05):**  
- `_enqueue_supervisor_report()` ahora re-lee el estado actualizado de la issue hija desde DB y añade al payload: `child_issue_status`, `child_liveness_state`, `child_liveness_reason`.  
- `run_liveness.py`: los `actions_override` de blocked añaden `_liveness_state` y `_liveness_reason` como campos privados que consume `_apply_result_actions` al llamar al supervisor.  
- Idempotency key mejorado: `child_report:{parent}:{supervisor}:{terminal_bucket}` donde `terminal_bucket` diferencia `blocked` de `done`/`progress` — el coalescing se mantiene para reportes del mismo tipo pero blocked y done ya no se fusionan entre sí.

**Verificación:**  
```
269 passed in 67.93s
```
Test existente `test_child_reports_to_same_lead_are_coalesced` sigue pasando (coalescing de 3 hijos en 1 wakeup preservado).

**Pendiente:** El prompt del lead todavía no usa activamente `child_issue_status` del payload para decidir escalar vs esperar. Esto requiere cambio en el prompt/skill del lead.

**Cierre (2026-07-08):** El pendiente quedó cubierto por tres piezas posteriores: (1) inyección de `unblock_action_required` + `mandatory_instruction` en el payload cuando un hijo reporta blocked (el Lead no puede "no darse cuenta"); (2) el contrato de orquestación del codex CLI dirige explícitamente hijos bloqueados a `update_child_issue`; (3) taxonomía de errores: runs del Lead fallidos por infra (`api_error`) ya no cuentan como `lead.unblock_skipped` — se registran como `lead.unblock_run_failed` y no disparan el circuit breaker (test `test_circuit_breaker_ignores_failed_lead_runs`).

---

### RUN-005 · RESUELTO — Lead builtin crea sub-issues duplicadas cuando se despierta manualmente estando ya en estado delegado

**Detectado:** 2026-05-05  
**Run ID(s):** Varias runs del lead en `Nuevo Proyecto AI Teams`  
**Proyecto:** Nuevo Proyecto AI Teams  
**Síntomas:**  
- El lead builtin se despertó manualmente por segunda vez mientras ya tenía issues hijas no-terminales (plan/build/review/qa).
- En lugar de skipear, volvió a entrar al flujo de propuesta y se crearon issues adicionales, llegando a 7 en total en lugar de 4.
- Las nuevas issues tenían el mismo rol que las originales, creando ownership duplicado para el mismo trabajo.

**Causa raíz:**  
1. El guard `_proposal_state(issue_id) in {"pending", "accepted"}` en `_execute_builtin_lead` cubre el caso de interacción `suggest_tasks` activa o aceptada, pero no cubre el caso donde la interacción ya terminó pero los hijos ya existen.  
2. El path de `create_issues` del LLM adapter llama a `_create_delegated_issue` que usa `create_issue()` con UUID generado, sin ningún check de existencia previa de hijos con el mismo rol.

**Fix aplicado (2026-05-05):**  
- En `_execute_builtin_lead`: añadido check `_has_non_terminal_children(issue_id)` como guard adicional antes de lanzar propuesta — si ya existen hijos no-terminales, skipea.  
- En `_create_delegated_issue`: añadido idempotency check — si ya existe una issue hija no-terminal con el mismo `parent_id` y `role`, devuelve la existente en lugar de crear una nueva.  
- Nuevo método `_has_non_terminal_children(issue_id)` en `RunExecutor`.

**Verificación:** Tests del executor + tests de liveness pasan sin regresión.

---

### RUN-006 · RESUELTO — Lead builtin ignora `child_issue_status: "blocked"` en wakeup `child_report`

**Detectado:** 2026-05-05  
**Run ID(s):** Runs del lead en `Nuevo Proyecto AI Teams` post-fix de RUN-004  
**Proyecto:** Nuevo Proyecto AI Teams  
**Síntomas:**  
- El wakeup `child_report` llega con `child_issue_status: "blocked"` y `child_liveness_reason: "api_only_engineer_no_workspace_changes"` (RUN-004 fix).
- El lead builtin (`_execute_builtin_lead`) entraba al branch `child_report`, llamaba `_format_supervisor_summary()` y devolvía `status="completed"` sin acción alguna.
- El usuario no recibía ninguna señal de bloqueo; el proyecto quedaba en dead-lock silencioso.

**Causa raíz:**  
El branch `child_report` en `_execute_builtin_lead` solo comprobaba si `_all_children_done()` para crear una interacción de cierre de ciclo. No había ningún check de hijos en estado `blocked`, por lo que el bloqueo del engineer se tragaba silenciosamente.

**Inspiración (Paperclip):**  
Paperclip computa `blockerAttention` con estados `covered/needs_attention/stalled` para surfacear bloqueos al supervisor. El equivalente en AI Teams es crear una interacción `request_confirmation` tipo "blocked_child_requires_action" que ponga el bloqueo delante del usuario.

**Fix aplicado (2026-05-05):**  
- En `_execute_builtin_lead`, el branch `child_report` ahora llama a `_blocked_child_rows(issue_id)` antes de `_all_children_done()`.
- Si hay hijos bloqueados, genera un output de escalación y crea una interacción `request_confirmation` con `reason: "child_blocked_requires_action"`, listando los hijos bloqueados con su `liveness_reason` más reciente.
- Idempotency key: `lead:blocked-child:{issue_id}` — una sola interacción por ciclo de bloqueo, no una por hijo.
- Nuevos métodos: `_blocked_child_rows(issue_id)`, `_format_blocked_escalation(blocked_rows)`.

**Verificación:** Tests del executor + tests de liveness pasan sin regresión.

---

### RUN-007 · RESUELTO — Bucle reviewer↔engineer por starvation de workspace_files (README "invisible")

**Detectado:** 2026-07-07  
**Run ID(s):** ~28 ciclos bajo `a0539b99` ("acaba suficientemente el juego…"), 08:36–12:33  
**Proyecto:** Nuevo Proyecto AI Teams  
**Síntomas:**  
- El reviewer repetía *"`README.md` no aparece en `workspace_files`"* y `changes_requested`, con el README presente en disco.
- El Lead cancelaba el reviewer, creaba un engineer de "fix" que re-creaba el archivo ya existente, y un reviewer nuevo que seguía sin verlo. 13+ ciclos la primera tarde; el patrón se reanudó tras cada wake.
- El cap automático de fix-cycles no aplicaba: el Lead creaba los pares engineer/reviewer a mano (vía `create_issue`), esquivando `_MAX_FIX_CYCLES`.

**Causa raíz:**  
1. `_read_workspace_files` ordenaba alfabéticamente y **descartaba** todos los archivos tras agotar el presupuesto de 32 KB. `README.md` ordena después de `Assets/` (~30 KB) → nunca entraba al payload. El reviewer decidía correctamente sobre datos falsos.
2. Sin cap para el churn *manual* de delegación del Lead.

**Fix aplicado (2026-07-07):**  
- `_read_workspace_files`: **todos** los archivos se listan siempre (path + tamaño); el contenido se incluye por prioridad (README/docs/manifests/escenas → fuentes) hasta el presupuesto; los que exceden llevan marcador `[content omitted — file exists]`. Test de regresión: `test_readme_content_not_starved_by_alphabetical_order`.
- **Freno de churn de delegación**: máx. `AITEAM_DELEGATION_CHURN_LIMIT` (8) issues del mismo rol bajo el mismo parent por ventana de 6 h; al límite, la creación se bloquea y se escala una interacción idempotente (accept = otra ronda; reject = bloqueado el resto de la ventana). Tests en `test_run_executor.py`.

**Verificación:** Post-fix, el reviewer lee el YAML real de la escena y contrasta contra `README.md` (comentarios de las 12:32) — rechaza ahora por motivos legítimos (escena stub), no por ceguera.

---

### RUN-008 · RESUELTO — file_ops con ruta absoluta crea árbol anidado `Users/...` dentro del workspace

**Detectado:** 2026-07-08 (artefacto creado 2026-07-07 03:53)  
**Proyecto:** Nuevo Proyecto AI Teams  
**Síntomas:** Árbol fantasma `Users/she__/Documents/AI Teams Projects/Nuevo Proyecto AI Teams/.aiteam/issue-intake-context.md` anidado dentro del propio workspace.  
**Causa raíz:** Un agente emitió la ruta absoluta completa en un file_op; `_execute_file_ops` le quitaba solo la unidad (`C:`) y trataba el resto como relativo → re-rooteo silencioso.  
**Fix aplicado (2026-07-07):** Rutas con unidad/UNC se resuelven de verdad: dentro del workspace → parte relativa correcta; fuera → rechazadas con warning. El `/` inicial a secas sigue significando raíz del workspace (shorthand LLM). Tests: `test_file_ops_absolute_workspace_path_relativized`, `test_file_ops_absolute_path_outside_workspace_skipped`, `test_file_ops_leading_slash_still_means_workspace_root`.  
**Nota:** El artefacto basura sigue en el workspace del proyecto (borrado manual pendiente — capa 2).

---

### RUN-009 · RESUELTO — codex CLI: prompt por argv supera el límite de línea de Windows; stdin cp1252 rechazado

**Detectado:** 2026-07-07  
**Run ID(s):** 6 runs fallidos 00:20–00:21 (`subscription_cli_nonzero_exit`, "La línea de comandos es demasiado larga"), 2 más 00:30 ("input is not valid UTF-8")  
**Proyecto:** Nuevo Proyecto AI Teams  
**Causa raíz:** (1) El prompt completo (skill + payload + workspace_files) iba como argumento de línea de comandos; `codex.cmd` corre vía cmd.exe (~8 191 chars máx). (2) Al pasar a stdin, Python codificaba en cp1252 (default Windows) y codex espera UTF-8 — además del mojibake en stdout.  
**Fix aplicado (2026-07-07):** Prompt por stdin (`codex exec … -`) + `encoding="utf-8"` en el subproceso. Además, los runs fallidos ya no publican su stdout crudo (el prompt ecoado) como comentario del chat.  
**Verificación:** Runs reales de file_scout/lead completando con acentos correctos; tests `test_prompt_read_from_stdin_not_argv`, `test_prompt_piped_via_stdin_input`, `test_failed_run_output_not_posted_as_chat_comment`.

---

### RUN-010 · MITIGADO — cache de modelos Codex más nuevo que el CLI instalado

**Detectado:** 2026-07-20

**Run ID(s):** canario `context-curator-auth-codex-luna-seed-1`

**Síntoma:** `gpt-5.6-luna` aparece en `models_cache.json`, pero Codex CLI `0.128.0` termina antes de ejecutar porque el catálogo requiere un cliente `0.145.0`. No hay summary, usage ni score comparable.

**Causa raíz:** el catálogo persistido y el binario ejecutor se actualizaron de forma independiente; presencia en cache no implica compatibilidad de ejecución.

**Mitigación (2026-07-20):** el adapter clasifica el error como `model_unavailable`; Equipo compara versión de CLI y catálogo, deshabilita opciones no demostradas, conserva evidencia de runs completadas y el hiring no fija esos modelos. La issue se bloquea y propone GPT-5.5 dentro del mismo perfil mediante una interacción owner; solo la aceptación actualiza el agente y reencola. GPT-5.5 continúa habilitado por evidencia real.

**Pendiente externo:** actualizar Codex CLI y repetir las semillas auth+queue. El JSON del canario es diagnóstico, no evidencia A/B.

---

## Patrones de riesgo conocidos

### P-1: Agente API-only en rol engineer
**Síntoma histórico:** Agente produce texto/plan, issue no avanza.
**Detección vigente:** ausencia de `file_ops` materializados/delta tras las continuaciones acotadas; el nombre legacy de la razón puede conservar `api_only`.
**Acción vigente:** diagnosticar contrato/ops y compatibilidad del modelo; reasignar solo si el adapter o modelo no puede cumplir, no por ser API automáticamente.

### P-2: Agente CLI sin cambios en workspace tras múltiples intentos
**Síntoma:** `liveness_state = "plan_only"` o `"empty_response"` en 2+ runs consecutivas.  
**Detección:** `continuation_attempt >= MAX_CONTINUATION_ATTEMPTS` → `blocked`  
**Acción:** Revisar el skill/contexto del agente. Puede necesitar instrucciones más específicas o un adapter diferente.

### P-3: Issue en `in_progress` sin wakeup activo
**Síntoma:** Issue bloqueada sin continuación pendiente — "zombie issue".  
**Detección:** `diagnose_issue()` + `reconcile_unqueued_assigned_issues()`  
**Acción:** El scheduler de heartbeat tiene `reconcile_unqueued_assigned_issues` que recupera estos casos automáticamente.

### P-4: Engineer produce workspace changes pero no declara done
**Síntoma:** Issue queda en `in_progress` aunque haya evidencia en workspace.  
**Detección:** `liveness_state = "advanced"` pero `issue.status != "done"`  
**Acción (2026-05-04):** El clasificador ahora auto-cierra cuando `workspace_files_changed > 0` y el adapter no declaró un `issue_status` explícito.

### P-5: Lead en skip-loop por bloqueo silencioso de issue hija
**Síntoma:** `role:lead` skippea repetidamente con "plan existe, trabajo delegado, sin reportes QA".  
**Detección:** Issue hija en `blocked` + supervisor en `in_progress` + wakeups del lead todos `skipped`/`completed` sin cambios de estado.  
**Causa:** El payload del wakeup `child_report` no diferencia `blocked` de `done`. El lead no tiene señal explícita del bloqueo.  
**Acción (2026-05-05):** Resuelto — `_enqueue_supervisor_report` enriquece el payload con `child_issue_status/liveness_state/reason`. Lead builtin escala automáticamente. `reconcile_stalled_subtrees` rescata el caso de dead-lock completo.

### P-6: Subtree completamente bloqueado sin escalación (dead-lock silencioso)
**Síntoma:** Todos los hijos del lead están en `blocked`. Reviewer/QA tienen dependencias en el engineer bloqueado → el scheduler los skippea. Lead en `in_progress` sin wakeup activo. Sistema paralizado indefinidamente.  
**Detección:** Parent `in_progress` + todos los hijos no-terminales en `blocked` + sin wakeup activo para el supervisor.  
**Causa:** El scheduler de heartbeat no tenía un reconciler para este patrón. El lead solo recibía `child_report` wakeups cuando un agente terminaba, pero si todos están bloqueados no hay ningún reporte.  
**Acción (2026-05-05):** `reconcile_stalled_subtrees()` detecta este patrón en cada tick y encola un `subtree_stalled` wakeup al supervisor con `wake_reason: child_report`. El lead responde escalando al usuario vía `request_confirmation`. Idempotency key: `subtree_stalled:{parent_id}:{blocked_ids}` — se re-activa si el conjunto de hijos bloqueados cambia.

### P-7: Lead en skip-loop al recibir manual wake con hijos bloqueados (guard demasiado amplio)
**Síntoma:** El lead recibe un wake manual, detecta hijos (todos bloqueados), ejecuta el guard `_has_non_terminal_children` → True, skippea. No produce escalación. El proyecto queda en dead-lock aun con wakeups manuales.  
**Causa:** `_has_non_terminal_children` contaba hijos `blocked` como "non-terminal", impidiendo cualquier acción del lead aunque debería escalar.  
**Acción (2026-05-05):** Renombrado a `_has_progressing_children` — excluye `blocked` del conteo. Hijos bloqueados no evitan el flujo del lead; en su lugar, el lead detecta los bloqueados y escala. Además añadido segundo check de `_blocked_child_rows` en la ruta pre-propuesta (fuera del branch `child_report`) para que cualquier wakeup manual también active la escalación.

### P-8: Engineer/QA recibe adapter API-only por scoring sin penalización para junior roles
**Síntoma:** El proyecto tiene `subscription_cli` disponible pero el engineer recibe `openai_api` al ser contratado via `choose_adapter_for_role`, bloqueando en la primera run.  
**Causa:** `_profile_score` no diferenciaba API vs CLI para roles junior (`engineer`, `qa`, `worker`). El score era idéntico entre `openai_api` y `subscription_cli` en igualdad de condiciones de salud.  
**Acción (2026-05-05):** `_profile_score` ahora penaliza -30 pts los adapters API-only para roles no-senior y añade +30 pts a `subscription_cli`. Propuesta del lead también advierte explícitamente si el engineer recibiría un adapter API-only al aceptar.

**Revisión (2026-07-21):** esa penalización y la advertencia ya no expresan la
capacidad actual: los adapters API pueden escribir mediante ops estructurados.
P0.3 de `task.md` reemplazará la heurística por compatibilidad explícita de
adapter×modelo×rol y retirará el warning genérico.

### P-9: Engineer pide al Lead el contenido de archivos (file-access blocking)
**Síntoma:** El engineer crea una interacción `lead_wants_file_read` o escala al Lead pidiendo que le "envíe" el contenido de archivos antes de poder implementar. El Lead queda en espera; el engineer nunca produce workspace changes.  
**Causa:** El agente engineer no sabía que el wake payload ya incluye `workspace_files` (contenido completo) para su rol. Sin ese conocimiento explícito, el agente infería que tenía que pedir los archivos.  
**Detección:** Interacción `reason: "lead_wants_file_read"` creada por el Lead **por un engineer** (no un scout). Output del engineer menciona "necesito ver los archivos" o similar. Workspace delta vacío.  
**Acción (2026-05-12):** `_WORKSPACE_READER_ROLES` incluye `engineer` — el payload de wake siempre incluye `workspace_files` con contenido completo. `engineer.md` skill y `work_contract.py` actualizados con instrucción explícita: "workspace_files está siempre en tu wake payload — NO pidas al Lead el contenido de archivos". `lead_wants_file_read` marcado como ONLY válido para scouts tier 3, nunca para engineers.

### P-10: Flood de popups de confirmación (múltiples `create_interaction` en una run)
**Síntoma:** El usuario recibe 2–3 ventanas de confirmación en sucesión rápida por el mismo ciclo de issue. Confirmar una dispara el siguiente wakeup antes de que las demás hayan sido respondidas, creando popups en cascada y duplicando runs.  
**Causa:** Un agente LLM podía incluir varios ops `create_interaction` en un solo `submit_work`. El executor los creaba todos, encolando un wakeup por interacción. El usuario veía múltiples diálogos simultáneos del mismo issue.  
**Detección:** `SELECT COUNT(*) FROM issue_thread_interactions WHERE issue_id = ? AND status = 'pending'` > 1 para la misma issue.  
**Acción (2026-05-12):** Interaction gate en `_apply_actions`: al crear interacciones, cuenta las pendientes pre-existentes. Si ya hay ≥ 1, descarta las nuevas con `logger.warning`. Dentro de un mismo run, solo la primera interacción de la lista se materializa (`_created_this_run` counter). `work_contract.py` documenta el límite explícitamente: "LIMIT: only ONE create_interaction per run. The executor will silently drop any extras."

### P-11: Reviewer `changes_requested` sin ciclo de corrección automático (deadlock de revisión)
**Síntoma:** El reviewer termina su run con `result: changes_requested` en el `---AGENT-REPORT---` pero no se crea ninguna issue de corrección. El Lead skippea en wakeups sucesivos porque `_all_children_done` devuelve `False` pero ningún hijo está en estado activo que genere una acción. El proyecto queda en limbo indefinido.  
**Causa:** El framework no tenía lógica automática para detectar `changes_requested` y responder creando una issue de corrección para el engineer. El Lead LLM a veces creaba una issue nueva pero a veces no — comportamiento no determinista. El reviewer tampoco tenía wakeup automático para re-ejecutarse después del fix.  
**Detección:** `SELECT role, status, last_agent_report FROM issues WHERE parent_id = ?` muestra reviewer en `done` con `result: changes_requested` y todos los engineers en `done` o `cancelled`. Sin nueva issue de engineer activa. Wakeups del Lead todos `skipped` o `completed` sin crear issues.  
**Acción (2026-05-12):**  
- `_handle_reviewer_changes_requested()` en el branch `child_report`: detecta reviewer `done` + `changes_requested`, resetea reviewer a `todo`, crea issue numerada `Fix #N` con description que incluye `blocker` y `evidence` del reviewer.  
- `sync_default_child_dependencies` cablea reviewer → fix_engineer: el reviewer se auto-despierta cuando el engineer termina.  
- `_MAX_FIX_CYCLES = 3`: límite duro. Tras 3 ciclos consecutivos sin aprobación, escala al usuario via interacción `reviewer_fix_cycle_limit` (accept → engineer final con `complexity=high`; reject → cancela proyecto).  
- `_cancel_stale_interaction(reason="initial_cycle_ready")` antes de cada ciclo: evita que coexista un popup "todo terminado" con un ciclo de fix activo.  
- 25 tests nuevos en `tests/test_reviewer_changes_requested.py` y `tests/test_fix_cycle_limit_resolved.py`.

### P-12: LLM propone rol incorrecto para tarea crítica (routing ciego del Lead)
**Síntoma:** El Lead LLM crea un hijo con `role="engineer"` para una tarea con `criticality="critical"` y `complexity="high"`. El engineer queda bloqueado o produce trabajo de baja calidad porque la tarea requería nivel senior. El Lead no escala porque no hay ningún gate automático.  
**Causa:** El routing de roles era 100% LLM-driven: el Lead proponía el rol en el JSON de `create_issues` y el executor lo usaba tal cual. No existía una función de scoring que validara si el rol propuesto era apropiado para la criticidad/complejidad.  
**Detección:** Hijo con `role=engineer` + `criticality=critical` en DB. Liveness stuck o output de baja calidad. `SELECT role, criticality FROM issues WHERE parent_id = ?`.  
**Acción (2026-05-13):** Nuevo módulo `aiteam/action_routing.py`: `route_action(criticality, complexity, action_type) → Routing`. `_create_delegated_issue` en executor llama al routing cuando el spec incluye `criticality + action_type`; sobreescribe el rol LLM si el scoring diverge. Log de actividad `action.routed`. `pick_role_for_routing(LEAD_SELF, action_type)` devuelve `lead_executor`. Tests: `tests/test_action_routing.py` (30 tests), `tests/test_lead_intake_routing.py` (3 tests).

### P-13: Agente Tier 3 creando issues / escribiendo archivos (violación de frontera)
**Síntoma:** Un `file_scout` o `context_curator` emite ops `create_issue` o `write_file` en su JSON. El executor los aplica silenciosamente, creando issues no supervisadas por el Lead o modificando el workspace sin revisión.  
**Causa:** No había ningún filtro en runtime que bloqueara ops inapropiadas para roles Tier 3. El contrato de trabajo definía las reglas en texto, pero el executor no las validaba.  
**Detección:** Issues con `parent_id` de una issue Tier 3 en la DB. Workspace changes cuyo `source_run_id` corresponde a un run de `file_scout`/`context_curator`.  
**Acción (2026-05-13):** `filter_forbidden_ops_for_role(ops, role)` en `work_contract.py`: ops prohibidas para `{file_scout, web_scout, context_curator, test_runner}` = `{create_issue, create_interaction, update_plan, write_file, append_file, delete_file}`. `_apply_result_actions` en executor invoca el filtro antes de procesar; log `warning` por cada op dropeada. Skill files de cada rol Tier 3 actualizados con tabla explícita de ops prohibidas. Tests: `tests/test_tier_discipline.py` (9 tests).

### P-14: Agente QA bloqueado por adapter API-only (rol deprecado sin sucesor claro)
**Síntoma:** Un proyecto legacy con `role:qa` (Tier 2) en DB tiene el agente bloqueado porque el adapter API-only no puede ejecutar comandos de test. El QA emite `result: blocked` con `blocker: no_workspace_access`. El Lead no tiene instrucciones para crear un `test_runner` en su lugar.  
**Causa:** El rol QA Tier 2 mezclaba ejecución de tests de runtime (que requiere CLI) con validación estática (que puede hacer cualquier LLM). Al eliminarse, el sucesor natural (`test_runner` Tier 3 para ejecución + `reviewer` absorbiendo QA estático) no se wired automáticamente.  
**Detección:** `SELECT id, role, adapter_type FROM agents WHERE lower(role) = 'qa'`. Runs con `agent_id LIKE 'role:qa%'` y `liveness_state = 'api_only_no_workspace'`.  
**Acción (2026-05-13):** `skills/qa.md` eliminado; rol `qa` marcado deprecated en `work_contract.py`, `run_profiles.py` y `project_adapters.py`. Reviewer absorbe QA estático. Nuevo `test_runner` (Tier 3) para ejecución de comandos: recibe lista de comandos, reporta stdout/exitcode, no toma decisiones. La migración quedó consolidada en `docs/HISTORY.md`. Tests: `tests/test_full_team_no_qa.py` (5 tests), `tests/test_test_runner_scout.py` (9 tests).

### P-15: Context curator en loop infinito (done bloqueaba re-spawn pero umbrales eran por conteo)
**Síntoma:** Hilo con 50 comentarios cortos (< 200 chars cada uno) no dispara el curator porque el conteo supera el umbral pero el contenido acumulado es trivial. O al revés: 3 comentarios de 5 000 chars c/u NO disparan el curator porque el conteo (3) es < 8.  
**Causa:** El umbral original era `_CONTEXT_CURATOR_COMMENT_THRESHOLD = 8` (conteo de comentarios). El conteo no refleja el volumen real de contexto. Además, un curator `done` bloqueaba re-spawn para siempre — en hilos que seguían creciendo el Lead no recibía síntesis adicionales.  
**Detección:** Issue padre con > 40 000 chars en comentarios sin curator hijo. O curator hijo `done` y 20 000 chars nuevos sin nuevo curator.  
**Acción (2026-05-13):** Umbral cambia a `_CONTEXT_CURATOR_CHAR_THRESHOLD = 8_000` (chars no sintetizados). "No sintetizados" = comentarios con `rowid > rowid(synthesized_through_comment_id)` del doc `context_summary`. Curator `done` ya **NO** bloquea re-spawn — permite bloques incrementales. Solo curatores activos (todo/in_progress/blocked) bloquean. El curator publica bloques vía `POST /api/issues/{id}/context-summary/blocks` (ratio ≤ 30% validado). Tests: `tests/test_context_curator_auto_trigger.py` (16 tests), `tests/test_append_summary_block.py` (13 tests).

### P-16: lead_executor creado con adapter incorrecto (Lead usa subscription_cli, ejecutor recibe openai_api)
**Síntoma:** El Lead usa `subscription_cli` (Codex / Antigravity) pero cuando routing determina LEAD_SELF y se crea `role:lead_executor`, el agente recibe `adapter_type=openai_api` por defecto. El executor no puede ejecutar workspace changes en modo API-only y queda bloqueado con `liveness_reason=api_only_no_workspace`.
**Causa:** `_ensure_role_agent` creaba el agente con el adapter elegido por scoring genérico (`choose_adapter_for_role`), sin relación con el adapter del Lead. Para un ejecutor senior del Lead, el adapter debería ser idéntico al del Lead.  
**Detección:** `SELECT a.adapter_type FROM agents a WHERE a.id = 'role:lead_executor'` difiere de `SELECT adapter_type FROM agents WHERE id = 'role:lead'`.  
**Acción (2026-05-13):** `_ensure_role_agent` en executor: caso especial para `lead_executor` — lee `adapter_type` y `adapter_config_json` del Lead desde DB y los hereda directamente. `seniority='senior'`. Tests: `tests/test_lead_executor.py` (11 tests).

### P-17: Payload de archivos con starvation (existencia invisible para el reviewer)
**Síntoma:** Reviewer afirma que un archivo "no existe" cuando está en disco; bucle de fixes que re-crean archivos presentes.  
**Causa:** Cualquier inyección de contexto que trunca por presupuesto **descartando** entradas en vez de degradarlas a "existe, contenido omitido". La existencia y el contenido son garantías distintas: la existencia debe ser total.  
**Detección:** Comentarios del reviewer citando `workspace_files` sin un archivo que `ls` sí muestra.  
**Acción (2026-07-07):** `_read_workspace_files` lista siempre todo; contenido por prioridad; ver RUN-007.

### P-18: Churn de delegación manual (el Lead esquiva los caps automáticos)
**Síntoma:** El mismo parent acumula pares engineer/reviewer creados y cancelados en ráfaga; los caps de fix-cycle no saltan porque cuentan solo la vía automática.  
**Detección:** `delegation.churn_blocked` en activity_log; o ≥8 issues del mismo rol bajo un parent en <6 h.  
**Acción (2026-07-07):** Freno de churn en `_create_delegated_issue` con escalación idempotente (`delegation_churn_limit`). Env: `AITEAM_DELEGATION_CHURN_LIMIT`.

### P-19: file_ops con rutas absolutas re-rooteadas
**Síntoma:** Árbol espejo del path del workspace anidado dentro del workspace (`Users/.../proyecto/...`).  
**Detección:** Directorios de primer nivel inesperados que replican la estructura del filesystem.  
**Acción (2026-07-07):** Resolución estricta de rutas absolutas en `_execute_file_ops`; ver RUN-008.

---

## Historial de cambios al sistema de evidencia

| Fecha | Cambio | Archivo |
|-------|--------|---------|
| 2026-05-04 | Creado `RunEvidence`, `LivenessResult`, `classify_run_liveness`, `collect_run_evidence` | `aiteam/run_liveness.py` |
| 2026-05-04 | Eliminada `_IMPLEMENTATION_CLAIM_RE`, refactorizado `execute()` | `aiteam/heartbeat/executor.py` |
| 2026-05-04 | Creados 51 tests del clasificador puro | `tests/test_run_liveness.py` |
| 2026-05-04 | Actualizados 3 tests afectados por nueva semántica | `tests/test_run_executor.py` |
| 2026-05-05 | `update_issue()` soporta `criticality` y `metadata` | `aiteam/db/issues.py` |
| 2026-05-05 | `UpdateIssueRequest` añade `criticality` y `metadata` | `api/routers/issues.py` |
| 2026-05-05 | `GET /api/runs` soporta filtro `liveness_state` | `api/routers/runs.py` |
| 2026-05-05 | Añadido `GET /api/wakeup-requests` con filtros | `api/routers/control_plane.py` |
| 2026-05-05 | Auto-migración de schema en startup (`_apply_schema`) | `api/main.py` |
| 2026-05-05 | Añadidos `liveness_continuation` y `dependency` a wakeup_reasons | `runtime/control_plane.json` |
| 2026-05-05 | Documentados RUN-003, RUN-004, patrón P-5 | `docs/RUN_PROBLEMS_REGISTRY.md` |
| 2026-05-05 | `_enqueue_supervisor_report` enriquece payload con `child_issue_status`, `child_liveness_state/reason` | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `actions_override` de blocked incluye `_liveness_state/_reason` para supervisor | `aiteam/run_liveness.py` |
| 2026-05-05 | Idempotency key `child_report` diferencia `blocked` de `done/progress` | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `_execute_builtin_lead`: guard `_has_non_terminal_children` evita propuesta duplicada | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `_create_delegated_issue`: idempotency check por rol evita issues duplicadas del LLM | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `child_report`: escalación automática cuando hay hijos en `blocked` | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | Documentados RUN-005, RUN-006 | `docs/RUN_PROBLEMS_REGISTRY.md` |
| 2026-05-05 | Scheduler copia `child_issue_status/liveness_state/reason` al context_snapshot | `aiteam/heartbeat/scheduler.py` |
| 2026-05-05 | `build_wake_payload` añade `children` con estado de issues hijas y liveness | `aiteam/db/wake_payload.py` |
| 2026-05-05 | `lead.md` skill: instrucción explícita de escalación ante hijos bloqueados | `skills/lead.md` |
| 2026-05-05 | `_has_non_terminal_children` → `_has_progressing_children`: excluye `blocked` del guard de skip | `aiteam/heartbeat/executor.py` |
| 2026-07-06 | Retry 429/5xx/timeout en adapters API + provider governor (pacing TPM, cooldown, degraded) | `aiteam/adapters/http_retry.py`, `aiteam/provider_governor.py` |
| 2026-07-06 | Runs fallidos por infra no cuentan como `lead.unblock_skipped`; backoff 2 min del reconciler tras `api_error` | `aiteam/heartbeat/executor.py`, `aiteam/db/liveness.py` |
| 2026-07-06 | Verificación automática adjunta a cierres de Lead LLM (veredicto reviewer + scan stubs + coste) | `aiteam/heartbeat/executor.py` |
| 2026-07-06 | Recuperación RUN-003: `_attempt_adapter_recovery` reabre issue agotada con adapter alternativo | `aiteam/heartbeat/executor.py` |
| 2026-07-06 | Cost breaker por subtree (gasto sin avance → escalación) + dedupe wakeups por (agente, issue) | `aiteam/heartbeat/executor.py`, `aiteam/db/wakeups.py` |
| 2026-07-07 | codex CLI: prompt por stdin + UTF-8; modelo por `-c model=`; catálogo GPT-5.x | `aiteam/adapters/subscription_cli_adapter.py`, `aiteam/user_config.py` |
| 2026-07-07 | `_read_workspace_files` sin starvation (existencia total, contenido por prioridad) | `aiteam/heartbeat/executor.py` |
| 2026-07-07 | Freno de churn de delegación + rutas absolutas estrictas en file_ops + curación → context_curator | `aiteam/heartbeat/executor.py`, `aiteam/adapters/subscription_cli_adapter.py` |
| 2026-07-08 | Matriz RBAC de ops por tier + gate preventivo de file_ops para roles no-editores | `aiteam/adapters/work_contract.py`, `aiteam/heartbeat/executor.py` |
| 2026-07-08 | AGENT-REPORT como artefacto validado con procedencia (tabla `agent_reports`) | `aiteam/db/agent_reports.py`, `aiteam/db/schema.sql` |
| 2026-07-08 | Documentados RUN-007/008/009, P-17/18/19; RUN-003/004 cerrados | `docs/RUN_PROBLEMS_REGISTRY.md` |
| 2026-05-05 | Check pre-propuesta: escalación si hay hijos bloqueados (fuera del branch `child_report`) | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `reconcile_stalled_subtrees`: detecta subtrees all-blocked y enqola wakeup al supervisor | `aiteam/db/liveness.py` |
| 2026-05-05 | `HeartbeatLoop.run_once`: registrado `reconcile_stalled_subtrees` en cada tick | `aiteam/heartbeat/loop.py` |
| 2026-05-05 | `_profile_score`: penaliza API-only (-30) y bonifica subscription_cli (+30) para roles junior | `aiteam/project_adapters.py` |
| 2026-05-05 | `format_team_proposal`: advertencia explícita si el engineer recibiría adapter API-only | `aiteam/lead_intake.py` |
| 2026-05-05 | Documentados patrones P-6, P-7, P-8 | `docs/RUN_PROBLEMS_REGISTRY.md` |
| 2026-05-12 | `_WORKSPACE_READER_ROLES` incluye `engineer`; wake payload siempre lleva `workspace_files` para engineers | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `engineer.md` skill: instrucción explícita "workspace_files siempre presente, no pedir al Lead" | `skills/engineer.md` |
| 2026-05-12 | `work_contract.py`: `workspace_files` documentado para ALL roles; `lead_wants_file_read` reservado solo para scouts | `aiteam/adapters/work_contract.py` |
| 2026-05-12 | `_safe_truncate_output`: preserva `---AGENT-REPORT---` block al truncar output largo | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | Interaction gate en `_apply_actions`: máx 1 interacción pendiente por issue, máx 1 creada por run | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `work_contract.py`: documenta límite de 1 `create_interaction` por run; lista `reviewer_fix_cycle_limit` como razón conocida | `aiteam/adapters/work_contract.py` |
| 2026-05-12 | `_handle_reviewer_changes_requested`: auto-crea Fix #N engineer + resetea reviewer a todo en `child_report` | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `_MAX_FIX_CYCLES = 3`: cap duro con escalación a usuario via `reviewer_fix_cycle_limit` | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `_handle_fix_cycle_limit_resolved`: accept → engineer final (complexity=high); reject → cancela proyecto | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `_cancel_stale_interaction(reason="initial_cycle_ready")` antes de cada ciclo de fix | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `lead.md` skill: documenta automatización de `changes_requested` y protocolo de `reviewer_fix_cycle_limit` | `skills/lead.md` |
| 2026-05-12 | 25 tests: `test_reviewer_changes_requested.py` (15) + `test_fix_cycle_limit_resolved.py` (10) | `tests/` |
| 2026-05-12 | `_CONTEXT_CURATOR_COMMENT_THRESHOLD = 8`: auto-spawn curator cuando hilo > 8 comentarios y sin plan | `aiteam/heartbeat/executor.py` |
| 2026-05-12 | `_maybe_spawn_context_curator`: side-effect silencioso en `child_report`; 17 tests en `test_context_curator_auto_trigger.py` | `aiteam/heartbeat/executor.py`, `tests/` |
| 2026-05-12 | 16 tests formales de `_safe_truncate_output` + 9 tests del interaction gate | `tests/test_safe_truncate.py`, `tests/test_interaction_gate.py` |
| 2026-05-12 | Documentados patrones P-9, P-10, P-11 | `docs/RUN_PROBLEMS_REGISTRY.md` |
| 2026-05-13 | Tier discipline: `filter_forbidden_ops_for_role()` + skills Tier 3 con tabla de ops prohibidas | `aiteam/adapters/work_contract.py`, `skills/` |
| 2026-05-13 | QA Tier 2 eliminado; `test_runner` Tier 3 introducido; `requires_qa_gate` deprecated | `aiteam/run_profiles.py`, `skills/test_runner.md` |
| 2026-05-13 | `aiteam/action_routing.py`: `route_action()` + `pick_role_for_routing()`; integrado en `_create_delegated_issue` | `aiteam/action_routing.py`, `aiteam/heartbeat/executor.py` |
| 2026-05-13 | `lead_executor`: Tier 1 senior, hereda adapter del Lead; `skills/lead_executor.md` | `aiteam/heartbeat/executor.py`, `skills/lead_executor.md` |
| 2026-05-13 | Context curator: umbral 8 comments → 8 000 chars; bloques incrementales; done no bloquea re-spawn | `aiteam/heartbeat/executor.py`, `aiteam/db/documents.py` |
| 2026-05-13 | `append_summary_block()` / `get_context_summary()` en documents.py; `POST /api/issues/{id}/context-summary/blocks` | `aiteam/db/documents.py`, `api/routers/documents.py` |
| 2026-05-13 | Wake payload: inyecta `context_summary.blocks`; filtra comentarios antes de `synthesized_through` | `aiteam/db/wake_payload.py` |
| 2026-05-13 | `GET /api/issues/{id}/thread?view=compact|full`; `ThreadView` React component | `api/routers/issues.py`, `ide-frontend/src/components/ThreadView/` |
| 2026-05-13 | `lead_intake.py` F3.2: `action_type` + `criticality` en `suggested_issues`; routing override en `apply_accepted_team_proposal` | `aiteam/lead_intake.py` |
| 2026-05-13 | Documentados patrones P-12, P-13, P-14, P-15, P-16 | `docs/RUN_PROBLEMS_REGISTRY.md` |
