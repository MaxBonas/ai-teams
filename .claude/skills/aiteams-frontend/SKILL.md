---
name: aiteams-frontend
description: "Trabajar en el frontend del IDE de AI Teams (ide-frontend/): stack React 19 + TypeScript + Vite, tokens de diseño, convenciones de API y verificación. Usar al tocar UI, estilos, componentes, o la capa de comunicación con el backend (puerto 8010)."
---

# Frontend del IDE de AI Teams — mapa y convenciones

El frontend vive en `ide-frontend/` y es el IDE web del orquestador: chat con el Lead,
tablero de issues, timeline y estado del proyecto contra el backend FastAPI (puerto 8010).

## Stack real (no asumir otro)

- **React 19 + TypeScript 5.9 + Vite 5**, sin router, sin librería de estado, sin framework CSS.
- Iconos: `lucide-react` (única dependencia de UI). NO añadir dependencias pesadas
  (Tailwind, MUI, styled-components…) sin razón funcional explícita.
- Estructura mínima y deliberada:
  - `src/App.tsx` — monolito (~3600 líneas) con casi toda la UI. Trocearlo solo con
    razón funcional, igual que el executor del backend.
  - `src/components/ThreadView/` — vista de hilo (patrón a seguir para extraer componentes:
    carpeta + `index.ts` re-export).
  - `src/lib/api.ts` — ÚNICA capa de acceso al backend.
  - `src/index.css` — TODOS los tokens de diseño y estilos base.

## Puertos y arranque (fuente de confusión conocida)

- **Canónico: `start_ide.bat`** → backend 8010, frontend **9490** (pasa
  `--port 9490 --strictPort` y setea `VITE_API_URL=http://127.0.0.1:8010`).
  Logs en `runtime/ide_logs/` (frontend.log / backend.log).
- `npm run dev` a secas usa **9483** (flag en package.json); `vite.config.ts` declara 9490.
  Si un puerto "no responde", verificar primero CÓMO se arrancó antes de tocar config.
- Parar todo: `stop_ide.bat`.

## Capa de API — reglas

- Toda llamada al backend pasa por `apiFetch()` de `src/lib/api.ts`: inyecta
  `Authorization: Bearer` + `x-aiteam-api-key` (de `localStorage.AITEAM_API_KEY`) y
  `x-aiteam-workspace` (de `localStorage.AITEAM_V2_WORKSPACE_PATH`).
  NUNCA usar `fetch` directo a `http://127.0.0.1:8010` desde componentes.
- Base URL: `VITE_API_URL` (env de Vite), default `http://127.0.0.1:8010`.
- El backend puede estar siendo modificado por otro agente (Codex en ORCH-01 o local):
  ante un error de API nuevo, comprobar `git status` / backend.log antes de asumir bug propio.

## Sistema de diseño — tokens obligatorios

- Tema único **oscuro** (paleta tipo GitHub dark) definido como CSS custom properties en
  `src/index.css`: fondos (`--bg`, `--surface`, `--surface-2`), estados semánticos
  (`--accent*` verde = ok, `--pending*` ámbar, `--blocked*` rojo), texto
  (`--text`, `--text-bright`, `--text-muted`, `--text-dim`), `--radius`, `--shadow`.
- Tipografías: **Figtree** (UI) y **JetBrains Mono** (código), cargadas por Google Fonts.
- Regla: colores y radios SIEMPRE vía `var(--token)`, nunca hex sueltos en componentes.
  Un estado nuevo (p.ej. "warning") se añade como familia de tokens en `index.css`
  (color + `-bg` + `-text`), no inline.
- Para diseño visual nuevo (paletas, jerarquía, layout) apoyarse en las skills generales
  `frontend-design` y `theme-factory`; para gráficas, `dataviz`.

## Verificación (antes de dar nada por terminado)

1. `cd ide-frontend && npm run build` — corre `tsc -b` + build de Vite; los errores de
   tipos rompen el build, es el gate real.
2. `npm run lint` — ESLint 9 con react-hooks y react-refresh.
3. Verificación en vivo: arrancar con `start_ide.bat` (o dev server en 9490 con
   `VITE_API_URL` seteado) y comprobar el flujo afectado en el navegador — el frontend
   sin backend en 8010 muestra errores de conexión, no es señal de bug de UI.
4. No hay tests de frontend a día de hoy (2026-07-17); no inventar framework de tests
   sin pedirlo.

## Entorno

- Windows + Git Bash: paths con espacios (`Antigravity Projects`) siempre entre comillas.
- `node_modules/` NO se sincroniza entre máquinas (Syncthing); si el build falla tras un
  sync, `npm install` local antes de investigar otra cosa.
