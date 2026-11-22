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

    def test_really_closed_stdout_keeps_exit_code(self):
        # stdout is a real pipe whose reader is gone (EPIPE/EINVAL). The
        # command result must still decide the exit code (not CPython's 120).
        for args, expected in ((["--target", "claude"], 0),
                               (["--target", "bogus"], 1)):  # JSON invalid-arguments
            with self.subTest(args=args):
                read_end, write_end = os.pipe()
                os.close(read_end)
                try:
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/sync.py"), *args,
                         "--home", str(self.home), "--format", "json"],
                        stdout=write_end, stderr=subprocess.PIPE, check=False,
                    )
                finally:
                    os.close(write_end)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)

    def test_failed_stream_on_non_standard_fd_is_never_redirected(self):
        # A stream object bound to an arbitrary (possibly reused) descriptor
        # must not have that descriptor replaced by devnull.
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)

        class ClosedPipe(io.BytesIO):
            def write(self, _data):
                raise OSError(22, "Invalid argument")

        class FakeStdout(io.StringIO):
            buffer = ClosedPipe()

            def fileno(self):
                return write_end

        with redirect_stdout(FakeStdout()):
            cli_contract.emit_json({"status": "applied"})  # must not raise
        os.write(write_end, b"intact")
        self.assertEqual(os.read(read_end, 16), b"intact")

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


class ReviewRegressionTests(ContractTestCase):
    """Regressions for the PR #16 cross-review P2 findings."""

    def assert_outcome(self, doc, outcome, recovery_required):
        self.assertEqual(doc["transaction"],
                         {"outcome": outcome, "recoveryRequired": recovery_required})
        self.assertEqual(self.workspace().exists(), recovery_required)

    def workspace_rmdir_fails(self):
        """Fail only the final rmdir of the transaction workspace."""
        real_rmdir = Path.rmdir

        def rmdir(path, *args, **kwargs):
            if path.name == transaction.WORK_DIR:
                raise OSError("workspace directory in use")
            return real_rmdir(path, *args, **kwargs)

        return patch.object(Path, "rmdir", rmdir)

    def failing_writer(self, error, on_call=2, before_raise=None):
        real = sync.write_with_backup
        calls = 0

        def writer(change, home, *, create_backup=True):
            nonlocal calls
            calls += 1
            if calls == on_call:
                if before_raise is not None:
                    before_raise()
                raise error
            return real(change, home, create_backup=create_backup)

        return patch.object(sync, "write_with_backup", side_effect=writer)

    def no_retry_delay(self):
        return patch.object(transaction, "CLEANUP_RETRY_DELAY", 0)

    # --- P2-1: terminal outcome must survive a cleanup failure -------------

    def test_recover_committed_keeps_outcome_when_last_rmdir_fails(self):
        config = self.existing_config()
        with patch.object(transaction, "_cleanup", side_effect=OSError("in use")):
            self.run_json(sync.main, "--target", "claude", "--apply")
        with self.workspace_rmdir_fails(), self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"], doc["error"]["code"]), (1, "error", "cleanup-failed"))
        self.assert_outcome(doc, "committed", True)
        self.assertEqual(list(self.workspace().iterdir()), [])  # journal removed, rmdir failed
        self.assertIn(b"ai-engineering-hub:start", config.read_bytes())
        code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["status"]), (0, "recovered"))
        self.assert_outcome(doc, "unknown", False)

    def test_recover_rolled_back_keeps_outcome_when_last_rmdir_fails(self):
        config = self.existing_config()
        real_unlink = Path.unlink

        def failing_unlink(path, *args, **kwargs):
            if path.name.startswith("before-"):
                raise OSError("snapshot locked")
            return real_unlink(path, *args, **kwargs)

        with self.failing_writer(OSError("write failed")), \
                patch.object(Path, "unlink", failing_unlink), self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "rolled-back")
        with self.workspace_rmdir_fails(), self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["error"]["code"]), (1, "cleanup-failed"))
        self.assert_outcome(doc, "rolled-back", True)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_recover_pending_restores_and_keeps_rolled_back_when_last_rmdir_fails(self):
        config = self.existing_config()
        with self.failing_writer(OSError("write failed")), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("blocked")):
            self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertNotEqual(config.read_bytes(), b"Personal instructions\n")
        with self.workspace_rmdir_fails(), self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--recover")
        self.assertEqual((code, doc["error"]["code"]), (1, "cleanup-failed"))
        self.assert_outcome(doc, "rolled-back", True)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_apply_rollback_keeps_rolled_back_when_last_rmdir_fails(self):
        # Previously the journal was gone, so the outcome degraded to
        # recovery-pending/recovery-required although everything was restored.
        config = self.existing_config()
        with self.failing_writer(OSError("write failed")), self.workspace_rmdir_fails(), \
                self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual((code, doc["error"]["code"]), (1, "cleanup-failed"))
        self.assert_outcome(doc, "rolled-back", True)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_rollback_updates_report_before_cleanup_raises(self):
        self.existing_config()
        with patch.object(transaction, "_cleanup", side_effect=OSError("in use")):
            self.run_json(sync.main, "--target", "claude", "--apply")
        report = transaction.TransactionReport(outcome="recovery-pending")
        with patch.object(transaction, "_cleanup", side_effect=OSError("in use")), \
                self.no_retry_delay():
            with self.assertRaises(transaction.TransactionError) as raised:
                transaction.recover(self.home, sync.ensure_safe, report)
        self.assertEqual(raised.exception.code, "cleanup-failed")
        self.assertEqual(report.outcome, "committed")

    # --- P2-2: a closed/broken stderr must not block the JSON response -----

    def stderr_variants(self):
        class BrokenPipeStderr(io.StringIO):
            def write(self, _text):
                raise BrokenPipeError(32, "Broken pipe")

        closed = io.StringIO()
        closed.close()
        return {"broken-pipe": BrokenPipeStderr(), "closed": closed, "missing": None}

    def test_internal_error_json_survives_unusable_stderr_in_every_cli(self):
        cases = [
            (sync, "build_plan", ["--target", "claude"], lambda d: d["error"]["code"]),
            (doctor, "inspect", ["--target", "claude"], lambda d: d["errorCode"]),
            (remove, "remove", ["--target", "claude", "--skill", "code-review"],
             lambda d: d["error"]["code"]),
        ]
        for module, name, args, code_of in cases:
            for label, stderr in self.stderr_variants().items():
                with self.subTest(module=module.__name__, stderr=label):
                    with patch.object(module, name, side_effect=RuntimeError("boom")), \
                            patch.object(sys, "stderr", stderr):
                        code, doc = self.run_json(module.main, *args)  # one JSON line
                    self.assertEqual((code, code_of(doc)), (1, "internal-error"))

    def test_text_mode_error_with_unusable_stderr_keeps_exit_code(self):
        self.workspace().mkdir()  # pending transaction -> text-mode ERROR line
        for label, stderr in self.stderr_variants().items():
            with self.subTest(stderr=label):
                out = io.StringIO()
                with patch.object(sys, "stderr", stderr), redirect_stdout(out):
                    code = sync.main(["--target", "claude", "--home", str(self.home)])
                self.assertEqual(code, 1)
                self.assertEqual(out.getvalue(), "")  # never falls back to stdout

    def test_really_closed_stderr_pipe_keeps_json_and_exit_code(self):
        # A real OS pipe whose read end is closed: writes fail with EPIPE/EINVAL.
        # The exit code must stay 1 (not CPython's 120 for a failed final flush).
        cases = [
            ("sync", "build_plan", []),
            ("doctor", "inspect", []),
            ("remove", "remove", ["--target", "claude", "--skill", "code-review"]),
        ]
        for module, name, extra in cases:
            with self.subTest(module=module):
                code = (
                    "import sys; sys.path.insert(0, sys.argv[1]);"
                    "from unittest.mock import patch;"
                    f"import {module} as m;"
                    f"patch.object(m, {name!r}, side_effect=RuntimeError('boom')).start();"
                    f"raise SystemExit(m.main({extra!r} + ['--home', sys.argv[2], "
                    "'--format', 'json']))"
                )
                read_end, write_end = os.pipe()
                os.close(read_end)
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", code, str(ROOT / "scripts"), str(self.home)],
                        stdout=subprocess.PIPE, stderr=write_end, check=False,
                    )
                finally:
                    os.close(write_end)
                self.assertEqual(result.returncode, 1)
                doc = json.loads(result.stdout)
                code_value = doc["errorCode"] if module == "doctor" else doc["error"]["code"]
                self.assertEqual(code_value, "internal-error")

    def test_unencodable_diagnostic_does_not_silence_healthy_stderr(self):
        strict = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
        with patch.object(sys, "stderr", strict), \
                patch.object(cli_contract, "_redirect_to_devnull") as redirect:
            cli_contract.diagnostic("ERROR: C:\\Users\\测试")
            cli_contract.diagnostic("ERROR: plain ascii")
        redirect.assert_not_called()
        strict.flush()
        self.assertIn(b"plain ascii", strict.buffer.getvalue())

    def test_non_stream_stderr_never_raises(self):
        with patch.object(sys, "stderr", object()):
            cli_contract.diagnostic("ERROR: anything")

    # --- Follow-up from the focused review -------------------------------

    def test_preparing_failure_with_real_cleanup_failure_is_cleanup_failed(self):
        # No mock of _rollback: the real path reads the "preparing" journal,
        # records not-started, then cleanup fails.
        config = self.existing_config()
        with patch.object(transaction, "_snapshot", side_effect=OSError("snapshot failed")), \
                patch.object(transaction, "_cleanup", side_effect=OSError("in use")), \
                self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual((code, doc["error"]["code"]), (1, "cleanup-failed"))
        self.assert_outcome(doc, "not-started", True)
        self.assertIn("OSError", doc["error"]["message"])  # original cause kept
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")
        code, doc = self.run_json(sync.main, "--recover")
        self.assert_outcome(doc, "not-started", False)

    def test_rolled_back_cleanup_failure_message_names_original_failure(self):
        self.existing_config()
        with self.failing_writer(PermissionError("write denied")), \
                self.workspace_rmdir_fails(), self.no_retry_delay():
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual(doc["error"]["code"], "cleanup-failed")
        self.assertIn("PermissionError", doc["error"]["message"])
        self.assertIn("rolled back", doc["error"]["message"])

    # --- P2-3: KeyboardInterrupt must survive a failed rollback ------------

    def test_interrupt_with_failed_rollback_keeps_130_and_journal(self):
        config = self.existing_config()
        with self.failing_writer(KeyboardInterrupt()), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("rollback blocked")):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual((code, doc["status"], doc["error"]["code"]), (130, "error", "interrupted"))
        self.assert_outcome(doc, "recovery-pending", True)
        state = json.loads((self.workspace() / transaction.JOURNAL).read_text(encoding="utf-8"))
        self.assertEqual(state["state"], "pending")
        code, doc = self.run_json(sync.main, "--recover")
        self.assert_outcome(doc, "rolled-back", False)
        self.assertEqual(config.read_bytes(), b"Personal instructions\n")

    def test_interrupt_with_real_rollback_refusal_keeps_130_and_user_edit(self):
        # Rollback genuinely refuses: the first destination was edited by
        # someone else before the interrupt, so it must not be overwritten.
        config = self.existing_config()

        def external_edit():
            config.write_bytes(b"edited concurrently\n")

        with self.failing_writer(KeyboardInterrupt(), before_raise=external_edit):
            code, doc = self.run_json(sync.main, "--target", "claude", "--apply")
        self.assertEqual((code, doc["error"]["code"]), (130, "interrupted"))
        self.assert_outcome(doc, "recovery-pending", True)
        self.assertEqual(config.read_bytes(), b"edited concurrently\n")
        self.assertTrue((self.workspace() / transaction.JOURNAL).is_file())

    def test_interrupt_in_text_mode_still_raises_after_failed_rollback(self):
        self.existing_config()
        with self.failing_writer(KeyboardInterrupt()), \
                patch.object(transaction, "_rollback",
                             side_effect=transaction.TransactionError("rollback blocked")):
            with self.assertRaises(KeyboardInterrupt):
                sync.main(["--target", "claude", "--apply", "--home", str(self.home)])
        self.assertTrue(self.workspace().exists())


if __name__ == "__main__":
    unittest.main()
