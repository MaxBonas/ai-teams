# Redactor reversible de configuración

Implementa una utilidad Python pequeña y autocontenida para publicar copias
seguras de archivos de configuración JSON.

Contrato público:

- módulo `config_redactor.py`;
- función `redact_config(value)` que devuelve una copia profunda sin modificar
  la entrada;
- redacta con `"***"` valores cuyas claves sean `api_key`, `token`, `secret`,
  `password` o terminen en `_key`, sin distinguir mayúsculas/minúsculas;
- recorre diccionarios y listas anidados;
- conserva tipos y valores no secretos;
- CLI `python config_redactor.py entrada.json salida.json` con UTF-8, JSON
  legible y código de salida distinto de cero ante JSON inválido.

Incluye tests públicos y un README breve. Es un cambio acotado, reversible y de
una sola rama; no necesita arquitectura distribuida ni revisión independiente.
