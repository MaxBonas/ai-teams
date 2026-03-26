# Tasks — AI Team Hybrid Orchestrator

> Ultima actualizacion: 2026-03-22

## Completado

- [x] **Batch 1**: Shared workflow state, result propagation, gate iteration loop, eager dep check, team ledger (2026-03-21)
- [x] **Batch 2**: Agent sessions, tool dispatch, 7 API endpoints (2026-03-21)
- [x] **Batch 3**: MCP server lifecycle manager, catalog sync, 9 MCP API endpoints (2026-03-21)
- [x] **Batch 4**: Real LLM adapters (OpenAI/Anthropic/Google/Groq), team decisions + voting, async chat SSE, skill usage tracking (2026-03-21)
- [x] **UI Redesign**: Progressive disclosure metadata, composer compacto, progress visual con badges, OpsHub status badges, tipografia legible (2026-03-22)
- [x] **Batch 5**: Agentidad — self-delegation, cross-agent memory, session history en retries, eager processing (2026-03-22)
- [x] **Batch 6**: Agentidad avanzada — tool invocation [USE_TOOL], peer dialogue 2 rondas, decision rank enforcement, skill ranking activo (2026-03-22)
- [x] **Backlog sweep**: Budget signaling, tool availability broadcast, agent specialization routing, conflict resolution protocol, mailbox read/unread + inbox queries + API (2026-03-22)
- [x] **Batch 7**: UI observability — event category filter en Timeline, mailbox inbox con unread badges + filtros, test suite 26 tests nuevos (2026-03-22)
- [x] **Batch 8**: Inteligencia de agentes — gate context enrichment (evidencia parseada en reviews), memory-driven prompts (multi-query + failure patterns + specialization), tool recommendation engine (proactivo por capabilities), adaptive error recovery (strategy switching) (2026-03-22)
- [x] **Batch 9**: Evidence Gate por fase — fases plan_*/lead_intake/lead_close/discovery saltan evidence gate; solo build/review/qa/security lo aplican. Fix para bloqueo en proyecto Prueba. 12 tests nuevos (2026-03-22)
- [x] **Batch 10**: Conversational task detection — auto-detección de tareas teóricas/filosóficas/preguntas; evidencia alternativa: doc .md generado, output LLM persistido como artefacto, o respuesta aceptada directamente. Sin impacto en tareas de build. 15 tests nuevos (2026-03-22)

## En Progreso

_(nada activo)_

## Backlog

- [ ] Peer debate logic — dialogo real entre peers con resolucion de desacuerdos
- [ ] Auto task decomposition — fragmentacion automatica de tareas complejas
- [ ] Evidence summarization — resumen curado de diffs para reviewers
- [ ] Domain expert knowledge transfer — compartir expertise entre agentes
