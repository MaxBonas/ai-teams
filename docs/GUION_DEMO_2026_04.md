# Guión de Demo — AI Teams
**Presentación a inversores · Abril 2026**
*Actualizado 2026-04-21 — inputs validados en sesión de ensayo*

---

## Antes de empezar

### Setup previo (5 min antes)
1. Arrancar el IDE: `.\start_ide.bat` en `Ai_Teams/`
2. Abrir en el navegador: `http://localhost:5173`
3. Workspace activo: `md-report-cli`
4. Resetear el proyecto externo a estado limpio:
   ```powershell
   cd "C:\Users\Max\Antigravity Projects\md-report-cli"
   git reset --hard demo-baseline
   ```
5. Tener este guión en otra pantalla

### Verificación rápida
- El selector de perfil muestra: Solo Lead / Lead + Quorum / AI Team Basic / AI Teams Full / Team Advanced
- El checkbox "Remember" es visible (fondo oscuro, tilde visible)
- El workspace `md-report-cli` aparece en el selector de proyecto

---

## Bloque 1 — El problema (2 min, sin demo)

> "El problema no es que las IAs no sepan programar. El problema es que **no trabajan juntas**, no recuerdan el proyecto, y no validan su propio trabajo. Copilot te sugiere una línea. ChatGPT te da un bloque. Ninguno escribe el archivo, ejecuta los tests y repara el fallo."

> "AI Teams es un equipo de desarrollo completo, no un asistente de texto."

---

## Bloque 2 — Demo: `solo_lead` (5 min)

### Qué muestra
El modo más directo: **un único agente que lee, decide, escribe y valida**. Como Codex o Claude Code, pero con protocolo de calidad y memoria de proyecto integrados.

### Perfil a seleccionar
`Solo Lead`

### Input exacto
```
Elige una mejora pequeña pero útil para este CLI de Python.
Hazla, ejecuta pytest, y dime el resultado.
```

### Flujo esperado (60–90 segundos)
| Fase | Qué ocurre | Tiempo |
|---|---|---|
| `lead_intake` | El Lead lee el proyecto, elige el cambio, escribe WORKFLOW_PLAN | ~15s |
| `build` | Escribe el archivo modificado, recibe resultado de pytest | ~40s |
| `lead_close` | 3 líneas: qué cambió / pytest OK / pendiente ninguno | ~5s |

### Output esperado
- Estado de la run: `completed` (no `running`)
- 3 fases visibles en agent lanes
- lead_close con formato limpio: cambio realizado, pytest OK, nada pendiente
- Sin rol QA, sin quorum, sin delegación — un único lane activo

### Lo que dices mientras pasa
> "Ven que el agente lee los archivos reales del proyecto — no trabaja sobre una descripción. Elige el cambio más pequeño que sea funcional. Escribe el archivo completo. El sistema ejecuta pytest automáticamente y el resultado aparece en el cierre."

> "Esto es lo que hace Codex: leer → decidir → escribir → validar. Solo que con memoria de proyecto y un protocolo de calidad debajo."

### Métricas a señalar
- Tiempo total: ~60 segundos
- Coste: ~0.001€ (gpt-mini)
- Fases: 3

---

## Bloque 3 — Demo: `lead_quorum` (6 min)

### Qué muestra
Antes de ejecutar, **un panel de IAs senior debate el enfoque**. El Lead adopta o rechaza el consejo. Ideal para cambios con riesgo arquitectónico o de diseño.

### Perfil a seleccionar
`Lead + Quorum`

### Input exacto
```
Quiero refactorizar el generador de TOC para separar la lógica de markdown
del formato de salida. ¿Es buen momento para hacerlo? Hazlo si procede.
```

### Flujo esperado (90–150 segundos)
| Fase | Qué ocurre |
|---|---|
| `lead_intake` | El Lead formula su plan de refactor |
| `lead_quorum_auditor_1` | Auditor 1 evalúa el plan (riesgos, timing, approach) |
| `lead_quorum_auditor_2` | Auditor 2 evalúa independientemente — puede discrepar |
| `lead_quorum_final` | Síntesis: el Lead decide si adopta algún cambio de plan |
| `build` | Ejecuta el plan (posiblemente modificado por el quorum) |
| `lead_close` | Resumen 3 líneas |

### Output esperado
- **6 lanes visibles** en agent lanes: lead_intake, quorum_auditor_1, quorum_auditor_2, quorum_final, build, lead_close
- Cada auditor con su provider/modelo visible (p.ej. auditor_1 = GPT, auditor_2 = Claude)
- En conversation history: los textos de los auditores son expandibles (preview + full_text)
- El `lead_quorum_final` muestra si adoptó o descartó sugerencias — el Lead debe justificar
- Estado de la run: `completed`

### Lo que señalas
> "Aquí ven dos auditores evaluando el enfoque en paralelo — no el código, la estrategia. Cada uno usa un modelo diferente deliberadamente: diversidad de criterio. El Lead después decide qué adopta y qué descarta, y lo justifica."

> "En el ensayo, el auditor 2 identificó que el punto de inyección no estaba confirmado y que faltaba especificar el valor por defecto. El Lead adoptó ese plan por encima del suyo. Eso es lo que hace un senior: escuchar antes de comprometerse."

### Métricas a señalar
- Tiempo total: ~2 minutos
- Fases: 6
- Coste: ~0.005€

---

## Bloque 4 — Demo: `ai_team_basic` (6 min)

### Qué muestra
El Lead no escribe código: **planifica y delega a scouts especializados**. Los scouts exploran el proyecto en paralelo y reportan. Sin QA gate — ciclo rápido de exploración y análisis.

### Perfil a seleccionar
`AI Team Basic`

### Input exacto *(el que vamos a probar ahora)*
```
Traza el flujo completo que ocurre cuando un usuario ejecuta el CLI de este proyecto:
desde el entry point hasta la salida al terminal. Incluye qué clases y funciones
se invocan, en qué orden, y dónde se generan el TOC y el contenido del reporte.
```

### Input alternativo (si el anterior ya se usó en demo anterior)
```
Analiza la arquitectura de este proyecto Python. Quiero entender: qué responsabilidad
tiene cada módulo, cómo se conectan cli.py, generator.py, toc_generator.py y
report_generator.py entre sí, y si hay duplicación de responsabilidades.
```

### Flujo esperado (2–4 minutos)
| Fase | Agente | Qué ocurre |
|---|---|---|
| `lead_intake` | Team Lead | Lee el proyecto, diseña el plan de exploración, escribe WORKFLOW_PLAN |
| `build_1` / `build_2` | Engineer (scout) | Explora módulos asignados, traza flujos, reporta hallazgos |
| `lead_close` | Team Lead | Sintetiza los reportes de los scouts en un output cohesionado |

### Output esperado
- **Agent lanes**: Lead + 1-2 Engineers visibles, **sin rol QA**
- El lead_close produce una descripción técnica ordenada del flujo del CLI
- El output hace referencia a archivos y funciones reales del proyecto (`cli.py`, `toc_generator.py`, etc.)
- Estado de la run: `completed`

### Lo que dices
> "Ahora el Lead no escribe código — planifica y delega. Los scouts exploran el proyecto en paralelo: uno traza el entry point, otro sigue el flujo hasta la salida. El Lead sintetiza."

> "En proyectos reales esto escala: cinco módulos, cinco scouts, exploración paralela. El Lead nunca toca un archivo directamente — coordina."

### Métricas a señalar
- Fases: 4–5
- Sin gate de QA (perfil de exploración/análisis)
- Coste: ~0.003–0.008€

---

## Bloque 5 — El IDE (3 min, sin escribir nada)

### Mostrar en pantalla
1. **Panel de fases** → cada agente tiene su lane, estado en tiempo real, provider y modelo visibles
2. **Conversation history** → outputs expandibles, preview + texto completo
3. **Cost ledger** → cuánto costó cada run, desglosado por agente
4. **Memoria de proyecto** → `runtime/lead_memory.md` — el sistema recuerda entre sesiones

### Lo que dices
> "Todo está instrumentado. Ven qué modelo tomó cada decisión, por qué canal, con qué coste. Esto no es una caja negra."

> "Y aquí la memoria de proyecto: el sistema recuerda qué se hizo en sesiones anteriores. La próxima vez que le pidas continuar, sabe exactamente dónde estaba."

---

## Bloque 6 — Continuidad y memoria (2 min)

### Perfil a seleccionar
`Solo Lead`

### Input exacto
```
en que punto del proyecto estamos?
```

### Flujo esperado
→ El agente responde con un resumen del estado actual sin abrir ninguna fase de build.
→ Estado: `completed` en segundos.
→ Sin output de código, sin WORKFLOW_PLAN — solo contexto.

### Lo que dices
> "El sistema distingue entre una pregunta de contexto y una petición de trabajo. Si preguntas dónde estás, te dice dónde estás. Si pides un cambio, lo ejecuta. Y lo hace con la memoria acumulada de runs anteriores."

---

## Tabla de perfiles — para mostrar en pantalla

| Perfil | Agentes activos | Gates | Tiempo aprox | Para qué |
|---|---|---|---|---|
| `solo_lead` | 1 (Lead) | ninguno | ~60s | fixes, mejoras pequeñas, contexto |
| `lead_quorum` | Lead + 2 auditores | ninguno | ~2min | refactors con riesgo de enfoque |
| `ai_team_basic` | Lead + scouts | ninguno | ~3min | análisis, exploración, features |
| `ai_teams_full` | Equipo completo + quorum | Review + QA | ~8min | entregas de alta criticidad |

---

## Preguntas difíciles — respuestas preparadas

**"¿Por qué no usar directamente Devin o Cursor?"**
> Devin es una caja negra que no podemos instrumentar, auditar ni integrar en nuestros pipelines. Cursor es un asistente de editor, no un sistema de entrega. AI Teams es la capa de orquestación que conecta múltiples modelos, proveedores y herramientas con control, memoria y calidad.

**"¿Cuánto cuesta por run?"**
> Solo_lead con gpt-mini: ~0.001€. Team_advanced con modelos de mayor calidad: ~0.05–0.20€. Una revisión de código de un junior cuesta más.

**"¿Está en producción?"**
> El sistema se desarrolla a sí mismo: cada feature nueva se implementa con AI Teams. 776 tests pasando. En uso activo desde hace meses.

**"¿Qué pasa si el modelo falla o alucina?"**
> Hay quality gates: evidence gate (¿escribió archivos reales?), semantic gate (¿el output tiene sentido?), pytest automático. Si falla, el sistema registra el fallo y puede reintentar o escalar a revisión humana.

**"¿Cómo monetizáis?"**
> Tres vías: SaaS por equipo (suscripción mensual basada en runs), licencia on-premise para empresas con datos sensibles, y consulting de integración. El pricing por run escala con el valor entregado.

---

## Cierre (2 min)

> "Lo que ven hoy es una fracción de lo que esto puede llegar a ser. Tenemos el núcleo funcionando: el orquestador, los perfiles, la memoria, el routing. Lo que necesitamos con vuestra financiación es productizar la capa de usuario, conectar con más herramientas reales —GitHub Actions, Jira, Slack— y pasar de dogfooding a los primeros clientes externos."

> "La pregunta no es si los equipos de desarrollo van a usar IA. Ya la usan. La pregunta es quién pone la capa de coordinación, memoria y calidad entre los modelos y el código que va a producción. Eso es AI Teams."

---

## Contingencias

| Situación | Respuesta |
|---|---|
| Timeout o error de API | Mostrar una run guardada en el historial del IDE |
| Conexión lenta | `AITEAM_SIM_MODE=1` genera respuestas simuladas instantáneas |
| Run queda en "running" | F5 en el navegador — el estado se recarga desde el backend |
| Preguntan por el código | Repo en GitHub, tests públicos, CLAUDE.md como onboarding |
| Quorum no aparece en lanes | Confirmar que el perfil es `lead_quorum`, no `solo_lead` |
| Proyecto externo sucio | `git reset --hard demo-baseline` en `md-report-cli/` |

---

## Orden recomendado

```
Bloque 1 — Pitch (2 min)
Bloque 2 — solo_lead live (5 min)        ← impacto inmediato
Bloque 3 — lead_quorum live (6 min)      ← "deliberación estratégica"
Bloque 4 — ai_team_basic live (6 min)    ← "equipo coordinado"
Bloque 5 — IDE walkthrough (3 min)       ← "instrumen tación total"
Bloque 6 — memoria/contexto (2 min)      ← "no empieza de cero"
Cierre (2 min)
─────────────────────────────
Total: ~26 min + preguntas
```
