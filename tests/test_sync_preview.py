"""Read-only sync preview contract (Desktop Phase 2A).

* ``describe_change`` classifies every managed destination (additive field);
* ``--preview`` is rejected together with ``--apply``/``--recover``;
* the preview performs no write, rename, delete, network or subprocess call,
  proven with a CPython audit hook in a child process across all Desktop
  option combinations and several home states (negative control: --apply).
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import sync  # noqa: E402


def run_json(argv: list[str]) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = sync.main(argv)
    return code, json.loads(out.getvalue())


class DescribeChangeTests(unittest.TestCase):
    def test_managed_layout(self):
        cases = {
            ".claude/CLAUDE.md": ("claude", "instructions", None),
            ".codex/AGENTS.md": ("codex", "instructions", None),
            ".claude/skills/ux-ui/SKILL.md": ("claude", "skill", "ux-ui"),
            ".agents/skills/code-review/SKILL.md": ("codex", "skill", "code-review"),
            ".claude/agents/hub-ux-auditor.md": ("claude", "specialist", "hub-ux-auditor"),
            ".codex/agents/hub-code-reviewer.toml": ("codex", "specialist", "hub-code-reviewer"),
            ".claude/other.md": (None, "unknown", None),
            ".claude/skills/x/README.md": (None, "unknown", None),
        }
        for path, (target, kind, name) in cases.items():
            with self.subTest(path=path):
                described = sync.describe_change(path, "CREATE")
                self.assertEqual(described, {"target": target,
                                             "component": {"type": kind, "name": name}})

    def test_approved_skill_kind(self):
        self.assertEqual(
            sync.describe_change(".agents/skills/third/SKILL.md", "APPROVED-CREATE")["component"],
            {"type": "approved-skill", "name": "third"})

    def test_every_planned_change_is_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, data = run_json(["--preview", "--specialists", "--home", tmp,
                                   "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "planned")
        for change in data["changes"]:
            self.assertIn(change["target"], ("claude", "codex"), change)
            self.assertIn(change["component"]["type"], ("instructions", "skill", "specialist"))
            self.assertEqual(sync.describe_change(change["path"], change["kind"]),
                             {"target": change["target"], "component": change["component"]})
        kinds = {c["component"]["type"] for c in data["changes"]}
        self.assertEqual(kinds, {"instructions", "skill", "specialist"})


class PreviewFlagTests(unittest.TestCase):
    def test_preview_equals_default_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, default = run_json(["--home", tmp, "--format", "json"])
            _, preview = run_json(["--preview", "--home", tmp, "--format", "json"])
        self.assertEqual(default["plan"]["fingerprint"], preview["plan"]["fingerprint"])
        self.assertEqual(default["changes"], preview["changes"])
        self.assertEqual(preview["transaction"]["outcome"], "not-started")

    def test_preview_rejects_write_actions(self):
        for extra in (["--apply"], ["--recover"], ["--apply", "--expect-plan",
                                                    "syncode-plan-v1:sha256:" + "a" * 64]):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                code, data = run_json(["--preview", *extra, "--home", tmp, "--format", "json"])
                self.assertEqual(code, 1)
                self.assertEqual(data["error"]["code"], "invalid-arguments")
                self.assertEqual(list(Path(tmp).iterdir()), [], "nothing may be created")


HARNESS = textwrap.dedent(r"""
    import json, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    home, argv = sys.argv[2], json.loads(sys.argv[3])
    violations = []
    import os
    WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                   | os.O_EXCL)

    def hook(event, args):
        if event == "open":
            path, mode, flags = args
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and flags & WRITE_FLAGS):
                violations.append((event, str(path)))
        elif event in ("os.mkdir", "os.rename", "os.replace", "os.remove", "os.rmdir",
                       "os.symlink", "os.link", "os.truncate", "os.chmod", "os.utime",
                       "shutil.copyfile", "shutil.move", "shutil.rmtree",
                       "socket.connect", "socket.getaddrinfo", "subprocess.Popen",
                       "urllib.Request", "os.system", "os.exec", "os.spawn",
                       "os.posix_spawn", "os.startfile", "os.chown", "os.kill",
                       "ctypes.dlopen", "winreg.SetValue", "winreg.CreateKey"):
            violations.append((event, str(args[0]) if args else ""))

    import io, contextlib
    import sync  # imported before the hook: module loading itself is not under test
    sys.addaudithook(hook)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sync.main(argv + ["--home", home, "--format", "json"])
    result = json.loads(out.getvalue())
    print(json.dumps({"code": code, "status": result["status"],
                      "error": (result["error"] or {}).get("code"),
                      "outcome": result["transaction"]["outcome"],
                      "violations": violations}))
""")


def _audited(home: Path, argv: list[str]) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-c", HARNESS, str(REPO / "scripts"), str(home),
         json.dumps(argv)], capture_output=True, text=True, timeout=120, env=env)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"harness failed: {proc.returncode} {proc.stderr[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _seed(kind: str, home: Path) -> None:
    """Representative home states a Desktop user can have."""
    if kind == "empty":
        return
    with contextlib.redirect_stdout(io.StringIO()):
        sync.main(["--target", "both", "--specialists", "--apply", "--home", str(home)])
    claude = home / ".claude" / "CLAUDE.md"
    claude.write_text("# Personal notes\n" + claude.read_text(encoding="utf-8"), encoding="utf-8")
    if kind == "baseline-skill":
        baseline = sync.BASELINES / "ux-ui" / "SKILL.md"
        (home / ".claude/skills/ux-ui/SKILL.md").write_text(
            baseline.read_text(encoding="utf-8"), encoding="utf-8")
    elif kind == "modified-skill":
        skill = home / ".agents/skills/code-review/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nlocal\n", encoding="utf-8")
    elif kind == "pending-transaction":
        (home / ".ai-engineering-hub-transaction").mkdir()


DESKTOP_COMBINATIONS = [
    ["--preview", "--target", target, *(["--specialists"] if specialists else []),
     *(["--upgrade-skills"] if upgrade else [])]
    for target, specialists, upgrade in itertools.product(
        ("both", "claude", "codex"), (False, True), (False, True))
]


class PreviewIsReadOnlyTests(unittest.TestCase):
    def test_no_write_network_or_subprocess_in_any_combination(self):
        seen = set()
        for state in ("empty", "installed", "baseline-skill", "modified-skill",
                      "pending-transaction"):
            with tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                _seed(state, home)
                before = sorted((p.relative_to(home).as_posix(), p.stat().st_mtime_ns,
                                 p.read_bytes() if p.is_file() else None)
                                for p in home.rglob("*"))
                for argv in DESKTOP_COMBINATIONS:
                    with self.subTest(state=state, argv=argv):
                        result = _audited(home, argv)
                        self.assertEqual(result["violations"], [])
                        self.assertEqual(result["outcome"], "not-started")
                        self.assertIn(result["status"], ("planned", "unchanged", "error"))
                        seen.add((result["status"], result["error"]))
                after = sorted((p.relative_to(home).as_posix(), p.stat().st_mtime_ns,
                                p.read_bytes() if p.is_file() else None)
                               for p in home.rglob("*"))
                self.assertEqual(before, after, state)
        # The matrix really exercised plans, no-ops and controlled refusals.
        statuses = {status for status, _ in seen}
        self.assertTrue({"planned", "unchanged", "error"} <= statuses, seen)
        errors = {error for _, error in seen if error}
        self.assertTrue({"skill-upgrade-available", "skill-modified",
                         "transaction-pending"} <= errors, errors)

    def test_hook_detects_apply_negative_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _audited(Path(tmp), ["--target", "claude", "--apply"])
        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["violations"], "the audit hook must observe real writes")


if __name__ == "__main__":
    unittest.main()
