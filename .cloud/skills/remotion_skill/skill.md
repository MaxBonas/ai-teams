# remotion_skill

Buenas practicas de Remotion para MP4

## Objetivo
Usar esta skill cuando la tarea requiera este dominio.

## Capacidades
- multimodal
- rendering
- video_generation

## Guardrails
- No exponer secretos ni credenciales.
- Priorizar cambios pequenos, verificables y con evidencia.

## Pre-requisitos
- Verificar que las herramientas de CLI estén en el PATH.
- Asegurar que las variables de entorno asociadas estén configuradas.

## Pasos de Recuperación (Recovery)
1. En caso de fallo de red, aplicar reintentos con backoff exponencial.
2. Si hay errores de permisos, solicitar al usuario los accesos faltantes explícitamente.
3. En fallos de sintaxis o formato, validar la consistencia del input antes de reintentar.
