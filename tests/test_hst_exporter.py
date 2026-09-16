import csv
import datetime as dt
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "export_hst.py"
HEADER = struct.Struct("<i64s12siiii13i")
RECORD = struct.Struct("<qddddqiq")
FIELDS = [
    "broker_company", "broker_server", "terminal_id", "symbol", "timeframe",
    "broker_time", "open", "high", "low", "close", "tick_volume",
    "real_volume", "spread_close",
]


class HstExporterTests(unittest.TestCase):
    def write_bars(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def test_exports_hst_v401_header_and_records(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bars_M1_20260914.csv"
            output = Path(folder) / "hst"
            common = {
                "broker_company": "Example Broker", "broker_server": "Demo-1",
                "terminal_id": "TERM001", "symbol": "XAUUSD", "timeframe": "M1",
                "real_volume": "0",
            }
            self.write_bars(source, [
                {**common, "broker_time": "2026-09-14T10:01:00", "open": "2500.20",
                 "high": "2500.30", "low": "2500.10", "close": "2500.25",
                 "tick_volume": "7", "spread_close": "12.00"},
                {**common, "broker_time": "2026-09-14T10:00:00", "open": "2500.10",
                 "high": "2500.20", "low": "2500.00", "close": "2500.20",
                 "tick_volume": "5", "spread_close": "10.00"},
            ])
            completed = subprocess.run(
                [sys.executable, str(EXPORTER), str(source), "--output", str(output),
                 "--symbol", "GOLD"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            files = list(output.rglob("*.hst"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, "GOLD1.hst")
            payload = files[0].read_bytes()
            self.assertEqual(len(payload), 148 + 2 * 60)

            header = HEADER.unpack_from(payload)
            self.assertEqual(header[0], 401)
            self.assertEqual(header[2].rstrip(b"\0"), b"GOLD")
            self.assertEqual(header[3], 1)
            self.assertEqual(header[4], 2)

            first = RECORD.unpack_from(payload, HEADER.size)
            expected_time = int(dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc).timestamp())
            self.assertEqual(first[0], expected_time)
            self.assertEqual(first[1:5], (2500.10, 2500.20, 2500.00, 2500.20))
            self.assertEqual(first[5:], (5, 10, 0))

    def test_rejects_duplicate_broker_minute(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bars_M1_duplicate.csv"
            row = {
                "broker_company": "Example", "broker_server": "Demo",
                "terminal_id": "TERM", "symbol": "EURUSD", "timeframe": "M1",
                "broker_time": "2026-09-14T10:00:00", "open": "1.10000",
                "high": "1.10010", "low": "1.09990", "close": "1.10000",
                "tick_volume": "2", "real_volume": "0", "spread_close": "10.00",
            }
            self.write_bars(source, [row, row])
            completed = subprocess.run(
                [sys.executable, str(EXPORTER), str(source),
                 "--output", str(Path(folder) / "hst")],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("duplicate or overlapping M1 time", completed.stderr)


if __name__ == "__main__":
    unittest.main()
