# Paperclip como guia operativa

AI Teams usa Paperclip como referencia fuerte de control plane, no como producto a copiar. La regla practica para nuevas decisiones es:

1. Consultar primero el codigo local de Paperclip cuando exista una duda de liveness, issues, wakeups, interactions, adapters o recovery.
2. Extraer el patron operativo minimo.
3. Adaptarlo a la identidad de AI Teams: equipos de programacion, Lead-first, hiring dinamico, perfiles `solo_lead`/`lead_quorum`/`full_team` y delegacion economica.
4. Escribir el equivalente en docs/tests antes de generalizarlo.

Repositorio de referencia local (cada desarrollador clona paperclip donde prefiera):

```
git clone https://github.com/paperclip-ai/paperclip <ruta-local>
```

## Patrones ya confirmados

### Issue delegado siempre tiene owner y wakeup

Paperclip no deja trabajo leaf en silencio. En el smoke plugin:

- `packages/plugins/examples/plugin-orchestration-smoke-example/src/worker.ts:102` crea child issue con `assigneeAgentId`.
- `packages/plugins/examples/plugin-orchestration-smoke-example/src/worker.ts:127` llama `ctx.issues.requestWakeup(child.id, ...)`.

AI Teams debe conservar esta invariante:

- Si el Lead crea subissues por hiring aceptado, se crean con `assignee_agent_id` y wakeup de assignment.
- Si un LLM crea subissues sin pasar por hiring, `reconcile_unassigned_role_issues()` materializa `role:<rol>`, asigna y encola wakeup.
- Si ya hay assignee pero no live path, `reconcile_unqueued_assigned_issues()` encola wakeup idempotente.

### Interactions reanudan al assignee

Paperclip usa continuation policy `wake_assignee` por defecto para interacciones que deben reanudar trabajo:

- `packages/mcp-server/src/tools.ts:121`
- `packages/mcp-server/src/tools.ts:132`
- `packages/shared/src/validators/issue.ts:439`
- `packages/shared/src/validators/issue.ts:449`

AI Teams debe usar `issue_thread_interactions` como pausa durable, no como final. Resolver una interaction debe despertar al owner correspondiente cuando la policy lo pida.

### Adapters locales aceptan auth nativa o API key

Paperclip trata los CLIs locales como canales con dos modos de auth:

- Codex: `OPENAI_API_KEY` o auth nativa en `~/.codex/auth.json`.
- Gemini: `GEMINI_API_KEY`/`GOOGLE_API_KEY` o `gemini auth login`.

AI Teams sigue esa separacion:

- Suscripcion/CLI y API son perfiles distintos.
- Las API keys se guardan en vault local de usuario, no en SQLite de proyecto.
- El cockpit muestra health: `funcional y testeado`, `CLI instalado; auth no verificada`, o fallo con razon.
- En Windows, los logins de suscripcion se lanzan mediante `.cmd` generado por AI Teams para evitar errores de quoting con ejecutables de `WindowsApps`.

## Politica de adapters por proyecto

Al crear un proyecto nuevo, AI Teams debe exigir al menos un adapter disponible. Esa seleccion queda en:

`.aiteam/project_config.json`

El Lead y los hirings no escogen de todo el catalogo global: escogen de los adapters habilitados para ese proyecto.

Regla de asignacion inicial:

- Lead, reviewers y quorum/seniors: preferir modelos avanzados o canales fuertes.
- Engineers/QA/workers: preferir modelos baratos, `mini`, `flash/lite` o locales cuando esten disponibles.
- La UI de hiring permite corregir perfil/modelo antes de aceptar.

Esto conserva la diferencia de AI Teams: Paperclip prioriza continuidad operativa general; AI Teams añade composicion de equipo, seniority y ahorro de coste como parte del producto.

## Borrado de proyectos

El borrado de proyecto es una operacion destructiva local:

- Solo se permite dentro de la raiz de proyectos.
- Nunca puede borrar el repo fuente de AI Teams ni la raiz contenedora.
- Rechaza symlinks.
- Requiere escribir `DELETE` exacto en mayusculas.
- Tras borrar, el workspace actual vuelve a estado no configurado y el cockpit muestra el flujo de crear proyecto.

## Como consultar Paperclip durante desarrollo

Busquedas utiles:

```powershell
# Ajusta <ruta-paperclip> a donde lo hayas clonado localmente
rg -n "requestWakeup|wake_assignee|assigneeAgentId|continuationPolicy" "<ruta-paperclip>"
rg -n "auth.json|GEMINI_API_KEY|GOOGLE_API_KEY|OPENAI_API_KEY|auth login" "<ruta-paperclip>\packages\adapters"
rg -n "checkout|activeRun|heartbeat|wakeup" "<ruta-paperclip>\server\src"
```

La referencia decide patrones de robustez, no nombres ni UX. Si una solucion Paperclip contradice Lead-first, hiring dinamico o bajo ruido operativo, se documenta la divergencia y se implementa la variante AI Teams.
