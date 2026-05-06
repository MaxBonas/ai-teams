# Contributing

Guia corta para contribuir a AI Teams durante la reconstruccion v2.

## Direccion actual

AI Teams ya no mantiene compatibilidad con dogfooding, proyectos antiguos ni el flujo round-based. El objetivo vigente es un control plane Paperclip-like sobre SQLite para equipos de programacion: issues, runs, wakeups, ownership, interacciones y costes como entidades durables.

La documentacion viva esta en:

- `AGENTS.md`
- `README.md`
- `HANDOFF.md`
- `task.md`
- `docs/MIGRATION_PAPERCLIP.md`
- `docs/INDEX.md`
- `docs/HISTORY.md`

## Entorno local

```powershell
.\scripts\prepare_dev_env.bat
.\scripts\python_local.bat -m pytest -p no:cacheprovider tests -q --tb=short
```

`prepare_dev_env` rehidrata solo plantillas v2:

- `config/control_plane.example.json` -> `runtime/control_plane.json`
- `config/agents.example.json` -> `runtime/agents.json`

`runtime/`, `venv/` y `node_modules/` son locales por maquina y no son fuente de verdad compartida.

## Reglas

- Documentacion en espanol.
- No reintroducir `CLAUDE.md`, `GEMINI.md`, `walkthrough.md` ni docs legacy.
- No reintroducir writers JSONL como persistencia primaria.
- No recuperar `/api/aiteam/*`, `process_once()` ni `run_until_idle()`.
- No volver al router con scoring multifactor; usar `adapter_type` fijo y registry auditable.
- En proyectos externos, AI Teams debe escribir bajo `.aiteam/`, no en archivos raiz de proveedor.

## Verificacion

Para backend:

```powershell
.\scripts\python_local.bat -m pytest -p no:cacheprovider tests -q --tb=short
```

Para frontend:

```powershell
cd ide-frontend
node_modules\.bin\tsc.cmd -b
```
