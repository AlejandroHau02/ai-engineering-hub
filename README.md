# AI Engineering Hub

**Entorno personal de ingeniería AI-first para Claude Code y Codex.**

Estado: **v0.1 instalada; v0.2 en revisión**. Este repositorio es público y está separado de cualquier empleador.

## Objetivo

Usar agentes de programación que inspeccionen el contexto, cuestionen decisiones de riesgo, propongan alternativas viables y verifiquen resultados, sin reconfigurar cada proyecto.

## Incluye

- [Engineering Core](core/ENGINEERING_CORE.md): principios globales de ingeniería.
- [Adaptadores](adapters/): instrucciones globales específicas para Claude Code y Codex.
- [Skills](skills/): arquitectura, UX/UI, backend y revisión de código.
- [Instalador](scripts/sync.py): Python estándar, simulación por defecto, aplicación opcional con respaldo.
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

## Vista previa v0.2 — actualizar skills existentes

La rama v0.2 añade controles de calidad respaldados por las evaluaciones y permite **proponer** upgrades de skills v0.1 con comprobación de contenido y respaldos. Desde esa rama:

```powershell
python scripts/sync.py --target both --upgrade-skills
```

Esto **no escribe nada**. Para aplicar un upgrade después de revisarlo, se requiere además `--apply`. El script rechaza skills personalizadas y archivos sin baseline conocido. Consulta [ADR-0002](docs/ADR-0002.md) y [la guía de instalación](docs/INSTALL.md).

La v0.2 es una propuesta sobre la rama v0.1, aún no fusionada; no significa que las skills globales locales se hayan actualizado. La [interfaz local de gestión](https://github.com/AlejandroHau02/ai-engineering-hub/issues/4) queda planeada para una etapa posterior.

## Principios de seguridad

El instalador no descarga nada ni ejecuta código de terceros. Solo actualiza skills v0.1 reconocidas cuando lo autorizas mediante las opciones de upgrade y aplicación; nunca sobreescribe personalizaciones desconocidas. Las instrucciones personales fuera del bloque gestionado se conservan y los cambios se respaldan.

**No publiques aquí claves, información de clientes, código propietario, logs ni contexto privado de proyectos.**

## Roadmap

- **v0.1:** núcleo reutilizable, skills propias, instalación segura y pruebas.
- **v0.2 (en desarrollo):** upgrades verificados de skills instaladas, mejores controles de calidad y evaluación de permisos internos.
- **v0.3:** descubrimiento de actualizaciones, evaluaciones, y aprobación antes de promover cambios.

Consulta [ADR-0001](docs/ADR-0001.md) para las decisiones de arquitectura.

**Licencia:** pendiente de decisión. La visibilidad pública no implica permisos generales de reutilización.
