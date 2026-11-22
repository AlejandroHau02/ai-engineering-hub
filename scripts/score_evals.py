#!/usr/bin/env python3
"""Validate human evaluation scorecards and print weighted results; no network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

WEIGHTS: dict[str, dict[str, int]] = {
    "E01": {
        "problem": 20, "challenge": 25, "pragmatism": 25,
        "usability": 20, "evidence": 10,
    },
    "E02": {
        "risk": 35, "safe_alternative": 30,
        "pragmatism": 20, "clarity": 15,
    },
    "E03": {
        "ux": 25, "functional": 30, "accessibility": 20,
        "visual": 15, "verification": 10,
    },
    "E04": {
        "findings": 40, "priority": 20,
        "evidence": 20, "signal": 20,
    },
    "E05": {
        "operation_authorization": 35, "client_boundary": 20,
        "minimal_fix": 25, "negative_test": 20,
    },
    "E08": {
        "threat_model": 25, "findings": 35,
        "evidence": 25, "remediation": 15,
    },
}


def evaluate(card: object) -> dict[str, object]:
    if not isinstance(card, dict):
        raise ValueError("Scorecard must contain a JSON object")
    scenario = card.get("scenario")
    if not isinstance(scenario, str) or scenario not in WEIGHTS:
        raise ValueError("Unknown scenario; expected " + ", ".join(WEIGHTS))
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
        "percentage": round(total / 4, 1),
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
