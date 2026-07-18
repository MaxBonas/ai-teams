`invoice_id` + `attempt`; cohorte 10 % y shadow 48 h. Tres intentos y luego DLQ. Replay de 10.000
mensajes: cero duplicados. Engineer crea `replay_dlq.py`; Reviewer acepta tras dry-run de 100 mensajes.
Webhooks fuera de orden: hasta 20 minutos tarde. Con 0,1 % de duplicados durante diez minutos,
desactivar flag y escalar. Fuera de alcance: broker o esquema de pagos.
