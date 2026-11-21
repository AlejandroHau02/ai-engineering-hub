"""Tests for explicit, baseline-verified personal skill upgrades."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync.py"


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_sync(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def install_v01_skill(self, skill="ux-ui", target="claude"):
        base = ROOT / "registry" / "baselines" / "v0.1" / skill / "SKILL.md"
        folder = ".claude/skills" if target == "claude" else ".agents/skills"
        destination = self.home / folder / skill / "SKILL.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = base.read_text(encoding="utf-8")
        destination.write_text(source, encoding="utf-8")
        return destination, source

    def test_default_mode_refuses_to_overwrite_v01_skill(self):
        skill, original = self.install_v01_skill()
        result = self.run_sync("--target", "claude", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--upgrade-skills", result.stderr)
        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_dry_run_upgrade_shows_change_without_writing(self):
        skill, original = self.install_v01_skill()
        result = self.run_sync("--target", "claude", "--upgrade-skills")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN UPGRADE", result.stdout)
        self.assertEqual(skill.read_text(encoding="utf-8"), original)
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertEqual(list(skill.parent.glob("SKILL.md.bak.*")), [])

    def test_upgrade_apply_backs_up_and_is_idempotent(self):
        skill, original = self.install_v01_skill()
        result = self.run_sync("--target", "claude", "--upgrade-skills", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = (ROOT / "skills" / "ux-ui" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(skill.read_text(encoding="utf-8"), updated)
        backups = list(skill.parent.glob("SKILL.md.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        again = self.run_sync("--target", "claude")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("NO CHANGES", again.stdout)

    def test_local_customization_is_not_overwritten(self):
        skill, original = self.install_v01_skill("backend-engineering", "codex")
        custom = original + "\n# personal note\n"
        skill.write_text(custom, encoding="utf-8")
        result = self.run_sync("--target", "both", "--upgrade-skills", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unrecognized or locally modified", result.stderr)
        self.assertEqual(skill.read_text(encoding="utf-8"), custom)
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertEqual(list(skill.parent.glob("SKILL.md.bak.*")), [])

    def test_upgrades_only_change_hub_managed_baselines(self):
        updated_skill, original = self.install_v01_skill("code-review")
        independent = self.home / ".claude/skills/custom-review/SKILL.md"
        independent.parent.mkdir(parents=True, exist_ok=True)
        independent.write_text("# owned by user", encoding="utf-8")
        result = self.run_sync("--target", "claude", "--upgrade-skills", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(updated_skill.read_text(encoding="utf-8"), original)
        self.assertEqual(independent.read_text(encoding="utf-8"), "# owned by user")

    def test_unchanged_skill_is_not_backed_up(self):
        same = self.home / ".claude/skills/architecture-review/SKILL.md"
        same.parent.mkdir(parents=True, exist_ok=True)
        current = (ROOT / "skills/architecture-review/SKILL.md").read_text(encoding="utf-8")
        same.write_text(current, encoding="utf-8")
        result = self.run_sync("--target", "claude", "--upgrade-skills", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(same.parent.glob("SKILL.md.bak.*")), [])


if __name__ == "__main__":
    unittest.main()
