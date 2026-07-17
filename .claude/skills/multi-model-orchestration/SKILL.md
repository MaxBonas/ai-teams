---
name: multi-model-orchestration
description: "Diseñar, revisar o depurar orquestación avanzada de LLMs en AI Teams: routing, adapters, cascadas, hiring, quorum, context engineering, verificación, liveness, coste y benchmarks. Usar al tocar policies, provider_governor, hiring_economics, adapters, gates o evaluación multiagente."
---

# Orquestación multi-modelo

Usar `docs/ORCHESTRATION.md` como fuente canónica y leer `docs/ORCHESTRATION_SOURCES.md` cuando aparezcan cifras, papers o comportamiento actual de proveedores/SDKs.

## Procedimiento

1. Contrastar documentación con código y tests activos.
2. Clasificar la solución mínima: código determinista, agente único, especialista, handoff o equipo.
3. Definir contrato, evidencia, presupuesto, escalado y continuación durable.
4. Mantener invariantes en código y juicio abierto en el LLM.
5. Evaluar calidad, coste, liveness y coordinación contra un baseline simple.

No duplicar aquí patrones, cifras ni el mapa del repo. Actualizar los documentos canónicos y dejar esta skill como adaptador de descubrimiento para Claude Code.
