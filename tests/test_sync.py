"""Tests for safe global installation. No real home files are changed."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync.py"


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_sync(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def test_dry_run_creates_nothing(self):
        result = self.run_sync("--target", "both")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_apply_installs_configs_and_skills(self):
        result = self.run_sync("--target", "both", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ai-engineering-hub:start", (self.home / ".claude/CLAUDE.md").read_text())
        self.assertIn("ai-engineering-hub:start", (self.home / ".codex/AGENTS.md").read_text())
        self.assertEqual(len(list((self.home / ".claude/skills").glob("*/SKILL.md"))), 5)
        self.assertEqual(len(list((self.home / ".agents/skills").glob("*/SKILL.md"))), 5)

    def test_existing_instructions_preserved_and_backed_up(self):
        config = self.home / ".claude/CLAUDE.md"
        config.parent.mkdir(parents=True)
        config.write_text("Personal instructions\n", encoding="utf-8")
        result = self.run_sync("--target", "claude", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(config.read_text().startswith("Personal instructions\n"))
        backups = list(config.parent.glob("CLAUDE.md.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Personal instructions\n")

    def test_apply_is_idempotent(self):
        self.assertEqual(self.run_sync("--target", "both", "--apply").returncode, 0)
        result = self.run_sync("--target", "both", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO CHANGES", result.stdout)
        self.assertEqual(list(self.home.rglob("*.bak.*")), [])

    def test_skill_conflict_aborts_before_any_write(self):
        skill = self.home / ".agents/skills/ux-ui/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Other skill", encoding="utf-8")
        result = self.run_sync("--target", "both", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(skill.read_text(), "Other skill")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_bad_markers_abort_before_any_write(self):
        config = self.home / ".codex/AGENTS.md"
        config.parent.mkdir(parents=True)
        config.write_text("<!-- ai-engineering-hub:start -->\n")
        result = self.run_sync("--target", "both", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_symlink_file_rejected(self):
        original = self.home / "protected.md"
        original.write_text("protected")
        config = self.home / ".claude/CLAUDE.md"
        config.parent.mkdir(parents=True)
        try:
            config.symlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("symlink permissions unavailable")
        result = self.run_sync("--target", "claude", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(original.read_text(), "protected")


if __name__ == "__main__":
    unittest.main()
