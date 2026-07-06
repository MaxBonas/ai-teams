# DESIGN · Enforcement y visibilidad de la política de costes en el hiring

> Objetivo: hacer **visible y exigible** la promesa central del producto — modelos
> fuertes para seniors, baratos/locales para workers — y cuantificar el ahorro
> (o el sobrecoste) de cada ciclo con datos reales.

Fecha: 2026-07-06 · Estado: diseño aprobado, pendiente de implementación.

---

## 1 · Problema observado

En el primer run real (proyecto "videojuego tipo Rust", 2026-07-05), Lead,
engineer y reviewer corrieron **todos** en `gpt-4.1` vía `openai_api`. El
hiring no tenía alternativa (único canal conectado) y no avisó de que se
estaba pagando tier premium para trabajo Tier 2/3. El usuario no tiene forma
de ver cuánto costó el ciclo ni cuánto habría ahorrado con un canal local.

## 2 · Inventario de lo que ya existe (no reinventar)

| Pieza | Estado |
|---|---|
| `_profile_score` — preferencia local/barato para juniors | Funciona cuando hay opciones |
| `reconcile_project_agent_policy` path 2 — upgrade API-only junior → CLI | Funciona |
| `runs.estimated_cost_cents` / `runs.estimated_savings_cents` | Columnas y paso por scheduler cableados, **nadie las calcula** (siempre 0) |
| `cost_events` + `record_cost` + `check_budget` (agente/mes) + `/api/budget` | Funciona |
| `actual_cost_cents` real en adapters OpenAI/Gemini/Anthropic | Desde 2026-07-06, tablas `_COST_TABLE` por adapter |
| `tool_access` "adapter selected for run execution" con modelo/canal | Se persiste en cada run |
| Provider governor — degraded state + fallback opt-in | Desde 2026-07-06 |

## 3 · Fases

### A1 · Tabla de precios unificada (fundación — pequeña)

- Extraer las tres `_COST_TABLE` de los adapters a `aiteam/pricing.py`:
  - `price_per_mtok(provider, model) -> (in_cents, out_cents)`
  - `estimate_cost_cents(provider, model, input_tokens, output_tokens) -> int`
  - Canales locales (`ollama`, `lmstudio`) → `(0, 0)`.
- Los adapters consumen `pricing.py` (una sola fuente de verdad de precios).
- `typical_tokens_for_role(db_path, role, provider) -> (in, out)`: media móvil
  de `usage_json` de las últimas N runs completadas de ese rol; fallback a
  constantes conservadoras (p. ej. 8k in / 1k out) cuando no hay historia.

### A2 · Estimación ex-ante y savings en el hiring (el core — media)

En el punto donde se elige adapter para una delegación
(`choose_adapter_for_role` / `apply_adapter_policy_to_member` y la creación de
issues del Lead en `lead_intake`):

1. `estimated_cost_cents` = precio del adapter elegido × tokens típicos del rol.
2. `premium_alternative_cents` = coste de la misma tarea con el mejor perfil
   premium conectado del proyecto.
3. `estimated_savings_cents = max(0, premium_alternative_cents - estimated_cost_cents)`.
4. Propagar ambos en el payload del wakeup — el scheduler ya los persiste en
   `runs`, y `record_cost` ya copia `estimated_savings_cents` a `cost_events`.

Nuevo evento `hiring.decision` en `activity_log` por cada asignación:

```json
{
  "role": "engineer",
  "adapter_profile_id": "local_qwen_ollama",
  "model": "qwen2.5-coder:14b",
  "estimated_cost_cents": 0,
  "premium_alternative_cents": 38,
  "estimated_savings_cents": 38,
  "policy_deviation": null
}
```

`policy_deviation` se rellena cuando un rol worker acaba en tier premium, con
la causa mecánica: `"no_local_channel_connected"`, `"local_degraded"`, etc.

### A3 · Enforcement: aviso por defecto, bloqueo opt-in (pequeña tras A2)

- **Aviso (siempre):** si al crear proyecto o en el reconcile algún rol junior
  queda en adapter premium, publicar comentario de sistema en `issue:intake` y
  exponer `policy_deviations` en `/api/loop-health`, con el sobrecoste
  estimado: *"Workers en gpt-4.1: ~38¢/ciclo extra. Conecta Ollama (gemma4/qwen)
  y baja a 0¢."*
- **Bloqueo (`AITEAM_ENFORCE_COST_POLICY=1`):** prohibir adapters premium en
  roles Tier 3 (scouts, curator, test_runner) si existe *cualquier* alternativa
  conectada, aunque puntúe menos en `_profile_score`.

### A4 · Visibilidad en UI (media, puede ir en paralelo tras A2)

- Panel "Coste del ciclo" en la vista Team: gasto real acumulado
  (`cost_events`), desglose por rol y canal, y el número comercial:
  **"ahorro vs todo-premium"** = Σ `estimated_savings_cents`.
- Badge por agente: modelo + canal + coste acumulado.
- Línea de coste en la verificación automática del cierre
  (`_machine_close_verification`): *"Ciclo: 47¢ · ahorro estimado 31¢ vs premium"*.

### A5 · Derivados (posterior)

- Cost circuit breaker: gasto por ciclo sin avance de workspace → pausa + escalación.
- Informe €/entrega por proyecto para comparar perfiles de run.

## 4 · Principios y riesgos

- **La política de coste elige el modelo dentro de la tier; nunca cambia la
  tier.** El routing criticidad×complejidad (Lead-as-Evaluator) decide QUIÉN;
  la política de costes decide CON QUÉ modelo. No mezclar.
- Los estimados ex-ante son aproximados: usar medias móviles reales y
  etiquetarlos como estimación en la UI; el dato duro es `actual_cost_cents`.
- Local "gratis" no es gratis en latencia: mostrar tiempo medio por run junto
  al coste para que la decisión del usuario sea informada.
- No romper el flujo cuando no hay historia de usage (proyecto nuevo):
  fallbacks conservadores y savings = 0 antes que números inventados.

## 5 · Orden de ejecución sugerido

1. **A1** — 1 sesión, sin riesgo (refactor + helpers puros + tests).
2. **A2** — el grueso: `lead_intake` + `project_adapters` + payload de wakeups
   + `hiring.decision`. Tests sobre la cadena wakeup→run→cost_events.
3. **A3** — pequeña una vez existe A2.
4. **A4** — frontend; requiere solo A2 en backend.
5. **A5** — opcional, cuando A2 lleve datos acumulados.
