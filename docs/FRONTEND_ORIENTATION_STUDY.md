# Estudio formativo de orientación — protocolo prerregistrado v1

Estado: `preregistered_no_sessions_observed`

Congelado: `2026-07-22`

Contrato machine-readable:
`../benchmarks/frontend_orientation/orientation-study-prereg-v1.json`

## Propósito y límite

Este estudio comprueba si personas nuevas en esta versión del cockpit pueden:

1. encontrar la Bandeja y reconocer que contiene decisiones pendientes;
2. elegir conscientemente `solo_lead`, `lead_quorum` y `full_team` a partir de
   coste operativo y riesgo;
3. convertir un plan aceptado en una tarea de ejecución que conserva la
   referencia al plan.

Es un estudio formativo pequeño. Puede abrir o bloquear la siguiente iteración
de interfaz, pero no estima adopción de mercado, satisfacción general,
productividad, retención ni causalidad.

## Muestra congelada

- Ocho sesiones completadas; se pueden reclutar hasta diez personas solo para
  sustituir retiradas o fallos técnicos ocurridos antes de completar la primera
  tarea.
- Cuatro participantes usan herramientas de programación con agentes como
  máximo una vez al mes; cuatro las usan al menos una vez por semana.
- Todos deben ser nuevos en la UI de orientación que se evalúa. Haber visto el
  prototipo, el E2E o este protocolo excluye la sesión.
- No se excluye a nadie por bajo rendimiento. Retirada, fallo técnico y ruptura
  del protocolo se conservan como conteos con una categoría cerrada, nunca como
  texto libre.

Los códigos `P01`–`P08` existen solo en la hoja del observador y no se guardan
en SQLite. El orden de los tres bloques está congelado y aproximadamente
contrabalanceado:

| Código | Orden |
|---|---|
| P01 | Bandeja → Perfiles → Plan |
| P02 | Bandeja → Plan → Perfiles |
| P03 | Perfiles → Bandeja → Plan |
| P04 | Perfiles → Plan → Bandeja |
| P05 | Plan → Bandeja → Perfiles |
| P06 | Plan → Perfiles → Bandeja |
| P07 | Bandeja → Perfiles → Plan |
| P08 | Perfiles → Plan → Bandeja |

## Consentimiento y privacidad

El moderador lee, sin resumir:

> Esta prueba registra únicamente pasos dentro de tres flujos de orientación:
> Bandeja, selección de perfil y plan aceptado a tarea. Los datos permanecen en
> la SQLite local del proyecto. No se guardan títulos, prompts, rutas, issues,
> texto, audio ni vídeo. Puedes no participar, revocar el consentimiento o
> borrar las medidas en cualquier momento sin consecuencia.

La persona activa por sí misma `Config → Privacidad y medición`. No se inicia la
primera tarea hasta que confirme verbalmente que entiende almacenamiento,
campos y derecho de borrado. Si no consiente, la sesión no empieza y no se
reemplaza por observación encubierta.

## Preparación y moderación

- Proyecto desechable con el mismo fixture para todos, una decisión pendiente y
  un plan aceptado. Ventana de escritorio de al menos 1280×720.
- Estado inicial: Chat abierto, ninguna tarea de estudio iniciada y medición
  apagada. Cada bloque restablece Chat sin explicar dónde está el objetivo.
- El moderador puede repetir literalmente el enunciado, pero no señalar
  controles, interpretar etiquetas ni confirmar una selección antes de que la
  persona la dé por final.
- Tiempo máximo: tres minutos para Bandeja, seis para los tres escenarios de
  perfil y tres para Plan. Superarlo o rendirse cuenta como abandono.
- Pensar en voz alta es opcional. La rúbrica guarda categorías y puntuaciones,
  no transcripciones ni citas.

## Tareas y verdad de referencia

### A. Bandeja

Enunciado: «El equipo dice que necesita una decisión tuya. Encuentra dónde está
y abre la superficie desde la que responderías».

Éxito: abre Bandeja e identifica que contiene la decisión pendiente, sin ayuda.
Camino mínimo: una acción. Gate de acciones: mediana ≤ 2.

### B. Selección consciente de perfiles

Se presentan los tres escenarios en orden latino según `Pxx mod 3`:

1. Cambio pequeño, reversible y sin necesidad de revisión independiente →
   `solo_lead`.
2. Autorización multi-tenant ambigua y crítica; primero se quiere auditar el
   plan con seniors independientes → `lead_quorum`.
3. Plan aceptado que debe implementarse con ejecución y revisión separadas →
   `full_team`.

Para cada escenario la persona selecciona un perfil y explica una consecuencia
de coste operativo y una de riesgo. El observador puntúa cada explicación como
correcta/incorrecta usando solo la guía visible. No se conserva el texto.

Éxito de selección: 3/3 perfiles correctos. Comprensión individual: al menos
5/6 consecuencias correctas y ninguna inversión peligrosa, definida como
afirmar que `solo_lead` aporta revisión independiente o que `lead_quorum`
implementa por sí mismo el plan.

### C. Plan aceptado → tarea

Enunciado: «La planificación ha terminado. Crea la tarea que ejecutará este plan
sin copiar manualmente su contenido».

Éxito: usa el CTA del plan, conserva `Plan aceptado adjunto`, mantiene
`full_team` y crea la tarea. Camino mínimo desde Plan: dos acciones; gate de
acciones desde Chat restablecido: mediana ≤ 3.

## Métricas y codificación

- `completed`: satisface el criterio sin pista.
- `actions`: clics o activaciones de teclado dirigidos a controles; scroll,
  lectura y foco no cuentan.
- `unnecessary_actions`: acciones fuera de cualquier camino válido que deben
  deshacerse o corregirse.
- `ui_error`: fallo visible o respuesta HTTP fallida provocado por el producto.
- `abandoned`: rendición explícita o tiempo máximo.
- `assisted`: el moderador dio una pista de navegación; se conserva pero no
  cuenta como completado sin ayuda.
- `profile_choices_correct`: 0–3.
- `cost_risk_statements_correct`: 0–6.
- `dangerous_misconception`: booleano.

La hoja del observador contiene solo código, estrato, orden y estas columnas
cerradas. El recibo final agrega conteos y medianas; no publica filas
individuales.

## Gates congelados

El estudio abre una expansión frontend pequeña solo si se cumplen todos:

- ≥ 7/8 completan Bandeja sin ayuda y su mediana de acciones es ≤ 2;
- ≥ 7/8 completan Plan → tarea sin ayuda y su mediana es ≤ 3;
- ≥ 6/8 aciertan los tres perfiles y superan la rúbrica de comprensión;
- como máximo una persona presenta una inversión peligrosa;
- como máximo una persona abandona cada bloque;
- cero `ui_error`, errores de navegador o violaciones del esquema privado;
- el auditor técnico confirma que los eventos solo contienen `flow`, `event` y
  `profile` opcional.

Estados de decisión:

- `ready_for_bounded_frontend_expansion`: todos los gates pasan;
- `iterate_and_repeat_with_new_preregistration`: falla cualquier gate de uso;
- `privacy_stop_delete_and_investigate`: aparece contenido no allowlisted o se
  registra sin consentimiento;
- `inconclusive_technical_failure`: no se alcanzan ocho sesiones válidas por
  fallos técnicos ajenos al flujo.

Pasar no permite afirmar adopción, claridad universal ni mejora causal. Fallar
no autoriza cambiar umbrales: se corrige la UI y se crea un protocolo v2 antes
de observar otra muestra.

## Reglas de parada y recibo

Una violación de consentimiento, transmisión externa o payload con contenido
detiene el estudio inmediatamente. Se revoca la medición, se borran los datos
afectados y se registra solo la categoría `privacy_stop`.

El recibo final debe referenciar commit y versión de este protocolo, declarar
reclutadas/completadas/excluidas, reportar todos los gates y mantener
`constructs_not_measured`. Los resultados no se añadirán a este archivo.
