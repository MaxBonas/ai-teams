# Reconciliador de snapshots de inventario

Implementa una utilidad Python, sin dependencias externas, que compare dos
snapshots JSON de inventario y produzca un diff determinista y auditable.

Entregables:

- módulo `inventory_diff.py`;
- función `reconcile_inventory(previous: dict, current: dict) -> dict`;
- CLI `python inventory_diff.py previous.json current.json diff.json`;
- tests públicos propios.

Contrato de entrada:

- cada snapshot es un objeto con `items`, una lista de productos;
- cada producto contiene exactamente `sku`, `quantity`, `price_cents` y `tags`;
- `sku` es string no vacío; `quantity` y `price_cents` son enteros no negativos
  (los booleanos no cuentan como enteros);
- `tags` es una lista sin duplicados de strings no vacíos;
- SKU duplicados, campos ausentes/desconocidos o valores inválidos producen
  `ValueError`;
- la función no modifica ninguno de los dos argumentos.

Contrato del diff:

- devuelve `added`, `removed`, `changed`, `unchanged` y `summary`;
- todas las listas se ordenan por SKU ascendente;
- `added` contiene los productos actuales completos y `removed` los anteriores;
- cada entrada de `changed` contiene `sku` y `changes`; `changes` incluye solo
  los campos realmente distintos, con `before` y `after`;
- el orden de campos modificados es `quantity`, `price_cents`, `tags`;
- dos listas de tags con los mismos elementos en distinto orden son equivalentes;
  al emitir productos o cambios, los tags quedan ordenados;
- `unchanged` contiene solo los SKU sin cambios;
- `summary` contiene los contadores de las cuatro categorías y `quantity_delta`,
  calculado como suma de cantidades actuales menos anteriores para todo el
  inventario;
- llamadas repetidas producen el mismo resultado serializable.

Contrato CLI:

- lee y escribe UTF-8;
- emite JSON con indentación de dos espacios, claves ordenadas y newline final;
- ante argumentos, JSON o snapshot inválidos devuelve exit code distinto de cero;
- un fallo nunca crea ni deja parcialmente escrito el archivo de salida.

La tarea es media, acotada, reversible y de una sola rama. No requiere red,
persistencia ni verificación humana independiente.
