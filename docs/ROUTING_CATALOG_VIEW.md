# Routing Catalog View

Fecha: `2026-04-02`
Estado: `MVP consultable completado`

## Objetivo

Dar visibilidad operativa al routing multimodelo real del sistema.

Hasta ahora, para responder preguntas como:

- que provider/model usa cada rol
- que fallbacks tiene cada rol
- por que un adapter no entra para un rol
- que está configurado vs que es realmente elegible en esta maquina

habia que inspeccionar `aiteam/config.py`, `aiteam/cli.py`, `config/model_catalog.example.json`,
`runtime/adapters.json`, `runtime/provider_ops.json` y el propio `HybridRouter`.

Eso era demasiado opaco para uso normal y hacia muy dificil ajustar coste, calidad y soberania del Lead.

## Qué resuelve esta vista

La vista consultable de routing muestra en un solo lugar:

- providers registrados
- adapters registrados
- orden configurado por rol
- primario efectivo por rol
- fallbacks efectivos por rol
- blockers por adapter/rol (`role_targets`, `team_lead_guard`, `adapter_unavailable`, etc.)
- diferencia entre lo **configurado** y lo **efectivamente elegible**

## Superficie implementada

### Backend

Endpoint:

- `/api/aiteam/routing/catalog`

Archivo:

- `api/routers/aiteam.py`

El payload compone:

- policy por defecto (`build_default_orchestrator(...).router.policy`)
- adapters efectivos del runtime actual
- model catalog cargado por el router
- estado operativo de providers (`runtime/provider_ops.json` cuando existe)
- matriz por rol con primario, fallbacks y blockers

### Frontend

Vista:

- nueva pestaña `Routing` en `StatusPanel`

Archivos:

- `ide-frontend/src/components/RoutingCatalogPanel.tsx`
- `ide-frontend/src/components/StatusPanel.tsx`
- `ide-frontend/src/styles/team.css`

## Decisión de producto

Se ha hecho **consultable primero** y **editable después**.

Razón:

- antes de permitir edición, había que hacer visible el estado real
- el sistema tiene varias capas de verdad parcial (policy, adapters, catalog, provider ops, role_targets)
- editar sin una vista consolidada iba a producir configuraciones ciegas y regresiones de routing

## Lo que aún no hace

Todavía no permite:

- editar el orden de providers por rol
- editar el orden de modelos por rol
- reasignar fallbacks por rol
- persistir overrides locales desde UI

Eso queda como siguiente fase:

- `vista editable de asignación por rol`

## Relación con el trabajo reciente

Esta vista nace después de varias correcciones funcionales relacionadas:

- visibilidad de `peer_consultation_summary`
- visibilidad de tareas creadas y lanes persistentes tras la run
- provider/model por agente en el chat
- reducción de uso de Anthropic fuera de `team_lead`
- endurecimiento prudente del placeholder gate

La intención es que el sistema deje de ser una caja negra tanto para observabilidad como para tuning económico.

## Trabajo reciente que esta vista debe absorber y robustizar

La vista de routing no nace aislada. Es la pieza de control que debe consolidar varias mejoras recientes
que ya existen en el sistema pero todavia no tienen una superficie única de gobierno:

- el chat ya muestra `provider/model` por agente y conserva historial de lanes al acabar la run
- `peer_consultation_summary` ya expone qué roles y qué familias de proveedor participaron realmente
- el core ya restringe Anthropic al `team_lead` por defecto
- el placeholder gate ya se endureció para dejar de bloquear corridas legítimas
- el chat ya expone `task_summaries` para que la run no desaparezca visualmente al terminar

La consecuencia es clara:

- ya no falta observabilidad mínima
- ahora falta gobierno operativo

La vista de routing debe pasar de "panel informativo" a "centro de control" del reparto real
de modelos, providers, coste, fallback y restricciones por rol.

## Puntos que aun necesitan robustez

### 1. Contrato backend de `/api/aiteam/routing/catalog`

Hoy el endpoint funciona y es útil, pero todavia debe endurecerse en varios frentes:

- versionado explícito del payload para no romper la UI futura editable
- separación más clara entre:
  - `defaults_del_repo`
  - `override_local`
  - `estado_efectivo_en_esta_maquina`
- códigos de `blockers` más estables y exhaustivos
- exposición explícita de `channel`, `tier`, `cost_class`, `reasoning_class`, `tool_support`, `stream_support`
- trazabilidad de por qué un rol cae en un fallback concreto

Objetivo de robustez:

- que la respuesta sea suficientemente estable como para servir tanto a la UI consultable como a la UI editable
- que explique estado real, no solo configuración bonita

### 2. Modelo de elegibilidad por rol

La elegibilidad actual ya contempla `role_targets`, salud del adapter, disponibilidad y algunas guardas del Lead.
Pero aún hay que consolidar mejor:

- reglas de exclusión por coste
- restricciones por canal (`subscription`, `api`, local)
- requisitos de capacidades (`tools`, `thinking`, `long_context`, `vision`, `json_mode`)
- restricciones por entorno o maquina
- prioridad por tipo de tarea (`lead_intake`, `engineering`, `review`, `qa`, `planning`)

Objetivo de robustez:

- que el catálogo no muestre solo "quién podría entrar", sino "quién debería entrar y por qué"

### 3. Superficie frontend del MVP

La vista actual es buena para leer, pero todavía es una primera versión:

- falta filtrado fuerte por rol, provider, canal y estado
- falta búsqueda por modelo
- falta drilldown completo de blockers y del porqué del primario/fallback actual
- falta snapshot histórico de una run concreta frente al catálogo actual
- falta comparación entre "configurado" y "lo que realmente se usó"

Objetivo de robustez:

- que no sea solo una lista de inventario, sino una herramienta de diagnóstico de routing

### 4. Persistencia futura de overrides

La fase editable todavía no existe, así que aún no está resuelto:

- dónde se guarda el override local
- cómo se fusiona con defaults del repo
- cómo se valida antes de guardar
- cómo se hace rollback
- cómo evitar dejar un rol sin fallback viable

Objetivo de robustez:

- que editar policy desde la UI sea seguro, reversible y explicable

## Objetivo de producto de la vista completa

La meta no es "una pantalla más".

La meta es tener una vista de configuración de modelos y roles que sea:

- muy completa
- funcional de verdad
- editable con seguridad
- suficientemente expresiva para gobernar el router sin tocar JSON a mano

Debe convertirse en la superficie principal para decidir:

- qué provider/model usa cada rol
- en qué orden
- con qué fallbacks
- con qué restricciones
- con qué coste máximo aceptable
- con qué capacidades mínimas
- y con qué overrides locales por máquina o por proyecto

## Alcance objetivo de la fase editable

### A. Configuración por rol

La UI debe permitir, por cada rol:

- definir orden de providers
- definir orden de modelos
- marcar primario preferido
- definir cadena de fallbacks
- limitar roles a ciertos providers/modelos
- preferir o vetar canales (`subscription`, `api`, local)
- fijar capacidades mínimas
- fijar tier/coste máximo

### B. Configuración por tipo de tarea

No basta con configurar por rol abstracto. Debe poder distinguirse:

- `lead_intake`
- `planning`
- `research`
- `engineering`
- `review`
- `qa`
- `delegate`
- `probe`

Porque un mismo rol puede necesitar routing distinto según el tipo de trabajo.

### C. Overrides locales y persistencia segura

La vista editable debe soportar:

- defaults del repo
- override local por máquina
- override por proyecto/workspace
- reset a defaults
- preview de diff antes de guardar
- rollback al último estado válido

Persistencia prevista:

- override runtime local y seguro
- nunca escribir directamente sobre `aiteam/config.py`

### D. Validaciones antes de guardar

La UI no debería permitir guardar una configuración inválida. Debe validar:

- que cada rol conserve al menos un primario y un fallback viable
- que no se deje al `team_lead` sin rutas soberanas
- que no se rompa el modo simulado
- que no se creen ciclos o prioridades imposibles
- que el coste/policy no contradiga restricciones globales

### E. Simulación y explicabilidad

La vista debe tener herramientas operativas, no solo formularios:

- simulación: "si ejecuto Engineer ahora, qué ruta elegiría"
- explicación: "por qué eligió este modelo y descartó estos otros"
- comparación entre catálogo efectivo y últimas runs reales
- indicadores de salud por provider y adapter
- visibilidad de blockers en lenguaje claro

### F. Gobierno económico y de calidad

La edición por rol también debe cubrir tuning operativo:

- preferir proveedores más baratos para roles no críticos
- reservar proveedores caros para `team_lead` o casos complejos
- distinguir rutas rápidas vs rutas de máxima calidad
- configurar defaults específicos para planning frente a coding

Esto es especialmente importante porque el objetivo de producto actual es:

- Anthropic casi exclusivo para `team_lead`
- resto de agentes priorizando alternativas más baratas y suficientes

## Fases recomendadas

### Fase 1. MVP consultable

Estado: completado.

### Fase 2. Hardening del catálogo

Antes de editar, conviene cerrar:

- payload estable
- blockers exhaustivos
- separación clara entre default, override y efectivo
- mayor detalle de capacidades/coste/canal
- explicación de la resolución del router

### Fase 3. Edición segura local

Primera edición real:

- orden de providers por rol
- orden de modelos por rol
- primario y fallbacks
- persistencia en override local
- validación previa
- reset a defaults

### Fase 4. Edición avanzada

Después:

- overrides por proyecto
- reglas por tipo de tarea
- restricciones por coste/capacidad
- simulador de routing
- diff entre política y runs reales

## Criterio de done de la vista completa

La vista estará realmente cerrada cuando permita, sin editar JSON a mano:

- saber qué usa cada rol
- saber qué podría usar pero está bloqueado
- entender por qué el router elige una ruta concreta
- cambiar primarios y fallbacks con seguridad
- persistir overrides locales válidos
- comprobar antes de guardar que no se rompe el sistema
- comparar configuración frente a uso real de las últimas runs

Mientras eso no exista, el MVP actual debe considerarse:

- útil
- valioso
- pero todavía incompleto como superficie de gobierno del routing
