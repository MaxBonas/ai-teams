# Hilo de proyecto: migración de autenticación

El usuario confirma que el corte debe ser reversible y sin downtime. Tras comparar alternativas,
se decide mantener JWT RS256 y rotar claves mediante doble validación durante 24 horas. La clave
antigua no puede borrarse hasta que la métrica `legacy_kid_hits` sea cero durante dos horas.

Engineer implementó el lector dual en `src/auth/keyring.py` y aporta `pytest tests/auth -q: 42 passed`.
Reviewer aprueba la compatibilidad, pero detecta que rollback aún no está automatizado. Se asigna a
Engineer como siguiente owner crear `scripts/rollback_keys.py`; Reviewer deberá aceptar su dry-run.

Riesgo alto: cachés regionales pueden conservar JWKS durante 15 minutos. Si aparecen más de 0,5 %
de respuestas 401 durante cinco minutos, se pausa la rotación y se escala al Lead. Queda fuera de
scope migrar sesiones existentes o cambiar el proveedor de identidad.

Después hubo varias conversaciones repetidas sobre nombres de variables, saludos, estimaciones ya
descartadas y fragmentos de logs sin relación con las decisiones anteriores. El equipo reiteró varias
veces que el objetivo era comprimir el hilo y conservar únicamente información causal y accionable.
