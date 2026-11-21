"""Failure injection and interrupted-transaction recovery tests. Never touch real home."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync  # noqa: E402
import transaction  # noqa: E402


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def write_existing(self, raw=b"Personal instructions\n"):
        existing = self.home / ".claude/CLAUDE.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(raw)
        return existing

    def workspace(self):
        return self.home / transaction.WORK_DIR

    def setup_interrupted(self, original=b"original\n", updated=b"updated\n",
                          include_created=False):
        dest = self.write_existing(original)
        root = self.workspace()
        root.mkdir()
        snapshot = transaction._snapshot(root, 0, original)
        name = "CLAUDE.md.bak.20261121T120000123456Z-0123456789abcdef"
        items = [{
            "path": ".claude/CLAUDE.md",
            "oldSha256": digest(original),
            "newSha256": digest(updated),
            "snapshot": snapshot,
            "backup": name,
            "originalMode": 0o644,
        }]
        folders = []
        if include_created:
            target = self.home / ".agents/skills/new-skill/SKILL.md"
            items.append({
                "path": ".agents/skills/new-skill/SKILL.md",
                "oldSha256": None,
                "newSha256": digest(b"new skill\n"),
                "snapshot": None,
                "backup": None,
                "originalMode": None,
            })
            folders = [".agents", ".agents/skills", ".agents/skills/new-skill"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"new skill\n")
        journal = {
            "version": 1, "state": "pending",
            "entries": items, "createdDirs": folders,
        }
        transaction._save_journal(root, journal)
        dest.write_bytes(updated)
        return dest

    def test_failure_after_first_file_restores_all_originals(self):
        original = b"Preserve my personal instructions\n"
        config = self.write_existing(original)
        changes = sync.build_plan(self.home, ("claude",))
        calls = 0

        def failing_writer(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected failure after first file")
            return sync.write_with_backup(change, home, create_backup=create_backup)

        with self.assertRaisesRegex(OSError, "injected failure"):
            transaction.apply_transaction(
                changes, self.home, sync.verify_change, failing_writer, sync.ensure_safe
            )
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.home / ".claude/skills").exists())
        self.assertFalse(self.workspace().exists())
        self.assertEqual(list(self.home.rglob("*.bak.*")), [])

    def test_failure_during_backup_phase_restores_originals(self):
        original = b"Keep\n"
        config = self.write_existing(original)
        changes = sync.build_plan(self.home, ("claude",))
        with patch.object(transaction, "_backups", side_effect=OSError("backup failure")):
            with self.assertRaisesRegex(OSError, "backup failure"):
                transaction.apply_transaction(
                    changes, self.home, sync.verify_change, sync.write_with_backup,
                    sync.ensure_safe
                )
        self.assertEqual(config.read_bytes(), original)
        self.assertFalse((self.home / ".claude/skills").exists())
        self.assertFalse(self.workspace().exists())

    def test_failure_on_second_backup_restores_every_file(self):
        claude = self.write_existing(b"my Claude config\n")
        codex = self.home / ".codex/AGENTS.md"
        codex.parent.mkdir(parents=True)
        codex.write_bytes(b"my Codex config\n")
        changes = sync.build_plan(self.home, ("claude", "codex"))
        link = transaction.os.link
        counter = 0

        def interrupt_second_backup(src, dst, *args, **kwargs):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise OSError("injected second backup failure")
            return link(src, dst, *args, **kwargs)

        with patch.object(transaction.os, "link", side_effect=interrupt_second_backup):
            with self.assertRaisesRegex(OSError, "second backup failure"):
                transaction.apply_transaction(
                    changes, self.home, sync.verify_change, sync.write_with_backup,
                    sync.ensure_safe
                )
        self.assertEqual(claude.read_bytes(), b"my Claude config\n")
        self.assertEqual(codex.read_bytes(), b"my Codex config\n")
        self.assertEqual(list(self.home.rglob("*.bak.*")), [])
        self.assertFalse(self.workspace().exists())

    def test_interrupted_after_backup_publication_recovers(self):
        config = self.setup_interrupted()
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text())
        backup = config.with_name(state["entries"][0]["backup"])
        backup.write_bytes(b"original\n")
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 0)
        self.assertEqual(config.read_bytes(), b"original\n")
        self.assertFalse(backup.exists())
        self.assertFalse(self.workspace().exists())

    def test_modified_backup_causes_safe_manual_recovery(self):
        config = self.setup_interrupted()
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text())
        backup = config.with_name(state["entries"][0]["backup"])
        backup.write_bytes(b"user-modified backup\n")
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertEqual(backup.read_bytes(), b"user-modified backup\n")
        self.assertTrue(self.workspace().exists())

    def test_forged_journal_unrelated_directory_is_rejected(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["createdDirs"] = [".claude/unrelated-folder"]
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")

    def test_malformed_directory_record_is_rejected_without_traceback(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["createdDirs"] = [{"invalid": "object"}]
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertTrue(self.workspace().exists())

    def test_forged_snapshot_filename_is_rejected(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["entries"][0]["snapshot"] = "before-9999.bin"
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")

    def test_success_produces_prior_version_backup_and_no_journal(self):
        original = b"My own instructions\n"
        config = self.write_existing(original)
        changes = sync.build_plan(self.home, ("claude",))
        transaction.apply_transaction(
            changes, self.home, sync.verify_change, sync.write_with_backup,
            sync.ensure_safe
        )
        self.assertIn(b"ai-engineering-hub:start", config.read_bytes())
        backups = list(config.parent.glob("CLAUDE.md.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse(self.workspace().exists())
        self.assertEqual(sync.build_plan(self.home, ("claude",)), [])

    def test_interrupted_writes_recover_via_explicit_cli(self):
        config = self.setup_interrupted(include_created=True)
        result = sync.main(["--recover", "--home", str(self.home)])
        self.assertEqual(result, 0)
        self.assertEqual(config.read_bytes(), b"original\n")
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse(self.workspace().exists())

    def test_pending_transaction_blocks_new_plans_before_other_changes(self):
        config = self.setup_interrupted()
        self.assertEqual(sync.main(["--home", str(self.home)]), 1)
        self.assertEqual(sync.main(["--apply", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertTrue(self.workspace().exists())

    def test_external_edit_aborts_recovery_without_overwrite(self):
        config = self.setup_interrupted()
        config.write_bytes(b"user edited during downtime\n")
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"user edited during downtime\n")
        self.assertTrue(self.workspace().exists())

    def test_tampered_original_snapshot_blocks_recovery(self):
        config = self.setup_interrupted()
        (self.workspace() / "before-0000.bin").write_bytes(b"tampered")
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertTrue(self.workspace().exists())

    def test_traversal_in_journal_is_rejected(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["entries"][0]["path"] = "../unrelated-secret.txt"
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertTrue(self.workspace().exists())

    def test_committed_journal_only_cleans_workspace(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["state"] = "committed"
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 0)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertFalse(self.workspace().exists())

    def test_preparing_journal_does_not_touch_destinations(self):
        config = self.setup_interrupted()
        journal = self.workspace() / transaction.JOURNAL
        state = json.loads(journal.read_text())
        state["state"] = "preparing"
        journal.write_text(json.dumps(state))
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 0)
        self.assertEqual(config.read_bytes(), b"updated\n")
        self.assertFalse(self.workspace().exists())

    def test_no_recovery_needed_is_noop(self):
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 0)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_recovery_rejects_symlinked_workspace(self):
        outside = self.home / "outside"
        outside.mkdir()
        workspace = self.workspace()
        try:
            workspace.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable")
        self.assertEqual(sync.main(["--recover", "--home", str(self.home)]), 1)
        self.assertTrue(workspace.is_symlink())
        self.assertTrue(outside.exists())

    def test_multiple_writes_are_preflighted_before_any_commit(self):
        changes = sync.build_plan(self.home, ("claude",))
        # Create a conflict after planning, before transaction staging.
        conflict = changes[2].path
        conflict.parent.mkdir(parents=True)
        conflict.write_text("personal", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "appeared"):
            transaction.apply_transaction(
                changes, self.home, sync.verify_change, sync.write_with_backup,
                sync.ensure_safe
            )
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        self.assertEqual(conflict.read_text(), "personal")
        self.assertFalse(self.workspace().exists())


if __name__ == "__main__":
    unittest.main()
