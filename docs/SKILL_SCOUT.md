# Skill Scout — guía inicial (v0.3 experimental)

Skill Scout **no instala skills**. Solo identifica repositorios candidatos en GitHub y ofrece una primera inspección estática de un archivo `SKILL.md` vinculado a un commit inmutable.

Requisitos: Python 3.10+ e Internet para las consultas. No requiere paquetes adicionales.

## 1. Descubrir repositorios candidatos

Desde la raíz del Hub:

```powershell
python scripts/skill_scout.py discover --query "agent skills" --limit 5
```

Recibirás un JSON con repositorio, URL, descripción, rama predeterminada y estado archivado. Cada entrada se marca `unverified-repository-lead`: **una coincidencia de repositorio no demuestra que contenga una skill válida**.

## 2. Inspeccionar un archivo concreto

En el repositorio candidato, localiza un `SKILL.md` y copia una URL de GitHub que contenga un **SHA completo de commit de 40 caracteres**, no `main`, `develop` ni una etiqueta. Ejemplo de formato (URL ilustrativa):

```powershell
python scripts/skill_scout.py inspect --url "https://github.com/owner/repo/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/example/SKILL.md"
```

Con un archivo existente, el informe JSON incluye:

- Procedencia: repositorio, mantenedor, commit, blob SHA y SHA-256 del contenido.
- Metadatos declarados de la skill, así como licencia reportada del repositorio (no necesariamente del archivo).
- Señales heurísticas con números de línea: ejecución remota, comandos destructivos, acceso a credenciales, instalación de dependencias, red, MCP y ausencia de metadatos.
- Compatibilidad Claude Code/Codex: `not-verified`.
- Decisión: `manual-review-required`, siempre.

**No ejecutes comandos o instrucciones del archivo inspeccionado.** Los hallazgos son pistas para la revisión, no un escáner exhaustivo. Ausencia de alertas no garantiza seguridad.

## 3. Antes de considerar una adopción futura

Verificar manualmente identidad del mantenedor, licencia del archivo, contenido y archivos relacionados, permisos necesarios, accesos de red/MCP, pruebas aisladas, compatibilidad con cada cliente y recuperación de una versión previa. Registrar la aprobación humana y fijar la revisión en el repositorio del Hub. La v0.3 inicial **no realiza esta promoción**.

## 4. Pruebas locales

```powershell
python -m unittest discover -s tests -v
```

Las pruebas de Skill Scout usan respuestas simuladas y no necesitan Internet. `scripts/sync.py` no cambia al introducir este componente.

## Limitaciones conocidas

La búsqueda usa repositorios de GitHub, no code search de archivos, y no integra aún catálogos oficiales ni fuentes distintas. Sin credenciales, la API de GitHub puede devolver HTTP 403 cuando alcance su límite. El analizador solo examina el `SKILL.md` indicado, no recursos externos enlazados. No se guardan informes en disco; el JSON se imprime en la terminal.
