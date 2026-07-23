# AI Teams

Control plane multi-agente Lead-first para equipos de programación. Mantiene en
SQLite issues, agentes del producto, runs, wakeups, interactions, costes y
evidencia; el Lead entiende la tarea, forma el equipo y mantiene el trabajo vivo
hasta cerrarlo o pedir un desbloqueo.

## Estado de instalación

| Entorno | Estado | Entrada |
|---|---|---|
| Windows nativo | Bootstrap operativo; aceptación completa pendiente | `scripts\prepare_dev_env.bat` |
| Linux | Objetivo, aún no verificado | Bootstrap POSIX pendiente |
| macOS | Objetivo, aún no verificado | Bootstrap POSIX pendiente |

“Objetivo” no significa soporte probado. La matriz y los criterios de promoción
viven en [task.md](task.md), bloque P0.I.

## Inicio rápido en Windows

Requisitos: Git, Python 3.10 o posterior, Node.js 22 o posterior con npm y
PowerShell. La fuente comprobable es
[`config/installation_support.v1.json`](config/installation_support.v1.json).
El bootstrap no instala programas globales ni autentica cuentas.

```powershell
git clone https://github.com/MaxBonas/ai-teams.git
Set-Location ai-teams
.\scripts\prepare_dev_env.bat
.\start_ide.bat
```

Abre `http://localhost:9490`. Para detener los procesos:

```powershell
.\stop_ide.bat
```

La aceptación reproducible de Windows usa
[windows-clean-room.yml](.github/workflows/windows-clean-room.yml): repite el
bootstrap, prueba start/stop y crea un proyecto fixture en un runner efímero.
Hasta conservar un recibo verde de ese workflow, Windows continúa en `preview`.

El bootstrap crea o actualiza dependencias locales del checkout y materializa
configuración runtime desde `config/*.example.json`. Es seguro repetirlo. No
copies desde otra máquina `venv/`, `runtime/`, `node_modules/`, bases activas,
sesiones CLI ni archivos con secretos.

Al terminar imprime un diagnóstico de instalación:

- para runs vivos hace falta **un** adapter Lead-capable instalado,
  autenticado y verde; no hacen falta todos los proveedores;
- Codex o Antigravity son opciones primarias guiadas;
- OpenCode Zen es un carril económico opcional y actualmente requiere una API
  key personal incluso para modelos de precio temporalmente cero;
- Ollama y LM Studio son opcionales y nunca deben instalarse como prerrequisito.

El mismo diagnóstico puede repetirse sin mutar la máquina:

```powershell
.\scripts\python_local.bat scripts\audit_installation_support.py --json
```

## Configuración

- La carpeta de proyectos se elige en la UI o mediante
  `AITEAM_PROJECTS_ROOT`.
- Los perfiles de adapters, modelos y su health se administran en Config. El
  catálogo vivo es la fuente; este README no congela nombres de modelos.
- `AITEAM_MODEL_DEFAULT_ROLLOUT` gobierna defaults nuevos: `shadow` es el
  fallback seguro y el rollback inmediato; la plantilla usa `recommend`, que
  registra sin asignar. `auto` sigue denegado hasta que cada rol tenga ganador
  vivo elegible y snapshot sellado. Nunca migra agentes existentes.
- API y suscripción son canales independientes. Una key API no autentica un CLI
  y una sesión CLI no habilita una API.
- `.env.example` documenta variables opcionales. Si se crea `.env`, es local y
  nunca se commitea.
- Los artefactos que AI Teams crea en proyectos externos viven bajo `.aiteam/`.
  El producto nunca crea `AGENTS.md`, `CLAUDE.md` o `GEMINI.md` allí.

## Verificación de desarrollo

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\migrate_to_v2.py --json
Set-Location ide-frontend
node_modules\.bin\tsc.cmd -b
```

El actual `aiteam system-check` comprueba que el registro de adapters puede
cargarse y enumera sus tipos; no prueba conectividad, autenticación ni health y
no sustituye al `doctor --json` cross-platform planificado.

## Instalar o trasladar a otra máquina

La guía canónica es
[Instalación e integración](docs/INSTALLATION_AND_INTEGRATION.md). Incluye el
procedimiento para personas y un protocolo determinista para agentes de IA, los
límites actuales y la estrategia de soporte poliglota.

Documentación activa adicional:

- [Plan y tareas](task.md)
- [Migración Paperclip-like](docs/MIGRATION_PAPERCLIP.md)
- [Orquestación](docs/ORCHESTRATION.md)
- [Índice documental](docs/INDEX.md)
- [Handoff vigente](HANDOFF.md)
- [Instrucciones para agentes de desarrollo](AGENTS.md)
