#!/usr/bin/env python3
"""Export broker-time M1 CSV bars to MT4 HST v401 files offline."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

HEADER = struct.Struct("<i64s12siiii13i")
RECORD = struct.Struct("<qddddqiq")
assert HEADER.size == 148
assert RECORD.size == 60

REQUIRED = {
    "broker_company", "broker_server", "terminal_id", "symbol", "timeframe",
    "broker_time", "open", "high", "low", "close", "tick_volume",
    "real_volume", "spread_close",
}


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())[:80]
    return cleaned or "unknown"


def hst_filename(symbol: str) -> str:
    if (not symbol or symbol.endswith((" ", ".")) or
            any(ord(char) < 32 or char in '<>:"/\\|?*' for char in symbol)):
        raise ValueError(f"symbol cannot be used as an MT4 history filename: {symbol!r}")
    return f"{symbol}1.hst"


def discover(targets: list[str]) -> list[Path]:
    files: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_file() and path.name.startswith("bars_M1_") and path.suffix.lower() == ".csv":
            files.add(path.resolve())
        elif path.is_dir():
            files.update(item.resolve() for item in path.rglob("bars_M1_*.csv"))
    return sorted(files)


def epoch_seconds(value: str) -> int:
    parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    # Preserve the broker wall-clock value; never apply the PC's timezone.
    return int(parsed.replace(tzinfo=dt.timezone.utc).timestamp())


def decimal_places(value: str) -> int:
    text = value.strip().lower()
    if "e" in text:
        return 10
    return len(text.partition(".")[2])


def finite_number(row: dict[str, str], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError(f"non-finite {field}")
    return value


def nonnegative_integer(row: dict[str, str], field: str) -> int:
    numeric = finite_number(row, field)
    value = round(numeric)
    if numeric < 0 or not math.isclose(numeric, value, abs_tol=1e-9):
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def read_bar_file(path: Path) -> tuple[tuple[str, str, str, str], list[tuple], int]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = sorted(REQUIRED - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"missing {', '.join(missing)}: {path}")
        identity: tuple[str, str, str, str] | None = None
        records: list[tuple] = []
        digits = 0
        for line, row in enumerate(reader, start=2):
            try:
                current_identity = tuple(
                    row[key] for key in
                    ("broker_company", "broker_server", "terminal_id", "symbol")
                )
                if identity is None:
                    identity = current_identity
                elif current_identity != identity:
                    raise ValueError("mixed broker/terminal/symbol in one file")
                if row["timeframe"] != "M1":
                    raise ValueError("timeframe must be M1")
                timestamp = epoch_seconds(row["broker_time"])
                prices = tuple(finite_number(row, field) for field in
                               ("open", "high", "low", "close"))
                open_price, high, low, close = prices
                if min(prices) <= 0 or high < max(prices) or low > min(prices):
                    raise ValueError("inconsistent OHLC")
                tick_volume = nonnegative_integer(row, "tick_volume")
                real_volume = nonnegative_integer(row, "real_volume")
                spread = nonnegative_integer(row, "spread_close")
                digits = max(digits, *(decimal_places(row[field]) for field in
                                       ("open", "high", "low", "close")))
                # MT4 HST v401 follows MqlRates record order:
                # time, open, high, low, close, tick volume, spread, real volume.
                records.append((timestamp, open_price, high, low, close,
                                tick_volume, spread, real_volume))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{path}:{line}: {exc}") from exc
    if identity is None:
        raise ValueError(f"empty bar file: {path}")
    if digits > 10:
        raise ValueError(f"unsupported price precision ({digits} digits): {path}")
    return identity, records, digits


def export_group(identity: tuple[str, str, str, str], records: list[tuple],
                 digits: int, output: Path) -> tuple[Path, int]:
    company, server, terminal, symbol = identity
    try:
        symbol_bytes = symbol.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"HST symbol must be ASCII: {symbol}") from exc
    if len(symbol_bytes) > 12:
        raise ValueError(f"symbol exceeds HST's 12-byte field: {symbol}")

    records.sort(key=lambda item: item[0])
    for previous, current in zip(records, records[1:]):
        if current[0] <= previous[0]:
            raise ValueError(f"duplicate or overlapping M1 time for {server}/{symbol}")

    folder = (output / safe_name(company) / safe_name(server) /
              safe_name(terminal) / safe_name(symbol))
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / hst_filename(symbol)
    copyright_bytes = b"MT4 Tick Lab - offline HST v401"
    header = HEADER.pack(
        401, copyright_bytes.ljust(64, b"\0"), symbol_bytes.ljust(12, b"\0"),
        1, digits, 0, 0, *([0] * 13),
    )
    with destination.open("wb") as stream:
        stream.write(header)
        for record in records:
            stream.write(RECORD.pack(*record))
    expected = HEADER.size + RECORD.size * len(records)
    if destination.stat().st_size != expected:
        raise OSError(f"unexpected HST size: {destination}")
    return destination, len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export M1 bars to offline MT4 HST v401")
    parser.add_argument("targets", nargs="+", help="bars_M1 CSV files or directories")
    parser.add_argument("--output", required=True, help="offline HST output directory")
    args = parser.parse_args()
    paths = discover(args.targets)
    if not paths:
        print("No bars_M1_*.csv files found.", file=sys.stderr)
        return 2

    try:
        grouped: dict[tuple[str, str, str, str], list[tuple]] = defaultdict(list)
        precisions: dict[tuple[str, str, str, str], int] = {}
        for path in paths:
            identity, records, digits = read_bar_file(path)
            grouped[identity].extend(records)
            precisions[identity] = max(precisions.get(identity, 0), digits)
        output = Path(args.output).resolve()
        total_bars = 0
        for identity, records in grouped.items():
            _, count = export_group(identity, records, precisions[identity], output)
            total_bars += count
        print(f"PASS | bars={total_bars} | HST_files={len(grouped)} | version=401")
        return 0
    except (OSError, ValueError, csv.Error, struct.error) as exc:
        print(f"FAIL | {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
