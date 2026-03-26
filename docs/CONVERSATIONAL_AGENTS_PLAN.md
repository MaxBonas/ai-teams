# Plan: Sesiones Conversacionales Reales por Agente

**Estado:** Diseñado — pendiente de implementación
**Fecha:** 2026-03-26
**Autor:** Claude Sonnet 4.6 (sesión de arquitectura con MaxBonas)

---

## 1. Motivación

### El problema actual

El sistema AITeams funciona como un **orquestador de cadenas de LLM**, no como un equipo
de agentes con conversaciones persistentes. Cada vez que un agente (Engineer, Reviewer,
etc.) ejecuta una tarea, ocurre lo siguiente:

```
build_prompt(rol, titulo, descripción)
  → un único mensaje {"role": "user", "content": prompt_gigante}
  → llamada LLM
  → resultado guardado en metadata
  → contexto descartado completamente
```

Esto tiene consecuencias directas:

- El Engineer no recuerda lo que diseñó en la tarea anterior cuando llega una revisión
- El Reviewer no puede referirse al razonamiento previo del Engineer
- El Team Lead no puede mantener un hilo de coordinación con su equipo
- Cada llamada LLM empieza desde cero, perdiendo coherencia entre tareas relacionadas

### El contraste con Claude Teams (referencia)

En sistemas como Claude Teams o GitHub Copilot Workspace, cada agente tiene:
- Un hilo de conversación acumulativo (`messages[]`)
- Memoria de sus propias decisiones previas
- Capacidad de recibir feedback del líder y responder en contexto

### Por qué importa para AITeams

El proyecto AITeams tiene como objetivo ser un **sistema de desarrollo de software
autónomo** donde el equipo trabaja en un proyecto a lo largo del tiempo. Sin sesiones
conversacionales, los agentes son herramientas de un solo uso — con ellas, son
colaboradores con continuidad.

---

## 2. Objetivo

Hacer que cada agente mantenga un **hilo de conversación persistente por proyecto**,
de modo que:

1. El Engineer recuerde su propio razonamiento y código previo cuando recibe feedback
2. El Team Lead pueda enviar un mensaje mid-task al Engineer y este responda en contexto
3. Las iteraciones de review/QA sean conversaciones reales, no prompts independientes
4. El historial sea auditable y recuperable entre sesiones del sistema

---

## 3. Arquitectura propuesta

### 3.1 ConversationThread (nuevo concepto)

Cada agente tiene un `ConversationThread` por proyecto/run, almacenado en
`runtime/sessions/{agent_id}/{project_root}.json`:

```python
@dataclass
class ConversationThread:
    agent_id: str          # "engineer-1"
    project_root: str      # "CHAT-A1B2C3"
    messages: list[dict]   # [{"role": "user"|"assistant", "content": "..."}]
    created_at: str
    last_updated: str
    total_tokens: int
    turn_count: int
```

### 3.2 Flujo con sesión conversacional

```
Task 1 (build):
  Thread engineer-1 = []
  + user: "Diseña el sistema de auth con JWT..."
  + assistant: "Propongo JWT con refresh tokens, aquí el esquema..."
  → guardado en disco

Task 2 (review gate, feedback):
  Thread engineer-1 = [turno 1 anterior]
  + user: "Reviewer dice: el refresh token no expira. Corrige."
  + assistant: "Tienes razón. Añado TTL de 24h y rotación automática..."
  → el Engineer referencia su propio diseño previo

Mensaje del Team Lead mid-task:
  Thread engineer-1 = [turnos 1 y 2]
  + user: "[TEAM LEAD] El cliente ha pedido también 2FA. Integra TOTP."
  + assistant: "Integro TOTP en el flujo de login que ya definí en el turno 1..."
```

### 3.3 Cambios en el código (4 ficheros)

#### `aiteam/agent_session.py`
- Añadir `ConversationThread` dataclass
- Añadir `ThreadStore` para persistencia en `runtime/sessions/threads/`
- Método `append_turn(role, content)` y `get_messages() -> list[dict]`

#### `aiteam/adapters/api.py`
- `invoke(prompt: str, messages: list[dict] | None = None)`
- Si `messages` está presente, usarlo directamente en la llamada API
- Si no, comportamiento actual (compatibilidad hacia atrás garantizada)

#### `aiteam/adapters/subscription.py`
- Mismo cambio que `api.py` para adapters de suscripción

#### `aiteam/orchestrator.py`
- En `_run_task()`: recuperar thread del agente para el project_root actual
- Construir `messages` = thread anterior + nuevo task como turno de usuario
- Después de la respuesta: añadir al thread y guardar
- En mailbox: cuando TL envía mensaje a un agente, insertarlo en su thread

---

## 4. Estrategia de implementación

### Fase 1: ConversationThread persistente (no rompe nada)
- Implementar `ThreadStore` y `ConversationThread`
- Los threads se crean pero no se usan todavía en llamadas LLM
- Tests: verificar persistencia y recuperación

### Fase 2: Adapters con messages[]
- Modificar `ApiAdapter.invoke()` para aceptar `messages`
- Testar con Anthropic y OpenAI (ambos usan formato `messages[]`)
- Compatibilidad hacia atrás: si `messages=None`, construir `[{"role":"user","content":prompt}]`

### Fase 3: Orchestrator usa los threads
- En `_run_task()`: inyectar thread en lugar de prompt plano
- El prompt actual se convierte en el último turno de usuario
- Guardar respuesta como turno de assistant

### Fase 4: Mailbox bidireccional reactivo
- Cuando TL envía mensaje a Engineer via mailbox, se inserta en el thread del Engineer
- El Engineer procesa el mensaje en su próxima invocación (o inmediatamente si está activo)

---

## 5. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Threads muy largos → context window overflow | Truncar a las últimas N tokens; resumir turnos antiguos con el propio LLM |
| Costes mayores (más tokens por llamada) | Opcional por agente; desactivable con `AITEAM_AGENT_THREADS=0` |
| Coherencia entre hilos de distintos agentes | Compartir solo vía memoria (`AgentMemoryStore`), no via threads directos |
| Tests existentes rotos | Todo nuevo parámetro es opcional; tests actuales no cambian |

---

## 6. Resultado esperado

Con este cambio, un ciclo completo de desarrollo se verá así:

```
[TL]       "Necesitamos auth con JWT y 2FA para el sprint"
[Engineer] "Entendido. Propongo este esquema de tokens..."
[TL]       "Bien. Añade rate limiting al endpoint de login"
[Engineer] "Integro rate limiting en el endpoint que diseñé arriba..."
[Reviewer] "El rate limiting no persiste entre reinicios"
[Engineer] "Tienes razón. Uso Redis para el contador, aquí el código..."
[QA]       "Tests de integración pasados. Aprobado."
```

Cada turno referencia el anterior. El equipo trabaja como un equipo.
