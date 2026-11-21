"""Crash-recoverable, fail-closed batch installation for the Engineering Hub.

A transaction records *all* original bytes and expected new hashes before writing
any destination. On an ordinary error, it rolls back immediately. If interrupted
mid-operation, a later explicit --recover restores the prior state only if
destinations still match the transaction's expected old/new bytes.

There is no claim of multi-file atomic visibility or protection against hostile
concurrent local filesystem mutation; those limitations are documented.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from secrets import token_hex

from path_safety import is_link_or_reparse

WORK_DIR = ".ai-engineering-hub-transaction"
JOURNAL = "journal.json"
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_FILES = 300
MAX_ORIGINAL_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
BACKUP_RE = re.compile(r"[\w.-]+\.bak\.[0-9TZ]+-[0-9a-f]{16}\Z")
SKILL_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class TransactionError(ValueError):
    """Transaction was rejected or needs explicit recovery."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
        raise TransactionError("Transaction destination escapes home") from None
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
    if data["version"] != 1 or data["state"] not in ("preparing", "pending", "committed"):
        raise TransactionError("Invalid transaction state/version")
    if not isinstance(data["entries"], list) or len(data["entries"]) > MAX_FILES:
        raise TransactionError("Invalid transaction entry count")
    if not isinstance(data["createdDirs"], list) or len(data["createdDirs"]) > 5000:
        raise TransactionError("Invalid created directory list")
    seen = set()
    for record in data["entries"]:
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
        elif (not isinstance(snapshot, str) or not re.fullmatch(r"before-[0-9]{4}\.bin", snapshot)
              or not isinstance(backup, str) or not BACKUP_RE.fullmatch(backup)
              or not isinstance(mode, int) or not 0 <= mode <= 0o7777):
            raise TransactionError("Invalid original file metadata")
    for rel in data["createdDirs"]:
        if (not isinstance(rel, str) or not rel.startswith((".claude/", ".agents/", ".codex/"))
            or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/"))
            or rel == WORK_DIR):
            raise TransactionError("Unsafe created directory path")
    return data


def _save_journal(directory: Path, state: dict) -> None:
    raw = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_JOURNAL_BYTES:
        raise TransactionError("Transaction journal size limit exceeded")
    temp = directory / (JOURNAL + ".tmp")
    with temp.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, directory / JOURNAL)
    _flush_parent(directory)


def _snapshot(directory: Path, index: int, raw: bytes) -> str:
    if len(raw) > MAX_ORIGINAL_BYTES:
        raise TransactionError("Existing file exceeds 16 MiB transaction snapshot limit")
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
    for item in data["entries"]:
        if item["snapshot"] is None:
            continue
        dest = home / Path(item["path"])
        backup = dest.with_name(item["backup"])
        ensure_safe(backup, home)
        if backup.exists():
            raise TransactionError(f"Backup path already exists: {backup}")
        original = (directory / item["snapshot"]).read_bytes()
        if _sha(original) != item["oldSha256"]:
            raise TransactionError("Snapshot hash changed before final backup")
        # Exclusive creation protects existing user files from being replaced.
        with backup.open("xb") as target:
            target.write(original)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(backup, item["originalMode"])
        print(f"BACKUP {backup}")


def _cleanup(directory: Path) -> None:
    if is_link_or_reparse(directory) or not directory.is_dir():
        raise TransactionError("Unsafe transaction workspace")
    shutil.rmtree(directory)


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


def _rollback(directory: Path, home: Path, ensure_safe) -> None:
    data = _read_journal(directory, home, ensure_safe)
    if data["state"] == "committed":
        _cleanup(directory)
        return
    if data["state"] == "preparing":
        _cleanup(directory)
        return

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
    _cleanup(directory)


def recover(home: Path, ensure_safe) -> bool:
    """Explicit recovery, never silently overwrite unknown local modifications."""
    directory = home / WORK_DIR
    ensure_safe(directory, home)
    if not directory.exists():
        return False
    _rollback(directory, home, ensure_safe)
    return True


def apply_transaction(changes, home: Path, verify_change, write_change, ensure_safe) -> None:
    """Stage preimages first; write, then commit or revert the entire batch."""
    if not changes:
        return
    if len(changes) > MAX_FILES:
        raise TransactionError(f"Refusing transaction of more than {MAX_FILES} files")
    directory = home / WORK_DIR
    ensure_safe(directory, home)
    if directory.exists():
        raise TransactionError("Unfinished transaction exists; run sync.py --recover")

    # Exclusive mkdir is the single-writer lock for this home.
    directory.mkdir(mode=0o700)
    journal = {"version": 1, "state": "preparing", "entries": [], "createdDirs": []}
    try:
        _save_journal(directory, journal)
        created = []
        for index, change in enumerate(changes):
            verify_change(change, home)
            rel = _relative(change.path, home)
            if not _allowed_relative(rel):
                raise TransactionError(f"Unsupported managed destination: {rel}")
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
        _save_journal(directory, journal)
        for change in changes:
            verify_change(change, home)
            write_change(change, home, create_backup=False)
        _backups(journal, directory, home, ensure_safe)
        journal["state"] = "committed"
        _save_journal(directory, journal)
        _cleanup(directory)
    except BaseException as original:
        # An unsuccessful preparation cannot have touched target files.
        try:
            _rollback(directory, home, ensure_safe)
        except Exception as rollback_error:
            raise TransactionError(
                f"Install interrupted ({type(original).__name__}); "
                f"rollback incomplete ({rollback_error}). "
                "Transaction preserved; run sync.py --recover after inspection."
            ) from original
        raise
