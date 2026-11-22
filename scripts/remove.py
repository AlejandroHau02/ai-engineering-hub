#!/usr/bin/env python3
"""Controlled, reversible removal of one SYNCODE-managed skill or specialist."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from secrets import token_hex
import sys

from cli_contract import (
    ArgumentContractError, ContractArgumentParser, SyncodeError, emit_json, envelope,
    error_object, resolve_home, wants_json,
)
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
            raise SyncodeError("Unknown managed specialist", code="unknown-component")
        suffix = ".md" if args.target == "claude" else ".toml"
        path = (Path(".claude/agents") if args.target == "claude"
                else Path(".codex/agents")) / (args.specialist + suffix)
        rendered = files_for_target(args.target)[path].encode("utf-8")
        return Candidate(path, _sha(rendered), False)

    root = Path(".claude/skills") if args.target == "claude" else Path(".agents/skills")
    if args.skill:
        if args.skill not in SKILLS:
            raise SyncodeError("Unknown first-party skill", code="unknown-component")
        expected = (ROOT / "skills" / args.skill / "SKILL.md").read_text(encoding="utf-8").encode("utf-8")
        return Candidate(root / args.skill / "SKILL.md", _sha(expected), True)

    # Optional third-party removal uses only the pre-approved pinned digest.
    # It never downloads or executes the third-party content.
    entries = validate_manifest(load_json(DEFAULT_MANIFEST))
    matches = [item for item in entries if item["id"] == args.approved_skill]
    if len(matches) != 1:
        raise SyncodeError("Approved skill is not in the reviewed manifest; manual review required",
                           code="unknown-component")
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
            raise SyncodeError(f"Unsafe managed skill directory: {folder}", code="unsafe-path")
        if {entry.name for entry in folder.iterdir()} != {"SKILL.md"}:
            raise SyncodeError(
                f"Skill directory has additional files; preserve and inspect manually: {folder}",
                code="destination-conflict",
            )
    if not dest.is_file() or _sha(dest.read_bytes()) != candidate.expected_sha256:
        raise SyncodeError(f"Modified or unrecognized managed content: {dest}; refusing removal",
                           code="skill-modified")
    return dest.parent if candidate.is_skill else dest


def _pending(home: Path) -> None:
    workspace = home / WORK_DIR
    if workspace.exists() or is_link_or_reparse(workspace):
        raise SyncodeError("Pending installation transaction; run sync.py --recover first",
                           code="transaction-pending")


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
        # Never retain backups underneath skills/ or agents/: native clients
        # may recursively discover SKILL.md regardless of directory basename.
        # Move one level above the discovery root, on the same filesystem.
        backup = source.parent.parent / (
            f"{source.parent.name}-{source.name}.removed.{stamp}-{token_hex(12)}"
        )
        # Validate both source and backup parents, including reparse points.
        ensure_safe(backup / ".probe" if candidate.is_skill else backup, home)
        if backup.exists() or is_link_or_reparse(backup):
            raise SyncodeError(f"Removal backup already exists: {backup}",
                               code="destination-conflict")
        # Exactly one path moves. On successful return, its original full
        # contents are retained for explicit manual restoration.
        source.rename(backup)
        return "removed", backup


def _parser(json_errors: bool) -> ContractArgumentParser:
    parser = ContractArgumentParser(description=__doc__, json_errors=json_errors)
    parser.add_argument("--target", required=True, choices=("claude", "codex"),
                        help="Remove from exactly one client per operation")
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--skill", choices=SKILLS)
    pick.add_argument("--specialist", help="Native specialist ID from the local catalog")
    pick.add_argument("--approved-skill", help="Approved third-party skill ID in the manifest")
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Explicitly disable selected component")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    json_mode = wants_json(argv)
    status, backup, error = "error", None, None
    candidate = None
    home: Path | None = None
    target = None
    try:
        args = _parser(json_mode).parse_args(argv)
        json_mode = args.format == "json"
        target = args.target
        home = resolve_home(args.home)
        candidate = _candidate(args)
        status, backup = remove(home, candidate, apply=args.apply)
    except (OSError, UnicodeError, ValueError, ArgumentContractError) as exc:
        error = exc
    except KeyboardInterrupt as exc:
        if not json_mode:
            raise
        error = exc
    except Exception as exc:  # noqa: BLE001 - contract boundary
        if not json_mode:
            raise
        error = exc
        print(f"ERROR: unexpected {type(exc).__name__}; rerun without --format json "
              "for a traceback", file=sys.stderr)
    if error is not None:
        status = "error"

    if json_mode:
        emit_json(envelope(
            status,
            home=None if home is None else str(home),
            target=target,
            component=(None if candidate is None else candidate.relative.as_posix()),
            backup=(None if backup is None else backup.relative_to(home).as_posix()),
            error=None if error is None else error_object(error),
        ))
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
    if isinstance(error, KeyboardInterrupt):
        return 130
    return 1 if error is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
