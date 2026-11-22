# SYNCODE

**SYNchronized COde Development Environment** — entorno personal de ingeniería AI-first para Claude Code y Codex.

*Un mismo criterio. Diferentes inteligencias.*

El repositorio y los marcadores de configuración mantienen por ahora sus identificadores técnicos `ai-engineering-hub` para conservar compatibilidad con las instalaciones actuales.

Estado: **v0.3 en evolución**, con cinco skills propias y cuatro especialistas opcionales para cada cliente. Este repositorio es público y está separado de cualquier empleador.

## Objetivo

Usar agentes de programación que inspeccionen el contexto, cuestionen decisiones de riesgo, propongan alternativas viables y verifiquen resultados, sin reconfigurar cada proyecto.

## Incluye

- [Engineering Core](core/ENGINEERING_CORE.md): principios globales de ingeniería.
- [Adaptadores](adapters/): instrucciones globales específicas para Claude Code y Codex.
- [Skills](skills/): arquitectura, UX/UI, backend, revisión de código y auditoría de seguridad de aplicaciones (AppSec).
- [Especialistas](specialists/README.md): catálogo común y agentes nativos de Claude/Codex; instalación global opcional con `--specialists`.
- [Instalador](scripts/sync.py): Python estándar, simulación por defecto, aplicación opcional con respaldo y salida JSON versionada (`--format json`).
- [SYNCODE Doctor](scripts/doctor.py): diagnóstico local de instrucciones, skills, especialistas, herramientas disponibles y recuperación pendiente; sin ejecutar agentes ni alterar archivos.
- [Pruebas](tests/test_sync.py) y CI para validar instalación segura.
- [Evaluaciones](evals/README.md) de criterio, UX/UI, implementación y revisión cruzada; [rúbrica](evals/RUBRIC.md) y [calculadora local](scripts/score_evals.py).
- [Registro de skills](registry/README.md): baselines v0.1 para upgrades verificados; diseño de futura revisión de capacidades externas (sin instalaciones automáticas).

## Inicio rápido

Requisitos: Python 3.10+; Claude Code o Codex instalados por separado.

1. Clona este repositorio y abre una terminal en su raíz.
2. Revisa los cambios propuestos sin escribir nada:

   ```bash
   python scripts/sync.py --target both
   ```

3. Inspecciona las políticas y, si estás de acuerdo, instala:

   ```bash
   python scripts/sync.py --target both --apply
   ```

4. Inicia una **nueva sesión** de Claude Code o Codex en cualquier proyecto.

En Windows también puedes utilizar `py -3`. Lee [la guía de instalación](docs/INSTALL.md) para conflictos, respaldos y rollback.

## Actualizar skills existentes de v0.1

La v0.2 incluye controles de calidad respaldados por las evaluaciones y permite **proponer** upgrades seguros de skills v0.1 con comprobación de contenido y respaldos. Desde la raíz del repositorio:

```powershell
python scripts/sync.py --target both --upgrade-skills
```

Esto **no escribe nada**. Para aplicar un upgrade después de revisarlo, se requiere además `--apply`. El script rechaza skills personalizadas y archivos sin baseline conocido. Consulta [ADR-0002](docs/ADR-0002.md) y [la guía de instalación](docs/INSTALL.md).

La instalación global de v0.2 y los especialistas se validó en Windows, y el CI se ejecuta en Windows y Linux. La [interfaz local de gestión](https://github.com/AlejandroHau02/ai-engineering-hub/issues/4) queda planeada para una etapa posterior.

## Especialistas globales opcionales

```powershell
python scripts/sync.py --target both --specialists
```

El primer paso es un dry-run; añade definiciones globales a `~/.claude/agents/` y `~/.codex/agents/` solo con `--apply`. Los especialistas se limitan a revisión y asesoría; no reemplazan al agente principal. Consulta [instalación, límites de permisos y ejemplos](specialists/README.md). En Codex las restricciones de solo lectura son instrucciones, no un sandbox independiente.

## Auditoría de seguridad de aplicaciones (quinta skill propia)

La skill [application-security-audit](skills/application-security-audit/SKILL.md) permite revisar código, APIs, autorización y aislamiento entre tenants, secretos, dependencias y configuraciones de despliegue, con hallazgos sustentados en evidencia y pruebas de regresión. **No es un agente adicional, un escáner activo ni un servicio en segundo plano.**

Al revisar riesgos dependientes de versiones o vulnerabilidades recientes, instruye al asistente a consultar fuentes primarias (OWASP, MITRE CWE, NIST, CISA KEV, OSV y avisos de proveedores) si hay acceso autorizado a internet; debe registrar la fecha de consulta o declarar falta de verificación si está offline. No se actualiza a sí misma ni ejecuta herramientas sin autorización.

Se instala exactamente como las demás skills propias: con el comando habitual `python scripts/sync.py --target both` para simular y `--apply` para aplicar, sin flags nuevos. Las cuatro definiciones de especialistas se mantienen sin cambios.

## Principios de seguridad

El instalador no ejecuta código de terceros. En el flujo normal no descarga skills; **solo con `--approved-skills`** consulta archivos aprobados de GitHub para verificar su integridad antes de proponer cambios. Solo actualiza skills v0.1 reconocidas cuando lo autorizas mediante las opciones de upgrade y aplicación; nunca sobreescribe personalizaciones desconocidas. Las instrucciones personales fuera del bloque gestionado se conservan y los cambios se respaldan.

**No publiques aquí claves, información de clientes, código propietario, logs ni contexto privado de proyectos.**

## Skill Scout (v0.3, prototipo en desarrollo)

Una herramienta de consulta de solo lectura que busca **repositorios candidatos** y analiza un `SKILL.md` externo fijado a un commit, sin instalarlo:

```powershell
python scripts/skill_scout.py discover --query "agent skills" --limit 5
python scripts/skill_scout.py locate --repo "owner/repo" --limit 20
python scripts/skill_scout.py inspect --url "https://github.com/owner/repo/blob/<commit-sha-40-caracteres>/skills/example/SKILL.md" --related
```

Devuelve JSON con procedencia, hashes y señales de riesgo. **Todos los resultados requieren revisión humana.** La búsqueda inicial no verifica skills individuales; `locate` confirma rutas existentes y `inspect --related` examina una selección limitada de archivos del mismo directorio. El escaneo no certifica seguridad y la compatibilidad con Claude/Codex queda pendiente de pruebas. No existe promoción automática al instalador. Consulta [la guía](docs/SKILL_SCOUT.md) y [ADR-0003](docs/ADR-0003.md).

## Registro de revisiones y aprobaciones (v0.3)

La revisión y la aprobación humana son etapas independientes del descubrimiento. El manifiesto `registry/approved-skills.json` inicia vacío, y el gestor asociado solo imprime resultados:

```powershell
python scripts/skill_registry.py review-template --report scout-report.json
python scripts/skill_registry.py validate
python scripts/skill_registry.py plan --target both
```

**No se instala ninguna skill externa ni existe `--apply`** en el gestor. El plan marca potenciales destinos y conflictos; las fichas requieren licencia, riesgos, compatibilidad y aprobación revisados manualmente. Consulta [la guía](docs/SKILL_REGISTRY.md) y [ADR-0004](docs/ADR-0004.md).

## Instalación verificada de skills aprobadas (PR #10, experimental)

La instalación opcional de skills externas aprobadas utiliza el `sync.py` existente, con **dry-run predeterminado** y un mecanismo de **rollback por lote y recuperación explícita** ante interrupciones. El manifiesto continúa vacío; no se instala ninguna skill de terceros por defecto.

```powershell
python scripts/sync.py --target both --approved-skills
# Aplicar solo después de revisar y aprobar el plan:
python scripts/sync.py --target both --approved-skills --apply
```

Primero se comprueba el estado declarado de licencia/aprobación en el manifiesto (que debe haber sido revisado manualmente), compatibilidad de cada cliente, el SHA del commit, blob y SHA-256 de la fuente. Este incremento **solo soporta skills autónomas de un único `SKILL.md`**; se rechazan dependencias dentro de su directorio. Consulta [la guía](docs/APPROVED_INSTALL.md), [la recuperación de instalaciones](docs/TRANSACTION_RECOVERY.md) y [ADR-0005](docs/ADR-0005.md).

## Diagnóstico y salida estructurada (preparación Desktop)

```powershell
python scripts/doctor.py --target both
python scripts/doctor.py --target both --format json
python scripts/sync.py --target both --format json
```

El Doctor no ejecuta los clientes ni descarga información; muestra estado de instalación y conflictos. `sync.py --format json` describe operaciones y resultados sin cambiar el modo de texto ni el requisito explícito de `--apply`. El formato es un contrato de datos para una futura aplicación local, **no** un servidor ni una interfaz HTTP. Consulta [contratos CLI](docs/SYNCODE_CLI_CONTRACT.md) y [criterios para cerrar v0.3](docs/SYNCODE_V03_EXIT.md).

## Roadmap

- **v0.1:** núcleo reutilizable, skills propias, instalación segura y pruebas.
- **v0.2:** upgrades verificados, controles de calidad y capa opcional de especialistas nativos.
- **v0.3 (en desarrollo):** inspección, aprobación e instalación controlada de skills externas autónomas y quinta skill propia de auditoría AppSec; bundles y fuentes adicionales siguen pendientes.

Consulta [ADR-0001](docs/ADR-0001.md) para las decisiones de arquitectura.

**Licencia:** pendiente de decisión. La visibilidad pública no implica permisos generales de reutilización.
