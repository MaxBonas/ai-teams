# Routing multimodelo y quorum — Referencia de implementación

**Fecha**: 2026-04-03
**Estado**: ✅ Implementado (sesión 2026-04-03) — `aiteam/cli.py`, `aiteam/quorum.py`, `runtime/model_catalog.json`
**Relacionado con**:
- `docs/MODEL_POLICY.md` — catálogo de modelos y tiers
- `docs/LEAD_QUORUM_PROJECT_CONTEXT_VISION.md` — visión original del quorum
- `docs/ROUTING_CATALOG_VIEW.md` — vista operativa del routing
- `aiteam/cli.py` → `build_default_orchestrator()`
- `aiteam/quorum.py` → `run_planning_quorum()`
- `aiteam/router.py` → `_team_lead_allowed()`, `_eligible()`

---

## Principio de diseño

El sistema sigue tres reglas de routing:

1. **Multi-provider por rol**: cada rol tiene acceso a modelos de distintos providers (OpenAI,
   Anthropic, Google, Groq). Nunca se depende de un único provider.
2. **Inteligencia y precio diferenciados por rango de rol**: Team Lead usa modelos frontier;
   los workers usan modelos avanzados/budget apropiados a su función.
3. **Un ejecutivo, varios auditores**: el Team Lead es siempre un modelo singular con autoridad.
   El quorum de auditores enriquece su plan, pero el Lead tiene la última palabra.

---

## Pools de adapters (`aiteam/cli.py` → `build_default_orchestrator`)

Los adapters están divididos en dos pools explícitos. La separación se refuerza con:
- `role_targets={"team_lead"}` en los adapters del pool TL → excluidos de roles worker
- `api_allowed_for_team_lead: false` en el catálogo para los adapters worker → `_team_lead_allowed()` los filtra

### Pool Team Lead — frontier, multi-provider

| Prioridad | Adapter | Provider | Modelo | Tier catálogo |
|-----------|---------|----------|--------|---------------|
| 10 | `openai_pro` | OpenAI | gpt-4.1 | `senior_cloud` |
| 20 | `claude_pro` | Anthropic | claude-sonnet-4-5 | `senior_cloud` |
| 30 | `gemini_pro` | Google | gemini-2.5-flash | `senior_cloud` |
| 40 | `groq_gpt120b` | Groq (gratis) | openai/gpt-oss-120b | `advanced_api` |
| 50 | `groq_kimi_k2` | Groq (gratis) | moonshotai/kimi-k2-instruct | `advanced_api` |

- Todos tienen `role_targets={"team_lead"}` → invisibles para worker tasks
- Todos tienen `api_allowed_for_team_lead: true` en `model_catalog.json`
- El router intenta en orden de prioridad; el primero disponible es el **Lead ejecutivo**

### Pool Workers — advanced/budget, multi-provider

| Adapter | Provider | Modelo | Tier catálogo | Roles accesibles |
|---------|----------|--------|---------------|-----------------|
| `gemini_worker` | Google | gemini-2.5-flash | `budget_api` | todos (excl. TL por catálogo) |
| `openai_api_mini` | OpenAI | gpt-4.1-mini | `advanced_api` | todos (excl. TL por catálogo) |
| `claude_haiku` | Anthropic | claude-haiku-4-5 | `budget_api` | todos (excl. TL por catálogo) |
| `groq_api_fast` | Groq (gratis) | llama-3.3-70b-versatile | `budget_api` | todos (excl. TL por catálogo) |
| `openai_api_fast` | OpenAI | gpt-4o-mini | `budget_api` | todos (excl. TL por catálogo) |

- Sin `role_targets` → disponibles para engineer, reviewer, qa, researcher, scout
- `api_allowed_for_team_lead: false` en catálogo → `_team_lead_allowed()` los rechaza aunque pasen el filtro de `role_targets`
- Scout prefiere `budget_api` (ver `_tier_rank()` en `router.py`)

### Adapter local (Ollama)

| Adapter | Path | Estado |
|---------|------|--------|
| `ollama_qwen_coder_local` | `C:\Users\she__\AppData\Local\Programs\Ollama\ollama.exe` | `enabled: false` |

Habilitar manualmente en `runtime/adapters.json` cuando el backend corre en **max-gamingpc**
(usuario `she__`, donde Ollama está instalado). Nunca habilitar en orch01.

---

## Catálogo de modelos (`runtime/model_catalog.json`)

El catálogo controla cómo el router califica cada adapter para cada rol.
Campos clave:

- `tier`: `senior_cloud` | `advanced_api` | `budget_api` | `local`
- `api_allowed_for_team_lead`: `true` solo en adapters autorizados como Lead

Regla en `router.py._team_lead_allowed()`:
```python
if profile.tier == "senior_cloud":
    return self._smoke_ok(adapter)          # Siempre permitido si humo OK
if profile.tier == "advanced_api" and profile.api_allowed_for_team_lead:
    return self._smoke_ok(adapter)          # Permitido como último recurso TL
return False                                # Budget/local nunca son Team Lead
```

Cuando runtime JSON existe, **sobreescribe** los defaults Python de `model_catalog.py`.
La precedencia de carga es: Python defaults → `config/model_catalog.json` → `runtime/model_catalog.json`.

---

## Quorum de planificación (`aiteam/quorum.py`)

### Propósito

El quorum enriquece el plan inicial del Lead ejecutivo con perspectivas de modelos senior
independientes. Se ejecuta **una sola vez por run**, en la fase de planificación, antes de
que el Lead dispare el workflow de workers.

No es democracia: el Lead tiene la última palabra. Los auditores son consultores, no co-líderes.

### Cuándo se activa

```python
# aiteam/quorum.py → should_apply_planning_quorum()
AITEAM_AUTO_QUORUM=1   # Activa en TODOS los runs (recomendado con 3+ providers senior)
AITEAM_AUTO_QUORUM=0   # Solo cuando payload.quorum=True y run_mode ∈ PLANNING_QUORUM_RUN_MODES
```

`PLANNING_QUORUM_RUN_MODES` = `{planning_only, architecture_review, roadmap}`

### Flujo deliberativo (una vez por run)

```
┌────────────────────────────────────────────────────────────────────────┐
│  QUORUM DE PLANIFICACIÓN                                               │
│                                                                        │
│  Contexto de entrada (igual para todos):                               │
│  - Solicitud del usuario                                               │
│  - Contexto del proyecto (.aiteam/instructions.md, historial, etc.)    │
│  - Plan inicial del Lead ejecutivo                                     │
│                                                                        │
│  [Lead ejecutivo] ──────── genera plan inicial                         │
│        │                                                               │
│        ▼                                                               │
│  [Auditor 1]   ←── ve: plan del Lead                                   │
│  provider: Anthropic/claude-sonnet                                     │
│  Evalúa independientemente. Detecta riesgos, mejoras, alternativas.   │
│        │                                                               │
│        ▼                                                               │
│  [Auditor 2]   ←── ve: plan del Lead + aporte del Auditor 1           │
│  provider: Google/gemini                                               │
│  Sintetiza. Puede coincidir, discrepar o ampliar. No repite sin valor. │
│        │                                                               │
│        ▼                                                               │
│  [Lead ejecutivo] ←── ve: plan propio + todos los aportes             │
│  provider: OpenAI/gpt-4.1  (excluye adapters de auditores)            │
│  Justifica: acepta / matiza / descarta cada aporte.                    │
│  Emite plan definitivo. Flujo continúa con este plan.                 │
└────────────────────────────────────────────────────────────────────────┘
```

**Por qué los auditores están encadenados** (no paralelos):
- Paralelo = ruido duplicado; dos modelos tienden a señalar los mismos puntos
- Encadenado = deliberación real; el Auditor 2 puede decir "Auditor 1 señaló X,
  yo añado Y" o "discrepo porque Z"
- El Lead al final ve una conversación coherente, no dos monólogos

### Diversidad de provider garantizada

Cada invocación acumula los adapters ya usados en `excluded_adapters`:

```
Lead ejecutivo  → usa openai_pro (gpt-4.1)
Auditor 1       → excluded={openai_pro} → usa claude_pro (claude-sonnet)
Auditor 2       → excluded={openai_pro, claude_pro} → usa gemini_pro
Consolidación   → excluded={claude_pro, gemini_pro} → usa openai_pro (o siguiente disponible)
```

Si un provider no está disponible, el quorum continúa con los que puede obtener.
Si no hay ningún auditor disponible, el quorum retorna `applied=False` y el plan del Lead
se usa sin modificar (el run continúa).

### Coste controlado

- **Exactamente N+1 llamadas extra** por run (N auditores + 1 consolidación)
- Con N=2 (default): 3 llamadas extra al inicio de cada run
- Solo modelos del pool Team Lead (senior, pero `role_targets={"team_lead"}`)
- Workers no tocan este pool

### Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `AITEAM_AUTO_QUORUM` | `0` | `1` = quorum en todos los runs |
| `AITEAM_QUORUM_CONSULTANT_COUNT` | `2` | Número de auditores (1–4) |

Configuración actual en `.env`:
```
AITEAM_AUTO_QUORUM=1
AITEAM_QUORUM_CONSULTANT_COUNT=2
```

### Resultado persistido

`QuorumResult` se guarda en `_ws["lead_quorum"]` y en el metadata de la tarea.
El plan consolidado reemplaza el plan inicial del Lead antes de parsear el workflow.
El evento `chat_quorum_applied` se emite al event logger con:
- `lead_adapter` — quién generó el plan inicial
- `consultant_count` — cuántos auditores participaron
- `final_adapter` — quién consolidó

---

## Fixes de robustez aplicados (sesión 2026-04-04)

### Fix L — Auditoría run CHAT-8FDCB4CC (regresión Fix J + root cause continuaciones)

Detectados en auditoría completa de CHAT-8FDCB4CC:

**RC-2 — `aiteam/orchestrator.py` → `_notify_dependents()` [regresión de Fix J]**:
Fix J introdujo detección de "bloqueada" en summary, pero el keyword era demasiado amplio.
Scouts y delegates mencionan "bloqueada" como descripción contextual del historial del proyecto
(no como auto-reporte de su propio bloqueo), disparando falsas notificaciones
`"Dependency blocked"` a lead_close y otros dependientes.
Fix L añade dos guards:
1. `_is_delegate`: fases cuyo nombre empieza por `delegate_` nunca emiten "blocked"
2. `_is_scout`: rol `scout` nunca emite "blocked" (describen contexto, no su propio estado)
3. Keywords reducidos: se eliminan `"bloqueada"` / `"bloqueado"` sueltos; se mantienen
   `"bloqueada:"` / `"bloqueado:"` (label de status con dos puntos), `"evidencegate"`,
   `"evidence gate"`, `"no hay evidencia"`, `"missing evidence"`, `": blocked"`.
La firma ahora acepta `task_role: str = ""` que el call site pasa como `task.role.value`.

**RC-3 — `aiteam/lead_close_policy.py` → `derive_lead_close_policy()` [timing bug]**:
La función se llama durante la ejecución de `lead_close` (antes de que `chat_policy.py`
calcule `policy_signals`), por lo que `run_verdict["policy_signals"]` está vacío en ese
momento. Fix L añade tres capas adicionales de detección:
1. Sweep de TODOS los `phase_verdicts` para fases con `status ∈ {blocked, failed, rejected}`
   (continuaciones usan nombres custom como `engineer_toc_implementation`)
2. Nuevo parámetro `phase_outputs: dict` — escanea outputs de fases engineer para keywords
   estructurales de bloqueo (`"bloqueada:"`, `"evidencegate"`, etc.)
3. `policy_signals` se mantiene para el path de observability/API (donde sí están disponibles)
La función acepta `phase_outputs=ws.get("phase_outputs", {})` desde el call site en orchestrator.

**RC-1 — `api/main.py` → `_WORKFLOW_PLAN_INSTRUCTION` + `_phase_contract_prompt_block()`**:
Runs de continuación producían `objective: "Ejecutar fase: engineer_toc_implementation"`
(placeholder genérico de `_build_spec`), causando que el Engineer reportara BLOQUEADA.
Dos fixes:
1. `_WORKFLOW_PLAN_INSTRUCTION` ahora incluye reglas explícitas: `objective` obligatorio
   y específico, `depends_on` obligatorio, y nota especial para runs de continuación
   ("COPIA su objetivo real desde el contexto del historial")
2. `_phase_contract_prompt_block()` detecta objectives genéricos en fases ENGINEER
   (`len < 12` o empieza por `"Ejecutar fase:"`) y los sustituye por una instrucción
   de recuperación activa: "extrae el objetivo real del contexto disponible y ejecutalo".

**RC-4 — `aiteam/phase_verdicts.py` → `extract_phase_verdict()` [detección engineer]**:
La detección heurística de bloqueo solo cubría `review`, `qa` y `build` por nombre exacto.
Fases engineer con nombres custom (p.ej. `engineer_toc_implementation`) no generaban
ningún verdict aunque su output contuviera `"BLOQUEADA:"`.
Fix L añade dos nuevas regexes (`_ENGINEER_BLOCKED_LABEL_RE` y `_ENGINEER_BLOCKED_PHRASE_RE`)
y las aplica a la fase `build` y a cualquier fase cuyo nombre contenga hints de engineer
(`"engineer"`, `"build"`, `"implement"`, `"develop"`, `"code"`).
El structured `[PHASE_VERDICT]` sigue teniendo prioridad sobre el heurístico.

### Fix J — P0 bugs detectados en run CHAT-34CA3EB3

**`aiteam/orchestrator.py` → `_notify_dependents()`**:
Antes enviaba siempre `subject="Dependency ready: {task_id}"` incluso cuando el output
de la tarea completada indicaba bloqueo (ej. "BLOQUEADA", "evidence gate").
Ahora detecta keywords de bloqueo en `summary` y, si los encuentra, emite
`subject="Dependency blocked: {task_id}"` para no falsamente desbloquear dependientes.
*(ver Fix L para refinamiento de esta lógica)*

**`aiteam/lead_close_policy.py` → `derive_lead_close_policy()`**:
`evidence_gate_failed` y otros `policy_signals` vivían solo en `run_verdict["policy_signals"]`
y nunca llegaban a `phase_verdicts`/`phase_states`, lo que causaba que la función
devolviera `eligible_for_done` por defecto aunque hubiera gates fallidos.
Ahora lee `run_verdict_dict.get("policy_signals")` y mapea las señales de no-completado
(`evidence_gate_failed`, `semantic_gate_failed`, etc.) a `reason_codes`, que disparan
`authoritative_close_state = "not_completed"` correctamente.
*(ver Fix L para solución completa del timing bug)*

### Fix K — Reset del fixture externo Python src-layout

`src/sample_cli/cli.py` contaminado por Codex (argparse + Pygments → reescrito con click).
`src/sample_cli/styles.py` reescrito: sin imports de Pygments, CSS estático en `DEFAULT_CSS`.
`pyproject.toml`: entry point `sample_cli.cli:main_cli`, deps `markdown` + `click` solo.
`tests/__init__.py` + `tests/test_cli.py`: 11 tests via `click.testing.CliRunner`, 11/11 pasan.
`README.md`: creado.

### Fix H — Engineer src-layout + plan_risks no-gate + session history

**`aiteam/profiles.py`** — Regla src-layout en el prompt del Engineer:
```
ESTRUCTURA SRC-LAYOUT: Si el proyecto tiene 'src/' o pyproject.toml con where=["src"],
TODOS los archivos del paquete van bajo src/<paquete>/
CORRECTO: path=src/sample_cli/cli.py
INCORRECTO: path=sample_cli/cli.py
```

**`aiteam/workflow_planner.py`** — `plan_risks` redefinido como auditor, no gate:
```
Esta fase es de evaluacion de riesgos, NO una decision go/no-go.
Nunca emitas 'RECHAZADO' ni bloquees el build.
```

**`api/utils.py`** — `_build_scout_session_history_context()` lee `lead_memory.md`
como fuente autoritativa de `resultado` por CHAT, evitando que el scout confunda
`estado=completed` en DB con éxito real de la run.

### Fix I — Streaming blocks preview

**`ide-frontend/src/components/TeamChat.tsx`** — Handler `agent_completed` inicializa
bloques vacíos con `ev.preview` (200 chars que el orchestrator incluye en el evento),
eliminando los bloques que quedaban en `...` permanentemente.

---

## Errores a evitar

- **No** usar `role_targets={"team_lead"}` en adapters workers: los bloquea para QA/engineer
- **No** poner `api_allowed_for_team_lead: true` en adapters budget: los expone como TL
- **No** habilitar Ollama en orch01 (no tiene el ejecutable)
- **No** añadir comentarios `//` en archivos JSON (JSON no admite comentarios)
- **No** mezclar pool TL y pool worker: la separación es la garantía de routing correcto
- **No** ejecutar el quorum por tarea (solo por run, en planificación)

---

## Referencia cruzada de código

| Concepto | Archivo | Función/Clase |
|----------|---------|---------------|
| Pools de adapters | `aiteam/cli.py` | `build_default_orchestrator()` |
| Gate team_lead | `aiteam/router.py` | `_team_lead_allowed()`, `_eligible()` |
| Catálogo de modelos | `aiteam/model_catalog.py` | `default_model_catalog()` |
| Override catálogo | `runtime/model_catalog.json` | — |
| Quorum deliberativo | `aiteam/quorum.py` | `run_planning_quorum()` |
| Trigger del quorum | `aiteam/quorum.py` | `should_apply_planning_quorum()` |
| Orquestación quorum | `api/main.py` | líneas ~1057–1124 |
| Adapters externos | `runtime/adapters.json` | — |
| Perfil Engineer | `aiteam/profiles.py` | `PROFILES["engineer"]` |
| plan_risks no-gate | `aiteam/workflow_planner.py` | `PhaseSpec(phase_id="plan_risks")` |
