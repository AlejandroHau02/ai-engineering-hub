"""Skill registry review, explicit approvals and read-only promotion plan tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from skill_registry import (RegistryError, review_template, validate_entry,
                            validate_manifest, promotion_plan, load_json)  # noqa: E402

COMMIT = "a" * 40
BLOB = "b" * 40
DIGEST = hashlib.sha256(b"original skill").hexdigest()
SOURCE_URL = f"https://github.com/example/repo/blob/{COMMIT}/skills/demo/SKILL.md"
EVIDENCE = "https://github.com/AlejandroHau02/ai-engineering-hub/pull/999"


def inspected():
    return {
        "schemaVersion": 1, "operation": "inspect", "decision": "manual-review-required",
        "source": {"url": SOURCE_URL, "repository": "example/repo",
                   "path": "skills/demo/SKILL.md", "commit": COMMIT, "blobSha": BLOB,
                   "contentSha256": DIGEST, "maintainer": "example"},
        "skill": {"name": "demo", "description": "Demonstration skill"},
        "findings": [{"type": "dependency-install", "lines": [10]}],
        "relatedFiles": {"status": "partial-review-required"},
    }


def approved():
    item = review_template(inspected())
    item["assessment"]["license"].update({
        "status": "verified", "identifier": "MIT", "evidence": EVIDENCE,
    })
    item["assessment"]["risk"].update({"status": "accepted", "evidence": EVIDENCE})
    item["assessment"]["compatibility"]["claudeCode"].update({
        "status": "tested", "evidence": EVIDENCE,
    })
    item["assessment"]["compatibility"]["codex"].update({
        "status": "unsupported", "evidence": EVIDENCE,
    })
    item["approval"].update({"status": "approved", "approvedBy": "repository-owner",
                             "approvedAt": "2026-11-21", "evidence": EVIDENCE})
    return item


class ReviewTemplateTests(unittest.TestCase):
    def test_template_requires_human_approval(self):
        result = review_template(inspected())
        self.assertEqual(result["approval"]["status"], "pending")
        self.assertEqual(result["assessment"]["risk"]["relatedScan"], "partial-review-required")
        self.assertEqual(result["assessment"]["risk"]["indicators"], ["dependency-install"])
        self.assertEqual(result["source"]["contentSha256"], DIGEST)
        with self.assertRaises(RegistryError):
            validate_entry(result)

    def test_bad_report_disallowed(self):
        for changes in [{"operation": "discover"}, {"decision": "approved"},
                        {"schemaVersion": 99}]:
            report = inspected()
            report.update(changes)
            with self.subTest(changes=changes), self.assertRaises(RegistryError):
                review_template(report)

    def test_source_mismatch_rejected(self):
        for key, value in [("commit", "c" * 40), ("repository", "other/repo"),
                           ("path", "../SKILL.md"), ("contentSha256", "bad"),
                           ("blobSha", "bad"), ("url", SOURCE_URL.replace("/blob/", "/tree/"))]:
            report = inspected()
            report["source"][key] = value
            with self.subTest(field=key), self.assertRaises(RegistryError):
                review_template(report)

    def test_first_party_collision_rejected(self):
        report = inspected()
        report["skill"]["name"] = "code-review"
        with self.assertRaises(RegistryError):
            review_template(report)


class ManifestTests(unittest.TestCase):
    def test_checked_in_manifest_is_intentionally_empty(self):
        path = ROOT / "registry/approved-skills.json"
        entries = validate_manifest(load_json(path))
        self.assertEqual(entries, [])

    def test_valid_manual_record(self):
        self.assertEqual(validate_manifest({"schemaVersion": 1, "skills": [approved()]}),
                         [approved()])

    def test_reject_incomplete_approvals(self):
        changes = [
            ("license", "status", "unverified"),
            ("license", "identifier", ""),
            ("license", "evidence", ""),
            ("risk", "status", "pending"),
            ("risk", "evidence", ""),
        ]
        for section, key, value in changes:
            item = approved()
            item["assessment"][section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(RegistryError):
                validate_entry(item)

    def test_reject_unsupported_or_unverified_clients(self):
        for status in ("not-verified", "unknown"):
            item = approved()
            item["assessment"]["compatibility"]["codex"]["status"] = status
            with self.subTest(status=status), self.assertRaises(RegistryError):
                validate_entry(item)
        item = approved()
        item["assessment"]["compatibility"]["claudeCode"]["status"] = "unsupported"
        with self.assertRaises(RegistryError):
            validate_entry(item)

    def test_reject_invalid_approval(self):
        for field, value in [("status", "pending"), ("approvedBy", ""),
                             ("approvedAt", "tomorrow"), ("approvedAt", "2026-02-30"),
                             ("evidence", "javascript:alert(1)")]:
            item = approved()
            item["approval"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RegistryError):
                validate_entry(item)

    def test_duplicate_ids_and_sources_rejected(self):
        one = approved()
        two = copy.deepcopy(one)
        with self.assertRaises(RegistryError):
            validate_manifest({"schemaVersion": 1, "skills": [one, two]})
        two["id"] = "other-skill"
        with self.assertRaises(RegistryError):
            validate_manifest({"schemaVersion": 1, "skills": [one, two]})

    def test_collision_with_core_skills_rejected(self):
        item = approved()
        item["id"] = "ux-ui"
        with self.assertRaises(RegistryError):
            validate_entry(item)

    def test_reject_unknown_schema_and_fields(self):
        with self.assertRaises(RegistryError):
            validate_manifest({"schemaVersion": True, "skills": []})
        with self.assertRaises(RegistryError):
            validate_manifest({"schemaVersion": 1, "skills": [], "trusted": True})
        item = approved()
        item["installation"] = "apply"
        with self.assertRaises(RegistryError):
            validate_entry(item)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_empty_manifest_has_zero_actions(self):
        plan = promotion_plan({"schemaVersion": 1, "skills": []}, self.home,
                              ("claude", "codex"))
        self.assertEqual(plan["changes"], [])
        self.assertEqual(plan["filesWritten"], 0)
        self.assertFalse(plan["executed"])

    def test_preview_never_creates_directories(self):
        plan = promotion_plan({"schemaVersion": 1, "skills": [approved()]},
                              self.home, ("claude", "codex"))
        self.assertEqual(len(plan["changes"]), 2)
        self.assertEqual({x["status"] for x in plan["changes"]}, {"PROPOSED_NEW"})
        self.assertTrue(all(x["action"] == "PROPOSAL_ONLY" for x in plan["changes"]))
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".agents").exists())

    def test_existing_custom_skill_blocks_preview(self):
        path = self.home / ".claude/skills/demo/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("user content", encoding="utf-8")
        result = promotion_plan({"schemaVersion": 1, "skills": [approved()]},
                                self.home, ("claude",))
        self.assertEqual(result["changes"][0]["status"], "BLOCKED_EXISTING")
        self.assertEqual(path.read_text(encoding="utf-8"), "user content")

    def test_symlink_blocks_preview(self):
        link = self.home / ".claude"
        try:
            link.symlink_to(self.home / "other", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        result = promotion_plan({"schemaVersion": 1, "skills": [approved()]},
                                self.home, ("claude",))
        self.assertEqual(result["changes"][0]["status"], "BLOCKED_SYMLINK")

    def test_cli_validate_plan_do_not_mutate(self):
        cmd = [sys.executable, str(ROOT / "scripts/skill_registry.py")]
        for args in (["validate"], ["plan", "--home", str(self.home)]):
            proc = subprocess.run(cmd + args, capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["approvedSkills"], 0)
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
