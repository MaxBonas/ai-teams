# Prompt de arranque para Codex en ORCH-01

<!-- layer: system-development | audiencia: Codex en ORCH-01 | NO es artefacto de producto -->

Fecha: `2026-04-02`
Máquina origen: `MAX-GAMINGPC`
Máquina destino: `ORCH-01` (alias `DESKTOP-SR6CQA1`)

---

## Tu primera tarea: adaptar el entorno local de ORCH-01

El repo acaba de recibir un commit grande desde MAX-GAMINGPC. Tu trabajo antes de hacer cualquier otra cosa es dejar ORCH-01 en verde: entorno funcional, suite pasando, sin estado roto del pasado.

---

## Paso 1 — Pull y bootstrap

```powershell
cd "C:\Users\she__\Documents\Antigravity Projects\Ai_Teams"
git pull
.\scripts\prepare_dev_env.bat
```

`prepare_dev_env.bat` hace:
1. `ensure_local_venv.ps1` — valida o recrea `venv/` con Python 3.12 desde `pyproject.toml`
2. `ensure_local_runtime.ps1` — rehidrata `runtime/` local desde plantillas en `config/`

Si el script falla, diagnostica en este orden:
1. ¿Existe `venv/Scripts/python.exe`? Si no → `py -3.12 -m venv venv --clear`
2. ¿El `venv/` fue sincronizado por Syncthing desde MAX-GAMINGPC? Si sí → recrear: paths internos de `pyvenv.cfg` apuntan a la otra máquina y están rotos
3. ¿Hay espacios en el path? La ruta es `C:\Users\she__\Documents\Antigravity Projects\Ai_Teams` — siempre usar comillas en comandos

---

## Paso 2 — Verificar suite

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
```

**Resultado esperado: `823 passed, 0 failed`**

Si hay menos de 823 o hay fallos:
- Primero verificar que el `venv/` tiene todas las dependencias: `venv\Scripts\pip list | findstr pytest`
- Si faltan paquetes: `venv\Scripts\pip install -e ".[dev]"` o equivalente según `pyproject.toml`
- Si los fallos son en tests de integración live (adaptadores, MCPs): son esperables en ORCH-01 si la config local difiere. Documentar cuáles son y continuar.
- **No modificar tests para que pasen** — diagnosticar la causa real.

---

## Paso 3 — Verificar TypeScript

```powershell
cd ide-frontend
npm install
npm exec -- tsc -b
cd ..
```

Debe terminar sin errores de compilación. Si `node_modules/` fue sincronizado desde MAX-GAMINGPC, borrarlo y hacer `npm install` de nuevo (mismo problema que `venv/`).

---

## Qué cambió en este commit (resumen para orientarte)

El commit `1a2b5a0` incluye una tanda grande. Lo más importante para el entorno:

### Nuevos módulos Python (necesitan estar en venv)
- `aiteam/quorum.py` — Plan/Quorum MVP
- `aiteam/routing_overrides.py` — overrides locales de routing

### Nuevas dependencias de runtime
- El runtime de proyectos **externos** ahora usa `.aiteam/` en vez de `runtime/` (migración automática)
- El propio repo AI Teams sigue usando `runtime/` — no cambiar
- `runtime/routing_overrides.json` puede no existir en ORCH-01 — es local por máquina, el sistema lo crea vacío si no existe

### Cambios en la API que afectan a config local
- `TeamChatRequest` tiene nuevo campo `continuation_policy` (default `"auto"` — sin impacto en comportamiento existente)
- `TaskState` tiene nuevo valor `ARCHIVED` — compatible con SQLite existente

### Documentación nueva (solo leer, no tocar)
- `docs/IMPLEMENTATION_PLAYBOOK.md` — guía técnica completa del siguiente trabajo
- `docs/LEAD_ADAPTIVE_FLOW_VISION.md` — diseño de A1-A5 (Lead adaptativo)
- `docs/NAMING_COLLISION_INVESTIGATION.md` — taxonomía de capas del sistema
- `docs/COMMUNICATION_GUIDE_FOR_DEVS.md` — cómo hablar del sistema sin confundir capas
- `docs/CODEX_HANDOFF_2026_04_02.md` — estado completo del proyecto

---

## Paso 4 — Verificar config local de ORCH-01

Comprueba que existe `runtime/config.json` o equivalente con la configuración de adaptadores para esta máquina. Si no existe:

```powershell
.\scripts\ensure_local_runtime.ps1
```

Las API keys de ORCH-01 deben estar en las variables de entorno o en el archivo de config local. No viajan por Git. Si faltan:
- Los tests de integración live fallarán — es esperado
- El sistema en modo `AITEAM_SIM_MODE=1` funciona sin keys reales

---

## Paso 5 — Smoke test del sistema

Arrancar el IDE para confirmar que el stack completo levanta:

```powershell
.\start_ide.bat
```

Verificar en el navegador que:
- El backend responde en `http://localhost:8000/health` o equivalente
- El frontend carga en `http://localhost:9483`
- La pestaña `Routing` en StatusPanel muestra el catálogo de routing

Si el frontend no carga: `cd ide-frontend && npm run dev`

---

## Qué NO hacer en ORCH-01

- **No tocar `venv/`** — es local. Si está roto, recrear desde cero
- **No tocar `runtime/`** — es local. Si falta, `ensure_local_runtime.ps1`
- **No tocar `node_modules/`** — local. Si está roto, `npm install`
- **No commitear** archivos de config local, logs, ni estado de runtime
- **No modificar** `docs/CODEX_HANDOFF_2026_04_02.md` ni `docs/IMPLEMENTATION_PLAYBOOK.md` — son fuente de verdad desde MAX-GAMINGPC

---

## Una vez que el entorno está verde

Cuando `823 passed` y el IDE levanta, ORCH-01 está listo para continuar el desarrollo.

El siguiente bloque de trabajo es **A1 — RunHealthReport**. La especificación técnica completa está en:

```
docs/LEAD_ADAPTIVE_FLOW_VISION.md  →  sección "Fase A1"
docs/IMPLEMENTATION_PLAYBOOK.md    →  orden de ejecución al inicio del doc
```

Antes de empezar A1, leer:
1. `AGENTS.md` — contexto operativo y glosario de capas
2. `task.md` — estado actual del backlog
3. `docs/LEAD_ADAPTIVE_FLOW_VISION.md` — diseño completo A1-A5

---

## Diagnóstico rápido si algo falla

| Síntoma | Causa probable | Fix |
|---|---|---|
| `python: not found` | venv roto o sincronizado | `py -3.12 -m venv venv --clear` + `pip install -e .` |
| `ModuleNotFoundError: aiteam` | venv sin el paquete | `venv\Scripts\pip install -e .` |
| Tests fallan con `sqlite3` errors | `runtime/aiteam.db` corrupto o de otra máquina | borrar `runtime/aiteam.db`, se regenera |
| `npm: EPERM` o `node_modules` roto | sincronizado desde otra máquina | borrar `ide-frontend/node_modules/`, `npm install` |
| `823 passed` → menos tests | dependencia faltante o config distinta | `pip install -e ".[dev]"` |
| Puerto 8000 ocupado | proceso anterior vivo | `taskkill /f /im python.exe` o cambiar puerto en config |
