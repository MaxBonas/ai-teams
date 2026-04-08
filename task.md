# Estado actual y siguientes pasos

Fecha: `2026-04-03`
Máquina: `ORCH-01`
Suite validada: `858 passed`

Para el historial completo de lo cerrado, ver `docs/HISTORY.md`.

---

## Pendiente activo

### Deuda técnica menor

- [ ] Compatibilidad JSON residual en tests/constructores — ya no gobierna la lectura normal, pero sigue presente
- [ ] Vigilar unos días que el bootstrap (`prepare_dev_env.bat`) absorbe bien pulls y cambios entre máquinas

### Routing — extensión futura (no urgente)

- [ ] Extender la vista editable de routing para cubrir: reglas por tipo de tarea, límites de coste, capacidades mínimas, canales preferidos y simulación de resolución del router

### Documentación

- [ ] Limpieza y criba de documentación interna: hecha la primera pasada (2026-04-03). Vigilar que nuevas sesiones no acumulen docs de sesión fuera de `docs/archive/`.

---

## Siguiente bloque — por definir

Los bloques 0-6 están cerrados (ver `docs/HISTORY.md`). El sistema tiene:

- Lead adaptativo completo (RunHealthReport, PAUSE_FOR_USER, SKIP_PHASE, DEGRADE, briefing, memoria)
- Routing editable con overrides locales
- Proyectos externos con `.aiteam/` aislado
- Evidence gate robusto
- Bootstrap estable en dos máquinas

### Criterio de producto vigente

AITeams debe orientarse en el corto plazo a **agent workspace** y no a **IDE full generalista**.

Priorizar:

- chat + timeline + estado de runs
- routing/capabilities/status
- memoria, continuidad y reanudacion
- diffs, artefactos, aprobaciones y control del flujo
- UX del Team Lead / operator console

Posponer salvo necesidad clara:

- features de editor avanzadas tipo VSCode
- ecosistema de extensiones
- debugger completo
- complejidad de tabs/layout propia de IDE tradicional

Regla de priorizacion para futuras sesiones:

- si una mejora optimiza sobre todo la edicion manual de codigo, prioridad baja
- si una mejora optimiza dirigir, entender y controlar agentes, prioridad alta

El siguiente bloque de features está por decidir. Candidatos naturales:

- **Agentes conversacionales reales por proyecto** (`docs/CONVERSATIONAL_AGENTS_PLAN.md`)
- **UX compacto del chat** — estilo Claude App (`docs/CHAT_UX_COMPACT_FLOW_VISION.md`)
- **Routing avanzado** — simulación explicable, reglas por tipo de tarea (`docs/ROUTING_EDITOR_VISION.md`)
- **MCP/CLI/Skills roadmap** — integración de herramientas externas (`docs/MCP_CLI_SKILLS_ROADMAP.md`)

---

## Riesgos abiertos

- [ ] La sincronización entre `MAX-GAMINGPC` y `ORCH-01` puede reintroducir entornos rotos si Syncthing sincroniza `venv/`
- [ ] En ORCH-01 / sesiones Codex en Windows, usar siempre `.\scripts\pytest_local_stable.bat` (no `pytest_local.bat`) para evitar locks y timeouts
- [ ] Parte de la documentación histórica archivada puede confundir si se saca de `docs/archive/` sin contexto
