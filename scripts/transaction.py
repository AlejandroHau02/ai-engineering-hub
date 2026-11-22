"""Crash-recoverable, fail-closed batch installation for the Engineering Hub.

A transaction records *all* original bytes and expected new hashes before writing
any destination. On an ordinary error, it rolls back immediately. If interrupted
mid-operation, a later explicit --recover restores the prior state only if
destinations still match the transaction's expected old/new bytes.

There is no claim of multi-file atomic visibility or protection against hostile
concurrent local filesystem mutation; those limitations are documented.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
from datetime import datetime, timezone
from secrets import token_hex

from cli_contract import SyncodeError
from path_safety import is_link_or_reparse

WORK_DIR = ".ai-engineering-hub-transaction"
LOCK_FILE = ".ai-engineering-hub.sync.lock"
LOCK_MARKER = b"AI_ENGINEERING_HUB_SYNC_LOCK_v1\n"
JOURNAL = "journal.json"
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_FILES = 300
MAX_ORIGINAL_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
BACKUP_RE = re.compile(r"[\w.-]+\.bak\.[0-9TZ]+-[0-9a-f]{16}\Z")
SKILL_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class TransactionError(SyncodeError):
    """Transaction was rejected or needs explicit recovery.

    Still a ValueError. The default contract code covers journal/snapshot
    validation during rollback or recovery; other failures pass a code.
    """

    default_code = "recovery-blocked"


# Outcome of the destination files for one transaction (see the CLI contract):
#   not-started      no destination was modified by this attempt;
#   rolled-back      destinations may have been written and were restored;
#   committed        every destination was written and verified, and the
#                    committed journal was recorded (the commit point);
#   recovery-pending destinations may be partially applied; the journal is
#                    preserved and `sync.py --recover` is required;
#   unknown          (normally recovery only) the workspace held no journal and no
#                    recovery data; nothing was restored and the earlier
#                    result cannot be determined from the workspace.
OUTCOMES = ("not-started", "rolled-back", "committed", "recovery-pending", "unknown")
CLEANUP_RETRY_DELAY = 0.25
# "rolled-back" is terminal: destinations were restored and only the workspace
# cleanup remains (its snapshots may already be gone).
JOURNAL_STATES = ("preparing", "pending", "committed", "rolled-back")


def _describe(error: BaseException) -> str:
    """Error detail safe for contract messages; hide unexpected exception text."""
    if isinstance(error, (OSError, SyncodeError)):
        return str(error)
    return type(error).__name__


@dataclass
class TransactionReport:
    """Mutable outcome holder, filled in even when an exception propagates."""

    outcome: str = "not-started"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def exclusive_home_lock(home: Path, ensure_safe):
    """Serialize applying and recovering across independent Python processes.

    Keep a fixed, validated lock file in home so a process killed mid-write
    releases its OS advisory lock automatically. Never unlink the lock path:
    unlinking could let two processes lock different inodes.
    """
    lock_path = home / LOCK_FILE
    ensure_safe(lock_path, home)
    flags = (os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_BINARY", 0))
    # This file deliberately persists, including after a clean installation.
    fd = os.open(lock_path, flags, 0o600)
    acquired = False
    try:
        ensure_safe(lock_path, home)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise TransactionError(
                "Installation lock is not a regular file",
                code="lock-unexpected",
            )
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise TransactionError(
                    "Another installation/recovery is active; do not recover concurrently",
                    code="lock-busy",
                ) from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise TransactionError(
                    "Another installation/recovery is active; do not recover concurrently",
                    code="lock-busy",
                ) from None
        acquired = True
        size = os.fstat(fd).st_size
        if size == 0:
            os.write(fd, LOCK_MARKER)
            os.fsync(fd)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(LOCK_MARKER) + 1) != LOCK_MARKER:
                raise TransactionError(
                    f"Unexpected existing installation lock contents: {lock_path}",
                    code="lock-unexpected",
                )
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _flush_parent(directory: Path) -> None:
    """Durably flush directories where POSIX permits; Windows may not allow it."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _relative(path: Path, home: Path) -> str:
    try:
        relative = path.relative_to(home)
    except ValueError:
        raise TransactionError("Transaction destination escapes home", code="unsafe-path") from None
    return relative.as_posix()


def _allowed_relative(value: str) -> bool:
    """Do not trust journal path strings while recovering."""
    if not isinstance(value, str) or "\\" in value or not value or "%" in value:
        return False
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in value.split("/")):
        return False
    if parts in ((".claude", "CLAUDE.md"), (".codex", "AGENTS.md")):
        return True
    if len(parts) == 4 and parts[0:2] in (
        (".claude", "skills"), (".agents", "skills")
    ):
        return parts[3] == "SKILL.md" and bool(SKILL_RE.fullmatch(parts[2]))
    if len(parts) == 3 and parts[0:2] in (
        (".claude", "agents"), (".codex", "agents")
    ):
        extension = ".md" if parts[0] == ".claude" else ".toml"
        filename = parts[2]
        return filename.endswith(extension) and bool(AGENT_RE.fullmatch(filename))
    return False


def _read_journal(directory: Path, home: Path, ensure_safe) -> dict:
    ensure_safe(directory / JOURNAL, home)
    journal = directory / JOURNAL
    if not journal.is_file():
        raise TransactionError(f"Incomplete transaction directory without journal: {directory}. "
                               "Inspect it manually before removal.")
    raw = journal.read_bytes()
    if len(raw) > MAX_JOURNAL_BYTES:
        raise TransactionError("Transaction journal exceeds size limit")
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError):
        raise TransactionError("Invalid transaction journal") from None
    if not isinstance(data, dict) or set(data) != {"version", "state", "entries", "createdDirs"}:
        raise TransactionError("Invalid transaction journal fields")
    if type(data["version"]) is not int or data["version"] != 1 or data["state"] not in JOURNAL_STATES:
        raise TransactionError("Invalid transaction state/version")
    if not isinstance(data["entries"], list) or len(data["entries"]) > MAX_FILES:
        raise TransactionError("Invalid transaction entry count")
    if not isinstance(data["createdDirs"], list) or len(data["createdDirs"]) > 5000:
        raise TransactionError("Invalid created directory list")
    seen = set()
    for index, record in enumerate(data["entries"]):
        if not isinstance(record, dict) or set(record) != (
            {"path", "oldSha256", "newSha256", "snapshot", "backup", "originalMode"}
        ):
            raise TransactionError("Invalid transaction entry structure")
        rel = record["path"]
        if not _allowed_relative(rel) or rel in seen:
            raise TransactionError("Unsafe or duplicate transaction destination")
        seen.add(rel)
        if not isinstance(record["newSha256"], str) or not HEX64.fullmatch(record["newSha256"]):
            raise TransactionError("Invalid new-file hash")
        old = record["oldSha256"]
        if old is not None and (not isinstance(old, str) or not HEX64.fullmatch(old)):
            raise TransactionError("Invalid old-file hash")
        snapshot, backup, mode = (record["snapshot"], record["backup"], record["originalMode"])
        if old is None:
            if snapshot is not None or backup is not None or mode is not None:
                raise TransactionError("New file unexpectedly has a backup")
        elif (snapshot != f"before-{index:04d}.bin"
              or not isinstance(backup, str) or not BACKUP_RE.fullmatch(backup)
              or not backup.startswith(Path(rel).name + ".bak.")
              or type(mode) is not int or not 0 <= mode <= 0o7777):
            raise TransactionError("Invalid original file metadata")
    if any(not isinstance(name, str) for name in data["createdDirs"]):
        raise TransactionError("Invalid created directory record")
    if len(data["createdDirs"]) != len(set(data["createdDirs"])):
        raise TransactionError("Duplicate directory records")
    for rel in data["createdDirs"]:
        if (not isinstance(rel, str) or rel.split("/")[0] not in (".claude", ".agents", ".codex")
            or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/"))
            or rel == WORK_DIR):
            raise TransactionError("Unsafe created directory path")
        if not any(item_path.startswith(rel + "/") for item_path in seen):
            raise TransactionError("Created directory is unrelated to managed changes")
    return data


def _save_journal(directory: Path, state: dict) -> None:
    raw = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_JOURNAL_BYTES:
        raise TransactionError("Transaction journal size limit exceeded", code="transaction-limit")
    temp = directory / (JOURNAL + ".tmp")
    # A stale temp file can only come from an interrupted earlier save in this
    # workspace (always written under the home lock); never follow a link.
    if temp.exists() and not is_link_or_reparse(temp) and temp.is_file():
        temp.unlink()
    with temp.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, directory / JOURNAL)
    _flush_parent(directory)


def _snapshot(directory: Path, index: int, raw: bytes) -> str:
    if len(raw) > MAX_ORIGINAL_BYTES:
        raise TransactionError("Existing file exceeds 16 MiB transaction snapshot limit",
                               code="transaction-limit")
    name = f"before-{index:04d}.bin"
    with (directory / name).open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return name


def _new_dirs(path: Path, home: Path) -> list[str]:
    dirs = []
    parent = path.parent
    while parent != home:
        if not parent.exists():
            dirs.append(_relative(parent, home))
        parent = parent.parent
    return dirs


def _backups(data: dict, directory: Path, home: Path, ensure_safe) -> None:
    """Preserve old versions in conventional .bak.* paths on successful apply."""
    for index, item in enumerate(data["entries"]):
        if item["snapshot"] is None:
            continue
        dest = home / Path(item["path"])
        backup = dest.with_name(item["backup"])
        ensure_safe(backup, home)
        if backup.exists():
            raise TransactionError(
                f"Backup path already exists: {backup}",
                code="destination-conflict",
            )
        snapshot = directory / item["snapshot"]
        original = snapshot.read_bytes()
        if _sha(original) != item["oldSha256"]:
            raise TransactionError(
                "Snapshot hash changed before final backup",
                code="integrity-mismatch",
            )
        # Write a complete, separate copy in the workspace, then publish it
        # atomically with an EXCLUSIVE hardlink. An interruption cannot leave
        # a partially written final .bak file; the original snapshot stays
        # separate and available for rollback.
        prepared = directory / f"backup-ready-{index:04d}.bin"
        with prepared.open("xb") as out:
            out.write(original)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(prepared, item["originalMode"])
        os.link(prepared, backup)
        print(f"BACKUP {backup}")


def _cleanup(directory: Path) -> None:
    """Remove the workspace, deleting the journal LAST.

    A partial failure therefore leaves either the journal (recover can redo
    the cleanup) or an empty directory (recover removes it), never recovery
    data without its journal.
    """
    if is_link_or_reparse(directory) or not directory.is_dir():
        raise TransactionError("Unsafe transaction workspace", code="unsafe-path")
    for entry in directory.iterdir():
        if entry.name == JOURNAL:
            continue
        if entry.is_dir() and not is_link_or_reparse(entry):
            shutil.rmtree(entry)
        else:
            entry.unlink()
    journal = directory / JOURNAL
    if journal.exists():
        journal.unlink()
    directory.rmdir()


def _cleanup_with_retry(directory: Path) -> None:
    """Retry once after a short pause (transient Windows scanner/indexer locks)."""
    try:
        _cleanup(directory)
    except Exception:
        time.sleep(CLEANUP_RETRY_DELAY)
        _cleanup(directory)


def _journal_state(directory: Path, home: Path, ensure_safe) -> str | None:
    try:
        return _read_journal(directory, home, ensure_safe)["state"]
    except Exception:
        return None


def _is_empty_workspace(directory: Path) -> bool:
    """No journal and nothing but an unpublished journal temp file.

    Such a workspace carries no recovery data: either the first journal was
    never recorded (no destination written yet) or a cleanup removed the
    journal last and stopped before rmdir. Removing it loses nothing.
    """
    if is_link_or_reparse(directory) or not directory.is_dir():
        return False
    entries = list(directory.iterdir())
    if any(entry.name != JOURNAL + ".tmp" for entry in entries):
        return False
    return all(
        not is_link_or_reparse(entry) and entry.is_file()
        and entry.stat().st_size <= MAX_JOURNAL_BYTES
        for entry in entries
    )


def _restore_bytes(destination: Path, raw: bytes, mode: int, home: Path, ensure_safe) -> None:
    ensure_safe(destination, home)
    parent = destination.parent
    if not parent.is_dir():
        raise TransactionError(f"Restore parent is absent or unsafe: {parent}")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, delete=False
        ) as handle:
            temp_name = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        ensure_safe(destination, home)
        os.replace(temp_name, destination)
    finally:
        if temp_name is not None and temp_name.exists():
            temp_name.unlink()


def _rollback(directory: Path, home: Path, ensure_safe) -> str:
    """Finish or revert a journaled transaction; return its destination outcome."""
    ensure_safe(directory / JOURNAL, home)
    if _is_empty_workspace(directory):
        _cleanup(directory)
        return "unknown"
    data = _read_journal(directory, home, ensure_safe)
    if data["state"] == "committed":
        # Already past the commit point: never revert, only finish cleanup.
        _cleanup(directory)
        return "committed"
    if data["state"] == "preparing":
        _cleanup(directory)
        return "not-started"
    if data["state"] == "rolled-back":
        _cleanup(directory)
        return "rolled-back"

    # Verify every destination/snapshot/backup before reverting ANY files.
    # Never overwrite user modifications or act on tampered journal paths.
    actions = []
    for record in data["entries"]:
        dest = home / Path(record["path"])
        ensure_safe(dest, home)
        if record["oldSha256"] is not None:
            snapshot_path = directory / record["snapshot"]
            ensure_safe(snapshot_path, home)
            original = snapshot_path.read_bytes()
            if len(original) > MAX_ORIGINAL_BYTES or _sha(original) != record["oldSha256"]:
                raise TransactionError(f"Missing or corrupted rollback snapshot for {dest}")
        else:
            original = None
        existing = dest.read_bytes() if dest.exists() else None
        old_hash = record["oldSha256"]
        new_hash = record["newSha256"]
        if existing is None:
            if old_hash is not None:
                raise TransactionError(f"Original file disappeared; refusing rollback: {dest}")
            action = "untouched"
        else:
            current = _sha(existing)
            if old_hash is not None and current == old_hash:
                action = "untouched"
            elif current == new_hash:
                action = "restore" if old_hash is not None else "delete"
            else:
                raise TransactionError(
                    f"File has unrecognized changes; preserve journal and inspect: {dest}"
                )
        backup = (dest.with_name(record["backup"]) if record["backup"] else None)
        if backup is not None:
            ensure_safe(backup, home)
            if backup.exists() and _sha(backup.read_bytes()) != old_hash:
                raise TransactionError(f"Backup file changed; manual recovery required: {backup}")
        actions.append((record, dest, original, action, backup))

    # Roll back in reverse order. Leave the journal intact until all actions
    # succeed so a failure can be retried safely using --recover.
    for record, dest, original, action, backup in reversed(actions):
        if action == "delete":
            ensure_safe(dest, home)
            if _sha(dest.read_bytes()) != record["newSha256"]:
                raise TransactionError(f"Destination changed during rollback: {dest}")
            dest.unlink()
        elif action == "restore":
            if _sha(dest.read_bytes()) != record["newSha256"]:
                raise TransactionError(f"Destination changed during rollback: {dest}")
            _restore_bytes(dest, original, record["originalMode"], home, ensure_safe)
        if backup is not None and backup.exists():
            backup.unlink()
    for name in sorted(set(data["createdDirs"]), key=lambda x: len(x.split("/")), reverse=True):
        folder = home / Path(name)
        ensure_safe(folder / ".placeholder", home)
        if folder.is_dir():
            try:
                folder.rmdir()
            except OSError:
                pass  # Leave a directory if unrelated user data appeared.
    # Record the terminal state before deleting snapshots: if cleanup stops
    # midway, --recover finishes it instead of failing on missing snapshots.
    data["state"] = "rolled-back"
    _save_journal(directory, data)
    _cleanup(directory)
    return "rolled-back"


def recover(home: Path, ensure_safe) -> str | None:
    """Explicit recovery; refuse concurrent installation and unknown modifications.

    Return None when there is nothing to recover, otherwise the outcome of the
    recovered transaction ("rolled-back", "committed", "not-started" or
    "unknown" for an empty, journal-less workspace). The
    result keeps the truthiness of the former bool return value.
    """
    directory = home / WORK_DIR
    ensure_safe(directory / JOURNAL, home)
    if not directory.exists():
        return None
    with exclusive_home_lock(home, ensure_safe):
        # Recheck after acquiring the process-wide lock.
        ensure_safe(directory / JOURNAL, home)
        if not directory.exists():
            return None
        return _rollback(directory, home, ensure_safe)


def apply_transaction(changes, home: Path, verify_change, write_change, ensure_safe,
                      report: TransactionReport | None = None) -> None:
    """Exclusive, crash-recoverable application of an approved batch.

    ``report.outcome`` describes the destinations whether this returns or raises.
    """
    report = report if report is not None else TransactionReport()
    report.outcome = "not-started"
    if not changes:
        return
    with exclusive_home_lock(home, ensure_safe):
        _apply_transaction_locked(changes, home, verify_change, write_change, ensure_safe, report)


def _apply_transaction_locked(changes, home: Path, verify_change, write_change, ensure_safe,
                              report: TransactionReport | None = None):
    """Stage preimages first; write, then commit or revert the entire batch."""
    report = report if report is not None else TransactionReport()
    if not changes:
        return
    if len(changes) > MAX_FILES:
        raise TransactionError(f"Refusing transaction of more than {MAX_FILES} files",
                               code="transaction-limit")
    directory = home / WORK_DIR
    ensure_safe(directory, home)
    if directory.exists():
        raise TransactionError("Unfinished transaction exists; run sync.py --recover",
                               code="transaction-pending")

    # Exclusive mkdir is the single-writer lock for this home.
    directory.mkdir(mode=0o700)
    journal = {"version": 1, "state": "preparing", "entries": [], "createdDirs": []}
    # Phases: created -> preparing -> pending (destinations may change) -> committed.
    phase = "created"
    try:
        _save_journal(directory, journal)
        phase = "preparing"
        created = []
        for index, change in enumerate(changes):
            verify_change(change, home)
            rel = _relative(change.path, home)
            if not _allowed_relative(rel):
                raise TransactionError(
                    f"Unsupported managed destination: {rel}",
                    code="unsafe-path",
                )
            raw = change.path.read_bytes() if change.path.exists() else None
            new_bytes = change.content.encode("utf-8")
            before = _snapshot(directory, index, raw) if raw is not None else None
            backup = (f"{change.path.name}.bak."
                      f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{token_hex(8)}"
                      if raw is not None else None)
            journal["entries"].append({
                "path": rel, "oldSha256": _sha(raw) if raw is not None else None,
                "newSha256": _sha(new_bytes), "snapshot": before, "backup": backup,
                "originalMode": stat.S_IMODE(change.path.stat().st_mode) if raw is not None else None,
            })
            created.extend(_new_dirs(change.path, home))
        journal["createdDirs"] = list(dict.fromkeys(created))
        # Final preflight before recording the durable intent.
        for change in changes:
            verify_change(change, home)
        journal["state"] = "pending"
        phase = "pending"
        _save_journal(directory, journal)
        for change in changes:
            verify_change(change, home)
            write_change(change, home, create_backup=False)
        # Verify all final bytes before creating backups or marking committed.
        for item in journal["entries"]:
            destination = home / Path(item["path"])
            ensure_safe(destination, home)
            if not destination.is_file() or _sha(destination.read_bytes()) != item["newSha256"]:
                raise TransactionError(f"Post-write integrity mismatch: {destination}",
                                       code="integrity-mismatch")
        _backups(journal, directory, home, ensure_safe)
        journal["state"] = "committed"
        # The commit point is the atomic replacement inside _save_journal. A
        # later failure in that call (e.g. a POSIX directory fsync) is handled
        # below: _rollback re-reads the journal and never reverts a commit.
        _save_journal(directory, journal)
        phase = "committed"
    except BaseException as original:
        if phase == "created":
            # The first journal was never recorded and no destination was
            # written. This process created the workspace exclusively under
            # the lock, so remove it. If that fails, the workspace holds at
            # most journal.json.tmp, which --recover removes safely.
            try:
                _cleanup(directory)
            except Exception as cleanup_error:
                if not isinstance(original, Exception):
                    # Keep KeyboardInterrupt/SystemExit semantics (a bare
                    # `raise` here would re-raise the cleanup error instead).
                    raise original from cleanup_error
                raise TransactionError(
                    f"Install aborted before any write ({type(original).__name__}); "
                    f"workspace cleanup failed ({_describe(cleanup_error)}). "
                    "No destination was modified; run sync.py --recover to remove "
                    "the empty transaction workspace.",
                    code="cleanup-failed",
                ) from original
            raise
        if phase == "pending" and _journal_state(directory, home, ensure_safe) == "committed":
            # The committed journal was atomically recorded before the error
            # (e.g. a directory fsync failed): the commit point was reached.
            report.outcome = "committed"
            try:
                _cleanup_with_retry(directory)
            except Exception as cleanup_error:
                if not isinstance(original, Exception):
                    raise original from cleanup_error
                raise _cleanup_failed(cleanup_error) from original
            raise
        # Preparation cannot have touched target files; after "pending" the
        # destinations may be partially written until rollback succeeds.
        report.outcome = "recovery-pending" if phase == "pending" else "not-started"
        try:
            report.outcome = _rollback(directory, home, ensure_safe)
        except Exception as rollback_error:
            if _journal_state(directory, home, ensure_safe) == "rolled-back":
                report.outcome = "rolled-back"
                if not isinstance(original, Exception):
                    raise original from rollback_error
                raise TransactionError(
                    f"Install failed ({type(original).__name__}) and was rolled back, "
                    "but the transaction workspace could not be removed "
                    f"({_describe(rollback_error)}). Run sync.py --recover to finish cleanup.",
                    code="cleanup-failed",
                ) from original
            raise TransactionError(
                f"Install interrupted ({type(original).__name__}); "
                f"rollback incomplete ({_describe(rollback_error)}). "
                "Transaction preserved; run sync.py --recover after inspection.",
                code="recovery-required",
            ) from original
        raise

    # Past the commit point: destinations and backups are final. Never roll
    # back. Retry the workspace removal once (transient Windows file locks);
    # otherwise report the committed state with an explicit cleanup error.
    report.outcome = "committed"
    try:
        _cleanup_with_retry(directory)
    except Exception as cleanup_error:
        raise _cleanup_failed(cleanup_error) from cleanup_error


def _cleanup_failed(cleanup_error: BaseException) -> TransactionError:
    return TransactionError(
        "Changes were committed and verified, but the transaction workspace "
        f"could not be removed ({_describe(cleanup_error)}). Do not re-apply; run "
        "sync.py --recover to finish cleanup.",
        code="cleanup-failed",
    )
