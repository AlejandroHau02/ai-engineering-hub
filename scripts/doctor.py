#!/usr/bin/env python3
"""Read-only installation diagnostics for SYNCODE (no subprocess execution)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from specialists import files_for_target
from sync import BASELINES, END, ROOT, SKILLS, START, ensure_safe, global_block
from transaction import LOCK_FILE, LOCK_MARKER, WORK_DIR


def _entry(home: Path, relative: Path, expected: str, kind: str,
           baseline: Path | None = None) -> dict[str, str]:
    """Inspect a known managed path without following links or leaking contents."""
    path = home / relative
    try:
        ensure_safe(path, home)
        if not path.exists():
            return {"status": "missing"}
        actual = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return {"status": "unsafe-or-unreadable"}

    if kind == "instructions":
        starts, ends = actual.count(START), actual.count(END)
        if starts == ends == 0:
            return {"status": "unmanaged"}
        if starts != 1 or ends != 1:
            return {"status": "modified"}
        start, end = actual.index(START), actual.index(END)
        if end < start:
            return {"status": "modified"}
        section = actual[start:end + len(END)]
        return {"status": "current" if section == expected.strip() else "modified"}

    if actual == expected:
        return {"status": "current"}
    if baseline is not None and baseline.is_file():
        if actual == baseline.read_text(encoding="utf-8"):
            return {"status": "upgrade-available"}
    return {"status": "modified"}


def _workspace_status(home: Path) -> str:
    path = home / WORK_DIR
    try:
        ensure_safe(path, home)
        if path.exists():
            return "pending-recovery"
        return "clear"
    except (OSError, ValueError):
        return "unsafe"


def _lock_status(home: Path) -> str:
    path = home / LOCK_FILE
    try:
        ensure_safe(path, home)
        if not path.exists():
            return "absent"
        if not path.is_file() or path.stat().st_size != len(LOCK_MARKER):
            return "unexpected"
        with path.open("rb") as handle:
            return "recognized" if handle.read(len(LOCK_MARKER) + 1) == LOCK_MARKER else "unexpected"
    except (OSError, ValueError):
        return "unsafe"


def inspect(home: Path, targets: tuple[str, ...]) -> dict:
    """Return a versioned status snapshot; no writes, network or agent execution."""
    home = home.expanduser().resolve()
    statuses: list[str] = []
    clients: dict[str, dict] = {}
    for target in targets:
        if target not in ("claude", "codex"):
            raise ValueError(f"Invalid target: {target}")
        config = Path(".claude/CLAUDE.md" if target == "claude" else ".codex/AGENTS.md")
        instruction = _entry(home, config, global_block(target), "instructions")
        statuses.append(instruction["status"])
        root = Path(".claude/skills") if target == "claude" else Path(".agents/skills")
        skills = {}
        for name in SKILLS:
            skills[name] = _entry(
                home, root / name / "SKILL.md",
                (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8"),
                "skill", BASELINES / name / "SKILL.md",
            )
            statuses.append(skills[name]["status"])
        specialists = {}
        for relative, expected in files_for_target(target).items():
            name = relative.stem
            specialists[name] = _entry(home, relative, expected, "specialist")
            statuses.append(specialists[name]["status"])
        executable = shutil.which("claude" if target == "claude" else "codex")
        clients[target] = {
            "executable": {"status": "found" if executable else "not-found",
                           "path": executable},
            "instructions": instruction,
            "skills": skills,
            "specialists": specialists,
        }

    transaction = _workspace_status(home)
    lock = _lock_status(home)
    blockers = sum(s in ("modified", "unsafe-or-unreadable") for s in statuses)
    blockers += int(transaction != "clear")
    blockers += int(lock in ("unexpected", "unsafe"))
    return {
        "schemaVersion": 1,
        "product": "SYNCODE",
        "home": str(home),
        "targets": list(targets),
        "transaction": transaction,
        "lock": lock,
        "clients": clients,
        "summary": {
            "current": statuses.count("current"),
            "missing": statuses.count("missing"),
            "upgradeAvailable": statuses.count("upgrade-available"),
            "unmanaged": statuses.count("unmanaged"),
            "blockers": blockers,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("both", "claude", "codex"), default="both")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    targets = ("claude", "codex") if args.target == "both" else (args.target,)
    try:
        report = inspect(args.home, targets)
    except (OSError, UnicodeError, ValueError) as error:
        if args.format == "json":
            print(json.dumps({"schemaVersion": 1, "product": "SYNCODE",
                              "status": "error", "error": str(error)}, ensure_ascii=False))
        else:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"SYNCODE Doctor | home={report['home']}")
        print(f"Transaction: {report['transaction']} | lock: {report['lock']}")
        for target, client in report["clients"].items():
            print(f"{target.upper()} CLI: {client['executable']['status']}")
            print(f"  global instructions: {client['instructions']['status']}")
            for name, status in client["skills"].items():
                print(f"  skill {name}: {status['status']}")
            for name, status in client["specialists"].items():
                print(f"  specialist {name}: {status['status']}")
        print(f"Summary: {report['summary']}")
    # Missing optional components/CLIs are diagnostics, not a hard error.
    # Modifications, unsafe paths and interrupted transactions require review.
    return 2 if report["summary"]["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
