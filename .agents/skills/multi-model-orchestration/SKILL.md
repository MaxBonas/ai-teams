---
name: multi-model-orchestration
description: Analizar, diseñar, revisar o depurar orquestación avanzada de LLMs y agentes en AI Teams. Usar con routing de modelos/proveedores, adapters, hiring, cascadas, quorum, context engineering, verificación independiente, gates, liveness, presupuestos, telemetría, benchmarks o decisiones entre código determinista, agente único, agents-as-tools, handoffs y equipos.
---

# Orquestación multi-modelo

Usar `docs/ORCHESTRATION.md` como fuente canónica. Leerla completamente antes de recomendar o implementar cambios de orquestación. Leer `docs/ORCHESTRATION_SOURCES.md` cuando aparezcan cifras, papers o comportamiento temporalmente inestable de proveedores, modelos, SDKs o APIs.

## Procedimiento

1. Contrastar documentación con código y tests activos.
2. Clasificar la solución mínima: código determinista, agente único, especialista, handoff o equipo.
3. Definir contrato, evidencia, presupuesto, escalado y continuación durable.
4. Mantener invariantes en código y juicio abierto en el LLM.
5. Evaluar calidad, coste, liveness y coordinación contra un baseline simple.

No duplicar aquí patrones, cifras ni mapa del repo. Actualizar los documentos canónicos y mantener esta skill como adaptador de descubrimiento para Codex.
