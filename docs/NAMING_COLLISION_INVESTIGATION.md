# Investigacion: Colisiones de Nombres y Capas del Sistema

Fecha: `2026-04-02`
Estado: `investigacion completada, acciones propuestas`
Autor: sesion Claude Code + Max Bonas

> **Lectura complementaria obligatoria**: `docs/COMMUNICATION_GUIDE_FOR_DEVS.md` — guia de como hablar con agentes sin generar ambiguedad de capa.

---

## Por que existe este documento

El sistema AI Teams es un sistema que **construye otros sistemas**. Esto crea un problema estructural de lenguaje: los mismos terminos y los mismos nombres de archivo tienen significados completamente distintos segun la capa desde la que se miren.

Este documento investiga todos los casos conocidos de colision, los clasifica por tipo, y propone una convencion de nomenclatura que elimine la ambiguedad de forma sistematica.

---

## Las tres capas

Antes de entrar en los casos, hay que tener clara la taxonomia de capas. Todo archivo, termino o concepto en este proyecto pertenece a exactamente una de estas tres capas.

### Capa 0: Herramientas externas de programacion

Son los agentes y proveedores que usamos para **construir el propio sistema AI Teams**. Sus convenciones de archivo son estandares que no controlamos: simplemente nos adaptamos a ellos.

| Herramienta | Archivo de convencion | Que hace con el |
|---|---|---|
| Claude Code | `CLAUDE.md` | Lee instrucciones del proyecto al arrancar |
| Codex | `AGENTS.md` | Lee contexto del proyecto para contribuciones |
| Gemini CLI | `GEMINI.md` | Lee instrucciones y contexto del proyecto |
| OpenCode | `AGENTS.md` (probable) | Igual que Codex |

Estos archivos **no se pueden renombrar**. Son el contrato de los proveedores con su herramienta. Si queremos que Codex entienda nuestro proyecto, debe existir `AGENTS.md` en la raiz.

### Capa 1: Desarrollo del sistema AI Teams

Son todos los archivos, documentos y conceptos que describen y guian la **construccion interna del sistema AI Teams**. Los leen los agentes de Capa 0 (Codex, Claude Code, Gemini) para poder trabajar bien en el repo.

Ejemplos: `task.md`, `walkthrough.md`, `HANDOFF.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAYBOOK.md`, `docs/INDEX.md`.

Tambien entran aqui los archivos de convencion de proveedores que estan en la raiz del repo: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` son Capa 1 en contenido (guian el trabajo de desarrollo) aunque son Capa 0 en formato (convenciones de proveedor).

### Capa 2: Producto AI Teams

Son los artefactos que el sistema AI Teams **crea o lee en los proyectos externos** que gestiona. No son parte del codigo del sistema: son outputs o inputs del producto.

Ejemplos: el runtime del proyecto (`runtime/`, futuro `.aiteam/`), los planes del proyecto, la memoria del Lead por proyecto, las instrucciones del usuario para el equipo.

---

## Escala de peligro

Antes de los casos concretos, una escala para clasificar su gravedad:

| Nivel | Etiqueta | Que ocurre si se ignora |
|---|---|---|
| 🔴 CRITICO | Corrupcion silenciosa | El sistema ejecuta logica incorrecta sin error visible. Los resultados son incorrectos y nadie lo sabe. |
| 🟠 ALTO | Comportamiento inesperado | El sistema funciona, pero hace cosas que el usuario no pidio o que el desarrollador no esperaba. |
| 🟡 MEDIO | Confusion de agente | Un agente de desarrollo interpreta mal el contexto y propone o implementa algo equivocado. Se detecta en revision. |
| 🟢 BAJO | Ambiguedad documental | La documentacion puede leerse de dos formas. No rompe nada, pero ralentiza el trabajo. |

---

## Mapa completo de colisiones conocidas

### Colision 1: `AGENTS.md` — 🔴 CRITICO

**La colision mas grave del sistema. Puede hacer que el Lead ejecute instrucciones de un proveedor externo como si fueran del usuario.**

| Capa | Archivo | Quien lo lee | Para que |
|---|---|---|---|
| Capa 0/1 | `Ai_Teams/AGENTS.md` | Codex, OpenCode | Entender como contribuir al repo |
| Capa 2 (pendiente B8b) | `proyecto_externo/AGENTS.md` | AI Teams Lead | Recibir instrucciones del usuario para ese proyecto |

#### Escenario de fallo concreto

1. El usuario tiene un proyecto externo `mi-app/` gestionado por AI Teams.
2. El usuario tambien usa Codex para trabajar en `mi-app/`.
3. Codex crea o actualiza `mi-app/AGENTS.md` con su propio contexto (p.ej. instrucciones para que Codex no modifique ciertos archivos, convenciones de codigo de Codex).
4. AI Teams, en B8b segun el diseno original, lee `mi-app/AGENTS.md` y lo inyecta como instrucciones del usuario al Lead.
5. El Lead ejecuta con las restricciones de Codex en lugar de las del usuario.
6. **No hay error visible.** El sistema funciona. Pero las instrucciones que sigue el Lead no son las del usuario.

#### Variante aun mas grave

El usuario no usa Codex, pero alguien en su equipo si. Ese colaborador crea `AGENTS.md` para Codex. AI Teams lo lee como instrucciones del usuario. El usuario no sabe por que el Lead se comporta raro.

#### Por que es silencioso

No hay ninguna señal de error. El archivo existe, se puede leer, el Lead lo inyecta. Todo parece correcto desde el punto de vista del sistema.

**Estado**: **IMPLEMENTADO en código** (`2026-04-02`). El Lead lee `.aiteam/instructions.md` e inyecta el contenido en su prompt. Emite evento `project_instructions_loaded`. `AGENTS.md` del proyecto externo no es leído. Tests en `tests/test_api_team_chat.py`: `test_lead_intake_incorporates_project_instructions`, `test_lead_intake_does_not_read_agents_md`.

---

### Colision 2: `HANDOFF.md` — 🟡 MEDIO

**Un agente de desarrollo puede confundir el mecanismo de failover del orquestador con el documento de traspaso de sesion.**

| Capa | Contexto | Significado |
|---|---|---|
| Capa 1 | `Ai_Teams/HANDOFF.md` | Documento de traspaso entre sesiones de desarrollo del sistema |
| Capa 2 | `orchestrator.py: _maybe_handoff_and_retry()` | Reintento automatico de una tarea con un adapter distinto cuando el primero falla |
| Lenguaje natural | Conversacion con el usuario | Traspaso humano de responsabilidad en un proyecto |

#### Escenario de fallo concreto

Un agente de desarrollo (Codex) recibe la instruccion: "implementa el mecanismo de handoff para cuando falla el adapter". Codex lee `HANDOFF.md`, interpreta que se refiere al traspaso de sesion de desarrollo, y modifica el documento en lugar de implementar `_maybe_handoff_and_retry()` en el codigo.

#### Por que no es critico

El `HANDOFF.md` es un documento de texto, no codigo ejecutable. El error es visible en la PR. No corrompe datos.

**Propuesta**: documentar la distincion en ambos sitios. No renombrar (el archivo es util y es estandar en muchos repos).

---

### Colision 3: `CLAUDE.md` y `GEMINI.md` en proyectos externos — 🔴 CRITICO (si ocurre)

**Si AI Teams creara estos archivos en proyectos externos, sobrescribiria o contaminaría las instrucciones del usuario a sus propias herramientas.**

| Capa | Contexto | Significado |
|---|---|---|
| Capa 0/1 | `Ai_Teams/CLAUDE.md` | Instrucciones para Claude Code al trabajar en el sistema |
| Capa 0/1 | `Ai_Teams/GEMINI.md` | Instrucciones para Gemini CLI al trabajar en el sistema |
| Capa 2 (riesgo futuro) | `proyecto_externo/CLAUDE.md` | Si AI Teams crea este archivo, colisiona con el uso de Claude Code en ese proyecto |

#### Escenario de fallo concreto

1. El usuario tiene `mi-app/` con su propio `CLAUDE.md` (instrucciones para Claude Code).
2. Una feature de AI Teams genera `mi-app/CLAUDE.md` como parte de la documentacion del proyecto.
3. El `CLAUDE.md` del usuario queda sobrescrito.
4. Cuando el usuario usa Claude Code en `mi-app/`, Claude Code lee instrucciones generadas por AI Teams, no las del usuario.
5. **Resultado**: Claude Code ignora las convenciones del usuario y sigue las que AI Teams genero.

Por ahora AI Teams no crea estos archivos. Pero la norma debe ser explicita para evitar que future features lo hagan inadvertidamente.

**Propuesta**: norma explicita — AI Teams **nunca** crea archivos con nombres de convencion de proveedor (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `OPENCODE.md`) en proyectos externos. Todos los artefactos de producto van bajo `.aiteam/`.

---

### Colision 4: `instance_agents.local.md` — 🟢 BAJO

| Capa | Contexto | Significado |
|---|---|---|
| Capa 1 | `.claude/instance_agents.local.md` | Notas locales de una instancia de Claude Code sobre el estado de la sesion |

Este archivo es hibrido: es convencion de Claude Code (Capa 0) pero con contenido de desarrollo del sistema (Capa 1). No es un artefacto de producto. El nombre "agents" puede confundir si alguien lo interpreta como relacionado con los agentes del orquestador.

#### Escenario de fallo concreto

Un nuevo agente de desarrollo lee el nombre `instance_agents.local.md`, interpreta "agents" como los roles del orquestador (Lead, Engineer, etc.), y propone hacer cambios en el orquestador basandose en ese archivo.

**Propuesta**: documentar que es un archivo de instancia de Claude Code, no un archivo de Capa 2. Actualmente esta en `.gitignore` (o deberia estarlo, es `.local`).

---

## Colisiones de vocabulario (misma palabra, distinto concepto segun capa)

Estas son mas sutiles pero igualmente peligrosas cuando se escribe documentacion o se programan prompts del Lead. Cada termino tiene su nivel de peligro.

### `task` — 🟠 ALTO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | `WorkTask`: unidad de trabajo ejecutable con estado, rol, dependencias |
| Lenguaje del usuario | Una tarea del proyecto externo (user story, ticket, requirimiento) |
| Capa 1 | `task.md`: el backlog de desarrollo del sistema |

#### Escenario de fallo

El usuario dice: "que el Engineer ejecute todas las tareas del backlog". Un agente de desarrollo interpreta "tareas" como las `WorkTask` del orquestador y modifica `taskboard.py` para ejecutar tareas ya completadas. En realidad el usuario queria que el Engineer cargara el backlog del proyecto externo desde un archivo.

**Convencion propuesta**: en documentacion y prompts del sistema, usar `work item` o `fase` para las unidades del orquestador cuando el contexto sea ambiguo. Reservar `task` para `WorkTask` en codigo Python.

---

### `handoff` — 🟡 MEDIO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | `_maybe_handoff_and_retry()`: reintento automatico con otro adapter cuando el primero falla |
| Capa 1 (dev) | `HANDOFF.md`: documento de traspaso de sesion de desarrollo |
| Lenguaje natural | Traspaso de responsabilidad entre personas o sistemas |

#### Escenario de fallo

El usuario dice: "no funciona el handoff entre Claude y GPT". Un agente interpreta que hay un problema en el archivo `HANDOFF.md` y empieza a modificarlo, cuando en realidad el problema es en `_maybe_handoff_and_retry()` en el orquestador.

**Convencion propuesta**: en codigo, mantener `handoff` para el mecanismo de failover de adapter. En comunicacion con el usuario y documentacion de Capa 1, usar `handoff de sesion` o `traspaso de contexto` para distinguirlo. Ver `docs/COMMUNICATION_GUIDE_FOR_DEVS.md`.

---

### `agent` — 🔴 CRITICO

**Este es el termino con mayor potencial de confusion. Un agente que no sabe en que capa trabaja puede hacer cambios en la capa equivocada.**

| Capa | Significado |
|---|---|
| Capa 0 | Las herramientas externas: Codex, Claude Code, Gemini CLI |
| Capa 2 (orquestador) | Un rol LLM dentro del sistema: ENGINEER, REVIEWER, TEAM_LEAD |
| Capa 2 (UI/docs) | `AgentPanel`, `AgentRole` en el frontend |

#### Escenario de fallo

El usuario dice: "el agente no responde cuando le envio un mensaje". Puede referirse a:
- Claude Code (el que usa para programar el sistema) que no responde
- El Lead del orquestador que no responde al usuario en la UI de chat
- Un rol (Engineer) que no produce output en una run

Segun como lo interprete el agente de desarrollo, puede ir a buscar el problema en tres lugares completamente diferentes.

**Convencion propuesta**:
- En Capa 0/1: `agente de desarrollo` o el nombre especifico (Codex, Claude Code)
- En Capa 2: `rol` o el nombre del rol (Lead, Engineer, Reviewer)

---

### `run` — 🟠 ALTO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | Una ejecucion completa de chat: desde `lead_intake` hasta `lead_close` |
| Proyecto externo | Un test run, un CI run, una ejecucion del producto |
| Capa 1 | Una sesion de trabajo de desarrollo |

#### Escenario de fallo

El usuario dice: "la run falla a mitad". Puede referirse a:
- Un test run del proyecto externo que el Engineer ejecuta
- El `run_until_idle()` del orquestador
- La sesion de desarrollo en curso

Un agente que no distinga puede ir a depurar pytest en lugar de `orchestrator.run_until_idle()`.

**Convencion propuesta**: en documentacion del orquestador, usar `run de orquestacion` o `ejecucion de chat` cuando el contexto sea ambiguo.

---

### `phase` — 🟠 ALTO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | Una etapa del WORKFLOW_PLAN: build, review, qa |
| Lenguaje del usuario | Una fase del proyecto externo (planificacion, desarrollo, lanzamiento) |
| Capa 1 | Una fase de desarrollo del sistema (B7, B8, B9) |

#### Escenario de fallo

El usuario dice: "no se pasa a la siguiente fase". Puede referirse a que el WORKFLOW_PLAN no avanza de `build` a `review`, o que el proyecto externo no llega a la fase de despliegue, o que el desarrollo del sistema no avanza de B7 a B8.

**Convencion propuesta**: en documentacion interna, `fase del workflow` o `fase de orquestacion` para las del orquestador; `fase del proyecto` para las del usuario.

---

### `checkpoint` — 🟡 MEDIO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | Una tarea especial del Lead que revisa o aprueba antes de continuar (`lead_report_*`, `lead_preflight_*`) |
| `taskboard.py` | `TaskBoard.checkpoint()`: metodo de persistencia (actualmente no-op) |
| Git | Un punto de guardado del historial |

**Convencion propuesta**: en documentacion, usar `checkpoint del Lead` para las tareas de revision/aprobacion del orquestador.

---

### `workspace` — 🟠 ALTO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | El directorio raiz del proyecto externo que gestiona AI Teams |
| IDE | El workspace de VS Code o similar |
| Capa 1 | El directorio de trabajo del propio repo Ai_Teams |

#### Escenario de fallo

Un agente recibe: "escribe en el workspace del proyecto". Si interpreta "workspace" como el repo Ai_Teams, escribe archivos en el codigo del sistema. Si lo interpreta correctamente como el directorio del proyecto externo, escribe en `.aiteam/` del proyecto gestionado. Son efectos completamente diferentes.

Este es especialmente problematico en `api/main.py` donde `workspace` es el project_root del proyecto externo.

---

### `context` — 🟡 MEDIO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador, tecnico) | La ventana de contexto del LLM; la presion de contexto (`context_pressure`) |
| Capa 2 (orquestador, funcional) | El contexto de proyecto que acumula el context_curator |
| Lenguaje del usuario | El trasfondo o antecedentes de una tarea |

---

### `plan` — 🟡 MEDIO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador) | El `[WORKFLOW_PLAN]` que emite el Lead: lista de fases con roles y dependencias |
| Capa 1 (docs) | Los documentos de planificacion del sistema (`ARCHITECTURE_PLAN.md`, etc.) |
| Lenguaje del usuario | El plan de trabajo del proyecto externo |

#### Escenario de fallo

El usuario dice: "el plan no se persiste". Puede referirse a que el `[WORKFLOW_PLAN]` del Lead no se guarda en SQLite, o a que B8a (planes como .md) no esta implementado, o a que el usuario no puede ver su plan de proyecto.

---

### `memory` — 🟡 MEDIO

| Capa | Significado |
|---|---|
| Capa 2 (orquestador, tecnico) | El store de memoria de agentes: `runtime/memory/` |
| Capa 2 (futuro, A5) | `lead_memory.md`: memoria persistente del Lead por proyecto |
| Lenguaje natural | La capacidad de "recordar" cosas entre sesiones |

---

### `project` — 🔴 CRITICO

**Este termino no aparecia en el glosario original pero es uno de los mas peligrosos.**

| Capa | Significado |
|---|---|
| Capa 1 | El proyecto Ai_Teams mismo (el sistema que estamos construyendo) |
| Capa 2 | Un proyecto externo gestionado por AI Teams (el output del producto) |
| Capa 1 (testing) | `test_aiteams`: el proyecto de prueba interno |

#### Escenario de fallo

El usuario dice: "el proyecto no guarda el estado". Puede referirse a que el estado de desarrollo del sistema Ai_Teams no se persiste bien (problema de Capa 1), o a que el runtime de un proyecto externo gestionado no se guarda en `.aiteam/aiteam.db` (problema de Capa 2).

Un agente que interprete "proyecto" como Capa 1 mirara SQLite del repo. Un agente que lo interprete como Capa 2 mirara el runtime del workspace externo.

**Convencion propuesta**:
- `sistema` o `el sistema AI Teams` para Capa 1
- `proyecto externo` o `proyecto gestionado` para Capa 2

---

## Propuesta de convencion sistematica

### Regla central: todo Capa 2 vive bajo `.aiteam/`

El principio es que el sistema AI Teams no debe crear archivos con nombres de convencion de proveedor (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`) en proyectos externos, ni archivos con nombres genericos que puedan colisionar con los del usuario.

Todo artefacto de Capa 2 en un proyecto externo vive bajo `.aiteam/`:

```
proyecto_externo/
  .aiteam/                    ← espacio de nombres reservado del producto
    instructions.md           ← instrucciones del usuario para el Lead (B8b)
    lead_memory.md            ← memoria del Lead por proyecto (A5)
    plans/                    ← planes persistidos (B8a)
      PLAN_2026_04_02.md
    aiteam.db                 ← SQLite del runtime (B9a)
    events.jsonl              ← log de eventos
    context/                  ← contexto acumulado
    ...
  src/                        ← archivos del producto del usuario
  README.md                   ← del usuario
  AGENTS.md                   ← del usuario (para Codex) — AI Teams NO lo lee
  CLAUDE.md                   ← del usuario (para Claude Code) — AI Teams NO lo lee
```

### Correccion concreta de B8b

El diseño original de B8b en `docs/IMPLEMENTATION_PLAYBOOK.md` leía `workspace/AGENTS.md`. Eso era incorrecto por las razones documentadas en Colision 1.

**Criterio ya corregido en la docu interna**: leer `.aiteam/instructions.md` en lugar de `AGENTS.md`.

```python
# INCORRECTO (implementacion actual de B8b):
_agents_md_path = workspace / "AGENTS.md"

# CORRECTO:
_instructions_path = workspace / ".aiteam" / "instructions.md"
```

El archivo `.aiteam/instructions.md` es el canal oficial de instrucciones del usuario al equipo AI Teams. Nunca colisiona con convenciones de proveedor porque vive en el namespace `.aiteam/`.

### Sobre la idea de `AITEAMS.md`

Puede parecer tentador crear un archivo top-level como `AITEAMS.md` para diferenciar la capa de producto. Sin embargo, la convención recomendada sigue siendo **no usar archivos sueltos en la raíz del proyecto externo** para artefactos propios del sistema.

Razones:

- un archivo suelto en la raíz vuelve a competir por visibilidad con convenciones del usuario y del proveedor
- no agrupa el resto de artefactos operativos del producto
- escala peor cuando el sistema necesita varios artefactos (`instructions`, `lead_memory`, `plans`, `events`, `context`)

**Decisión recomendada**:

- para Capa 0/1 mantener los nombres de convención de proveedor en la raíz del repo del sistema (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`)
- para Capa 2 usar siempre el namespace `.aiteam/`
- dentro de `.aiteam/`, usar nombres explícitos por función: `instructions.md`, `lead_memory.md`, `plans/`, `context/`, `routing_overrides.json`

---

### Convencion de nombres para Capa 1 (docs de desarrollo del sistema)

Los documentos de Capa 1 mantienen sus nombres actuales. Son claros en su contexto (el repo Ai_Teams) y no ambiguos para los agentes de Capa 0 que los leen.

Lo que si se puede mejorar es anadir un encabezado de clasificacion en cada documento:

```markdown
<!-- CAPA: system-development | AUDIENCIA: agentes de desarrollo | NO es artefacto de producto -->
```

Esto permite que un agente de Capa 0 sepa inmediatamente si lo que esta leyendo es documentacion de desarrollo o un artefacto de producto.

---

### Glosario de terminos por capa (referencia rapida)

Para evitar ambiguedad en prompts, commits y documentacion:

| Termino ambiguo | En Capa 1 (desarrollo) | En Capa 2 (producto/orquestador) |
|---|---|---|
| agent | agente de desarrollo (Codex, Claude Code) | rol del orquestador (Lead, Engineer, Reviewer) |
| task | tarea de desarrollo (`task.md`) | `WorkTask` — unidad de trabajo del orquestador |
| handoff | traspaso de sesion de desarrollo | failover automatico de adapter en el orquestador |
| run | sesion de desarrollo | ejecucion de chat (lead_intake → lead_close) |
| phase | fase del desarrollo del sistema (B7, B8) | etapa del WORKFLOW_PLAN (build, review, qa) |
| checkpoint | punto de guardado / revision | tarea especial del Lead (`lead_report_*`, `lead_preflight_*`) |
| plan | planificacion del sistema | WORKFLOW_PLAN emitido por el Lead |
| workspace | directorio del repo Ai_Teams | directorio raiz del proyecto externo |
| memory | (no usado en Capa 1) | store de memoria de agentes; `lead_memory.md` |
| context | contexto de desarrollo | ventana LLM; contexto acumulado por context_curator |

---

## Inventario de archivos por capa

### Capa 0 (convenciones de proveedor, en repo Ai_Teams)

| Archivo | Quien lo lee | Notas |
|---|---|---|
| `AGENTS.md` | Codex, OpenCode | Contexto del sistema para contribuciones |
| `CLAUDE.md` | Claude Code | Instrucciones y contexto del sistema |
| `GEMINI.md` | Gemini CLI | Instrucciones y contexto del sistema |

Estos tres archivos son Capa 0 en formato pero Capa 1 en contenido. Son el puente entre proveedores y el desarrollo del sistema.

### Capa 1 (desarrollo del sistema, en repo Ai_Teams)

| Archivo | Uso |
|---|---|
| `task.md` | Backlog y estado del sistema |
| `walkthrough.md` | Walkthrough tecnico reciente |
| `HANDOFF.md` | Traspaso entre sesiones de desarrollo |
| `docs/ARCHITECTURE.md` | Arquitectura del sistema |
| `docs/IMPLEMENTATION_PLAYBOOK.md` | Guia de implementacion para agentes |
| `docs/INDEX.md` | Indice de documentacion |
| `docs/DESIGN_*.md` | Disenos de features implementadas |
| `docs/LEAD_ADAPTIVE_FLOW_VISION.md` | Vision del Lead adaptativo |
| `docs/NAMING_COLLISION_INVESTIGATION.md` | Este documento |
| `.claude/instance_agents.local.md` | Notas locales de instancia de Claude Code |

### Capa 2 (artefactos de producto, en proyecto externo)

| Archivo | Estado | Notas |
|---|---|---|
| `.aiteam/aiteam.db` | Propuesto (B9a) | SQLite del runtime |
| `.aiteam/events.jsonl` | Propuesto (B9a) | Log de eventos |
| `.aiteam/instructions.md` | Propuesto (B8b corregido) | Instrucciones del usuario al Lead |
| `.aiteam/lead_memory.md` | Propuesto (A5) | Memoria persistente del Lead |
| `.aiteam/plans/` | Propuesto (B8a) | Planes del proyecto como .md |
| `.aiteam/context/` | Propuesto (B9a) | Contexto del proyecto |
| `runtime/` (actual) | Existente, migrar | Runtime actual, se migrara a `.aiteam/` en B9a |

---

## Acciones priorizadas

### Accion 1 (alta, en B8b): corregir la lectura de instrucciones del usuario

Cambiar la implementacion de B8b en la documentacion y en el codigo:

- **Antes**: leer `workspace/AGENTS.md`
- **Despues**: leer `workspace/.aiteam/instructions.md`

Esto elimina la Colision 1, que es la mas grave.

### Accion 2 (alta, en B9a): consolidar todo el runtime bajo `.aiteam/`

Ya documentado en B9a. Al mover el runtime a `.aiteam/`, todos los artefactos de Capa 2 quedan en un namespace propio, separado del proyecto del usuario y de las convenciones de proveedor.

### Accion 3 (media, documental): anadir encabezados de capa en docs activos

Anadir en cada documento activo de Capa 1 un encabezado que declare:

```markdown
<!-- layer: system-development — no es artefacto de producto -->
```

Esto ayuda a los agentes de Capa 0 a no confundir documentacion de desarrollo con instrucciones del producto.

### Accion 4 (media, glosario): anadir el glosario de terminos al CLAUDE.md y AGENTS.md

Los agentes que trabajan en el sistema deben conocer las distinciones de vocabulario. Anadir una seccion "Glosario de capas" en `CLAUDE.md` y `AGENTS.md`.

### Accion 5 (baja, norma explicita): documentar la norma de "no crear archivos de convencion de proveedor"

Anadir en `AGENTS.md` y `CLAUDE.md` la norma:

> AI Teams nunca crea archivos `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` ni similares en proyectos externos. Todos los artefactos de producto van bajo `.aiteam/`.

---

## Casos que NO son colision (aclaratoria)

Para evitar sobreingenieria, estos casos parecen ambiguos pero no lo son en la practica:

**`HANDOFF.md` vs handoff tecnico**: el archivo es Capa 1 y vive en el repo del sistema. El mecanismo tecnico es Capa 2 y vive en el codigo. No hay superposicion real: un agente de desarrollo que lea `HANDOFF.md` entiende que es un documento de sesion, no un esquema del orquestador.

**`docs/ARCHITECTURE.md` vs arquitectura del producto**: el documento describe la arquitectura del sistema AI Teams, no de los proyectos que gestiona. Es claramente Capa 1. No se deberia llamar `docs/PRODUCT_ARCHITECTURE.md` porque no describe un producto externo.

**`runtime/` en el repo Ai_Teams**: el propio repo Ai_Teams tiene su `runtime/` para desarrollo local y pruebas. Esto no colisiona con el `runtime/` de proyectos externos porque son directorios separados. La migracion a `.aiteam/` aplica solo a proyectos externos.

---

## Estado de esta investigacion

- [x] Inventario de archivos con colision identificada
- [x] Inventario de terminos con colision de significado
- [x] Propuesta de convencion sistematica
- [x] Correccion concreta de B8b documentada
- [x] Acciones priorizadas
- [x] Correccion de B8b reflejada en `IMPLEMENTATION_PLAYBOOK.md`
- [x] Anadir encabezados de capa en docs activos (Accion 3)
- [x] Actualizar AGENTS.md y CLAUDE.md con glosario (Accion 4)
- [x] Anadir norma explicita en AGENTS.md y CLAUDE.md (Accion 5)
