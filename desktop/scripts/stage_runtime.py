#!/usr/bin/env python3
"""Stage the embedded Python runtime and SYNCODE core for SYNCODE Desktop.

Stdlib only. Produces two resource trees consumed by the Tauri bundle:

    desktop/src-tauri/resources/python/   official CPython embeddable package
    desktop/src-tauri/resources/syncode/  read-only copy of the SYNCODE core

The CPython archive is verified against a pinned SHA-256 before extraction and
its ``python312._pth`` is rewritten so ``sys.path`` is fixed and relative to the
runtime: it never depends on PYTHONPATH, PYTHONHOME, the current directory or a
global Python installation. Nothing outside the destination is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import zipfile

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DEST = REPO / "desktop" / "src-tauri" / "resources"

PYTHON_VERSION = "3.12.10"
ARCHIVE_NAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
ARCHIVE_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{ARCHIVE_NAME}"
# SHA-256 published in python.org's SPDX SBOM for this file; MD5 cross-checked
# against the 3.12.10 release page (fe8ef205f2e9c3ba44d0cf9954e1abd3).
ARCHIVE_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PTH_NAME = "python312._pth"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024

# sys.path for the embedded interpreter, relative to the ._pth file. No
# ``import site``: no site-packages, no user site, no .pth processing.
PTH_CONTENT = "python312.zip\n.\n..\\syncode\\scripts\n"

# Explicit allow-list: Doctor and its transitive imports plus the resources it
# reads. Files keep the repository layout so ``Path(__file__).resolve()
# .parents[1]`` resolves the bundled resources exactly as in a checkout.
# Entry points Desktop runs (doctor, sync, catalog) and their transitive imports.
CORE_SCRIPTS = ("doctor", "catalog", "cli_contract", "specialists", "sync", "transaction",
                "approved_installer", "path_safety", "plan_identity", "skill_registry",
                "skill_scout", "skill_scout_inventory")
SKILL_NAMES = ("architecture-review", "ux-ui", "backend-engineering", "code-review",
               "application-security-audit")
BASELINE_NAMES = ("architecture-review", "backend-engineering", "code-review", "ux-ui")
CORE_FILES = (
    tuple(f"scripts/{name}.py" for name in CORE_SCRIPTS)
    + ("core/ENGINEERING_CORE.md", "adapters/claude.md", "adapters/codex.md",
       "specialists/catalog.json", "registry/approved-skills.json")
    + tuple(f"skills/{name}/SKILL.md" for name in SKILL_NAMES)
    + tuple(f"registry/baselines/v0.1/{name}/SKILL.md" for name in BASELINE_NAMES)
)
SOURCE_DIRS = ("scripts", "core", "adapters", "skills", "specialists", "registry")


class StageError(RuntimeError):
    """Staging refused to continue (fail-closed)."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path) -> None:
    with urlopen(url, timeout=60) as response, target.open("wb") as out:  # noqa: S310 - pinned https
        total = 0
        while block := response.read(1 << 20):
            total += len(block)
            if total > MAX_ARCHIVE_BYTES:
                raise StageError("Archive exceeds size limit")
            out.write(block)


def verify_archive(path: Path, expected: str = ARCHIVE_SHA256) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise StageError(f"SHA-256 mismatch for {path.name}: {actual} != {expected}")


def safe_extract(archive: Path, dest: Path) -> list[str]:
    """Extract a zip into an empty ``dest`` after validating every entry.

    All entries are checked before anything is written: absolute, drive,
    parent or backslash paths and non-regular entries (symlinks, devices)
    abort the whole extraction.
    """
    with zipfile.ZipFile(archive) as bundle:
        members = []
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if (info.filename.startswith(("/", "\\")) or ":" in info.filename
                    or "\\" in info.filename or any(p in ("", "..") for p in name.parts)):
                raise StageError(f"Unsafe archive entry: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode not in (0, 0o100000, 0o040000):  # regular file or dir only
                raise StageError(f"Unsupported archive entry type: {info.filename!r}")
            if not info.is_dir():
                members.append((info, name))
        if dest.exists() and any(dest.iterdir()):
            raise StageError(f"Extraction target is not empty: {dest}")
        for info, name in members:
            target = dest.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as src, target.open("xb") as out:
                shutil.copyfileobj(src, out)
    return [name.as_posix() for _, name in members]


def stage_python(archive: Path, dest: Path) -> None:
    verify_archive(archive)
    names = safe_extract(archive, dest)
    if PTH_NAME not in names or "python.exe" not in names:
        raise StageError("Archive is not the expected CPython embeddable package")
    (dest / PTH_NAME).write_text(PTH_CONTENT, encoding="ascii", newline="\n")


def core_sources(repo: Path = REPO) -> list[str]:
    """Relative POSIX paths of every file copied into the ``syncode`` tree."""
    missing = [f for f in CORE_FILES if not (repo / f).is_file()]
    if missing:
        raise StageError(f"Missing core resources: {missing}")
    return list(CORE_FILES)


def source_commit(repo: Path = REPO) -> dict:
    try:
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--",
                                     *SOURCE_DIRS], check=True, capture_output=True,
                                    text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def python_hashes(python_dir: Path) -> dict[str, str]:
    """SHA-256 of every file of the staged interpreter (after the ._pth rewrite)."""
    return {p.relative_to(python_dir).as_posix(): sha256_file(p)
            for p in sorted(python_dir.rglob("*")) if p.is_file()}


def stage_core(dest: Path, repo: Path = REPO, source: dict | None = None,
               python_files: dict[str, str] | None = None) -> dict:
    hashes = {}
    for relative in core_sources(repo):
        target = dest.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / relative, target)
        hashes[relative] = sha256_file(target)
    manifest = {
        "schemaVersion": 1,
        "product": "SYNCODE",
        "component": "desktop-runtime",
        "source": source if source is not None else source_commit(repo),
        "python": {"version": PYTHON_VERSION, "archive": ARCHIVE_NAME,
                   "sha256": ARCHIVE_SHA256, "files": python_files or {}},
        "files": hashes,
    }
    (dest / "BUNDLE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def stage(dest: Path, archive: Path | None, allow_dirty: bool = False,
          repo: Path = REPO) -> dict:
    """Rebuild ``dest/python`` and ``dest/syncode`` from scratch."""
    source = source_commit(repo)
    if source["dirty"] is not False and not allow_dirty:
        raise StageError("Core sources have uncommitted changes or git is unavailable; "
                         "commit them or pass --allow-dirty (recorded in the manifest)")
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest, prefix=".stage-") as tmp:
        work = Path(tmp)
        if archive is None:
            archive = work / ARCHIVE_NAME
            download(ARCHIVE_URL, archive)
        stage_python(archive, work / "python")
        manifest = stage_core(work / "syncode", repo, source, python_hashes(work / "python"))
        _swap_in(work, dest, ("python", "syncode"))
    return manifest


def _swap_in(work: Path, dest: Path, names: tuple[str, ...]) -> None:
    """Move the new trees into place; restore the previous ones on failure.

    Existing trees are first renamed aside (inside ``work``, removed with it),
    so a locked or failing rename never leaves a mixed old/new runtime.
    """
    old = work / ".previous"
    old.mkdir()
    moved_aside, installed = [], []
    try:
        for name in names:
            if (dest / name).exists():
                (dest / name).replace(old / name)
                moved_aside.append(name)
        for name in names:
            (work / name).replace(dest / name)
            installed.append(name)
    except OSError:
        for name in installed:
            (dest / name).replace(work / name)
        for name in moved_aside:
            (old / name).replace(dest / name)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path,
                        help=f"pre-downloaded {ARCHIVE_NAME} (otherwise downloaded from python.org)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--allow-dirty", action="store_true",
                        help="development only: stage uncommitted core sources")
    args = parser.parse_args(argv)
    try:
        manifest = stage(args.dest, args.archive, args.allow_dirty)
    except (StageError, OSError, zipfile.BadZipFile) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Staged CPython {PYTHON_VERSION} and {len(manifest['files'])} SYNCODE files "
          f"into {args.dest} (source {manifest['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
