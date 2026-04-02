# Vision UX — Chat compacto con flujo desplegable al estilo Claude App

Fecha: `2026-04-02`
Estado: `vision de producto — no prioritario`
Relacionado con:

- `ide-frontend/src/components/TeamChat.tsx`
- `ide-frontend/src/components/AgentPanel.tsx`
- `ide-frontend/src/components/AgentLane.tsx`
- `docs/IMPLEMENTATION_PLAYBOOK.md`

---

## El problema actual

El chat funciona, pero el flujo de una run es verboso por defecto.

Hoy cuando el equipo trabaja:

1. El `AgentPanel` aparece como un bloque separado debajo de los mensajes
2. Cada fase tiene su propia `AgentLane` con cabecera, icono, chip de modelo, timer, output y pensamiento
3. Todo visible a la vez: si hay 6 fases, hay 6 bloques expandidos
4. El informe final del Team Lead (`lead_close`) aparece como otro mensaje mas entre todo ese ruido

El resultado visual es denso. Una run con 4-6 agentes produce una pagina de bloques con texto, chips, timers y badges que compite con el mensaje final en importancia visual.

---

## La referencia: como lo hace Claude App

Claude App tiene un patron de UX muy bueno cuando usa herramientas o delega:

1. **Mientras trabaja**: aparece un bloque colapsado compacto dentro del hilo del mensaje
   - Muestra: `> Ejecuto 4 agentes` con spinner animado
   - Expandible: log compacto de pasos en curso
   - Cada paso expandible individualmente si se quiere ver detalle

2. **Al terminar**: el bloque se cierra solo en una linea de resumen
   - Muestra: `> Ejecuto 4 agentes` (sin spinner, colapsado)
   - Click para ver el historial si interesa

3. **El mensaje final**: aparece limpio, como prosa directa
   - Sin competencia visual con el proceso interno
   - El proceso fue visible si lo quisiste ver; ahora no molesta

La clave del patron: **el proceso es opt-in, el resultado es opt-out**.

---

## Vision para Ai_Teams

El flujo del chat deberia sentirse asi:

### Mientras corre una run

El chat muestra el mensaje del usuario, luego:

```
┌─────────────────────────────────────────────┐
│ ▸ Equipo trabajando...   [3 activos]  [spinner]│
└─────────────────────────────────────────────┘
```

Colapsado por defecto. Si el usuario hace click:

```
┌─────────────────────────────────────────────┐
│ ▾ Equipo trabajando...   [3 activos]  [spinner]│
│                                             │
│   ✓ lead_intake   TL    12s   openai/gpt-5  │
│   ◉ build         EG    →     openai/gpt-5  │  ← streaming en vivo
│     > Implementando el modulo de auth...    │
│   ○ review        RV    en espera           │
│   ○ qa            QA    en espera           │
└─────────────────────────────────────────────┘
```

Cada linea es compacta: icono de estado + rol + fase + tiempo + modelo.
Solo la activa muestra texto en streaming, y solo el inicio (preview).
El detalle completo: click en la linea.

### Al terminar la run

El bloque se cierra automaticamente a su forma colapsada:

```
┌─────────────────────────────────────────────┐
│ ▸ 4 fases · 78s · 2 warnings               │
└─────────────────────────────────────────────┘

[Mensaje limpio del Team Lead con el informe final]

[input del usuario]
```

El mensaje del Team Lead es lo que domina visualmente.
El historial esta ahi, clickable, pero no interfiere.

### Si el usuario quiere el detalle

Click en el bloque colapsado:

```
┌─────────────────────────────────────────────┐
│ ▾ 4 fases · 78s · 2 warnings               │
│                                             │
│   ✓ lead_intake   TL    8s    openai/gpt-5  │
│   ✓ build         EG    45s   groq/llama    │
│     ▸ Ver output completo                   │
│   ✓ review        RV    15s   openai/gpt-5  │
│   ✓ qa            QA    10s   openai/gpt-5  │
└─────────────────────────────────────────────┘
```

Cada fase expandible individualmente para ver su output.

---

## Diferencia clave con la implementacion actual

| Hoy | Vision |
|---|---|
| AgentPanel separado del hilo de mensajes | Disclosure inline dentro del mensaje |
| Todas las lanes visibles a la vez | Solo la activa muestra texto; el resto compacto |
| Panel colapsable como bloque completo | Cada paso colapsable individualmente |
| Lead_close aparece como otro mensaje | Lead_close es el mensaje principal |
| Verboso por defecto | Compacto por defecto, detalle opt-in |
| Timer vivo en cada lane | Solo en la lane activa |
| Badges, chips y colores en cada lane | Solo en hover o al expandir |

---

## Estado actual del codigo

### Lo que ya existe y se puede reutilizar

**`AgentLane.tsx`**:
- Tiene status (`waiting`, `active`, `completed`, `failed`)
- Tiene `thinkingExpanded` y `outputExpanded` para colapsar secciones
- Tiene timer (`ElapsedTimer`) ya implementado
- Tiene chips de modelo y provider ya implementados
- Tiene `preview` de 160 chars para mostrar sin expandir

**`AgentPanel.tsx`**:
- Tiene toggle de colapso del panel completo
- Tiene resumen de estados (activos, ok, fail, total)
- Ordena por rol (`ROLE_ORDER`)

**`TeamChat.tsx`**:
- `agentLanes: Map<string, AgentLaneState>` — estado de todas las lanes
- `setAgentLanes` actualizado via SSE (`agent_started`, `agent_chunk`, `agent_completed`)
- `buildTaskHistoryLanes()` — construye lanes desde task_summaries al rehidratar
- `mergeAgentLaneMaps()` — combina incoming con current

**SSE events disponibles** (ya emitidos por el backend):
- `agent_started`: `{task_id, agent_id, role, phase, provider, model, channel}`
- `agent_chunk`: `{task_id, chunk_type: "output"|"thinking", text}`
- `agent_completed`: `{task_id, output_preview, duration_ms, state}`

Todo el estado y los eventos ya existen. Lo que cambia es **solo el renderizado**.

### Lo que hay que cambiar

**`AgentPanel.tsx`**: convertir de "panel separado con todas las lanes" a "disclosure compacto integrable en el hilo del chat"

**`AgentLane.tsx`**: anadir modo `compact` (una sola linea) vs modo `expanded` (el comportamiento actual)

**`TeamChat.tsx`**:
- Integrar el `AgentPanel` dentro del JSX del hilo de mensajes, no como elemento hermano
- Controlar auto-colapso cuando la run termina y aparece el mensaje final
- Separar visualmente el streaming del Lead (`streamingText`) del bloque de lanes

---

## Diseno de componentes para la nueva UX

### `RunDisclosure` (nuevo componente o refactor de AgentPanel)

```tsx
interface RunDisclosureProps {
  lanes: Map<string, AgentLaneState>;
  runComplete: boolean;
  runSummary?: {
    phaseCount: number;
    durationMs: number;
    warnings: number;
    failedPhases: number;
  };
}

export default function RunDisclosure({ lanes, runComplete, runSummary }: RunDisclosureProps) {
  // Por defecto: abierto mientras corre, cerrado al terminar
  const [expanded, setExpanded] = useState(!runComplete);
  const [expandedLaneId, setExpandedLaneId] = useState<string | null>(null);

  useEffect(() => {
    // Auto-colapsar cuando la run termina
    if (runComplete) {
      const timeout = setTimeout(() => setExpanded(false), 1500);
      return () => clearTimeout(timeout);
    }
  }, [runComplete]);

  const activeCount = [...lanes.values()].filter(l => l.status === 'active').length;
  const isRunning = activeCount > 0;

  return (
    <div className="run-disclosure">
      <button
        className="run-disclosure-trigger"
        onClick={() => setExpanded(v => !v)}
      >
        <span className="run-disclosure-chevron">{expanded ? '▾' : '▸'}</span>
        <span className="run-disclosure-label">
          {isRunning
            ? `Equipo trabajando...`
            : runSummary
              ? `${runSummary.phaseCount} fases · ${Math.round((runSummary.durationMs || 0) / 1000)}s`
              : `${lanes.size} fases`
          }
        </span>
        {isRunning && <span className="run-disclosure-spinner" />}
        {!isRunning && runSummary && runSummary.failedPhases > 0 && (
          <span className="run-disclosure-badge run-disclosure-badge-fail">
            {runSummary.failedPhases} fail
          </span>
        )}
      </button>

      {expanded && (
        <div className="run-disclosure-body">
          {sortLanes([...lanes.values()]).map(lane => (
            <CompactLaneRow
              key={lane.taskId}
              lane={lane}
              expanded={expandedLaneId === lane.taskId}
              onToggle={() => setExpandedLaneId(
                expandedLaneId === lane.taskId ? null : lane.taskId
              )}
            />
          ))}
        </div>
      )}
    </div>
  );
}
```

### `CompactLaneRow` (nuevo, reemplaza AgentLane en modo compacto)

```tsx
interface CompactLaneRowProps {
  lane: AgentLaneState;
  expanded: boolean;
  onToggle: () => void;
}

function CompactLaneRow({ lane, expanded, onToggle }: CompactLaneRowProps) {
  const icon = STATUS_ICONS[lane.status]; // ✓ ◉ ○ ✗
  const isActive = lane.status === 'active';

  return (
    <div className={`compact-lane compact-lane--${lane.status}`}>
      <button className="compact-lane-row" onClick={onToggle}>
        <span className="compact-lane-icon">{icon}</span>
        <span className="compact-lane-phase">{lane.phase}</span>
        <span className="compact-lane-role">{lane.role.slice(0,2).toUpperCase()}</span>
        {lane.durationMs > 0 && (
          <span className="compact-lane-time">{Math.round(lane.durationMs / 1000)}s</span>
        )}
        {isActive && <ElapsedTimer startedAt={lane.startedAt} stopped={false} />}
        {(lane.provider || lane.model) && (
          <span className="compact-lane-model">
            {lane.provider ?? ''}/{(lane.model ?? '').split('-').slice(-1)[0]}
          </span>
        )}
        {expanded ? '▾' : (lane.preview || isActive) ? '▸' : null}
      </button>

      {/* Preview en streaming — solo para lane activa, sin expandir */}
      {isActive && lane.outputText && !expanded && (
        <div className="compact-lane-streaming">
          {lane.outputText.slice(-200)}
        </div>
      )}

      {/* Detalle completo — solo al expandir */}
      {expanded && (
        <div className="compact-lane-detail">
          {lane.thinkingText && (
            <div className="compact-lane-thinking">{lane.thinkingText}</div>
          )}
          <div className="compact-lane-output">
            {lane.outputText || lane.preview || '(sin output)'}
          </div>
        </div>
      )}
    </div>
  );
}
```

### Integracion en `TeamChat.tsx`

El cambio clave en el hilo de mensajes: el `RunDisclosure` va DENTRO del JSX de mensajes, no como elemento hermano:

```tsx
// Donde hoy va AgentPanel como elemento hermano del log:
// ANTES:
<div className="chat-log" ref={logRef}>
  {messages.map(msg => <MessageBubble key={msg.id} msg={msg} />)}
  {streamingText && <StreamingBubble text={streamingText} />}
</div>
<AgentPanel lanes={agentLanes} visible={agentLanes.size > 0} />

// DESPUES:
<div className="chat-log" ref={logRef}>
  {messages.map((msg, i) => (
    <>
      <MessageBubble key={msg.id} msg={msg} />
      {/* Despues del ultimo mensaje del usuario, antes del streaming del Lead */}
      {i === lastUserMessageIndex && agentLanes.size > 0 && (
        <RunDisclosure
          lanes={agentLanes}
          runComplete={!loading}
          runSummary={runComplete ? buildRunSummary(chatProgress) : undefined}
        />
      )}
    </>
  ))}
  {/* El streaming del Lead aparece DEBAJO del RunDisclosure, limpio */}
  {streamingText && (
    <div className="streaming-lead-message">
      <StreamingBubble text={streamingText} />
    </div>
  )}
</div>
```

---

## Comportamiento de auto-colapso

### Regla de apertura/cierre

| Situacion | Estado del disclosure |
|---|---|
| Run empieza | Abierto automaticamente |
| Hay fases activas | Abierto, spinner visible |
| Run termina | Esperar 1.5s, luego cerrar suavemente |
| Lead_close aparece | Cerrar el disclosure |
| Usuario hizo click para abrir | Respetar la preferencia manual |
| Usuario hizo click para cerrar mientras corre | Respetar (no forzar re-apertura) |

La preferencia manual tiene prioridad sobre el auto-colapso.

### Transicion CSS recomendada

```css
.run-disclosure-body {
  overflow: hidden;
  transition: max-height 0.3s ease-out, opacity 0.3s ease-out;
}

.run-disclosure-body.is-collapsing {
  max-height: 0;
  opacity: 0;
}
```

---

## Iconos de estado para el modo compacto

```tsx
const STATUS_ICONS: Record<AgentLaneState['status'], string> = {
  waiting:   '○',
  active:    '◉',  // o un spinner CSS
  completed: '✓',
  failed:    '✗',
};

const STATUS_COLORS: Record<AgentLaneState['status'], string> = {
  waiting:   'var(--text-secondary)',
  active:    'var(--status-amber)',
  completed: 'var(--status-green)',
  failed:    'var(--status-red)',
};
```

---

## CSS — clases nuevas necesarias

```css
/* Contenedor del disclosure dentro del hilo */
.run-disclosure {
  margin: 4px 0 8px 0;
  border-radius: 6px;
  border: 1px solid var(--border-color, rgba(255,255,255,0.08));
  background: var(--bg-surface, rgba(255,255,255,0.03));
  overflow: hidden;
}

/* Trigger / cabecera clickable */
.run-disclosure-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: left;
}
.run-disclosure-trigger:hover {
  color: var(--text-primary);
  background: rgba(255,255,255,0.04);
}

/* Spinner pequeño */
.run-disclosure-spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--text-secondary);
  border-top-color: var(--status-amber);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* Cuerpo del disclosure */
.run-disclosure-body {
  border-top: 1px solid var(--border-color, rgba(255,255,255,0.06));
  padding: 4px 0;
}

/* Fila compacta de una fase */
.compact-lane {
  border-bottom: 1px solid rgba(255,255,255,0.04);
}
.compact-lane:last-child {
  border-bottom: none;
}

.compact-lane-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 10px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 11px;
  color: var(--text-secondary);
  text-align: left;
}
.compact-lane-row:hover {
  background: rgba(255,255,255,0.04);
}

.compact-lane--active .compact-lane-row {
  color: var(--text-primary);
}
.compact-lane--completed .compact-lane-icon { color: var(--status-green); }
.compact-lane--failed .compact-lane-icon    { color: var(--status-red); }
.compact-lane--active .compact-lane-icon    { color: var(--status-amber); }

.compact-lane-phase { flex: 1; font-family: monospace; font-size: 11px; }
.compact-lane-role  { opacity: 0.6; font-size: 10px; }
.compact-lane-time  { opacity: 0.5; font-size: 10px; }
.compact-lane-model { opacity: 0.45; font-size: 10px; }

/* Streaming en vivo debajo de la fila activa */
.compact-lane-streaming {
  padding: 2px 10px 6px 28px;
  font-size: 11px;
  color: var(--text-secondary);
  font-family: monospace;
  white-space: pre-wrap;
  opacity: 0.75;
}

/* Detalle expandido */
.compact-lane-detail {
  padding: 6px 10px 8px 28px;
  font-size: 11px;
  color: var(--text-secondary);
  white-space: pre-wrap;
}
.compact-lane-thinking {
  opacity: 0.6;
  font-style: italic;
  margin-bottom: 6px;
  border-left: 2px solid rgba(255,255,255,0.1);
  padding-left: 8px;
}
.compact-lane-output {
  white-space: pre-wrap;
  max-height: 300px;
  overflow-y: auto;
}
```

---

## Migracion desde la implementacion actual

Este cambio es **aditivo y no rompe nada** si se hace bien:

### Estrategia de implementacion en 3 pasos

**Paso 1** — Crear `CompactLaneRow` sin eliminar `AgentLane`
- El componente nuevo vive en `ide-frontend/src/components/CompactLaneRow.tsx`
- `AgentLane.tsx` no se toca
- Se puede hacer A/B testeable con un flag

**Paso 2** — Crear `RunDisclosure` sin eliminar `AgentPanel`
- El componente nuevo en `ide-frontend/src/components/RunDisclosure.tsx`
- `AgentPanel.tsx` no se toca
- Probar el nuevo componente con datos mock antes de conectar al SSE real

**Paso 3** — Cambiar `TeamChat.tsx` para usar `RunDisclosure` en lugar de `AgentPanel`
- Un solo punto de cambio
- Si hay regresion, revertir solo ese cambio

**Tests requeridos**:
```typescript
// tests/frontend/ (si se usan con vitest o similar)
describe('RunDisclosure', () => {
  it('opens automatically when run starts');
  it('shows spinner when lanes are active');
  it('auto-collapses 1.5s after run completes');
  it('respects manual open/close preference');
  it('shows compact row per lane');
  it('expands individual lane on click');
  it('shows streaming preview only for active lane');
  it('shows run summary when collapsed and complete');
});

describe('CompactLaneRow', () => {
  it('shows status icon per status');
  it('shows model chip abbreviated');
  it('shows elapsed timer only when active');
  it('expands output on click');
  it('hides expand arrow when no output');
});
```

**Criterio de done**:
- `RunDisclosure` reemplaza a `AgentPanel` en `TeamChat.tsx`
- El disclosure esta integrado en el hilo de mensajes, no fuera
- Auto-colapso al terminar la run
- Lead_close aparece como mensaje limpio debajo
- `AgentPanel.tsx` puede eliminarse o quedarse como fallback
- TypeScript clean: `tsc --noEmit` sin errores
- No hay regresion visual en runs en curso ni en historial

---

## Lo que NO debe cambiar

- Los SSE events (`agent_started`, `agent_chunk`, `agent_completed`) — no tocar backend
- El estado `agentLanes: Map<string, AgentLaneState>` — no cambiar la estructura
- La logica de `buildTaskHistoryLanes()` y `mergeAgentLaneMaps()` — no tocar
- Los colores de rol — usar los mismos que ya existen en `AgentLane.tsx`
- El comportamiento de `streamingText` — el streaming del Lead sigue igual

El cambio es 100% de renderizado. Cero cambios de logica de estado o backend.

---

## Cuando hacerlo

Esta mejora es **no prioritaria**. Hacerla cuando:

1. Los 2 tests fallidos esten arreglados (URGENTE-1)
2. B7a (hardening del catalogo de routing) este completo
3. Haya tiempo sin otras urgencias

No hacerla antes de esos bloqueantes: el riesgo de regresion en `TeamChat.tsx` (73KB) no justifica el beneficio visual mientras haya bugs funcionales.

**Estimacion**: 2-4 horas de trabajo de frontend bien acotado.

---

## Referencia visual rapida

```
ANTES:                          DESPUES:
─────────────────────           ─────────────────────
👤 "Crea un juego"              👤 "Crea un juego"

[AgentPanel separado]           ▸ 4 fases · 78s
  ┌─ TL lead_intake ──┐
  │ openai/gpt-5 12s  │         🤖 [Informe final del Team Lead]
  │ [output completo] │             limpio y legible
  └───────────────────┘
  ┌─ EG build ────────┐
  │ groq/llama 45s    │
  │ [output completo] │
  └───────────────────┘         👤 [input del usuario]
  ... etc

🤖 [Informe final]
   buried under lanes

👤 [input]
─────────────────────           ─────────────────────
```
