# Auditoria forense — `test_aiteams` / flujo de videojuego

Fecha: `2026-04-02`
Estado: `investigacion completada — convertida a backlog C1/C2/C3 en task.md + IMPLEMENTATION_PLAYBOOK.md`

Gaps resueltos por B7-B9: R1 (context_curator best-effort), R3 (plan .md), R5 (.aiteam/), R6 (.aiteam/instructions.md), R7 (semantica de estados en UI).
Gaps aun abiertos → ver C1/C2/C3 en playbook: R2 (delegate lazy), R4 (nuevo intento limpio), R3-extendido (entrega minima en vacio para todos los modos).
Proyecto auditado:

- `C:\Users\she__\Documents\Antigravity Projects\test_aiteams`

Runs auditadas:

- `CHAT-ABCE891F`
- `CHAT-1F789CCB`
- `CHAT-28015BB0`

## Objetivo de esta auditoria

Explicar por qué el flujo no produjo archivos de producto ni código visible,
por qué se acumularon tantas tareas pendientes,
por qué la sensación de avance era débil,
y qué está fallando realmente en la orquestación del proyecto externo.

Esta auditoria se centra en:

- secuencia real de runs
- tareas persistidas en SQLite
- eventos del orquestador
- artefactos creados o no creados
- deuda de producto y de flujo revelada por el caso

No es una especulación general. Está basada en el runtime real del proyecto auditado.

---

## Resumen ejecutivo

La conclusión principal es esta:

- el sistema **sí planificó internamente**
- **sí creó tareas**
- **sí deliberó entre agentes**
- pero el flujo **no llegó nunca a la parte productiva**

No hay archivos de juego ni código porque la cadena se rompió antes de `build`.

La secuencia real fue:

1. primera run con `lead_intake` fallida por falso positivo del placeholder gate
2. segunda run que sí completó `lead_intake`, pero bloqueó `plan_research` de inmediato
3. tercera run de continuación que volvió a completar `lead_intake`, pero repitió el bloqueo en `plan_research`

El usuario ve muchas tareas pendientes porque:

- se generan demasiadas tareas por adelantado
- se arrastran tareas de runs previas
- la UI todavía no separa bien `pending`, `blocked` y `carried_over`

El usuario no ve un plan porque:

- el plan existe solo como output interno (`lead_intake`, `workflow_state`, `context/chats`)
- no se persistió como archivo del proyecto

El usuario no ve archivos de producto porque:

- la run nunca alcanzó `build`
- y el proyecto externo hoy solo muestra el runtime interno del sistema

---

## Estado observado en disco

Raíz del proyecto auditado:

- solo existe `runtime/`

No se encontraron:

- `index.html`
- `src/`
- `assets/`
- `docs/`
- `planning/`
- `PROJECT_PLAN.md`
- ningún archivo de producto del videojuego

Sí existe un runtime voluminoso:

- `runtime/aiteam.db`
- `runtime/events.jsonl`
- `runtime/mailbox.jsonl`
- `runtime/session_index.jsonl`
- `runtime/memory/`
- `runtime/sandboxes/`
- `runtime/context/`

Además, en:

- `runtime/context/projects/`

coexisten:

- el contexto del propio proyecto `test_aiteams`
- el contexto de `Ai_Teams`

Eso confirma mezcla de contexto del sistema dentro del runtime del proyecto externo.

---

## Secuencia real de runs

## 1. `CHAT-ABCE891F`

Input original:

- `quiero que creeis un videojuego original entre vosotros`

Resultado real:

- scouts completados
- `lead_intake` ejecutado con éxito a nivel LLM
- `lead_intake` marcado como `failed`

Causa raíz:

- `placeholder_gate_failed`
- razón persistida: `Placeholder detected: placeholder`

Efecto:

- el sistema llegó a crear plan y tareas
- pero la fase raíz quedó fallida
- el resto de fases quedaron bloqueadas

Resumen de tareas persistidas:

- `3 completed`
- `1 failed`
- `28 pending`

Conclusión:

La primera run quedó en un estado híbrido malo:

- suficiente estado como para contaminar la continuidad
- pero sin progreso productivo real

## 2. `CHAT-1F789CCB`

Resultado real:

- scouts completados
- `lead_intake` completado correctamente
- `plan_research` bloqueado casi instantáneamente

Resumen de tareas persistidas:

- `4 completed`
- `1 blocked`
- `27 pending`

Hallazgo clave:

El `lead_intake` sí generó un plan útil.

Quedó visible en:

- `runtime/context/chats/CHAT-1F789CCB.json`
- `workflow_state_entries`
- `session_index.jsonl`

Pero no se guardó como archivo del proyecto.

## 3. `CHAT-28015BB0`

Esta run no fue un nuevo intento limpio.

Fue una continuación explícita de:

- `CHAT-1F789CCB`

Se observa en eventos:

- `continuation_requested: true`
- `continuation_of: CHAT-1F789CCB`

Resultado real:

- scouts completados
- `lead_intake` completado
- `plan_research` bloqueado otra vez
- la run termina clasificada como `simulated`
- evidence gate falla aguas abajo

Resumen de tareas persistidas:

- `4 completed`
- `1 blocked`
- `12 pending`

Conclusión:

La tercera run no resolvió la causa anterior.
Solo reintentó la capa de intake y volvió a chocar en el mismo punto estructural.

---

## Qué sí hizo el sistema

Es importante separar percepción de ejecución real.

El sistema sí hizo estas cosas:

- lanzó scouts
- recopiló contexto
- ejecutó `lead_intake`
- hizo peer consultation
- creó un plan interno
- persistió estado en SQLite
- creó tareas de fases y delegaciones
- abrió sandboxes para algunas fases

Es decir:

- **no es que no hiciera nada**

El problema es otro:

- hizo mucho trabajo interno
- pero no consiguió convertirlo en trabajo productivo visible

---

## Qué no hizo

No hizo ninguna de estas cosas:

- crear archivos del juego
- crear un plan Markdown visible en la raíz del proyecto
- crear estructura mínima de producto
- ejecutar `build`
- producir artefactos verificables fuera del runtime
- completar `review`
- completar `qa`
- cerrar la run con entrega real

---

## Causa inmediata del bloqueo actual

La causa inmediata observable de las dos últimas runs está en:

- `CHAT-1F789CCB::plan_research`
- `CHAT-28015BB0::plan_research`

Ambas quedaron bloqueadas con:

- `specialist_quorum_not_met`

Y el evento detonante es este:

- `specialist_prefetch_failed`
- `specialist = context_curator`
- `reason = no_eligible_adapter`

Secuencia exacta en `CHAT-28015BB0`:

1. `lead_intake` completa y crea plan
2. se crea `plan_research`
3. `plan_research` arranca
4. el prefetch del especialista `context_curator` intenta ejecutarse
5. el router devuelve `no_eligible_adapter`
6. quorum `any` no se cumple
7. `plan_research` queda `blocked`
8. `build/review/qa/lead_close` quedan sin camino real

---

## Hallazgo importante: inconsistencia no reproducida en frío

La parte más delicada de esta auditoría es esta:

Reproduciendo en frío el routing actual para:

- rol `scout`
- `required_capabilities={"analysis"}`
- `tool_specialist="context_curator"`
- `preferred_tool_tier="budget_api"`
- `environment="dev"`

el router **sí encuentra adapters elegibles**.

Eso significa que el `no_eligible_adapter` observado en la run:

- no coincide con la reproducción fría del estado actual

Las explicaciones plausibles son estas:

1. el proceso live que atendió la run tenía estado/config distinto al que hoy reproduce `build_default_orchestrator`
2. hubo una condición transitoria no visible en la reproducción offline
3. existe una inconsistencia/bug en la ruta live del prefetch que no aparece al reconstruir el router fuera de la ejecución original

Conclusión prudente:

- el bloqueo de `plan_research` es real
- la causa observable es `context_curator/no_eligible_adapter`
- pero la causa raíz exacta de ese `no_eligible_adapter` sigue siendo **inconsistente** y debe tratarse como bug abierto de routing live/prefetch

No debe cerrarse como “ya entendido” hasta reproducirlo de forma determinista.

---

## Por qué hay tantas tareas pendientes

No son solo “tareas inútiles”.

Hay tres tipos mezclados:

### 1. Tareas de fases principales no ejecutadas

Ejemplos:

- `build`
- `review`
- `qa`
- `lead_close`

Quedan pendientes porque dependen de `plan_research`, que quedó bloqueada.

### 2. Tareas delegadas creadas demasiado pronto

Ejemplos:

- `delegate_build_test_runner_0`
- `delegate_build_repo_scout_1`
- `delegate_qa_test_runner_0`

Se crean por adelantado como parte del evidence plan, aunque la fase padre todavía no haya demostrado viabilidad.

Eso produce inflación de backlog visual.

### 3. Arrastre de continuidad entre runs

La tercera run hereda contexto y snapshot de la segunda.

Eso mezcla:

- deuda previa
- tareas todavía pendientes
- expectativas de continuación

Sin una separación visual fuerte entre:

- `pending`
- `blocked`
- `carried_over`

el usuario ve un volumen de tareas que parece caótico.

---

## Por qué no se creó un plan visible

El plan sí existe.

Se observa en:

- `CHAT-1F789CCB::lead_intake`
- `CHAT-28015BB0::lead_intake`
- `workflow_state_entries`
- `runtime/context/chats/*.json`

Lo que falta es persistencia de producto.

El sistema hoy trata el plan como:

- estado interno
- resumen de run
- contexto curado

No como:

- artefacto del proyecto

Por eso el usuario no encuentra:

- `PROJECT_PLAN.md`
- `docs/aiteam/plan-*.md`
- `planning/plan-*.md`

Conclusión:

No es que el sistema no planifique.
Es que **planifica sin materializar el plan como archivo del proyecto**.

---

## Por qué no se creó código ni archivos del videojuego

Porque `build` nunca empezó.

El flujo se cortó antes:

- `lead_intake` sí
- `plan_research` no
- `build` nunca entró

Sin `build`, el sistema no alcanza la parte donde tendría sentido:

- crear archivos
- escribir código
- producir assets

Además, en un proyecto vacío no existe hoy un scaffold mínimo de salida temprana como:

- plan visible
- brief
- TODO técnico
- estructura inicial de proyecto

Eso empeora la percepción de “no ha hecho nada”.

---

## Qué sobra hoy en el flujo

## 1. Continuación demasiado automática

La continuidad es útil, pero en este caso arrastró deuda de runs malas.

Falta:

- opción clara entre `nuevo intento limpio` y `continuar`

## 2. Delegación masiva demasiado temprana

Se crean demasiadas tareas de evidencia antes de que la fase principal demuestre viabilidad.

Eso genera:

- ruido
- backlog inflado
- falsa sensación de progreso

## 3. Evidence/policy demasiado aguas abajo

La run acaba recibiendo señales de:

- `evidence_gate_failed`
- `simulated`
- `low_productivity`

cuando el problema real ya había ocurrido antes.

Eso añade ruido diagnóstico.

## 4. Runtime visible como única huella del proyecto

Para un usuario externo, ver solo `runtime/` es una UX incorrecta.

---

## Qué falta

## 1. Plan persistido como archivo del proyecto

Imprescindible.

## 2. Separación entre estado interno y producto

Idealmente:

- `.aiteam/` para estado interno
- raíz del proyecto para artefactos reales

## 3. Clasificación visual de tareas

La UI debe separar:

- `pending`
- `blocked`
- `carried_over`
- `not_started_because_parent_blocked`

## 4. Política de arranque para proyectos vacíos

En un proyecto vacío, el sistema debería poder entregar aunque todavía no haya build:

- plan
- brief técnico
- stack propuesto
- estructura mínima

## 5. Investigación dura del bug live de `context_curator`

Este es el bug técnico bloqueante más claro que sigue abierto.

---

## Causas raíz priorizadas

## Causa raíz 1 — falso positivo inicial del placeholder gate

La primera run útil quedó contaminada por una falla artificial.

Impacto:

- generó deuda de continuidad
- creó tareas sin cierre limpio

## Causa raíz 2 — bloqueo temprano de `plan_research` por prefetch/quorum

El flujo de planning no tolera bien un fallo del especialista previo.

Impacto:

- impide llegar a `build`
- deja la run muerta muy pronto

## Causa raíz 3 — el sistema produce estado interno antes que artefactos visibles

Impacto:

- el usuario percibe “mucho ruido y nada útil”

## Causa raíz 4 — continuidad sin higiene suficiente entre runs fallidas

Impacto:

- arrastre de tareas viejas
- confusión sobre qué pertenece a cada intento

## Causa raíz 5 — el proyecto externo no tiene todavía una gramática de artefactos

Faltan convenciones visibles para:

- plan
- instrucciones del proyecto
- artefactos de producto

---

## Recomendaciones para backlog posterior

No son fixes aplicados. Son direcciones recomendadas.

### R1. Investigar y reproducir de forma determinista el `context_curator/no_eligible_adapter`

Debe tratarse como bug de alta prioridad.

### R2. No crear evidencia delegada masiva antes de que la fase padre arranque de verdad

Reduciría mucho el ruido de tareas.

### R3. Persistir siempre un plan visible en proyectos externos

Aunque luego falle `build`, debe quedar al menos:

- un plan utilizable
- un artefacto visible del proyecto

### R4. Añadir opción explícita `nuevo intento` vs `continuar`

La continuidad no debe ser implícita cuando la run anterior terminó mal.

### R5. Mover el runtime interno de proyectos externos a `.aiteam/`

Y reservar la raíz del proyecto para artefactos del producto.

### R6. Soportar `.aiteam/instructions.md` por proyecto

Para que el Lead tenga instrucciones persistentes del proyecto externo sin colisionar con archivos de proveedor.

### R7. Mostrar mejor el estado real de las tareas

No basta con `pending`.

---

## Conclusión final

`test_aiteams` no está fallando porque “los agentes no hagan nada”.

Está fallando por una combinación de:

- una primera run contaminada por un guardrail agresivo
- un bloqueo muy temprano del planning por prefetch/quorum
- una continuidad que arrastra deuda
- un sistema que guarda demasiado estado interno y muy pocos artefactos visibles

El resultado es un sistema que internamente se mueve, delibera y planifica,
pero externamente parece no entregar.

Ese gap de producto es real y queda suficientemente demostrado por esta auditoria.
