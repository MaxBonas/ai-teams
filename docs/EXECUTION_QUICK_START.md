# Quick Start — Estado Actual y Referencias Historicas

**Status**: documento historico actualizado para no inducir trabajo obsoleto
**Estado real verificado**: **282 tests passing** (`venv/Scripts/python.exe -m pytest tests/ -q --tb=short`)

---

## Que usar hoy

Si quieres trabajar en el proyecto hoy, usa esta secuencia:

1. `README.md`
2. `ROADMAP_FLUJOS_Y_AGENTES.md`
3. `TASKS.md`
4. `docs/CONVERSATIONAL_AGENTS_PLAN.md`
5. `docs/TEAM_FLOW_ANALYSIS.md`

---

## Estado del plan Q1

El plan de Sprints 1-3 ya no es el trabajo activo.

- `docs/SPRINT_ROADMAP_Q1_2026.md`: historico del hardening Q1 completado
- `docs/TEST_MATRIX_SPRINTS_1_2_3.md`: matriz historica de tests de ese ciclo
- este archivo: referencia rapida para no confundir el roadmap historico con el roadmap vivo

---

## Comandos vigentes

### Suite completa

```bash
venv/Scripts/python.exe -m pytest tests/ -q --tb=short
```

Resultado verificado: `282 passed`

### Suite focalizada reciente

```bash
venv/Scripts/python.exe -m pytest tests/test_dashboard.py tests/test_api_team_chat.py tests/test_memory_comms.py tests/test_orchestrator.py tests/test_parallel_taskboard.py tests/test_taskboard.py -q
```

Resultado verificado: `60 passed`

### Dashboard

```bash
python -m aiteam.cli dashboard --dashboard-output runtime/dashboard.html
```

### System check

```bash
python -m aiteam.cli system-check --environment stage --strict
```

---

## Implementado / Parcial / Planificado

### Implementado
- Continuidad por proyecto
- Threads persistentes por agente/proyecto
- Mailbox conversacional basico
- Observabilidad de flujo en dashboard/API/UI
- Paralelismo con barreras y claim guard

### Parcial
- Adapters conversacionales nativos con `messages[]`
- Mailbox TL <-> agentes mas profundo

### Planificado
- Cierre E2E del modelo conversacional multi-LLM
- Limpieza documental total de todos los documentos historicos restantes

---

## Nota importante

Las metricas antiguas de `91`, `108`, `122`, `142+` tests pertenecen al plan historico Q1. La baseline operativa real del proyecto hoy es `282 passed`.

---

## Siguiente paso recomendado

Para trabajo activo, deja este documento y sigue con `ROADMAP_FLUJOS_Y_AGENTES.md`.
