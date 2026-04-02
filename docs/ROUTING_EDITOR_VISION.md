# Visión objetivo — vista perfecta de configuración de roles, providers y modelos

Fecha: `2026-04-02`
Estado: `visión objetivo de producto`
Relacionado con:

- `docs/ROUTING_CATALOG_VIEW.md`
- `docs/ARCHITECTURE_PLAN.md` (`B7`)
- `docs/LEAD_QUORUM_PROJECT_CONTEXT_VISION.md`

## Propósito

La vista de routing no debe ser solo un panel técnico.

Debe convertirse en la superficie principal desde la que un operador pueda:

- entender cómo decide el router
- ver qué está pasando realmente en esta máquina
- editar la política sin tocar JSON a mano
- controlar coste, calidad, soberanía del Lead y fallback
- simular el impacto de un cambio antes de guardarlo

La meta es que esta vista sea el **centro de control multimodelo** del sistema.

## Estado actual del MVP editable

El trabajo ya no está solo en vision:

- el backend de overrides locales ya existe
- la pestaña `Routing` ya tiene un primer `modo edición local`
- ese modo permite guardar y resetear overrides por rol contra la API real

Todavía no existe la version "perfecta" descrita en este documento:

- no hay drag & drop de prioridades
- no hay simulador de impacto antes de guardar
- no hay diff estructurado entre pending/defaults/effective
- no hay historial ni rollback con snapshots

## Qué problema resuelve

Hoy la verdad está repartida entre:

- policy por defecto
- model catalog
- adapters efectivos
- role targets
- provider ops
- estado local de la máquina
- historial real de runs

Eso hace difícil responder preguntas básicas:

- qué usa realmente `engineer`
- por qué `qa` no puede usar un modelo concreto
- qué fallback usaría `reviewer` si falla OpenAI
- por qué el Lead terminó en Claude
- qué cambiar para bajar coste sin romper calidad

La vista perfecta debe resolver eso de forma operativa y segura.

## Principios de diseño

### 1. Una sola superficie de verdad operativa

La vista debe reunir en un mismo lugar:

- defaults del repo
- overrides locales
- overrides por proyecto
- estado efectivo de esta máquina
- últimas decisiones reales del router

### 2. Explicabilidad antes que magia

No basta con mostrar "primario" y "fallback".

Debe explicar:

- por qué ese modelo es elegible
- por qué otro quedó descartado
- qué política lo empujó
- qué estado operativo de provider/adapter influyó

### 3. Edición segura

Nunca debe permitir guardar una policy que:

- deje un rol sin ruta viable
- rompa el modo simulado
- quite al Lead rutas soberanas
- contradiga restricciones globales

### 4. Diferencia clara entre lectura y edición

La vista debe tener dos modos visibles:

- `inspección`
- `edición`

No conviene mezclar ambos hasta que la UI haga evidente qué es diagnóstico y qué es cambio persistente.

### 5. Local-first y reversible

La política editable no debe escribir sobre código fuente.

Debe operar sobre:

- override local por máquina
- override por proyecto
- reset a defaults del repo

Y cada cambio debe ser reversible.

## Usuarios objetivo

### Operador técnico / desarrollador del sistema

Necesita:

- entender el comportamiento del router
- ajustar coste/calidad
- depurar por qué una ruta falló

### Usuario del producto en proyecto externo

Necesita:

- entender qué roles usarán qué modelos
- controlar gasto y fallback
- adaptar el sistema al tipo de proyecto sin editar configuración a mano

### Mantenedor del sistema en dos máquinas

Necesita:

- que los cambios locales no destruyan el entorno de la otra máquina
- que el estado editable sea portable cuando toca y local cuando debe serlo

## Funciones que la vista perfecta debe tener

## Topologia del Lead y planning avanzado

La vista no debe limitarse a asignar modelos por rol simple.

Tambien debe poder gobernar la **topologia del Team Lead**:

- `Lead solo`
- `Lead + consultores`
- `Plan`
- `Plan/Quorum`

Eso implica poder configurar:

- modelo principal del Lead
- consultores avanzados
- en qué modos se activa el quorum
- presupuesto y limites de esa deliberación previa

La vision completa de esa capacidad vive en:

- `docs/LEAD_QUORUM_PROJECT_CONTEXT_VISION.md`

## A. Catálogo consultable completo

Debe mostrar:

- todos los roles
- todos los providers
- todos los models/adapters disponibles
- canal (`subscription`, `api`, `local`)
- tier
- coste relativo
- capacidades (`tools`, `stream`, `vision`, `thinking`, `long_context`, etc.)
- estado de salud
- role targets

## B. Matriz por rol

Para cada rol debe verse:

- providers en orden
- modelos en orden
- primario efectivo
- cadena completa de fallbacks
- estado de cada candidate
- gaps entre configurado y efectivo

Roles mínimos:

- `team_lead`
- `researcher`
- `engineer`
- `reviewer`
- `qa`
- `scout`

## C. Configuración por tipo de tarea

No basta con configurar por rol abstracto.

La vista perfecta debe permitir reglas distintas para:

- `lead_intake`
- `planning`
- `probe`
- `research`
- `engineering`
- `review`
- `qa`
- `delegate`
- `build`

Porque el mismo rol no necesita el mismo modelo en todos los contextos.

## D. Edición por rol

La edición mínima correcta debe permitir:

- reordenar providers
- reordenar modelos
- fijar primario
- fijar fallback 1, fallback 2, fallback 3...
- excluir un provider para un rol
- excluir un modelo para un rol
- limitar un modelo a un rol

## E. Edición por capacidad y coste

También debe poderse configurar:

- coste máximo permitido por rol
- canales permitidos
- capacidades mínimas requeridas
- si se permite o no `thinking`
- si se permite o no `vision`
- si se requieren `tools`
- si se permite `local` como fallback

## F. Simulación

Debe existir un simulador claro:

- "si lanzo `engineer` ahora en este proyecto, qué elegiría"
- "si falla OpenAI, qué fallback usaría"
- "si activo strict policy, qué se quedaría fuera"

El simulador debe mostrar:

- ruta elegida
- candidatos descartados
- razón de descarte

## G. Comparación con uso real

La vista ideal debe cruzar:

- política actual
- últimas runs reales

Y permitir responder:

- qué modelos se configuraron
- cuáles se usaron realmente
- dónde hubo fallback inesperado
- dónde hubo desvíos por salud, cuota o indisponibilidad

## H. Overrides por ámbito

Debe soportar tres capas:

- `defaults del repo`
- `override local por máquina`
- `override por proyecto`

Y mostrar siempre de dónde viene cada valor.

## I. Preview y diff antes de guardar

Antes de persistir un cambio, la UI debe mostrar:

- qué cambia
- en qué capa se guarda
- qué roles quedan afectados
- si cambia primario o fallback
- si aumenta riesgo/coste

## J. Reset y rollback

La vista perfecta debe permitir:

- reset a defaults del repo
- reset del override local
- reset del override del proyecto
- rollback al último estado válido guardado

## K. Alertas operativas

La vista debe alertar de:

- provider caído
- adapter no disponible
- fallback agotado
- rol sin ruta segura
- coste excesivo
- policy incoherente

## L. Export / import

Idealmente debería soportar:

- export de la policy activa
- import controlado de una policy
- clonado de configuración entre proyectos

Siempre con validación previa.

## Cómo debería estar organizada

## 1. Cabecera

Debe mostrar:

- workspace/proyecto actual
- capa activa (`repo`, `local`, `proyecto`)
- si hay cambios sin guardar
- botón de simular
- botón de guardar
- botón de reset

## 2. Panel lateral de roles

Lista de roles con:

- primario actual
- health general
- si tienen warnings
- badge de cambios pendientes

## 3. Panel central de edición

Debe tener pestañas o bloques:

- `Resumen`
- `Providers`
- `Modelos`
- `Fallbacks`
- `Capacidades`
- `Coste`
- `Simulación`
- `Uso real`

## 4. Panel de diagnóstico

Siempre visible o desplegable:

- blockers
- razones de descarte
- estado de providers
- conflictos de policy

## Estética y UX

## Qué debería transmitir

La vista debe sentirse:

- técnica
- clara
- fiable
- operativa

No debe parecer un panel genérico de settings.

Debe parecer un panel de control de routing real.

## Estética recomendada

- layout denso pero legible
- jerarquía clara entre `configurado`, `efectivo` y `usado`
- badges compactos y consistentes
- colores semánticos:
  - verde para elegible/operativo
  - ámbar para fallback/degradado
  - rojo para inválido/bloqueado
  - azul o neutro para preferido/configurado
- muy poco texto ornamental
- foco en tablas, matrices, explicaciones breves y comparaciones

## Interacciones deseables

- drag and drop para prioridad de providers/modelos
- toggles claros para permitir/vetar
- simulación inline
- diff antes de guardar
- filtros rápidos
- búsqueda instantánea

## Errores de producto que debe evitar

### 1. UI engañosa

No puede mostrar como disponible algo que no es realmente elegible en esta máquina.

### 2. Edición destructiva silenciosa

No debe guardar cambios sin preview ni validación.

### 3. Mezclar defaults con overrides sin explicarlo

Siempre debe saberse:

- qué viene del repo
- qué viene del local
- qué viene del proyecto

### 4. Dejar roles sin fallback

La UI debe bloquear esa posibilidad.

### 5. Esconder blockers críticos

Un adapter bloqueado debe mostrar el motivo claro, no solo una etiqueta vaga.

### 6. Ignorar el coste

La vista debe hacer visible si una policy empuja demasiados roles a modelos caros.

### 7. Ignorar el uso real

No debe convertirse en un formulario desconectado de lo que realmente hace el sistema.

### 8. Hacer editable demasiado pronto sin hardening

Antes de abrir la edición completa, el payload del catálogo tiene que ser estable y explicable.

## Persistencia correcta

La persistencia ideal debe cumplir:

- no escribir en `aiteam/config.py`
- no depender de editar `*.example.json`
- usar overrides locales seguros
- permitir override por proyecto
- ser compatible con dos máquinas

Dirección recomendada:

- default en código/repo
- override local por máquina en runtime del sistema
- override por proyecto en el estado del proyecto

## Compatibilidad entre máquinas

La vista perfecta debe respetar el modelo operativo del repo:

- Git comparte código y plantillas
- cada máquina mantiene su propio runtime local

Por tanto:

- los overrides locales no deben forzarse por Git
- los defaults sí deben viajar por Git
- la UI debe dejar claro cuándo estás tocando algo local y cuándo algo del repo

## Qué debería quedar fuera

No debería intentar, al menos en la primera fase completa:

- editar directamente el catálogo base del repo desde la UI
- ocultar completamente la complejidad del routing
- tomar decisiones opacas sin explicabilidad

## Criterio de “vista perfecta”

La vista estaría cerca de ser perfecta cuando un usuario pueda:

1. ver qué usa cada rol y por qué
2. entender qué está bloqueado y por qué
3. cambiar providers y modelos por rol sin tocar archivos manualmente
4. definir primario y fallbacks con seguridad
5. limitar coste y capacidades
6. simular el resultado antes de guardar
7. comparar la política con el uso real de las últimas runs
8. guardar overrides locales o por proyecto sin romper la otra máquina
9. revertir cambios fácilmente

## Definición de done de producto

La vista puede considerarse realmente cerrada cuando:

- sea fuente de verdad operativa del routing
- sea editable con seguridad
- sea explicable
- sea útil para coste y calidad
- y sustituya la necesidad de tocar JSON a mano en el flujo normal

Hasta entonces, cualquier versión anterior debe tratarse como:

- una fase útil
- pero incompleta
