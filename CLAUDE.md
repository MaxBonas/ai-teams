# AI Teams Orchestrator — instrucciones de proyecto

Orquestador multi-modelo de equipos de agentes. Un **Lead** descompone un goal en
issues, delega a roles (engineer, reviewer, test_runner, scouts) que corren cada
uno en su adapter (OpenAI API, Gemini, Anthropic SDK, o codex/claude CLI vía
suscripción), y un heartbeat secuencial procesa los runs de uno en uno.

## Comandos verificados

- **Tests (todos)**: `venv/Scripts/python.exe -m pytest tests/ -q --tb=short`
  (o `.\scripts\pytest_local.bat tests -q`). Estado 2026-07-15: **802 passing**.
- **Backend**: `venv/Scripts/python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8010`
  (arranca el heartbeat). Frontend: puerto 9490. Lanzador conjunto: `start_ide.bat`.
- **Auditar un proyecto capa-2**: `venv/Scripts/python.exe scripts/audit_project_db.py "<workspace>"`
  (ver skill `layer2-audit`).
- **Venv**: Python 3.12 en `venv/`. Rama activa: `master`. GitHub es la fuente de
  verdad — `git fetch` antes de asumir que local está al día (ORCH-01 también commitea).

## Arquitectura (dónde vive cada cosa)

- Heartbeat: `aiteam/heartbeat/loop.py` → `scheduler.py` → `executor.py` (el corazón,
  monolítico — trocear solo con razón funcional).
- **`aiteam/policies.py` es la ÚNICA fuente de reglas de rol/flujo** (tiers, RBAC
  denylist, máquina de estados de issue, breakers env-tunables). Cualquier regla
  nueva va aquí, NUNCA en un prompt.
- Adapters en `aiteam/adapters/`; selección de modelo por rol en
  `project_adapters.py` + `hiring_economics.py`. Config de usuario en
  `LOCALAPPDATA/AI Teams` (override con env `AITEAM_USER_CONFIG_DIR`).
- Verificación de cierre: `agent_reports` (con provenance) + gate
  `test_runner_exit_zero_required` + `_machine_close_verification`.

## Reglas del proyecto (aprendidas en producción)

1. **No refactorizar sin razón funcional.** Tests y comportamiento real mandan.
2. **Ningún gate falla en silencio.** Si un gate deniega una acción, debe producir
   una continuación (comentario correctivo + re-wake + escalación con cap), nunca
   solo un log. El deadlock de CLI Notas (2026-07-15) fue exactamente esto.
3. **Exige el recibo, no la narración.** Un cierre se computa de evidencia
   estructurada (exit codes, diffs, agent_reports valid+assignee), jamás del texto
   con el que un agente dice haber terminado.
4. **Trabajo determinista → builtin, no LLM.** Ejecutar tests es `subprocess`
   (`_execute_builtin_test_runner`), no una conversación: más barato y sin
   evidencia alucinable.
5. **Toda feature de adapter responde "¿qué deja en cost_events?"** El canal de
   suscripción es flat-rate pero consume tokens/TPM; medir siempre.
6. **Capa 2 nunca crea archivos de convención de proveedor** (AGENTS.md/CLAUDE.md/
   etc.) en workspaces externos; todo bajo `.aiteam/`. Enforcement en
   `_execute_file_ops`.
7. **Auditar proyectos capa-2 por SQL, no leyendo transcripciones** (skill
   `layer2-audit`).

## Skills de este repo

- `multi-model-orchestration`: patrones 2026 de routing/cascadas/verificación
  cruzada mapeados al código — consultar al tocar adapters, policies, governor,
  hiring_economics, quorum o gates.
- `layer2-audit`: auditoría barata en tokens de proyectos capa-2.

## Entorno Windows

- Paths con espacios (`Antigravity Projects`): comillas dobles siempre.
- `py -3.12` para Python; en el proyecto usar `venv/Scripts/python.exe`.
- Claude Code corre bash (Git Bash); también hay PowerShell disponible.
