#!/usr/bin/env python3
"""Review templates, approved-skill manifest validation and read-only promotion previews.

This program never executes external content, installs skills or writes files.
Approval metadata is a *claim* requiring human and repository review, not proof
of the identity of an approver or a license/security attestation.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from skill_scout import parse_skill_url

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "registry" / "approved-skills.json"
MAX_JSON_BYTES = 1024 * 1024
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
OWN_SKILLS = frozenset({"architecture-review", "backend-engineering", "code-review", "ux-ui"})
WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul"})
WINDOWS_RESERVED |= frozenset(f"{name}{number}" for name in ("com", "lpt") for number in range(1, 10))
TARGET_PATHS = { "claude": ".claude/skills", "codex": ".agents/skills" }
COMPAT = frozenset({"tested", "unsupported", "not-verified"})


class RegistryError(ValueError):
    """Input or approval record invalid; no changes made."""


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise RegistryError(f"Unexpected {label} fields")
    return value


def _text(value: object, label: str, *, required: bool = True, max_length: int = 1024) -> str:
    if (not isinstance(value, str) or len(value) > max_length
        or "\x00" in value or "\r" in value or "\n" in value
        or (required and not value.strip())):
        raise RegistryError(f"Invalid {label}")
    return value


def _https(value: object, label: str) -> str:
    value = _text(value, label, max_length=1024)
    try:
        parts = urlsplit(value)
    except ValueError:
        raise RegistryError(f"Invalid {label} URL") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or any(ord(c) < 33 for c in value)):
        raise RegistryError(f"{label} requires an HTTPS URL without credentials")
    return value


def _sha(value: object, label: str, pattern=HEX64) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RegistryError(f"Invalid {label}")
    return value


def _source(value: object) -> dict:
    value = _object(value, {"url", "repository", "path", "commit",
                            "blobSha", "contentSha256", "maintainer"}, "source")
    try:
        owner, repo, commit, path = parse_skill_url(value["url"])
    except (ValueError, TypeError) as error:
        raise RegistryError(f"Invalid pinned source URL: {error}") from None
    if value["repository"] != f"{owner}/{repo}" or value["path"] != path:
        raise RegistryError("Source URL, repository and path disagree")
    if value["commit"] != commit:
        raise RegistryError("Pinned commit does not match source URL")
    _sha(value["contentSha256"], "content SHA-256")
    _sha(value["blobSha"], "Git blob SHA", HEX40)
    _text(value["maintainer"], "maintainer", max_length=100)
    return value


def load_json(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise RegistryError("JSON input exceeds the 1 MiB limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RegistryError("Expected a JSON object")
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, RegistryError):
            raise
        raise RegistryError(f"Unable to read JSON input: {type(exc).__name__}") from None


def review_template(report: dict) -> dict:
    """Convert inspect JSON into an INCOMPLETE manual review template."""
    if (not isinstance(report, dict) or report.get("schemaVersion") != 1
        or report.get("operation") != "inspect"
        or report.get("decision") != "manual-review-required"):
        raise RegistryError("Expected a Skill Scout inspect report")
    source = report.get("source")
    if not isinstance(source, dict):
        raise RegistryError("Inspect report has no source")
    normalized = _source({
        key: source.get(key)
        for key in ("url", "repository", "path", "commit",
                    "blobSha", "contentSha256", "maintainer")
    })
    skill = report.get("skill")
    if not isinstance(skill, dict):
        raise RegistryError("Inspect report missing skill metadata")
    name = skill.get("name")
    if not isinstance(name, str) or len(name) > 64 or not ID_RE.fullmatch(name):
        raise RegistryError("Skill name is not a safe registry identifier")
    if name in OWN_SKILLS:
        raise RegistryError("Skill name conflicts with a first-party Hub skill")
    if name in WINDOWS_RESERVED:
        raise RegistryError("Skill name is reserved on Windows")
    findings = report.get("findings")
    if not isinstance(findings, list) or len(findings) > 1000:
        raise RegistryError("Invalid inspect findings")
    indicators = []
    for item in findings:
        if not isinstance(item, dict):
            raise RegistryError("Invalid finding")
        label = _text(item.get("type"), "finding type", max_length=100)
        if label not in indicators:
            indicators.append(label)
    related = report.get("relatedFiles")
    coverage = "not-scanned"
    if related is not None:
        if not isinstance(related, dict):
            raise RegistryError("Invalid companion-file inspection")
        coverage = related.get("status")
        if coverage not in ("complete-text-scan", "partial-review-required"):
            raise RegistryError("Unknown companion-file scan status")
    return {
        "id": name,
        "source": normalized,
        "assessment": {
            "license": {"status": "unverified", "identifier": "", "evidence": ""},
            "risk": {"status": "pending", "evidence": "",
                     "indicators": indicators, "relatedScan": coverage},
            "compatibility": {
                "claudeCode": {"status": "not-verified", "evidence": ""},
                "codex": {"status": "not-verified", "evidence": ""},
            },
        },
        "approval": {"status": "pending", "approvedBy": "",
                     "approvedAt": "", "evidence": ""},
    }


def validate_entry(entry: object) -> dict:
    entry = _object(entry, {"id", "source", "assessment", "approval"}, "approved entry")
    name = entry["id"]
    if not isinstance(name, str) or not ID_RE.fullmatch(name) or len(name) > 64:
        raise RegistryError("Invalid approved-skill identifier")
    if name in OWN_SKILLS:
        raise RegistryError(f"Approved skill {name} would shadow a first-party skill")
    if name in WINDOWS_RESERVED:
        raise RegistryError(f"Approved skill {name} is a reserved Windows path")
    _source(entry["source"])
    assessment = _object(entry["assessment"], {"license", "risk", "compatibility"}, "assessment")
    lic = _object(assessment["license"], {"status", "identifier", "evidence"}, "license")
    if lic["status"] != "verified":
        raise RegistryError(f"{name}: license has not been verified")
    _text(lic["identifier"], "license identifier", max_length=120)
    _https(lic["evidence"], "license evidence")
    risk = _object(assessment["risk"], {"status", "evidence", "indicators", "relatedScan"}, "risk")
    if risk["status"] != "accepted":
        raise RegistryError(f"{name}: risks are not accepted after review")
    _https(risk["evidence"], "risk review evidence")
    if risk["relatedScan"] not in ("not-scanned", "complete-text-scan", "partial-review-required"):
        raise RegistryError("Unsupported related file scan state")
    indicators = risk["indicators"]
    if (not isinstance(indicators, list) or len(indicators) > 100
        or any(not isinstance(v, str) or len(v) > 100 for v in indicators)
        or len(indicators) != len(set(indicators))):
        raise RegistryError("Invalid risk indicators")
    compat = _object(assessment["compatibility"], {"claudeCode", "codex"}, "compatibility")
    tested = 0
    for target in ("claudeCode", "codex"):
        record = _object(compat[target], {"status", "evidence"}, f"{target} compatibility")
        if record["status"] not in COMPAT or record["status"] == "not-verified":
            raise RegistryError(f"{name}: missing compatibility disposition for {target}")
        if record["status"] == "tested":
            tested += 1
        _https(record["evidence"], f"{target} compatibility evidence")
    if tested == 0:
        raise RegistryError(f"{name}: at least one target must have passing tests")
    approval = _object(entry["approval"],
                       {"status", "approvedBy", "approvedAt", "evidence"}, "approval")
    if approval["status"] != "approved":
        raise RegistryError(f"{name}: explicit approval not recorded")
    _text(approval["approvedBy"], "approver", max_length=120)
    at = _text(approval["approvedAt"], "approval date", max_length=10)
    try:
        if date.fromisoformat(at).isoformat() != at:
            raise ValueError("Invalid ISO date")
    except ValueError:
        raise RegistryError(f"{name}: invalid approval date") from None
    _https(approval["evidence"], "approval evidence")
    return entry


def validate_manifest(manifest: object) -> list[dict]:
    data = _object(manifest, {"schemaVersion", "skills"}, "approved manifest")
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise RegistryError("Unsupported manifest schema version")
    entries = data["skills"]
    if not isinstance(entries, list) or len(entries) > 100:
        raise RegistryError("Approved manifest must contain at most 100 skills")
    ids = set()
    sources = set()
    for record in entries:
        validated = validate_entry(record)
        skill_id = validated["id"]
        source = validated["source"]
        identity = (source["repository"], source["path"], source["commit"])
        if skill_id in ids or identity in sources:
            raise RegistryError("Duplicate approved skill identifier or source revision")
        ids.add(skill_id)
        sources.add(identity)
    return entries


def _obstacle(home: Path, target: Path) -> str:
    """Conservatively flag existing destinations or symlinked parent paths."""
    current = target
    while current != home:
        if current.is_symlink():
            return "BLOCKED_SYMLINK"
        if current.exists() and current != target and not current.is_dir():
            return "BLOCKED_NON_DIRECTORY"
        current = current.parent
    if target.parent.exists():
        if not target.is_file():
            return "BLOCKED_EXISTING_DIRECTORY"
        if {entry.name for entry in target.parent.iterdir()} != {"SKILL.md"}:
            return "BLOCKED_EXTRA_FILES"
        return "BLOCKED_EXISTING"
    return "PROPOSED_NEW"


def promotion_plan(manifest: dict, home: Path, targets: tuple[str, ...]) -> dict:
    entries = validate_manifest(manifest)
    if not home.is_absolute():
        raise RegistryError("Home path must be absolute")
    if any(target not in TARGET_PATHS for target in targets):
        raise RegistryError("Invalid target")
    changes = []
    for entry in entries:
        for target in targets:
            destination = home / TARGET_PATHS[target] / entry["id"] / "SKILL.md"
            compatibility = entry["assessment"]["compatibility"][
                "claudeCode" if target == "claude" else "codex"
            ]["status"]
            changes.append({
                "id": entry["id"], "target": target,
                "destination": str(destination),
                "contentSha256": entry["source"]["contentSha256"],
                "sourceCommit": entry["source"]["commit"],
                "compatibility": compatibility,
                "status": ("BLOCKED_UNSUPPORTED" if compatibility == "unsupported"
                           else _obstacle(home, destination)),
                "action": "PROPOSAL_ONLY",
            })
    return {
        "schemaVersion": 1, "operation": "promotion-plan",
        "approvedSkills": len(entries), "targets": list(targets),
        "changes": changes, "executed": False, "filesWritten": 0,
        "requires": [
            "Independent verification of approved GitHub revision and all relevant files",
            "Use sync.py --approved-skills for separate pinned verification; --apply writes only after consent",
            "Explicit owner authorization before any installation",
        ],
        "note": "Preview only; never writes. The opt-in sync.py installer separately verifies approved standalone skills.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("review-template", help="Prepare pending review JSON from inspect report")
    template.add_argument("--report", required=True, type=Path)
    validate = commands.add_parser("validate", help="Validate the versioned approved manifest")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    plan = commands.add_parser("plan", help="Preview target paths; NEVER install")
    plan.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    plan.add_argument("--home", type=Path, default=Path.home())
    plan.add_argument("--target", choices=("claude", "codex", "both"), default="both")
    args = parser.parse_args(argv)
    try:
        if args.command == "review-template":
            result = review_template(load_json(args.report))
        else:
            manifest = load_json(args.manifest)
            if args.command == "validate":
                entries = validate_manifest(manifest)
                result = {"schemaVersion": 1, "operation": "validate",
                          "valid": True, "approvedSkills": len(entries),
                          "note": "Structural validation only; not proof of owner identity or safety."}
            else:
                targets = ("claude", "codex") if args.target == "both" else (args.target,)
                result = promotion_plan(manifest, args.home.expanduser().absolute(), targets)
    except (RegistryError, OSError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
