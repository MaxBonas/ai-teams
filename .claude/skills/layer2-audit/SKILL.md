---
name: layer2-audit
description: "Auditar un proyecto capa-2 de AI Teams (un equipo de agentes trabajando en un workspace) de forma barata en tokens: agregados SQL + invariantes sobre .aiteam/aiteam.db, sin leer transcripciones. Usar cuando se pide revisar el estado, salud o comportamiento de un proyecto que corre bajo el orquestador."
---

# Auditoría de proyectos capa-2 — estrategia barata en tokens

Un proyecto capa-2 es un equipo de agentes (Lead + engineer/reviewer/test_runner…)
trabajando autónomamente sobre un workspace, con todo su estado en
`<workspace>/.aiteam/aiteam.db`. Auditar leyendo transcripciones cuesta cientos de
miles de tokens y es innecesario: el estado estructurado responde casi todo.

## Regla de oro

**No leer transcripciones de runs para auditar.** El comportamiento del equipo se
juzga desde campos estructurados (status, error_code, timestamps, agent_reports,
cost_events). Solo se leen excerpts (`--excerpts`) de runs FALLIDAS concretas, y
solo si un agregado ya apuntó a una anomalía.

## Flujo

1. Localizar la DB: `settings.json` (LOCALAPPDATA/AI Teams) → `projects_root`;
   el proyecto activo está en `runtime/current_workspace.json` del repo.
2. Correr el informe:
   `venv/Scripts/python.exe scripts/audit_project_db.py "<workspace_dir>"`
   (añadir `--excerpts` solo si hay runs fallidas que investigar).
3. Leer los **5 invariantes** del final: cada hit distinto de 0 es un bug o algo
   que investigar:
   - runs 'running' > 30 min (zombis — reconcile_stale_runs no las cazó)
   - wakeups 'running'/'claimed' sin run viva (huérfanos)
   - issues in_progress sin actividad en 2h (estancadas)
   - runs failed sin error_code (taxonomía incompleta)
   - interacciones pendientes de usuario (esperando decisión humana)
4. Cruzar señales clave:
   - `RUNS por error_code` → fallos de infra (subscription_cli_not_found, api_error)
     vs. de producto. Los de infra NO cuentan como fallo del equipo.
   - `RUNS skipped` → rereview_limit_reached, review_evidence_unchanged y
     issue_terminal son guards SANOS, no problemas.
   - `COSTE` por canal/agente → desde 2026-07-15 el canal suscripción también
     registra tokens; un agente con muchos tokens y pocas issues cerradas es sospechoso.

## Qué es señal de deadlock (mirar siempre)

- issue raíz `blocked`/`in_progress` + CERO wakeups pendientes = el proyecto no
  se moverá solo. Fue el patrón del deadlock del quality gate (CLI Notas).
- Un comentario del Lead diciendo "cierro/marco como completada" mientras la issue
  sigue abierta = una denegación de gate que no produjo continuación.

## Cuándo SÍ leer algo de texto

- Solo el último `result_json.output_preview` o el último comentario de la issue
  bloqueada, para entender el porqué — nunca la conversación completa.
- `activity_log` filtrada por acción (`quality_gate.denied`, `quality_gate.waived`,
  `role.op_denied`) da la historia de decisiones sin coste de tokens.

## Herramienta

`scripts/audit_project_db.py` — solo lectura (abre la DB en modo `ro`), UTF-8 en
Windows, desglose de tokens por canal/agente. Extender AQUÍ cualquier invariante
nuevo, no en scripts ad-hoc.
