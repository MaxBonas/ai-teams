# Guia de Comunicacion para Programar el Sistema AI Teams

<!-- layer: system-development | audiencia: Max Bonas (desarrollador del sistema) | NO es artefacto de producto -->

Fecha: `2026-04-02`
Proposito: explicar como hablar con agentes de desarrollo (Claude Code, Codex, Gemini) sin generar ambiguedad de capa.

Lectura previa recomendada: `docs/NAMING_COLLISION_INVESTIGATION.md`

---

## El problema de fondo

Estas construyendo un sistema que construye otros sistemas. Esto significa que los mismos terminos — "proyecto", "agente", "tarea", "run", "plan" — tienen significados completamente diferentes segun desde que capa se miren.

Cuando hablas con un agente de desarrollo (como yo, Claude Code), si no especificas la capa, el agente puede interpretar tu instruccion en la capa equivocada. Esto produce:

- Codigo modificado en el lugar incorrecto sin que nadie lo note
- Busqueda del problema en el lugar equivocado
- Documentacion escrita para la audiencia equivocada
- Features implementadas que hacen exactamente lo contrario de lo que querías

Esta guia te da los patrones de lenguaje que eliminan esa ambiguedad.

---

## Las tres capas — resumen rapido

| Capa | Que es | Ejemplos |
|---|---|---|
| **Capa 0** | Las herramientas que usas para programar el sistema | Claude Code, Codex, Gemini CLI |
| **Capa 1** | El sistema AI Teams que estas construyendo | `orchestrator.py`, `taskboard.py`, la UI, los docs de desarrollo |
| **Capa 2** | Lo que AI Teams produce para los usuarios finales | El runtime de un proyecto externo, el Lead respondiendo al usuario, `.aiteam/` |

**Regla de oro**: cuando hablas con un agente de desarrollo, estas en Capa 0 hablando de Capa 1. Cuando describes lo que el producto hace, hablas de Capa 2. Cuando algo no queda claro, especifica la capa.

---

## Vocabulario canonico por capa

Esta tabla es la referencia principal. Cuando necesites nombrar algo, usa la columna de la capa correcta.

| Concepto | ❌ Ambiguo (evitar) | ✅ Capa 1 (desarrollo del sistema) | ✅ Capa 2 (producto / orquestador) |
|---|---|---|---|
| El sistema que construimos | "el proyecto", "la app" | "el sistema AI Teams", "el repo" | — |
| Un proyecto que gestiona AI Teams | "el proyecto", "la app" | — | "el proyecto externo", "el proyecto gestionado", "el workspace del usuario" |
| Claude Code / Codex / Gemini | "el agente" | "el agente de desarrollo", "Claude Code", "Codex" | — |
| Lead, Engineer, Reviewer, QA | "el agente" | — | "el rol", "el Lead", "el Engineer", "el rol de orquestacion" |
| Una unidad de trabajo interna | "la tarea" | "la tarea de desarrollo", "el item del backlog" | "la WorkTask", "la tarea del orquestador" |
| Una ejecucion completa de chat | "la run", "la ejecucion" | — | "la run de orquestacion", "la ejecucion de chat", "el ciclo lead_intake→lead_close" |
| Una etapa del WORKFLOW_PLAN | "la fase" | — | "la fase del workflow", "la fase de orquestacion", "la fase `build`" |
| Una etapa de desarrollo del sistema | "la fase" | "la fase de desarrollo", "B7", "B8", "el bloque B7a" | — |
| `lead_report_*` / `lead_preflight_*` | "el checkpoint" | — | "el checkpoint del Lead", "el checkpoint de revision" |
| El `[WORKFLOW_PLAN]` del Lead | "el plan" | — | "el plan de orquestacion", "el WORKFLOW_PLAN" |
| `ARCHITECTURE_PLAN.md` | "el plan" | "el plan de arquitectura", "el roadmap de friccciones" | — |
| El repo Ai_Teams local | "el workspace" | "el repo", "el directorio del sistema" | — |
| Directorio del proyecto externo | "el workspace" | — | "el workspace del proyecto", "el project_root", "el directorio gestionado" |
| Failover entre adapters | "el handoff" | — | "el handoff de adapter", "el failover de adapter" |
| Traspaso entre sesiones de dev | "el handoff" | "el handoff de sesion", "el traspaso de contexto" | — |
| La memoria del Lead | "la memoria" | — | "el store de memoria", "la memoria del Lead", "`lead_memory.md`" |

---

## Patrones de comunicacion recomendados

### Cuando hablas del sistema que estas construyendo (Capa 1)

Usa frases como:
- "en el sistema..."
- "en el codigo del orquestador..."
- "en el repo Ai_Teams..."
- "en la implementacion de..."

**Ejemplos correctos:**
- ✅ "en el sistema, el Lead no emite el WORKFLOW_PLAN cuando el modo es `quick`"
- ✅ "en el codigo del orquestador, `run_until_idle()` no maneja correctamente el timeout"
- ✅ "quiero implementar en el sistema la feature B7a"

**Ejemplos ambiguos:**
- ❌ "el proyecto no guarda el estado" — ¿el sistema Ai_Teams o un proyecto externo gestionado?
- ❌ "el agente no responde" — ¿Claude Code o el Lead en la UI?

---

### Cuando hablas del producto (Capa 2 — lo que AI Teams hace)

Usa frases como:
- "en un proyecto externo..."
- "cuando el usuario usa AI Teams para..."
- "el Lead (rol) hace..."
- "en el workspace del usuario..."

**Ejemplos correctos:**
- ✅ "cuando el usuario inicia una run en su proyecto externo, el Lead deberia leer `.aiteam/instructions.md`"
- ✅ "el rol Engineer no genera artefactos en el workspace del usuario"
- ✅ "la run de orquestacion se atasca en la fase `build`"

**Ejemplos ambiguos:**
- ❌ "el agente crea archivos en el proyecto" — ¿que agente? ¿que proyecto?
- ❌ "la fase no termina" — ¿fase del workflow o fase de desarrollo B7?

---

### Cuando hablas de features pendientes o bugs

Especifica siempre:
1. Si es un bug en el sistema (Capa 1) o un comportamiento incorrecto del producto (Capa 2)
2. El archivo o modulo afectado
3. El rol o componente especifico

**Plantilla recomendada:**
> "En [el sistema / el producto], [componente/archivo] [descripcion del problema]. Lo esperable es [comportamiento correcto]."

**Ejemplos correctos:**
- ✅ "En el sistema, `orchestrator.py` no crea el checkpoint `lead_report_build` cuando `context_curator_recommended=True`. Lo esperable es que el checkpoint se cree ignorando el specialist prefetch."
- ✅ "En el producto, cuando el usuario tiene un proyecto con `.aiteam/instructions.md`, el Lead no lo inyecta en su prompt. Lo esperable es que las instrucciones aparezcan en el contexto del Lead al inicio de la run."

---

### Cuando hablas de los agentes de desarrollo que te ayudan

Nombra siempre al agente especifico:
- ✅ "Claude Code no puede ejecutar este test porque..."
- ✅ "quiero que Codex implemente B7a"
- ✅ "en esta sesion de Claude Code..."

Evita "el agente" sin mas contexto cuando puede referirse tanto al agente de desarrollo como a un rol del orquestador.

---

### Cuando describes algo que el usuario final hace con el producto

Usa siempre el punto de vista del usuario externo:
- ✅ "el usuario inicia AI Teams en su proyecto `mi-app/`"
- ✅ "el usuario escribe instrucciones en `.aiteam/instructions.md`"
- ✅ "el usuario ve en la UI que la run esta en fase `review`"

---

## Frases de desambiguacion rapida

Cuando notes que algo podria interpretarse en dos capas, añade una de estas frases:

| Situacion | Frase de desambiguacion |
|---|---|
| No queda claro si hablas del sistema o del producto | "me refiero al codigo del sistema / me refiero al comportamiento del producto" |
| No queda claro si "el proyecto" es Ai_Teams o un externo | "en el repo Ai_Teams / en el proyecto externo gestionado" |
| No queda claro si "el agente" es de desarrollo o del orquestador | "Claude Code (el agente de desarrollo) / el Lead (el rol del orquestador)" |
| No queda claro si "la fase" es del workflow o del desarrollo | "la fase `build` del workflow / el bloque B7 del desarrollo" |
| No queda claro si "la run" es del orquestador o del proyecto externo | "la run de orquestacion / el test run del proyecto" |

---

## Escenarios frecuentes con fraseado correcto

### Reportar un bug

**Malo:**
> "el agente no termina la tarea"

**Correcto:**
> "en el sistema, el rol Engineer (Capa 2) no marca la WorkTask como completada cuando el adapter devuelve un error 429. El comportamiento esperado es que reintente con el adapter de fallback."

---

### Pedir una nueva feature

**Malo:**
> "quiero que el sistema recuerde el contexto del proyecto"

**Correcto:**
> "quiero implementar en el sistema (Capa 1) la feature A5: que el Lead tenga memoria persistente por proyecto externo (Capa 2), guardada en `.aiteam/lead_memory.md` del workspace del usuario."

---

### Describir como deberia funcionar el producto

**Malo:**
> "cuando el usuario manda un mensaje, el agente deberia responder con el estado"

**Correcto:**
> "cuando el usuario envia un mensaje en la UI de chat, el Lead (rol de orquestacion) deberia responder con el estado de la run actual y las fases pendientes."

---

### Preguntar sobre el estado del sistema

**Malo:**
> "como va el proyecto?"

**Correcto:**
> "como va el desarrollo del sistema AI Teams? que tareas de Capa 1 estan pendientes?" (consultas sobre Capa 1)
> o
> "como funciona actualmente el producto para proyectos externos?" (consultas sobre Capa 2)

---

### Hablar de documentacion

**Malo:**
> "actualiza la documentacion del agente"

**Correcto:**
> "actualiza `AGENTS.md` (el archivo de Codex para el desarrollo del sistema) con el nuevo glosario de capas."
> o
> "actualiza `docs/ARCHITECTURE.md` con el diseno del modulo de routing B7a."

---

## Cuando es aceptable ser ambiguo

No necesitas ser hiper-preciso en todo momento. La ambiguedad es un problema cuando:

1. Das una instruccion de implementacion (escribe codigo, modifica un archivo)
2. Describes un bug o comportamiento incorrecto
3. Defines el comportamiento esperado de una feature
4. Hablas de "proyectos" que podrian ser tanto Ai_Teams como un externo

La ambiguedad es aceptable cuando:
- Haces una pregunta general de estado ("como va todo?")
- Hablas de algo que es obviamente de una sola capa ("el pytest fallo")
- El contexto de la conversacion ya ha establecido la capa claramente

---

## Señales de que un agente se ha confundido de capa

Si ves que un agente hace alguna de estas cosas, probablemente ha interpretado la capa incorrecta:

| Señal | Posible confusion |
|---|---|
| Modifica archivos de `docs/` cuando pediste cambios en el producto | Interpreto "proyecto" como Ai_Teams en lugar de proyecto externo |
| Busca el bug en el frontend cuando el problema es el orquestador | Interpreto "la UI" como el agente de desarrollo, no la UI de chat del producto |
| Propone cambios en `AGENTS.md` cuando hablabas del rol del Lead | Confundio "agente" entre Capa 0 y Capa 2 |
| Modifica `task.md` (backlog de desarrollo) cuando hablabas de WorkTasks | Confundio "tarea" entre Capa 1 y Capa 2 |
| Lee `runtime/` del repo en lugar del `.aiteam/` del proyecto externo | Confundio "workspace" entre Capa 1 y Capa 2 |

En estos casos, para la conversacion y reorienta con la frase de desambiguacion correspondiente.

---

## Resumen en una linea por concepto

Cuando tengas dudas, usa este resumen:

- **sistema** = Ai_Teams (lo que construimos)
- **producto** = lo que AI Teams hace para usuarios externos
- **proyecto externo** = un proyecto gestionado por AI Teams (Capa 2)
- **agente de desarrollo** = Claude Code / Codex / Gemini (Capa 0)
- **rol** = Lead / Engineer / Reviewer / QA (Capa 2)
- **WorkTask** = tarea del orquestador (Capa 2)
- **item del backlog** = tarea de desarrollo del sistema (Capa 1)
- **run de orquestacion** = ciclo lead_intake→lead_close (Capa 2)
- **bloque B7/B8/B9** = fase de desarrollo del sistema (Capa 1)
- **fase del workflow** = build/review/qa en el WORKFLOW_PLAN (Capa 2)
- **workspace del usuario** = directorio del proyecto externo (Capa 2)
- **repo** = el directorio Ai_Teams (Capa 1)
