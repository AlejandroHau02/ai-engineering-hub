# Claude Code global adapter

Apply the shared Engineering Core above across all repositories. Respect local project instructions and the user’s objective.

For substantive specialist tasks, consider personal skills architecture-review, ux-ui, backend-engineering, and code-review. Do not invoke everything automatically.

Question risky approaches with reasons and alternatives. State verification limits. This managed policy belongs in global CLAUDE.md, not in application repositories.

When a substantive task needs an independent, bounded second opinion, consider user-level subagents hub-ux-auditor, hub-backend-auditor, hub-architecture-advisor, or hub-code-reviewer. Prefer one specialist with a specific question; keep ordinary changes in the main session. These advisors are for analysis, not coding or deployment. Their tool availability is constrained by their own agent definitions; never presume an unrun tool was used.
