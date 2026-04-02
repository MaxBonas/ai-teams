# Visión: Lead Adaptativo — Autodiagnóstico, Recuperación y Pausa Conversacional

**Fecha**: 2026-04-02
**Estado**: ✅ IMPLEMENTADO (2026-04-03) — A1/A2/A3/A4/A5 completos. Suite: `858 passed`.
**Módulos**: `aiteam/run_health.py`, `aiteam/lead_memory.py`, `aiteam/lead_control.py`
**Relacionado**: `docs/HISTORY.md` (bloque 5), `CONVERSATIONAL_AGENTS_PLAN.md`

---

## 1. Problema central

El Team Lead planifica al principio (`lead_intake`) y evalúa al final (`lead_close`), pero durante la
ejecución de las fases es **ciego**:

- No sabe cuántas veces el evidence gate rechazó una fase ni por qué.
- No sabe qué fases encontraron `no_eligible_adapter` ni qué rol fue afectado.
- No sabe si un modelo estaba caído, si faltaba una API key o si el contexto era insuficiente.
- Solo puede reaccionar en `lead_close` con lo que ya pasó, no puede pausar a mitad de carrera para
  pedir información crítica ni para ajustar el plan dinámicamente.
- El usuario solo se entera del problema en el informe final, si es que el Lead lo menciona.

El resultado es runs que fallan silenciosamente, que producen outputs degradados sin diagnóstico, o
que simplemente repiten el mismo error en la siguiente run porque no hay aprendizaje entre iteraciones.

---

## 2. Capacidades actuales (lo que ya existe)

Antes de definir qué falta, es crucial saber qué hay:

| Capacidad | Dónde vive | Estado |
|---|---|---|
| `[CLARIFY: "pregunta"]` — Lead pide aclaración al usuario | `lead_intake` únicamente | ✅ Funciona |
| `[FORCE_GATE: "phase_id"]` — Lead reabre gate de una fase | `lead_close` | ⚠️ Bug (URGENTE-1) |
| `[RETRY_ROUTE: "phase_id"]` — Lead pide rerouteo de fase | `lead_close` | ⚠️ Bug (URGENTE-1) |
| `[SKIP: "phase_id"]` — Lead elimina fase del plan | `lead_intake` únicamente | ✅ Funciona |
| `[ABORT_PHASES: "razón"]` — Lead cancela fases, responde directo | `lead_intake` | ✅ Funciona |
| `[ADVISORY_MODE: "razón"]` — Lead acepta resultado en modo advisory | `lead_close` | ✅ Funciona |
| `[DELEGATE]` y variantes — Lead delega scout antes de planificar | `lead_intake` | ✅ Funciona |
| `TaskState.WAITING_USER` — estado de pausa en taskboard | `taskboard.py` | ✅ Implementado |
| `taskboard.wait_for_user_input()` — método de pausa | `taskboard.py` | ✅ Implementado |

**Lo que falta:**
- Lead no recibe un resumen estructurado de errores en `lead_close`.
- `[CLARIFY]` solo funciona en `lead_intake`; no hay equivalente en `lead_close`.
- No hay directiva para saltar una fase ya ejecutada que falló irrecuperablemente.
- No hay directiva para entrega degradada explícita con diagnóstico.
- No hay briefing de capacidades disponibles antes de planificar.

---

## 3. Visión objetivo: el Lead como orquestador adaptativo

El Lead debe poder:

1. **Ver** el estado real de la run al llegar a `lead_close`: qué fases fallaron el gate, cuántas
   veces, por qué razón, qué errores de routing ocurrieron, qué recursos no estuvieron disponibles.

2. **Preguntar** al usuario cuando un error es no-resolvible internamente: falta de API key,
   objetivo ambiguo que solo el usuario puede resolver, decisión de producto bloqueante.

3. **Adaptar** el plan en respuesta a lo que pasó: saltar una fase que falló irrecuperablemente,
   aceptar entrega parcial, pedir rerouteo con contexto adicional, extender el presupuesto.

4. **Abortar con diagnóstico** cuando la run no puede completarse y el usuario necesita saber por qué
   y qué hacer para que funcione la próxima vez.

5. **Planificar con conocimiento de capacidades**: antes de proponer un plan, saber qué modelos están
   disponibles, qué APIs tienen key configurada, qué herramientas MCP responden.

El resultado es un sistema que **se repara a sí mismo** dentro de lo posible y, cuando no puede,
**explica con precisión por qué falló y qué necesita el usuario para resolverlo**.

---

## 4. Nuevo concepto: Run Health Report

El mecanismo más impactante y menos invasivo es el **Run Health Report**: un bloque estructurado que
el sistema inyecta automáticamente en el contexto de `lead_close`, justo antes de que el Lead evalúe
el resultado.

### 4.1 Estructura del bloque

```
== RUN HEALTH REPORT ==
Fases completadas: 3 / 5
Fases con evidencia aceptada: 2 / 3

GATE REJECTIONS:
  - phase=research, iterations=3/3 (máx), última razón: output placeholder detectado
  - phase=build, iterations=1/2, última razón: output demasiado corto para rol engineer

ROUTING ERRORS:
  - phase=context_curator, error=no_eligible_adapter, rol=researcher, intento=1
  - phase=review, error=model_unavailable, modelo=claude-opus-4, fallback=gemini-pro-1.5

FASES SALTADAS / ABORTADAS:
  - phase=qa, razón: ABORT_PHASES desde lead_intake

RECURSOS NO DISPONIBLES:
  - API key ausente: openai (afecta roles: engineer, reviewer)
  - Modelo no disponible en este momento: claude-opus-4 (fallback activo: gemini-pro-1.5)

PRESUPUESTO:
  - Rondas usadas: 8 / 10
  - Extensiones automáticas: 1
== FIN REPORT ==
```

### 4.2 Dónde se genera

**Archivo**: `aiteam/run_health.py` (nuevo módulo)

```python
# aiteam/run_health.py
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class PhaseHealthEntry:
    phase_id: str
    gate_iterations: int = 0
    gate_max: int = 0
    last_gate_reason: str = ""
    routing_errors: list[dict] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    completed: bool = False
    evidence_accepted: bool = False

@dataclass
class RunHealthReport:
    phases: list[PhaseHealthEntry] = field(default_factory=list)
    missing_api_keys: list[str] = field(default_factory=list)
    unavailable_models: list[str] = field(default_factory=list)
    rounds_used: int = 0
    round_budget: int = 0
    auto_extensions: int = 0

    def to_prompt_block(self) -> str:
        """Formatea el report como bloque de texto para inyectar en el prompt del Lead."""
        lines = ["== RUN HEALTH REPORT =="]
        completed = sum(1 for p in self.phases if p.completed)
        accepted = sum(1 for p in self.phases if p.evidence_accepted)
        lines.append(f"Fases completadas: {completed} / {len(self.phases)}")
        lines.append(f"Fases con evidencia aceptada: {accepted} / {max(completed, 1)}")

        gate_issues = [p for p in self.phases if p.gate_iterations > 0]
        if gate_issues:
            lines.append("\nGATE REJECTIONS:")
            for p in gate_issues:
                lines.append(
                    f"  - phase={p.phase_id}, iterations={p.gate_iterations}/{p.gate_max}, "
                    f"última razón: {p.last_gate_reason or 'desconocida'}"
                )

        routing_issues = [p for p in self.phases if p.routing_errors]
        if routing_issues:
            lines.append("\nROUTING ERRORS:")
            for p in routing_issues:
                for err in p.routing_errors:
                    lines.append(
                        f"  - phase={p.phase_id}, error={err.get('error')}, "
                        f"rol={err.get('role', '?')}, modelo={err.get('model', '?')}"
                    )

        skipped = [p for p in self.phases if p.skipped]
        if skipped:
            lines.append("\nFASES SALTADAS:")
            for p in skipped:
                lines.append(f"  - phase={p.phase_id}, razón: {p.skip_reason or 'Lead decision'}")

        if self.missing_api_keys:
            lines.append("\nRECURSOS NO DISPONIBLES:")
            for key in self.missing_api_keys:
                lines.append(f"  - API key ausente: {key}")
        if self.unavailable_models:
            for model in self.unavailable_models:
                lines.append(f"  - Modelo no disponible: {model}")

        lines.append(f"\nPRESUPUESTO:")
        lines.append(f"  - Rondas usadas: {self.rounds_used} / {self.round_budget}")
        if self.auto_extensions:
            lines.append(f"  - Extensiones automáticas: {self.auto_extensions}")

        lines.append("== FIN REPORT ==")
        return "\n".join(lines)
```

### 4.3 Cómo se rellena

El `Orchestrator` ya tiene acceso a todos los datos necesarios:

- **Gate iterations**: el orchestrator registra cuántas veces se intenta el gate por fase. Este
  contador debe exponerse como evento `gate_iteration` (ya existe) y acumularse en un dict
  `_phase_gate_counts: dict[str, int]` en `_run_chat_workflow()`.

- **Routing errors**: cuando `_to_bool(failure_reason == "no_eligible_adapter")` ya se maneja en
  el orchestrator (línea ~2175). Añadir al `RunHealthReport` con `phase_id` y `role`.

- **Missing API keys**: el `Router` ya sabe qué providers tienen key disponible. Exponer método
  `router.get_missing_api_keys() -> list[str]`.

- **Unavailable models**: el router ya trackea fallbacks. Exponer método
  `router.get_unavailable_models() -> list[str]` con los modelos que fallaron en esta run.

El `RunHealthReport` se construye justo antes de llamar a `lead_close` y se inyecta como bloque
adicional en el prompt del Lead:

```python
# En orchestrator._build_lead_close_context() o equivalente:
health_report = self._build_run_health_report()
health_block = health_report.to_prompt_block()
# Se añade al contexto del lead_close antes de llamar al LLM
context_parts.append(health_block)
```

---

## 5. Nuevas directivas LCP

### 5.1 `[PAUSE_FOR_USER: "pregunta"]`

**Propósito**: Lead pausa la run en `lead_close` para hacer una pregunta al usuario que no puede
resolver internamente. Diferente de `[CLARIFY]` (que solo existe en `lead_intake`): esta directiva
puede emitirse cuando el Lead ya tiene el health report y decide que sin información del usuario no
puede cerrarse bien.

**Casos de uso**:
- "El evidence gate rechazó la fase de build 3 veces por output placeholder. ¿Quieres que lo
  reintente con un modelo diferente, o prefieres revisar el objetivo de esa fase?"
- "La API key de OpenAI no está configurada. ¿Tienes acceso? Puedo replantear el flujo con modelos
  alternativos si no."
- "El Researcher encontró dos enfoques contradictorios. ¿Cuál priorizar: A o B?"

**Patrón de emisión**:
```
[PAUSE_FOR_USER: "El gate rechazó la fase build 3 veces (placeholder). ¿Quieres reintentar con otra ruta o ajustar el objetivo de esa fase?"]
```

**Procesamiento** (en `api/main.py` o `api/routers/aiteam.py`, junto con `FORCE_GATE` y `RETRY_ROUTE`):
```python
def _extract_pause_for_user_from_outputs(outputs: list[str]) -> str | None:
    pattern = re.compile(r'\[PAUSE_FOR_USER:\s*"(.+?)"\]', re.DOTALL | re.IGNORECASE)
    for output in reversed(outputs):
        m = pattern.search(str(output or ""))
        if m:
            return m.group(1).strip()
    return None
```

**Efecto**:
1. Run transiciona a `TaskState.WAITING_USER` (ya implementado en taskboard).
2. La pregunta se emite como mensaje visible en el chat thread (no como mensaje del sistema).
3. El usuario responde via chat. La respuesta se inyecta como `[Respuesta del usuario: "..."]`
   en el contexto del siguiente intento de `lead_close`.
4. La run reanuda `lead_close` con la respuesta disponible.

**Añadir a `extract_lcp_directives()` en `lead_control.py`**:
```python
m = re.search(r'\[PAUSE_FOR_USER:\s*"(.+?)"\]', t, re.DOTALL | re.IGNORECASE)
if m:
    result["pause_for_user"] = m.group(1).strip()
```

**Añadir a `_SELECTIVE_LCP_PATTERNS`**:
```python
"PAUSE_FOR_USER": r'\[PAUSE_FOR_USER:\s*"[^"]+"\]',
```

**Añadir al prompt del Lead** (en `profiles.py`, bloque de directivas de `lead_close`):
```
[PAUSE_FOR_USER: "pregunta"] — si necesitas respuesta del usuario para resolver un bloqueo no resolvible internamente
(API key faltante, decisión de producto, ambigüedad crítica post-ejecución). Solo 1 pregunta, concreta y accionable.
El sistema pausará el run y el usuario podrá responder desde el chat. Recibirás su respuesta en el siguiente intento de lead_close.
```

---

### 5.2 `[SKIP_PHASE: "phase_id" reason="..."]`

**Propósito**: Lead decide en `lead_close` que una fase que ya se ejecutó (y falló o produjo output
inaceptable) debe marcarse como saltada para que el informe final sea honesto sobre qué se completó.
Diferente de `[SKIP]` (que elimina fases antes de que se ejecuten desde `lead_intake`): esta directiva
opera sobre fases ya ejecutadas.

**Casos de uso**:
- "La fase `build` falló el gate 3 veces y el output es placeholder. Saltarla y entregar lo que
  tienen las demás fases."
- "La fase `review` produjo output vacío por `no_eligible_adapter`. No hay revisión disponible,
  continuar sin ella."

**Patrón de emisión**:
```
[SKIP_PHASE: "build" reason="gate rechazado 3 iteraciones, output placeholder irrecuperable"]
```

**Procesamiento**:
```python
def _extract_skip_phase_from_outputs(outputs: list[str]) -> tuple[str, str] | None:
    pattern = re.compile(
        r'\[SKIP_PHASE:\s*"([^"]+)"(?:\s+reason="([^"]*)")?\]', re.IGNORECASE
    )
    for output in reversed(outputs):
        m = pattern.search(str(output or ""))
        if m:
            return m.group(1).strip(), (m.group(2) or "").strip()
    return None
```

**Efecto**: marca la tarea de esa fase como `SKIPPED` en el taskboard con motivo visible, la excluye
del informe de evidencia, pero la lista en el resumen final para transparencia.

---

### 5.3 `[DEGRADE: scope="minimal|partial" reason="..."]`

**Propósito**: Lead acepta entregar un resultado de menor alcance de forma explícita y documentada,
en lugar de reintentar indefinidamente o fallar sin diagnóstico.

- `scope="partial"`: se entregan las fases que sí completaron. Las que fallaron se mencionan.
- `scope="minimal"`: solo se entrega el análisis del Lead con los datos disponibles. No hay
  artefactos de fases.

**Casos de uso**:
- "Solo el Researcher completó exitosamente. El build falló. Entrego análisis + hallazgos del
  Researcher en modo partial."
- "Ninguna fase produjo evidencia aceptable. El objetivo era demasiado amplio. Entrego mi análisis
  del problema en modo minimal."

**Patrón de emisión**:
```
[DEGRADE: scope="partial" reason="build y review sin evidencia; researcher completó con hallazgos sólidos"]
```

**Procesamiento**: añadir a `extract_lcp_directives()`:
```python
m = re.search(r'\[DEGRADE:\s*scope="(minimal|partial)"(?:\s+reason="([^"]*)")?\]', t, re.IGNORECASE)
if m:
    result["degrade"] = {"scope": m.group(1), "reason": (m.group(2) or "").strip()}
```

**Efecto en el informe**: el resumen del chat incluye `degraded: true`, `degrade_scope`, y
`degrade_reason`. El frontend muestra un badge `⚠ Entrega degradada` con el motivo.

---

### 5.4 `[ABORT: reason="..."]`

**Propósito**: Lead termina la run con un diagnóstico explícito porque no puede producir ningún
resultado útil y el usuario necesita hacer algo antes de volver a intentarlo.

**Diferencias con directivas existentes**:
- `[REJECT]` opera en `lead_intake` (antes de ejecutar fases).
- `[ADVISORY_MODE]` produce un resultado, aunque sea opinativo.
- `[ABORT]` opera en `lead_close` después de ejecutar fases y confirmar que nada funcionó.

**Casos de uso**:
- "No hay ningún modelo disponible para los roles críticos de esta tarea. Configurar al menos una
  API key antes de continuar."
- "El objetivo requiere acceso al filesystem del proyecto pero el workspace está vacío."

**Patrón de emisión**:
```
[ABORT: reason="No hay modelos disponibles para engineer ni reviewer. Configurar OpenAI o Gemini API key."]
```

**Procesamiento**: marca la run como `ABORTED` con mensaje de diagnóstico visible en el chat.

---

## 6. Briefing de capacidades pre-run

Antes de que `lead_intake` planifique, el sistema debe informarle qué recursos tiene disponibles.
Esto permite al Lead adaptar el plan a la realidad del entorno.

### 6.1 Estructura del briefing

```
== SYSTEM CAPABILITIES ==
Modelos disponibles (con API key activa):
  - gemini-pro-1.5 (pro_cloud) — disponible
  - claude-haiku-3 (haiku) — disponible
  - claude-opus-4 (senior_cloud) — API key presente, estado: desconocido

Modelos NO disponibles (sin API key):
  - gpt-4o (pro_cloud) — OPENAI_API_KEY ausente
  - mistral-large (local_cloud) — MISTRAL_API_KEY ausente

MCPs disponibles: filesystem, context7
MCPs con error: browser_mcp (último ping: timeout)
== FIN CAPABILITIES ==
```

### 6.2 Dónde se inyecta

En `api/main.py` o `api/routers/aiteam.py`, antes de lanzar `lead_intake`:

```python
def _build_capabilities_briefing(router, mcp_status: dict) -> str:
    lines = ["== SYSTEM CAPABILITIES =="]
    available = router.list_available_models()
    unavailable = router.list_unavailable_models()

    if available:
        lines.append("Modelos disponibles:")
        for model in available:
            lines.append(f"  - {model['id']} ({model['tier']}) — disponible")
    if unavailable:
        lines.append("Modelos NO disponibles (sin API key o error):")
        for model in unavailable:
            lines.append(f"  - {model['id']} ({model['tier']}) — {model['reason']}")

    working_mcps = [k for k, v in mcp_status.items() if v == "ok"]
    broken_mcps = [k for k, v in mcp_status.items() if v != "ok"]
    if working_mcps:
        lines.append(f"MCPs disponibles: {', '.join(working_mcps)}")
    if broken_mcps:
        lines.append(f"MCPs con error: {', '.join(broken_mcps)}")

    lines.append("== FIN CAPABILITIES ==")
    return "\n".join(lines)
```

**Cuándo inyectar**: solo si el briefing tiene información relevante (modelos faltantes o MCPs
caídos). No añadir el bloque si todo está disponible — reducir ruido cuando el entorno es sano.

---

## 7. Flujo completo de run adaptativa

```
Usuario envía petición
        │
        ▼
[lead_intake]
  ┌─ Recibe: capabilities briefing (si hay recursos faltantes)
  ├─ Decide: ¿tengo suficiente para planificar?
  │   ├─ No: [CLARIFY] → WAITING_USER → usuario responde → reintenta lead_intake
  │   ├─ No (recursos): [ABORT: "falta X"] → run abortada con diagnóstico
  │   └─ Sí: emite WORKFLOW_PLAN + directivas opcionales
  └─ Output: plan de fases
        │
        ▼
[Ejecución de fases] ← el Lead no interviene aquí (por ahora)
  Cada fase: routing → ejecución → evidence gate
  Errores registrados en RunHealthReport en tiempo real
        │
        ▼
[lead_close]
  ┌─ Recibe: outputs de fases + RunHealthReport estructurado
  ├─ Evalúa: ¿qué funcionó, qué falló y por qué?
  ├─ Decide con directivas:
  │   ├─ [FORCE_GATE: "phase"] → reabre gate de esa fase
  │   ├─ [RETRY_ROUTE: "phase"] → reintenta esa fase con otra ruta
  │   ├─ [SKIP_PHASE: "phase" reason="..."] → acepta que esa fase falló
  │   ├─ [PAUSE_FOR_USER: "pregunta"] → pausa y pregunta al usuario
  │   │       └─ usuario responde → lead_close reintentado con respuesta
  │   ├─ [DEGRADE: scope="partial"] → entrega parcial documentada
  │   ├─ [ABORT: reason="..."] → aborta con diagnóstico accionable
  │   └─ (ninguna) → cierre normal
  └─ Output: informe final
```

---

## 8. Mecanismo de pausa y reanudación

### 8.1 Estado actual

`TaskState.WAITING_USER` ya existe. `taskboard.wait_for_user_input()` también. El mecanismo ya se
usa para `[CLARIFY]` en `lead_intake`. Lo que falta es extenderlo a `lead_close`.

### 8.2 Lo que hay que añadir

**Backend (`api/routers/aiteam.py`)**:

```python
# Después de procesar lead_close outputs:
pause_question = _extract_pause_for_user_from_outputs(lead_close_outputs)
if pause_question:
    # 1. Transicionar la tarea lead_close a WAITING_USER
    taskboard.wait_for_user_input(lead_close_task_id, question=pause_question)

    # 2. Emitir evento SSE con la pregunta
    yield _sse_event("lead_paused", {
        "chat_id": chat_id,
        "question": pause_question,
        "phase": "lead_close",
    })

    # 3. La run queda en WAITING_USER hasta que el usuario responda
    return  # no cerrar la run, esperar respuesta
```

**Endpoint de respuesta** (ya debe existir o crear):
```
POST /api/aiteam/chat/{chat_id}/resume
Body: { "user_response": "mi respuesta" }
```

Acción: inyecta `[Respuesta del usuario a tu pregunta: "..."]` al contexto del siguiente intento de
`lead_close` y reanuda la run.

**Frontend** (`TeamChat.tsx`):
- Cuando el chat recibe un evento SSE `lead_paused`, muestra un bloque de pregunta prominente con
  un input para que el usuario responda, en lugar del spinner habitual.
- La respuesta se envía al endpoint `/resume`.
- Al recibir respuesta del usuario, el estado vuelve a "running" y el progreso continúa.

---

## 9. Cómo instruir al Lead en el prompt

El prompt del Lead en `profiles.py` debe añadir, en la sección de directivas de `lead_close`:

```python
# Añadir al bloque de directivas del Team Lead en profiles.py
"DIRECTIVAS DE CIERRE (emitir al final de lead_close según necesidad): "
"[PAUSE_FOR_USER: \"pregunta\"] — pausa la run y pregunta al usuario algo que no puedes resolver "
"internamente (API faltante, decisión de producto, ambigüedad post-ejecución). "
"Solo cuando el bloqueo no es resolvible con las directivas técnicas disponibles. "
"[SKIP_PHASE: \"phase_id\" reason=\"...\"] — acepta que esa fase falló irrecuperablemente "
"y la excluye del informe de evidencia. Usar cuando el gate rechazó 3+ iteraciones y el output "
"es inservible, o cuando hubo no_eligible_adapter sin fallback disponible. "
"[DEGRADE: scope=\"partial\"] — acepta entregar resultado parcial con las fases que sí completaron. "
"[DEGRADE: scope=\"minimal\"] — entrega solo tu análisis del problema sin artefactos de fases. "
"[ABORT: reason=\"...\"] — termina la run con diagnóstico accionable. Usar solo cuando ningún "
"resultado es posible y el usuario necesita resolver algo antes de reintentar. "
"CUANDO USAR EL RUN HEALTH REPORT: el bloque '== RUN HEALTH REPORT ==' describe qué pasó "
"durante la ejecución. Úsalo para fundamentar tus decisiones. Si hay gate rejections, evalúa "
"si [FORCE_GATE] o [SKIP_PHASE] es más apropiado según las iteraciones consumidas. "
"Si hay routing errors con no_eligible_adapter, evalúa [RETRY_ROUTE] o [SKIP_PHASE]. "
"Si hay recursos faltantes que no tienen fallback, usa [PAUSE_FOR_USER] o [ABORT]."
```

### 9.1 Consideración de diseño: instrucción de verificación en prompts de roles de ejecución

> **Nota**: consideración de diseño pendiente de validar, no implementada.

El evidence gate es un guardrail externo: rechaza outputs que no cumplen criterios mínimos. Pero hay una capa anterior que podría reducir la tasa de rechazo del gate antes de que sea necesario aplicarlo: la instrucción explícita de verificación en el prompt de los roles de ejecución (Engineer, QA, Reviewer).

La hipótesis: un Engineer que razona internamente "¿este output realmente funciona?" antes de responder produce menos rechazos del gate que un Engineer que escribe el output y deja al gate decidir.

Si se decide explorar esto, la instrucción en el prompt de Engineer/QA sería algo como:

```
Antes de entregar tu output final, verifica internamente:
- ¿Esto realmente resuelve el objetivo de la tarea?
- Si escribiste código, ¿es ejecutable o hay partes que siguen siendo placeholder?
- ¿Tu output tiene evidencia suficiente o solo describe lo que harías?
```

La diferencia con el evidence gate es que el gate opera sobre el texto ya producido y lo rechaza si no cumple. La instrucción opera durante el razonamiento del modelo, antes de que produzca el output. Ambas capas juntas son más robustas que solo el gate.

**Cuándo validar**: al implementar A1 (RunHealthReport) habrá datos sobre gate rejection rates por rol. Si Engineer y QA tienen tasas de rechazo altas en producción, probar esta instrucción y medir si baja la tasa. Sin datos, no justifica el cambio.

---

## 10. Plan de implementación (fases)

### Fase A1 — RunHealthReport (más impacto, menos invasivo)

**Prerequisito**: URGENTE-1 debe estar resuelto primero (los tests de FORCE_GATE/RETRY_ROUTE deben
pasar para que el Lead pueda tomar decisiones informadas con esas directivas).

**Archivos a crear/modificar**:
- `aiteam/run_health.py` — módulo nuevo con `RunHealthReport`, `PhaseHealthEntry`
- `aiteam/orchestrator.py` — acumular `_phase_gate_counts` y `_phase_routing_errors` durante la run;
  construir `RunHealthReport` antes de llamar a `lead_close`; inyectar `to_prompt_block()` en el
  contexto del Lead
- `aiteam/router.py` — exponer `get_missing_api_keys()` y `get_unavailable_models()` (o equivalentes
  según la estructura interna del router)

**Tests requeridos** (en `tests/test_run_health.py`):
```
test_empty_report_renders_without_errors()
test_gate_rejection_shows_in_report()
test_routing_error_shows_in_report()
test_missing_api_key_shows_in_report()
test_budget_consumed_shows_in_report()
test_all_green_run_renders_cleanly()
test_prompt_block_injected_into_lead_close_context()
```

**Criterio de done**: el Lead recibe el health report en `lead_close` y puede referenciarlo en su
output final. Verificable en logs de eventos (el evento `lead_close_started` debe incluir el bloque
en el contexto).

---

### Fase A2 — `[PAUSE_FOR_USER]` en lead_close

**Prerequisito**: A1 completo (el Lead necesita el health report para tomar la decisión de pausar).

**Archivos a modificar**:
- `aiteam/lead_control.py` — añadir regex en `extract_lcp_directives()` y en
  `_SELECTIVE_LCP_PATTERNS`
- `aiteam/profiles.py` — añadir directiva al prompt del Lead
- `api/routers/aiteam.py` — detectar `pause_for_user` en outputs de `lead_close`, transicionar a
  `WAITING_USER`, emitir evento SSE, pausar
- `api/routers/aiteam.py` — endpoint `POST /api/aiteam/chat/{chat_id}/resume` con inyección de
  respuesta del usuario
- `ide-frontend/src/components/TeamChat.tsx` — escuchar evento `lead_paused`, mostrar bloque de
  pregunta, capturar respuesta del usuario, enviar al endpoint `/resume`

**Tests requeridos** (en `tests/test_api_team_chat.py`):
```
test_chat_pause_for_user_transitions_to_waiting_user()
test_chat_resume_with_user_response_injects_answer()
test_chat_pause_then_resume_completes_run()
```

---

### Fase A3 — `[SKIP_PHASE]` y `[DEGRADE]` en lead_close

**Prerequisito**: A1 completo.

**Archivos a modificar**:
- `aiteam/lead_control.py` — añadir regex para `SKIP_PHASE` y `DEGRADE`
- `aiteam/profiles.py` — añadir directivas al prompt del Lead
- `api/routers/aiteam.py` — procesar `skip_phase` (marcar tarea como skipped con motivo),
  procesar `degrade` (añadir al resumen del chat)
- `aiteam/taskboard.py` — añadir método `skip_task(task_id, reason)` si no existe

**Tests requeridos**:
```
test_skip_phase_marks_task_skipped_with_reason()
test_degrade_partial_appears_in_chat_summary()
test_degrade_minimal_appears_in_chat_summary()
test_abort_from_lead_close_terminates_run_with_diagnosis()
```

---

### Fase A4 — Briefing de capacidades pre-run

**Prerequisito**: ninguno (independiente de A1-A3, pero de menor prioridad).

**Archivos a modificar**:
- `aiteam/run_health.py` — añadir `build_capabilities_briefing(router, mcp_status)`
- `api/routers/aiteam.py` — generar briefing antes de `lead_intake`, inyectar solo si hay recursos
  faltantes
- `aiteam/profiles.py` — documentar en prompt que puede recibir el bloque SYSTEM CAPABILITIES

**Tests requeridos**:
```
test_capabilities_briefing_omitted_when_all_available()
test_capabilities_briefing_includes_missing_keys()
test_capabilities_briefing_includes_broken_mcps()
test_lead_intake_receives_capabilities_block()
```

---

## 11. Orden de ejecución recomendado

```
URGENTE-1 (fix tests LCP)          ← prerequisito obligatorio
    │
    ▼
A1 (RunHealthReport)               ← mayor impacto, Lead puede ver errores
    │
    ▼
A3 (SKIP_PHASE + DEGRADE)          ← Lead puede adaptar con datos del report
    │
    ├─▶ A2 (PAUSE_FOR_USER)        ← más complejo (frontend + resume endpoint)
    │
    └─▶ A4 (Capabilities briefing) ← independiente, menor prioridad
```

**Nota**: A2 requiere cambios en el frontend y un nuevo endpoint REST. Separar en su propio PR para
no bloquear A1 y A3 que son solo backend.

---

## 12. Puntos débiles conocidos y restricciones

- **`TeamChat.tsx` (73 KB)**: añadir el bloque de `PAUSE_FOR_USER` no requiere refactor del archivo
  entero. Añadir solo un condicional que renderice el bloque de pregunta cuando el estado SSE sea
  `lead_paused`. Extraer a hook solo si se necesita tocar SSE/progreso por otra razón.

- **Compatibilidad con runs existentes**: el `RunHealthReport` es un bloque adicional en el prompt.
  Si el Lead no sabe interpretarlo (versión de prompt anterior), simplemente lo ignorará. No es un
  cambio breaking.

- **El Lead puede equivocarse**: si el Lead emite `[SKIP_PHASE]` de forma agresiva en runs normales,
  se perderán fases válidas. El prompt debe ser preciso: "usa `SKIP_PHASE` solo cuando el gate
  rechazó 3+ iteraciones o hubo `no_eligible_adapter` sin fallback". No es un guardrail técnico,
  es una instrucción de comportamiento — el Lead puede ignorarla, pero en la práctica los LLMs
  buenos siguen constraints bien expresados.

- **`WAITING_USER` en medio de la run**: la reanudación requiere que la tarea `lead_close` no haya
  sido marcada como `COMPLETED` o `FAILED`. El taskboard ya maneja esto (línea ~208: no propaga ni
  resetea tareas en `WAITING_USER`). Verificar que el timeout de run no expire mientras el usuario
  está respondiendo.

- **`[ABORT]` vs `[ADVISORY_MODE]`**: el Lead debe preferir `ADVISORY_MODE` si puede dar algo útil,
  y solo usar `ABORT` cuando literalmente no hay nada que entregar. Instruir explícitamente en el
  prompt que `ABORT` es el último recurso.

---

## 13. Memoria primaria del Lead por proyecto

### 13.1 El problema

El prompt de sistema del Lead en `profiles.py` es **estático y universal**: las mismas instrucciones
para todos los proyectos, todas las runs, todos los contextos. El Lead no tiene conciencia de:
- Qué falló en runs anteriores de este mismo proyecto.
- Qué decisiones de arquitectura se tomaron.
- Qué restricciones o preferencias aplica el equipo de este proyecto.
- Qué recursos estuvieron problemáticos antes (APIs caídas, modelos sin key).
- Qué `.aiteam/instructions.md` ha definido el dueño del proyecto sobre cómo debe trabajar el equipo.

Cada run empieza desde cero. El Lead es capaz pero amnésico por diseño.

### 13.2 La visión: Lead Memory File

Al inicio de cualquier proyecto, el sistema genera (o lee, si ya existe) un archivo
**`lead_memory.md`** en la carpeta de runtime del proyecto (`.aiteam/lead_memory.md` en la visión
B9a, o `runtime/lead_memory.md` actualmente).

Este archivo se inyecta en el contexto del Lead **antes de `lead_intake`**, antes del briefing de
capacidades, antes de cualquier otra cosa. Es la primera cosa que lee el Lead al comenzar una run.

Contiene:

```markdown
# Lead Memory — [nombre del proyecto]

## Identidad del sistema
Eres el Team Lead de AI Teams. Tu rol es planificar, coordinar y evaluar. No ejecutas código
directamente. Tienes un equipo (Scout, Researcher, Engineer, Reviewer, QA) y directivas LCP
para adaptar el flujo. Recibirás un Run Health Report antes de lead_close con el estado real de
la ejecución.

## Contexto de este proyecto
- Proyecto: [nombre]
- Stack: [tecnología principal]
- Objetivo de largo plazo: [descripción]
- Restricciones conocidas: [lista]

## Historial de runs recientes
- Run 2026-04-01: objetivo=migrar SQLite, resultado=exitoso, fases=4, duración=8min
- Run 2026-04-02: objetivo=fix tests LCP, resultado=parcial (build falló gate 3x),
  razón=modelo engineer sin key OpenAI, acción_tomada=ADVISORY_MODE

## Capacidades conocidas de este entorno
- API keys configuradas: gemini, anthropic
- API keys ausentes: openai (afecta roles engineer/reviewer en tareas de código complejas)
- MCPs disponibles: filesystem, context7

## Instrucciones del equipo (.aiteam/instructions.md)
[contenido de `.aiteam/instructions.md` del proyecto si existe]

## Decisiones arquitectónicas relevantes
[extraídas automáticamente de docs/DECISION_LOG.md o de conversaciones anteriores donde el Lead
 emitió una decisión explícita]
```

### 13.3 Dos capas de contenido

**Capa estática** (escrita por el humano o generada una vez):
- `.aiteam/instructions.md` del proyecto — quién es el equipo, cómo quiere trabajar el dueño del proyecto.
- Restricciones del stack (p.ej. "este proyecto usa solo Python 3.12, nunca async sin razón").
- Objetivo de largo plazo del proyecto.

**Capa dinámica** (mantenida automáticamente por el sistema):
- Historial de runs recientes: objetivo, resultado, fases completadas, errores significativos.
- Capacidades observadas del entorno: qué APIs tuvieron key en la última run, qué MCPs respondieron.
- Decisiones que el Lead tomó en runs anteriores (extraídas de sus outputs y de los eventos LCP
  registrados en el JSONL de eventos).

### 13.4 Formato de actualización automática

Al cierre de cada run (en `lead_close`, después de procesar las directivas), el sistema añade una
entrada al historial:

```python
# En aiteam/run_health.py o en un nuevo aiteam/lead_memory.py
def update_lead_memory(
    project_root: str,
    chat_id: str,
    objective: str,
    result: str,           # "exitoso" | "parcial" | "fallido" | "abortado"
    phases_completed: int,
    phases_total: int,
    significant_errors: list[str],   # gate rejections, routing errors críticos
    lead_decisions: list[str],       # directivas LCP usadas: ADVISORY_MODE, SKIP_PHASE, etc.
    duration_seconds: int,
) -> None:
    """Añade una entrada al historial de runs en lead_memory.md."""
    memory_path = Path(project_root) / ".aiteam" / "lead_memory.md"
    # ...leer, añadir entrada, truncar historial a N runs recientes, escribir
```

### 13.5 Relación con B8b (`.aiteam/instructions.md`)

`B8b` en el playbook ya prevé leer e inyectar `.aiteam/instructions.md` del proyecto en el prompt del Lead. La
`lead_memory.md` es la extensión natural de eso:

| Archivo | Quién lo escribe | Cuándo se inyecta | Contenido |
|---|---|---|---|
| `.aiteam/instructions.md` | El humano | En `lead_intake` | Instrucciones de equipo del dueño del proyecto |
| `lead_memory.md` (estática) | El humano + sistema al crear proyecto | Antes de `lead_intake` | Identidad, restricciones, objetivo de largo plazo |
| `lead_memory.md` (dinámica) | Sistema automáticamente al cierre de run | Antes de `lead_intake` | Historial, capacidades observadas, decisiones pasadas |

La sección estática de `lead_memory.md` es básicamente `.aiteam/instructions.md` fusionado con la configuración
del proyecto. La sección dinámica es el aprendizaje acumulado.

### 13.6 Implementación (Fase A5)

**Archivos a crear/modificar**:
- `aiteam/lead_memory.py` — nuevo módulo: `load_lead_memory()`, `update_lead_memory()`,
  `build_memory_prompt_block()`
- `api/routers/aiteam.py` — inyectar `build_memory_prompt_block()` antes de `lead_intake`;
  llamar `update_lead_memory()` al cierre de `lead_close`
- `aiteam/profiles.py` — documentar en el prompt del Lead que puede recibir un bloque
  `== LEAD MEMORY ==` al inicio de la conversación

**Prerequisito**: B9a (`.aiteam/` como carpeta de runtime) para que la memoria viva en el lugar
correcto del proyecto. Sin B9a, puede vivir en `runtime/lead_memory.md` como transitorio.

**Tests requeridos**:
```
test_lead_memory_created_on_first_run()
test_lead_memory_appends_run_history()
test_lead_memory_truncates_to_recent_runs()
test_lead_memory_injected_before_lead_intake()
test_lead_memory_includes_project_instructions_if_present()
test_lead_memory_skipped_if_empty()
```

**Orden en el flujo de ejecución global**: A1 → A3 → A2 → A4 → B8b → A5.
A5 requiere B8b (`.aiteam/instructions.md`) como insumo. Implementar B8b y A5 juntos tiene sentido.

---

### 13.7 Consideraciones de diseño para A5 — a tener en cuenta antes de implementar

> **Nota**: estas consideraciones no cambian la visión de A5, pero sí pueden influir en cómo se estructura el archivo y el módulo si se aplican. Son hipótesis de diseño con fundamento, no requisitos fijos.

#### Índice ligero vs archivo plano

El diseño actual plantea `lead_memory.md` como un archivo único con secciones. Una alternativa con posible ventaja de escalabilidad: separar el **índice** del **contenido**.

- `lead_memory.md` como índice de punteros ultra-ligeros (~1-2 líneas por entrada, sin contenido completo)
- archivos de tema en `.aiteam/memory/<topic>.md` con el detalle expandido
- el Lead carga el índice siempre; los archivos de tema solo si los referencia

La ventaja potencial: un proyecto de largo plazo con muchas runs no convierte `lead_memory.md` en un archivo pesado que consume tokens solo por existir. El índice se mantiene lean independientemente del historial acumulado.

La desventaja: más complejidad en `lead_memory.py` (gestionar múltiples archivos). Vale la pena solo si el historial crece más de lo que cabe cómodamente en contexto.

**Recomendación provisional**: empezar con archivo plano + truncado a N runs recientes (sección 13.4). Migrar a índice + topics solo si el archivo supera ~4000 tokens en proyectos reales.

---

#### Criterio de escritura estricta: no almacenar lo derivable

Sea cual sea la estructura elegida, hay un criterio que vale la pena hacer explícito en el módulo `lead_memory.py` y en el prompt del Lead:

> Si un dato es derivable del estado actual del proyecto (archivos existentes, código, SQLite del runtime), no se escribe en memoria.

La memoria del Lead debería contener cosas que **no están en ningún otro lugar**:
- preferencias del usuario expresadas en conversación
- restricciones del entorno descubiertas en runs anteriores
- decisiones tomadas con el usuario que no quedaron en ningún artefacto
- errores recurrentes que el Lead debería anticipar

Lo que **no** debería almacenar:
- estructura de archivos del proyecto (derivable leyendo el workspace)
- historial de eventos (ya en el JSONL de eventos y SQLite)
- estado de tareas (ya en `aiteam.db`)
- contenido de `lead_close` outputs anteriores (redundante con eventos)

La razón es práctica: una entrada de memoria que describe algo que ya cambió en el proyecto es activamente dañina. El Lead confía en su memoria — si dice "el proyecto usa pytest", pero el proyecto migró a unittest en la última run, el Lead planificará mal.

**Cómo implementarlo**: en `update_lead_memory()`, cada campo que se escriba debería tener una justificación de "¿por qué esto no está disponible en otro lugar?". Si la respuesta es "está en el JSONL", no se escribe.

---

#### Paso de consolidación antes de inyectar al Lead

El diseño actual inyecta `lead_memory.md` directamente antes de `lead_intake`. Una posible mejora: antes de inyectarlo, ejecutar un **paso de consolidación ligero**.

Qué haría ese paso:
1. leer el índice / archivo de memoria
2. detectar entradas que se contradicen entre sí
3. detectar entradas probablemente obsoletas (p.ej. "API key de OpenAI ausente" cuando en el routing catalog actual sí aparece disponible)
4. producir una versión depurada para inyectar, sin modificar el archivo en disco hasta confirmación

La consolidación no tiene que ser un LLM call separado. Puede ser lógica determinista en Python: si el routing catalog dice que una API está disponible, marcar como obsoleta la entrada de memoria que dice lo contrario. Si dos entradas dicen cosas contradictorias sobre el mismo tema, priorizar la más reciente.

La ventaja: el Lead empieza cada run con memoria que no contradice la realidad observable del entorno, sin que eso requiera intervención del usuario.

**Cuándo implementar**: probablemente en la segunda iteración de A5, no en la primera. La primera versión puede inyectar directamente y confiar en el truncado temporal para mantener frescura. La consolidación es una mejora sobre eso, no un requisito inicial.

---

## 14. Relación con otros documentos

| Documento | Relación |
|---|---|
| `IMPLEMENTATION_PLAYBOOK.md` → URGENTE-1 | Fix de FORCE_GATE/RETRY_ROUTE debe ir primero |
| `IMPLEMENTATION_PLAYBOOK.md` → B8b | `.aiteam/instructions.md` es el insumo principal de la capa estática de `lead_memory` |
| `IMPLEMENTATION_PLAYBOOK.md` → B9a | `.aiteam/` es la carpeta donde vivirá `lead_memory.md` |
| `DESIGN_2026_03_31.md` → E7-D4 | `WAITING_USER` ya implementado, esta vision lo extiende a `lead_close` |
| `CONVERSATIONAL_AGENTS_PLAN.md` | `PAUSE_FOR_USER` + `lead_memory` son pasos hacia continuidad conversacional real |
| `EXTERNAL_PROJECT_RUNTIME_GAPS.md` | El diagnóstico de `no_eligible_adapter` que da el Lead será visible en B9c |
| `LEAD_QUORUM_PROJECT_CONTEXT_VISION.md` | El briefing de capacidades pre-run y la memoria primaria se alinean con Plan/Quorum |
