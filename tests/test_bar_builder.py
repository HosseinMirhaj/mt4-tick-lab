import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "build_bars.py"
FIXTURE = ROOT / "tests" / "fixtures" / "ticks_v2_valid.csv"


class BarBuilderTests(unittest.TestCase):
    def test_builds_correct_m1_ohlc_from_unsorted_ticks(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "ticks_20260912.csv"
            output = Path(folder) / "bars"
            with FIXTURE.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
                fields = stream.seek(0) or next(csv.reader(stream))
            enriched = []
            for row in reversed(rows):
                enriched.append({
                    "broker_company": "Example Broker", "broker_server": "Demo-1",
                    "terminal_id": "TERM001", "symbol": "XAUUSD", **row,
                })
            fieldnames = list(enriched[0])
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(enriched)

            completed = subprocess.run(
                [sys.executable, str(BUILDER), str(source), "--output", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            files = list(output.rglob("bars_M1_*.csv"))
            self.assertEqual(len(files), 1)
            with files[0].open(encoding="utf-8", newline="") as stream:
                bars = list(csv.DictReader(stream))
            self.assertEqual(len(bars), 1)
            bar = bars[0]
            self.assertEqual(bar["broker_time"], "2026-09-12T10:00:00")
            self.assertEqual(bar["open"], "2500.10")
            self.assertEqual(bar["high"], "2500.20")
            self.assertEqual(bar["low"], "2500.10")
            self.assertEqual(bar["close"], "2500.20")
            self.assertEqual(bar["tick_volume"], "2")
            self.assertEqual(bar["spread_avg"], "20.00")


if __name__ == "__main__":
    unittest.main()
