#!/usr/bin/env python3
"""Validate MT4 Tick Lab CSV files without third-party dependencies."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REQUIRED = {
    "schema_version", "session_id", "sequence", "broker_time",
    "received_utc", "monotonic_us", "bid", "ask", "last",
    "volume", "spread_points",
}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.rstrip("Z"))


def find_files(targets: list[str]) -> list[Path]:
    found: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_file():
            found.add(path.resolve())
        elif path.is_dir():
            found.update(p.resolve() for p in path.rglob("ticks_*.csv"))
    return sorted(found)


def validate_file(path: Path) -> dict:
    result = {
        "file": str(path), "rows": 0, "sessions": 0,
        "sequence_gaps": 0, "sequence_regressions": 0,
        "received_time_regressions": 0, "broker_time_regressions": 0,
        "invalid_rows": 0, "invalid_quotes": 0,
        "min_spread_points": None, "max_spread_points": None,
        "issues": [],
    }
    last_sequence: dict[str, int] = {}
    last_received: dict[str, datetime] = {}
    last_broker: dict[str, datetime] = {}
    spread_min = math.inf
    spread_max = -math.inf
    session_rows: defaultdict[str, int] = defaultdict(int)

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(REQUIRED - set(reader.fieldnames or []))
            if missing:
                result["issues"].append("missing columns: " + ", ".join(missing))
                return result

            for line_number, row in enumerate(reader, start=2):
                result["rows"] += 1
                try:
                    if row["schema_version"] != "1":
                        raise ValueError("unsupported schema_version")
                    session = row["session_id"].strip()
                    if not session:
                        raise ValueError("empty session_id")
                    sequence = int(row["sequence"])
                    monotonic_us = int(row["monotonic_us"])
                    bid = float(row["bid"])
                    ask = float(row["ask"])
                    float(row["last"])
                    int(row["volume"])
                    spread = float(row["spread_points"])
                    broker_time = parse_time(row["broker_time"])
                    received_time = parse_time(row["received_utc"])
                    if sequence < 1 or monotonic_us < 0:
                        raise ValueError("negative/zero sequence or clock")
                except (KeyError, TypeError, ValueError) as exc:
                    result["invalid_rows"] += 1
                    if len(result["issues"]) < 10:
                        result["issues"].append(f"line {line_number}: {exc}")
                    continue

                session_rows[session] += 1
                previous_sequence = last_sequence.get(session)
                if previous_sequence is not None:
                    if sequence <= previous_sequence:
                        result["sequence_regressions"] += 1
                    elif sequence > previous_sequence + 1:
                        result["sequence_gaps"] += sequence - previous_sequence - 1
                last_sequence[session] = sequence

                previous_received = last_received.get(session)
                if previous_received is not None and received_time < previous_received:
                    result["received_time_regressions"] += 1
                last_received[session] = received_time

                previous_broker = last_broker.get(session)
                if previous_broker is not None and broker_time < previous_broker:
                    result["broker_time_regressions"] += 1
                last_broker[session] = broker_time

                if not all(map(math.isfinite, (bid, ask, spread))) or bid <= 0 or ask <= 0 or ask < bid:
                    result["invalid_quotes"] += 1
                spread_min = min(spread_min, spread)
                spread_max = max(spread_max, spread)
    except (OSError, csv.Error) as exc:
        result["issues"].append(str(exc))

    result["sessions"] = len(session_rows)
    if result["rows"] == 0:
        result["issues"].append("file contains no tick rows")
    if spread_min != math.inf:
        result["min_spread_points"] = spread_min
        result["max_spread_points"] = spread_max

    hard_failures = (
        result["sequence_gaps"] + result["sequence_regressions"] +
        result["received_time_regressions"] + result["invalid_rows"] +
        result["invalid_quotes"]
    )
    result["status"] = "FAIL" if result["issues"] or hard_failures else "PASS"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MT4 Tick Lab CSV data")
    parser.add_argument("targets", nargs="+", help="tick CSV file(s) or directories")
    parser.add_argument("--json", dest="json_path", help="write full report to JSON")
    args = parser.parse_args()

    files = find_files(args.targets)
    if not files:
        print("No ticks_*.csv files found.", file=sys.stderr)
        return 2

    reports = [validate_file(path) for path in files]
    summary = {
        "status": "PASS" if all(r["status"] == "PASS" for r in reports) else "FAIL",
        "files": len(reports),
        "rows": sum(r["rows"] for r in reports),
        "failed_files": sum(r["status"] == "FAIL" for r in reports),
        "results": reports,
    }

    for report in reports:
        print(f"{report['status']:4}  rows={report['rows']:8}  gaps={report['sequence_gaps']:6}  {report['file']}")
    print(f"Summary: {summary['status']} | files={summary['files']} | rows={summary['rows']} | failed={summary['failed_files']}")

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
