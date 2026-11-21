"""Offline fail-closed tests for approved SKILL.md promotion and safe global sync."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync  # noqa: E402
from approved_installer import PromotionError, approved_candidates  # noqa: E402
from skill_registry import RegistryError  # noqa: E402

COMMIT = "a" * 40
RAW = b"---\nname: third-party-demo\ndescription: A standalone third-party skill\n---\n\n# Demo\n"
SOURCE_PATH = "skills/third-party-demo/SKILL.md"
URL = f"https://github.com/example/skills/blob/{COMMIT}/{SOURCE_PATH}"


def git_sha(raw=RAW):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()


def entry(raw=RAW, claude="tested", codex="tested"):
    blob = git_sha(raw)
    evidence = "https://github.com/example/skills/pull/123"
    return {
        "id": "third-party-demo",
        "source": {
            "url": URL, "repository": "example/skills", "path": SOURCE_PATH,
            "commit": COMMIT, "blobSha": blob,
            "contentSha256": hashlib.sha256(raw).hexdigest(), "maintainer": "example",
        },
        "assessment": {
            "license": {"status": "verified", "identifier": "MIT", "evidence": evidence},
            "risk": {"status": "accepted", "evidence": evidence,
                     "indicators": [], "relatedScan": "complete-text-scan"},
            "compatibility": {
                "claudeCode": {"status": claude, "evidence": evidence},
                "codex": {"status": codex, "evidence": evidence},
            },
        },
        "approval": {"status": "approved", "approvedBy": "owner",
                     "approvedAt": "2026-11-21", "evidence": evidence},
    }


def fake_api(raw=RAW, siblings=(), mode="100644", sha=None, extra=None):
    """A fake immutable GitHub API: no external access or GitHub credentials."""
    sha = sha or git_sha(raw)
    tree = [{"path": SOURCE_PATH, "type": "blob", "mode": mode,
             "sha": sha, "size": len(raw)}]
    tree.extend({"path": path, "type": "blob", "mode": "100644",
                 "sha": "0" * 40, "size": 10} for path in siblings)
    def get_json(path):
        if "/git/trees/" in path:
            result = {"truncated": False, "tree": tree}
            if extra and extra.get("tree"):
                result.update(extra["tree"])
            return result
        if "/contents/" in path:
            result = {"type": "file", "encoding": "base64", "sha": sha,
                      "size": len(raw),
                      "content": base64.b64encode(raw).decode()}
            if extra and extra.get("contents"):
                result.update(extra["contents"])
            return result
        raise AssertionError("Unexpected network path " + path)
    return get_json


class ApprovedPromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.manifest = self.home / "approved.json"

    def write_manifest(self, records):
        self.manifest.write_text(json.dumps({"schemaVersion": 1, "skills": records}),
                                 encoding="utf-8")

    def test_empty_registry_no_network_and_no_files(self):
        self.write_manifest([])
        def forbidden(_):
            self.fail("Network must not be accessed for empty manifest")
        self.assertEqual(approved_candidates(("claude",), self.manifest, forbidden), [])
        self.assertEqual(list(self.home.iterdir()), [self.manifest])

    def test_verified_standalone_skill_installs_to_correct_client_paths(self):
        self.write_manifest([entry()])
        paths = approved_candidates(("claude", "codex"), self.manifest, fake_api())
        self.assertEqual([str(p[0]).replace("\\", "/") for p in paths],
                         [".claude/skills/third-party-demo/SKILL.md",
                          ".agents/skills/third-party-demo/SKILL.md"])
        self.assertTrue(all(content.encode("utf-8") == RAW for _, content in paths))
        self.assertEqual(list(self.home.iterdir()), [self.manifest])

    def test_mismatched_sha256_is_rejected(self):
        item = entry()
        item["source"]["contentSha256"] = "0" * 64
        self.write_manifest([item])
        with self.assertRaisesRegex(PromotionError, "SHA-256"):
            approved_candidates(("claude",), self.manifest, fake_api())

    def test_github_blob_metadata_mismatch_is_rejected(self):
        self.write_manifest([entry()])
        for extra in ({"contents": {"sha": "0" * 40}},
                      {"tree": {"truncated": True}},
                      {"contents": {"size": 999}}):
            with self.subTest(extra=extra), self.assertRaises(PromotionError):
                approved_candidates(("claude",), self.manifest,
                                    fake_api(extra=extra))

    def test_symlink_and_companion_files_rejected(self):
        self.write_manifest([entry()])
        with self.assertRaisesRegex(PromotionError, "pinned Git tree"):
            approved_candidates(("claude",), self.manifest,
                                fake_api(mode="120000"))
        for siblings in (("skills/third-party-demo/scripts/setup.py",),
                         ("skills/third-party-demo/references",),
                         ("skills/third-party-demo/README.md",)):
            with self.subTest(siblings=siblings), self.assertRaisesRegex(PromotionError, "multi-file"):
                approved_candidates(("claude",), self.manifest,
                                    fake_api(siblings=siblings))

    def test_crlf_rejected_because_sync_must_preserve_hash(self):
        raw = RAW.replace(b"\n", b"\r\n")
        self.write_manifest([entry(raw)])
        with self.assertRaisesRegex(PromotionError, "CR/NUL"):
            approved_candidates(("claude",), self.manifest, fake_api(raw))

    def test_frontmatter_name_must_match_approved_id(self):
        raw = RAW.replace(b"name: third-party-demo", b"name: other-skill")
        self.write_manifest([entry(raw)])
        with self.assertRaisesRegex(PromotionError, "frontmatter"):
            approved_candidates(("claude",), self.manifest, fake_api(raw))

    def test_incompatible_target_rejected_before_source_fetch(self):
        self.write_manifest([entry(codex="unsupported")])
        def forbidden(_):
            self.fail("Incompatible target must not trigger network request")
        with self.assertRaisesRegex(PromotionError, "not approved as tested"):
            approved_candidates(("codex",), self.manifest, forbidden)

    def test_missing_approval_blocks_fetch(self):
        item = entry()
        item["approval"]["status"] = "pending"
        self.write_manifest([item])
        def forbidden(_):
            self.fail("Unapproved source must not be fetched")
        with self.assertRaises(RegistryError):
            approved_candidates(("claude",), self.manifest, forbidden)

    def test_existing_skill_conflict_prevents_all_sync_writes(self):
        dest = self.home / ".claude/skills/third-party-demo/SKILL.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("user modification", encoding="utf-8")
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            with self.assertRaisesRegex(ValueError, "Approved skill conflict"):
                sync.build_plan(self.home, ("claude",), include_approved_skills=True)
        self.assertEqual(dest.read_text(encoding="utf-8"), "user modification")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_verified_source_uses_existing_safe_sync_writer(self):
        self.write_manifest([entry()])
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            changes = sync.build_plan(self.home, ("claude",), include_approved_skills=True)
            self.assertEqual(len([x for x in changes if x.kind == "APPROVED-CREATE"]), 1)
            for change in changes:
                sync.verify_change(change, self.home)
            for change in changes:
                sync.write_with_backup(change, self.home)
            self.assertEqual((self.home / ".claude/skills/third-party-demo/SKILL.md").read_bytes(), RAW)
            self.assertEqual(sync.build_plan(self.home, ("claude",), include_approved_skills=True), [])

    def test_existing_skill_directory_without_skill_md_blocks_all_writes(self):
        folder = self.home / ".claude/skills/third-party-demo"
        folder.mkdir(parents=True)
        (folder / "notes.txt").write_text("personal", encoding="utf-8")
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            with self.assertRaisesRegex(ValueError, "directory already exists"):
                sync.build_plan(self.home, ("claude",), include_approved_skills=True)
        self.assertEqual((folder / "notes.txt").read_text(encoding="utf-8"), "personal")
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_existing_matching_skill_with_extra_files_blocks(self):
        folder = self.home / ".claude/skills/third-party-demo"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_bytes(RAW)
        (folder / "personal.txt").write_text("personal", encoding="utf-8")
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            with self.assertRaisesRegex(ValueError, "contains other files"):
                sync.build_plan(self.home, ("claude",), include_approved_skills=True)
        self.assertEqual((folder / "personal.txt").read_text(encoding="utf-8"), "personal")

    def test_empty_preexisting_skill_directory_blocks(self):
        folder = self.home / ".claude/skills/third-party-demo"
        folder.mkdir(parents=True)
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            with self.assertRaisesRegex(ValueError, "directory already exists"):
                sync.build_plan(self.home, ("claude",), include_approved_skills=True)

    def test_apply_is_explicit_and_approved_file_is_idempotent(self):
        destination = self.home / ".claude/skills/third-party-demo/SKILL.md"
        with patch.object(sync, "approved_candidates", return_value=[
            (Path(".claude/skills/third-party-demo/SKILL.md"), RAW.decode())
        ]):
            with patch.object(sys, "argv", ["sync.py", "--target", "claude", "--home",
                                            str(self.home), "--approved-skills"]):
                self.assertEqual(sync.main(), 0)
            self.assertFalse(destination.exists())
            with patch.object(sys, "argv", ["sync.py", "--target", "claude", "--home",
                                            str(self.home), "--approved-skills", "--apply"]):
                self.assertEqual(sync.main(), 0)
            self.assertEqual(destination.read_bytes(), RAW)
            self.assertEqual(sync.build_plan(self.home, ("claude",), include_approved_skills=True), [])

    def test_all_targets_checked_before_any_fetch(self):
        item = entry()
        other = entry()
        other["id"] = "second-skill"
        other["source"]["path"] = "skills/second-skill/SKILL.md"
        other["source"]["url"] = f"https://github.com/example/skills/blob/{COMMIT}/skills/second-skill/SKILL.md"
        other["assessment"]["compatibility"]["codex"]["status"] = "unsupported"
        self.write_manifest([item, other])
        def forbidden(_):
            self.fail("No source may be fetched before all compatibility checks")
        with self.assertRaisesRegex(PromotionError, "not approved as tested"):
            approved_candidates(("codex",), self.manifest, forbidden)

    def test_bad_target_rejected_before_any_fetch(self):
        self.write_manifest([entry()])
        def forbidden(_):
            self.fail("Invalid target must not trigger a remote request")
        with self.assertRaisesRegex(PromotionError, "Invalid target"):
            approved_candidates(("custom",), self.manifest, forbidden)

    def test_ordinary_sync_does_not_contact_approved_registry(self):
        with patch.object(sync, "approved_candidates", side_effect=AssertionError("Unexpected read")):
            planned = sync.build_plan(self.home, ("claude",))
        self.assertNotEqual(planned, [])
        self.assertFalse((self.home / ".claude").exists())

    def test_cli_empty_manifest_approved_mode_is_noop_on_preinstalled_home(self):
        self.write_manifest([])
        cmd = [sys.executable, str(ROOT / "scripts" / "sync.py"),
               "--target", "claude", "--home", str(self.home)]
        initial = subprocess.run(cmd + ["--apply"], capture_output=True, text=True, check=False)
        self.assertEqual(initial.returncode, 0, initial.stderr)
        dry = subprocess.run(cmd + ["--approved-skills"], capture_output=True, text=True, check=False)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("NO CHANGES", dry.stdout)


if __name__ == "__main__":
    unittest.main()
