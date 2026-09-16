import csv
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "build_fxt.py"
HEADER_SIZE = 728
RECORD = struct.Struct("<qddddqii")


class FxtBuilderTests(unittest.TestCase):
    def test_builds_v405_tick_records_from_real_quotes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference = root / "reference.fxt"
            header = bytearray(HEADER_SIZE)
            struct.pack_into("<i", header, 0, 405)
            header[196:208] = b"XAUUSD".ljust(12, b"\0")
            reference.write_bytes(header)

            spec = root / "spec.csv"
            values = {
                "broker_company": "Alpari", "broker_server": "Alpari-Pro.ECN-Demo",
                "terminal_id": "TERM001", "terminal_build": "1470",
                "symbol": "XAUUSD", "account_currency": "USD",
                "account_leverage": "400", "account_stopout_mode": "0",
                "account_stopout_level": "50", "digits": "2", "point": "0.01",
                "stop_level": "0", "contract_size": "100", "tick_value": "1",
                "tick_size": "0.01", "swap_long": "-63.05", "swap_short": "42.04",
                "min_lot": "0.01", "lot_step": "0.01", "max_lot": "100",
                "swap_type": "0", "profit_calc_mode": "1", "margin_calc_mode": "4",
                "margin_initial": "0", "margin_maintenance": "0", "margin_hedged": "0",
            }
            with spec.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["key", "value"])
                writer.writerows(values.items())

            ticks = root / "ticks_20260914.csv"
            fields = [
                "broker_company", "broker_server", "terminal_id", "symbol",
                "session_id", "sequence", "broker_time", "received_utc",
                "monotonic_us", "bid", "ask", "spread_points",
            ]
            common = {
                "broker_company": "Alpari", "broker_server": "Alpari-Pro.ECN-Demo",
                "terminal_id": "TERM001", "symbol": "XAUUSD", "session_id": "S1",
            }
            rows = [
                {**common, "sequence": "2", "broker_time": "2026-09-14T18:34:42",
                 "received_utc": "2026-09-14T15:34:42Z", "monotonic_us": "200",
                 "bid": "4291.93", "ask": "4292.03", "spread_points": "10"},
                {**common, "sequence": "1", "broker_time": "2026-09-14T18:34:41",
                 "received_utc": "2026-09-14T15:34:41Z", "monotonic_us": "100",
                 "bid": "4291.78", "ask": "4291.87", "spread_points": "9"},
                {**common, "sequence": "3", "broker_time": "2026-09-14T18:35:01",
                 "received_utc": "2026-09-14T15:35:01Z", "monotonic_us": "300",
                 "bid": "4291.80", "ask": "4291.91", "spread_points": "11"},
            ]
            with ticks.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            output = root / "output"
            completed = subprocess.run(
                [sys.executable, str(BUILDER), str(ticks), "--reference", str(reference),
                 "--spec", str(spec), "--output", str(output), "--symbol", "GOLD"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = (output / "GOLD1_0.fxt").read_bytes()
            self.assertEqual(len(payload), HEADER_SIZE + 3 * RECORD.size)
            self.assertEqual(struct.unpack_from("<i", payload, 0)[0], 405)
            self.assertEqual(payload[68:196].split(b"\0", 1)[0], b"Alpari-Pro.ECN-Demo")
            self.assertEqual(payload[196:208].split(b"\0", 1)[0], b"GOLD")
            self.assertEqual(struct.unpack_from("<iiiiii", payload, 208)[0:3], (1, 0, 2))
            self.assertEqual(struct.unpack_from("<ii", payload, 252), (10, 2))
            self.assertEqual(struct.unpack_from("<d", payload, 264)[0], 0.01)

            first = RECORD.unpack_from(payload, HEADER_SIZE)
            second = RECORD.unpack_from(payload, HEADER_SIZE + RECORD.size)
            third = RECORD.unpack_from(payload, HEADER_SIZE + 2 * RECORD.size)
            self.assertEqual(first[1:6], (4291.78, 4291.78, 4291.78, 4291.78, 1))
            self.assertEqual(second[1:6], (4291.78, 4291.93, 4291.78, 4291.93, 2))
            self.assertEqual(third[1:6], (4291.80, 4291.80, 4291.80, 4291.80, 1))
            self.assertEqual(first[-1], 4)

            manifest = json.loads((output / "GOLD1_0.manifest.json").read_text())
            self.assertEqual(manifest["source_symbol"], "XAUUSD")
            self.assertEqual(manifest["tester_symbol"], "GOLD")
            self.assertEqual(manifest["ticks"], 3)
            self.assertEqual(manifest["bars"], 2)
            self.assertEqual(manifest["fixed_spread_points"], 10)


if __name__ == "__main__":
    unittest.main()
