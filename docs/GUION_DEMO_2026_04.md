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
- El selector de perfil muestra: Solo Lead / Lead + Quorum / AI Team Basic / …
- El checkbox "Remember" es visible (fondo oscuro, tilde visible)
- El workspace `md-report-cli` aparece en el selector de proyecto

---

## Bloque 1 — El problema (2 min, sin demo)

> "El problema no es que las IAs no sepan programar. El problema es que **no trabajan juntas**, no recuerdan el proyecto, y no validan su propio trabajo. Copilot te sugiere una línea. ChatGPT te da un bloque. Ninguno escribe el archivo, ejecuta los tests y repara el fallo."

> "AI Teams es un equipo de desarrollo completo, no un asistente de texto."

---

## Test 1 — `solo_lead`: feature concreta + pytest (5 min)

### Qué muestra
El agente más directo: lee el proyecto real, implementa un cambio específico, ejecuta pytest y reporta. Como Codex, pero con memoria de proyecto y protocolo de calidad integrados.

### Perfil
`Solo Lead`

### Input
```
Añade la opción --version al CLI. Cuando el usuario ejecute md-report --version
debe mostrar "md-report, version 0.1.0". Escríbela, ejecuta pytest, y dime el resultado.
```

### Flujo esperado (60–90 segundos)
| Fase | Qué ocurre |
|---|---|
| `lead_intake` | Lee `cli.py` y `pyproject.toml`, escribe WORKFLOW_PLAN con el cambio concreto |
| `build` | Añade `@click.version_option(version="0.1.0", prog_name="md-report")` en el entry point, ejecuta pytest |
| `lead_close` | Confirma: opción añadida / pytest OK / sin pendientes |

### Output esperado
- Estado: `completed`
- 3 lanes: lead_intake, build, lead_close
- `lead_close` menciona el comando `--version` y el resultado de pytest
- Sin QA, sin quorum, sin delegación

### Lo que dices mientras pasa
> "Le doy una tarea exacta y comprobable. El agente abre los archivos reales del proyecto, localiza el entry point del CLI, escribe el cambio y ejecuta los tests automáticamente. No simula nada — el pytest se ejecuta de verdad en el workspace."

---

## Test 2 — `solo_lead`: consulta de contexto / memoria (2 min)

### Qué muestra
El sistema distingue entre una pregunta de contexto y una petición de trabajo. Responde sin abrir ninguna fase de build.

### Perfil
`Solo Lead` (nueva run, no continuation de la anterior)

### Input
```
en que punto del proyecto estamos?
```

### Flujo esperado (10–20 segundos)
→ El Lead responde directamente con el estado del proyecto a partir de su memoria.
→ No hay fase `build`. No hay WORKFLOW_PLAN.
→ Estado: `completed` en segundos.

### Output esperado
- Resumen de lo que se hizo en la run anterior (la opción `--version`)
- Estado general del proyecto según la memoria acumulada
- Sin código, sin fases de ejecución

### Lo que dices
> "El sistema distingue. Si le preguntas dónde estás, te dice dónde estás. Si pides trabajo, lo ejecuta. Y recuerda la sesión anterior — sabe que acabamos de añadir `--version`."

---

## Test 3 — `lead_quorum`: decisión arquitectónica antes de implementar (6 min)

### Qué muestra
Antes de ejecutar, un panel de IAs senior evalúa el enfoque. El Lead adopta o descarta el consejo. Para cambios donde el *cómo* importa tanto como el *qué*.

### Perfil
`Lead + Quorum` (nueva run)

### Input
```
Quiero añadir --toc-title al CLI para que el usuario pueda personalizar el título
del TOC. Antes de implementarlo, ¿tiene sentido hacerlo así o hay algo en la
arquitectura actual que deba considerar?
```

### Flujo esperado (90–150 segundos)
| Fase | Qué ocurre |
|---|---|
| `lead_intake` | El Lead lee `cli.py` y `toc_generator.py`, formula su plan de implementación |
| `lead_quorum_auditor_1` | Evalúa: ¿dónde se inyecta el parámetro? ¿Capa CLI o capa generador? |
| `lead_quorum_auditor_2` | Evalúa independientemente — puede identificar riesgos distintos |
| `lead_quorum_final` | El Lead sintetiza: adopta o descarta sugerencias, justifica la decisión |
| `build` | Implementa con el enfoque decidido |
| `lead_close` | Confirma: opción añadida, pytest, pendientes |

### Output esperado
- **6 lanes visibles**: intake, auditor_1, auditor_2, quorum_final, build, lead_close
- Cada auditor con su modelo visible (auditor_1 ≠ auditor_2)
- Los textos de los auditores son expandibles en conversation history
- `lead_quorum_final` justifica qué adoptó y por qué
- Estado: `completed`

### Lo que señalas
> "Le hago una pregunta de diseño, no solo de implementación. Los dos auditores evalúan el plan en paralelo — cada uno con un modelo diferente, deliberadamente. Diversidad de criterio."

> "En nuestro ensayo, uno de los auditores identificó que el parámetro debía pasarse a través de la capa generadora, no solo al CLI. El Lead adoptó ese enfoque. Eso es lo que hace un senior antes de comprometerse con una arquitectura."

### Métricas a señalar
- Fases: 6
- Tiempo: ~2 min
- Coste: ~0.005€

---

## Test 4 — `ai_team_basic`: equipo coordinado, tarea dividida (5 min)

### Qué muestra
El Lead no escribe código: planifica y delega a scouts. Cada scout recibe una subtarea específica y reporta. El Lead sintetiza.

### Perfil
`AI Team Basic` (nueva run)

### Input
```
Revisa si el CLI maneja correctamente el caso en que el archivo de entrada no existe.
Si no lo hace, implementa el error handling y añade un test para ese caso.
```

### Flujo esperado (2–4 minutos)
| Fase | Agente | Qué ocurre |
|---|---|---|
| `lead_intake` | Team Lead | Lee el proyecto, divide la tarea: un scout revisa el handling actual, otro prepara el fix y el test |
| `build_1` | Engineer (scout 1) | Revisa `cli.py` y `cli_utils.py`, identifica si hay manejo del caso |
| `build_2` | Engineer (scout 2) | Implementa el error handling y escribe el test |
| `lead_close` | Team Lead | Sintetiza: qué había, qué se añadió, pytest OK |

### Output esperado
- **Agent lanes**: Lead + 2 Engineers, **sin rol QA**
- `lead_close` menciona el archivo modificado, el test añadido y el resultado de pytest
- El output hace referencia a código real del proyecto (`cli.py`, `FileNotFoundError`, etc.)
- Estado: `completed`

### Lo que dices
> "Ahora el Lead no toca un archivo — planifica y delega. Un scout revisa qué había, otro implementa el fix. El Lead coordina y sintetiza."

> "En proyectos reales esto escala: diez módulos, diez scouts, exploración paralela. El Lead actúa como un tech lead real: divide, asigna, integra."

### Métricas a señalar
- Fases: 4–5
- Sin QA (perfil de equipo básico)
- Coste: ~0.005–0.010€

---

## Bloque final — El IDE (3 min, sin escribir nada)

### Mostrar en pantalla
1. **Panel de fases** → cada agente tiene su lane, estado en tiempo real, provider y modelo
2. **Conversation history** → outputs expandibles, preview + texto completo
3. **Cost ledger** → coste por run, desglosado por agente
4. **Memoria de proyecto** → el sistema recuerda entre sesiones

### Lo que dices
> "Todo está instrumentado. Ven qué modelo tomó cada decisión, por qué canal, con qué coste. Esto no es una caja negra."

> "Y aquí la memoria: el sistema recuerda que añadimos `--version` y `--toc-title`, y que el CLI ahora maneja archivos inexistentes. La próxima sesión empieza sabiendo todo eso."

---

## Tabla de perfiles

| Perfil | Agentes | Gates | Tiempo | Para qué |
|---|---|---|---|---|
| `solo_lead` | 1 (Lead) | ninguno | ~60s | fixes, features pequeñas, contexto |
| `lead_quorum` | Lead + 2 auditores | ninguno | ~2min | decisiones arquitectónicas con riesgo |
| `ai_team_basic` | Lead + scouts | ninguno | ~3min | tareas divisibles, exploración paralela |
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
> Hay quality gates: evidence gate (¿escribió archivos reales?), semantic gate (¿el output tiene sentido?), pytest automático. Si falla, el sistema registra el fallo y puede reintentar o escalar.

**"¿Cómo monetizáis?"**
> Tres vías: SaaS por equipo (suscripción mensual basada en runs), licencia on-premise para empresas con datos sensibles, y consulting de integración.

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
| Run queda en "running" | F5 — el estado se recarga desde el backend |
| Quorum no aparece en lanes | Verificar que el perfil es `lead_quorum`, no `solo_lead` |
| Proyecto externo sucio | `git reset --hard demo-baseline` en `md-report-cli/` |

---

## Orden y tiempos

```
Bloque 1   — Pitch verbal                    2 min
Test 1     — solo_lead: --version            5 min   ← impacto inmediato, resultado comprobable
Test 2     — solo_lead: contexto/memoria     2 min   ← "no empieza de cero"
Test 3     — lead_quorum: --toc-title        6 min   ← "deliberación antes de actuar"
Test 4     — ai_team_basic: error handling   5 min   ← "equipo coordinado"
IDE walk   — instrumentación                 3 min   ← transparencia total
Cierre                                       2 min
──────────────────────────────────────────────────
Total: ~25 min + preguntas
```
