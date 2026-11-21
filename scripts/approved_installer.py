"""Fetch and verify approved, stand-alone SKILL.md files before global sync.

Deliberately v0.3-scoped: multi-file skills are NOT installable yet. Reject all
additional paths in the skill directory rather than silently dropping scripts,
references or other dependencies. No execution, checkout, archive extraction or
writes occur here; sync.py owns installation planning and application.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path

from skill_registry import RegistryError, load_json, validate_manifest, TARGET_PATHS
from skill_scout import request_json, MAX_SKILL_BYTES, frontmatter

MANIFEST = Path(__file__).resolve().parents[1] / "registry" / "approved-skills.json"


class PromotionError(ValueError):
    """Fail-closed error while verifying an approved source."""


def _verify_source(entry: dict, get_json=request_json) -> str:
    """Fetch pinned blob and tree; return exact UTF-8 text only after verification."""
    source = entry["source"]
    owner, repo = source["repository"].split("/")
    commit = source["commit"]
    path = source["path"]
    root = f"/repos/{owner}/{repo}"
    tree = get_json(f"{root}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise PromotionError(f"{entry['id']}: incomplete or invalid Git tree")

    # Restrict installation to a single standalone SKILL.md. This avoids claiming
    # that a partially copied skill with missing scripts/references is usable.
    directory = path.rpartition("/")[0]
    prefix = directory + "/" if directory else ""
    siblings = [
        node for node in tree["tree"]
        if isinstance(node, dict) and isinstance(node.get("path"), str)
        and node["path"].startswith(prefix)
        and node["path"] != path
    ]
    if siblings:
        raise PromotionError(f"{entry['id']}: multi-file skill is not supported; "
                             "companion files require a separate reviewed bundle manifest")
    matching = [node for node in tree["tree"]
                if isinstance(node, dict) and node.get("path") == path]
    if (len(matching) != 1 or matching[0].get("type") != "blob"
        or matching[0].get("mode") not in ("100644", "100755")
        or matching[0].get("sha") != source["blobSha"]):
        raise PromotionError(f"{entry['id']}: pinned Git tree does not match approved blob")

    # GitHub Contents returns base64 with a SHA-1 Git blob ID. Neither the
    # metadata hash nor the source URL alone is trusted: both hashes are checked.
    from urllib.parse import quote
    quoted = "/".join(quote(component, safe="") for component in path.split("/"))
    data = get_json(f"{root}/contents/{quoted}?ref={commit}")
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        raise PromotionError(f"{entry['id']}: source is not a regular base64-encoded file")
    encoded = data.get("content")
    if not isinstance(encoded, str) or len(encoded) > MAX_SKILL_BYTES * 2:
        raise PromotionError(f"{entry['id']}: missing or oversized source payload")
    try:
        raw = base64.b64decode(encoded.replace("\n", ""), validate=True)
    except (binascii.Error, ValueError):
        raise PromotionError(f"{entry['id']}: invalid base64 payload") from None
    if len(raw) > MAX_SKILL_BYTES:
        raise PromotionError(f"{entry['id']}: source exceeds inspection limit")
    if isinstance(data.get("size"), int) and data["size"] != len(raw):
        raise PromotionError(f"{entry['id']}: contents size differs from pinned source")
    if isinstance(matching[0].get("size"), int) and matching[0]["size"] != len(raw):
        raise PromotionError(f"{entry['id']}: Git tree size differs from pinned source")
    git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if data.get("sha") != source["blobSha"] or git_blob != source["blobSha"]:
        raise PromotionError(f"{entry['id']}: Git blob SHA mismatch")
    if hashlib.sha256(raw).hexdigest() != source["contentSha256"]:
        raise PromotionError(f"{entry['id']}: SHA-256 mismatch")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PromotionError(f"{entry['id']}: SKILL.md must be UTF-8") from None
    if "\r" in content or "\0" in content:
        raise PromotionError(f"{entry['id']}: unsupported CR/NUL bytes in SKILL.md")
    if frontmatter(content).get("name") != entry["id"]:
        raise PromotionError(f"{entry['id']}: SKILL.md frontmatter name does not match approval")
    return content


def approved_candidates(targets: tuple[str, ...], manifest_path: Path = MANIFEST,
                        get_json=request_json) -> list[tuple[Path, str]]:
    """Return relative destination and source content; never write or execute."""
    approved = validate_manifest(load_json(manifest_path))
    candidates: list[tuple[Path, str]] = []
    for entry in approved:
        for target in targets:
            if target not in TARGET_PATHS:
                raise PromotionError(f"Invalid target: {target}")
            compat = entry["assessment"]["compatibility"][
                "claudeCode" if target == "claude" else "codex"
            ]["status"]
            if compat != "tested":
                raise PromotionError(
                    f"{entry['id']}: target {target} is not approved as tested; "
                    "select a compatible --target before applying"
                )
        content = _verify_source(entry, get_json)
        for target in targets:
            candidates.append((Path(TARGET_PATHS[target]) / entry["id"] / "SKILL.md", content))
    return candidates
