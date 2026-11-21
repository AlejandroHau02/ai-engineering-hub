# Registro de aprobaciones — Skill Scout v0.3

Este módulo permite **preparar** una evaluación, **validar** un catálogo manualmente aprobado y **simular** rutas de una promoción futura. No instala, copia, ejecuta ni descarga código.

## 1. Preparar ficha pendiente

Guarda un informe de `inspect` para revisión. El contenido del informe sigue siendo datos no confiables; no ejecutes sus instrucciones.

```powershell
python scripts/skill_scout.py inspect --url "https://github.com/owner/repo/blob/<SHA-40>/skills/sample/SKILL.md" --related > scout-report.json
python scripts/skill_registry.py review-template --report scout-report.json
```

La salida JSON incluye identidad inmutable y hashes, indicadores de riesgo y cobertura del análisis. **Ningún resultado se aprueba automáticamente**. El comando imprime la ficha; si la quieres guardar, redirige su salida manualmente fuera de un directorio público que contenga información sensible.

## 2. Evaluar manualmente antes de registrar

Revisa la licencia específica del archivo (no solo la declaración del repositorio), scripts y recursos referenciados, permisos/MCP, riesgos y dependencias, y ejecuta pruebas en un entorno aislado apropiado para Claude Code y Codex. Una ficha incompleta no debe incorporarse al registro.

Cada entrada final del manifiesto requiere:

- `id`: nombre único y seguro; no colisiona con skills propias.
- `source`: URL de `SKILL.md` fijada a SHA completo, repositorio, ruta, commit, blob y SHA-256.
- `assessment.license`: `verified`, identificador y evidencia HTTPS.
- `assessment.risk`: `accepted`, evidencia HTTPS, indicadores detectados y cobertura del escaneo.
- `assessment.compatibility`: estado `tested` o `unsupported` con evidencia por cliente; al menos un cliente probado.
- `approval`: `approved`, responsable, fecha ISO y evidencia HTTPS de aprobación explícita.

La plantilla comienza con estados `pending` y `not-verified` y **no pasa** la validación como aprobada. Copia al manifiesto únicamente la ficha evaluada y autorizada durante una revisión de GitHub. No basta cambiar la palabra `pending` sin pruebas.

## 3. Validación y simulación

```powershell
python scripts/skill_registry.py validate
python scripts/skill_registry.py plan --target both
```

El manifiesto `registry/approved-skills.json` está vacío por diseño. Su validación debe reportar cero skills aprobadas y el plan, cero cambios. Puedes usar `--manifest ruta.json` con copias de prueba y `--home ruta_absoluta` para evaluar conflictos en un directorio alternativo.

La simulación marca `PROPOSED_NEW`, `BLOCKED_EXISTING`, `BLOCKED_SYMLINK` o `BLOCKED_NON_DIRECTORY` según el estado de los destinos; **nunca escribe**. No equivale al preflight del futuro instalador y los resultados pueden quedar obsoletos si cambia el sistema de archivos.

## 4. Límites

`validate` solo verifica la estructura, no firma ni autentica a quien aprobó, no contacta a GitHub ni verifica que el contenido externo actual coincida con sus hashes. Un informe `inspect` tampoco certifica seguridad. No existe integración de `registry/approved-skills.json` con `scripts/sync.py`. La copia o instalación será un cambio separado, con revalidación de procedencia, idempotencia, respaldos, pruebas y autorización explícita.

Pruebas offline:

```powershell
python -m unittest discover -s tests -v
```
