"""Read-only SYNCODE Doctor tests with synthetic home directories."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "scripts/doctor.py"
SYNC = ROOT / "scripts/sync.py"
sys.path.insert(0, str(ROOT / "scripts"))
from doctor import inspect  # noqa: E402
from transaction import WORK_DIR  # noqa: E402


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(script), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def test_empty_home_read_only_json(self):
        result = self.cli(DOCTOR, "--target", "both", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["product"], "SYNCODE")
        self.assertEqual(report["transaction"], "clear")
        self.assertEqual(report["summary"]["current"], 0)
        self.assertEqual(report["summary"]["missing"], 20)
        self.assertEqual(set(report["clients"]), {"claude", "codex"})
        self.assertEqual(list(self.home.iterdir()), [])

    def test_installed_core_and_agents_detected_without_running_cli(self):
        applied = self.cli(SYNC, "--target", "both", "--specialists", "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        report = inspect(self.home, ("claude", "codex"))
        self.assertEqual(report["summary"]["current"], 20)
        self.assertEqual(report["summary"]["blockers"], 0)
        self.assertEqual(report["lock"], "recognized")
        self.assertEqual(report["transaction"], "clear")
        self.assertTrue(all(c["instructions"]["status"] == "current"
                            for c in report["clients"].values()))

    def test_original_global_instructions_are_preserved_and_count_current(self):
        path = self.home / ".claude/CLAUDE.md"
        path.parent.mkdir()
        path.write_text("Personal instructions\n", encoding="utf-8")
        self.assertEqual(self.cli(SYNC, "--target", "claude", "--apply").returncode, 0)
        report = inspect(self.home, ("claude",))
        self.assertEqual(report["clients"]["claude"]["instructions"]["status"], "current")
        self.assertTrue(path.read_text().startswith("Personal instructions\n"))

    def test_modified_skill_is_flagged_but_content_not_reported(self):
        self.assertEqual(self.cli(SYNC, "--target", "claude", "--apply").returncode, 0)
        path = self.home / ".claude/skills/code-review/SKILL.md"
        token = "sensitive-user-marker-must-not-leak"
        path.write_text(token, encoding="utf-8")
        result = self.cli(DOCTOR, "--target", "claude", "--format", "json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["clients"]["claude"]["skills"]["code-review"]["status"],
                         "modified")
        self.assertNotIn(token, result.stdout)

    def test_unmanaged_and_malformed_global_config(self):
        path = self.home / ".codex/AGENTS.md"
        path.parent.mkdir()
        path.write_text("My custom guide", encoding="utf-8")
        report = inspect(self.home, ("codex",))
        self.assertEqual(report["clients"]["codex"]["instructions"]["status"], "unmanaged")
        path.write_text("<!-- ai-engineering-hub:start -->\n", encoding="utf-8")
        report = inspect(self.home, ("codex",))
        self.assertEqual(report["clients"]["codex"]["instructions"]["status"], "modified")
        self.assertGreater(report["summary"]["blockers"], 0)

    def test_pending_transaction_flags_recovery_without_editing_workspace(self):
        workspace = self.home / WORK_DIR
        workspace.mkdir()
        marker = workspace / "evidence"
        marker.write_text("keep", encoding="utf-8")
        result = self.cli(DOCTOR, "--target", "claude", "--format", "json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["transaction"], "pending-recovery")
        self.assertEqual(marker.read_text(), "keep")

    def test_unknown_lock_is_flagged_without_modification(self):
        lock = self.home / ".ai-engineering-hub.sync.lock"
        lock.write_text("private lock", encoding="utf-8")
        result = self.cli(DOCTOR, "--target", "claude", "--format", "json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["lock"], "unexpected")
        self.assertEqual(lock.read_text(), "private lock")

    def test_symlinked_skill_is_flagged_without_following(self):
        secret = self.home / "private.txt"
        secret.write_text("DO_NOT_READ", encoding="utf-8")
        dest = self.home / ".agents/skills/code-review/SKILL.md"
        dest.parent.mkdir(parents=True)
        try:
            dest.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        result = self.cli(DOCTOR, "--target", "codex", "--format", "json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["clients"]["codex"]["skills"]["code-review"]["status"],
            "unsafe-or-unreadable",
        )
        self.assertNotIn("DO_NOT_READ", result.stdout)


if __name__ == "__main__":
    unittest.main()
