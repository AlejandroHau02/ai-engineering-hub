"""Bounded, read-only inventory of actual GitHub SKILL.md files and companion files.

No filesystem writes, execution, installation or arbitrary-origin network requests.
The caller supplies the constrained GitHub JSON fetcher.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from urllib.parse import quote

GITHUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z")
FULL_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
MAX_LISTED = 40
MAX_INSPECTED = 12
MAX_EACH_FILE = 32 * 1024
MAX_TOTAL_TEXT = 128 * 1024
TEXT_EXTENSIONS = frozenset((".md", ".txt", ".py", ".js", ".ts", ".sh",
                             ".ps1", ".json", ".yaml", ".yml", ".toml"))
RELATED_FOLDERS = frozenset(("scripts", "references", "assets"))
ROOT_MANIFESTS = frozenset(("requirements.txt", "package.json",
                            "pyproject.toml", "LICENSE", "LICENSE.md", "LICENSE.txt"))


class InventoryError(ValueError):
    """Invalid source, unsafe path, or malformed remote response."""


def repository_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise InventoryError("Repository must use owner/repo")
    names = value.split("/")
    if len(names) != 2 or not all(GITHUB_NAME.fullmatch(name) for name in names):
        raise InventoryError("Repository must use a safe owner/repo identifier")
    return names[0], names[1]


def _git_tree(owner: str, repo: str, commit: str, get_json) -> dict:
    if not FULL_SHA.fullmatch(commit):
        raise InventoryError("A complete commit SHA is required")
    tree = get_json(f"/repos/{owner}/{repo}/git/trees/{commit}?recursive=1")
    if not isinstance(tree.get("tree"), list) or type(tree.get("truncated")) is not bool:
        raise InventoryError("Malformed GitHub tree response")
    return tree


def _safe_path(path: str) -> bool:
    return bool(path and isinstance(path, str) and
                all(segment not in ("", ".", "..") and "\\" not in segment
                    and "%" not in segment and not any(ord(ch) < 32 for ch in segment)
                    for segment in path.split("/")))


def locate_skills(repo_name: str, limit: int = 20, get_json=None) -> dict:
    """Find real tracked SKILL.md paths, pinned to a GitHub commit SHA.

    The file is not parsed here, so paths are candidates, NOT approved skills.
    """
    owner, repo = repository_name(repo_name)
    if type(limit) is not int or not 1 <= limit <= 30:
        raise InventoryError("Limit must be between 1 and 30")
    if get_json is None:
        raise InventoryError("A GitHub JSON fetcher is required")
    root = f"/repos/{owner}/{repo}"
    metadata = get_json(root)
    branch = metadata.get("default_branch")
    if not isinstance(branch, str) or not branch or len(branch) > 200:
        raise InventoryError("Missing default branch")
    pinned = get_json(root + "/commits/" + quote(branch, safe=""))
    commit = pinned.get("sha")
    if not isinstance(commit, str) or not FULL_SHA.fullmatch(commit):
        raise InventoryError("GitHub returned an invalid commit SHA")
    tree = _git_tree(owner, repo, commit, get_json)
    entries = []
    for item in tree["tree"]:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if (isinstance(path, str) and _safe_path(path)
            and (path == "SKILL.md" or path.endswith("/SKILL.md"))
            and item.get("type") == "blob" and item.get("mode") in ("100644", "100755")):
            entries.append({
                "path": path,
                "url": f"https://github.com/{owner}/{repo}/blob/{commit.lower()}/{path}",
                "blobSha": item.get("sha"), "size": item.get("size"),
                "verification": "tracked-file-not-reviewed",
            })
    entries.sort(key=lambda x: x["path"])
    return {
        "schemaVersion": 1, "operation": "locate", "provider": "github",
        "repository": f"{owner}/{repo}", "defaultBranch": branch,
        "pinnedCommit": commit.lower(),
        "treeComplete": not tree["truncated"],
        "pathsFound": len(entries), "candidates": entries[:limit],
        "resultsLimited": len(entries) > limit,
        "decision": "review-required",
        "note": "Pinned tracked paths only; content, license and compatibility are not verified.",
    }


def _related_path(base: str, path: str) -> bool:
    if not _safe_path(path) or (base and not path.startswith(base)):
        return False
    remainder = path[len(base):]
    if remainder == "SKILL.md" or not remainder:
        return False
    parts = remainder.split("/")
    if len(parts) == 1:
        return (parts[0] in ROOT_MANIFESTS or
                "." + parts[0].rsplit(".", 1)[-1].lower() in TEXT_EXTENSIONS)
    return parts[0] in RELATED_FOLDERS


def _priority(path: str) -> tuple[int, str]:
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return (0 if suffix in (".sh", ".py", ".js", ".ts", ".ps1") else 1, path)


def _decode_text_file(data: dict, max_size: int) -> bytes:
    if data.get("type") != "file" or data.get("encoding") != "base64":
        raise InventoryError("Related path was not returned as a regular file")
    value = data.get("content")
    if not isinstance(value, str):
        raise InventoryError("Related file content missing")
    try:
        raw = base64.b64decode(value.replace("\n", ""), validate=True)
    except (ValueError, binascii.Error):
        raise InventoryError("Related file has invalid base64") from None
    if len(raw) > max_size:
        raise InventoryError("Related file changed size or exceeded scan limit")
    return raw


def inspect_related(owner: str, repo: str, commit: str, skill_path: str,
                    get_json, risk_rules) -> dict:
    """Inspect only selected sibling files, bounded by count and byte caps.

    A partial scan, an unscanned file or symlink always remains visible.
    """
    repository_name(f"{owner}/{repo}")
    if not _safe_path(skill_path) or not (
        skill_path == "SKILL.md" or skill_path.endswith("/SKILL.md")
    ):
        raise InventoryError("Unsafe skill path")
    tree = _git_tree(owner, repo, commit, get_json)
    base = skill_path.rpartition("/")[0]
    base = base + "/" if base else ""
    entries = [
        entry for entry in tree["tree"]
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and _related_path(base, entry["path"])
        and entry.get("type") in ("blob", "tree")
    ]
    # Tree directories are not files; only leaf paths count in the inventory.
    files = sorted((item for item in entries if item.get("type") == "blob"),
                   key=lambda x: _priority(x["path"]))
    listed = [
        {"path": entry["path"], "bytes": entry.get("size"),
         "mode": entry.get("mode"), "inspection": "not-scanned"}
        for entry in files[:MAX_LISTED]
    ]
    inspected = []
    findings = []
    consumed = 0
    skipped = 0
    for entry in files:
        if len(inspected) >= MAX_INSPECTED:
            break
        path = entry["path"]
        extension = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        filename = path.rsplit("/", 1)[-1]
        size = entry.get("size")
        if (entry.get("mode") not in ("100644", "100755")
            or (extension not in TEXT_EXTENSIONS and filename not in ROOT_MANIFESTS)
            or type(size) is not int or size > MAX_EACH_FILE
            or consumed + size > MAX_TOTAL_TEXT):
            skipped += 1
            continue
        quoted_path = "/".join(quote(part, safe="") for part in path.split("/"))
        result = get_json(f"/repos/{owner}/{repo}/contents/{quoted_path}?ref={commit}")
        raw = _decode_text_file(result, MAX_EACH_FILE)
        consumed += len(raw)
        if len(raw) != size:
            raise InventoryError("Related file size differs from pinned Git tree")
        try:
            content = raw.decode("utf-8")
        except UnicodeError:
            skipped += 1
            continue
        file_findings = []
        for category, rule in risk_rules:
            lines = [i for i, line in enumerate(content.splitlines(), 1) if rule.search(line)]
            if lines:
                finding = {"type": category, "path": path, "lines": lines[:20]}
                file_findings.append(finding)
                findings.append(finding)
        inspected.append({"path": path,
                          "sha256": hashlib.sha256(raw).hexdigest(),
                          "bytes": size, "findings": file_findings})
    scanned_paths = {item["path"] for item in inspected}
    for entry in listed:
        if entry["path"] in scanned_paths:
            entry["inspection"] = "static-text-scan"
    complete = (not tree["truncated"] and len(files) == len(inspected))
    return {
        "treeComplete": not tree["truncated"],
        "filesFound": len(files),
        "filesListed": listed,
        "listingLimited": len(files) > MAX_LISTED,
        "inspected": inspected,
        "inspectedBytes": consumed,
        "filesNotScanned": len(files) - len(inspected),
        "status": "complete-text-scan" if complete else "partial-review-required",
        "findings": findings,
        "note": "Only allowlisted text files within the skill directory are scanned; "
                "linked files elsewhere, content semantics, binaries and dependencies "
                "are not security-reviewed.",
    }
