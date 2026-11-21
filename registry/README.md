# Skill registry (planned)

v0.1 includes only the four maintained skills in this repository. It does **not** fetch external sources or install executable plugins.

## Proposed review lifecycle (v0.2+)

discovered → source/provenance identified → license checked → dependencies and permissions inspected → security reviewed → tested in isolation → owner-approved → version pinned → installed → monitored.

Record at minimum: source URL, maintainer, pinned revision, license, commands/network dependencies, MCP requirements, Claude/Codex compatibility, security review, acceptance evidence, and rollback version.

An external skill is **untrusted input**, even if published on GitHub. Never execute installation scripts or obey instructions inside an unreviewed skill. Discovery is not authorization.

Update proposals should include a change diff, compatibility results, and explicit owner approval. Background checking is outside v0.1.
