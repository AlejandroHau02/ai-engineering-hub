"""SYNCODE catalog command (read-only): components and unmanaged entries."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import catalog  # noqa: E402
import specialists  # noqa: E402
import sync  # noqa: E402


def run_json(argv: list[str]) -> tuple[int, dict]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = catalog.main(argv)
    return code, json.loads(out.getvalue())


def tree(home: Path) -> list[tuple[str, int, bytes | None]]:
    return sorted((p.relative_to(home).as_posix(), p.stat().st_mtime_ns,
                   p.read_bytes() if p.is_file() else None) for p in home.rglob("*"))


class CatalogTests(unittest.TestCase):
    def test_components_come_from_the_real_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, data = run_json(["--home", tmp, "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "ok")
        skills = [c for c in data["components"] if c["kind"] == "skill"]
        agents = [c for c in data["components"] if c["kind"] == "specialist"]
        self.assertEqual([s["name"] for s in skills], list(sync.SKILLS))
        self.assertEqual([a["name"] for a in agents], [e["name"] for e in specialists.read_catalog()])
        for skill in skills:
            self.assertTrue(skill["description"])
            self.assertEqual(skill["paths"], {
                "claude": f".claude/skills/{skill['name']}/SKILL.md",
                "codex": f".agents/skills/{skill['name']}/SKILL.md"})
        for agent in agents:
            self.assertIn(agent["skill"], sync.SKILLS)
            self.assertTrue(agent["focus"] and agent["deliverable"])
            self.assertEqual(agent["paths"], {
                "claude": f".claude/agents/{agent['name']}.md",
                "codex": f".codex/agents/{agent['name']}.toml"})
        # Every first-party skill has a specialist that applies it.
        self.assertEqual({a["skill"] for a in agents}, set(sync.SKILLS))

    def test_lists_only_unmanaged_entries_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            mine = home / ".claude/skills/my-skill"
            mine.mkdir(parents=True)
            (mine / "SKILL.md").write_text(
                "---\nname: my-skill\ndescription: Mine‮ evil\x07 text\n---\n", encoding="utf-8")
            (home / ".claude/skills/ux-ui").mkdir()          # managed name: not listed
            (home / ".claude/agents").mkdir(parents=True)
            (home / ".claude/agents/helper.md").write_text(
                "---\ndescription: <img src=x onerror=alert(1)>\n---\n", encoding="utf-8")
            (home / ".claude/agents/notes.txt").write_text("x", encoding="utf-8")
            (home / ".codex/agents").mkdir(parents=True)
            (home / ".codex/agents/reviewer.toml").write_text(
                'name = "reviewer"\ndescription = "Reviews \\"things\\""\n', encoding="utf-8")
            big = home / ".agents/skills/huge"
            big.mkdir(parents=True)
            (big / "SKILL.md").write_text("---\ndescription: big\n---\n" + "x" * 70000, encoding="utf-8")
            before = tree(home)
            code, data = run_json(["--home", tmp, "--format", "json"])
            self.assertEqual(tree(home), before, "catalog must not write")
        self.assertEqual(code, 0)
        claude = data["unmanaged"]["claude"]
        self.assertEqual([e["name"] for e in claude["skills"]["entries"]], ["my-skill"])
        description = claude["skills"]["entries"][0]["description"]
        self.assertNotIn("‮", description)
        self.assertNotIn("\x07", description)
        # Text is data: the UI renders it with textContent; nothing is executed here.
        self.assertEqual(claude["agents"]["entries"][0]["description"], "<img src=x onerror=alert(1)>")
        self.assertEqual([e["name"] for e in claude["agents"]["entries"]], ["helper"])
        codex = data["unmanaged"]["codex"]
        self.assertEqual(codex["agents"]["entries"][0]["description"], 'Reviews "things"')
        # Only the first bytes are read: the frontmatter still yields its description.
        self.assertEqual(codex["skills"]["entries"], [{"name": "huge", "link": False, "description": "big"}])

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows feature")
    def test_junctions_are_reported_but_never_followed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            home = Path(tmp)
            (Path(outside) / "SKILL.md").write_text("---\ndescription: secret\n---\n", encoding="utf-8")
            (home / ".claude/skills").mkdir(parents=True)
            subprocess.run(["cmd", "/C", "mklink", "/J", str(home / ".claude/skills/linked"), outside],
                           check=True, capture_output=True)
            code, data = run_json(["--home", tmp, "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(data["unmanaged"]["claude"]["skills"]["entries"],
                         [{"name": "linked", "link": True, "description": None}])

    def test_toml_descriptions_keep_accents(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ".codex/agents"
            folder.mkdir(parents=True)
            (folder / "revisor.toml").write_text(
                'description = "Revisi\u00f3n de c\u00f3digo \\"segura\\""\n', encoding="utf-8")
            _, data = run_json(["--home", tmp, "--format", "json"])
        self.assertEqual(data["unmanaged"]["codex"]["agents"]["entries"][0]["description"],
                         'Revisi\u00f3n de c\u00f3digo "segura"')

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows feature")
    def test_linked_client_folder_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "skills" / "elsewhere").mkdir(parents=True)
            subprocess.run(["cmd", "/C", "mklink", "/J", str(Path(tmp) / ".claude"), outside],
                           check=True, capture_output=True)
            _, data = run_json(["--home", tmp, "--format", "json"])
        self.assertEqual(data["unmanaged"]["claude"]["skills"]["entries"], [])

    def test_entry_count_is_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ".agents/skills"
            folder.mkdir(parents=True)
            for i in range(catalog.MAX_ENTRIES + 5):
                (folder / f"s{i:04d}").mkdir()
            _, data = run_json(["--home", tmp, "--format", "json"])
        entries = data["unmanaged"]["codex"]["skills"]
        self.assertEqual(len(entries["entries"]), catalog.MAX_ENTRIES)
        self.assertTrue(entries["truncated"])

    def test_reads_are_capped_and_text_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / ".claude/skills"
            huge = folder / "huge"
            huge.mkdir(parents=True)
            # A large file: only the first bytes (the frontmatter) are read.
            with (huge / "SKILL.md").open("w", encoding="utf-8") as out:
                out.write("---\ndescription: " + "d" * 500 + "\n---\n")
                out.write("x" * (5 * 1024 * 1024))
            odd = folder / ("odd​⁦name" + "n" * 100)
            odd.mkdir()
            _, data = run_json(["--home", tmp, "--format", "json"])
        entries = {e["name"]: e for e in data["unmanaged"]["claude"]["skills"]["entries"]}
        self.assertEqual(len(entries["huge"]["description"]), catalog.MAX_TEXT)
        long_name = [name for name in entries if name.startswith("odd")][0]
        self.assertLessEqual(len(long_name), catalog.MAX_NAME)
        self.assertNotIn("​", long_name)
        self.assertNotIn("⁦", long_name)

    def test_unmanaged_output_stays_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            for folder in (".claude/skills", ".agents/skills"):
                for i in range(catalog.MAX_ENTRIES):
                    entry = Path(tmp) / folder / f"s{i:03d}"
                    entry.mkdir(parents=True)
                    (entry / "SKILL.md").write_text(
                        "---\ndescription: " + "é" * 400 + "\n---\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                catalog.main(["--home", tmp, "--format", "json"])
        self.assertLess(len(out.getvalue().encode("ascii")), 1 << 20)

    def test_invalid_arguments_use_the_json_contract(self):
        code, data = run_json(["--format", "json", "--bogus"])
        self.assertEqual(code, 1)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"]["code"], "invalid-arguments")


if __name__ == "__main__":
    unittest.main()
