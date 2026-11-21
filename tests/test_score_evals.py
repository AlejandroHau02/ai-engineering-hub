"""Contract tests for human-scorecard validation; no model/API requests."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/score_evals.py"


class ScoreEvalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "card.json"
        self.card = {
            "scenario": "E02",
            "agent": "Synthetic agent",
            "scores": {
                "risk": 4, "safe_alternative": 3,
                "pragmatism": 2, "clarity": 3,
            },
            "critical_failure": False,
        }

    def run_card(self):
        self.path.write_text(json.dumps(self.card), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.path)],
            capture_output=True, text=True, check=False,
        )

    def test_weighted_score(self):
        result = self.run_card()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("78.8%", result.stdout)
        self.assertIn("Synthetic agent", result.stdout)

    def test_critical_failure_is_visible(self):
        self.card["critical_failure"] = True
        result = self.run_card()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("YES", result.stdout)

    def test_missing_dimension_rejected(self):
        del self.card["scores"]["risk"]
        result = self.run_card()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid score dimensions", result.stderr)

    def test_score_out_of_range_rejected(self):
        self.card["scores"]["risk"] = 5
        result = self.run_card()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integer from 0 to 4", result.stderr)

    def test_boolean_score_rejected(self):
        self.card["scores"]["risk"] = True
        result = self.run_card()
        self.assertNotEqual(result.returncode, 0)

    def test_unknown_scenario_rejected(self):
        self.card["scenario"] = "E99"
        result = self.run_card()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown scenario", result.stderr)


if __name__ == "__main__":
    unittest.main()
