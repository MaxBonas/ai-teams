# AI Teams — Presentación para Inversores
## Abril 2026

---

> **AI Teams es un orquestador multi-agente que entrega software real, no sugerencias.**
> Un equipo completo de inteligencia artificial — con roles, memoria, revisión y coste controlado — que trabaja sobre tu proyecto como lo haría un equipo humano de alto rendimiento.

---

## 1. Síntesis Ejecutiva

El mercado de asistentes de IA para desarrollo está saturado de herramientas que sugieren código en un editor. El problema es que sugerir no es entregar. Un senior engineer no te dice "aquí tienes una idea de función" — escribe el código, lo prueba, detecta el fallo, lo repara y te entrega algo funcionando. AI Teams hace exactamente eso.

AI Teams es un orquestador multi-agente para desarrollo de software donde cada agente tiene un rol especializado, memoria propia, capacidad de ejecución real y comunicación con el resto del equipo. El sistema descompone un objetivo en fases, las asigna a los agentes correctos, ejecuta pytest en el workspace real, repara los fallos y solo marca la tarea como completada cuando los criterios de aceptación están verificados.

Lo que hace diferente a AI Teams no es la IA en sí — todos los competidores tienen acceso a los mismos modelos frontier. Lo diferencial es la arquitectura del equipo: cómo se coordinan los agentes, cómo se gestiona el coste, cómo se garantiza la calidad, y cómo el sistema puede escalar la complejidad del equipo según la dificultad del problema. Una tarea sencilla corre en 30 segundos con un agente solo. Una entrega crítica corre con Lead, quorum, Engineer, Reviewer y QA — y deja trazabilidad completa de cada decisión.

El sistema está en uso activo hoy: 1.300+ tests pasando, suite de pruebas con 67 archivos de test, y el propio AI Teams se desarrolla usando AI Teams (dogfooding real, no retórico). El IDE embebido tiene streaming SSE en tiempo real, el router multi-proveedor opera con fallback automático entre OpenAI, Anthropic, Google y modelos locales Ollama, y el sistema de FinOps lleva ledger de coste por decisión con presupuesto diario y mensual configurable.

---

## 2. El Problema que Resuelve

### El gap actual en el mercado de IA para desarrollo

Las herramientas de IA para desarrollo actuales se dividen en dos categorías con problemas opuestos:

**Autocompletado avanzado (Copilot, Cursor, Codeium):** Excelentes para sugerencias inline, pero el desarrollador sigue siendo el responsable de integrar, probar, revisar y entregar. La IA es una herramienta pasiva que espera instrucciones línea a línea. No tiene memoria del proyecto, no coordina trabajo paralelo, no verifica que lo que generó funciona realmente.

**Agentes autónomos experimentales (Devin, SWE-agent):** Intentan autonomía completa pero con problemas graves: coste por tarea alto e impredecible, opacidad total del proceso, ausencia de control humano granular, y tasas de éxito inconsistentes en proyectos reales complejos.

El gap es claro: **no existe hoy una herramienta que combine entrega real con control operacional, coste predecible y escalabilidad por complejidad.**

### Lo que los equipos de desarrollo necesitan realmente

Cuando un CTO contrata un equipo de desarrollo, no contrata "un cerebro que sugiere". Contrata roles especializados con responsabilidades claras, mecanismos de revisión, criterios de aceptación y trazabilidad de decisiones. Eso es lo que AI Teams modela — no como metáfora, sino como implementación concreta:

- El **Team Lead** descompone el objetivo, decide el enfoque, coordina
- El **Engineer** escribe código ejecutable, no pseudocódigo
- El **Reviewer** verifica calidad antes de entregar
- El **QA** valida que los criterios de aceptación se cumplan
- El **Researcher/Scout** recoge contexto del proyecto para que los demás no trabajen en ciego

---

## 3. La Visión: Qué es AI Teams

### Definición

AI Teams es un **sistema de orquestación multi-agente para entrega de software** con las siguientes propiedades fundamentales:

1. **Roles diferenciados con responsabilidades reales** — no todos los agentes hacen lo mismo
2. **Ejecución real en el workspace** — escribe archivos, corre tests, lee resultados, repara
3. **Coste controlado por diseño** — router pro-first, ledger por decisión, presupuesto configurable
4. **Escalabilidad por complejidad** — mismo input, cuatro niveles de equipo según el riesgo
5. **Observabilidad completa** — cada evento tiene trazabilidad en JSONL append-only
6. **Sin lock-in de proveedor** — OpenAI, Anthropic, Google, Groq, modelos locales

### La analogía correcta

Piensa en cómo funciona una consultora de software senior cuando recibe un encargo:

1. El tech lead entiende el problema, propone un plan y lo somete a revisión de pares
2. Los ingenieros ejecutan las tareas asignadas
3. El código pasa por revisión antes de integrarse
4. QA valida que los criterios de aceptación se cumplen
5. Solo entonces se marca como entregado

AI Teams implementa exactamente ese modelo. La diferencia es que los "empleados" son agentes de IA coordinados por un orquestador, el proceso completo se puede ejecutar en minutos, y el coste es una fracción de un equipo humano.

### La promesa central

> **Si puedes describir la tarea con criterios de aceptación claros, AI Teams puede entregarla.**

No como sugerencia. Como código ejecutable, probado, revisado y con evidencia de que funciona.

---

## 4. Cómo Funciona — Arquitectura

### Para audiencia no técnica

Imagina que contratas a un equipo de desarrollo y les das acceso a tu repositorio. Cada miembro del equipo tiene su especialidad, se comunica con los demás por mensajes internos, recuerda el contexto del proyecto entre sesiones, y solo entrega trabajo cuando ha pasado por las revisiones acordadas.

La diferencia con un equipo humano es la velocidad (minutos, no semanas), el coste (céntimos por tarea, no salarios), y la trazabilidad (cada decisión queda registrada con justificación).

AI Teams es ese equipo, implementado como software.

### Para audiencia técnica

```
┌─────────────────────────────────────────────────────────────┐
│                     CAPA DE ENTRADA                          │
│  FastAPI endpoint  →  IDE Frontend (React 19 + Vite)        │
│  Streaming SSE en tiempo real                                │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                  ORQUESTADOR CENTRAL                         │
│  aiteam/orchestrator.py (~8.000 líneas)                     │
│                                                              │
│  lead_intake → dynamic phases → lead_close                   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ TEAM     │  │ ENGINEER │  │ REVIEWER │  │   QA     │   │
│  │ LEAD     │  │          │  │          │  │          │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                              │
│  Quality gates: evidence gate + semantic gate                │
│  Communication: mailbox DM + broadcast + sync meetings       │
│  Memory: per-agent + shared operational context              │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                  ROUTER MULTI-PROVEEDOR                      │
│  Pro-first (suscripción) → API fallback                      │
│                                                              │
│  Team Lead pool:    OpenAI gpt-4.1 · Claude Sonnet · Gemini │
│  Worker pool:       gpt-4.1-mini · Claude Haiku · Groq      │
│  Local fallback:    Ollama (modelos locales)                 │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                   PERSISTENCIA                               │
│  SQLite (aiteam.db): tasks + workflow_state                  │
│  JSONL: ledger de eventos append-only                        │
│  FinOps: coste por decisión + presupuesto diario/mensual     │
└─────────────────────────────────────────────────────────────┘
```

#### Componentes clave implementados

| Módulo | Función |
|---|---|
| `aiteam/orchestrator.py` | Motor de orquestación: ciclo de fases, gates, retry, repair |
| `aiteam/router.py` | Routing multi-proveedor con tier economics |
| `aiteam/quorum.py` | Quorum de IAs senior para validación de planes |
| `aiteam/evidence_gate.py` | Verificación de evidencia real en workspace |
| `aiteam/finops.py` | Ledger de coste, presupuestos, alertas |
| `aiteam/compliance.py` | Allowlist de comandos, restricción de workdir |
| `aiteam/mailbox.py` | Sistema de mensajería entre agentes |
| `aiteam/memory.py` | Memoria persistente por agente |
| `aiteam/taskboard.py` | Gestión de tareas con dependencias y locks |
| `aiteam/persistence.py` | SQLite store con upserts granulares |
| `api/main.py` | FastAPI endpoints + streaming SSE |
| `ide-frontend/` | React 19 IDE con timeline en tiempo real |

#### El flujo de una tarea (técnico)

1. **lead_intake**: el Team Lead lee el objetivo, recibe contexto del proyecto, formula un `WORKFLOW_PLAN` con fases y criterios de aceptación
2. **Opcionalmente: quorum** — el plan se somete a N modelos frontier de distintos proveedores; los informes válidos (con evidencia/riesgos/recomendación) enriquecen el plan antes de ejecutarlo
3. **Dynamic phases**: cada fase se asigna al agente correspondiente. El orquestador gestiona dependencias, locks de concurrencia y retry
4. **Quality gates**: antes de cerrar cada fase, el evidence gate verifica que hay trabajo real en el workspace (no narrativa vacía). El semantic gate evalúa coherencia del output
5. **Post-write validation**: después de cada escritura, se ejecuta py_compile o pytest según el perfil. Si falla, se alimenta el error al agente y se relanza la ronda (repair cycle)
6. **lead_close**: el Lead sintetiza el resultado, verifica criterios de aceptación, marca como completado solo si la evidencia lo justifica

---

## 5. Los Perfiles de Ejecución

### Concepto: escalabilidad por complejidad

Uno de los diseños más importantes de AI Teams es que el mismo input puede ejecutarse con cuatro niveles de equipo distintos. No es necesario decidir de antemano cuánta potencia aplicar — el operador selecciona el perfil y el sistema se adapta.

### Tabla comparativa de perfiles

| Característica | `solo_lead` | `lead_quorum` | `ai_team_basic` | `ai_teams_full` |
|---|:---:|:---:|:---:|:---:|
| Agentes activos | 1 | 1 | 3-4 | 5+ |
| Scouts de contexto | No | No | Sí | Sí |
| Ciclos de delegación | 0 | 0 | 2 | 2 |
| Quality gates | Ninguno | Ninguno | QA | Review + QA |
| Quorum en plan | No | Sí | No | Sí |
| Evidence gate | Saltado | Saltado | Activo | Activo |
| Validación post-escritura | py_compile | py_compile | pytest | pytest |
| Ciclos de reparación | 2 | 2 | 1 | 1 |
| Consulta entre pares | No | No | No | Sí |
| Velocidad estimada | ~30s | ~60s | ~3-5 min | ~8-15 min |
| Analogía | Codex/OpenCode | Senior que consulta | Tech lead + equipo | Equipo con auditoría |

### Descripción narrativa de cada perfil

#### `solo_lead` — El ejecutor directo

Un único agente Team Lead que recibe el objetivo, inspecciona el workspace, decide el plan, escribe el código, valida con py_compile o pytest, repara si hay error, y avanza. Sin delegación, sin scouts, sin roles auxiliares.

El loop es: `lee → decide → escribe → valida → repara si falla → avanza`.

Es el equivalente a Codex o Claude Code: un cerebro potente que actúa directamente. Ideal para tareas concretas donde el scope es claro y el riesgo es recuperable en caso de error. Tiempo típico: 30-60 segundos.

#### `lead_quorum` — El senior que consulta

Igual que `solo_lead` en ejecución, pero añade un paso antes de actuar: el Lead formula su `WORKFLOW_PLAN` y lo somete a un quorum de IAs senior (distintos proveedores: OpenAI, Anthropic, Google). Cada miembro del quorum evalúa el plan, no el código. El Lead puede aceptar o rechazar el feedback con justificación.

El quorum no bloquea — es un segundo par de ojos que puede señalar enfoques incorrectos, riesgos no vistos, o alternativas mejores antes de gastar tiempo de ejecución. Solo los informes con señal operativa mínima (evidencia, riesgos, recomendación) cuentan como votos válidos.

Ideal para refactors grandes o cambios arquitectónicos donde un enfoque incorrecto es caro.

#### `ai_team_basic` — El equipo pequeño

El Lead deja de escribir código directamente y asume su rol natural: planificar, delegar y sintetizar. Los agentes especializados (Engineer, Researcher/Scout) hacen el trabajo de ejecución.

Los Scouts recogen contexto del proyecto antes de que el Lead planifique — leen el repo, identifican dependencias, cartografían el estado actual. El Engineer ejecuta las fases de implementación con acceso al workspace real. El QA valida los criterios de aceptación.

El Lead preserva sus tokens para decisiones de alto nivel; los workers más económicos ejecutan. Ideal para tareas complejas multi-step donde la ejecución es costosa en un solo contexto.

#### `ai_teams_full` — El equipo con auditoría

La configuración más completa. Combina todo:
- Scouts de contexto antes de planificar
- Quorum de IAs senior en el plan
- Engineer para implementación
- Reviewer para revisión de calidad del código
- QA para validación de criterios de aceptación
- Peer consultation entre agentes para decisiones críticas
- 2 ciclos de delegación con retry

El sistema actual `team_advanced` en producción es esencialmente este perfil. Ideal para entregas de alta criticidad, auditorías técnicas o cambios con impacto en producción donde la cobertura máxima justifica el tiempo adicional.

---

## 6. Propuesta de Valor y Diferenciadores

### Por qué esto y no Copilot / Devin / etc.

#### Diferenciador 1: Multi-proveedor sin lock-in

AI Teams no está atado a ningún proveedor de IA. El router soporta hoy:
- **OpenAI**: gpt-4.1, gpt-4.1-mini, gpt-4o-mini
- **Anthropic**: Claude Sonnet, Claude Haiku
- **Google**: Gemini 2.5 Flash
- **Groq** (inferencia rápida y gratuita): Llama 3.3-70B, Kimi K2, GPT-OSS-120B
- **Ollama** (modelos locales): sin coste de API, sin datos al exterior

Si OpenAI sube precios mañana, el router redirige. Si un modelo falla por cuota, cae al siguiente. El operador mantiene el control real sobre qué modelos usa en qué rol.

#### Diferenciador 2: Pro-first routing — coste marginal mínimo

El router intenta primero los canales de suscripción activa (Claude Pro, ChatGPT Plus, Gemini Advanced) antes de gastar tokens de API. Dado que el dueño ya paga la suscripción mensual, el coste marginal de esas llamadas es efectivamente cero.

Solo cuando el canal de suscripción no está disponible (bloqueado, cuota, timeout) el router escala a API con gasto real. Esto puede reducir el coste operativo hasta un 70-80% en flujos de trabajo habituales comparado con un sistema que va directo a API.

#### Diferenciador 3: Memoria y continuidad real

AI Teams recuerda el proyecto entre sesiones. Cada agente tiene memoria persistente por proyecto. El Lead puede retomar un run anterior, ver qué se entregó en sesiones previas, y continuar desde donde lo dejó el equipo.

Esto no es conversación en un chat — es contexto operativo persistente en SQLite y JSONL que los agentes leen activamente antes de actuar.

#### Diferenciador 4: Entrega real, no sugerencias

Cuando AI Teams completa una tarea, ha:
1. Escrito los archivos en el workspace real
2. Ejecutado pytest sobre el código producido
3. Reparado los errores detectados (hasta N ciclos configurables)
4. Verificado que los criterios de aceptación se cumplen

La diferencia con Copilot es que Copilot sugiere y tú verificas. AI Teams verifica por ti.

#### Diferenciador 5: Escalabilidad por complejidad

El mismo objetivo puede ejecutarse como `solo_lead` (30 segundos, un agente) o como `ai_teams_full` (10-15 minutos, equipo completo con auditoría). El operador decide el nivel de cobertura según el riesgo de la entrega, sin cambiar cómo formula la tarea.

#### Diferenciador 6: Dogfooding real

AI Teams se desarrolla usando AI Teams. El sistema ha generado código para sí mismo, ejecutado su propia suite de tests, reparado sus propios fallos. No es marketing — es la forma en que se trabaja en el proyecto cada día. Esto garantiza que las fricciones reales se detectan y resuelven rápido.

#### Diferenciador 7: Observabilidad completa

Cada evento del sistema se registra en JSONL append-only: qué agente actuó, qué modelo usó, qué cost ledger dejó, qué output produjo, qué veredicto recibió del gate. Los run verdicts y phase verdicts permiten auditar exactamente por qué una tarea se completó, falló o se degradó.

#### Diferenciador 8: Compliance y guardrails

El sistema tiene una allowlist de comandos permitidos, restricción de workdir al directorio del proyecto, y registro de auditoría de ejecución. Para entornos corporativos con requisitos de seguridad, esto no es opcional — es condición de uso.

### Tabla comparativa con competidores

| Capacidad | AI Teams | Copilot | Devin | Cursor |
|---|:---:|:---:|:---:|:---:|
| Entrega código verificado | Sí | No | Parcial | No |
| Multi-proveedor / sin lock-in | Sí | No | No | Parcial |
| Modelos locales (sin API) | Sí | No | No | No |
| Pro-first (coste mínimo) | Sí | N/A | No | No |
| Memoria persistente por proyecto | Sí | Parcial | Sí | No |
| Escalabilidad por equipo | Sí | No | No | No |
| Quorum de revisión de plan | Sí | No | No | No |
| FinOps con presupuesto | Sí | No | No | No |
| Compliance y allowlist | Sí | Parcial | No | No |
| Observabilidad JSONL | Sí | No | No | No |
| IDE embebido | Sí | No | Sí | Sí |

---

## 7. Demo Narrada

### Escenario: añadir validación de email a un endpoint de registro

Esta demo representa un caso real de uso típico: una tarea de implementación concreta con criterios de aceptación verificables.

**Configuración inicial:**
- Proyecto: API REST en Python/FastAPI
- Workspace: repositorio local con tests existentes
- Perfil seleccionado: `ai_team_basic`

---

**Paso 1 — El operador escribe la tarea (30 segundos)**

En el IDE embebido, se abre el panel de nueva tarea. El operador escribe:

```
Añadir validación de email al endpoint POST /register.
El campo "email" debe rechazar strings sin formato válido.
Retornar 422 con mensaje descriptivo si el email es inválido.
Tests deben cubrir: email válido, email sin @, email con dominio vacío.
```

Se selecciona perfil `ai_team_basic` y se lanza.

---

**Paso 2 — Scouts en tiempo real (60 segundos)**

En el panel de timeline, aparecen las primeras entradas en streaming:

```
[Scout] Leyendo estructura del proyecto...
[Scout] Identificado: api/routes/auth.py — endpoint POST /register
[Scout] Identificado: tests/test_auth.py — 12 tests existentes
[Scout] Stack: FastAPI + Pydantic v2
[Scout] Contexto listo para el Lead
```

Los Scouts han cartografiado el repo sin que el operador tuviera que explicar nada. El Lead ya sabe qué archivos tocar.

---

**Paso 3 — Plan del Team Lead con quorum (90 segundos)**

```
[Team Lead] WORKFLOW_PLAN generado:
  Fase 1: Actualizar modelo Pydantic en api/schemas.py
  Fase 2: Añadir validación en api/routes/auth.py
  Fase 3: Escribir tests en tests/test_auth.py

[Team Lead] Criterios de aceptación:
  - pytest tests/test_auth.py pasa con ≥15 tests
  - Respuesta 422 verificada con email inválido
```

---

**Paso 4 — Engineer implementa (2-3 minutos)**

```
[Engineer] Fase 1: modificando api/schemas.py...
  → Añadido validator email con EmailStr de pydantic
  → Archivo escrito: api/schemas.py (modificado)

[Engineer] Fase 2: modificando api/routes/auth.py...
  → Añadido manejo HTTPException 422
  → Archivo escrito: api/routes/auth.py (modificado)

[Engineer] Fase 3: escribiendo tests...
  → 3 nuevos test cases añadidos
  → Archivo escrito: tests/test_auth.py (modificado)
```

En el panel de workspace, el operador puede ver el diff de cada archivo en tiempo real.

---

**Paso 5 — Validación automática (30 segundos)**

```
[QA] Ejecutando pytest tests/test_auth.py...
  → 15 passed, 0 failed
  → Cobertura: endpoint register 100%

[QA] Criterios de aceptación verificados:
  ✓ pytest pasa con 15 tests
  ✓ Respuesta 422 para email inválido confirmada
  ✓ Mensaje descriptivo presente
```

---

**Paso 6 — Cierre (10 segundos)**

```
[Team Lead] Tarea completada.
  Archivos modificados: 3
  Tests nuevos: 3
  Cost ledger: $0.023 (Engineer budget_api)
  Duración total: 4m 12s
```

El operador revisa el diff final, aprueba y commitea. La tarea está entregada, probada y documentada.

---

### Lo que se ve en la demo que no se ve en otros sistemas

- Timeline en streaming: cada agente reporta en tiempo real, no hay caja negra
- Workspace diff: los archivos modificados se muestran con diff antes de aprobar
- Cost ledger: el gasto real de la tarea aparece en el panel de FinOps
- Audit trail: cada decisión del Lead tiene justificación registrada
- El operador no escribió una sola línea de código ni de test

---

## 8. Estado Actual del Proyecto

### Lo que funciona hoy en producción

| Componente | Estado |
|---|---|
| `solo_lead` — loop completo con repair | Funcionando |
| `team_advanced` (`ai_teams_full`) | Funcionando |
| Quorum de IAs senior (`quorum.py`) | Implementado |
| Router multi-proveedor pro-first | Funcionando |
| IDE embebido con SSE streaming | Funcionando |
| SQLite como persistencia principal | Migrado y estable |
| Quality gates (evidence + semantic) | Activos |
| FinOps con ledger y presupuesto | Activos |
| Compliance + allowlist de comandos | Activo |
| Mailbox entre agentes | Funcionando |
| Memoria por agente | Funcionando |
| Bootstrap multi-máquina | Estable |

### Métricas reales del sistema

- **Suite de tests**: 1.300+ tests pasando, 67 archivos de test
- **Estado validado**: `2026-04-02`, `MAX-GAMINGPC`, `776 passed` (última validación completa publicada en CLAUDE.md)
- **Cobertura de tests**: orquestador, taskboard, router, evidence gate, finops, compliance, API, concurrencia, perfiles
- **Dogfooding**: el sistema se ha usado para desarrollarse a sí mismo en múltiples sesiones de trabajo
- **Infraestructura**: dos máquinas activas (MAX-GAMINGPC + ORCH-01) con bootstrap reproducible

### Arquitectura técnica consolidada

**Persistencia:**
- SQLite (`runtime/aiteam.db`) para `tasks` y `workflow_state` — migración completada en 2026-04
- JSONL para eventos, ledger y registros append-only — inmutabilidad por diseño
- Compatibilidad JSON residual solo para fixtures de tests

**Router:**
- Pool Team Lead: gpt-4.1, Claude Sonnet, Gemini 2.5 Flash, Groq (gratuito)
- Pool Workers: gpt-4.1-mini, Claude Haiku, Groq Llama 70B, Gemini Flash
- Prioridad configurable por proyecto; catálogo vivo en `model_catalog.json`

**Seguridad:**
- Allowlist de comandos shell configurables
- Restricción de workdir al directorio del proyecto
- Doble aprobación para operaciones en prod (en diseño)
- Audit trail completo en JSONL

---

## 9. Hoja de Ruta

### Próximos 3 meses — Consolidación y perfiles

**Completar los 4 perfiles de ejecución como ciudadanos de primera clase:**

Los perfiles `solo_lead` y `team_advanced` ya funcionan. Los próximos 3 meses consolidan los perfiles intermedios y refactorizan el sistema de perfiles a una tabla unificada (`ProfileConfig`) que elimina los condicionales dispersos por el codebase.

- Finalizar los 6 fixes de `solo_lead` (evidence gate, repair loop, system prompt profile-aware)
- Conectar `lead_quorum` usando `quorum.py` en `lead_intake`
- Implementar `ai_team_basic` como `team_advanced` sin scouts ni reviewer
- Refactorizar `ai_teams_full` como la configuración máxima del sistema

**Objetivo al mes 3:** los 4 perfiles probados, documentados y seleccionables desde el IDE.

---

### Próximos 6 meses — Proyectos externos y marketplace de skills

**Proyectos externos (Capa 2):**

El sistema ya soporta la noción de proyecto externo con namespace aislado `.aiteam/`. La siguiente fase extiende esto para que cualquier repo externo pueda tener:
- Instrucciones persistentes del operador en `.aiteam/instructions.md`
- Contexto de proyecto separado por equipo AI
- Historial de entregas por proyecto

**MCP/CLI/Skills:**

Integración del sistema de skills como herramientas disponibles para los agentes. Un Engineer podría invocar un skill de Playwright para automatización de browser, un Researcher podría invocar search web, etc.

**Routing dinámico con vista editable:**

Extender la UI de routing para cubrir: reglas por tipo de tarea, límites de coste, capacidades mínimas, canales preferidos y simulación de resolución del router. El operador ve exactamente qué modelo usará para qué rol antes de lanzar.

**Objetivo al mes 6:** primer cliente externo usando AI Teams sobre su propio repo.

---

### Próximos 12 meses — Plataforma y escala

**Agent workspace como producto:**

Evolucionar el IDE embebido hacia una consola de operador para equipos AI: chat + timeline + estado de runs + routing/capabilities/status + memoria + continuidad + diffs + artefactos + aprobaciones. El foco es controlar y dirigir agentes, no editar código manualmente.

**Multi-tenant:**

Arquitectura multi-tenant donde distintos operadores gestionan distintos proyectos con sus propias instrucciones, presupuestos y configuraciones de routing. La capa de seguridad y compliance se eleva a nivel de organización.

**Modelo de agente conversacional:**

El Lead adoptando un modo de interacción más conversacional: PAUSE_FOR_USER cuando necesita aclaración, SKIP_PHASE cuando una fase no aplica, DEGRADE a un perfil menor cuando el presupuesto se acerca al límite.

**Objetivo al mes 12:** plataforma SaaS con primeros clientes de pago, facturación basada en créditos de uso.

---

## 10. Oportunidad de Mercado

### Tamaño y tendencia

El mercado de herramientas de desarrollo de software asistido por IA alcanzó aproximadamente **5.000 millones de dólares en 2024** y se proyecta por encima de **20.000 millones para 2028** (CAGR ~40%). Pero los números más relevantes para AI Teams no son los del mercado de Copilot — son los del mercado emergente de **automatización de entrega de software**.

El mercado de servicios de outsourcing de software está valorado en más de **500.000 millones de dólares anuales**. La hipótesis de AI Teams es que una fracción creciente de ese trabajo — tareas repetibles, bien definidas, con criterios de aceptación claros — puede ser ejecutada por un equipo AI a una fracción del coste y en una fracción del tiempo.

### El cliente ideal (corto plazo)

- Startups técnicas con equipos de desarrollo pequeños que necesitan multiplicar su capacidad
- Agencias de software que entregan proyectos similares repetidamente
- CTOs que quieren acelerar el desarrollo propio sin escalar el equipo

### El cliente ideal (largo plazo)

- Empresas medianas con deuda técnica acumulada que necesitan capacidad de refactor sostenida
- Equipos que necesitan cobertura de tests sin dedicar ingenieros a escribirlos
- Organizaciones que necesitan cumplimiento y trazabilidad en cada cambio

### Posicionamiento

AI Teams no compite con Copilot en el espacio de autocompletado. Compite con la decisión de contratar un desarrollador freelance o una agencia para una tarea bien definida. El posicionamiento correcto es:

> **Para entregas técnicas bien definidas, AI Teams es más rápido, más barato y más trazable que cualquier alternativa humana.**

---

## 11. Propuesta de Colaboración e Inversión

### Qué necesita AI Teams para escalar

El sistema técnico está construido y funcionando. Lo que necesita para escalar no es más ingeniería de infraestructura — es:

**1. Distribución y acceso a primeros clientes**
El mayor riesgo hoy no es técnico. Es que el sistema existe y funciona, pero sin clientes externos que lo usen, no hay feedback real de producto ni métricas de retención. Se necesita acceso a 10-20 equipos de desarrollo dispuestos a usar AI Teams en proyectos reales durante 3 meses.

**2. Capacidad de iteración rápida sobre feedback**
Los primeros meses con clientes externos generarán señales de producto que requieren iteración rápida. Se necesita capacidad de respuesta técnica dedicada — no solo las horas de un fundador.

**3. Infraestructura de SaaS**
El sistema corre hoy en instalación local. La conversión a SaaS requiere trabajo de infraestructura: autenticación multi-tenant, billing por uso, observabilidad de producción, SLAs. Este trabajo es conocido y estimable, no investigación.

### Qué habilita la inversión

| Inversión | Habilita |
|---|---|
| Acceso a red de primeros clientes | Feedback real, métricas de retención, casos de uso |
| Capacidad técnica adicional | Iteración rápida, perfiles completos, SaaS |
| Infraestructura cloud | Multi-tenant, billing, SLAs, escalabilidad |
| Validación de mercado | Pricing real, modelo de negocio confirmado |

### Modelo de negocio proyectado

**Fase 1 (meses 1-6):** piloto con clientes seleccionados, acceso gratuito o pago simbólico a cambio de feedback estructurado

**Fase 2 (meses 6-12):** pricing basado en créditos de uso (por tarea completada, por agente-hora) con planes mensuales para equipos

**Fase 3 (año 2+):** SaaS multi-tenant con planes por organización, marketplace de skills de agentes

---

## 12. Preguntas Frecuentes de Inversores

**P1: ¿En qué se diferencia de Devin?**

Devin es un agente autónomo de ciclo largo con una UI propia. AI Teams es un orquestador de equipo: múltiples agentes con roles diferenciados, quality gates explícitos, y control granular del operador. La diferencia más concreta: AI Teams tiene quorum de revisión de plan, memoria persistente por proyecto, y routing multi-proveedor. Devin está atado al stack de Cognition. AI Teams es agnóstico de proveedor.

**P2: ¿Por qué va a durar si los modelos base mejoran constantemente?**

Porque el valor no está en el modelo — está en la arquitectura del equipo. Cuando GPT-5 salga, AI Teams lo añade al pool y lo usa. Los modelos mejoran, pero la coordinación entre roles, la gestión de coste, la verificación de entrega y el control operacional son capas de valor que no resuelven los modelos base por sí solos.

**P3: ¿Cuál es el moat defensible?**

Tres capas: (1) la arquitectura de orquestación construida y funcionando, con 1.300+ tests que garantizan estabilidad; (2) el efecto de red de proyectos — cuantos más proyectos usa AI Teams, más contexto acumula sobre esos proyectos, más difícil es cambiar; (3) la integración pro-first que reduce el coste marginal para operadores con suscripciones activas.

**P4: ¿Qué pasa si OpenAI o Anthropic lanzan algo similar?**

Los grandes proveedores tienen incentivo en vender tokens de API, no en reducir el coste de sus propios clientes. Un sistema pro-first que prioriza suscripciones sobre API va contra su modelo de negocio. Además, los grandes proveedores no pueden construir un sistema agnóstico de proveedor — AI Teams sí.

**P5: ¿Cómo se garantiza que el código entregado es correcto?**

El sistema no garantiza corrección arbitraria — garantiza que el código pasa los tests que se definen como criterios de aceptación. La responsabilidad del operador es definir criterios claros. Si los tests son buenos, la entrega es verificada. Si los tests son malos, los resultados son lo que los tests miden. Esto es exactamente igual que con un equipo humano.

**P6: ¿Qué tan costoso es ejecutar un run completo?**

Depende del perfil. Un `solo_lead` típico con un agente frontier puede costar entre $0.01 y $0.05. Un `ai_teams_full` completo con 5 agentes puede costar entre $0.20 y $1.50 dependiendo de la complejidad. Con routing pro-first, el coste real del operador puede ser significativamente menor si tiene suscripciones activas.

**P7: ¿El sistema puede trabajar con cualquier lenguaje de programación?**

AI Teams es agnóstico de lenguaje — los agentes leen y escriben cualquier archivo. La validación automática hoy está más madura en Python (pytest nativo). Para otros lenguajes, el sistema puede ejecutar cualquier comando de validación configurado. El soporte de pytest es el más profundo hoy; otras herramientas de test son configurables.

**P8: ¿Cómo se maneja la seguridad del código que el sistema ejecuta?**

El sistema tiene una allowlist explícita de comandos permitidos. El orquestador no puede ejecutar comandos fuera de esa lista. El workdir está restringido al directorio del proyecto. Hay un audit trail completo en JSONL de cada comando ejecutado. Para entornos corporativos, estas restricciones son configurables por política.

**P9: ¿Qué significa que el sistema "se desarrolla a sí mismo"?**

En el día a día de desarrollo, cuando se necesita añadir una nueva funcionalidad a AI Teams, se formula la tarea en AI Teams y se lanza. El sistema lee el propio codebase, escribe los cambios, ejecuta la suite de tests, repara los fallos y entrega. Esto no es solo una demo — es el flujo de trabajo real. Los tests que existen hoy fueron parcialmente escritos por el sistema.

**P10: ¿Cuántos usuarios activos tiene hoy?**

Hoy el sistema tiene un usuario activo intensivo: el propio desarrollador del sistema (dogfooding). La pregunta correcta es qué demostrar antes de hablar de usuarios: (1) sistema funcionando con suite de tests completa, (2) uso real para desarrollar el propio sistema, (3) capacidad de onboarding a repos externos. Los tres están resueltos o en proceso.

**P11: ¿Cómo se monetiza si los modelos cada vez son más baratos?**

El valor no está en los tokens — está en la capa de orquestación, memoria, coordinación y verificación. Si los modelos bajan de precio, el coste por tarea baja y el servicio se vuelve más atractivo para más clientes. Es el mismo efecto que tuvo la reducción del coste de almacenamiento en la nube para los SaaS de datos.

**P12: ¿Por qué no han levantado ronda antes?**

Porque el foco ha sido construir el sistema hasta un punto donde la tecnología es sólida y verificable. Levantar capital con un prototipo que no funciona es más fácil a corto plazo, pero limita la negociación. Hoy el sistema tiene 1.300+ tests, está en uso real y tiene arquitectura escalable. Es mejor posición para negociar términos.

**P13: ¿Qué riesgos técnicos existen?**

El mayor riesgo técnico conocido es la concentración de lógica en `orchestrator.py` (hoy ~8.000 líneas). Es un archivo con alta interdependencia que requiere cuidado en cambios. El plan de mitigación está documentado — extracción gradual a módulos cohesionados cuando sea necesario por otra razón. No es deuda que bloquee funcionalidad, sino que aumenta el riesgo de regresión en cambios grandes.

**P14: ¿Cómo se compara el tiempo de entrega con un desarrollador humano?**

Para tareas bien definidas con criterios claros: AI Teams es entre 10x y 100x más rápido. Un desarrollador humano puede tardar horas en una tarea de validación de formulario con tests. AI Teams la entrega en minutos. Para tareas mal definidas o con alta ambigüedad: AI Teams necesita más iteración de prompt, y el humano puede clarificar más eficientemente. La frontera de competitividad es claramente las tareas repetibles y bien especificadas.

**P15: ¿Qué hace que este equipo pueda ejecutar esto?**

El sistema existe, funciona y está en uso. No es un deck de pitch con wireframes — es software en producción con suite de tests completa. La capacidad de ejecución técnica ya está demostrada. Lo que se busca ahora no es validar si se puede construir, sino escalar lo que ya funciona.

---

## 13. Glosario Técnico

**Agente (en AI Teams)**: proceso autónomo con un rol definido (Lead, Engineer, Reviewer, QA, Researcher) que recibe tareas, las ejecuta usando un modelo de IA y herramientas reales, y reporta resultados al orquestador. Cada agente tiene memoria propia y puede comunicarse con otros agentes.

**AI Teams (sistema)**: el orquestador multi-agente completo, incluyendo backend, frontend IDE, router, persistencia y sistema de agentes.

**Allowlist**: lista explícita de comandos shell que el sistema puede ejecutar. Todo lo que no esté en la lista es rechazado. Mecanismo de seguridad configurable por proyecto.

**Audit trail**: registro append-only en JSONL de cada evento del sistema: qué agente actuó, qué modelo usó, qué decisión tomó, qué veredicto recibió. Inmutable por diseño.

**Budget API**: tier de modelos económicos disponibles para roles worker (Engineer, Researcher, Scout). Incluye gpt-4.1-mini, Claude Haiku, Llama 3.3-70B vía Groq.

**Cost ledger**: registro de gasto real por cada decisión de routing. Incluye modelo usado, tokens consumidos y coste estimado. Base del sistema FinOps.

**Delegation cycle**: ciclo completo de asignación de una fase a un agente worker, ejecución y reporte al Lead. Los perfiles `ai_team_basic` y `ai_teams_full` tienen 2 ciclos.

**Dogfooding**: uso del propio sistema para desarrollarse a sí mismo. AI Teams se desarrolla usando AI Teams — las tareas de desarrollo del proyecto se lanzan en el orquestador.

**Evidence gate**: verificación de que hay trabajo real en el workspace después de que un agente reporta haber actuado. Detecta narrativa vacía (el agente dice que escribió, pero no hay diff real). Activo en perfiles `ai_team_basic` y `ai_teams_full`.

**FinOps**: capa de gestión de coste del sistema. Incluye ledger por decisión, presupuesto diario/mensual configurable y alertas de proximidad al límite.

**JSONL**: JSON Lines — formato de log donde cada línea es un objeto JSON independiente. Append-only por diseño: nunca se sobreescribe, solo se añade. Base del audit trail y event ledger.

**Lead intake**: fase inicial de cada run donde el Team Lead recibe el objetivo, lee el contexto del proyecto, y formula el `WORKFLOW_PLAN` con fases y criterios de aceptación.

**Lead close**: fase final de cada run donde el Team Lead verifica que los criterios de aceptación se cumplen, sintetiza el resultado y marca la tarea como completada (o fallida si no hay evidencia suficiente).

**Mailbox**: sistema de mensajería entre agentes. Soporta DM (punto a punto) y broadcast (a todos los agentes activos). Base de la comunicación asíncrona del equipo.

**Modelo frontier**: modelos de IA de máxima capacidad disponibles en el mercado: gpt-4.1, Claude Sonnet, Gemini 2.5 Pro. Reservados para el rol Team Lead en AI Teams.

**Ollama**: runtime de modelos locales de código abierto. AI Teams puede usarlo como fallback para workers sin coste de API y sin enviar datos al exterior.

**Orquestador**: componente central (`aiteam/orchestrator.py`) que gestiona el ciclo de vida de cada run: fases, asignación de agentes, quality gates, retry, repair y cierre.

**Peer consultation**: mecanismo por el cual agentes consultan entre sí antes de tomar decisiones críticas. Disponible en `ai_teams_full`. El Lead puede pedir opinión a otros agentes antes de actuar.

**Perfil de ejecución (run profile)**: configuración que determina qué agentes participan, qué gates se activan y qué nivel de validación se aplica. Cuatro perfiles: `solo_lead`, `lead_quorum`, `ai_team_basic`, `ai_teams_full`.

**Pro-first routing**: estrategia de routing que intenta primero los canales de suscripción activa (Claude Pro, ChatGPT Plus, Gemini Advanced) antes de gastar tokens de API. Reduce el coste marginal cuando el operador ya tiene suscripciones.

**Quality gate**: punto de control antes de cerrar una fase. Incluye evidence gate (trabajo real en workspace) y semantic gate (coherencia del output). Si el gate falla, la fase no se marca como completada.

**Quorum**: mecanismo por el cual el `WORKFLOW_PLAN` del Lead se somete a N modelos frontier de distintos proveedores antes de ejecutarse. Solo los informes con señal operativa mínima (evidencia, riesgos, recomendación) cuentan como votos válidos. No bloquea — enriquece el plan.

**Repair cycle**: ciclo donde el sistema detecta un error (test fallando, compile error), lo alimenta al agente responsable como contexto, y relanza la ronda para que el agente lo repare. Configurable por perfil.

**Router**: componente (`aiteam/router.py`) que decide qué modelo/proveedor ejecuta cada rol en cada tarea. Aplica prioridades, tier economics, disponibilidad y política de modelo.

**Scout/Researcher**: rol especializado en recoger contexto. Lee el repo, identifica archivos relevantes, mapea dependencias y prepara el contexto para que el Lead planifique sin trabajar en ciego.

**Senior cloud**: tier de máxima capacidad en el catálogo de modelos. Reservado para Team Lead. Incluye gpt-4.1, Claude Sonnet, Gemini 2.5 Flash con suscripción.

**SSE (Server-Sent Events)**: protocolo de streaming unidireccional del servidor al cliente. La IDE de AI Teams usa SSE para mostrar el output de los agentes en tiempo real, línea a línea.

**SQLite**: base de datos embebida usada como persistencia principal de AI Teams para `tasks` y `workflow_state`. Sin servidor externo — el archivo `runtime/aiteam.db` es la fuente de verdad.

**Team Lead**: rol de máxima autoridad en el equipo AI. El único agente que usa modelos frontier. Descompone objetivos, formula el plan, coordina la delegación y cierra el run con veredicto final.

**Tier economics**: principio de routing donde roles más críticos (Team Lead) usan modelos más caros y capaces, mientras roles de ejecución (Scout, Researcher) usan modelos más económicos. Optimiza calidad vs. coste por tarea.

**Veredicto (verdict)**: resultado final de un run o una fase. Puede ser: `completed` (criterios cumplidos), `failed` (error no recuperable), `degraded` (completado con calidad reducida) o `paused` (esperando input del operador).

**WORKFLOW_PLAN**: artefacto estructurado que el Team Lead produce en `lead_intake`. Contiene la descomposición del objetivo en fases, los agentes asignados a cada fase, y los criterios de aceptación que determinan el `completed`.

**Workspace**: directorio del proyecto externo donde AI Teams lee y escribe archivos. El workdir está restringido por el sistema de compliance — los agentes no pueden escribir fuera de él.

---

*Documento generado: abril 2026*
*Versión del sistema: estado validado `2026-04-02`, `1.300+ tests pasando`*
*Contacto: maxbonas@gmail.com*
