# Política de seguridad de Stardust

Stardust es un asistente personal de IA mantenido de forma independiente sobre la base de código de Hermes Agent. La responsabilidad de seguridad de este repositorio pertenece a `9529360-cpu/stardust-hermes`.

## Reportar una vulnerabilidad

Reporta vulnerabilidades de forma privada mediante GitHub Security Advisories de este repositorio:

https://github.com/9529360-cpu/stardust-hermes/security/advisories/new

No envíes vulnerabilidades de Stardust a Nous Research y no abras un issue público para una vulnerabilidad que todavía no haya sido corregida.

Un informe útil debería incluir el commit afectado, el componente o archivo, el entorno, pasos de reproducción, el límite de seguridad esperado y el impacto observado. No incluyas credenciales reales ni datos privados del usuario.

Stardust no opera actualmente un programa de recompensas por errores.

## Modelo de seguridad

Stardust puede tener acceso intencionado a terminales, archivos, navegadores, servidores MCP, plugins, servicios de mensajería y credenciales. Esas capacidades deben tratarse como privilegiadas.

- El aislamiento a nivel de sistema operativo o contenedor es el límite de contención principal frente a salida adversarial del modelo.
- Las superficies expuestas a la red deben autenticar o limitar explícitamente a los llamadores autorizados.
- Los plugins y skills deben tratarse como código ejecutable con los privilegios del agente.
- Las credenciales, conversaciones, memorias, registros, bases de datos y configuración privada deben permanecer fuera del checkout Git.
- El contenido del renderer de escritorio tiene menos confianza que el proceso principal de Electron; las operaciones privilegiadas deben pasar por IPC validado y limitado.
- Las rutas de instalación, reparación, actualización y release son límites de cadena de suministro. El código de producto de Stardust debe resolverse desde `9529360-cpu/stardust-hermes`, no volver silenciosamente al repositorio original de Hermes.

## Alcance

Entre los problemas que deben notificarse de forma privada están los escapes de aislamiento documentado, acceso no autorizado a superficies externas, fugas de credenciales o tokens, escaladas desde contenido web/renderers hacia APIs privilegiadas y rutas de instalación o actualización que puedan sustituir inesperadamente Stardust por código de otro repositorio.

La inyección de prompts por sí sola, los bypasses de heurísticas internas de redacción/aprobación y el comportamiento expresamente permitido por el modo de aislamiento seleccionado no constituyen automáticamente una vulnerabilidad sin un cruce adicional de un límite de seguridad.

Consulta [`SECURITY.md`](SECURITY.md) para la política canónica y los detalles de hardening.
