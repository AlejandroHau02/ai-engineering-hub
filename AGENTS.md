# Instructions for agents working on this hub

Read `README.md`, `core/ENGINEERING_CORE.md` and `docs/ADR-0001.md` before architecture changes.

- Maintain vendor-neutral, employer-independent behavior.
- Never add private code, customer data, secrets or unauthorized copies of third-party skills.
- Favor portable Python standard library and documented Agent Skills conventions.
- Preserve users' existing global instructions and skill files.
- Do not install or execute untrusted third-party code.
- For installer changes, add tests and run `python -m unittest discover -s tests -v`.
- Keep commits reviewable; do not merge PRs, deploy or enable background automation without authorization.
