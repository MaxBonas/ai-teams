# Security And Compliance Policy (v0)

Este documento define los guardrails de cumplimiento para el AI Team local.

## 1) Aprobacion para operaciones sensibles

Se consideran sensibles comandos que incluyan patrones como:

- `publish`, `release`, `deploy`, `prod`, `production`
- `playstore` / `google play`
- `terraform apply`, `kubectl apply`, `docker push`
- `whatsapp`, `send message`

Si una tarea tiene esos pasos en `execution_plan`, queda bloqueada salvo que incluya:

```json
{
  "metadata": {
    "approved_sensitive_ops": true,
    "approved_by": ["lead-1"]
  }
}
```

En `prod` se exigen al menos **2 aprobadores** en `approved_by`.

## 2) Aprobacion para adapters sensibles

Los adapters externos pueden declararse con:

```json
{
  "requires_approval": true
}
```

En ese caso, la tarea debe incluir una de estas opciones:

- `approved_sensitive_ops: true`, o
- `approved_adapters: ["adapter_name"]`

Y en `prod`, adicionalmente `approved_by` con 2+ revisores.

## 3) Redaccion de secretos

Antes de persistir contexto operativo se aplica redaccion basica para:

- `api_key=...`
- `token: ...`
- `secret=...`
- `password=...`
- patrones comunes (`sk-...`, `ghp_...`)

Objetivo: evitar fuga de credenciales en memoria, mailbox y resumentes de ejecucion.

## 4) Trazabilidad de cumplimiento

Cuando se bloquea por compliance:

- la tarea pasa a `failed`
- se registra evento `compliance_violation`
- se envia notificacion a `team_lead`
- se dispara reunion por evento critico

## 5) Perfiles por entorno

La CLI soporta `--environment` (`dev|stage|prod`) para evolucionar reglas por entorno.
Valor por defecto: `dev` (o `AITEAM_ENV`).

## 6) Alcance actual y siguiente iteracion

Implementado en esta version:

- enforcement de aprobacion para comandos sensibles
- enforcement para adapters `requires_approval`
- redaccion de secretos comunes en contexto operativo
- workdirs permitidos para ejecucion: `Ai_Teams` y `Antigravity Projects` (root compartido)
- integracion automatica de tools (`cli|mcp|skill`) con trazabilidad en `runtime/tool_registry.json`

## 7) Zero-trust para MCP y tools de internet

- Toda fuente externa debe declararse en catalogo (`config/tool_sources.catalog.json`) o `tool_requirements`.
- En `prod`, herramientas con `uses_internet=true` deben pasar aprobacion sensible.
- Herramientas empresariales (`billing`, `messaging`, `database`) deben marcarse `requires_approval=true`.
- Cada integracion queda auditada como `tool_integration` / `tool_auto_discovery` en eventos.
- Adquisiciones opcionales fallidas se auto-desactivan (`enabled=false`) para no romper tareas.
- `mcp-doctor --enable-healthy` solo auto-habilita MCP no sensibles por defecto.
- Para auto-habilitar MCP sensibles se requiere `--enable-sensitive`.

Siguiente paso recomendado:

- ampliar detectores de PII y secretos por proveedor
- integrar scan de secretos como gate dedicado
- exigir doble aprobacion para acciones irreversibles en `prod`
