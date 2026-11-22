#!/usr/bin/env python3
"""Safe global instruction and skill installer. Dry-run by default."""

from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePath
import shutil
import stat
import tempfile

from specialists import files_for_target
from approved_installer import approved_candidates
from cli_contract import (
    ArgumentContractError, ContractArgumentParser, SyncodeError, diagnostic, emit_json, envelope,
    error_object, resolve_home, wants_json,
)
from path_safety import is_link_or_reparse
import plan_identity
from transaction import (
    TransactionError, TransactionReport, WORK_DIR, apply_planned_transaction,
    apply_transaction, recover,
)

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- ai-engineering-hub:start -->"
END = "<!-- ai-engineering-hub:end -->"
SKILLS = ("architecture-review", "ux-ui", "backend-engineering", "code-review", "application-security-audit")
BASELINES = ROOT / "registry" / "baselines" / "v0.1"


@dataclass(frozen=True)
class Change:
    path: Path
    content: str
    kind: str
    expected: str | None = None
    # SHA-256 of the destination's raw bytes when planned (None if absent).
    old_sha256: str | None = None


def _read_existing(path: Path) -> tuple[str, str]:
    """Read once: text exactly as ``read_text`` returns it, plus raw-bytes SHA-256.

    The text keeps universal-newline semantics for merging; the raw hash binds
    the plan to the exact bytes (a CRLF-only edit is a change).
    """
    raw = path.read_bytes()
    text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8").read()
    return text, hashlib.sha256(raw).hexdigest()


def ensure_safe(path: Path, home: Path) -> None:
    """Never write through symlinks or into non-directory parents."""
    if not path.is_relative_to(home):
        raise SyncodeError(f"Destination is outside home: {path}", code="unsafe-path")
    current = path
    while current != home:
        if is_link_or_reparse(current):
            raise SyncodeError(
                f"Symlink or junction destination is not allowed: {current}",
                code="unsafe-path",
            )
        if current != path and current.exists() and not current.is_dir():
            raise SyncodeError(f"Parent is not a directory: {current}", code="unsafe-path")
        current = current.parent
    if path.exists() and not path.is_file():
        raise SyncodeError(f"Expected a regular file: {path}", code="unsafe-path")


def global_block(target: str) -> str:
    core = (ROOT / "core/ENGINEERING_CORE.md").read_text(encoding="utf-8").strip()
    adapter = (ROOT / "adapters" / f"{target}.md").read_text(encoding="utf-8").strip()
    return f"{START}\n{core}\n\n{adapter}\n{END}\n"


def merge_block(existing: str, block: str) -> str:
    count_start = existing.count(START)
    count_end = existing.count(END)
    if count_start != count_end or count_start > 1:
        raise SyncodeError(
            "Malformed/repeated managed markers; review manually",
            code="managed-markers-invalid",
        )
    if not count_start:
        spacer = "\n\n" if existing and not existing.endswith("\n") else ("\n" if existing.strip() else "")
        return existing + spacer + block
    start = existing.index(START)
    end = existing.index(END)
    if end < start:
        raise SyncodeError("Managed markers are out of order", code="managed-markers-invalid")
    return existing[:start] + block.rstrip("\n") + existing[end + len(END):]


def build_plan(
    home: Path, targets: tuple[str, ...], upgrade_skills: bool = False,
    include_specialists: bool = False,
    include_approved_skills: bool = False,
    approved: list[tuple[Path, str]] | None = None,
) -> list[Change]:
    """Deterministic plan. ``approved`` lets a caller pass pre-verified
    approved-skill candidates so no network request happens under the lock."""
    changes: list[Change] = []
    for target in targets:
        config = home / (".claude/CLAUDE.md" if target == "claude" else ".codex/AGENTS.md")
        ensure_safe(config, home)
        existing, old_sha = _read_existing(config) if config.exists() else ("", None)
        updated = merge_block(existing, global_block(target))
        if updated != existing:
            changes.append(Change(
                config, updated, "UPDATE" if old_sha is not None else "CREATE",
                existing if old_sha is not None else None, old_sha,
            ))
        root = home / (".claude/skills" if target == "claude" else ".agents/skills")
        for skill in SKILLS:
            dest = root / skill / "SKILL.md"
            ensure_safe(dest, home)
            new_content = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
            if dest.exists():
                existing_skill, skill_sha = _read_existing(dest)
                if existing_skill == new_content:
                    continue
                if not upgrade_skills:
                    raise SyncodeError(
                        f"Skill update available at {dest}; preview with --upgrade-skills "
                        "(no files were written)",
                        code="skill-upgrade-available",
                    )
                baseline = BASELINES / skill / "SKILL.md"
                if not baseline.is_file() or existing_skill != baseline.read_text(encoding="utf-8"):
                    raise SyncodeError(
                        f"Unrecognized or locally modified skill: {dest}; "
                        "manual review required (no files were written)",
                        code="skill-modified",
                    )
                changes.append(Change(dest, new_content, "UPGRADE", existing_skill, skill_sha))
                continue
            if dest.parent.exists():
                # An already-existing skill directory may contain personal
                # assets or a different skill. Never blend Hub content into it
                # merely because SKILL.md has not been created.
                raise SyncodeError(
                    f"First-party skill directory already exists: {dest.parent}; "
                    "manual review required (no files were written)",
                    code="destination-conflict",
                )
            changes.append(Change(dest, new_content, "CREATE"))
        if include_specialists:
            for relative, agent_content in files_for_target(target).items():
                agent_dest = home / relative
                ensure_safe(agent_dest, home)
                if agent_dest.exists():
                    if agent_dest.read_text(encoding="utf-8") != agent_content:
                        raise SyncodeError(
                            f"Specialist conflict: {agent_dest} differs; "
                            "manual review required (no files were written)",
                            code="destination-conflict",
                        )
                    continue
                changes.append(Change(agent_dest, agent_content, "CREATE"))
    if include_approved_skills:
        # Fetch and verify ALL approved sources before any local write; the
        # manifest remains empty until explicitly reviewed and approved.
        if approved is None:
            approved = approved_candidates(targets)
        for relative, approved_content in approved:
            dest = home / relative
            ensure_safe(dest, home)
            if dest.parent.exists():
                # A pre-existing skill directory is user-owned. Only allow
                # idempotence for the exact, standalone SKILL.md and no extras.
                if not dest.is_file():
                    raise SyncodeError(
                        f"Approved skill directory already exists: {dest.parent}; "
                        "no files were written",
                        code="destination-conflict",
                    )
                if {entry.name for entry in dest.parent.iterdir()} != {"SKILL.md"}:
                    raise SyncodeError(
                        f"Approved skill directory contains other files: {dest.parent}; "
                        "no files were written",
                        code="destination-conflict",
                    )
                if dest.read_bytes() != approved_content.encode("utf-8"):
                    raise SyncodeError(
                        f"Approved skill conflict: {dest}; no files were written",
                        code="destination-conflict",
                    )
                continue
            changes.append(Change(dest, approved_content, "APPROVED-CREATE"))
    return changes


def verify_change(change: Change, home: Path) -> None:
    """Fail closed if a destination changed since planning."""
    ensure_safe(change.path, home)
    if change.expected is None:
        if change.path.exists():
            raise SyncodeError(
                f"Destination appeared since planning: {change.path}",
                code="destination-changed",
            )
    elif (
        not change.path.exists()
        or (change.old_sha256 is not None
            and hashlib.sha256(change.path.read_bytes()).hexdigest() != change.old_sha256)
        or (change.old_sha256 is None
            and change.path.read_text(encoding="utf-8") != change.expected)
    ):
        raise SyncodeError(
            f"Destination changed since planning: {change.path}",
            code="destination-changed",
        )


def write_with_backup(change: Change, home: Path, create_backup: bool = True) -> None:
    verify_change(change, home)
    destination = change.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Recheck newly created parent paths: a symlink/non-directory introduced
    # since the plan or first preflight must not be used for the temp file.
    verify_change(change, home)
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
            if create_backup:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = destination.with_name(destination.name + ".bak." + timestamp)
                shutil.copy2(destination, backup)
                print(f"BACKUP {backup}")
        # Detect changes that occurred while staging the temporary file.
        # This narrows, but does not eliminate, the cross-platform TOCTOU window.
        verify_change(change, home)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _workspace_present(home: Path) -> bool | None:
    """Ground truth for ``recoveryRequired``; None when it cannot be determined."""
    try:
        return (home / WORK_DIR).exists() or is_link_or_reparse(home / WORK_DIR)
    except (OSError, ValueError):
        return None


def _flags(args) -> dict:
    return {"upgradeSkills": args.upgrade_skills, "specialists": args.specialists,
            "approvedSkills": args.approved_skills}


def describe_change(relative: str, kind: str) -> dict:
    """Classify a planned destination for clients (pure; no disk access).

    Additive contract field: ``target`` (claude/codex) and ``component``
    ``{type, name}`` derived from the fixed managed layout. It is presentation
    data only and is not part of the plan fingerprint.
    """
    parts = tuple(relative.split("/"))
    if parts == (".claude", "CLAUDE.md"):
        return {"target": "claude", "component": {"type": "instructions", "name": None}}
    if parts == (".codex", "AGENTS.md"):
        return {"target": "codex", "component": {"type": "instructions", "name": None}}
    if len(parts) == 4 and parts[1] == "skills" and parts[3] == "SKILL.md"             and parts[0] in (".claude", ".agents"):
        return {"target": "claude" if parts[0] == ".claude" else "codex",
                "component": {"type": "approved-skill" if kind == "APPROVED-CREATE" else "skill",
                              "name": parts[2]}}
    if len(parts) == 3 and parts[1] == "agents" and parts[0] in (".claude", ".codex"):
        return {"target": "claude" if parts[0] == ".claude" else "codex",
                "component": {"type": "specialist", "name": PurePath(parts[2]).stem}}
    return {"target": None, "component": {"type": "unknown", "name": None}}


def plan_fingerprint(home: Path, targets: tuple[str, ...], flags: dict,
                     changes: list[Change]) -> str:
    """Identity of exactly this batch; the single function used by dry-run and apply."""
    ops = [{"kind": c.kind, "path": c.path.relative_to(home).as_posix(),
            "newSha256": hashlib.sha256(c.content.encode("utf-8")).hexdigest(),
            "oldSha256": c.old_sha256}
           for c in changes]
    return plan_identity.fingerprint(home, targets, flags, ops)


def _print_plan(changes: list[Change], apply: bool) -> None:
    for change in changes:
        mode = "APPLY" if apply else "DRY-RUN"
        digest = (f" sha256={hashlib.sha256(change.content.encode('utf-8')).hexdigest()}"
                  if change.kind == "APPROVED-CREATE" else "")
        print(f"{mode} {change.kind} {change.path}{digest}")


def _apply_bound(args, home: Path, targets: tuple[str, ...], report: TransactionReport,
                 planned: list[Change], plan_info: dict) -> str:
    """Apply only the reviewed batch identified by ``--expect-plan``.

    1. Network-dependent inputs (approved skills) are fetched and verified
       BEFORE the lock, once, and reused.
    2. Best-effort early check outside the lock: only a clean rebuild that
       differs rejects (``stale-plan``) without creating the lock file. It is
       skipped when a transaction workspace exists and its errors are ignored,
       because another process may be mid-transaction; the locked check below
       is authoritative (``lock-busy``, ``transaction-pending``, ...).
    3. Under the exclusive lock (after the pending-transaction check) the plan
       is rebuilt from disk and its fingerprint compared again; only an exact
       match proceeds to the transactional writes.
    A rebuild that raises keeps its specific code; a rebuild that succeeds but
    differs is ``stale-plan``. Either way nothing is written.
    """
    expected = args.expect_plan
    flags = _flags(args)
    approved = approved_candidates(targets) if args.approved_skills else None

    def bound_plan() -> list[Change]:
        # Never report a previous build's plan if this rebuild fails.
        planned[:] = []
        plan_info.update(fingerprint=None, matched=None)
        changes = build_plan(
            home, targets,
            upgrade_skills=args.upgrade_skills,
            include_specialists=args.specialists,
            include_approved_skills=args.approved_skills,
            approved=approved,
        )
        planned[:] = changes
        current = plan_fingerprint(home, targets, flags, changes)
        plan_info.update(fingerprint=current, matched=current == expected)
        if current != expected:
            raise SyncodeError(
                "Plan changed since it was reviewed; no files were written. "
                "Review the current plan and confirm it again before applying.",
                code="stale-plan",
            )
        return changes

    if not _workspace_present(home):
        try:
            bound_plan()  # early rejection, no lock file created
        except SyncodeError as early:
            if early.code == "stale-plan":
                raise
            # Possibly another process mid-transaction: decide under the lock.
        except (OSError, UnicodeError, ValueError):
            pass

    def locked_plan() -> list[Change]:
        changes = bound_plan()
        if changes:
            _print_plan(changes, apply=True)
        return changes

    changes = apply_planned_transaction(locked_plan, home, verify_change, write_with_backup,
                                        ensure_safe, report=report)
    if not changes:
        print("NO CHANGES")
        return "unchanged"
    print(f"Applied {len(changes)} file change(s) transactionally.")
    return "applied"


def _run_operation(args, home: Path, targets: tuple[str, ...], report: TransactionReport,
                   planned: list[Change], plan_info: dict | None = None) -> str:
    """Keep legacy text output and the safe sync workflow unchanged.

    ``planned`` receives the plan before any write so a failed apply can still
    report what was attempted; ``report.outcome`` describes the destinations;
    ``plan_info`` receives the plan identity.
    """
    plan_info = {} if plan_info is None else plan_info
    if getattr(args, "expect_plan", None) is not None:
        return _apply_bound(args, home, targets, report, planned, plan_info)
    if args.recover:
        # Until recovery finishes, a preserved journal means the destinations
        # may be partially applied.
        if _workspace_present(home):
            report.outcome = "recovery-pending"
        recovered = recover(home, ensure_safe, report)
        report.outcome = recovered or "not-started"
        print("RECOVERED" if recovered else "NO PENDING TRANSACTION")
        return "recovered" if recovered else "no-pending-transaction"

    if (home / WORK_DIR).exists() or is_link_or_reparse(home / WORK_DIR):
        raise TransactionError(
            "Unfinished installation transaction; inspect and run sync.py --recover "
            "before planning new changes",
            code="transaction-pending",
        )
    changes = build_plan(
        home, targets,
        upgrade_skills=args.upgrade_skills,
        include_specialists=args.specialists,
        include_approved_skills=args.approved_skills,
    )
    planned[:] = changes
    plan_info["fingerprint"] = plan_fingerprint(home, targets, _flags(args), changes)
    if not changes:
        print("NO CHANGES")
        return "unchanged"
    _print_plan(changes, apply=args.apply)
    if args.apply:
        apply_transaction(changes, home, verify_change, write_with_backup, ensure_safe,
                          report=report)
        print(f"Applied {len(changes)} file change(s) transactionally.")
        return "applied"
    print("Preview only; nothing changed. Use --apply after review.")
    return "planned"


def _parser(json_errors: bool) -> ContractArgumentParser:
    parser = ContractArgumentParser(description=__doc__, json_errors=json_errors)
    parser.add_argument("--target", choices=("both", "claude", "codex"), default="both")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="Write files explicitly")
    action.add_argument("--recover", action="store_true", help="Explicitly recover interrupted install")
    action.add_argument(
        "--preview", action="store_true",
        help="Explicit read-only plan (same as the default dry-run); rejected together "
             "with --apply or --recover so a preview can never become a write",
    )
    parser.add_argument(
        "--specialists", action="store_true",
        help="Include trusted first-party specialist definitions (dry-run unless --apply)",
    )
    parser.add_argument(
        "--upgrade-skills", action="store_true",
        help="Allow reviewed upgrades only when installed skill exactly matches v0.1 baseline",
    )
    parser.add_argument(
        "--approved-skills", action="store_true",
        help="Include manually approved standalone third-party skills; dry-run unless --apply",
    )
    parser.add_argument(
        "--expect-plan", metavar="FINGERPRINT", default=None,
        help="With --apply: apply only if the current plan has exactly this fingerprint "
             "(from a reviewed dry-run); otherwise reject with stale-plan and write nothing",
    )
    # Resolved inside the error-handled section so an unusable home is reported
    # through the contract instead of a traceback.
    parser.add_argument("--home", type=Path, default=None,
                        help="Override home for testing (default: user home)")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Machine-readable JSON contract for plans, results and errors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    json_mode = wants_json(argv)
    home: Path | None = None
    targets: tuple[str, ...] = ()
    planned: list[Change] = []
    report = TransactionReport()
    plan_info: dict = {"fingerprint": None, "expected": None, "matched": None}
    status = "error"
    error: BaseException | None = None
    buffer = io.StringIO()
    try:
        parser = _parser(json_mode)
        args = parser.parse_args(argv)
        json_mode = args.format == "json"
        if args.expect_plan is not None:
            # Usage errors: checked before any filesystem access.
            if not args.apply:
                parser.error("--expect-plan requires --apply")
            if not plan_identity.is_well_formed(args.expect_plan):
                parser.error("--expect-plan must be a plan fingerprint "
                             f"({plan_identity.PREFIX}<64 hex>)")
            plan_info["expected"] = args.expect_plan
        targets = ("claude", "codex") if args.target == "both" else (args.target,)
        home = resolve_home(args.home)
        # Capture any installer/backups progress so JSON stdout is one complete
        # object even on a failed or interrupted transaction. Default text
        # output is unchanged for CLI users and existing scripts.
        context = redirect_stdout(buffer) if json_mode else nullcontext()
        with context:
            status = _run_operation(args, home, targets, report, planned, plan_info)
    except (OSError, UnicodeError, ValueError, ArgumentContractError) as failure:
        error = failure
        if not json_mode:
            diagnostic(f"ERROR: {failure}")
    except KeyboardInterrupt as failure:
        if not json_mode:
            raise
        error = failure
    except Exception as failure:  # noqa: BLE001 - contract boundary
        if not json_mode:
            raise
        error = failure
        diagnostic(f"ERROR: unexpected {type(failure).__name__}; rerun without --format json "
              "for a traceback")

    if json_mode:
        if error is not None:
            status = "error"
        emit_json(envelope(
            status,
            home=None if home is None else str(home),
            targets=list(targets),
            changes=[
                {"kind": change.kind,
                 "path": change.path.relative_to(home).as_posix(),
                 "sha256": hashlib.sha256(change.content.encode("utf-8")).hexdigest(),
                 **describe_change(change.path.relative_to(home).as_posix(), change.kind)}
                for change in planned
            ] if home is not None else [],
            events=buffer.getvalue().splitlines(),
            transaction={
                "outcome": report.outcome,
                "recoveryRequired": None if home is None else _workspace_present(home),
            },
            plan={"algorithm": plan_identity.ALGORITHM, **plan_info},
            error=None if error is None else error_object(error),
        ))
    if isinstance(error, KeyboardInterrupt):
        return 130
    return 1 if error is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
