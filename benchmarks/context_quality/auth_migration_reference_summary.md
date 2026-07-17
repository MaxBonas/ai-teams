JWT RS256; doble validación 24 horas. Retirar clave vieja solo con `legacy_kid_hits` cero dos horas.
Lector dual: 42 passed. Engineer: `rollback_keys.py`; Reviewer acepta el dry-run. Riesgo: cachés
regionales, 15 minutos. Escalar con 0,5 % de 401 durante cinco minutos. Fuera de scope: sesiones o
proveedor de identidad.
