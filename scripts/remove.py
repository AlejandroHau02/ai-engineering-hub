#!/usr/bin/env python3
"""Controlled, reversible removal of one SYNCODE-managed skill or specialist."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from secrets import token_hex
import sys

from path_safety import is_link_or_reparse
from skill_registry import DEFAULT_MANIFEST, load_json, validate_manifest
from specialists import files_for_target, read_catalog
from sync import ROOT, SKILLS, ensure_safe
from transaction import WORK_DIR, exclusive_home_lock


@dataclass(frozen=True)
class Candidate:
    relative: Path
    expected_sha256: str
    is_skill: bool


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _candidate(args) -> Candidate:
    """Only trusted, known targets; no user-supplied filesystem paths."""
    if args.specialist:
        spec = dict((entry["name"], entry) for entry in read_catalog())
        if args.specialist not in spec:
            raise ValueError("Unknown managed specialist")
        suffix = ".md" if args.target == "claude" else ".toml"
        path = (Path(".claude/agents") if args.target == "claude"
                else Path(".codex/agents")) / (args.specialist + suffix)
        rendered = files_for_target(args.target)[path].encode("utf-8")
        return Candidate(path, _sha(rendered), False)

    root = Path(".claude/skills") if args.target == "claude" else Path(".agents/skills")
    if args.skill:
        if args.skill not in SKILLS:
            raise ValueError("Unknown first-party skill")
        expected = (ROOT / "skills" / args.skill / "SKILL.md").read_text(encoding="utf-8").encode("utf-8")
        return Candidate(root / args.skill / "SKILL.md", _sha(expected), True)

    # Optional third-party removal uses only the pre-approved pinned digest.
    # It never downloads or executes the third-party content.
    entries = validate_manifest(load_json(DEFAULT_MANIFEST))
    matches = [item for item in entries if item["id"] == args.approved_skill]
    if len(matches) != 1:
        raise ValueError("Approved skill is not in the reviewed manifest; manual review required")
    return Candidate(
        root / args.approved_skill / "SKILL.md",
        matches[0]["source"]["contentSha256"],
        True,
    )


def _preflight(home: Path, candidate: Candidate) -> Path | None:
    """Fail closed on modifications, links, extra files or unknown ownership."""
    dest = home / candidate.relative
    ensure_safe(dest, home)
    if not dest.exists():
        return None
    if candidate.is_skill:
        folder = dest.parent
        if is_link_or_reparse(folder) or not folder.is_dir():
            raise ValueError(f"Unsafe managed skill directory: {folder}")
        if {entry.name for entry in folder.iterdir()} != {"SKILL.md"}:
            raise ValueError(
                f"Skill directory has additional files; preserve and inspect manually: {folder}"
            )
    if not dest.is_file() or _sha(dest.read_bytes()) != candidate.expected_sha256:
        raise ValueError(f"Modified or unrecognized managed content: {dest}; refusing removal")
    return dest.parent if candidate.is_skill else dest


def _pending(home: Path) -> None:
    workspace = home / WORK_DIR
    if workspace.exists() or is_link_or_reparse(workspace):
        raise ValueError("Pending installation transaction; run sync.py --recover first")


def remove(home: Path, candidate: Candidate, *, apply: bool) -> tuple[str, Path | None]:
    """A single-target atomic rename; backup is outside active discovery path.

    Interruption after rename leaves a complete disabled backup. No batch delete,
    no automatic housekeeping and no alteration of other skill directories.
    """
    _pending(home)
    source = _preflight(home, candidate)
    if source is None:
        return "unchanged", None
    if not apply:
        return "planned", None

    with exclusive_home_lock(home, ensure_safe):
        _pending(home)
        source = _preflight(home, candidate)
        if source is None:
            return "unchanged", None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = source.with_name(source.name + f".removed.{stamp}-{token_hex(12)}")
        # Validate both source and backup parents, including reparse points.
        ensure_safe(backup / ".probe" if candidate.is_skill else backup, home)
        if backup.exists() or is_link_or_reparse(backup):
            raise ValueError(f"Removal backup already exists: {backup}")
        # Exactly one path moves. On successful return, its original full
        # contents are retained for explicit manual restoration.
        source.rename(backup)
        return "removed", backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("claude", "codex"),
                        help="Remove from exactly one client per operation")
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--skill", choices=SKILLS)
    pick.add_argument("--specialist", help="Native specialist ID from the local catalog")
    pick.add_argument("--approved-skill", help="Approved third-party skill ID in the manifest")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--apply", action="store_true", help="Explicitly disable selected component")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    home = args.home.expanduser().resolve()
    status, backup, error = "error", None, None
    candidate = None
    try:
        candidate = _candidate(args)
        status, backup = remove(home, candidate, apply=args.apply)
    except (OSError, UnicodeError, ValueError) as exc:
        error = exc

    if args.format == "json":
        print(json.dumps({
            "schemaVersion": 1,
            "product": "SYNCODE",
            "status": status,
            "target": args.target,
            "component": (None if candidate is None else candidate.relative.as_posix()),
            "backup": (None if backup is None else backup.relative_to(home).as_posix()),
            "error": None if error is None else {
                "type": type(error).__name__, "message": str(error),
            },
        }, ensure_ascii=False, sort_keys=True))
    elif error is not None:
        print(f"ERROR: {error}", file=sys.stderr)
    elif status == "planned":
        print(f"DRY-RUN REMOVE {home / candidate.relative}")
        print("Preview only; nothing changed. Use --apply after review.")
    elif status == "removed":
        print(f"REMOVED {home / candidate.relative}")
        print(f"RECOVERABLE BACKUP {backup}")
    else:
        print("NO CHANGES")
    return 1 if error is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
