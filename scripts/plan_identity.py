"""Deterministic identity of an installation plan (bound plans, Phase 0B).

A plan fingerprint is a *freshness* check, not an authorization: it lets a
client prove that ``sync.py --apply --expect-plan <fingerprint>`` executes
exactly the batch the user reviewed in a dry-run. Human confirmation is still
required by the client. Stdlib only.

Clients must treat the fingerprint as opaque and echo it back unchanged; they
cannot recompute it (original-byte hashes are deliberately not published and
the home is normalized). Canonical document:

    {"v": 1, "home": <normalized resolved home>, "targets": [...],
     "flags": {"upgradeSkills", "specialists", "approvedSkills"},
     "ops": [{"kind", "path", "newSha256", "oldSha256"}, ...]}

``ops`` keep the order produced by ``build_plan``; ``path`` is POSIX-relative
to home; ``oldSha256`` is the SHA-256 of the destination's raw bytes when the
plan was built, or null when the destination did not exist.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

ALGORITHM = "syncode-plan-v1"
PREFIX = ALGORITHM + ":sha256:"


def normalized_home(home: Path) -> str:
    """Resolved home as compared in fingerprints.

    On Windows, paths are case-insensitive and ``normcase`` also unifies
    separators, so ``C:\\Users\\X`` and ``c:/users/x`` bind the same plan.
    Other aliases (8.3 names, junctions) are resolved by ``Path.resolve``
    before this point; a residual alias can only cause a safe ``stale-plan``.
    """
    text = str(home)
    return os.path.normcase(text) if os.name == "nt" else text


def canonical_document(home: Path, targets, flags: dict, ops: list[dict]) -> dict:
    return {
        "v": 1,
        "home": normalized_home(home),
        "targets": list(targets),
        "flags": {
            "upgradeSkills": bool(flags["upgradeSkills"]),
            "specialists": bool(flags["specialists"]),
            "approvedSkills": bool(flags["approvedSkills"]),
        },
        "ops": [
            {"kind": op["kind"], "path": op["path"],
             "newSha256": op["newSha256"], "oldSha256": op["oldSha256"]}
            for op in ops
        ],
    }


def fingerprint(home: Path, targets, flags: dict, ops: list[dict]) -> str:
    """Return ``syncode-plan-v1:sha256:<hex>`` for the canonical document."""
    raw = json.dumps(canonical_document(home, targets, flags, ops),
                     sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return PREFIX + hashlib.sha256(raw.encode("ascii")).hexdigest()


def is_well_formed(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    digest = value[len(PREFIX):]
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
