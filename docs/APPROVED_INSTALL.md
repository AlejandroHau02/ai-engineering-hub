# Instalación controlada de skills externas aprobadas — PR #10

**Estado:** primer incremento experimental de Skill Scout v0.3. El manifiesto `registry/approved-skills.json` sigue **vacío**: no hay ninguna skill aprobada o instalable por defecto.

## 1. Revisar y aprobar antes de utilizar

Sigue primero [Skill Scout](SKILL_SCOUT.md) y la [guía de aprobaciones](SKILL_REGISTRY.md). Para que una skill pueda instalarse deben constar, tras revisión humana y PR versionado: licencia aplicable, evidencia de aceptación de riesgos, compatibilidad evaluada para cada cliente y autorización explícita.

El registro es una **declaración**, no una garantía automática de seguridad o propiedad intelectual. No modifiques `approved-skills.json` para simular que un tercero fue evaluado.

## 2. Simulación sin escritura

Desde la raíz del checkout del Hub:

```powershell
python scripts/sync.py --target both --approved-skills
```

`--approved-skills` es una opción explícita; por defecto hace **dry-run**. Prepara las skills propias del Hub como siempre y, si el manifiesto tiene entradas, verifica el contenido remoto de cada skill aprobada contra el commit y hashes registrados. Después comprueba los posibles destinos en `~/.claude/skills/` y `~/.agents/skills/`.

Si la skill fue probada para Claude pero no para Codex (o viceversa), `--target both` se rechaza de forma conservadora. Selecciona solo un target con estado `tested`:

```powershell
python scripts/sync.py --target claude --approved-skills
```

Un origen modificado, URL inconsistente, hash distinto, symlink, directorio de skill preexistente sin `SKILL.md`, directorio con archivos adicionales o conflicto local bloquea el plan **antes de escribir**. Solo un directorio existente con exactamente el mismo `SKILL.md` (sin otros archivos) se trata como instalación ya satisfecha.

## 3. Aplicación explícita

Únicamente después de verificar personalmente la revisión y la simulación:

```powershell
python scripts/sync.py --target claude --approved-skills --apply
python scripts/sync.py --target claude --approved-skills
```

`--apply` es el único interruptor de escritura. Una segunda ejecución debe responder `NO CHANGES` si los destinos ya coinciden. El mecanismo utiliza respaldos `*.bak.<timestamp>` para archivos gestionados que deban actualizarse; una skill externa preexistente con bytes diferentes **nunca se sobrescribe**, ni siquiera con `--apply`.

**No ejecutes estos comandos de aplicación por una skill de terceros sin aprobación específica.** En este PR, el manifiesto está vacío; no se entregan aprobaciones ni se instala contenido de GitHub.

## Limitaciones explícitas

Este primer incremento permite solo skills de **un archivo `SKILL.md` sin archivos hermanos** en el mismo directorio del repositorio de origen. Bundles con scripts, bibliotecas o assets necesitan un siguiente desarrollo. Una referencia a un archivo remoto o una dependencia que no esté en ese directorio podría seguir existiendo: el propietario debe evaluarla.

Se exige UTF-8, LF, un nombre y descripción no ambiguos en el frontmatter, SHA del blob Git y SHA-256 del archivo. La descripción debe estar expresada en una línea; las variantes multilínea todavía no se admiten. Las solicitudes remotas están limitadas a GitHub y pueden fallar por cuota o falta de red; tales fallos bloquean la operación, no degradan a instalación sin verificar.

El instalador sigue siendo no transaccional entre archivos: una interrupción durante `--apply` puede dejar una instalación parcial. Revisa rutas y respaldos antes de reintentar; no borres contenido personal del usuario.

Ejecuta las pruebas locales con:

```powershell
python -m unittest discover -s tests -v
```
