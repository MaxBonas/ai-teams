# Historial condensado

## 2026-05-12 — Arquitectura de tiers, routing y compresión de contexto

Diseño: `docs/DESIGN_2026_05_12.md`. Implementación: `docs/TASKS_2026_05_12.md`.

### Fase 1 — Tier discipline

- Skills actualizados con fronteras explícitas (Tier 2: engineer, reviewer; Tier 3: file_scout, web_scout, context_curator, test_runner).
- `filter_forbidden_ops_for_role()` en `work_contract.py` + filtrado en executor al aplicar acciones.
- Reviewer skill: patrón `result: blocked + blocker: needs_scouting_for_<topic>`.

### Fase 2 — Eliminación de QA Tier 2 + test_runner Tier 3

- `skills/qa.md` eliminado. Reviewer absorbe QA estático.
- Nuevo `skills/test_runner.md`: ejecuta comandos, reporta stdout/exitcode, sin decisiones.
- `requires_qa_gate` marcado como deprecated (siempre False, mantenido para compatibilidad API).
- `test_runner` añadido a `_WORKSPACE_READER_ROLES` y `_TIER3_ROLES`.
- Migración: `docs/MIGRATION_2026_05_12.md`.

### Fase 3 — Action routing (Lead-as-Evaluator)

- Nuevo módulo `aiteam/action_routing.py`: `route_action(criticality, complexity, action_type) → Routing`.
- `criticality=critical` siempre → LEAD_SELF. `test_exec/scout_*` siempre → TIER_3.
- Integrado en `_create_delegated_issue`: override automático del rol propuesto por el LLM.
- Log de actividad `action.routed` por cada decisión.

### Fase 4 — lead_executor

- Nuevo rol Tier 1: `lead_executor` (seniority=senior, hereda adapter del Lead).
- `skills/lead_executor.md`: ejecución de acciones críticas/complejas por `action_type`.
- `_ensure_role_agent` crea el agente con los atributos correctos.
- `pick_role_for_routing(LEAD_SELF, action_type)` → `lead_executor`.

### Fase 5 — Context curator basado en bloques de chars

- Umbral cambia de 8 comentarios → 8 000 chars no sintetizados.
- `append_summary_block()` y `get_context_summary()` en `documents.py`.
- `_maybe_spawn_context_curator` reescrito: calcula chars sin sintetizar desde `synthesized_through_comment_id`; curator done no bloquea re-spawn.
- Wake payload inyecta `context_summary.blocks` y filtra comentarios anteriores al punto sintetizado.
- API: `POST /api/issues/{id}/context-summary/blocks` (validación ≤ 30% ratio) + `GET /api/issues/{id}/context-summary`.
- `skills/context_curator.md` reescrito para modelo de bloques.

### Fase 6 — Thread compact/full view

- API: `GET /api/issues/{id}/thread?view=compact|full`.
- UI: componente `ThreadView` en `ide-frontend/src/components/ThreadView/`.
  - Compact: bloques de síntesis colapsables + comentarios recientes.
  - Full: modal con todos los comentarios vía `?view=full`.
- App.tsx wired to `<ThreadView issueId={...} preloadedComments={...} />`.

**Tests finales: 567 (baseline 534 al empezar).**

---

## 2026-05-04

Reorientacion del producto:

- AI Teams converge hacia un control plane estilo Paperclip sobre SQLite.
- Se conserva el foco en equipos de programacion.
- Se fijan perfiles canonicos: `solo_lead`, `lead_quorum`, `full_team`.
- Se adopta Lead-first con hiring dinamico.
- La delegacion economica pasa a ser objetivo central de producto.
- Suscripciones LLM y APIs se tratan como canales independientes.

Implementado en la reconstruccion:

- schema v2 paralelo;
- migrador dry-run/apply;
- run profiles y team blueprints;
- checkout atomico;
- runs y wakeups basicos;
- scheduler inicial;
- endpoints de control plane;
- retirada de `FileLockRegistry` del camino principal.

Limpieza:

- eliminada documentacion legacy;
- eliminados prompts raiz `CLAUDE.md` y `GEMINI.md`;
- eliminada suite legacy no alineada con el objetivo nuevo;
- limpiado runtime local antiguo.
- realizado rescate selectivo de piezas antiguas valiosas en `docs/legacy_rescue/`, con snapshots aislados y notas de port v2.

## Antes de 2026-05-04

El historial detallado de bloques antiguos queda en Git. No usarlo como roadmap activo.
