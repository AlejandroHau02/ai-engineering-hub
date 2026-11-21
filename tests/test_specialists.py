"""Specialist catalog, native adapters and safe opt-in installation tests."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync.py"
sys.path.insert(0, str(ROOT / "scripts"))
from specialists import read_catalog, files_for_target, render_claude, render_codex  # noqa: E402


class SpecialistsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def sync(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--home", str(self.home), *args],
            text=True, capture_output=True, check=False,
        )

    def test_catalog_has_four_unique_known_skills(self):
        entries = read_catalog()
        self.assertEqual(len(entries), 4)
        self.assertEqual(len({x["name"] for x in entries}), 4)
        self.assertEqual(
            {x["skill"] for x in entries},
            {"ux-ui", "backend-engineering", "architecture-review", "code-review"},
        )

    def test_native_codex_files_are_valid_toml(self):
        for relative, rendered in files_for_target("codex").items():
            self.assertEqual(relative.suffix, ".toml")
            data = tomllib.loads(rendered)
            self.assertEqual(data["name"], relative.stem)
            self.assertIn("Analyze only", data["developer_instructions"])
            self.assertNotIn("model", data)
            self.assertEqual(
                set(data), {"name", "description", "developer_instructions"}
            )

    def test_claude_agents_are_read_only_and_preload_correct_skills(self):
        entries = read_catalog()
        for entry in entries:
            rendered = render_claude(entry)
            self.assertIn(f"name: {entry['name']}", rendered)
            self.assertIn("tools: Read, Glob, Grep", rendered)
            self.assertIn("permissionMode: plan", rendered)
            self.assertIn(f"  - {entry['skill']}", rendered)
            self.assertNotIn("tools: Bash", rendered)
            self.assertNotIn("tools: Write", rendered)

    def test_renderer_stable(self):
        entry = read_catalog()[0]
        self.assertEqual(render_claude(entry), render_claude(entry))
        self.assertEqual(render_codex(entry), render_codex(entry))

    def test_ordinary_sync_does_not_create_specialists(self):
        result = self.sync("--target", "both", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".claude/agents").exists())
        self.assertFalse((self.home / ".codex/agents").exists())

    def test_specialist_dry_run_writes_nothing(self):
        result = self.sync("--target", "both", "--specialists")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("DRY-RUN CREATE"), 18)
        self.assertIn(".claude", result.stdout)
        self.assertIn(".codex", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_apply_installs_eight_and_is_idempotent(self):
        applied = self.sync("--target", "both", "--specialists", "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(len(list((self.home / ".claude/agents").glob("*.md"))), 4)
        self.assertEqual(len(list((self.home / ".codex/agents").glob("*.toml"))), 4)
        again = self.sync("--target", "both", "--specialists")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("NO CHANGES", again.stdout)

    def test_custom_agent_conflict_blocks_all_writes(self):
        path = self.home / ".codex/agents/hub-ux-auditor.toml"
        path.parent.mkdir(parents=True)
        path.write_text("Custom agent owned by user", encoding="utf-8")
        result = self.sync("--target", "both", "--specialists", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Specialist conflict", result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), "Custom agent owned by user")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertFalse((self.home / ".claude/agents").exists())

    def test_symlink_agent_blocks_all_writes(self):
        original = self.home / "personal.toml"
        original.write_text("personal", encoding="utf-8")
        path = self.home / ".codex/agents/hub-code-reviewer.toml"
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(original)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privileges unavailable")
        result = self.sync("--target", "both", "--specialists", "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(original.read_text(), "personal")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_target_claude_does_not_install_codex(self):
        result = self.sync("--target", "claude", "--specialists", "--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".claude/agents/hub-code-reviewer.md").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_reject_duplicate_names(self):
        entries = json.loads((ROOT / "specialists/catalog.json").read_text())
        entries["specialists"].append(dict(entries["specialists"][0]))
        invalid = self.home / "invalid.json"
        invalid.write_text(json.dumps(entries), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate specialist"):
            read_catalog(invalid)

    def test_reject_catalog_path_escape(self):
        entries = json.loads((ROOT / "specialists/catalog.json").read_text())
        entries["specialists"][0]["name"] = "../unsafe"
        invalid = self.home / "invalid.json"
        invalid.write_text(json.dumps(entries), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid specialist name"):
            read_catalog(invalid)


if __name__ == "__main__":
    unittest.main()
