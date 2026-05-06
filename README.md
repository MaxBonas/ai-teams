# AI Teams

Control plane multi-agente para equipos de programación.

AI Teams implementa un modelo Lead-first sobre SQLite: issues, agents, runs, wakeup queue e interactions persistentes. El Lead decide el proyecto, forma el equipo y convierte tareas en issues vivos que avanzan por heartbeats hasta completarse, bloquearse o pedir una decisión al usuario.

## Índice

- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Arranque](#arranque)
- [Primer uso (UI)](#primer-uso-ui)
- [Adapters disponibles](#adapters-disponibles)
- [Comandos útiles](#comandos-útiles)
- [Stack técnico](#stack-técnico)
- [Documentación viva](#documentación-viva)
- [Norte de producto](#norte-de-producto)

---

## Requisitos

| Herramienta | Versión mínima | Notas |
|---|---|---|
| Python | 3.10+ | Recomendado 3.12 |
| Node.js | 18+ | Para el frontend Vite/React |
| Git | cualquiera | Para clonar el repo |
| SQLite | incluido en Python | No requiere instalación extra |

Al menos **un adapter LLM** debe estar disponible (ver [Adapters disponibles](#adapters-disponibles)).

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/tu-org/ai-teams.git
cd ai-teams
```

### 2. Entorno Python

**Windows:**
```powershell
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Dependencias del frontend

```bash
cd ide-frontend
npm install
cd ..
```

### 4. Archivo de entorno

```bash
cp .env.example .env
```

Edita `.env` y completa al menos una de las siguientes secciones:
- **API key** de OpenAI, Google o Anthropic (para inferencia directa por API)
- **Comando CLI** de Codex, Gemini o Claude Code (para inferencia por suscripción)

El campo `AITEAM_API_KEY` es la clave interna de autenticación entre el frontend y el backend. Puedes usar cualquier cadena aleatoria (p.ej. `openssl rand -hex 16`).

---

## Configuración

### Carpeta de proyectos (`AITEAM_PROJECTS_ROOT`)

AI Teams guarda cada proyecto en una subcarpeta dentro de una carpeta raíz. Hay dos formas de configurarla:

**Opción A — Variable de entorno** (recomendada para CI o instalaciones sin UI):
```env
AITEAM_PROJECTS_ROOT=C:\Users\TuNombre\MisProyectosAI
```

**Opción B — Primera vez en la UI** (recomendada para uso interactivo):
Al arrancar por primera vez sin `AITEAM_PROJECTS_ROOT` configurado, la UI muestra una pantalla de configuración inicial donde puedes seleccionar la carpeta. También puedes cambiarla más tarde desde la pestaña **Config**.

### API keys

Añade en `.env` las claves de los providers que vayas a usar:

```env
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Arranque

### Windows (script automático)

```powershell
.\start_ide.bat
```

Detener:
```powershell
.\stop_ide.bat
```

### Manual (cualquier OS)

**Terminal 1 — Backend:**
```bash
# Windows
venv\Scripts\python -m uvicorn api.main:app --reload --port 8010

# macOS / Linux
venv/bin/python -m uvicorn api.main:app --reload --port 8010
```

**Terminal 2 — Frontend:**
```bash
cd ide-frontend
npm run dev -- --port 9490
```

Abre el navegador en **http://localhost:9490**

---

## Primer uso (UI)

1. **Configuración inicial**: si no configuraste `AITEAM_PROJECTS_ROOT`, la UI te pedirá una carpeta raíz para proyectos. Elige la carpeta donde AI Teams creará sus proyectos.

2. **Crear un proyecto**: haz clic en **Nuevo proyecto** e introduce un nombre. Se creará la carpeta y la base de datos SQLite dentro de ella.

3. **Configurar adapters**: en la pestaña **Config**, verifica qué adapters CLI están disponibles (Codex, Gemini, Claude Code) y autentica los que vayas a usar.

4. **Nueva tarea**: escribe la tarea en el panel izquierdo y selecciona el perfil de ejecución:
   - **Equipo completo**: Lead + Engineer + Reviewer (recomendado)
   - **Lead + Quorum**: planificación con revisión múltiple
   - **Solo Lead**: para tareas simples de planificación

5. **Seguimiento**: usa las pestañas Chat, Timeline, Runs e Issue para seguir el progreso del equipo.

---

## Adapters disponibles

AI Teams puede usar LLMs de dos formas independientes:

### Por API (inferencia directa)

Requiere API key en `.env`. Sin instalación adicional.

| Provider | Variable | Modelos disponibles |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | GPT-4o, GPT-4o-mini, etc. |
| Google | `GOOGLE_API_KEY` | Gemini 2.0 Flash, Pro, etc. |
| Anthropic | `ANTHROPIC_API_KEY` | Claude Sonnet, Opus, Haiku |
| Groq | `GROQ_API_KEY` | Llama 3, Mixtral, etc. |

### Por suscripción CLI (sin consumo de API key)

Requiere tener el CLI instalado y autenticado con tu cuenta de suscripción.

| CLI | Instalación | Variable de entorno |
|---|---|---|
| Codex CLI (OpenAI) | `npx -y @openai/codex` | `AITEAM_OPENAI_PRO_COMMAND` |
| Gemini CLI (Google) | `npx -y @google/gemini-cli` | `AITEAM_GEMINI_PRO_COMMAND` |
| Claude Code (Anthropic) | `npx -y @anthropic-ai/claude-code` | `AITEAM_CLAUDE_PRO_COMMAND` |

### Modelo local (Ollama)

```bash
# Instalar Ollama: https://ollama.ai
# Luego crear el modelo personalizado:
ollama create aiteam-qwen-coder -f runtime/ollama/Modelfile.aiteam-qwen-coder
```

Configura en `.env`:
```env
OLLAMA_HOST=http://localhost:11434
```

---

## Comandos útiles

```powershell
# Tests
.\scripts\pytest_local.bat tests -q --tb=short

# Migración de schema SQLite
.\scripts\python_local.bat scripts\migrate_to_v2.py --json

# Verificar providers disponibles
.\scripts\python_local.bat scripts\probe_providers.py
```

---

## Stack técnico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.10+ · FastAPI · Uvicorn |
| Base de datos | SQLite (una DB por proyecto) |
| Frontend | React 19 · TypeScript 5 · Vite |
| Tests | pytest |
| Config por máquina | `~/.config/aiteams/settings.json` + `.env` |

`runtime/`, `venv/` y `node_modules/` son estado local por máquina. No se versionan.

---

## Documentación viva

- `docs/MIGRATION_PAPERCLIP.md` — plan rector de la arquitectura actual
- `docs/PAPERCLIP_GUIDE.md` — patrones operativos de referencia
- `docs/INDEX.md` — índice de fuentes activas
- `AGENTS.md` — instrucciones para agentes de desarrollo en este repo
- `task.md` — backlog activo

---

## Norte de producto

AI Teams es un **agent workspace para desarrollo de software**:

- Se crea primero un Lead; el Lead decide el proyecto y forma el equipo
- El usuario propone tareas y el Lead las convierte en issues vivos
- El equipo avanza por heartbeats hasta completar, bloquearse o pedir una decisión
- `solo_lead`, `lead_quorum` y `full_team` son perfiles de ejecución de primera clase
- Los seniors planifican, supervisan y ejecutan lo complejo; los workers baratos hacen lectura, investigación y tareas simples
- Suscripciones LLM y APIs se pueden usar de forma independiente
