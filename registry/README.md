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
