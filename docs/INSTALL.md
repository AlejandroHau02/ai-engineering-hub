# Installation (experimental v0.1)

Requires Python 3.10+ and a local checkout. Claude Code and Codex must be installed independently.

**Dry-run, without changing files:**

```bash
python scripts/sync.py --target both
```

**Explicit installation after review:**

```bash
python scripts/sync.py --target both --apply
```

On Windows, `py -3` is an alternative to `python`. A single provider can be chosen with `--target claude` or `--target codex`.

The installer writes a managed block to the current user's global `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`, preserving everything outside that block. It creates four skills under `~/.claude/skills/` and `~/.agents/skills/`. Modified global files receive timestamped `.bak.*` backups.

**Safety:** conflicting skill files, malformed managed markers and symlink destinations abort before writing. v0.1 never overwrites an existing skill with different content. No network, external skills, packages, credentials or hidden scripts are involved.

**Test in a disposable home:**

```bash
python scripts/sync.py --target both --home ./scratch-home
python -m unittest discover -s tests -v
```

The first command above only previews; it does not create `scratch-home`.

Start a new Claude/Codex session after installing. Confirm instruction loading using a non-sensitive test repository and check that relevant skills are discoverable. Behavior quality needs human evaluation; unit tests validate the installer, not the model's judgment.

## Rollback

Restore the most recent timestamped backup for modified global instructions or remove only the block between `<!-- ai-engineering-hub:start -->` and `<!-- ai-engineering-hub:end -->`. Remove a created skill directory only after confirming it contains no personal work. Do not erase unrelated instructions.
