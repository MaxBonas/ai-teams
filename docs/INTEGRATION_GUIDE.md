# Guia de integracion con tus programas agenticos

Este documento define como conectar tus sistemas existentes al orquestador hibrido.

## 1) Contrato de adapter

Todo runtime externo debe exponerse como `ModelAdapter`:

- `available() -> bool`: salud/capacidad actual.
- `invoke(prompt: str) -> AdapterResponse`: ejecucion de una unidad de trabajo.
- Metadatos: `provider`, `model`, `channel`, `capabilities`, `cost_tier`.

### Opcion rapida: adapter de programa externo

Puedes envolver un programa propio con `ExternalProgramAdapter`.

```python
from aiteam.adapters import ExternalProgramAdapter
from aiteam.types import ChannelType

adapter = ExternalProgramAdapter(
    name="mi_runtime",
    provider="custom",
    model="agentic-v1",
    command=["my-agent-cli", "run", "--prompt", "{prompt}"],
    capabilities={"coding", "analysis"},
    channel=ChannelType.SUBSCRIPTION,
    routing_priority=200,  # secundario por defecto
)
```

Tambien puedes cargar adapters desde archivo:

- `runtime/adapters.json` (se crea template con `aiteam init`)
- revisar con `python -m aiteam.cli adapters`
- descubrir candidatos con `python -m aiteam.cli inventory-tools --inventory-output runtime/tool_inventory.json`

### Prioridad recomendada para herramientas secundarias

Cuando conectes herramientas auxiliares (ej. automatizaciones de WhatsApp, Play Store, Android audit),
marcalas como secundarias para que no desplacen a los adapters principales:

```json
{
  "type": "external_program",
  "name": "mi_herramienta_aux",
  "priority": "secondary",
  "enabled": true
}
```

Campos soportados:

- `priority`: `primary` o `secondary`.
- `routing_priority`: entero opcional (menor = mas prioridad). Si se define, sobrescribe `priority`.
- `enabled`: permite registrar herramientas sin activarlas aun (`false` para deshabilitar).
- `requires_approval`: obliga aprobacion humana por tarea antes de poder enrutar ese adapter.

Ejemplo de herramienta multimodal de video (Remotion):

```json
{
  "type": "external_program",
  "name": "video_editor_remotion",
  "priority": "secondary",
  "enabled": false,
  "capabilities": ["multimodal", "video_generation", "rendering"]
}
```

### Aprobacion de operaciones sensibles

Si una tarea incluye comandos sensibles en `execution_plan` o necesita adapters con
`requires_approval=true`, debes marcar la tarea con aprobacion explicita:

```json
{
  "metadata": {
    "approved_sensitive_ops": true,
    "approved_by": ["lead-1", "security-1"],
    "approved_adapters": ["playstore_publisher"]
  }
}
```

Sin esa aprobacion, la tarea se bloquea por compliance y queda en `failed` con trazabilidad.
En `prod`, se requiere doble aprobacion (`approved_by` con al menos 2 IDs).

## 2) Estrategia de adopcion progresiva

1. Integrar primero el programa mas estable como adapter de suscripcion.
2. Integrar despues un adapter API del mismo proveedor para fallback.
3. Repetir por proveedor (OpenAI, Anthropic, Google).
4. Activar enrutado cruzado por rol.
5. Activar `contract-first` para generar pipelines con gates de calidad.

## 3) Mapeo sugerido de roles

- Team Lead: OpenAI o Claude Pro (razonamiento y planeacion).
- Researcher: Gemini Pro o Claude Pro (analisis de contexto).
- Engineer: OpenAI/Claude Pro segun stack.
- Reviewer/QA: proveedor alternativo para reducir sesgo del implementador.

## 4) Señales de fallback a API

- `timeout` repetido en canal de suscripcion.
- `quota_exceeded` o indisponibilidad temporal.
- tarea `high` en complejidad/criticidad.
- necesidad de feature avanzada (tooling/caching/structured output estricto).

Fallback recomendado por defecto:

- `gpt-4.1-mini` para razonamiento/coding costo-eficiente.
- `gpt-4o-mini` para tareas multimodales (vision/inspeccion).
- `llama-3.3-70b-versatile` via Groq para razonamiento/coding de baja latencia.

## 5) Control de colisiones y calidad

- `owned_files` por tarea activa locks en `runtime/file_locks.json`.
- Tareas de Engineer abren gates automaticas de `review` y `qa`.
- La tarea principal queda bloqueada hasta que ambas gates completen.

## 6) Memoria y reuniones

- Cada agente persiste su memoria en `runtime/memory/<agent>.jsonl`.
- El orquestador inyecta memoria relevante + mensajes recientes al prompt.
- Se corre una reunion de sincronizacion por ronda (`Sync meeting`) para alinear estado.

## 7) Ejecucion de planes (entorno/sistema/browser)

Puedes definir `execution_plan` en `task.metadata`:

```json
{
  "execution_plan": [
    {"type": "cmd", "command": "python --version"},
    {"type": "powershell", "command": "Write-Output 'ok'"},
    {"type": "browser_fetch", "url": "https://example.com"}
  ]
}
```

Tambien puedes indicar `workdir` por paso para consultar herramientas fuera del repo,
siempre dentro de roots permitidos (`Ai_Teams` + `Antigravity Projects` por defecto):

```json
{
  "execution_plan": [
    {
      "type": "cmd",
      "command": "dir",
      "workdir": "C:\\Users\\Max\\Antigravity Projects"
    }
  ]
}
```

Tipos soportados:

- `cmd`
- `powershell`
- `browser_fetch`
- `browser_open`
- `browser_script` (modo Playwright opcional)

Para habilitar Playwright en local:

```bash
python -m pip install playwright
python -m playwright install chromium
```

Acciones avanzadas para `browser_script` (Playwright):

- `goto`, `click`, `type`, `press`, `hover`
- `wait_for_selector`, `wait_for_url`, `wait_for_timeout`
- `select_option`, `evaluate`, `extract_text`, `assert_text`
- `set_viewport`, `screenshot` (evidencia)

Ejemplo multi-step con evidencia:

```json
{
  "execution_plan": [
    {
      "type": "browser_script",
      "url": "https://example.com",
      "actions": [
        {"type": "set_viewport", "width": 1366, "height": 768},
        {"type": "wait_for_selector", "selector": "body"},
        {"type": "extract_text", "selector": "h1", "label": "title"},
        {"type": "screenshot", "path": "runtime/evidence/example-home.png", "full_page": true}
      ]
    }
  ]
}
```

## 8) Recomendacion inicial de operacion

- Ejecutar 2 semanas en modo observacion.
- Medir: `% Pro`, `fallback_rate`, `coste por tarea`, `pass rate`.
- Ajustar politicas de `max_subscription_attempts` y orden de proveedores.

## 9) Auto-integracion de CLI/MCP/Skills

El orquestador puede integrar herramientas automaticamente por tarea usando `tool_requirements`:

```json
{
  "metadata": {
    "required_capabilities": ["security_scan"],
    "tool_requirements": [
      {
        "name": "semgrep_mcp",
        "category": "mcp",
        "source_type": "npm",
        "source": "@modelcontextprotocol/server-semgrep",
        "enabled": false,
        "required": false
      },
      {
        "name": "remotion_skill",
        "category": "skill",
        "source_type": "builtin"
      }
    ]
  }
}
```

Comandos clave:

- `python -m aiteam.cli tool-catalog`
- `python -m aiteam.cli tool-sync --tool-request-file runtime/tool_requests.json`
- `python -m aiteam.cli tool-sync --runtime-dir runtime_stage --tool-request-file config/tool_requests.pro.json`
- `python -m aiteam.cli mcp-doctor --runtime-dir runtime_stage --doctor-timeout 20`
- `python -m aiteam.cli skills-coverage --runtime-dir runtime_stage`

Notas:

- En `prod`, la integracion de herramientas con internet debe ir con aprobacion sensible.
- Si una tarea queda sin adapter elegible, el sistema puede intentar `auto_discover_tools` por capacidad.
- Para auto-adquisicion explicita, usa `"acquire": true` en cada requirement (`pip|npm|git`).
- Si la adquisicion opcional falla, la herramienta se registra pero queda `enabled=false` para evitar fallos operativos.

## 10) Biblioteca de Skills y consciencia operacional

- Biblioteca base: `config/skills.library.json`.
- Fuentes remotas versionadas (por lote): `config/skills.sources.json`.
- Sincronizacion al filesystem de skills: `python -m aiteam.cli skills-sync`.
- Descarga por lotes desde repos permitidos (OpenAI/Anthropic): `python -m aiteam.cli skills-pull --skills-batch gpt-system,claude-core --skills-max-items 6`.
- Export multi-runtime (`.cloud`, `.agents`, `.claude`): `python -m aiteam.cli skills-export --skills-targets cloud,agents,claude`.
- Diagnostico de cobertura/instalacion: `python -m aiteam.cli skills-doctor`.
- Inspeccion de biblioteca: `python -m aiteam.cli skills-library --show-content`.

El orquestador inyecta automaticamente en el prompt:

- skills aplicables al rol + capacidades,
- MCPs recomendados por skill,
- MCPs activos ya registrados en runtime.

Esto obliga a que el equipo de agentes opere con contexto procedural reutilizable,
en lugar de improvisar reglas en cada tarea.

## 11) Pro accounts, APIs y continuidad operativa

Modelos Pro por defecto en el router:

- OpenAI: `gpt-5.3-codex`
- Google: `gemini-3.1-pro`
- Anthropic: `claude-code`

Checks y toggles:

- `python -m aiteam.cli provider-status --runtime-dir runtime_stage --environment stage`
- `python -m aiteam.cli provider-connect --runtime-dir runtime_stage`
- `python -m aiteam.cli provider-doctor --runtime-dir runtime_stage`
- `AITEAM_SUBSCRIPTION_<PROVIDER>_ENABLED=0` para marcar cuenta no disponible.
- `AITEAM_SUBSCRIPTION_<PROVIDER>_LIMIT_REACHED=1` para simular limite agotado.
- `AITEAM_PROVIDER_<PROVIDER>_DEGRADED=1` para degradacion tecnica.
- `AITEAM_REQUIRE_API_KEYS=1` para exigir claves API en fallback.

Comandos opcionales para CLIs Pro:

- `AITEAM_OPENAI_PRO_COMMAND=["opencode","run","--prompt","{prompt}"]`
- `AITEAM_GEMINI_PRO_COMMAND=["gemini","chat","{prompt}"]`
- `AITEAM_CLAUDE_PRO_COMMAND=["claude","-p","{prompt}"]`

Continuidad:

- Si hay fallo tecnico/límites de modelo, el orquestador puede handoff a agente sustituto del mismo rol.
- El sustituto recibe contexto de memoria (`handoff_context`) y notificacion por mailbox.

## 12) Preflight y snapshots de recuperación

Preflight recomendado antes de ejecutar rondas críticas:

- `python -m aiteam.cli system-check --runtime-dir runtime_stage --environment stage --strict`

Versionado local rápido (sin Git):

- crear punto de recuperación: `snapshot-create`
- listar puntos: `snapshot-list`
- restaurar un punto: `snapshot-restore --snapshot-id <id>`

Ejemplo:

```bash
python -m aiteam.cli snapshot-create --snapshot-label "pre-release"
python -m aiteam.cli snapshot-list
python -m aiteam.cli snapshot-restore --snapshot-id 20260220T120000Z-abc123
```
