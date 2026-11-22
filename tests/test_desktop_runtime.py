"""Tests for SYNCODE Desktop runtime staging (no network, no global writes)."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stage_runtime", REPO / "desktop" / "scripts" / "stage_runtime.py")
stage_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_runtime)


def _fake_embed(path: Path, extra: dict[str, bytes] | None = None) -> str:
    members = {"python.exe": b"MZ", "python312.dll": b"MZ", "python312.zip": b"PK",
               stage_runtime.PTH_NAME: b"python312.zip\n.\n\n#import site\n"}
    members.update(extra or {})
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StagePythonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_hash_mismatch_before_extracting(self):
        archive = self.base / "embed.zip"
        _fake_embed(archive)
        dest = self.base / "python"
        with self.assertRaises(stage_runtime.StageError):
            stage_runtime.stage_python(archive, dest)
        self.assertFalse(dest.exists())

    def test_rewrites_pth_to_fixed_relative_path_without_site(self):
        archive = self.base / "embed.zip"
        digest = _fake_embed(archive)
        stage_runtime.verify_archive(archive, digest)  # the hash check itself passes
        dest = self.base / "python"
        with mock.patch.object(stage_runtime, "verify_archive", lambda p: None):
            stage_runtime.stage_python(archive, dest)
        lines = (dest / stage_runtime.PTH_NAME).read_text(encoding="ascii").splitlines()
        self.assertEqual(lines, ["python312.zip", ".", "..\\syncode\\scripts"])
        self.assertNotIn("import site", "\n".join(lines))
        for line in lines:
            self.assertFalse(os.path.isabs(line))

    def test_rejects_unsafe_archive_entries(self):
        for bad in ("../evil.py", "/abs.py", "C:/x.py", "a/../../b.py", "..\\evil.py"):
            with self.subTest(entry=bad):
                archive = self.base / "bad.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr("python.exe", b"MZ")
                    bundle.writestr(bad, b"x")
                dest = self.base / f"out-{abs(hash(bad))}"
                with self.assertRaises(stage_runtime.StageError):
                    stage_runtime.safe_extract(archive, dest)
                self.assertFalse(dest.exists(), "nothing may be written on refusal")
                self.assertFalse((self.base / "evil.py").exists())

    def test_rejects_symlink_entries(self):
        archive = self.base / "link.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 << 16)
            bundle.writestr(info, "target")
        with self.assertRaises(stage_runtime.StageError):
            stage_runtime.safe_extract(archive, self.base / "out")

    def test_requires_expected_layout(self):
        archive = self.base / "other.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("readme.txt", b"x")
        with mock.patch.object(stage_runtime, "verify_archive", lambda p: None):
            with self.assertRaises(stage_runtime.StageError):
                stage_runtime.stage_python(archive, self.base / "python")


class StageCoreTests(unittest.TestCase):
    def test_allowlist_covers_doctor_import_graph(self):
        """Every SYNCODE module Doctor imports transitively is staged."""
        scripts = REPO / "scripts"
        local = {p.stem for p in scripts.glob("*.py")}
        seen, todo = set(), ["doctor", "sync", "catalog"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            tree = ast.parse((scripts / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in local:
                    todo.append(node.module)
                elif isinstance(node, ast.Import):
                    todo.extend(a.name for a in node.names if a.name in local)
        self.assertEqual(seen, set(stage_runtime.CORE_SCRIPTS))

    def test_skill_lists_match_installer(self):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import sync  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        self.assertEqual(tuple(sync.SKILLS), stage_runtime.SKILL_NAMES)
        baselines = sorted(p.parent.name for p in sync.BASELINES.glob("*/SKILL.md"))
        self.assertEqual(baselines, sorted(stage_runtime.BASELINE_NAMES))

    def test_stage_core_copies_allowlist_with_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "syncode"
            manifest = stage_runtime.stage_core(dest, source={"commit": "0" * 40, "dirty": False})
            copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")
                            if p.is_file() and p.name != "BUNDLE_MANIFEST.json")
            self.assertEqual(copied, sorted(stage_runtime.CORE_FILES))
            on_disk = json.loads((dest / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(on_disk, manifest)
            for relative, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256((dest / relative).read_bytes()).hexdigest(), digest)
            self.assertFalse(any(p.startswith(("tests/", "docs/", "evals/")) for p in copied))

    def test_dirty_or_unknown_sources_are_refused(self):
        for dirty in (True, None):
            with self.subTest(dirty=dirty), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(stage_runtime, "source_commit",
                                      return_value={"commit": None, "dirty": dirty}):
                with self.assertRaises(stage_runtime.StageError):
                    stage_runtime.stage(Path(tmp), archive=Path(tmp) / "missing.zip")
                self.assertEqual(list(Path(tmp).iterdir()), [])


class SwapInTests(unittest.TestCase):
    def test_failed_swap_restores_previous_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dest, work = base / "dest", base / "work"
            for root, label in ((dest, "old"), (work, "new")):
                for name in ("python", "syncode"):
                    (root / name).mkdir(parents=True)
                    (root / name / "marker").write_text(label, encoding="utf-8")
            real_replace = Path.replace

            def flaky(self, target):
                if self.parent == work and self.name == "syncode":
                    raise PermissionError("locked")
                return real_replace(self, target)

            with mock.patch.object(Path, "replace", flaky):
                with self.assertRaises(PermissionError):
                    stage_runtime._swap_in(work, dest, ("python", "syncode"))
            for name in ("python", "syncode"):
                self.assertEqual((dest / name / "marker").read_text(encoding="utf-8"), "old")

    def test_swap_replaces_both_trees(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dest, work = base / "dest", base / "work"
            (dest / "python").mkdir(parents=True)
            for name in ("python", "syncode"):
                (work / name).mkdir(parents=True)
                (work / name / "marker").write_text("new", encoding="utf-8")
            stage_runtime._swap_in(work, dest, ("python", "syncode"))
            for name in ("python", "syncode"):
                self.assertEqual((dest / name / "marker").read_text(encoding="utf-8"), "new")


STAGED = REPO / "desktop" / "src-tauri" / "resources"


@unittest.skipUnless(os.name == "nt" and (STAGED / "python" / "python.exe").is_file(),
                     "embedded runtime not staged (desktop/scripts/stage_runtime.py)")
class StagedRuntimeSmokeTests(unittest.TestCase):
    def test_isolated_doctor_from_foreign_directory(self):
        result = subprocess.run(
            [sys.executable, "-I", str(REPO / "desktop" / "scripts" / "smoke_runtime.py"),
             "--resources", str(STAGED)], capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout)
        self.assertTrue(evidence["ok"], evidence["problems"])
        self.assertFalse(evidence["repoOnPath"])


if __name__ == "__main__":
    unittest.main()
