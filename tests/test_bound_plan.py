"""Bound plans (Phase 0B): apply only the batch that was reviewed.

All tests use temporary homes; subprocesses also point HOME/USERPROFILE at the
temporary home so no real Claude/Codex installation can be touched. Negative
tests snapshot every file (bytes) in home before and after the rejection.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_identity  # noqa: E402
import sync  # noqa: E402
import transaction  # noqa: E402

UNICODE_NAME = "home_测试_Привет"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def tree(home: Path, include_lock: bool = False) -> dict[str, str]:
    """Every entry under home: file -> sha256 of bytes, directory -> '<dir>'."""
    state = {}
    for path in sorted(home.rglob("*")):
        rel = path.relative_to(home).as_posix()
        if rel == transaction.LOCK_FILE and not include_lock:
            continue
        state[rel] = "<dir>" if path.is_dir() else sha(path.read_bytes())
    return state


class BoundPlanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def run_json(self, *argv, home: Path | None = None):
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.main([*argv, "--home", str(home or self.home), "--format", "json"])
        self.assertEqual(out.getvalue().count("\n"), 1, out.getvalue())
        return code, json.loads(out.getvalue())

    def plan(self, *argv, home: Path | None = None) -> str:
        code, doc = self.run_json(*argv, home=home)
        self.assertEqual(code, 0, doc)
        self.assertIn(doc["status"], ("planned", "unchanged"))
        self.assertIsNone(doc["plan"]["expected"])
        self.assertIsNone(doc["plan"]["matched"])
        self.assertTrue(plan_identity.is_well_formed(doc["plan"]["fingerprint"]))
        return doc["plan"]["fingerprint"]

    def apply(self, fingerprint: str, *argv, home: Path | None = None):
        return self.run_json(*argv, "--apply", "--expect-plan", fingerprint, home=home)

    def existing_config(self, raw=b"Personal instructions\n") -> Path:
        config = self.home / ".claude/CLAUDE.md"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_bytes(raw)
        return config

    def assert_rejected_without_writes(self, doc, code, before, lock_expected=False):
        self.assertEqual(doc["status"], "error")
        self.assertEqual(doc["error"]["code"], code)
        self.assertEqual(doc["transaction"], {"outcome": "not-started", "recoveryRequired": False})
        self.assertEqual(tree(self.home), before)  # identical names and bytes
        self.assertEqual((self.home / transaction.LOCK_FILE).exists(), lock_expected)

    def cli(self, *args, home: Path | None = None, env_extra=None, root: Path = ROOT):
        isolated = str(home or self.home)
        env = dict(os.environ, HOME=isolated, USERPROFILE=isolated, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(root / "scripts/sync.py"), *args,
             "--home", isolated, "--format", "json"],
            capture_output=True, check=False, env=env,
        )


class SuccessTests(BoundPlanCase):
    def test_plan_then_bound_apply_succeeds(self):
        config = self.existing_config()
        fp = self.plan("--target", "both", "--specialists")
        code, doc = self.apply(fp, "--target", "both", "--specialists")
        self.assertEqual((code, doc["status"]), (0, "applied"), doc)
        self.assertEqual(doc["plan"], {"algorithm": "syncode-plan-v1", "expected": fp,
                                       "fingerprint": fp, "matched": True})
        self.assertEqual(doc["transaction"], {"outcome": "committed", "recoveryRequired": False})
        for change in doc["changes"]:
            self.assertEqual(sha((self.home / change["path"]).read_bytes()), change["sha256"])
        self.assertTrue(config.read_bytes().startswith(b"Personal instructions\n"))
        self.assertEqual(self.run_json("--target", "both", "--specialists")[1]["status"],
                         "unchanged")

    def test_empty_plan_binds_to_no_changes(self):
        self.run_json("--target", "claude", "--apply")
        fp = self.plan("--target", "claude")
        code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual((code, doc["status"], doc["plan"]["matched"]), (0, "unchanged", True))

    def test_fingerprint_is_deterministic_across_processes(self):
        fps = set()
        for seed in ("1", "2", "random"):
            result = self.cli("--target", "both", env_extra={"PYTHONHASHSEED": seed})
            self.assertEqual(result.returncode, 0, result.stderr)
            fps.add(json.loads(result.stdout)["plan"]["fingerprint"])
        self.assertEqual(len(fps), 1)
        result = self.cli("--target", "both", "--apply", "--expect-plan", fps.pop())
        self.assertEqual(json.loads(result.stdout)["status"], "applied")

    def test_unicode_home_subprocess_under_cp1252(self):
        home = self.home / UNICODE_NAME
        home.mkdir()
        plan = self.cli("--target", "both", home=home)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        fp = json.loads(plan.stdout)["plan"]["fingerprint"]
        applied = self.cli("--target", "both", "--apply", "--expect-plan", fp, home=home)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        doc = json.loads(applied.stdout)
        self.assertEqual((doc["status"], doc["plan"]["matched"]), ("applied", True))

    @unittest.skipUnless(os.name == "nt", "Windows paths are case-insensitive")
    def test_windows_home_case_variants_bind_the_same_plan(self):
        fp = self.plan("--target", "claude")
        variant = Path(str(self.home).upper())
        code, doc = self.apply(fp, "--target", "claude", home=variant)
        self.assertEqual((code, doc["status"]), (0, "applied"), doc)

    def test_legacy_apply_still_works_and_reports_fingerprint(self):
        code, doc = self.run_json("--target", "claude", "--apply")
        self.assertEqual((code, doc["status"]), (0, "applied"))
        self.assertTrue(plan_identity.is_well_formed(doc["plan"]["fingerprint"]))
        self.assertEqual((doc["plan"]["expected"], doc["plan"]["matched"]), (None, None))


class StaleRejectionTests(BoundPlanCase):
    def test_destination_modified_between_plan_and_apply(self):
        config = self.existing_config()
        fp = self.plan("--target", "claude")
        config.write_bytes(b"Edited after review\n")
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual(code, 1)
        self.assert_rejected_without_writes(doc, "stale-plan", before)
        self.assertEqual(doc["plan"]["expected"], fp)
        self.assertFalse(doc["plan"]["matched"])
        self.assertNotEqual(doc["plan"]["fingerprint"], fp)  # current plan, for re-review
        self.assertEqual(config.read_bytes(), b"Edited after review\n")

    def test_line_ending_only_change_is_stale(self):
        config = self.existing_config(b"Personal instructions\n")
        fp = self.plan("--target", "claude")
        config.write_bytes(b"Personal instructions\r\n")
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before)

    def test_target_change_is_stale(self):
        fp = self.plan("--target", "claude")
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "both")
        self.assert_rejected_without_writes(doc, "stale-plan", before)

    def test_option_changes_are_stale(self):
        fp = self.plan("--target", "claude")
        for extra in (["--specialists"], ["--upgrade-skills"]):
            with self.subTest(extra=extra):
                before = tree(self.home)
                code, doc = self.apply(fp, "--target", "claude", *extra)
                self.assert_rejected_without_writes(doc, "stale-plan", before)

    def test_installed_content_change_is_stale(self):
        fp = self.plan("--target", "claude")
        real = sync.global_block
        before = tree(self.home)
        with patch.object(sync, "global_block", side_effect=lambda t: real(t) + "extra\n"):
            code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before)

    def test_source_resource_change_in_checkout_is_stale(self):
        # A separate checkout copy whose skill source changes after review.
        checkout = Path(self.tmp.name) / "checkout"
        for part in ("scripts", "core", "adapters", "skills", "registry", "specialists"):
            shutil.copytree(ROOT / part, checkout / part,
                            ignore=shutil.ignore_patterns("__pycache__"))
        home = Path(self.tmp.name) / "home2"
        home.mkdir()
        plan = self.cli("--target", "claude", home=home, root=checkout)
        fp = json.loads(plan.stdout)["plan"]["fingerprint"]
        skill = checkout / "skills/code-review/SKILL.md"
        skill.write_bytes(skill.read_bytes() + b"\nchanged after review\n")
        before = tree(home)
        result = self.cli("--target", "claude", "--apply", "--expect-plan", fp,
                          home=home, root=checkout)
        doc = json.loads(result.stdout)
        self.assertEqual((result.returncode, doc["error"]["code"]), (1, "stale-plan"))
        self.assertEqual(tree(home), before)

    def tracking_lock(self, held):
        real_lock = transaction.exclusive_home_lock

        @contextmanager
        def lock(home, ensure_safe):
            with real_lock(home, ensure_safe):
                held.append(True)
                try:
                    yield
                finally:
                    held.pop()

        return patch.object(transaction, "exclusive_home_lock", lock)

    APPROVED_REL = Path(".claude/skills/demo-skill/SKILL.md")

    def test_approved_skill_content_change_is_stale(self):
        versions = iter([[(self.APPROVED_REL, "---\nname: demo-skill\n---\nA\n")],
                         [(self.APPROVED_REL, "---\nname: demo-skill\n---\nB\n")]])
        with patch.object(sync, "approved_candidates", side_effect=lambda _t: next(versions)):
            fp = self.plan("--target", "claude", "--approved-skills")
            before = tree(self.home)
            code, doc = self.apply(fp, "--target", "claude", "--approved-skills")
        self.assert_rejected_without_writes(doc, "stale-plan", before)

    def test_approved_skills_are_fetched_once_and_never_under_the_lock(self):
        held, calls = [], []

        def candidates(_targets):
            calls.append(list(held))
            return [(self.APPROVED_REL, "---\nname: demo-skill\n---\nA\n")]

        with patch.object(sync, "approved_candidates", side_effect=candidates), \
                self.tracking_lock(held):
            fp = self.plan("--target", "claude", "--approved-skills")
            code, doc = self.apply(fp, "--target", "claude", "--approved-skills")
        self.assertEqual((code, doc["status"]), (0, "applied"), doc)
        self.assertEqual(calls, [[], []])  # one dry-run fetch + one apply fetch, both unlocked
        self.assertEqual((self.home / self.APPROVED_REL).read_text(encoding="utf-8"),
                         "---\nname: demo-skill\n---\nA\n")

    def test_new_destination_file_appearing_is_stale(self):
        fp = self.plan("--target", "claude")
        config = self.existing_config(b"created after review\n")  # CREATE becomes UPDATE
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before)
        self.assertEqual(config.read_bytes(), b"created after review\n")

    def test_new_file_in_planned_skill_directory_keeps_specific_code(self):
        fp = self.plan("--target", "claude")
        extra = self.home / ".claude/skills/code-review/personal-notes.md"
        extra.parent.mkdir(parents=True)
        extra.write_text("mine", encoding="utf-8")
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        # Early-check errors are deferred to the authoritative locked rebuild,
        # so the persistent lock file may exist; nothing else is written.
        self.assert_rejected_without_writes(doc, "destination-conflict", before,
                                            lock_expected=True)
        self.assertIsNone(doc["plan"]["fingerprint"])  # no current plan could be built
        self.assertEqual(doc["changes"], [])

    def test_replay_after_successful_apply_is_stale(self):
        fp = self.plan("--target", "claude")
        self.assertEqual(self.apply(fp, "--target", "claude")[1]["status"], "applied")
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before, lock_expected=True)

    def test_fingerprint_of_another_home_is_stale(self):
        other = Path(self.tmp.name) / "other-home"
        other.mkdir()
        fp = self.plan("--target", "claude", home=other)
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before)


class ConcurrencyTests(BoundPlanCase):
    def test_change_after_precheck_is_caught_by_rebuild_under_lock(self):
        # A writer slips in after the early check but before the lock is held:
        # the rebuild under the lock must catch it before any write.
        config = self.existing_config()
        fp = self.plan("--target", "claude")
        real_lock = transaction.exclusive_home_lock

        @contextmanager
        def lock_then_concurrent_edit(home, ensure_safe):
            config.write_bytes(b"concurrent edit\n")
            with real_lock(home, ensure_safe):
                yield

        with patch.object(transaction, "exclusive_home_lock", lock_then_concurrent_edit):
            code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual(doc["error"]["code"], "stale-plan")
        self.assertEqual(doc["transaction"]["outcome"], "not-started")
        self.assertEqual(config.read_bytes(), b"concurrent edit\n")
        self.assertEqual(set(tree(self.home)), {".claude", ".claude/CLAUDE.md"})

    def test_rebuild_error_under_lock_never_reports_the_early_plan(self):
        fp = self.plan("--target", "claude")
        real_lock = transaction.exclusive_home_lock

        @contextmanager
        def lock_after_conflicting_file(home, ensure_safe):
            extra = home / ".claude/skills/code-review/personal.md"
            extra.parent.mkdir(parents=True)
            extra.write_text("mine", encoding="utf-8")
            with real_lock(home, ensure_safe):
                yield

        with patch.object(transaction, "exclusive_home_lock", lock_after_conflicting_file):
            code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual(doc["error"]["code"], "destination-conflict")
        self.assertEqual(doc["plan"], {"algorithm": "syncode-plan-v1", "expected": fp,
                                       "fingerprint": None, "matched": None})
        self.assertEqual(doc["changes"], [])
        self.assertEqual(doc["transaction"]["outcome"], "not-started")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_workspace_appearing_before_lock_is_transaction_pending(self):
        fp = self.plan("--target", "claude")
        real_lock = transaction.exclusive_home_lock

        @contextmanager
        def lock_after_crash_leftover(home, ensure_safe):
            (home / transaction.WORK_DIR).mkdir()
            with real_lock(home, ensure_safe):
                yield

        with patch.object(transaction, "exclusive_home_lock", lock_after_crash_leftover):
            code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual(doc["error"]["code"], "transaction-pending")
        self.assertEqual(doc["transaction"], {"outcome": "not-started", "recoveryRequired": True})
        self.assertEqual(set(tree(self.home)), {transaction.WORK_DIR})

    def hold_lock(self, with_workspace=False):
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path; import sync, transaction;"
            "home = Path(sys.argv[2]);"
            "cm = transaction.exclusive_home_lock(home, sync.ensure_safe); cm.__enter__();"
            + ("(home / transaction.WORK_DIR).mkdir();" if with_workspace else "")
            + "(home / 'ready').write_text('1'); sys.stdin.readline();"
            + ("(home / transaction.WORK_DIR).rmdir();" if with_workspace else "")
            + "cm.__exit__(None, None, None)"
        )
        holder = subprocess.Popen([sys.executable, "-c", script, str(ROOT / "scripts"),
                                   str(self.home)], stdin=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while not (self.home / "ready").exists():
            self.assertIsNone(holder.poll(), "lock holder exited early")
            self.assertLess(time.monotonic(), deadline, "lock holder did not start")
            time.sleep(0.02)
        return holder

    def test_apply_in_progress_elsewhere_is_lock_busy_not_transaction_pending(self):
        # Another process holds the lock with its workspace present (mid-apply):
        # a bound apply must report lock-busy (retry), not transaction-pending.
        fp = self.plan("--target", "claude")
        holder = self.hold_lock(with_workspace=True)
        try:
            before = tree(self.home)
            code, doc = self.apply(fp, "--target", "claude")
            self.assertEqual(doc["error"]["code"], "lock-busy")
            self.assertEqual(tree(self.home), before)
        finally:
            holder.communicate(b"\n", timeout=30)

    def test_lock_held_by_another_process_is_lock_busy_without_writes(self):
        fp = self.plan("--target", "claude")
        holder = self.hold_lock()
        try:
            before = tree(self.home)
            code, doc = self.apply(fp, "--target", "claude")
            self.assertEqual(doc["error"]["code"], "lock-busy")
            self.assertEqual(doc["transaction"]["outcome"], "not-started")
            self.assertEqual(tree(self.home), before)
        finally:
            holder.communicate(b"\n", timeout=30)

    def test_concurrent_bound_applies_apply_exactly_once(self):
        for round_ in range(3):
            with self.subTest(round=round_):
                home = Path(self.tmp.name) / f"race-{round_}"
                home.mkdir()
                fp = self.plan("--target", "both", "--specialists", home=home)
                env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
                procs = [subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts/sync.py"), "--target", "both",
                     "--specialists", "--apply", "--expect-plan", fp, "--home", str(home),
                     "--format", "json"], stdout=subprocess.PIPE, env=env)
                    for _ in range(3)]
                docs = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
                statuses = sorted(d["status"] if d["status"] != "error" else d["error"]["code"]
                                  for d in docs)
                self.assertEqual(statuses.count("applied"), 1, statuses)
                self.assertTrue(set(statuses) <= {"applied", "lock-busy", "stale-plan"}, statuses)
                self.assertFalse((home / transaction.WORK_DIR).exists())
                self.assertEqual(self.run_json("--target", "both", "--specialists",
                                               home=home)[1]["status"], "unchanged")

    def test_legacy_writer_between_plan_and_bound_apply_is_detected(self):
        fp = self.plan("--target", "claude")
        self.run_json("--target", "claude", "--apply")  # unbound writer (manual CLI)
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assert_rejected_without_writes(doc, "stale-plan", before, lock_expected=True)

    def test_pending_transaction_blocks_bound_apply(self):
        fp = self.plan("--target", "claude")
        (self.home / transaction.WORK_DIR).mkdir()
        before = tree(self.home)
        code, doc = self.apply(fp, "--target", "claude")
        self.assertEqual(doc["error"]["code"], "transaction-pending")
        self.assertEqual(tree(self.home), before)

    def test_change_between_verify_and_snapshot_is_rejected(self):
        # Legacy engine path: the snapshot must match the planned raw bytes.
        config = self.existing_config()
        changes = sync.build_plan(self.home, ("claude",))
        calls = 0

        def verify_then_edit(change, home):
            nonlocal calls
            sync.verify_change(change, home)
            calls += 1
            if calls == 1:
                config.write_bytes(b"edited during preparation\n")

        with self.assertRaises(transaction.TransactionError) as raised:
            transaction.apply_transaction(changes, self.home, verify_then_edit,
                                          sync.write_with_backup, sync.ensure_safe)
        self.assertEqual(raised.exception.code, "destination-changed")
        self.assertEqual(config.read_bytes(), b"edited during preparation\n")
        self.assertFalse((self.home / ".claude/skills").exists())
        self.assertFalse((self.home / transaction.WORK_DIR).exists())

    def test_unbound_apply_detects_line_ending_change_via_raw_bytes(self):
        config = self.existing_config(b"Personal instructions\n")
        changes = sync.build_plan(self.home, ("claude",))
        config.write_bytes(b"Personal instructions\r\n")
        with self.assertRaises(ValueError) as raised:
            transaction.apply_transaction(changes, self.home, sync.verify_change,
                                          sync.write_with_backup, sync.ensure_safe)
        self.assertEqual(raised.exception.code, "destination-changed")
        self.assertEqual(config.read_bytes(), b"Personal instructions\r\n")


class UsageTests(BoundPlanCase):
    def test_invalid_usage_is_rejected_before_any_io(self):
        good = "syncode-plan-v1:sha256:" + "0" * 64
        cases = [
            ["--expect-plan", good],                     # dry-run
            ["--recover", "--expect-plan", good],        # recover
            ["--apply", "--expect-plan", "abc"],         # malformed
            ["--apply", "--expect-plan", good.upper()],  # not canonical
        ]
        for args in cases:
            with self.subTest(args=args):
                code, doc = self.run_json("--target", "claude", *args)
                self.assertEqual((code, doc["error"]["code"]), (1, "invalid-arguments"))
                self.assertIsNone(doc["home"])
        self.assertEqual(list(self.home.iterdir()), [])  # not even the lock file

    def test_text_mode_usage_error_keeps_exit_2(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/sync.py"), "--expect-plan", "abc",
             "--home", str(self.home)], capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--expect-plan", result.stderr)

    def test_text_mode_bound_apply(self):
        fp = self.plan("--target", "claude")
        out = io.StringIO()
        with redirect_stdout(out):
            code = sync.main(["--target", "claude", "--apply", "--expect-plan", fp,
                              "--home", str(self.home)])
        self.assertEqual(code, 0)
        self.assertIn("APPLY CREATE", out.getvalue())
        self.assertIn("Applied 6 file change(s) transactionally.", out.getvalue())


class FingerprintUnitTests(unittest.TestCase):
    OPS = [{"kind": "CREATE", "path": "a", "newSha256": "1" * 64, "oldSha256": None},
           {"kind": "UPDATE", "path": "b", "newSha256": "2" * 64, "oldSha256": "3" * 64}]
    FLAGS = {"upgradeSkills": False, "specialists": False, "approvedSkills": False}

    def fp(self, home="/h", targets=("claude",), flags=None, ops=None):
        return plan_identity.fingerprint(Path(home), targets, flags or self.FLAGS,
                                         self.OPS if ops is None else ops)

    def test_every_input_changes_the_fingerprint(self):
        base = self.fp()
        self.assertEqual(base, self.fp())
        variants = [
            self.fp(home="/other"),
            self.fp(targets=("claude", "codex")),
            self.fp(flags={**self.FLAGS, "specialists": True}),
            self.fp(ops=list(reversed(self.OPS))),
            self.fp(ops=self.OPS[:1]),
            self.fp(ops=[{**self.OPS[0], "oldSha256": "4" * 64}, self.OPS[1]]),
            self.fp(ops=[{**self.OPS[0], "newSha256": "5" * 64}, self.OPS[1]]),
            self.fp(ops=[{**self.OPS[0], "kind": "UPDATE"}, self.OPS[1]]),
        ]
        self.assertEqual(len({base, *variants}), len(variants) + 1)
        self.assertTrue(plan_identity.is_well_formed(base))

    @unittest.skipUnless(os.name == "nt", "normcase only folds case on Windows")
    def test_windows_home_normalization_folds_case_and_separators(self):
        self.assertEqual(plan_identity.normalized_home(Path("C:/Users/Some.User")),
                         plan_identity.normalized_home(Path(r"c:\users\some.user")))
        self.assertEqual(self.fp(home="C:/Users/X"), self.fp(home=r"c:\USERS\x"))

    def test_fingerprint_contains_no_private_data(self):
        value = self.fp(home="/home/secret-user")
        self.assertNotIn("secret-user", value)
        self.assertEqual(len(value), len(plan_identity.PREFIX) + 64)


if __name__ == "__main__":
    unittest.main()
