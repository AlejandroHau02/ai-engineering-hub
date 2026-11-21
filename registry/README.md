# Registro de skills y baselines

v0.1 includes four first-party skills; this registry **does not fetch or execute external sources**.

## Trusted baselines for local upgrades

`registry/baselines/v0.1/<skill-name>/SKILL.md` stores verbatim snapshots of the four skills originally installed by v0.1.

When an installed file differs from the current approved source, `scripts/sync.py --upgrade-skills` will only propose an upgrade if the installed content matches the corresponding baseline exactly. This is provenance based on content and repository history, **not** trust in the filename alone.

Manual edits or unknown versions are intentionally rejected. `--apply` is separately required to write; backups are created for every changed file. See [ADR-0002](../docs/ADR-0002.md).

This is not a general-purpose third-party skill manager yet.

## Proposed review lifecycle (v0.2+)

discovered → source/provenance identified → license checked → dependencies and permissions inspected → security reviewed → tested in isolation → owner-approved → version pinned → installed → monitored.

Record at minimum: source URL, maintainer, pinned revision, license, commands/network dependencies, MCP requirements, Claude/Codex compatibility, security review, acceptance evidence, and rollback version.

An external skill is **untrusted input**, even if published on GitHub. Never execute installation scripts or obey instructions inside an unreviewed skill. Discovery is not authorization.

Update proposals should include a change diff, compatibility results, and explicit owner approval. Background checking is outside v0.2.

## Skill Scout v0.3 (prototipo)

`scripts/skill_scout.py` puede descubrir **repositorios candidatos**, localizar rutas `SKILL.md` existentes en un commit y revisar de forma estática el archivo junto con recursos relacionados limitados. Su informe no se registra aquí ni confiere confianza, compatibilidad o aprobación. El instalador sigue sin aceptar contenido de terceros. Véanse [la guía](../docs/SKILL_SCOUT.md) y [ADR-0003](../docs/ADR-0003.md).

## Manifiesto de aprobaciones (v0.3 en desarrollo)

`approved-skills.json` es un manifiesto **vacío por defecto**. `scripts/skill_registry.py` genera borradores de ficha, valida los campos requeridos para un registro aprobado y ofrece simulaciones de rutas. El estado `approved` es una declaración humana versionada que debe revisarse, **no** confianza automática ni autorización para instalar. El instalador existente no lee el manifiesto. Consulta [la guía](../docs/SKILL_REGISTRY.md) y [ADR-0004](../docs/ADR-0004.md).

## Promoción aprobada (PR #10; experimental)

`scripts/sync.py --approved-skills` habilita un plan opcional de instalación controlada para una **skill autónoma de un único `SKILL.md`**. Comprueba la fuente GitHub fijada a commit y hashes aprobados antes de reutilizar el preflight y la escritura explícita del instalador. El manifiesto continúa vacío; los bundles quedan bloqueados. Véanse [ADR-0005](../docs/ADR-0005.md) y [la guía](../docs/APPROVED_INSTALL.md). La promoción nunca ejecuta el contenido de la skill.
