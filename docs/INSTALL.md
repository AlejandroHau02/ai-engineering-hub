# Instalación y upgrades del Hub (v0.2 en revisión)

Requiere **Python 3.10+**, un checkout local del repositorio y Claude Code o Codex instalados independientemente.

## Instalar por primera vez

```powershell
python scripts/sync.py --target both
python scripts/sync.py --target both --apply
```

El primer comando solo muestra el plan. El segundo escribe **únicamente tras tu autorización**, y hace copias de seguridad de los archivos que cambie. Puedes elegir `--target claude` o `--target codex`.

Ubicaciones globales: `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.claude/skills/` y `~/.agents/skills/`. El Hub no agrega archivos a los proyectos.

## Actualizar skills previamente instaladas con v0.1

La v0.2 incorpora mejoras de UX/UI, backend y revisión de código. El instalador original no podía actualizar las skills ya instaladas.

Con la **rama v0.2 revisada** y dentro de la raíz del repositorio:

1. Primero ejecuta el diagnóstico. No escribe nada:
   ```powershell
   python scripts/sync.py --target both --upgrade-skills
   ```
2. Examina qué aparece como `UPGRADE`, `UPDATE` o `CREATE`. Si no lo esperas, **no apliques**.
3. Cuando lo apruebes, escribe con respaldos:
   ```powershell
   python scripts/sync.py --target both --upgrade-skills --apply
   ```
4. Comprueba idempotencia:
   ```powershell
   python scripts/sync.py --target both
   ```
   Esperado cuando los archivos coinciden: `NO CHANGES`. Reinicia las sesiones de Claude/Codex para adoptar el contenido.

Sin `--upgrade-skills`, el instalador **rechaza** sobrescribir una skill existente que difiere. Incluso con esa opción, solo actualiza los archivos que coincidan exactamente con los baselines aprobados de v0.1: si editaste una skill localmente, se detiene sin sobrescribirla.

Las instrucciones fuera del bloque `<!-- ai-engineering-hub:start -->` / `<!-- ai-engineering-hub:end -->` se conservan. Las modificaciones **dentro** de ese bloque pertenecen al Hub y pueden ser reemplazadas: revisa el plan y los respaldos antes de aplicar.

## Añadir especialistas globales (opcional)

Tras revisar la rama de especialistas, inicia una **simulación sin escritura**:

```powershell
python scripts/sync.py --target both --specialists
```

En un equipo con v0.2 ya sincronizada, deben aparecer cuatro agentes nuevos de Claude Code y cuatro de Codex, además de actualizaciones de las instrucciones globales del Hub. Si los archivos gestionados son los esperados y no existen conflictos, la instalación requiere la autorización explícita:

```powershell
python scripts/sync.py --target both --specialists --apply
python scripts/sync.py --target both --specialists
```

La última ejecución debe responder `NO CHANGES`. Un agente existente con contenido diferente bloquea el proceso sin sobrescribirlo. Los cambios en configuraciones globales se respaldan. Los especialistas se instalan en `~/.claude/agents/` y `~/.codex/agents/`. No es necesario añadir archivos en cada proyecto.

**Límite de seguridad:** los subagentes de Codex heredan los permisos de la sesión principal y su instrucción de no editar no equivale a un sandbox de lectura. Lee [especialistas y alcance](../specialists/README.md) antes de delegar tareas sensibles.

## Simulaciones y pruebas aisladas

```powershell
python scripts/sync.py --target both --home ./scratch-home --upgrade-skills
python -m unittest discover -s tests -v
```

La primera línea es solo una simulación. La segunda ejecuta pruebas con directorios temporales independientes; no instala nada en tus directorios globales reales.

## Recuperación

Los archivos actualizados tienen respaldos `*.bak.<timestamp>` al lado del original. Puedes restaurar manualmente el respaldo apropiado tras inspeccionarlo. No borres reglas personales o directorios de skills que no sean del Hub.

La aplicación es segura por archivo, pero **no completamente transaccional** entre todos los archivos; si un error interrumpe el proceso, inspecciona el estado antes de reintentar.

## Política de seguridad

No se descargan skills de terceros, no se instalan paquetes y no se ejecutan comandos externos. Las actualizaciones requieren revisión humana. El repositorio es público y no debe almacenar secretos ni código privado.
