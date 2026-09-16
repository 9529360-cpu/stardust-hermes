# Stardust

> Un asistente personal de IA mantenido para uso propio y a largo plazo.

Stardust es un proyecto de asistente personal mantenido de forma independiente. Utiliza el código abierto de **Hermes Agent** como base técnica, pero la dirección del producto, la experiencia de escritorio, el enrutamiento de modelos, las actualizaciones y el mantenimiento pertenecen a este repositorio.

No es un espejo de Hermes ni sigue su ciclo de releases.

## Principios del proyecto

- **Mantenimiento independiente:** `9529360-cpu/stardust-hermes` es la autoridad de código y releases de este proyecto.
- **Sin sincronización automática con upstream:** no se realizan merges/rebases periódicos ni instalaciones desde el repositorio original.
- **Asistente personal primero:** funciones, interfaz y flujos de trabajo evolucionan para el uso personal a largo plazo.
- **Privacidad primero:** el repositorio no almacena credenciales reales, conversaciones, memorias, logs, bases de datos ni estado privado de ejecución.
- **Compatibilidad interna:** algunos paquetes, comandos o rutas pueden conservar el nombre `hermes` por compatibilidad histórica; eso no define la identidad actual del producto.

## Instalación

Usa únicamente los puntos de entrada de Stardust.

### Linux / macOS / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh | bash
```

### Windows PowerShell

```powershell
iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
```

No uses los instaladores oficiales de Hermes para instalar este proyecto: instalarían el código upstream, no Stardust.

## Límites del repositorio

Este repositorio contiene únicamente código fuente, pruebas y documentación. No se deben subir `.env`, claves API, tokens, claves privadas, configuración local, conversaciones, memorias, perfiles, logs, cachés, bases de datos ni builds personales empaquetados.

Consulta [`STARDUST.md`](STARDUST.md) para las reglas de mantenimiento y privacidad, y [`SECURITY.md`](SECURITY.md) para informar problemas de seguridad.

## Base técnica y licencia

Stardust se construyó originalmente sobre el código abierto de [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) y conserva su historial Git, avisos de copyright y licencia MIT.

**Hermes Agent es la base técnica y el origen del código; Stardust es el asistente personal que continúa desde esa base con mantenimiento y evolución independientes.**

Este proyecto no es una distribución oficial de Nous Research.
