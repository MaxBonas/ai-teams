# AI Teams

Control plane multi-agente Lead-first para equipos de programación. Mantiene en
SQLite issues, agentes del producto, runs, wakeups, interactions, costes y
evidencia; el Lead entiende la tarea, forma el equipo y mantiene el trabajo vivo
hasta cerrarlo o pedir un desbloqueo.

## Estado de instalación

| Entorno | Estado | Entrada |
|---|---|---|
| Windows nativo | Verificado | `scripts\prepare_dev_env.bat` |
| Linux | Objetivo, aún no verificado | Bootstrap POSIX pendiente |
| macOS | Objetivo, aún no verificado | Bootstrap POSIX pendiente |

“Objetivo” no significa soporte probado. La matriz y los criterios de promoción
viven en [task.md](task.md), bloque P0.I.

## Inicio rápido en Windows

Requisitos: Git, Python 3.10 o posterior, Node.js/npm y PowerShell. Los adapters
LLM se configuran después; sus CLIs y sesiones no se instalan automáticamente.

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

El bootstrap crea o actualiza dependencias locales del checkout y materializa
configuración runtime desde `config/*.example.json`. Es seguro repetirlo. No
copies desde otra máquina `venv/`, `runtime/`, `node_modules/`, bases activas,
sesiones CLI ni archivos con secretos.

## Configuración

- La carpeta de proyectos se elige en la UI o mediante
  `AITEAM_PROJECTS_ROOT`.
- Los perfiles de adapters, modelos y su health se administran en Config. El
  catálogo vivo es la fuente; este README no congela nombres de modelos.
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

El actual `aiteam system-check` comprueba adapters, pero todavía no sustituye al
`doctor --json` cross-platform planificado.

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
