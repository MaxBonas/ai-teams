# Deployment wave planner

Implementa una utilidad Python que valide un manifiesto de servicios y produzca
un plan de despliegue determinista por oleadas.

Entregables:

- módulo `deployment_planner.py`;
- función `plan_deployment(manifest: dict) -> dict`;
- CLI `python deployment_planner.py manifest.json plan.json`;
- tests públicos propios.

Contrato del manifiesto:

- `max_parallel` es un entero positivo;
- `services` es una lista no vacía de objetos con `name`, `region`, `risk` y
  `depends_on`;
- `name` y `region` son strings no vacíos, `risk` es `low`, `medium` o `high`, y
  `depends_on` es una lista de nombres sin duplicados;
- nombres duplicados, dependencias inexistentes, auto-dependencias y ciclos
  producen `ValueError`.

Contrato del plan:

- devuelve `waves`, `rollback_order` y `critical_path_length`;
- una wave contiene `ordinal` (desde 1) y `services`;
- un servicio solo entra cuando todas sus dependencias están en waves anteriores;
- nunca hay más de `max_parallel` servicios por wave ni dos de la misma región;
- un servicio de riesgo `high` siempre ocupa una wave él solo;
- entre candidatos listos se prioriza `high`, después `medium`, después `low`, y
  por nombre ascendente dentro del mismo riesgo;
- el algoritmo llena cada wave de forma greedy siguiendo ese orden; candidatos
  que no caben esperan a la siguiente wave;
- `rollback_order` invierte exactamente el orden plano de despliegue;
- `critical_path_length` es el máximo número de servicios de una cadena de
  dependencias (un servicio sin dependencias tiene longitud 1);
- el input no se modifica y llamadas repetidas producen el mismo resultado.

La CLI lee y escribe UTF-8, emite JSON con indentación de dos espacios y newline,
y ante argumentos, JSON o manifiesto inválidos devuelve exit code distinto de
cero sin dejar un archivo de salida parcial.

La tarea tiene grafo, política de scheduling, integración CLI y verificación
independiente. Es reversible, pero contiene varias superficies de trabajo.
