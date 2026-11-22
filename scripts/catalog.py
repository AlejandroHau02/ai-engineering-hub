#!/usr/bin/env python3
"""SYNCODE catalog: first-party components and other installed entries (read-only).

    python scripts/catalog.py --format json [--home DIR]

Reports, from the real sources of this checkout or bundle:

* ``components``: the five first-party skills (name and description from the
  ``SKILL.md`` frontmatter) and the specialists of ``specialists/catalog.json``
  (description, skill, focus, deliverable), each with its destination path per
  client relative to home;
* ``unmanaged``: names of other entries found in the clients' skill and agent
  folders that SYNCODE does not manage, with a short description when the
  entry declares one.

Installed status per client is Doctor's job; this command does not repeat it.
No writes, no network, no subprocess. Links and junctions are never followed;
file reads are size-capped and text is sanitized before it is reported.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import unicodedata

from cli_contract import (
    ArgumentContractError, ContractArgumentParser, diagnostic, emit_json, envelope,
    error_object, resolve_home, wants_json,
)
from path_safety import is_link_or_reparse
from skill_scout import frontmatter
from specialists import read_catalog
from sync import ROOT, SKILLS

CLIENT_DIRS = {
    "claude": {"skills": ".claude/skills", "agents": ".claude/agents"},
    "codex": {"skills": ".agents/skills", "agents": ".codex/agents"},
}
AGENT_SUFFIX = {"claude": ".md", "codex": ".toml"}
MAX_ENTRIES = 100          # per folder
MAX_READ = 8 * 1024        # bytes read from any user file (frontmatter only)
MAX_NAME = 64
MAX_TEXT = 200
# Total budget for the unmanaged listing once JSON-escaped to ASCII, well under
# the 1 MiB stdout cap of the desktop runner.
MAX_UNMANAGED_BYTES = 256 * 1024
TOML_DESCRIPTION = re.compile(r'^description\s*=\s*"((?:[^"\\]|\\.){0,2000})"\s*$', re.M)


def clean(text: str | None, limit: int = MAX_TEXT) -> str | None:
    """Printable single-line text: no control or bidi-override characters."""
    if not text:
        return None
    kept = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in text)
    kept = " ".join(kept.split())
    if not kept:
        return None
    return kept if len(kept) <= limit else kept[: limit - 1] + "…"


def _read_small(path: Path) -> str | None:
    """First MAX_READ bytes of a regular, non-link file, decoded leniently.

    Never reads more than MAX_READ bytes, whatever the file size: the
    frontmatter that carries the description sits at the top.
    """
    try:
        if is_link_or_reparse(path) or not path.is_file():
            return None
        with path.open("rb") as handle:
            # Re-check the opened object: a file swapped for a pipe or device
            # between the check and the open must not block the read.
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            return handle.read(MAX_READ).decode("utf-8", errors="replace")
    except OSError:
        return None


def components() -> list[dict]:
    items: list[dict] = []
    for name in SKILLS:
        meta = frontmatter((ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
        items.append({
            "kind": "skill",
            "name": name,
            "description": clean(meta.get("description"), 600),
            "paths": {client: f"{dirs['skills']}/{name}/SKILL.md"
                      for client, dirs in CLIENT_DIRS.items()},
        })
    for entry in read_catalog():
        name = str(entry["name"])
        items.append({
            "kind": "specialist",
            "name": name,
            "description": clean(str(entry["description"]), 600),
            "skill": entry["skill"],
            "focus": [clean(str(item), 600) for item in entry["focus"]],
            "deliverable": clean(str(entry["deliverable"]), 600),
            "paths": {client: f"{dirs['agents']}/{name}{AGENT_SUFFIX[client]}"
                      for client, dirs in CLIENT_DIRS.items()},
        })
    return items


def _describe_skill_dir(path: Path) -> str | None:
    text = _read_small(path / "SKILL.md")
    return clean(frontmatter(text).get("description")) if text else None


def _describe_agent(path: Path) -> str | None:
    text = _read_small(path)
    if not text:
        return None
    if path.suffix == ".toml":
        match = TOML_DESCRIPTION.search(text)
        if not match:
            return None
        try:  # TOML basic strings use JSON-compatible escapes; keep UTF-8 intact
            return clean(json.loads('"' + match[1] + '"'))
        except ValueError:
            return clean(match[1])
    return clean(frontmatter(text).get("description"))


def _linked_ancestor(home: Path, folder: Path) -> bool:
    """True if any component between home and folder is a link or junction."""
    current = folder
    while current != home and current != current.parent:
        if is_link_or_reparse(current):
            return True
        current = current.parent
    return False


def unmanaged(home: Path, items: list[dict]) -> dict:
    """Entries in the clients' folders that are not SYNCODE components."""
    managed = {item["name"] for item in items}
    result: dict[str, dict] = {}
    budget = MAX_UNMANAGED_BYTES
    for client, dirs in CLIENT_DIRS.items():
        result[client] = {}
        for kind, relative in dirs.items():
            folder = home / relative
            found: list[dict] = []
            truncated = False
            try:
                if folder.exists() and not _linked_ancestor(home, folder) and folder.is_dir():
                    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
                        stem = entry.name if kind == "skills" else entry.stem
                        if stem in managed or entry.name.startswith("."):
                            continue
                        if kind == "agents" and entry.suffix != AGENT_SUFFIX[client]:
                            continue
                        if len(found) >= MAX_ENTRIES or budget <= 0:
                            truncated = True
                            break
                        link = is_link_or_reparse(entry)
                        description = None
                        if not link:
                            description = (_describe_skill_dir(entry) if kind == "skills"
                                           and entry.is_dir() else
                                           _describe_agent(entry) if kind == "agents" else None)
                        item = {"name": clean(stem, MAX_NAME) or "?", "link": link,
                                "description": description}
                        budget -= len(json.dumps(item, ensure_ascii=True))
                        found.append(item)
            except OSError:
                pass
            result[client][kind] = {"entries": found, "truncated": truncated}
    return result


def main(argv: list[str] | None = None) -> int:
    json_mode = wants_json(argv)
    home: Path | None = None
    try:
        parser = ContractArgumentParser(description=__doc__, json_errors=json_mode)
        parser.add_argument("--home", type=Path, default=None)
        parser.add_argument("--format", choices=("text", "json"), default="text")
        args = parser.parse_args(argv)
        json_mode = args.format == "json"
        home = resolve_home(args.home)
        items = components()
        report = envelope("ok", home=str(home), components=items,
                          unmanaged=unmanaged(home, items), error=None)
    except (OSError, UnicodeError, ValueError, ArgumentContractError) as error:
        if not json_mode:
            diagnostic(f"ERROR: {error}")
            return 1
        emit_json(envelope("error", home=None if home is None else str(home),
                           components=[], unmanaged={}, error=error_object(error)))
        return 1
    if json_mode:
        emit_json(report)
    else:
        for item in report["components"]:
            print(f"{item['kind']:<10} {item['name']}: {item['description']}")
        for client, kinds in report["unmanaged"].items():
            for kind, data in kinds.items():
                for entry in data["entries"]:
                    print(f"other      {client}/{kind}: {entry['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
