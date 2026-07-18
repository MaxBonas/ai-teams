# Hilo de proyecto: rollout de facturación asíncrona

El usuario exige desplegar el nuevo consumidor sin duplicar facturas y con rollback en menos de diez
minutos. Se decide usar una clave de idempotencia compuesta por `invoice_id` y `attempt`, activar el
consumidor mediante feature flag solo para una cohorte inicial del 10 %, y mantener el consumidor
antiguo en shadow durante 48 horas.

La política de retry queda limitada a tres intentos; después el mensaje debe ir a la dead-letter queue
sin confirmarse como procesado. Platform ejecutó un replay de 10.000 mensajes y obtuvo cero facturas
duplicadas. El siguiente owner es Engineer, que debe crear `scripts/replay_dlq.py`; Reviewer aceptará
la transición únicamente tras reproducir un dry-run de 100 mensajes en staging.

Riesgo principal: webhooks fuera de orden pueden llegar hasta 20 minutos tarde y reabrir estados ya
cerrados. Si la tasa de facturas duplicadas supera 0,1 % durante diez minutos, SRE desactiva la feature
flag, vuelve al consumidor anterior y escala al Lead. Quedan fuera de alcance migrar de broker y
cambiar el esquema de pagos.

El resto del hilo contiene saludos, propuestas de nombres descartadas, estimaciones antiguas y bloques
de logs repetidos que no alteran las decisiones, los owners ni los criterios de aceptación anteriores.
