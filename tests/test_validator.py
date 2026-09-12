import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "validate_ticks.py"
FIXTURE = ROOT / "tests" / "fixtures" / "ticks_valid.csv"
FIXTURE_V2 = ROOT / "tests" / "fixtures" / "ticks_v2_valid.csv"


class ValidatorCliTests(unittest.TestCase):
    def test_valid_fixture_passes(self):
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), str(FIXTURE)],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn("Summary: PASS", completed.stdout)

    def test_v2_fixture_reports_price_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), str(FIXTURE_V2), "--json", str(report)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            result = json.loads(report.read_text(encoding="utf-8"))["results"][0]
            self.assertEqual(result["quote_source_counts"], {"market_info": 2})
            self.assertEqual(result["raw_market_disagreements"], 2)
            self.assertEqual(result["zero_spread_percent"], 0.0)

    def test_sequence_gap_fails(self):
        content = FIXTURE.read_text(encoding="utf-8").replace(
            "test_session,2,", "test_session,4,", 1
        )
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "ticks_gap.csv"
            report = Path(folder) / "report.json"
            source.write_text(content, encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), str(source), "--json", str(report)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAIL")
            self.assertGreater(payload["results"][0]["sequence_gaps"], 0)


if __name__ == "__main__":
    unittest.main()
