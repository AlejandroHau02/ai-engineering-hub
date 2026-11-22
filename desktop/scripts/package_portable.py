#!/usr/bin/env python3
"""Build or verify the SYNCODE Desktop portable ZIP for Windows x64 (stdlib only).

The archive has a single versioned root folder and exactly these entries:

    SYNCODE-<version>-<commit7>-win-x64/
        syncode-desktop.exe
        README-PORTABLE.txt
        python/...     every file listed in BUNDLE_MANIFEST.json python.files
        syncode/...    every file listed in BUNDLE_MANIFEST.json files + the manifest

Nothing is globbed from the build directory, and the listing is checked
against the bundle manifest both when building and with ``--verify``. A
``SHA256SUMS`` file is written next to the ZIP. It detects corruption, not
tampering by whoever controls the distribution channel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

REPO = Path(__file__).resolve().parents[2]
EXE_NAME = "syncode-desktop.exe"
README_NAME = "README-PORTABLE.txt"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # deterministic entries
MAX_ENTRIES = 512

README = """SYNCODE Desktop {version} — paquete portable de prueba (Windows x64)
Núcleo: {commit} · Python integrado {python}

Qué es
  Diagnóstico y vista previa de sincronización de SYNCODE. Solo lectura: no
  aplica cambios, no ejecuta agentes y no se actualiza automáticamente.

Requisitos
  Windows 10 u 11 x64 con Microsoft Edge WebView2 Runtime (incluido en Windows 11).
  No necesita Python instalado ni privilegios de administrador.

Antes de abrirlo
  1. Comprueba el hash del ZIP con el SHA256SUMS publicado junto a él:
       Get-FileHash .\\{zip_name} -Algorithm SHA256
  2. Extrae el ZIP COMPLETO en una carpeta propia (no en la raíz de Descargas
     ni junto a otros programas). Ejecutar el .exe desde dentro del ZIP no
     funciona: faltarían python\\ y syncode\\.
  3. Abre {exe}.

Límites de esta versión de prueba
  - Ejecutables sin firma de código: SmartScreen o Smart App Control pueden
    advertir o bloquear su ejecución.
  - WebView2 guarda su perfil en %LOCALAPPDATA%\\io.github.syncode.desktop
    (compartido con la versión instalable).
  - Para retirarlo, borra la carpeta extraída y, si quieres, ese perfil.
"""


class PackageError(RuntimeError):
    """Packaging or verification refused (fail-closed)."""


def _manifest(resources: Path) -> dict:
    path = resources / "syncode" / "BUNDLE_MANIFEST.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackageError(f"Bundle manifest unreadable: {error}") from None
    if manifest.get("product") != "SYNCODE" or not isinstance(manifest.get("files"), dict):
        raise PackageError("Bundle manifest is not a SYNCODE desktop-runtime manifest")
    if not isinstance(manifest.get("python", {}).get("files"), dict) or not manifest["python"]["files"]:
        raise PackageError("Bundle manifest lacks the embedded Python file list; restage")
    return manifest


def _safe(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(rel) and "\\" not in rel and ":" not in rel and not rel.startswith("/") \
        and all(p not in ("", ".", "..") for p in parts)


def expected_entries(manifest: dict) -> set[str]:
    """Relative paths (inside the root folder) the ZIP must contain."""
    names = {EXE_NAME, README_NAME, "syncode/BUNDLE_MANIFEST.json"}
    for group, prefix in ((manifest["files"], "syncode/"), (manifest["python"]["files"], "python/")):
        for rel in group:
            if not _safe(rel):
                raise PackageError(f"Unsafe path in manifest: {rel!r}")
            names.add(prefix + rel)
    return names


def root_name(manifest: dict, version: str) -> str:
    commit = (manifest.get("source") or {}).get("commit") or "unknown"
    return f"SYNCODE-{version}-{commit[:7]}-win-x64"


def app_version() -> str:
    conf = json.loads((REPO / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    return conf["version"]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(exe: Path, resources: Path, out_dir: Path, allow_dirty: bool = False,
          version: str | None = None) -> Path:
    manifest = _manifest(resources)
    if (manifest.get("source") or {}).get("dirty") is not False and not allow_dirty:
        raise PackageError("Bundle was staged from uncommitted sources; restage from a clean tree")
    if not exe.is_file():
        raise PackageError(f"Executable not found: {exe}")
    version = version or app_version()
    root = root_name(manifest, version)
    zip_name = f"{root}.zip"
    readme = README.format(version=version, commit=(manifest.get("source") or {}).get("commit"),
                           python=manifest["python"].get("version"), zip_name=zip_name,
                           exe=EXE_NAME).replace("\n", "\r\n").encode("utf-8")

    sources: dict[str, Path | bytes] = {EXE_NAME: exe, README_NAME: readme}
    for rel in sorted(expected_entries(manifest) - {EXE_NAME, README_NAME}):
        path = resources.joinpath(*rel.split("/"))
        if path.is_symlink() or not path.is_file():
            raise PackageError(f"Missing or non-regular bundle file: {rel}")
        sources[rel] = path
    # Every staged file must be declared: nothing extra (e.g. a planted DLL).
    staged = {p.relative_to(resources).as_posix()
              for tree in ("python", "syncode") for p in (resources / tree).rglob("*") if p.is_file()}
    extra = staged - set(sources)
    if extra:
        raise PackageError(f"Undeclared files in the bundle: {sorted(extra)}")
    for rel, digest in list(manifest["files"].items()):
        if _sha256((resources / "syncode").joinpath(*rel.split("/")).read_bytes()) != digest:
            raise PackageError(f"Core file differs from the manifest: {rel}")
    for rel, digest in manifest["python"]["files"].items():
        if _sha256((resources / "python").joinpath(*rel.split("/")).read_bytes()) != digest:
            raise PackageError(f"Runtime file differs from the manifest: {rel}")

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / zip_name
    tmp = target.with_suffix(".zip.partial")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for rel in sorted(sources):
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            data = sources[rel] if isinstance(sources[rel], bytes) else sources[rel].read_bytes()
            bundle.writestr(info, data)
    tmp.replace(target)
    verify(target)
    (out_dir / "SHA256SUMS").write_text(f"{_sha256(target.read_bytes())}  {zip_name}\n",
                                        encoding="ascii", newline="\n")
    return target


def verify(archive: Path) -> dict:
    """Check the ZIP listing is exactly the declared set under one root folder."""
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise PackageError("Unexpected number of entries")
        roots = {info.filename.split("/", 1)[0] for info in infos}
        if len(roots) != 1:
            raise PackageError(f"Expected a single root folder, found {sorted(roots)}")
        root = roots.pop()
        names = set()
        for info in infos:
            if info.is_dir() or not _safe(info.filename):
                raise PackageError(f"Unexpected entry: {info.filename!r}")
            names.add(info.filename[len(root) + 1:])
        manifest = json.loads(bundle.read(f"{root}/syncode/BUNDLE_MANIFEST.json"))
        expected = expected_entries(manifest)
        if names != expected:
            raise PackageError(f"Listing mismatch: extra={sorted(names - expected)} "
                               f"missing={sorted(expected - names)}")
        for rel, digest in manifest["files"].items():
            if _sha256(bundle.read(f"{root}/syncode/{rel}")) != digest:
                raise PackageError(f"Core file differs from the manifest: {rel}")
        for rel, digest in manifest["python"]["files"].items():
            if _sha256(bundle.read(f"{root}/python/{rel}")) != digest:
                raise PackageError(f"Runtime file differs from the manifest: {rel}")
    return {"root": root, "entries": len(names), "source": manifest.get("source")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--exe", type=Path, required=True)
    b.add_argument("--resources", type=Path,
                   default=REPO / "desktop" / "src-tauri" / "resources")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--allow-dirty", action="store_true", help="development only")
    v = sub.add_parser("verify")
    v.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            target = build(args.exe, args.resources, args.out, args.allow_dirty)
            print(json.dumps({"zip": str(target), **verify(target)}))
        else:
            print(json.dumps(verify(args.archive)))
    except (PackageError, OSError, zipfile.BadZipFile, KeyError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
