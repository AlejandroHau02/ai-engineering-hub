"""Portable ZIP packaging for SYNCODE Desktop (synthetic inputs, no network)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_portable", REPO / "desktop" / "scripts" / "package_portable.py")
package_portable = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_portable)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.res = base / "resources"
        files = {"python/python.exe": b"MZ", "python/python312._pth": b"python312.zip\n",
                 "python/python312.dll": b"MZ", "syncode/scripts/doctor.py": b"print()\n",
                 "syncode/scripts/sync.py": b"print()\n", "syncode/skills/ux-ui/SKILL.md": b"x\n"}
        for rel, data in files.items():
            path = self.res / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.manifest = {
            "schemaVersion": 1, "product": "SYNCODE", "component": "desktop-runtime",
            "source": {"commit": "abcdef0123456789abcdef0123456789abcdef01", "dirty": False},
            "python": {"version": "3.12.10", "files": {
                rel[len("python/"):]: _sha(data) for rel, data in files.items()
                if rel.startswith("python/")}},
            "files": {rel[len("syncode/"):]: _sha(data) for rel, data in files.items()
                      if rel.startswith("syncode/")},
        }
        self._write_manifest()
        self.exe = base / "app.exe"
        self.exe.write_bytes(b"MZapp")
        self.out = base / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_manifest(self):
        (self.res / "syncode" / "BUNDLE_MANIFEST.json").write_text(
            json.dumps(self.manifest), encoding="utf-8")

    def _build(self, **kw):
        return package_portable.build(self.exe, self.res, self.out, version="9.9.9", **kw)

    def test_builds_exact_listing_under_versioned_root(self):
        target = self._build()
        self.assertEqual(target.name, "SYNCODE-9.9.9-abcdef0-win-x64.zip")
        with zipfile.ZipFile(target) as bundle:
            names = {n.split("/", 1)[1] for n in bundle.namelist()}
            self.assertEqual({n.split("/", 1)[0] for n in bundle.namelist()},
                             {"SYNCODE-9.9.9-abcdef0-win-x64"})
            readme = bundle.read("SYNCODE-9.9.9-abcdef0-win-x64/README-PORTABLE.txt").decode()
        self.assertEqual(names, {
            "syncode-desktop.exe", "README-PORTABLE.txt", "syncode/BUNDLE_MANIFEST.json",
            "python/python.exe", "python/python312._pth", "python/python312.dll",
            "syncode/scripts/doctor.py", "syncode/scripts/sync.py",
            "syncode/skills/ux-ui/SKILL.md"})
        self.assertIn("Extrae el ZIP COMPLETO", readme)
        sums = (self.out / "SHA256SUMS").read_text(encoding="ascii")
        self.assertEqual(sums, f"{_sha(target.read_bytes())}  {target.name}\n")

    def test_build_is_deterministic(self):
        first = self._build().read_bytes()
        second = self._build().read_bytes()
        self.assertEqual(_sha(first), _sha(second))

    def test_refuses_dirty_sources_unless_allowed(self):
        self.manifest["source"]["dirty"] = True
        self._write_manifest()
        with self.assertRaises(package_portable.PackageError):
            self._build()
        self.assertTrue(self._build(allow_dirty=True).is_file())

    def test_refuses_undeclared_or_altered_files(self):
        planted = self.res / "python" / "version.dll"
        planted.write_bytes(b"evil")
        with self.assertRaisesRegex(package_portable.PackageError, "Undeclared"):
            self._build()
        planted.unlink()
        (self.res / "syncode" / "scripts" / "sync.py").write_bytes(b"import os\n")
        with self.assertRaisesRegex(package_portable.PackageError, "differs"):
            self._build()

    def test_refuses_missing_files_and_unsafe_manifest_paths(self):
        (self.res / "syncode" / "scripts" / "doctor.py").unlink()
        with self.assertRaisesRegex(package_portable.PackageError, "Missing"):
            self._build()
        self.manifest["files"]["../evil.py"] = "0" * 64
        self._write_manifest()
        with self.assertRaisesRegex(package_portable.PackageError, "Unsafe"):
            self._build()

    def _rewrite(self, target: Path, mutate) -> Path:
        with zipfile.ZipFile(target) as src:
            entries = [(i.filename, src.read(i)) for i in src.infolist()]
        entries = mutate(entries)
        tampered = target.with_name("tampered.zip")
        with zipfile.ZipFile(tampered, "w") as dst:
            for name, data in entries:
                dst.writestr(name, data)
        return tampered

    def test_verify_detects_tampering(self):
        target = self._build()
        root = target.stem
        cases = {
            "extra entry": lambda e: e + [(f"{root}/python/version.dll", b"x")],
            "missing entry": lambda e: [x for x in e if not x[0].endswith("sync.py")],
            "two roots": lambda e: e + [("other/readme.txt", b"x")],
            "altered core": lambda e: [(n, b"changed" if n.endswith("doctor.py") else d) for n, d in e],
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                with self.assertRaises(package_portable.PackageError):
                    package_portable.verify(self._rewrite(target, mutate))
        self.assertEqual(package_portable.verify(target)["root"], root)


if __name__ == "__main__":
    unittest.main()
