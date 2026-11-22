"""Controlled removal tests: no live home, no network or unapproved deletions."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYNC = ROOT / "scripts/sync.py"
REMOVE = ROOT / "scripts/remove.py"
sys.path.insert(0, str(ROOT / "scripts"))
from transaction import WORK_DIR, exclusive_home_lock  # noqa: E402
from sync import ensure_safe  # noqa: E402
import remove  # noqa: E402


class RemoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_command(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def install(self, *args):
        result = self.run_command(SYNC, *args, "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)

    def remove(self, *args):
        return self.run_command(REMOVE, *args)

    def test_empty_home_dry_run_creates_no_files(self):
        result = self.remove("--target", "claude", "--skill", "code-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO CHANGES", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_preview_does_not_write_and_single_target_only(self):
        self.install("--target", "both")
        before = sorted(str(p) for p in self.home.rglob("*"))
        result = self.remove("--target", "claude", "--skill", "code-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN REMOVE", result.stdout)
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)

    def test_remove_first_party_skill_is_reversible_and_reinstallable(self):
        self.install("--target", "both")
        target = self.home / ".claude/skills/code-review"
        original = (target / "SKILL.md").read_bytes()
        result = self.remove("--target", "claude", "--skill", "code-review",
                             "--apply", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "removed")
        backup = self.home / doc["backup"]
        self.assertTrue(backup.is_dir())
        self.assertEqual((backup / "SKILL.md").read_bytes(), original)
        self.assertFalse(target.exists())
        self.assertTrue((self.home / ".agents/skills/code-review/SKILL.md").exists())
        self.assertEqual(self.run_command(SYNC, "--target", "claude", "--apply").returncode, 0)
        self.assertEqual((target / "SKILL.md").read_bytes(), original)
        self.assertTrue(backup.exists())

    def test_remove_specialist_does_not_affect_other_agents(self):
        self.install("--target", "both", "--specialists")
        target = self.home / ".codex/agents/hub-code-reviewer.toml"
        original = target.read_bytes()
        result = self.remove("--target", "codex", "--specialist", "hub-code-reviewer",
                             "--apply", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "removed")
        backup = self.home / doc["backup"]
        self.assertEqual(backup.read_bytes(), original)
        self.assertFalse(target.exists())
        self.assertTrue((self.home / ".claude/agents/hub-code-reviewer.md").exists())
        self.assertEqual(self.run_command(SYNC, "--target", "codex", "--specialists",
                                          "--apply").returncode, 0)
        self.assertEqual(target.read_bytes(), original)

    def test_user_modified_skill_refused_without_data_loss(self):
        self.install("--target", "claude")
        path = self.home / ".claude/skills/code-review/SKILL.md"
        path.write_text("My own skill", encoding="utf-8")
        result = self.remove("--target", "claude", "--skill", "code-review", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Modified or unrecognized", result.stderr)
        self.assertEqual(path.read_text(), "My own skill")
        self.assertEqual(list(path.parent.parent.glob("code-review.removed.*")), [])

    def test_extra_files_in_skill_directory_prevent_removal(self):
        self.install("--target", "claude")
        folder = self.home / ".claude/skills/code-review"
        user_data = folder / "my-notes.txt"
        user_data.write_text("keep", encoding="utf-8")
        result = self.remove("--target", "claude", "--skill", "code-review", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("additional files", result.stderr)
        self.assertTrue((folder / "SKILL.md").exists())
        self.assertEqual(user_data.read_text(), "keep")

    def test_symlinked_managed_file_is_never_followed(self):
        self.install("--target", "codex")
        path = self.home / ".agents/skills/code-review/SKILL.md"
        original = path.read_bytes()
        shadow = self.home / "private.md"
        shadow.write_bytes(original)
        path.unlink()
        try:
            path.symlink_to(shadow)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        result = self.remove("--target", "codex", "--skill", "code-review", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(path.is_symlink())
        self.assertEqual(shadow.read_bytes(), original)

    def test_pending_transaction_aborts_without_removing_file(self):
        self.install("--target", "claude")
        workspace = self.home / WORK_DIR
        workspace.mkdir()
        target = self.home / ".claude/skills/code-review/SKILL.md"
        result = self.remove("--target", "claude", "--skill", "code-review", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Pending installation transaction", result.stderr)
        self.assertTrue(target.exists())

    def test_concurrent_installation_lock_prevents_removal(self):
        self.install("--target", "claude")
        target = self.home / ".claude/skills/code-review/SKILL.md"
        args = ["--target", "claude", "--skill", "code-review"]
        parsed = self._args(args)
        candidate = remove._candidate(parsed)
        with exclusive_home_lock(self.home, ensure_safe):
            with self.assertRaisesRegex(ValueError, "active"):
                remove.remove(self.home, candidate, apply=True)
        self.assertTrue(target.exists())

    def _args(self, extras):
        from argparse import Namespace
        return Namespace(target="claude", skill="code-review",
                         specialist=None, approved_skill=None)

    def test_unregistered_approved_skill_is_refused(self):
        result = self.remove("--target", "claude",
                             "--approved-skill", "unregistered", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not in the reviewed manifest", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
