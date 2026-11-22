"""Desktop-facing JSON contract: encoding, error codes and transaction outcomes.

Every test uses a temporary home. Subprocess tests also point HOME/USERPROFILE
at it so no real global Claude/Codex installation can be touched.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cli_contract  # noqa: E402
import doctor  # noqa: E402
import remove  # noqa: E402
import sync  # noqa: E402
import transaction  # noqa: E402

UNICODE_NAME = "home_测试_Привет"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ContractTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def workspace(self) -> Path:
        return self.home / transaction.WORK_DIR

    def run_json(self, main, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([*argv, "--home", str(self.home), "--format", "json"])
        text = out.getvalue()
        self.assertEqual(text.count("\n"), 1, text)  # exactly one JSON object
        return code, json.loads(text)

    def cli(self, script: str, *args, home: Path | None = None, encoding="cp1252"):
        env = dict(os.environ)
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = encoding  # simulate a legacy Windows code page
        isolated = str(home or self.home)
        env.update(HOME=isolated, USERPROFILE=isolated)
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *args],
            capture_output=True, check=False, env=env,
        )

    def existing_config(self, raw=b"Personal instructions\n") -> Path:
        config = self.home / ".claude/CLAUDE.md"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(raw)
        return config


class EncodingTests(ContractTestCase):
    """Non-ASCII paths must never break the single-object JSON contract."""

    def setUp(self):
        super().setUp()
        self.uhome = self.home / UNICODE_NAME
        self.uhome.mkdir()

    def parse(self, result):
        self.assertNotIn(b"Traceback", result.stderr, result.stderr)
        raw = result.stdout
        raw.decode("ascii")  # locale-independent bytes
        return json.loads(raw.decode("utf-8"))

    def test_doctor_json_with_unicode_home_under_cp1252(self):
        result = self.cli("doctor.py", "--target", "both", "--home", str(self.uhome),
                          "--format", "json", home=self.uhome)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.parse(result)
        self.assertEqual(Path(report["home"]).name, UNICODE_NAME)
        self.assertEqual(report["summary"]["missing"], 20)

    def test_sync_plan_and_apply_json_with_unicode_home_under_cp1252(self):
        plan = self.cli("sync.py", "--target", "claude", "--home", str(self.uhome),
                        "--format", "json", home=self.uhome)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(self.parse(plan)["status"], "planned")
        self.assertFalse((self.uhome / ".claude").exists())

        applied = self.cli("sync.py", "--target", "claude", "--apply", "--home",
                           str(self.uhome), "--format", "json", home=self.uhome)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        doc = self.parse(applied)
        self.assertEqual(doc["status"], "applied")
        self.assertEqual(doc["transaction"], {"outcome": "committed", "recoveryRequired": False})
        for change in doc["changes"]:
            path = self.uhome / change["path"]
            self.assertEqual(sha(path.read_bytes()), change["sha256"])

    def test_remove_json_with_unicode_home_under_cp1252(self):
        result = self.cli("remove.py", "--target", "claude", "--skill", "code-review",
                          "--home", str(self.uhome), "--format", "json", home=self.uhome)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = self.parse(result)
        self.assertEqual(doc["status"], "unchanged")
        self.assertEqual(Path(doc["home"]).name, UNICODE_NAME)

    def test_emit_json_is_ascii_and_survives_lone_surrogates(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli_contract.emit_json({"path": "C:/Users/\udcff/测试"})
        out.getvalue().encode("ascii")
        self.assertEqual(json.loads(out.getvalue())["path"], "C:/Users/\udcff/测试")

    def test_emit_json_write_error_redirects_real_fd_to_devnull(self):
        # Simulated closed pipe (Windows reports EINVAL) on a real descriptor:
        # the error is swallowed and the fd is replaced by devnull, so the
        # interpreter cannot fail again while flushing at shutdown.
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)

        class ClosedPipe(io.BytesIO):
            def write(self, _data):
                raise OSError(22, "Invalid argument")

        class FakeStdout(io.StringIO):
            buffer = ClosedPipe()

            def fileno(self):
                return write_end

        with redirect_stdout(FakeStdout()):
            cli_contract.emit_json({"status": "applied"})  # must not raise
        os.write(write_end, b"after")  # goes to devnull, not the pipe
        os.close(write_end)
        self.assertEqual(os.read(read_end, 16), b"")

    def test_emit_json_without_stdout_is_noop(self):
        with patch.object(sys, "stdout", None):
            cli_contract.emit_json({"status": "applied"})


class ArgumentTests(ContractTestCase):
    def test_wants_json_detects_forms_and_abbreviations(self):
        self.assertTrue(cli_contract.wants_json(["--format", "json"]))
        self.assertTrue(cli_contract.wants_json(["--format=json"]))
        self.assertTrue(cli_contract.wants_json(["--form", "json"]))
        self.assertFalse(cli_contract.wants_json(["--format", "text"]))
        self.assertFalse(cli_contract.wants_json(["--", "--format", "json"]))
        # argparse accepts any unambiguous prefix, including "--f".
        self.assertTrue(cli_contract.wants_json(["--f", "json"]))
        self.assertFalse(cli_contract.wants_json(["-f", "json"]))
        self.assertFalse(cli_contract.wants_json(["--format"]))

    def test_invalid_arguments_emit_json_error_in_every_cli(self):
        cases = [
            ("sync.py", ["--target", "bogus"]),
            ("doctor.py", ["--target", "bogus"]),
            ("remove.py", ["--target", "claude"]),  # missing required component
            ("sync.py", ["--apply", "--recover"]),
        ]
        for script, args in cases:
            with self.subTest(script=script, args=args):
                result = self.cli(script, *args, "--format", "json")
                self.assertEqual(result.returncode, 1, result.stderr)
                doc = json.loads(result.stdout)
                self.assertEqual(doc["status"], "error")
                code = doc["errorCode"] if script == "doctor.py" else doc["error"]["code"]
                self.assertEqual(code, "invalid-arguments")
                self.assertEqual(result.stderr, b"")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_abbreviated_format_option_still_gets_json_errors(self):
        result = self.cli("sync.py", "--target", "bogus", "--form=json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid-arguments")

    def test_text_mode_keeps_argparse_usage_and_exit_2(self):
        for script in ("sync.py", "doctor.py", "remove.py"):
            with self.subTest(script=script):
                result = self.cli(script, "--target", "bogus")
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"usage:", result.stderr)
                self.assertEqual(result.stdout, b"")


class ErrorCodeTests(ContractTestCase):
    def test_codes_are_documented_and_stable(self):
        contract = (ROOT / "docs/SYNCODE_CLI_CONTRACT.md").read_text(encoding="utf-8")
        for code in cli_contract.ERROR_CODES:
            self.assertIn(f"`{code}`", contract, code)
        with self.assertRaises(AssertionError):
            cli_contract.SyncodeError("x", code="not-a-documented-code")

    def test_skill_conflicts_have_distinct_codes_and_legacy_type(self):
        dest = self.home / ".claude/skills/code-review/SKILL.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("modified skill", encoding="utf-8")
        code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["type"], "ValueError")  # schemaVersion 1 value kept
        self.assertEqual(doc["error"]["code"], "skill-upgrade-available")
        self.assertEqual(doc["transaction"], {"outcome": "not-started", "recoveryRequired": False})
        code, doc = self.run_json(sync.main, "--target", "claude", "--upgrade-skills")
        self.assertEqual(doc["error"]["code"], "skill-modified")
        self.assertEqual(dest.read_text(encoding="utf-8"), "modified skill")

    def test_pending_transaction_reports_recovery_required(self):
        self.workspace().mkdir()
        code, doc = self.run_json(sync.main, "--target", "claude")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "transaction-pending")
        self.assertEqual(doc["error"]["type"], "TransactionError")
        self.assertEqual(doc["transaction"], {"outcome": "not-started", "recoveryRequired": True})

    def test_active_lock_is_lock_busy_and_writes_nothing(self):
        with transaction.exclusive_home_lock(self.home, sync.ensure_safe):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "lock-busy")
        self.assertEqual(doc["transaction"]["outcome"], "not-started")
        self.assertFalse((self.home / ".claude").exists())

    def test_unexpected_exception_is_internal_error_without_leaking_text(self):
        with patch.object(sync, "build_plan", side_effect=RuntimeError("SECRET-TOKEN-123")):
            code, doc = self.run_json(sync.main, "--target", "claude")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "internal-error")
        self.assertNotIn("SECRET-TOKEN-123", json.dumps(doc))

    def test_unexpected_exception_in_text_mode_still_raises(self):
        with patch.object(sync, "build_plan", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                sync.main(["--home", str(self.home)])

    def test_doctor_error_keeps_string_and_adds_error_code(self):
        with patch.object(doctor, "inspect", side_effect=OSError("disk unavailable")):
            code, doc = self.run_json(doctor.main, "--target", "claude")
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["error"], "disk unavailable")
        self.assertEqual(doc["errorCode"], "io-error")
        self.assertEqual(doc["targets"], ["claude"])

    def test_doctor_and_remove_internal_error_and_interrupt(self):
        cases = [
            (doctor, "inspect", ["--target", "claude"], lambda d: d["errorCode"]),
            (remove, "remove", ["--target", "claude", "--skill", "code-review"],
             lambda d: d["error"]["code"]),
        ]
        for module, name, args, code_of in cases:
            with self.subTest(module=module.__name__):
                with patch.object(module, name, side_effect=RuntimeError("SECRET-XYZ")):
                    code, doc = self.run_json(module.main, *args)
                self.assertEqual((code, code_of(doc)), (1, "internal-error"))
                self.assertNotIn("SECRET-XYZ", json.dumps(doc))
                with patch.object(module, name, side_effect=KeyboardInterrupt):
                    code, doc = self.run_json(module.main, *args)
                self.assertEqual((code, code_of(doc)), (130, "interrupted"))

    def test_unresolvable_default_home_is_io_error(self):
        out = io.StringIO()
        with patch.object(Path, "home", side_effect=RuntimeError("no home")), \
                redirect_stdout(out):
            code = sync.main(["--format", "json"])
        doc = json.loads(out.getvalue())
        self.assertEqual((code, doc["error"]["code"], doc["home"]), (1, "io-error", None))

    def test_remove_unknown_specialist_code_and_home(self):
        code, doc = self.run_json(remove.main, "--target", "claude", "--specialist", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "unknown-component")
        self.assertEqual(doc["home"], str(self.home.resolve()))


class TransactionOutcomeTests(ContractTestCase):
    """Each outcome must be verifiable from JSON and from disk."""

    def assert_outcome(self, doc, outcome, recovery_required):
        self.assertEqual(doc["transaction"],
                         {"outcome": outcome, "recoveryRequired": recovery_required})
        self.assertEqual(self.workspace().exists(), recovery_required)

    def test_first_journal_failure_is_not_started_without_workspace(self):
        config = self.existing_config()
        with patch.object(transaction, "_save_journal", side_effect=OSError("disk full")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "io-error")
        self.assert_outcome(doc, "not-started", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")
        self.assertEqual(len(doc["changes"]), 6)  # what was attempted, never applied

    def test_write_failure_is_rolled_back(self):
        config = self.existing_config()
        real = sync.write_with_backup
        calls = 0

        def failing(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected write failure")
            return real(change, home, create_backup=create_backup)

        with patch.object(sync, "write_with_backup", side_effect=failing):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assert_outcome(doc, "rolled-back", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")
        self.assertFalse((self.home / ".claude/skills").exists())

    def test_interrupt_during_writes_is_rolled_back_with_exit_130(self):
        config = self.existing_config()
        real = sync.write_with_backup
        calls = 0

        def interrupting(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return real(change, home, create_backup=create_backup)

        with patch.object(sync, "write_with_backup", side_effect=interrupting):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 130)
        self.assertEqual(doc["error"]["code"], "interrupted")
        self.assert_outcome(doc, "rolled-back", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_incomplete_rollback_is_recovery_pending_then_recoverable(self):
        config = self.existing_config()
        real = sync.write_with_backup
        calls = 0

        def failing(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            return real(change, home, create_backup=create_backup)

        with patch.object(sync, "write_with_backup", side_effect=failing), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("rollback blocked")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "recovery-required")
        self.assert_outcome(doc, "recovery-pending", True)
        self.assertNotEqual(config.read_bytes(), b"Personal instructions\n")

        # A new plan is blocked until explicit recovery.
        code, doc = self.run_json(sync.main, "--target", "claude")
        self.assertEqual(doc["error"]["code"], "transaction-pending")

        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "recovered")
        self.assert_outcome(doc, "rolled-back", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_blocked_recovery_keeps_journal_and_reports_pending(self):
        self.existing_config()
        with patch.object(sync, "write_with_backup", side_effect=OSError("fail")), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("blocked")):
            self.run_json(sync.main, "--target", "claude", "--apply")
        (self.workspace() / "before-0000.bin").write_bytes(b"tampered")
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual(code, 1)
        self.assertEqual(doc["error"]["code"], "recovery-blocked")
        self.assert_outcome(doc, "recovery-pending", True)

    def test_preparing_failure_with_stuck_workspace_is_not_started(self):
        self.existing_config()
        with patch.object(transaction, "_snapshot", side_effect=OSError("snapshot failed")), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("cleanup blocked")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "recovery-required")
        # No destination was written; only the workspace needs explicit cleanup.
        self.assert_outcome(doc, "not-started", True)
        self.assertEqual((self.home / ".claude/CLAUDE.md").read_bytes(),
                         b"Personal instructions\n")
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual(code, 0)
        self.assert_outcome(doc, "not-started", False)

    def test_failure_after_commit_point_is_reported_as_committed(self):
        config = self.existing_config()
        real_flush = transaction._flush_parent

        def fail_after_commit(directory):
            journal = json.loads((directory / transaction.JOURNAL).read_text(encoding="utf-8"))
            if journal["state"] == "committed":
                raise OSError("directory fsync failed after commit")
            return real_flush(directory)

        with patch.object(transaction, "_flush_parent", side_effect=fail_after_commit):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assert_outcome(doc, "committed", False)
        # Never reverted: every destination holds the planned bytes, backups kept.
        for change in doc["changes"]:
            self.assertEqual(sha((self.home / change["path"]).read_bytes()), change["sha256"])
        self.assertEqual(len(list(config.parent.glob("CLAUDE.md.bak.*"))), 1)

    def test_cleanup_failure_after_commit_is_safe_and_finished_by_recover(self):
        config = self.existing_config()
        with patch.object(transaction, "_cleanup", side_effect=OSError("file in use")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 1)
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "committed", True)
        self.assertEqual(len(doc["changes"]), 6)
        for change in doc["changes"]:
            self.assertEqual(sha((self.home / change["path"]).read_bytes()), change["sha256"])
        backups = list(config.parent.glob("CLAUDE.md.bak.*"))
        self.assertEqual([b.read_bytes() for b in backups], [b"Personal instructions\n"])
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "committed")

        # Recovery only finishes cleanup; it never reverts committed changes.
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "recovered")
        self.assert_outcome(doc, "committed", False)
        self.assertIn(b"ai-engineering-hub:start", config.read_bytes())
        self.assertEqual(len(list(config.parent.glob("CLAUDE.md.bak.*"))), 1)
        code, doc = self.run_json(sync.main, "--target", "claude")
        self.assertEqual(doc["status"], "unchanged")

    def test_first_journal_and_cleanup_failure_is_recoverable(self):
        self.existing_config()
        with patch.object(transaction, "_save_journal", side_effect=OSError("disk full")), \
                patch.object(transaction, "_cleanup", side_effect=OSError("in use")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "not-started", True)
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"]), (0, "recovered"))
        self.assert_outcome(doc, "unknown", False)
        self.assertEqual((self.home / ".claude/CLAUDE.md").read_bytes(),
                         b"Personal instructions\n")

    def test_interrupt_before_first_journal_keeps_exit_130(self):
        self.existing_config()
        with patch.object(transaction, "_save_journal", side_effect=KeyboardInterrupt), \
                patch.object(transaction, "_cleanup", side_effect=OSError("in use")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual((code, doc["error"]["code"]), (130, "interrupted"))
        self.assert_outcome(doc, "not-started", True)

    def test_commit_point_error_plus_cleanup_failure_is_committed(self):
        self.existing_config()
        real_flush = transaction._flush_parent

        def fail_after_commit(directory):
            journal = json.loads((directory / transaction.JOURNAL).read_text(encoding="utf-8"))
            if journal["state"] == "committed":
                raise OSError("directory fsync failed after commit")
            return real_flush(directory)

        with patch.object(transaction, "_flush_parent", side_effect=fail_after_commit), \
                patch.object(transaction, "_cleanup", side_effect=OSError("in use")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "committed", True)
        code, doc = self.run_json(sync.main, "--recover")
        self.assert_outcome(doc, "committed", False)

    def test_partial_cleanup_deletes_journal_last_and_recover_finishes(self):
        config = self.existing_config()
        # Real _cleanup: every file is removed but the final rmdir fails.
        with patch.object(Path, "rmdir", side_effect=OSError("directory in use")), \
                patch.object(transaction, "CLEANUP_RETRY_DELAY", 0):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "committed", True)
        self.assertEqual(list(self.workspace().iterdir()), [])  # no orphaned recovery data
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"]), (0, "recovered"))
        self.assert_outcome(doc, "unknown", False)
        self.assertIn(b"ai-engineering-hub:start", config.read_bytes())

    def test_cleanup_keeps_journal_until_last_file_is_removed(self):
        config = self.existing_config()
        real_unlink = Path.unlink

        def failing_unlink(path, *args, **kwargs):
            if path.name.startswith("before-"):
                raise OSError("snapshot locked")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink), \
                patch.object(transaction, "CLEANUP_RETRY_DELAY", 0):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "committed", True)
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "committed")  # journal survived the partial cleanup
        code, doc = self.run_json(sync.main, "--recover")
        self.assert_outcome(doc, "committed", False)
        self.assertIn(b"ai-engineering-hub:start", config.read_bytes())

    def test_partial_cleanup_after_rollback_stays_recoverable(self):
        config = self.existing_config()
        real_write = sync.write_with_backup
        real_unlink = Path.unlink
        calls = 0

        def failing_write(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            return real_write(change, home, create_backup=create_backup)

        def failing_unlink(path, *args, **kwargs):
            if path.name.startswith("before-"):
                raise OSError("snapshot locked")
            return real_unlink(path, *args, **kwargs)

        with patch.object(sync, "write_with_backup", side_effect=failing_write), \
                patch.object(Path, "unlink", failing_unlink):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assert_outcome(doc, "rolled-back", True)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"]), (0, "recovered"))
        self.assert_outcome(doc, "rolled-back", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_workspace_with_only_journal_temp_file_is_removed(self):
        self.workspace().mkdir()
        (self.workspace() / (transaction.JOURNAL + ".tmp")).write_bytes(b'{"partial"')
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"]), (0, "recovered"))
        self.assert_outcome(doc, "unknown", False)

    def test_journal_temp_name_as_directory_is_never_removed(self):
        fake = self.workspace() / (transaction.JOURNAL + ".tmp")
        fake.mkdir(parents=True)
        (fake / "user-data.txt").write_text("keep", encoding="utf-8")
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["error"]["code"]), (1, "recovery-blocked"))
        self.assertEqual((fake / "user-data.txt").read_text(encoding="utf-8"), "keep")

    def test_journal_less_workspace_with_data_is_never_removed(self):
        self.workspace().mkdir()
        (self.workspace() / "before-0000.bin").write_bytes(b"evidence")
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["error"]["code"]), (1, "recovery-blocked"))
        self.assert_outcome(doc, "recovery-pending", True)
        self.assertEqual((self.workspace() / "before-0000.bin").read_bytes(), b"evidence")

    def test_transient_cleanup_failure_is_retried_once(self):
        real = transaction._cleanup
        calls = 0

        def flaky(directory):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient lock")
            return real(directory)

        with patch.object(transaction, "_cleanup", side_effect=flaky):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "applied")
        self.assert_outcome(doc, "committed", False)

    def test_dry_run_and_no_pending_recovery_are_not_started(self):
        code, doc = self.run_json(sync.main, "--target", "claude")
        self.assertEqual(doc["status"], "planned")
        self.assert_outcome(doc, "not-started", False)
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual(doc["status"], "no-pending-transaction")
        self.assert_outcome(doc, "not-started", False)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
