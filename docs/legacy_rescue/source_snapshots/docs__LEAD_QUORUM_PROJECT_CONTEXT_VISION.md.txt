# Visión objetivo — quorum del Team Lead, planes persistidos y `.aiteam/instructions.md` por proyecto

Fecha: `2026-04-02`
Estado: ✅ IMPLEMENTADO (2026-04-02) — B8a/B8b/B8c completos. Suite: `823 passed`.
Relacionado con:

- `docs/HISTORY.md` (bloque 3)
- `docs/ROUTING_EDITOR_VISION.md`
- `docs/EXTERNAL_PROJECT_RUNTIME_GAPS.md`
- `docs/ARCHITECTURE_PLAN.md`

## Propósito

El sistema no debe limitarse a ejecutar una run productiva.

También debe poder:

- planificar muy bien antes de ejecutar
- guardar ese plan como un artefacto real del proyecto
- respetar instrucciones persistentes específicas del proyecto

La idea es que el Team Lead pueda trabajar de dos formas:

- `Plan`: planifica en solitario
- `Plan/Quorum`: planifica con consultores de alto nivel antes de arrancar la run productiva

Y que el resultado no desaparezca en el runtime interno, sino que quede como parte visible y útil del proyecto.

---

## 1. Modo opcional `Plan/Quorum`

## Qué es

`Plan/Quorum` debe ser una opción explícita del sistema para planificación de alta calidad.

En este modo:

- sigue existiendo un `team_lead` principal
- el Lead mantiene la última palabra
- el Lead organiza la deliberación
- pero el Lead no planifica solo

Se le asignan uno o más **consultores avanzados**.

Ejemplo deseado:

- `team_lead`: GPT-5.4
- `lead_consultant_1`: Claude Sonnet
- `lead_consultant_2`: Gemini Pro

No son peers genéricos ni especialistas de ejecución. Son **consultores del Lead**.

## Para qué sirve

Debe usarse cuando la calidad del plan importa más que la latencia:

- arquitectura
- roadmap
- diseño de producto
- arranque de proyecto externo
- decisiones difíciles de alcance o secuencia
- runs donde un mal plan inicial dispara mucho coste posterior

## Qué no debe ser

No debe convertirse en:

- tres leads peleando sin jerarquía
- una pseudo-democracia donde el Lead pierde soberanía
- un reemplazo de la run productiva
- una consulta opaca sin evidencia de qué aportó cada consultor

El Lead sigue mandando. El quorum existe para **mejorar el plan**, no para desdibujar el rol.

---

## 2. Flujo ideal de `Plan/Quorum`

La secuencia ideal debe ser esta:

### Fase 1 — Ingesta común

Los tres modelos reciben el mismo input base:

- petición del usuario
- contexto del proyecto
- estado actual del workspace
- `.aiteam/instructions.md` del proyecto
- restricciones activas
- historial/plans previos relevantes

### Fase 2 — Razonamiento inicial independiente

Cada uno:

- razona por separado
- propone enfoque
- identifica riesgos
- detecta huecos de información
- propone fases o entregables

Sin contaminarse todavía unos con otros.

### Fase 3 — Puesta en común

Cuando los tres acaban:

- el Lead ve las tres propuestas
- los consultores ven las demás
- se abre una fase de reunión/deliberación

### Fase 4 — Reunión estructurada

La reunión debe converger sobre:

- objetivo real
- supuestos
- dudas abiertas
- plan recomendado
- secuencia de fases
- criterios de done
- riesgos
- artefactos esperados

### Fase 5 — Resolución final del Lead

El Lead emite el plan final.

Debe quedar claro:

- qué decidió el Lead
- qué aceptó de los consultores
- qué descartó
- por qué

### Fase 6 — Persistencia del plan

Antes de arrancar la run productiva:

- el plan se guarda como archivo del proyecto
- la run productiva referencia ese plan

---

## 3. Qué debe producir `Plan/Quorum`

El resultado no debe ser solo texto de chat.

Debe generar un artefacto de proyecto legible y reutilizable.

Contenido mínimo:

- resumen ejecutivo
- objetivo
- contexto asumido
- alcance
- no-alcance
- fases propuestas
- entregables esperados
- riesgos
- dependencias
- decisiones abiertas
- criterio de éxito
- origen del plan (`lead_only` o `lead_quorum`)
- modelos participantes
- fecha

## Formato esperado

Por defecto:

- Markdown estructurado

Con encabezados reales:

- `#`
- `##`
- `###`

Y con metadatos legibles al principio.

---

## 4. Dónde debe guardarse el plan

El plan debe guardarse en la raíz lógica del proyecto como un archivo más del proyecto, no como ruido escondido en el runtime interno.

## Regla de producto

Los **planes son artefactos del proyecto**.

No son solo estado del sistema.

## Estructura ideal

Si el proyecto ya tiene `docs/`:

- `docs/aiteam/plan-YYYY-MM-DD-<slug>.md`

Si no tiene `docs/`:

- `planning/plan-YYYY-MM-DD-<slug>.md`

Además puede existir un alias estable opcional:

- `PROJECT_PLAN.md`

que apunte al plan vigente o sea el plan vigente consolidado.

## Qué debe evitarse

No debe acabar en:

- `runtime/`
- `.aiteam/runtime/`
- una base SQLite sin representación legible
- un blob JSON que el usuario no identifica como parte del proyecto

## Gestión como archivo real del proyecto

La UI debería tratar estos planes como artefactos visibles:

- ver plan actual
- ver planes anteriores
- marcar uno como vigente
- comparar versiones
- relanzar una run usando un plan existente

---

## 5. `.aiteam/instructions.md` por proyecto como instrucción persistente de equipo

Cada proyecto debería poder tener su propio:

- `.aiteam/instructions.md`

o documento equivalente de instrucciones permanentes para el equipo.

## Qué función cumple

Debe actuar como:

- memoria normativa del proyecto
- instrucciones de estilo y constraints
- acuerdos de trabajo del equipo
- recordatorio de preferencias del usuario

## Qué puede contener

Ejemplos:

- stack preferido
- librerías prohibidas o preferidas
- estilo de UI
- criterios de calidad
- reglas de testing
- convenciones de carpetas
- restricciones de costes
- tono o idioma de documentos
- requisitos de seguridad
- criterio de uso de modelos o providers

## Cómo debe usarlo el sistema

Al iniciar una run, especialmente el Lead, debe incorporar:

- `.aiteam/instructions.md` del proyecto
- plan vigente del proyecto
- historial relevante de decisiones

Y debe dejar visible en la run que ese archivo fue tenido en cuenta.

## Prioridad de instrucciones esperada

La jerarquía deseable es:

1. políticas duras del sistema
2. instrucciones del repositorio del sistema
3. `.aiteam/instructions.md` del proyecto externo
4. plan vigente del proyecto
5. prompt inmediato del usuario

El sistema debe mostrar conflictos cuando una capa contradiga claramente a otra.

## Cómo debería poder evolucionar

Idealmente el equipo puede proponer cambios a `.aiteam/instructions.md` del proyecto, pero con control.

Dos modos razonables:

- `solo sugerencia`: el equipo propone diff y el usuario aprueba
- `gestion asistida`: el equipo puede escribir en una sección gestionada si el usuario lo habilitó

Lo que no conviene:

- que agentes reescriban silenciosamente `.aiteam/instructions.md`
- que mezclen instrucciones históricas y actuales sin trazabilidad

---

## 6. Relación con la futura vista de routing y configuración

La futura vista editable no debería limitarse a roles y modelos aislados.

También debe poder gobernar:

- topología del Lead
- `Lead only` vs `Lead + consultants`
- composición del quorum
- modelo principal del Lead
- modelos consultores
- reglas de uso por modo (`plan`, `plan/quorum`, `probe`, `run productiva`)

## Controles esperados en esa vista

- selector de topología:
  - `Lead solo`
  - `Lead + 1 consultor`
  - `Lead + 2 consultores`
- selección de modelos del quorum
- orden de prioridad entre consultores
- presupuesto máximo para quorum
- trigger de uso:
  - siempre
  - solo planning
  - solo architecture review
  - solo roadmap
  - manual por run

La vista también debe dejar claro:

- qué plan vigente usa el proyecto
- qué `.aiteam/instructions.md` se está leyendo
- qué overrides del proyecto afectan al routing

---

## 7. Errores de producto y arquitectura a evitar

## Sobre `Plan/Quorum`

- no lanzar la run productiva antes de cerrar la deliberación
- no mezclar consultores del Lead con especialistas de ejecución
- no ocultar qué aportó cada consultor
- no hacer que el Lead pierda la última palabra
- no convertir el quorum en algo obligatorio para todo

## Sobre planes persistidos

- no esconderlos en `.aiteam/` como si fueran solo runtime
- no generar archivos irreconocibles
- no sobrescribir automáticamente el plan vigente sin trazabilidad
- no crear planes sin fecha, origen ni contexto

## Sobre `.aiteam/instructions.md` por proyecto

- no ignorarlo silenciosamente
- no dejar que una run use instrucciones de otro proyecto
- no cargarlo sin mostrar que fue aplicado
- no permitir ediciones invisibles o incontroladas

---

## 8. Estética y UX esperadas

La experiencia ideal debe sentirse como una mezcla de:

- panel de estrategia
- centro de control de equipo
- historial operativo

No como un formulario frío.

## La UI debería mostrar

- topología actual del Lead
- consultores configurados
- plan vigente del proyecto
- fecha y origen del plan
- si el plan se generó con quorum
- qué `.aiteam/instructions.md` está activo
- diferencias entre plan vigente y prompt actual

## Principios visuales

- mucha jerarquía visual
- claridad sobre qué es configuración, qué es contexto y qué es resultado
- trazabilidad visible
- lenguaje de sistema serio, no juguetón
- sensación de control, no de magia

---

## 9. Definición de “bien hecho”

Esto estará bien resuelto cuando ocurra todo esto:

- el usuario puede activar `Plan/Quorum` de forma explícita
- el Lead consulta a modelos avanzados antes de la run productiva
- el Lead sigue teniendo la última palabra
- el plan final queda guardado como archivo del proyecto
- ese plan puede reutilizarse, compararse y seguirse
- el proyecto puede tener su propio `.aiteam/instructions.md`
- el sistema muestra que lo ha leído y aplicado
- la futura vista editable puede gobernar también esta topología

Si no se cumplen esas condiciones, la funcionalidad seguiría incompleta aunque exista una versión parcial.
