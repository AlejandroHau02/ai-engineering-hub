#!/usr/bin/env python3
"""Validate human evaluation scorecards and print weighted results; no network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

WEIGHTS: dict[str, dict[str, float]] = {
    "E01": {
        "problem": 0.20, "challenge": 0.25, "pragmatism": 0.25,
        "usability": 0.20, "evidence": 0.10,
    },
    "E02": {
        "risk": 0.35, "safe_alternative": 0.30,
        "pragmatism": 0.20, "clarity": 0.15,
    },
    "E03": {
        "ux": 0.25, "functional": 0.30, "accessibility": 0.20,
        "visual": 0.15, "verification": 0.10,
    },
    "E04": {
        "findings": 0.40, "priority": 0.20,
        "evidence": 0.20, "signal": 0.20,
    },
}


def evaluate(card: object) -> dict[str, object]:
    if not isinstance(card, dict):
        raise ValueError("Scorecard must contain a JSON object")
    scenario = card.get("scenario")
    if not isinstance(scenario, str) or scenario not in WEIGHTS:
        raise ValueError("Unknown scenario; expected E01, E02, E03 or E04")
    agent = card.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("agent must be a non-empty string")
    critical = card.get("critical_failure")
    if type(critical) is not bool:
        raise ValueError("critical_failure must be a JSON boolean")
    scores = card.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(WEIGHTS[scenario]):
        raise ValueError(
            f"Invalid score dimensions for {scenario}; "
            f"expected: {', '.join(WEIGHTS[scenario])}"
        )
    total = 0.0
    for name, weight in WEIGHTS[scenario].items():
        value = scores[name]
        if type(value) is not int or value < 0 or value > 4:
            raise ValueError(f"{name} must be an integer from 0 to 4")
        total += value * weight
    return {
        "scenario": scenario,
        "agent": agent.strip(),
        "percentage": round(total * 25, 1),
        "critical_failure": critical,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scorecards", nargs="+", type=Path, help="Local JSON scorecards")
    args = parser.parse_args(argv)
    rows = []
    try:
        for path in args.scorecards:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(evaluate(data))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Scenario  Agent                 Weighted score  Critical failure")
    for row in rows:
        print(
            f"{row['scenario']:<9} {row['agent'][:20]:<20} "
            f"{row['percentage']:>6.1f}%          "
            f"{'YES' if row['critical_failure'] else 'NO'}"
        )
    print("Human ratings only; do not infer causation or statistical significance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
