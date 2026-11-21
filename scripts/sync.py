#!/usr/bin/env python3
"""Safe global instruction and skill installer. Dry-run by default."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- ai-engineering-hub:start -->"
END = "<!-- ai-engineering-hub:end -->"
SKILLS = ("architecture-review", "ux-ui", "backend-engineering", "code-review")


@dataclass(frozen=True)
class Change:
    path: Path
    content: str
    kind: str


def ensure_safe(path: Path, home: Path) -> None:
    """Never write through symlinks or into non-directory parents."""
    if not path.is_relative_to(home):
        raise ValueError(f"Destination is outside home: {path}")
    current = path
    while current != home:
        if current.is_symlink():
            raise ValueError(f"Symlink destination is not allowed: {current}")
        if current != path and current.exists() and not current.is_dir():
            raise ValueError(f"Parent is not a directory: {current}")
        current = current.parent
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")


def global_block(target: str) -> str:
    core = (ROOT / "core/ENGINEERING_CORE.md").read_text(encoding="utf-8").strip()
    adapter = (ROOT / "adapters" / f"{target}.md").read_text(encoding="utf-8").strip()
    return f"{START}\n{core}\n\n{adapter}\n{END}\n"


def merge_block(existing: str, block: str) -> str:
    count_start = existing.count(START)
    count_end = existing.count(END)
    if count_start != count_end or count_start > 1:
        raise ValueError("Malformed/repeated managed markers; review manually")
    if not count_start:
        spacer = "\n\n" if existing and not existing.endswith("\n") else ("\n" if existing.strip() else "")
        return existing + spacer + block
    start = existing.index(START)
    end = existing.index(END)
    if end < start:
        raise ValueError("Managed markers are out of order")
    return existing[:start] + block.rstrip("\n") + existing[end + len(END):]


def build_plan(home: Path, targets: tuple[str, ...]) -> list[Change]:
    changes: list[Change] = []
    for target in targets:
        config = home / (".claude/CLAUDE.md" if target == "claude" else ".codex/AGENTS.md")
        ensure_safe(config, home)
        existing = config.read_text(encoding="utf-8") if config.exists() else ""
        updated = merge_block(existing, global_block(target))
        if updated != existing:
            changes.append(Change(config, updated, "UPDATE" if config.exists() else "CREATE"))
        root = home / (".claude/skills" if target == "claude" else ".agents/skills")
        for skill in SKILLS:
            dest = root / skill / "SKILL.md"
            ensure_safe(dest, home)
            new_content = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            if dest.exists():
                if dest.read_text(encoding="utf-8") != new_content:
                    raise ValueError(f"Skill conflict: {dest} differs; no files were written")
                continue
            changes.append(Change(dest, new_content, "CREATE"))
    return changes


def write_with_backup(change: Change) -> None:
    destination = change.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            dir=destination.parent, delete=False
        ) as out:
            out.write(change.content)
            temp_path = Path(out.name)
        if destination.exists():
            os.chmod(temp_path, stat.S_IMODE(destination.stat().st_mode))
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = destination.with_name(destination.name + ".bak." + timestamp)
            shutil.copy2(destination, backup)
            print(f"BACKUP {backup}")
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("both", "claude", "codex"), default="both")
    parser.add_argument("--apply", action="store_true", help="Write files explicitly")
    parser.add_argument("--home", type=Path, default=Path.home(), help="Override home for testing")
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    targets = ("claude", "codex") if args.target == "both" else (args.target,)
    try:
        changes = build_plan(home, targets)
        if not changes:
            print("NO CHANGES")
            return 0
        for change in changes:
            mode = "APPLY" if args.apply else "DRY-RUN"
            print(f"{mode} {change.kind} {change.path}")
        if args.apply:
            for change in changes:
                write_with_backup(change)
            print(f"Applied {len(changes)} file change(s).")
        else:
            print("Preview only; nothing changed. Use --apply after review.")
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
