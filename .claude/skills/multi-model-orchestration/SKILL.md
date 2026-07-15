---
name: multi-model-orchestration
description: "Patrones actuales (2026) de orquestación multi-modelo aplicados al código de AI Teams: routing por coste/capacidad, cascadas con umbral, verificación cruzada entre proveedores y economía de tokens. Usar al diseñar o modificar adapters, policies, hiring_economics, provider_governor, quorum o gates de review."
---

# Orquestación multi-modelo — patrones 2026 mapeados a AI Teams

Estado del arte contrastado (julio 2026) para decisiones de routing, verificación
y coste en este repo. Cada patrón indica dónde vive (o dónde faltaría) en el código.

## 1. Routing por capacidad mínima suficiente ("cheapest capable model")

- **Patrón**: clasificar la tarea (complejidad/criticidad/tipo) y enviarla al modelo
  más barato que la resuelve; los flagship solo para coordinación y decisiones.
  Tres niveles típicos: flagship para orquestar, medio para implementación de
  volumen, mini para operaciones mecánicas. Ahorros medidos del 50-60% frente a
  usar el flagship uniforme; RouteLLM reporta 2× de ahorro manteniendo 95% de calidad.
- **En AI Teams**: `aiteam/policies.py` (SENIOR_ROLES/JUNIOR_ROLES),
  `adapter_policy` (junior_preference=cheap_or_local, senior_preference=advanced),
  `aiteam/action_routing.py` (route_action por criticality+complexity),
  `aiteam/hiring_economics.py` (estimación ex-ante),
  `choose_adapter_for_role` (project_adapters.py).
- **Regla local**: los roles Tier 3 deterministas (test_runner) NO usan LLM —
  ejecutar una suite es `subprocess`, no una conversación (executor:
  `_execute_builtin_test_runner`). Antes de dar un rol a un LLM, pregunta si el
  trabajo es determinista.

## 2. Cascadas con umbral y sus modos de fallo

- **Patrón**: intentar con el barato y escalar solo si no supera un umbral de
  calidad (FrugalGPT). La métrica de salud es la **tasa de escalación**: >50%
  sostenido = umbral demasiado conservador (paga doble); <5% = demasiado permisivo
  (probablemente estás aceptando basura).
- **Riesgo conocido**: *cascade pile-up* — bajo carga o 429s, el barato falla y
  TODO cae al caro. Mitigación: circuit breakers conscientes de rate-limit y cap
  diario de coste.
- **En AI Teams**: `aiteam/provider_governor.py` (pacing TPM + cooldown 429 +
  degraded) cubre el pile-up; `_attempt_adapter_recovery` es una cascada de 1 salto.
  **Hueco**: no medimos tasa de escalación/recovery por proveedor — con la
  telemetría de tokens por canal (cost_events desde 2026-07-15) ya se puede derivar.

## 3. Verificación: planner ≠ verifier, y siempre con recibo

- **Patrón**: quien planifica no verifica; cada subtarea necesita dueño, condición
  de éxito y formato de retorno. Si un agente dice que hizo/verificó algo,
  **exige el recibo** (diff, exit code, artefacto) — nunca su narración.
- **En AI Teams**: agent_reports con provenance (`valid=1 AND is_assignee=1`),
  gate `test_runner_exit_zero_required` (evidencia = exit code real),
  `_machine_close_verification` (el contexto de cierre se computa de reports
  estructurados y escaneo del workspace, jamás de la narración del Lead).
- **Regla local**: cualquier gate nuevo debe fallar HACIA una continuación
  (comentario correctivo + re-wake + escalación con cap), nunca en silencio —
  lección del deadlock de CLI Notas (2026-07-15).

## 4. Sesgo de auto-preferencia del juez (cross-provider review)

- **Dato**: un LLM juez favorece salidas de su propia familia (~10% más de win
  rate medido en GPT-4; el mecanismo es perplejidad baja = familiar = "bueno").
  Mitigación primaria: juez de OTRA familia que el generador. Alternativa:
  panel de 2-3 jueces con voto (más caro).
- **En AI Teams**: `_separation_of_duties_line` (executor) ya SEÑALA cuando maker
  y checker comparten proveedor; `aiteam/quorum.py` existe para cierres críticos.
  **Hueco**: la señal es informativa, no vinculante — para issues de criticidad
  alta, considerar forzar reviewer de proveedor distinto en `choose_adapter_for_role`.

## 5. Economía de tokens como telemetría de primera clase

- **Patrón**: tratar el router como infraestructura de producción — logs,
  presupuestos, alertas. Sin medir tokens por canal no hay decisiones de routing
  honestas (el flat-rate "gratis" también consume presupuesto de TPM y ventana).
- **En AI Teams**: `cost_events` registra tokens también a coste 0 (canal
  suscripción vía codex --json); `scripts/audit_project_db.py` los desglosa por
  canal/agente; `AITEAM_TPM_<PROVIDER>` para pacing.
- **Regla local**: toda feature nueva de adapter debe responder "¿qué deja en
  cost_events?" antes de mergear.

## 6. Anti-patrón: "bag of agents" (error compounding)

- **Dato**: errores por agente se COMPONEN entre agentes encadenados (el "17x
  error trap"): más agentes sin contratos estrictos = menos fiabilidad, no más.
  Pocos agentes con vocabulario de ops cerrado y RBAC en código > muchos agentes
  flexibles.
- **En AI Teams**: matriz RBAC por tier ejecutada en código
  (`forbidden_ops_for_role`, guards preventivos de delegación) — mantenerla como
  única fuente; cualquier rol nuevo entra por `policies.py`, no por prompt.

## Fuentes (julio 2026)

- Multi-LLM orchestration patterns / costes: velsof.com, mindra.co, augmentcode.com
- Cascadas y routing: RouteLLM, FrugalGPT, arxiv 2606.27457 (Cluster-Route-Escalate),
  arxiv 2605.06350 (decision-theoretic cascades), tianpan.co
- Verificación multi-agente: arxiv 2510.17109 (Verification-Aware Planning),
  mikemason.ca (coherence through orchestration), towardsdatascience.com (17x trap)
- Sesgo de juez: arxiv 2410.21819 (Self-Preference Bias), arxiv 2604.16790
  (Bias in the Loop, SE), deepchecks.com (calibración)
