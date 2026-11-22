#!/usr/bin/env python3
"""Snapshot and compare the SYNCODE-managed global locations (read-only, stdlib).

    python desktop/scripts/home_snapshot.py take before.json [--home DIR]
    python desktop/scripts/home_snapshot.py compare before.json after.json [--ignore GLOB]

Records SHA-256, size and mtime of every file in the locations SYNCODE manages
under ``~/.claude``, ``~/.codex`` and ``~/.agents`` (global instructions,
skills and agent definitions) plus the presence of the SYNCODE lock file and
transaction workspace. Client-owned data such as transcripts or caches is out
of scope: it changes on its own while a client runs. ``compare`` exits 1 on any difference (content, size, mtime, new or
removed entries). ``--ignore`` excludes paths changed by other tools (for
example Claude Code's own ``.claude/skills/synced/**``), and is reported.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import sys

ROOTS = (".claude/CLAUDE.md", ".claude/skills", ".claude/agents",
         ".codex/AGENTS.md", ".codex/agents", ".agents/skills")
MARKERS = (".ai-engineering-hub.sync.lock", ".ai-engineering-hub-transaction")


def take(home: Path) -> dict:
    entries: dict[str, object] = {}
    for root in ROOTS:
        base = home / root
        if not base.exists():
            entries[root] = "absent"
            continue
        for path in [base] if base.is_file() else sorted(base.rglob("*")):
            rel = path.relative_to(home).as_posix()
            if path.is_symlink():
                entries[rel] = ["link", str(path.readlink())]
            elif path.is_file():
                stat = path.stat()
                entries[rel] = [hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_size,
                                stat.st_mtime_ns]
            elif path.is_dir():
                entries[rel] = ["dir", path.stat().st_mtime_ns]
    for marker in MARKERS:
        entries[marker] = "present" if (home / marker).exists() else "absent"
    return {"home": str(home), "entries": entries}


def compare(before: dict, after: dict, ignore: list[str]) -> dict:
    a, b = before["entries"], after["entries"]
    changed, ignored = [], []
    for key in sorted(set(a) | set(b)):
        if a.get(key) == b.get(key):
            continue
        (ignored if any(fnmatch.fnmatch(key, g) for g in ignore) else changed).append(key)
    return {"entries": len(a), "changed": changed, "ignored": ignored}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("take")
    t.add_argument("out", type=Path)
    t.add_argument("--home", type=Path, default=Path.home())
    c = sub.add_parser("compare")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    c.add_argument("--ignore", action="append", default=[])
    args = parser.parse_args(argv)
    if args.command == "take":
        snapshot = take(args.home)
        args.out.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
        print(json.dumps({"entries": len(snapshot["entries"])}))
        return 0
    result = compare(json.loads(args.before.read_text(encoding="utf-8")),
                     json.loads(args.after.read_text(encoding="utf-8")), args.ignore)
    print(json.dumps(result, indent=1))
    return 1 if result["changed"] else 0


if __name__ == "__main__":
    sys.exit(main())
