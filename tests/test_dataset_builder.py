import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "build_dataset.py"
TICKS = ROOT / "tests" / "fixtures" / "ticks_v2_valid.csv"


class DatasetBuilderTests(unittest.TestCase):
    def test_builds_isolated_daily_dataset(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / "raw"
            output = Path(folder) / "output"
            raw.mkdir()
            tick_path = raw / "ticks_session.csv"
            tick_path.write_text(TICKS.read_text(encoding="utf-8"), encoding="utf-8")
            metadata = raw / "metadata_session.csv"
            metadata.write_text(
                "key,value\n"
                "schema_version,2\n"
                "session_id,test_v2\n"
                "broker_company,Example Broker\n"
                "broker_server,Demo-1\n"
                "terminal_id,TERM001\n"
                "symbol,XAUUSD\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(BUILDER), str(raw), "--output", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["inserted"], 2)
            self.assertEqual(len(manifest["datasets"]), 1)
            dataset = output / manifest["datasets"][0]["file"]
            with dataset.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["sequence"] for row in rows], ["1", "2"])
            self.assertEqual({row["broker_server"] for row in rows}, {"Demo-1"})

    def test_requires_matching_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(folder) / "raw"
            output = Path(folder) / "output"
            raw.mkdir()
            (raw / "ticks_session.csv").write_text(
                TICKS.read_text(encoding="utf-8"), encoding="utf-8"
            )
            completed = subprocess.run(
                [sys.executable, str(BUILDER), str(raw), "--output", str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
