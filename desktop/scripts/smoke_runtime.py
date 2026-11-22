#!/usr/bin/env python3
"""Isolation smoke test for the staged SYNCODE Desktop runtime (stdlib only).

Copies the staged ``python/`` and ``syncode/`` trees to a fresh directory
outside the repository, then runs the embedded interpreter exactly as the
desktop shell does (``-I -B``) from a hostile working directory with a
poisoned environment:

* PYTHONPATH / PYTHONHOME / PYTHONSTARTUP point at modules that would shadow
  ``sync``, ``json`` and ``cli_contract`` and drop a marker if imported;
* the working directory contains the same shadowing modules;
* PATH excludes every global Python installation;
* the user home is a temporary directory, so real global configuration is
  never read or written.

It asserts that ``sys.path``, the executable and ``python312.dll`` all resolve
inside the copied runtime, that Doctor returns a schemaVersion 1 report and
that no marker or ``__pycache__`` appears. Prints a JSON evidence record.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DEFAULT_RESOURCES = Path(__file__).resolve().parents[1] / "src-tauri" / "resources"
SHADOWED = ("sync", "json", "cli_contract", "doctor", "specialists", "encodings_shadow")
PROBE = r"""
import ctypes, json, sys
buf = ctypes.create_unicode_buffer(32768)
ctypes.windll.kernel32.GetModuleFileNameW(ctypes.c_void_p(sys.dllhandle), buf, 32768)
print(json.dumps({"executable": sys.executable, "prefix": sys.prefix,
                  "path": sys.path, "dll": buf.value,
                  "flags": {"isolated": sys.flags.isolated,
                            "ignore_environment": sys.flags.ignore_environment,
                            "no_user_site": sys.flags.no_user_site,
                            "dont_write_bytecode": sys.flags.dont_write_bytecode,
                            "safe_path": sys.flags.safe_path},
                  "site_imported": "site" in sys.modules}))
"""


def _poison(directory: Path, marker: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = (f"open({str(marker)!r}, 'a').write(__name__ + '\\n')\n"
               "raise SystemExit(99)\n")
    for name in SHADOWED:
        (directory / f"{name}.py").write_text(payload, encoding="utf-8")
    (directory / "startup.py").write_text(payload, encoding="utf-8")


def _clean_path() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.pathsep.join([str(Path(system_root) / "System32"), system_root])


def run(resources: Path, keep: bool = False) -> dict:
    resources = resources.resolve()
    for required in ("python/python.exe", "python/python312._pth",
                     "syncode/scripts/doctor.py", "syncode/BUNDLE_MANIFEST.json"):
        if not (resources / required).is_file():
            raise SystemExit(f"Runtime not staged: missing {required}; run stage_runtime.py")
    base = Path(tempfile.mkdtemp(prefix="syncode-smoke-"))
    try:
        app = base / "app"
        shutil.copytree(resources / "python", app / "python")
        shutil.copytree(resources / "syncode", app / "syncode")
        home = base / "home"
        home.mkdir()
        poison = base / "poison"
        marker = base / "poison-imported.txt"
        _poison(poison, marker)

        env = {
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "PATH": _clean_path(),
            "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            "USERPROFILE": str(home),
            "TEMP": str(base), "TMP": str(base),
            "PYTHONPATH": str(poison), "PYTHONHOME": str(poison),
            "PYTHONSTARTUP": str(poison / "startup.py"), "PYTHONINSPECT": "",
            "NoDefaultCurrentDirectoryInExePath": "1",
        }
        python = app / "python" / "python.exe"
        common = dict(cwd=poison, env=env, capture_output=True, timeout=60,
                      stdin=subprocess.DEVNULL)
        probe = subprocess.run([str(python), "-I", "-B", "-c", PROBE], **common)
        if probe.returncode != 0:
            raise AssertionError(f"probe failed: {probe.returncode} {probe.stderr[-400:]!r}")
        info = json.loads(probe.stdout)

        doctor = subprocess.run([str(python), "-I", "-B",
                                 str(app / "syncode" / "scripts" / "doctor.py"),
                                 "--target", "both", "--format", "json"], **common)
        report = json.loads(doctor.stdout)

        runtime_root = str(app).lower()
        problems = []
        if any(not entry.lower().startswith(runtime_root) for entry in info["path"]):
            problems.append(f"sys.path escapes runtime: {info['path']}")
        for key in ("executable", "dll", "prefix"):
            if not info[key].lower().startswith(runtime_root):
                problems.append(f"{key} outside runtime: {info[key]}")
        if not (info["flags"]["isolated"] and info["flags"]["dont_write_bytecode"]):
            problems.append(f"unexpected flags: {info['flags']}")
        if info["site_imported"]:
            problems.append("site was imported")
        if marker.exists():
            problems.append(f"poison imported: {marker.read_text()!r}")
        if doctor.returncode not in (0, 2):
            problems.append(f"doctor exit {doctor.returncode}")
        if report.get("schemaVersion") != 1 or report.get("product") != "SYNCODE":
            problems.append("contract mismatch")
        if Path(report.get("home", "")).resolve() != home.resolve():
            problems.append(f"doctor read a different home: {report.get('home')}")
        if list((app / "syncode").rglob("__pycache__")):
            problems.append("__pycache__ written into runtime")
        if list(home.iterdir()):
            problems.append("doctor wrote into the home directory")
        evidence = {
            "ok": not problems, "problems": problems,
            "runtimeOutsideRepo": str(app),
            "repoOnPath": any("ai-engineering-hub" in e.lower() for e in info["path"]),
            "probe": info,
            "doctor": {"exitCode": doctor.returncode, "summary": report.get("summary"),
                       "transaction": report.get("transaction"), "lock": report.get("lock"),
                       "clients": sorted(report.get("clients", {}))},
        }
        return evidence
    finally:
        if not keep:
            shutil.rmtree(base, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resources", type=Path, default=DEFAULT_RESOURCES)
    parser.add_argument("--keep", action="store_true", help="keep the temporary copy")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("SKIP: the embedded runtime is Windows-only", file=sys.stderr)
        return 0
    evidence = run(args.resources, args.keep)
    print(json.dumps(evidence, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
