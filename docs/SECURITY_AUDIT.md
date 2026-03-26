# Informe de Auditoría de Seguridad (AI Teams)

Este documento detalla los controles de seguridad y mitigaciones implementadas en la versión corporativa de **AI Teams**, asegurando un entorno Zero-Trust para la ejecución de LLMs y automatizaciones.

## 1. Sandboxing de Ejecutor de Comandos
El `LocalCommandExecutor` impone restricciones duras en los comandos enviados por el LLM.
* **Control de Directorio**: Los comandos son encarcelados a las raíces de directorios (`allowed_roots`) provistas al arrancar, previniendo path traversal y accesos arbitrarios al disco del host.
* **Entorno Sanitizado**: El nuevo mecanismo `AITEAM_SANDBOX_STRICT` elimina y purga secretos agresivamente de las variables de entorno inyectadas al proceso de Python/Comando. Palabras clave como `API_KEY`, `TOKEN`, `CREDENTIALS`, `AWS_SECRET` son suprimidas antes de hacer fork de un proceso `cmd` o `powershell`.
* **Lista Blanca / Lista Negra**: El `CommandPolicy` prohíbe sintácticamente patrones destructivos como `rm -rf`, `reg delete` o `format`.

## 2. Redacción Zero-Trust (ComplianceGuard)
El orquestador de AI Teams asume que el LLM y las herramientas de ejecución externas pueden generar fugas de datos.
* **Redacción de Tokens E2E**: El `ComplianceGuard.redact_text` aplica expresiones regulares implacables que detectan claves y tokens (OpenAI, GitHub, AWS, etc) en la salida del LLM para evitar exponer credenciales o filtrarlas a logs o interacciones secundarias.

## 3. Prevención de Context Poisoning (Prompt Injection)
La inyección de comandos desde memoria, páginas web bajadas con fetch, o bases de datos es un vector común.
* **Defensas Anti-Poisoning**: Todos los buffers externos (`peer_context`, `memory`, `tool_report`, `execution_output`) son forzados a través de `sanitize_context` antes de ser ensamblados en el Prompt.
* **Cadenas Defensivas**: La validación suprime cadenas del tipo *"ignore all previous instructions"*, *"system override"*, *"You are now"*, asegurando que el agente no sufra secuestros de contexto generados por actores externos.

## 4. Quality Gates y Strict Evidence Gates
* **Sin Diffs No Hay Éxito**: Las tareas no pasan a etapa "Review" o "QA" si el LLM no puede certificar alteraciones físicas en el código base (vía `git status`).
* **Erradicación de Placeholders**: Intervención inmediata si el LLM miente y devuelve contenido falso como *"Placeholder:"*, o *"Simulated Output"*, impidiendo que la IA se engañe a sí misma o al equipo.

## 5. Prevención de Riesgos Sistémicos (Tool Locking)
* Los agentes solo tienen acceso al hash congelado e implementaciones concretas de herramientas dictadas por `ToolLockManager`, evitando adquisición dinámica de herramientas potencialmente envenenadas de la red.

---
**Fecha de Realización:** 03 de Marzo de 2026
**Estado:** Controles Mitigados en Producción.
