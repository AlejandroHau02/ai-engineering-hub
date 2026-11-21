"""Render a small trusted specialist catalog to Claude Code and Codex native agent files.

This module does not run agents or any third-party code.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "specialists" / "catalog.json"
KNOWN_SKILLS = frozenset(
    {"architecture-review", "ux-ui", "backend-engineering", "code-review"}
)
NAME_RE = re.compile(r"hub-[a-z][a-z0-9-]{0,48}\Z")
SPEC_KEYS = frozenset({"name", "description", "skill", "focus", "deliverable"})
CLAUDE_READ_ONLY_TOOLS = "Read, Glob, Grep"


def read_catalog(path: Path = CATALOG) -> tuple[dict[str, object], ...]:
    """Reject malformed schemas before any destination is touched."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "release", "specialists"}:
        raise ValueError("Invalid specialist catalog structure")
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1:
        raise ValueError("Unsupported specialist catalog schema")
    if not isinstance(data["release"], str) or not data["release"].strip():
        raise ValueError("Missing release")
    entries = data["specialists"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 12:
        raise ValueError("Catalog must have 1–12 specialists")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != SPEC_KEYS:
            raise ValueError("Invalid specialist fields")
        name = entry["name"]
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError(f"Invalid specialist name: {name!r}")
        if name in seen:
            raise ValueError(f"Duplicate specialist: {name}")
        seen.add(name)
        if entry["skill"] not in KNOWN_SKILLS:
            raise ValueError(f"Unsupported skill for {name}")
        for field in ("description", "deliverable"):
            value = entry[field]
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ValueError(f"Invalid {field} for {name}")
            if len(value) > 1200:
                raise ValueError(f"{field} too long for {name}")
        focus = entry["focus"]
        if (
            not isinstance(focus, list)
            or not 1 <= len(focus) <= 6
            or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in focus)
        ):
            raise ValueError(f"Invalid focus for {name}")
    return tuple(entries)


def prompt(entry: dict[str, object]) -> str:
    skill = entry["skill"]
    focus = "\n".join(f"- {item}" for item in entry["focus"])
    return (
        f"You are {entry['name']}, a focused independent engineering advisor.\n"
        f"Consult the globally installed {skill} skill if available; use the "
        "codebase's instructions and real evidence, not generic checklists.\n\n"
        "Scope and constraints:\n"
        "- Analyze only. Do not edit files, install dependencies, commit, push, "
        "deploy, change permissions, or execute destructive commands.\n"
        "- Read only what is necessary; do not expose repository secrets or "
        "copy proprietary code into an external/public location.\n"
        "- Request clarification only for a decision that materially changes "
        "the assessment.\n"
        "- Return concise, actionable evidence to the parent agent. Do not "
        "spawn another specialist for routine work.\n\n"
        "Specific review focus:\n"
        f"{focus}\n\n"
        f"Expected output: {entry['deliverable']}\n"
        "Clearly identify checks actually executed, assumptions and items not verified."
    )


def render_claude(entry: dict[str, object]) -> str:
    name = entry["name"]
    description = json.dumps(entry["description"], ensure_ascii=False)
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"tools: {CLAUDE_READ_ONLY_TOOLS}\n"
        "permissionMode: plan\n"
        "skills:\n"
        f"  - {entry['skill']}\n"
        "---\n\n"
        + prompt(entry) + "\n"
    )


def render_codex(entry: dict[str, object]) -> str:
    # TOML's quoted strings share the JSON escaping used here.
    name = json.dumps(entry["name"], ensure_ascii=False)
    desc = json.dumps(entry["description"], ensure_ascii=False)
    instructions = json.dumps(prompt(entry), ensure_ascii=False)
    return (
        f"name = {name}\n"
        f"description = {desc}\n"
        f"developer_instructions = {instructions}\n"
    )


def files_for_target(target: str) -> dict[Path, str]:
    """Relative paths are fixed internally; catalog strings cannot choose destinations."""
    if target not in ("claude", "codex"):
        raise ValueError(f"Unsupported target: {target}")
    base = Path(".claude/agents") if target == "claude" else Path(".codex/agents")
    suffix = ".md" if target == "claude" else ".toml"
    renderer = render_claude if target == "claude" else render_codex
    return {
        base / (entry["name"] + suffix): renderer(entry)
        for entry in read_catalog()
    }
