# Plan de Mejoras — Segunda Auditoria `aiteam-hybrid`

> Fecha: 2026-03-03
> Estado: En implementacion

Segunda pasada de auditoria del proyecto. La primera ronda (sesion anterior) corrigio 12 bugs (C1-C6, A1-A6, M1/M5/M6). Esta ronda cubre problemas nuevos en backend, API, frontend, tests y configuracion.

---

## Hallazgos por prioridad

### CRITICOS / ALTOS

| # | Archivo | Problema | Fix |
|---|---------|----------|-----|
| N1 | `aiteam/mailbox.py:46` | Falta `f.flush()` despues de append JSONL | Agregar `f.flush()` |
| N2 | `aiteam/persistence.py:157` | `record.pop("_checksum")` muta el dict de entrada | Usar `record.get()` y filtrar al copiar |
| N3 | `aiteam/observability.py:40` | `datetime.fromisoformat()` puede lanzar `ValueError` con timestamps corruptos | Wrap en try/except |
| N4 | `aiteam/runtime.py:46` | `_save()` usa `write_text()` sin atomicidad — puede corromper locks | Patron atomic temp+rename |
| N5 | `api/main.py:2609-2620` | Endpoints `/api/fs/file` retornan HTTP 200 con `{"error":"..."}` | `raise HTTPException(status_code=404/400)` |
| N6 | `api/main.py:2671,2689` | `print()` en produccion — errores de PTY no se capturan | `logger.exception()` / `logger.info()` |
| N7 | `ide-frontend/src/components/TerminalPanel.tsx` | WebSocket sin `onerror`/`onclose` — falla silenciosamente | Handlers con mensaje en terminal |
| N8 | `ide-frontend/src/App.tsx:96-126` | Promise de bootstrap sin cleanup | Flag `cancelled` en useEffect |
| N9 | `aiteam/learning_registry.py:349` | `_rewrite_ledger()` no es atomico | Escribir a temp file y renombrar |
| N10 | `aiteam/snapshots.py:101-107` | ZIP restore no verifica symlinks | Check `target.is_symlink()` |

### MEDIOS

| # | Archivo | Problema | Fix |
|---|---------|----------|-----|
| M1 | `ide-frontend/src/components/FileExplorer.tsx:85` | `TreeNode` usa `any` type | Interface `TreeNodeProps` |
| M2 | `ide-frontend/src/components/TeamChat.tsx:485` | No verifica `response.ok` antes de parsear JSON | Check `if (!response.ok)` |
| M3 | `api/main.py:2659` | PowerShell path hardcodeado | Deteccion con `sys.platform` |
| M4 | `aiteam/finops.py:181` | `input_tokens`/`output_tokens` podrian ser None | Agregar `or 0` defensivo |

### BAJOS

| # | Archivo | Problema | Fix |
|---|---------|----------|-----|
| L1 | `tests/test_finops_anomaly.py:17` | Usa `/tmp/` hardcodeado — falla en Windows | `tempfile.TemporaryDirectory()` |
| L2 | `pyproject.toml` | No lista dependencias (fastapi, uvicorn, playwright, etc.) | Seccion `[project.dependencies]` |

---

## Archivos modificados

```
EDITADO: aiteam/mailbox.py            (N1)
EDITADO: aiteam/persistence.py        (N2)
EDITADO: aiteam/observability.py      (N3)
EDITADO: aiteam/runtime.py            (N4)
EDITADO: aiteam/learning_registry.py  (N9)
EDITADO: aiteam/snapshots.py          (N10)
EDITADO: aiteam/finops.py             (M4)
EDITADO: api/main.py                  (N5, N6, M3)
EDITADO: ide-frontend/src/components/TerminalPanel.tsx  (N7)
EDITADO: ide-frontend/src/App.tsx                       (N8)
EDITADO: ide-frontend/src/components/TeamChat.tsx       (M2)
EDITADO: ide-frontend/src/components/FileExplorer.tsx   (M1)
EDITADO: tests/test_finops_anomaly.py                   (L1)
```

## Historial de auditorias

### Ronda 1 (sesion anterior)
- C1: `taskboard.py` — atomic writes
- C2: `execution.py` — browser finally block
- C3: `finops.py` — null check decision.response
- C4: `api/routers/aiteam.py` — traceback exposure
- C5: `api/main.py` — auth en notebooklm
- C6: `api/routers/workspace.py` — path traversal
- A1: `api/main.py` — CORS restriction
- A2: `api/routers/aiteam.py` — HTTPException 404
- A3: `observability.py` — bool coercion
- A4: `orchestrator.py` — dynamic meeting participants
- A5: `api.ts` — configurable API_BASE
- A6: `TerminalPanel.tsx` — dynamic WS URL
- M1: `memory.py` — flush after append
- M5: `CodeEditor.tsx` — response.ok check
- M6: `routing_policy.example.json` — budget alignment

### Ronda 2 (esta sesion)
Ver tabla de hallazgos arriba.
