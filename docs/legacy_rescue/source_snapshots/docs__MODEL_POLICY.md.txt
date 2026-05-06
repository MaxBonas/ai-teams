# Model Policy

## Objetivo

Definir un catalogo vivo de modelos con ranking de capacidad, confianza y reglas de relevo.

## Fuente de verdad

- Default en codigo: `aiteam/model_catalog.py`
- Override editable por proyecto/maquina: `runtime/model_catalog.json`
- Ejemplo base: `config/model_catalog.example.json`
- Vista operativa unificada: `runtime/provider_ops.json`

## Reglas clave

- `team_lead` solo puede usar modelos `senior_cloud` o `advanced_api`.
- `team_lead` nunca puede usar modelos `local`.
- Si los modelos Pro senior fallan en salud real, el relevo permitido es una API avanzada y eficiente.
- La salud real consolidada se toma de `runtime/provider_ops.json`.
- Las alertas operativas se consolidan en `runtime/provider_ops.json` y pueden emitirse a eventos/mailbox cuando cambie el estado.

## Campos del catalogo

- `adapter_name`
- `provider`
- `model`
- `tier`
- `intelligence_rank`
- `coding_rank`
- `reasoning_rank`
- `trust_rank`
- `local_allowed_for_team_lead`
- `api_allowed_for_team_lead`
- `notes`

## Tiers sugeridos

- `senior_cloud`
- `advanced_api`
- `budget_api`
- `local`

## Uso recomendado

- Ajusta `runtime/model_catalog.json` para calibrar prioridades reales sin tocar codigo.
- Mantiene `openai_pro_cli`, `gemini_pro_cli`, `claude_pro_cli` y fallbacks en el catalogo.
- Baja confianza o ranking cuando un modelo sea inestable, caro o flojo para un rol.
- El catalogo puede representar la *clase* de modelo objetivo aunque el runtime actual exponga una variante concreta. Ejemplo: `gpt-5.4 / gpt-4o class` o `Gemini 3.1 Pro / Gemini Pro class`.
- No descartes modelos baratos: ubicalos en `budget_api` para que el router pueda degradar hacia ellos cuando suba la presion de presupuesto o se alcancen limites/caidas de modelos top.

## Estado actual recomendado para Team Lead

1. `openai_pro_cli`
2. `gemini_pro_cli`
3. `claude_pro_cli` (solo cuando vuelva a estar sano en smoke real)
4. `advanced_api`

`ollama_qwen_coder_local` queda expresamente excluido de `team_lead`.

## Politica de fallback economico

- Los modelos baratos siguen formando parte del sistema y no se descartan.
- Se agrupan en `budget_api`.
- Para roles no `team_lead`, cuando sube la presion de presupuesto API el router puede degradar hacia `budget_api` antes de bloquear servicio.
- Ejemplos actuales: `gpt-4o-mini`, `groq_fallback`.

## Nota sobre modelos top y modelo local

- Si las cuentas/runtimes exponen modelos top como GPT-5.4, Gemini 3.1 Pro o Claude top-class, deben entrar arriba del ranking cloud.
- El ranking real debe reflejar dos cosas a la vez: capacidad teorica y confianza operativa actual.
- `qwen2.5-coder:32b` queda descartado para esta maquina como opcion principal: es claramente mas capaz que 14B, pero su tamano (~20 GB en Ollama) no encaja bien con una GPU de ~12 GB VRAM para uso multiagente eficiente.
- Se mantiene `aiteam-qwen-coder:14b` como modelo local principal por equilibrio entre capacidad, latencia y estabilidad.
