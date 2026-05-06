# Historial condensado

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
