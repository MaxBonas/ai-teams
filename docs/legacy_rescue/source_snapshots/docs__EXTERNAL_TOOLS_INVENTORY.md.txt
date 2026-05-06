# Inventario inicial de herramientas externas

Objetivo: registrar programas de `Antigravity Projects` que pueden aportar capacidad adicional al AI Team, manteniendolos como soporte secundario.

## Regla operativa

- Prioridad por defecto: `secondary`.
- Solo promover a `primary` cuando demuestren utilidad estable en tareas reales.
- Integrar via `runtime/adapters.json` con `enabled` controlado por herramienta.
- Para herramientas sensibles, usar `requires_approval: true`.

## Herramientas identificadas

1. `secretariawhatsapp`
   - Uso potencial: lectura, gestion y respuesta de WhatsApp; gestion de base de conocimiento asociada.
   - Rol sugerido: `researcher` (recopilar contexto) o `team_lead` (coordinacion operacional).
   - Riesgos: datos sensibles, privacidad, politicas de mensajeria.
   - Estado sugerido: `enabled: false` hasta definir compliance.

2. Publicador automatico Play Store
   - Uso potencial: empaquetado/publicacion de apps Android.
   - Rol sugerido: `engineer` en fase de release.
   - Riesgos: cambios irreversibles en distribucion, credenciales de tienda.
   - Estado sugerido: `enabled: false` hasta agregar aprobacion humana.

3. Runner/auditor Android en navegador
   - Uso potencial: ejecucion y auditoria funcional de apps Android en flujo QA.
   - Rol sugerido: `qa`.
   - Riesgos: cobertura parcial respecto a dispositivo real, costo de ejecucion.
   - Estado sugerido: `enabled: true` en modo experimental, `priority: secondary`.

4. Editor de video IA con Remotion (`VideoGenerator`)
   - Uso potencial: render de piezas de video, variaciones creativas y pruebas multimodales.
   - Rol sugerido: `engineer` (pipeline de render) + `researcher` (experimentacion creativa).
   - Riesgos: tiempos de render/costo de GPU local, drift de prompts visuales.
   - Estado sugerido: `enabled: false`, `priority: secondary` hasta definir casos de uso.

## Inventario automatico

Puedes generar inventario de herramientas detectadas en `Antigravity Projects` con:

`python -m aiteam.cli inventory-tools --catalog-root "C:\Users\Max\Antigravity Projects" --inventory-output runtime/tool_inventory.json`

Esto crea un JSON con sugerencias de adapters, capacidades y riesgo/aprobacion.

## Criterios para priorizar estas herramientas

- Impacto: reduce tiempo real de entrega en tareas frecuentes.
- Fiabilidad: tasa de exito sostenida sin intervencion manual.
- Seguridad: cumple politicas de secretos y auditoria.
- Costo: mejora o mantiene el costo por tarea.

## Siguiente accion recomendada

- Integrar 1 herramienta en modo `secondary` y medir durante 1 sprint:
  - `% de tareas asistidas por herramienta`
  - `tasa de fallo`
  - `tiempo ahorrado`
  - `incidentes de seguridad/compliance`
