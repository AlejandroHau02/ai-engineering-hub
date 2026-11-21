#!/usr/bin/env python3
"""Read-only GitHub skill discovery and pinned SKILL.md inspection.

Untrusted SKILL.md content is parsed as data only, never executed or installed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.github.com"
USER_AGENT = "ai-engineering-hub-skill-scout/0.3"
MAX_RESPONSE = 512 * 1024
MAX_SKILL_BYTES = 128 * 1024
NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z")
SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
SKILL_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
RISK_RULES = (
    ("remote-script-execution", re.compile(r"(?:curl|wget)\b[^\n|]{0,250}\|\s*(?:sh|bash)\b|\biwr\b[^\n|]*\|\s*iex\b", re.I)),
    ("destructive-command", re.compile(r"\brm\s+-[a-z]*r[a-z]*f\b|\bformat\s+[a-z]:|\bRemove-Item\b[^\n]*-Recurse", re.I)),
    ("credential-access", re.compile(r"(?<![A-Za-z0-9_])(?:\.env|id_rsa|credentials\.json|GITHUB_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY)\b", re.I)),
    ("dependency-install", re.compile(r"\b(?:pip3?|uv)\s+install\b|\b(?:npm|pnpm|yarn)\s+(?:install|add)\b|\bapt(?:-get)?\s+install\b", re.I)),
    ("external-network", re.compile(r"https?://|\b(?:fetch|requests\.(?:get|post)|urllib\.request)\s*\(", re.I)),
    ("mcp-reference", re.compile(r"\bMCP\b|\bmodel.context.protocol\b", re.I)),
)


class ScoutError(ValueError):
    """Safe, user-readable inspection error."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ScoutError("GitHub API redirect refused")


def request_json(path: str) -> dict:
    """Only fetch fixed-origin public GitHub API paths, with no redirects."""
    if not path.startswith("/") or path.startswith("//"):
        raise ScoutError("Invalid GitHub API path")
    opener = build_opener(NoRedirect())
    req = Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with opener.open(req, timeout=12) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except HTTPError as exc:
        raise ScoutError(f"GitHub API returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ScoutError(f"GitHub API unavailable: {type(exc).__name__}") from None
    if len(raw) > MAX_RESPONSE:
        raise ScoutError("GitHub API response is too large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ScoutError("Invalid GitHub API JSON") from None
    if not isinstance(data, dict):
        raise ScoutError("Unexpected GitHub API response")
    return data


def validate_slug(value: str) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ScoutError("Invalid GitHub owner or repository name")
    return value


def parse_skill_url(value: str) -> tuple[str, str, str, str]:
    """Require an immutable full SHA and a SKILL.md path under github.com."""
    if any(ord(char) < 33 for char in value):
        raise ScoutError("Invalid whitespace or control character in URL")
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment:
        raise ScoutError("Expected a clean https://github.com/.../blob/<40-char-sha>/.../SKILL.md URL")
    chunks = parts.path.strip("/").split("/")
    if len(chunks) < 5 or chunks[2] != "blob":
        raise ScoutError("Expected a GitHub blob URL")
    owner, repo, _, commit, *file_parts = chunks
    validate_slug(owner)
    validate_slug(repo)
    if not SHA_RE.fullmatch(commit):
        raise ScoutError("Pin to a complete 40-character commit SHA, not a branch or tag")
    if (not file_parts or file_parts[-1] != "SKILL.md" or
        any(p in ("", ".", "..") or "%" in p or "\\" in p for p in file_parts)):
        raise ScoutError("Expected a safe repository path ending with SKILL.md")
    return owner, repo, commit.lower(), "/".join(file_parts)


def discover(query: str, limit: int = 5, get_json=request_json) -> dict:
    if not 2 <= len(query.strip()) <= 100 or any(ord(x) < 32 for x in query):
        raise ScoutError("Search query must be 2–100 printable characters")
    if not 1 <= limit <= 10:
        raise ScoutError("Limit must be between 1 and 10")
    # Repository search produces leads, not verified SKILL.md matches.
    params = urlencode({"q": query.strip() + " in:name,description", "per_page": limit})
    response = get_json("/search/repositories?" + params)
    items = response.get("items")
    if not isinstance(items, list):
        raise ScoutError("Malformed repository search response")
    leads = []
    for entry in items[:limit]:
        if not isinstance(entry, dict):
            continue
        leads.append({
            "repository": entry.get("full_name"),
            "url": entry.get("html_url"),
            "description": entry.get("description"),
            "default_branch": entry.get("default_branch"),
            "archived": entry.get("archived"),
            "verification": "unverified-repository-lead",
        })
    return {"schemaVersion": 1, "operation": "discover", "provider": "github",
            "query": query.strip(), "candidates": leads,
            "note": "Repository leads only. Locate SKILL.md and pin a full commit SHA before inspecting."}


def frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i in range(1, min(len(lines), 100)) if lines[i].strip() == "---")
    except StopIteration:
        return {}
    result = {}
    for line in lines[1:end]:
        match = re.fullmatch(r"(name|description):\s*(.*?)\s*", line)
        if match:
            result[match[1]] = match[2].strip("'\"")
    return result


def inspect(url: str, get_json=request_json) -> dict:
    owner, repo, commit, path = parse_skill_url(url)
    root = f"/repos/{quote(owner)}/{quote(repo)}"
    metadata = get_json(root)
    file_data = get_json(f"{root}/contents/{'/'.join(quote(p) for p in path.split('/'))}?ref={commit}")
    if file_data.get("type") != "file" or file_data.get("encoding") != "base64":
        raise ScoutError("GitHub did not return an inspectable file")
    try:
        raw = base64.b64decode(file_data["content"].replace("\n", ""), validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise ScoutError("Invalid GitHub file encoding") from None
    if len(raw) > MAX_SKILL_BYTES:
        raise ScoutError("SKILL.md exceeds 128 KiB inspection limit")
    try:
        content = raw.decode("utf-8")
    except UnicodeError:
        raise ScoutError("SKILL.md must be UTF-8") from None
    fields = frontmatter(content)
    findings = []
    if not SKILL_NAME_RE.fullmatch(fields.get("name", "")):
        findings.append({"type": "invalid-or-missing-name", "lines": []})
    if not fields.get("description"):
        findings.append({"type": "missing-description", "lines": []})
    for category, rule in RISK_RULES:
        matches = [i for i, line in enumerate(content.splitlines(), 1) if rule.search(line)]
        if matches:
            findings.append({"type": category, "lines": matches[:20]})
    lic = metadata.get("license")
    license_id = lic.get("spdx_id") if isinstance(lic, dict) else None
    if not license_id or license_id == "NOASSERTION":
        findings.append({"type": "license-unverified", "lines": []})
    if metadata.get("archived"):
        findings.append({"type": "repository-archived", "lines": []})
    owner_data = metadata.get("owner")
    maintainer = owner_data.get("login") if isinstance(owner_data, dict) else None
    return {
        "schemaVersion": 1, "operation": "inspect", "provider": "github",
        "source": {"url": url, "repository": f"{owner}/{repo}", "path": path,
                   "commit": commit, "blobSha": file_data.get("sha"),
                   "contentSha256": hashlib.sha256(raw).hexdigest(),
                   "maintainer": maintainer, "repositoryLicense": license_id},
        "skill": {"name": fields.get("name"), "description": fields.get("description"),
                  "bytes": len(raw)},
        "compatibility": {"claudeCode": "not-verified", "codex": "not-verified"},
        "findings": findings,
        "decision": "manual-review-required",
        "notes": [
            "Heuristic static inspection only; an empty finding list does not prove safety.",
            "Repository license metadata does not establish the license of this file.",
            "Untrusted instructions were never executed or installed.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("discover", help="Find unverified GitHub repository leads")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)
    check = commands.add_parser("inspect", help="Read and assess a pinned GitHub SKILL.md")
    check.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        report = discover(args.query, args.limit) if args.command == "discover" else inspect(args.url)
    except ScoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
