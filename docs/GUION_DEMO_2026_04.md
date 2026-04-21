# Guión de Demo — AI Teams
**Presentación a inversores · Abril 2026**

---

## Antes de empezar

### Setup previo (5 min antes)
1. Arrancar el IDE: `.\start_ide.bat` en `Ai_Teams/`
2. Abrir en el navegador: `http://localhost:5173`
3. Workspace activo: `md-report-cli` (proyecto real de Python)
4. Tener abierto este guión en otra pantalla

### Configuración del entorno
- Modelos activos: `openai/gpt-5-mini` (rápido, visible en UI)
- Modo: streaming SSE en tiempo real (el inversor ve cómo piensa el agente)

---

## Bloque 1 — El problema (2 min, sin demo)

> "El problema no es que las IAs no sepan programar. El problema es que **no trabajan juntas**, no recuerdan el proyecto, y no validan su propio trabajo. Copilot te sugiere una línea. ChatGPT te da un bloque. Ninguno escribe el archivo, ejecuta los tests y repara el fallo."

> "AI Teams es un equipo de desarrollo completo, no un asistente de texto."

---

## Bloque 2 — Demo: solo_lead (6 min)

### Qué muestra
El perfil más directo: **un único agente que lee, decide, escribe y valida**. Sin equipo, sin burocracia. Como Codex o Claude Code, pero orquestado y con memoria de proyecto.

### Lo que escribes en el chat
```
Elige una mejora pequeña pero útil para este CLI de Python.
Hazla, ejecuta pytest, y dime el resultado.
```
*Seleccionar modo: `solo_lead`*

### Lo que pasa (30-90 segundos)
1. **lead_intake** → 3 líneas: "cambio elegido: X" + WORKFLOW_PLAN
2. **build** → el agente lee los archivos del workspace, escribe el cambio completo, recibe el resultado de pytest automáticamente
3. **lead_close** → 3 líneas: qué cambió / pytest OK / ninguno

### Lo que dices mientras pasa
> "Ven que el agente lee los archivos reales del proyecto. No trabaja a ciegas. Elige el cambio más pequeño que sea funcional. Escribe el archivo completo. El sistema ejecuta pytest automáticamente y el resultado aparece en el cierre."

> "Esto es lo que hace Codex: leer → decidir → escribir → validar. Solo que con memoria de proyecto y un protocolo de calidad debajo."

### Métricas a señalar
- Tiempo total: ~60 segundos
- Fases creadas: 3 (lead_intake, build, lead_close)
- Coste: ~0.001€ (gpt-mini)

---

## Bloque 3 — Demo: lead_quorum (5 min)

### Qué muestra
Antes de ejecutar, **un panel de IAs senior debate el enfoque**. El Lead puede aceptar o rechazar. Ideal para cambios con riesgo arquitectónico.

### Lo que escribes en el chat
```
Quiero refactorizar el generador de TOC para separar la lógica de markdown del formato de salida.
¿Es buen momento para hacerlo?
```
*Seleccionar modo: `lead_quorum`*

### Lo que pasa
1. **lead_intake** → el Lead formula su plan de refactor
2. **lead_quorum_auditor_1, _2** → otros agentes evalúan el plan (¿riesgos? ¿es el momento?)
3. **lead_quorum_final** → síntesis y decisión
4. **build** → el Lead ejecuta (o adapta el plan según el quorum)
5. **lead_close** → resumen 3 líneas

### Lo que dices
> "Este modo es para cuando el scope no está completamente claro o el riesgo es alto. El Lead propone, un panel de IAs opina sobre el enfoque —no sobre el código, sobre la estrategia—, y el Lead decide si acepta o rechaza. Es como consultar a seniors antes de comprometerse con una arquitectura."

---

## Bloque 4 — Mostrar el IDE (3 min, no escribir nada)

### Mostrar en pantalla
1. **El panel de fases** → cada agente tiene su lane, el estado en tiempo real
2. **El routing** → "openai/gpt-5-mini via api" → transparencia de qué modelo y qué canal
3. **El cost ledger** → cuánto costó cada run
4. **La memoria** → `runtime/lead_memory.md` → el sistema recuerda entre sesiones

### Lo que dices
> "Todo está instrumentado. Ven qué modelo tomó cada decisión, por qué canal, con qué coste. Esto no es una caja negra."

> "Y aquí la memoria de proyecto — el sistema recuerda qué se hizo en sesiones anteriores. La próxima vez que le pidas continuar, sabe dónde estaba."

---

## Bloque 5 — Explicar ai_team_basic y ai_teams_full (3 min, sin demo live)

### Mostrar el selector de perfil en el UI
*(No ejecutar — solo señalar las opciones)*

> "Cuando la tarea es más compleja, escalamos. En `ai_team_basic`, el Lead ya no escribe código: planifica y delega a un Engineer especializado. Hay un gate de QA antes de cerrar."

> "En `ai_teams_full`, añadimos quorum en el plan, un Reviewer que revisa el código del Engineer, y QA formal. Es el pipeline que usaríamos para un cambio en producción."

### Mostrar la tabla de perfiles (del documento de presentación)

| Perfil | Agentes activos | Gates | Tiempo aprox | Para qué |
|---|---|---|---|---|
| `solo_lead` | 1 (Lead) | ninguno | ~60s | fixes, mejoras pequeñas |
| `lead_quorum` | 1 + panel | ninguno | ~2min | refactors con riesgo de enfoque |
| `ai_team_basic` | Lead + Engineer + QA | QA | ~4min | features complejas |
| `ai_teams_full` | Equipo completo | Review + QA | ~8min | entregas de alta criticidad |

---

## Bloque 6 — Continuidad y memoria (2 min)

### Lo que escribes
```
en que punto del proyecto estamos?
```
*Modo: solo_lead*

### Lo que pasa
→ El agente responde con un resumen del estado actual del proyecto sin abrir ningún build

### Lo que dices
> "El sistema distingue entre una pregunta de contexto y una petición de trabajo. Si preguntas dónde estás, te dice dónde estás. Si pides un cambio, lo ejecuta."

> "Y lo hace con la memoria acumulada de runs anteriores. No empieza de cero cada vez."

---

## Preguntas difíciles — respuestas preparadas

**"¿Por qué no usar directamente Devin o Cursor?"**
> Devin es una caja negra que no podemos instrumentar, auditar ni integrar en nuestros pipelines. Cursor es un asistente de editor, no un sistema de entrega. AI Teams es la capa de orquestación que conecta múltiples modelos, proveedores y herramientas con control, memoria y calidad.

**"¿Cuánto cuesta por run?"**
> Solo_lead con gpt-mini: ~0.001€. Team_advanced con modelos de mayor calidad: ~0.05-0.20€. Una run de revisión de código de un junior cuesta más.

**"¿Está en producción?"**
> El sistema se desarrolla a sí mismo: cada feature nueva se implementa con AI Teams. 1300+ tests pasando. En uso activo desde hace meses.

**"¿Qué pasa si el modelo falla o alucina?"**
> Hay quality gates: evidence gate (¿escribió archivos reales?), semantic gate (¿el output tiene sentido?), pytest automático. Si falla, el sistema registra el fallo y puede reintentar o escalar a revisión humana.

**"¿Cómo monetizáis?"**
> Tres vías: SaaS por equipo (suscripción mensual basada en runs), licencia on-premise para empresas con datos sensibles, y consulting de integración. El modelo de pricing por run hace que el coste escale con el valor entregado.

---

## Cierre (2 min)

> "Lo que ven hoy es una fracción de lo que esto puede llegar a ser. Tenemos el núcleo funcionando: el orquestador, los perfiles, la memoria, el routing. Lo que necesitamos con vuestra financiación es productizar la capa de usuario, conectar con más herramientas reales —GitHub Actions, Jira, Slack— y pasar de dogfooding a los primeros clientes externos."

> "La pregunta no es si los equipos de desarrollo van a usar IA. Ya la usan. La pregunta es quién pone la capa de coordinación, memoria y calidad entre los modelos y el código que va a producción. Eso es AI Teams."

---

## Notas de contingencia

- Si el demo falla por timeout/error de API → mostrar una run guardada en el historial del IDE
- Si la conexión va lenta → usar `AITEAM_SIM_MODE=1` que genera respuestas simuladas instantáneas
- Si preguntan por el código → repo en GitHub, tests públicos, CLAUDE.md como onboarding
