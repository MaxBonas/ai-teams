# UI Redesign: Simplificación del Frontend (EPIC-3)

**Fecha**: 2026-03-28
**Estado**: ✅ Implementado
**Commit de referencia**: `ffaa050`
**Bundle**: 671 KB → 278 KB

---

## Motivación

El frontend original era un IDE completo con paneles para editor de código (Monaco), terminal (xterm), explorador de archivos, dashboard de métricas y OpsHub (4 tabs). La mayor parte de esta funcionalidad era legacy no utilizada, añadía complejidad visual y triplicaba el tamaño del bundle.

El objetivo del sistema es orquestar un equipo de agentes de IA. La UI debe reflejar eso: un chat con el equipo + visibilidad de estado. Nada más.

---

## Qué se eliminó

| Componente | Razón |
|------------|-------|
| `CodeEditor` / `DiffEditor` (Monaco) | No utilizado activamente; 400+ KB del bundle |
| `Terminal` (xterm) | No integrado con el workflow de agentes |
| `FileExplorer` | Panel lateral sin función en el flujo actual |
| `AIDashboard` | Información redundante con StatusPanel |
| `OpsHub` (4 tabs: Queues, Events, Agents, Memory) | Demasiado técnico, bajo valor UX para el usuario final |

---

## Nueva arquitectura de layout

```
TopBar (logo + workspace + budget HUD)
└─ PanelGroup horizontal (react-resizable-panels)
   ├─ Panel Chat (68%) — collapsible
   │   └─ TeamChat
   │       ├─ header (título + botón minimizar)
   │       ├─ team-chat-log (scroll)
   │       │   ├─ mensajes del usuario
   │       │   ├─ mensajes del Team Lead / asistente
   │       │   ├─ AgentPanel (lanes en tiempo real)
   │       │   └─ streaming text
   │       └─ footer fijo (composer: textarea + send)
   │
   └─ Panel Estado (32%) — collapsible
       └─ StatusPanel
           ├─ budget hoy (USD)
           └─ últimas 5 runs
```

### Características clave del layout

- **Paneles collapsibles**: misma mecánica que antes (`react-resizable-panels`). Al colapsar, el panel aparece en la TopBar como botón "Minimized" para restaurar.
- **Composer fijo**: el footer del chat no desaparece al scrollear. CSS crítico: `.team-chat-body .team-chat-input-wrap { height: auto; flex-shrink: 0 }`.
- **AgentPanel in-thread**: las lanes de agentes aparecen dentro del log de conversación, entre los mensajes y el streaming text, no en un panel lateral separado.

---

## Componentes nuevos

### `Modal.tsx`
Overlay genérico para contenido largo. Se usa cuando un mensaje supera los 600 caracteres.
- Cierra con `Escape` o click fuera del card
- Props: `title`, `onClose`, `children`, `wide?`
- Clases CSS: `.modal-overlay`, `.modal-card`, `.modal-card--wide`

### `StatusPanel.tsx`
Reemplaza OpsHub. Muestra:
- Budget del día (`/api/dashboard` cada 4s)
- Últimas 5 runs del estado del equipo (`/api/aiteam/state?environment=dev` cada 4s)
- Props: `workspacePath`, `minimized`, `onToggleMinimize`

---

## Cambios en `TeamChat.tsx`

### Layout anterior
```
TeamChat (horizontal panels)
├─ Conversation panel (58%)
│   └─ log de mensajes
└─ Composer panel (42%)
    ├─ AgentPanel (en la mitad superior del composer)
    └─ textarea + send
```

### Layout nuevo
```
TeamChat (vertical flex)
├─ .team-chat-log (flex: 1, overflow-y: auto)
│   ├─ mensajes
│   ├─ AgentPanel
│   └─ streaming
└─ footer fijo
    └─ textarea + send
```

### Outputs largos
Mensajes con más de 600 caracteres se truncan a 500 chars con un botón "Ver respuesta completa →" que abre Modal. El contenido completo se pasa como `<pre>` dentro del modal.

---

## Cambios en `TopBar.tsx`

- Eliminado: tabs Editor/Dashboard, botón PlayCircle
- Eliminado prop `activeTab`, `onTabChange`
- Conservado: logo "AI Teams", WorkspaceSelector, minimized windows tray, budget HUD

---

## Impacto en bundle

| Antes | Después |
|-------|---------|
| 671 KB | 278 KB |
| Monaco (editor) activo | Monaco no importado → tree-shaken |
| xterm (terminal) activo | xterm no importado → tree-shaken |

---

## Problemas conocidos resueltos durante la implementación

1. **Footer robaba todo el espacio**: `.team-chat-input-wrap` tenía `height: 100%` del layout antiguo (panel de 42%). Fix: override con `height: auto; flex-shrink: 0` en el contexto de `.team-chat-body`.

2. **TypeScript verbatimModuleSyntax**: `AgentPanel.tsx` importaba `AgentLaneState` como value import. Fix: `import type { AgentLaneState }` separado.

3. **Imports React no usados**: `AgentLane.tsx` y `AgentPanel.tsx` tenían `import React` explícito. Eliminados (React 19 no requiere import explícito con nuevo JSX transform).

---

## Pendiente (EPIC-6)

Para convertir este frontend en extensión VS Code:
- Auditar `localStorage` → reemplazar con `acquireVsCodeApi` message passing
- Verificar ausencia de `window.open` y URLs absolutas
- Crear scaffold `extension/` con Webview panel
- Ajustar `apiFetch` para funcionar en ambos entornos

Ver `docs/TASKS_2026_03_28.md#epic-6` para detalles.
