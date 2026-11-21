# AI Engineering Hub

**Entorno personal de ingeniería AI-first para Claude Code y Codex.**

Estado: **v0.1 experimental**. Este repositorio es público y está separado de cualquier empleador.

## Objetivo

Usar agentes de programación que inspeccionen el contexto, cuestionen decisiones de riesgo, propongan alternativas viables y verifiquen resultados, sin reconfigurar cada proyecto.

## Incluye

- [Engineering Core](core/ENGINEERING_CORE.md): principios globales de ingeniería.
- [Adaptadores](adapters/): instrucciones globales específicas para Claude Code y Codex.
- [Skills](skills/): arquitectura, UX/UI, backend y revisión de código.
- [Instalador](scripts/sync.py): Python estándar, simulación por defecto, aplicación opcional con respaldo.
- [Pruebas](tests/test_sync.py) y CI para validar instalación segura.
- [Evaluaciones](evals/README.md) de criterio, UX/UI, implementación y revisión cruzada; [rúbrica](evals/RUBRIC.md) y [calculadora local](scripts/score_evals.py).
- [Registro de skills](registry/README.md): diseño del proceso de revisión de capacidades externas (sin instalaciones automáticas).

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

## Principios de seguridad

El instalador no descarga nada ni ejecuta código de terceros. No sobrescribe skills con contenido diferente. Las instrucciones personales fuera del bloque gestionado se conservan y los cambios se respaldan.

**No publiques aquí claves, información de clientes, código propietario, logs ni contexto privado de proyectos.**

## Roadmap

- **v0.1:** núcleo reutilizable, skills propias, instalación segura y pruebas.
- **v0.2:** agentes especializados y registro versionado de skills, con propuestas revisables.
- **v0.3:** descubrimiento de actualizaciones, evaluaciones, y aprobación antes de promover cambios.

Consulta [ADR-0001](docs/ADR-0001.md) para las decisiones de arquitectura.

**Licencia:** pendiente de decisión. La visibilidad pública no implica permisos generales de reutilización.
