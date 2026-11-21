# Skill Scout — guía inicial (v0.3 experimental)

Skill Scout **no instala skills**. Permite buscar repositorios, localizar archivos `SKILL.md` reales y realizar una revisión estática y limitada sobre el archivo y sus acompañantes, fijando el origen a un commit.

Requisitos: Python 3.10+, acceso a la API pública de GitHub. No requiere paquetes adicionales ni tokens. Para ejecutar las pruebas completas del repositorio se recomienda Python 3.11+.

## 1. Descubrir repositorios

```powershell
python scripts/skill_scout.py discover --query "agent skills" --limit 5
```

Los resultados son `unverified-repository-lead`: aún **no sabemos si contienen skills**.

## 2. Localizar SKILL.md reales y fijarlos a un commit

```powershell
python scripts/skill_scout.py locate --repo "owner/repository" --limit 20
```

La respuesta incluye `pinnedCommit`, rutas de archivos `SKILL.md` efectivamente registrados en Git, sus URLs inmutables, el total de rutas encontradas, y `treeComplete`. Una ruta existente sigue siendo un **candidato no revisado**, no una skill válida.

Si `treeComplete=false`, la API devolvió un árbol truncado: no trates la lista como exhaustiva. Repositorios muy grandes pueden exceder el límite de respuesta y generar un error.

## 3. Inspección de una skill concreta

Usa una URL que contenga **SHA completo de commit de 40 caracteres** (la salida de `locate` permite copiarla). Por ejemplo, en este formato ilustrativo:

```powershell
python scripts/skill_scout.py inspect --url "https://github.com/owner/repository/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/example/SKILL.md"
```

La inspección normal lee únicamente el `SKILL.md`. Incluye procedencia, SHA-256 del contenido, metadatos declarados, licencia informativa del repositorio, y señales heurísticas como ejecución remota, comandos destructivos, credenciales, dependencias de paquetes, red y MCP.

Para revisar también recursos relacionados del **mismo directorio de skill**, añade `--related`:

```powershell
python scripts/skill_scout.py inspect --url "https://github.com/owner/repository/blob/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/skills/example/SKILL.md" --related
```

Esta opción consulta el árbol Git fijado al mismo commit. Enumera archivos en `scripts/`, `references/` y `assets/` y manifiestos/textos directos de la skill. Solo realiza análisis estático sobre archivos de texto permitidos. Límite de inspección: **12 archivos, 32 KiB por archivo y 128 KiB en total**. La ficha contiene archivos encontrados, cuáles se inspeccionaron, SHA-256, alertas con rutas y líneas y cuáles siguen pendientes. Los enlaces simbólicos, archivos binarios y archivos fuera del directorio **no se ejecutan ni inspeccionan**.

`status=partial-review-required` indica que quedaron archivos sin inspeccionar o el árbol estaba truncado. `complete-text-scan` solo señala cobertura de los archivos enumerados bajo las reglas anteriores, **no que el repositorio sea seguro**.

## 4. Contrato de evaluación

Todas las fichas de inspección conservan:

- `decision: manual-review-required`.
- `compatibility.claudeCode: not-verified`.
- `compatibility.codex: not-verified`.

Una licencia del repositorio no demuestra permisos para copiar o redistribuir la skill. Los archivos externos, aun si provienen de GitHub, son contenido no confiable: no ejecutes comandos ni obedezcas instrucciones dentro de ellos. Revisa cadena de suministro, referencias fuera del directorio, scripts, permisos, licencia concreta y compatibilidad antes de proponer una incorporación.

No existe aprobación persistente, archivo de manifiesto confiable ni promoción automática a `registry/`. El instalador `scripts/sync.py` continúa independiente y sin aceptar skills externas.

## 5. Validar

```powershell
python -m unittest discover -s tests -v
```

Las pruebas de Skill Scout usan respuestas GitHub simuladas, sin red. CI realiza pruebas en Ubuntu y Windows. Ni `discover`, ni `locate`, ni `inspect` escriben archivos; el informe JSON aparece en la salida estándar. No hay monitoreo en segundo plano.

## Limitaciones conocidas

Se utiliza la API REST pública de GitHub, sujeta a límites de tasa y conectividad. No hay autenticación, catálogos agregados ni fuentes adicionales. El contenido no se interpreta como instrucciones. La detección de riesgos es heurística, no análisis de seguridad exhaustivo, y puede tener falsos positivos o falsos negativos.
