# DESIGN — Auto-extensión: skills y MCP servers gestionados por el propio sistema

**Fecha**: 2026-07-10 · **Estado**: diseño aprobable, sin implementar
**Objetivo**: que AI Teams pueda **detectar** que un proyecto necesita una capacidad
nueva (un MCP server, una skill), **proponerla**, **instalarla tras aprobación**,
**verificarla** y **gobernarla** — sin intervención manual más allá del gate de
aprobación del owner.

Caso motivador real: el equipo capa-2 no puede verificar Play Mode de Unity
(reviews estáticas eternas sobre YAML escrito a ciegas). Un MCP de Unity lo
resolvería; el propio equipo lo investigó por su cuenta y recomendó `pilot_later`
— la necesidad emergió del trabajo, el sistema no tenía forma de actuar sobre ella.

---

## 1 · Principios de diseño

1. **Instalar herramientas = ejecutar código de terceros.** La instalación de un
   MCP es SIEMPRE una decisión de producto (interacción `request_confirmation`
   con reason NO operacional) — la autonomía nunca la auto-acepta. Las skills
   (markdown puro, sin ejecución) pueden ser semi-autónomas.
2. **Por proyecto, no global.** Todo vive bajo `.aiteam/` del workspace: un
   proyecto no hereda las extensiones de otro, y borrar el proyecto borra sus
   extensiones. La config global del usuario (~/.codex/config.toml) NO se toca.
3. **Reutilizar la gobernanza existente.** RBAC por tiers, `capabilities_json`,
   `tool_access` (auditoría), interacciones producto-vs-operativo, y el patrón
   reconciler. No inventar un segundo sistema de permisos.
4. **Ground truth verificable.** Ninguna extensión se considera activa sin un
   health check que haya pasado; el estado vive en DB, no en la memoria del lead.
5. **Presupuesto de contexto.** Skills y tools MCP consumen prompt; cada
   extensión declara su coste aproximado y el executor la inyecta solo a los
   roles que la tienen concedida.

---

## 2 · Estado actual (qué hay, qué falta)

| Pieza | Hoy | Falta |
|---|---|---|
| Skills | `skills/<rol>.md` estático en el repo; `load_skill(role, skills_dir=None)` ya acepta dir custom | Skills por proyecto, aprendidas, versionadas, con gate |
| MCP | Nada. Codex CLI *soporta* MCP vía config; AI Teams no lo gestiona | Plumbing `-c mcp_servers...` por run, registry, health check |
| Permisos | RBAC tiers + `capabilities_json` + catálogo de tools | Mapear extensiones → capabilities; grant por agente |
| Aprobación | Interacciones producto (popup Pendientes) + directivas vinculantes | Reasons nuevos + UI de diff "qué se va a instalar" |
| Auditoría | `tool_access`, `activity_log` | Eventos `extension.*` |
| Detección de necesidad | El lead ya investiga (caso Unity MCP) pero sin vocabulario para actuar | Op `propose_extension` + señales del sistema (blockers repetidos) |

---

## 3 · Arquitectura: Extension Registry por proyecto

### 3.1 Datos

`.aiteam/extensions.json` (fuente de verdad declarativa, junto a project_config.json):

```json
{
  "version": 1,
  "mcp_servers": {
    "unity": {
      "source": "npx -y unity-mcp@1.2.0",
      "args": [], "env_required": ["UNITY_PROJECT_PATH"],
      "status": "active | proposed | approved | failed | retired",
      "approved_by": "user", "approved_at": "...",
      "health": {"status": "ok", "checked_at": "...", "tools_count": 12},
      "granted_roles": ["engineer", "test_runner"],
      "pin": {"kind": "npm", "version": "1.2.0", "integrity": "sha512-..."}
    }
  },
  "skills": {
    "unity-scene-regeneration": {
      "path": "skills/unity-scene-regeneration.md",
      "applies_to_roles": ["engineer", "reviewer"],
      "origin": "learned | owner | catalog",
      "status": "active", "approved_by": "autonomy|user"
    }
  }
}
```

`.aiteam/skills/*.md` — las skills del proyecto. En DB, tabla `extension_events`
(o reutilizar `activity_log` con `action='extension.*'`) para el ciclo de vida.

### 3.2 Módulos nuevos

- `aiteam/extensions.py` — leaf CRUD del registry + validación (espejo de
  `project_adapters.py`): `list_extensions`, `propose_extension`,
  `approve_extension`, `record_health`, `granted_mcp_for_agent(role)`.
- `aiteam/mcp_launcher.py` — traduce entradas activas del registry a overrides
  de codex por run: `-c mcp_servers.unity.command=...` (codex ya acepta `-c`
  como override efímero — mismo mecanismo que usamos para `model`). Para
  adapters API (openai/anthropic/gemini in-process), fase posterior con
  cliente MCP propio; el primer target es subscription_cli, que es donde
  corren los roles que ejecutan.
- `aiteam/extension_health.py` — health check: lanza el server MCP en frío,
  hace `initialize` + `tools/list` por stdio con timeout, registra
  `tools_count` y version. Sin health ok → nunca se inyecta.

### 3.3 Inyección en runs

En `executor.execute()` (donde ya se inyectan skill + workspace_files):
1. `load_skill(role)` pasa a componer: skill base del repo + skills del
   proyecto activas cuyo `applies_to_roles` incluya el rol (orden: base
   primero, proyecto después — el proyecto refina, no reemplaza).
2. Si el agente tiene MCPs concedidos → `_command_context` añade los `-c
   mcp_servers.*` al argv de codex, y el prompt lista las tools disponibles
   ("tienes el MCP unity con tools X, Y — úsalo para verificar Play Mode en
   vez de inferir del YAML").
3. `tool_access` registra grant por run (`tool_name="mcp:unity"`), igual que
   hoy con `adapter:*`.

---

## 4 · Ciclo de vida de una extensión

```
necesidad → propuesta → gate → instalación/pin → health check → grant → uso → auditoría → retiro
```

1. **Necesidad** (dos orígenes):
   - *Agente*: el lead (o un engineer vía report) emite op
     `propose_extension` con justificación y evidencia del blocker.
   - *Sistema*: un reconciler detecta patrones — N blockers con la misma causa
     (`blocker:` del AGENT-REPORT repetido), o "untestable items" recurrentes
     en veredictos de reviewer — y postea una sugerencia al lead (comentario
     de sistema), que decide si formaliza la propuesta. El sistema sugiere,
     el lead propone, el owner aprueba: tres niveles, tres responsabilidades.
2. **Propuesta** → interacción `request_confirmation` con
   `reason="extension_install_requested"` (PRODUCTO — excluida del mapa
   `OPERATIONAL_INTERACTION_DEFAULTS`; la pausa de subtree existente aplica:
   nada se instala mientras esperas). La tarjeta muestra: qué, de dónde
   (comando/paquete + versión pineada), por qué (evidencia), qué roles lo
   usarían, y riesgos.
3. **Gate**: solo el owner acepta. `user_note` puede acotar ("solo para
   engineer, no reviewer"). La resolución queda como directiva vinculante
   (mecanismo ya desplegado).
4. **Instalación**: ejecutor de instalación fuera del ciclo de runs (paso del
   heartbeat, con timeout): resuelve y **pinea** versión + integrity. Nada de
   `latest` flotante.
5. **Health check** obligatorio (3.2). Falla → `status=failed` + comentario
   de sistema; nunca reintenta en bucle (idempotencia por versión — patrón
   del rereview gate).
6. **Grant**: `granted_roles` → capabilities de los agentes; RBAC existente
   decide quién lo ve. Tier 3 read-only nunca recibe MCPs con tools de
   escritura (validación contra `NON_EDITING_ROLES`).
7. **Uso + auditoría**: cada run con MCP inyectado queda en `tool_access`;
   el cost tracking existente absorbe el overhead de contexto.
8. **Retiro**: op del lead o decisión del owner; también automático si el
   health check falla M veces seguidas (reconciler) → `retired` + escalación
   informativa.

### Skills: variante ligera del mismo ciclo

- **Aprendidas**: al cerrar un ciclo, el context_curator ya comprime contexto;
  se le añade el encargo de destilar "lecciones operativas del workspace" a
  una skill propuesta (`.aiteam/skills/` + entrada `proposed`).
- **Gate suave**: como una skill es texto sin ejecución, en modo `autonomous`
  se auto-aprueba con límites (máx K skills activas, tamaño máximo por skill,
  presupuesto total de prompt); en `supervised` pasa por el popup. El owner
  puede editar el markdown directamente (pestaña Config → Extensiones).
- **Antiveneno**: una skill aprendida nunca puede contradecir una directiva
  del usuario (el contrato ya establece la precedencia: directivas > skills).

---

## 5 · Seguridad y gobernanza

- **Ejecución**: los MCP servers corren como subprocess del run de codex, con
  el working dir del workspace — mismo blast radius que ya tiene el CLI. No
  se instalan servers que requieran privilegios del sistema.
- **Secretos**: `env_required` se satisface desde el store de secretos
  existente (user_config), nunca se escribe en extensions.json ni en prompts.
- **Cadena de suministro**: pin de versión + integrity hash en la propuesta;
  la tarjeta del gate muestra exactamente el comando. Catálogo curado
  (fase 4) con entradas pre-verificadas (unity, playwright, sqlite, fs-extra)
  para que la propuesta típica sea "del catálogo" y no un npx arbitrario.
- **Roles**: `propose_extension` es op de Tier 1 (denylist para Tier 2/3 en
  `OPS_FORBIDDEN_*`). Tier 2 puede *sugerir* en su report (`needs_capability:`)
  — señal para el reconciler de necesidad.
- **Contexto**: límite de MCPs activos por rol (2-3) y de skills inyectadas;
  el executor loguea el coste en tokens del bloque de extensiones.

---

## 6 · Plan de implementación (PRs incrementales, cada uno útil por sí solo)

| PR | Contenido | Valor inmediato |
|---|---|---|
| **1. Skills por proyecto** | `.aiteam/skills/` + composición en `load_skill` + pestaña Config→Extensiones (listar/editar) | El owner puede darle conocimiento local al equipo hoy |
| **2. Registry + ops** | `extensions.py`, `extensions.json`, op `propose_extension` (Tier 1), reason producto + tarjeta en popup | El lead puede pedir capacidades formalmente |
| **3. MCP plumbing codex** | `mcp_launcher.py` + inyección `-c mcp_servers.*` + health check + grants→capabilities + auditoría | Un MCP aprobado funciona de verdad en runs |
| **4. Detección de necesidad** | Reconciler de blockers repetidos / untestable-items → sugerencia al lead; curator destila skills aprendidas (gate suave por autonomía) | El sistema se auto-extiende sin que nadie lo pida |
| **5. Catálogo curado + retiro** | Catálogo pre-verificado (empezando por Unity MCP), retiro automático por health, límites de presupuesto | Endurecimiento para uso continuo |

Estimación: PRs 1-2 pequeños (1 sesión); PR 3 es el grueso (subprocess,
health, Windows quirks); PR 4-5 medianos. Tests por PR siguiendo el patrón
actual (fixtures SQLite + runtimes falsos; para el health check, un MCP stub
en Python que hable stdio).

**Primer hito de valor completo (PRs 1-3)**: el lead del capa-2 propone el
MCP de Unity con la evidencia que ya tiene, tú lo apruebas desde el popup, y
el reviewer deja de adivinar Play Mode desde YAML — el bucle de re-revisión
que hemos estado frenando toda la semana desaparece por su causa raíz.

---

## 7 · Riesgos principales

| Riesgo | Mitigación |
|---|---|
| MCP malicioso/roto rompe runs | Catálogo curado + pin + health gate + retiro automático |
| Skills aprendidas acumulan ruido | Límite K + presupuesto de prompt + revisión del curator + owner edita/borra |
| Overhead de contexto degrada calidad | Coste declarado por extensión, inyección solo a roles con grant |
| Windows (paths, spawn, encoding) | Mismo tratamiento ya batallado en subscription_cli (stdin, UTF-8, timeouts) |
| El lead propone extensiones en bucle | Idempotencia por (extensión, versión) + churn breaker existente aplica a ops |
