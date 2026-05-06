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

*(ninguno actualmente)*

---

## Problemas resueltos

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

### RUN-003 · ABIERTO — Engineer API-only ejecutó 9 runs sin workspace changes; sistema antiguo bloqueó tarde

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

---

### RUN-004 · ABIERTO — Lead en loop de skip por falta de notificación ante engineer bloqueado

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

## Patrones de riesgo conocidos

### P-1: Agente API-only en rol engineer
**Síntoma:** Agente produce texto/plan, issue no avanza.  
**Detección:** `liveness_state = "blocked"`, `reason = "api_only_engineer_no_workspace_changes"`  
**Acción:** Reasignar a adapter CLI/local (Codex CLI, Gemini CLI, Ollama/subprocess local).

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
| 2026-05-05 | Check pre-propuesta: escalación si hay hijos bloqueados (fuera del branch `child_report`) | `aiteam/heartbeat/executor.py` |
| 2026-05-05 | `reconcile_stalled_subtrees`: detecta subtrees all-blocked y enqola wakeup al supervisor | `aiteam/db/liveness.py` |
| 2026-05-05 | `HeartbeatLoop.run_once`: registrado `reconcile_stalled_subtrees` en cada tick | `aiteam/heartbeat/loop.py` |
| 2026-05-05 | `_profile_score`: penaliza API-only (-30) y bonifica subscription_cli (+30) para roles junior | `aiteam/project_adapters.py` |
| 2026-05-05 | `format_team_proposal`: advertencia explícita si el engineer recibiría adapter API-only | `aiteam/lead_intake.py` |
| 2026-05-05 | Documentados patrones P-6, P-7, P-8 | `docs/RUN_PROBLEMS_REGISTRY.md` |
