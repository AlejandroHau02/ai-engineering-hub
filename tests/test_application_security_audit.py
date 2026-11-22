"""AppSec skill integration, standards references and install safety checks."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_registry import RegistryError, validate_entry  # noqa: E402
from skill_scout import frontmatter  # noqa: E402
from sync import SKILLS  # noqa: E402

SKILL_ID = "application-security-audit"
SOURCE = ROOT / "skills" / SKILL_ID / "SKILL.md"
SCRIPT = ROOT / "scripts" / "sync.py"


class AppSecSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_sync(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def test_registered_as_fifth_first_party_skill(self):
        self.assertEqual(len(SKILLS), 5)
        self.assertEqual(SKILLS.count(SKILL_ID), 1)
        self.assertEqual(frontmatter(SOURCE.read_text(encoding="utf-8"))["name"],
                         SKILL_ID)

    def test_has_explicit_review_and_freshness_boundaries(self):
        content = SOURCE.read_text(encoding="utf-8")
        metadata = frontmatter(content)
        self.assertTrue(metadata.get("description"))
        for required in (
            "authorization", "tenant", "secrets", "dependencies",
            "severity", "confidence", "regression", "offline",
            "not checked for freshness", "Do not self-modify",
            "not a background", "https://top10.owasp.org/",
            "https://asvs.owasp.org/", "https://api-security.owasp.org/",
            "https://cwe.mitre.org/top25/", "https://csrc.nist.gov/projects/ssdf",
            "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
            "https://osv.dev/",
        ):
            with self.subTest(required=required):
                self.assertIn(required.lower(), content.lower())

    def test_cannot_shadow_new_first_party_skill_as_external(self):
        with self.assertRaisesRegex(RegistryError, "shadow"):
            validate_entry({"id": SKILL_ID, "source": {}, "assessment": {}, "approval": {}})

    def test_dry_run_proposes_new_skill_for_both_clients_without_writes(self):
        result = self.run_sync("--target", "both")
        self.assertEqual(result.returncode, 0, result.stderr)
        for root in (".claude", ".agents"):
            self.assertIn(
                str(self.home / root / "skills" / SKILL_ID / "SKILL.md"),
                result.stdout,
            )
        self.assertEqual(list(self.home.iterdir()), [])

    def test_upgrade_from_four_skills_only_creates_fifth(self):
        first = self.run_sync("--target", "both", "--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        for root in (".claude", ".agents"):
            skill_dir = self.home / root / "skills" / SKILL_ID
            (skill_dir / "SKILL.md").unlink()
            skill_dir.rmdir()
        preview = self.run_sync("--target", "both")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(preview.stdout.count("DRY-RUN CREATE"), 2)
        self.assertIn(SKILL_ID, preview.stdout)
        apply = self.run_sync("--target", "both", "--apply")
        self.assertEqual(apply.returncode, 0, apply.stderr)
        self.assertEqual(apply.stdout.count("APPLY CREATE"), 2)
        second = self.run_sync("--target", "both")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("NO CHANGES", second.stdout)

    def test_custom_existing_appsec_skill_aborts_without_overwrite(self):
        dest = self.home / ".claude/skills" / SKILL_ID / "SKILL.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("my own security policy\n", encoding="utf-8")
        result = self.run_sync("--target", "both", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dest.read_text(encoding="utf-8"), "my own security policy\n")
        self.assertFalse((self.home / ".codex/AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
