# DESIGN · Enforcement de roles y jerarquía — investigación y roadmap

Fecha: 2026-07-08 · Estado: fases 1-2 implementadas; 3-5 propuestas.

## 1 · Principio rector

**Un rol no es un prompt: es un conjunto de capacidades.** Todo lo que el
sistema quiera garantizar sobre un rol debe estar *enforced en código* (gates,
filtros, sandbox) con el prompt como capa de intención, nunca como única
defensa. La literatura de 2026 converge en lo mismo: la topología dominante en
producción (~70%) es orquestador-worker jerárquico precisamente porque permite
presupuestos por rama, cancelación de subtrees y auditoría limpia; y los
sistemas de enforcement en runtime (p. ej. AgentSpec, ICSE'26) externalizan las
reglas del LLM en triggers/predicados/acciones inspeccionables, logrando >90%
de prevención con overhead de milisegundos.

## 2 · Stack de enforcement actual (tras 2026-07-08)

| Capa | Mecanismo | Tipo |
|---|---|---|
| Identidad | `supervisor_agent_id` (cadena al Lead); parentesco verificado en `update_child_issue` | Código |
| Capacidades por rol | **Matriz RBAC de ops** (`work_contract.forbidden_ops_for_role`): Tier 3 = leer+reportar; Tier 2 = trabajar+reportar (sin `create_issue`/`update_child_issue`/`update_plan`); Tier 1 = vocabulario completo | Código |
| Escritura de archivos | Sandbox `read-only` del CLI para roles no-editores + **gate preventivo de `file_ops`** en adapters API + guard de nombres (AGENTS.md/CLAUDE.md…) + resolución estricta de rutas absolutas | Código |
| Routing de trabajo | `route_action(criticidad×complejidad)` puede sobreescribir el rol propuesto por el LLM | Código |
| Anti-bucle | Fix-cycle cap (vía automática) + **freno de churn de delegación** (vía manual, 8 mismo-rol/6h) + cost breaker + loop detector | Código |
| Veracidad | `_machine_close_verification` (veredicto estructurado + scan de stubs + coste) adjunto a todo cierre propuesto por un Lead LLM | Código |
| Auditoría | `role.violation` (no-editor escribió), `role.op_denied` (op fuera de vocabulario), `hiring.decision`, `delegation.churn_blocked` | Código |
| Intención | Skills por rol + contrato de orquestación en el prompt codex | Prompt |

## 3 · Qué hacen otros sistemas (aplicabilidad)

- **OpenAI Agents SDK — handoffs + guardrails + tracing**: cada agente declara
  a quién puede transferir y valida entrada/salida con tripwires. *Aplicable:*
  nuestro equivalente de handoff es el wakeup+AGENT-REPORT; el gap es que el
  report es texto parseado, no un artefacto validado por schema (→ fase 3).
- **LangGraph — el grafo decide quién habla**: las transiciones entre agentes
  son aristas tipadas de una máquina de estados, no decisiones del prompt.
  *Aplicable:* nuestra tabla de routing ya es esto en pequeño; formalizar las
  transiciones válidas de estado de issue por rol (quién puede mover qué estado
  a qué estado) cerraría el hueco restante (→ fase 4).
- **CrewAI — herramientas escopadas por agente**: least privilege por
  construcción; un agente sin la tool no puede usarla. *Aplicable:* la matriz
  RBAC de ops implementada hoy es exactamente esto para nuestro vocabulario.
- **AutoGen/AG2 — allowed speaker transitions**: grafo explícito de quién puede
  suceder a quién en la conversación. *Aplicable:* cubierto por jerarquía de
  supervisión + dedupe de wakeups.
- **MetaGPT — SOPs y artefactos como contrato**: los roles se comunican por
  artefactos estructurados (PRD → diseño → código), no por chat libre.
  *Aplicable:* refuerza la fase 3 (report como schema) y los acceptance
  criteria estructurados del PLAN_2026_07_06.
- **AgentSpec (ICSE'26) — DSL de reglas runtime**: triggers + predicados +
  enforcement fuera del LLM, auditable. *Aplicable:* nuestras reglas están
  dispersas en el executor; consolidarlas en un módulo `policies` inspeccionable
  es la versión pragmática (→ fase 5).
- **Erlang/OTP — supervision trees**: reinicio jerárquico con estrategia por
  rama. *Ya lo tenemos* en espíritu: heartbeat + reconcilers + adapter recovery.

## 4 · Implementado hoy (fases 1-2)

1. **Matriz RBAC de ops por tier** con `role.op_denied` auditado. Antes solo
   Tier 3 estaba filtrado; un engineer podía emitir `create_issue` (contratar),
   `update_child_issue` (dirigir a otros) o `update_plan` (reescribir el plan)
   y el executor lo aplicaba. Verificado en la auditoría: no ocurrió de facto,
   pero nada lo impedía.
2. **Gate preventivo de `file_ops` para roles no-editores.** El sandbox
   read-only solo cubre runs de CLI; en adapters API el Lead podía materializar
   archivos vía ops (solo se detectaba post-hoc con `role.violation`). Ahora se
   bloquea antes de tocar disco.

## 5 · Roadmap propuesto (por retorno/esfuerzo)

- **Fase 3 — AGENT-REPORT como artefacto validado** (medio): sustituir el
  bloque de texto `---AGENT-REPORT---` por un documento estructurado validado
  por schema y escrito solo por el run del rol correspondiente. Elimina la
  clase "el Lead re-narra/blanquea al reviewer" de raíz (hoy mitigada por la
  verificación automática).
- **Fase 4 — Máquina de estados de issue por rol** (medio): tabla declarativa
  de transiciones válidas (p. ej. solo el reviewer o el Lead pueden poner
  `in_review→done` de un issue de engineering; un worker no puede reabrir
  issues ajenas). Hoy `set_status` está poco restringido dentro del issue
  propio.
- **Fase 5 — Consolidar políticas en `aiteam/policies.py`** (bajo, refactor):
  mover las reglas dispersas (matriz de ops, roles no-editores, umbrales de
  breakers, ventanas) a un módulo único declarativo, inspeccionable y testeado
  — la versión pragmática de AgentSpec.
- **Fase 6 — Separation of duties del reviewer** (bajo): señal (no bloqueo)
  cuando reviewer y engineer del mismo issue comparten proveedor/modelo;
  recomendación de quorum con proveedor distinto para cierres críticos.
- **No adoptar**: swarm/peer-to-peer (pierde la jerarquía que es la fortaleza
  del producto) ni migrar a un framework externo (LangGraph/CrewAI) — el modelo
  DB-céntrico con heartbeat es sólido; se adoptan *patrones*, no frameworks.

## 6 · Fuentes

- AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents
  (ICSE 2026) — https://arxiv.org/abs/2503.18666
- The Orchestration of Multi-Agent Systems: Architectures, Protocols, and
  Enterprise Adoption — https://arxiv.org/html/2601.13671v1
- Multi-Agent Systems Explained: 2026 Patterns —
  https://decodethefuture.org/en/multi-agent-systems-explained/
- Guardrails and Best Practices for Agentic Orchestration (Camunda, 2026) —
  https://camunda.com/blog/2026/01/guardrails-and-best-practices-for-agentic-orchestration/
- Best Multi-Agent Frameworks in 2026: LangGraph, CrewAI… —
  https://gurusup.com/blog/best-multi-agent-frameworks-2026
