"""Versioned JSON contract for sync CLI, with legacy text mode unchanged."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync.py"


class SyncJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_sync(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--home", str(self.home), *args],
            capture_output=True, text=True, check=False,
        )

    def test_plan_json_is_single_object_and_writes_nothing(self):
        result = self.run_sync("--target", "both", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["schemaVersion"], 1)
        self.assertEqual(doc["product"], "SYNCODE")
        self.assertEqual(doc["status"], "planned")
        self.assertEqual(doc["targets"], ["claude", "codex"])
        self.assertEqual(len(doc["changes"]), 12)
        self.assertTrue(all("sha256" in c and "path" in c for c in doc["changes"]))
        self.assertIsNone(doc["error"])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_apply_json_includes_backup_events_and_idempotent_status(self):
        config = self.home / ".claude/CLAUDE.md"
        config.parent.mkdir(parents=True)
        config.write_text("user config\n", encoding="utf-8")
        result = self.run_sync("--target", "claude", "--apply", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(len(doc["changes"]), 6)
        self.assertTrue(any(event.startswith("BACKUP ") for event in doc["events"]))
        self.assertTrue(config.read_text().startswith("user config\n"))
        again = self.run_sync("--target", "claude", "--format", "json")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["status"], "unchanged")

    def test_conflict_emits_error_object_and_no_partial_writes(self):
        dest = self.home / ".claude/skills/code-review/SKILL.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("modified skill", encoding="utf-8")
        result = self.run_sync("--apply", "--format", "json")
        self.assertEqual(result.returncode, 1)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["error"]["type"], "ValueError")
        self.assertEqual(doc["changes"], [])
        self.assertFalse((self.home / ".codex/AGENTS.md").exists())
        self.assertEqual(dest.read_text(), "modified skill")

    def test_recovery_json_status_without_pending_transaction(self):
        result = self.run_sync("--recover", "--format", "json")
        self.assertEqual(result.returncode, 0)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "no-pending-transaction")
        self.assertEqual(doc["changes"], [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_text_output_still_matches_legacy_format(self):
        result = self.run_sync("--target", "claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN CREATE", result.stdout)
        self.assertIn("Preview only; nothing changed.", result.stdout)
        self.assertFalse(result.stdout.lstrip().startswith("{"))


if __name__ == "__main__":
    unittest.main()
