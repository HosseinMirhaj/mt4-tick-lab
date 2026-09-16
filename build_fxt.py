#!/usr/bin/env python3
"""Build an MT4 FXT v405 pilot from normalized real ticks and a Build-1470 header template."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import struct
import sys
from pathlib import Path

HEADER_SIZE = 728
RECORD = struct.Struct("<qddddqii")
assert RECORD.size == 56
REQUIRED = {
    "broker_company", "broker_server", "terminal_id", "symbol", "session_id",
    "sequence", "broker_time", "received_utc", "monotonic_us", "bid",
    "ask", "spread_points",
}


def discover(targets: list[str]) -> list[Path]:
    found: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_file() and path.name.startswith("ticks_") and path.suffix.lower() == ".csv":
            found.add(path.resolve())
        elif path.is_dir():
            found.update(item.resolve() for item in path.rglob("ticks_*.csv"))
    return sorted(found)


def read_key_values(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or rows[0] != ["key", "value"]:
        raise ValueError(f"invalid symbol specification: {path}")
    values: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) != 2 or not row[0]:
            raise ValueError(f"invalid symbol specification row: {row!r}")
        values[row[0]] = row[1]
    return values


def ascii_field(value: str, length: int, label: str) -> bytes:
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be ASCII") from exc
    if len(encoded) > length:
        raise ValueError(f"{label} exceeds {length} bytes")
    return encoded.ljust(length, b"\0")


def broker_epoch(value: str) -> int:
    parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    return int(parsed.replace(tzinfo=dt.timezone.utc).timestamp())


def load_ticks(paths: list[Path], spec: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    expected = (spec["broker_company"], spec["broker_server"],
                spec["terminal_id"], spec["symbol"])
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(REQUIRED - set(reader.fieldnames or []))
            if missing:
                raise ValueError(f"missing {', '.join(missing)}: {path}")
            for line, row in enumerate(reader, start=2):
                try:
                    identity = tuple(row[key] for key in
                                     ("broker_company", "broker_server", "terminal_id", "symbol"))
                    if identity != expected:
                        raise ValueError("tick identity does not match symbol specification")
                    bid, ask = float(row["bid"]), float(row["ask"])
                    spread = float(row["spread_points"])
                    sequence, monotonic = int(row["sequence"]), int(row["monotonic_us"])
                    timestamp = broker_epoch(row["broker_time"])
                    if not all(math.isfinite(value) for value in (bid, ask, spread)):
                        raise ValueError("non-finite quote")
                    if bid <= 0 or ask < bid or spread < 0:
                        raise ValueError("invalid quote")
                    rows.append({
                        "timestamp": timestamp, "bid": bid, "spread": spread,
                        "order": (row["received_utc"], monotonic,
                                  row["session_id"], sequence),
                    })
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"{path}:{line}: {exc}") from exc
    rows.sort(key=lambda item: item["order"])
    if not rows:
        raise ValueError("no tick rows")
    return rows


def patch_header(template: bytes, spec: dict[str, str], ticks: list[dict],
                 bars: int, fixed_spread: int, tester_symbol: str) -> bytearray:
    if len(template) != HEADER_SIZE:
        raise ValueError("FXT reference header must be exactly 728 bytes")
    header = bytearray(template)
    if struct.unpack_from("<i", header, 0)[0] != 405:
        raise ValueError("FXT reference must be version 405")
    reference_symbol = bytes(header[196:208]).split(b"\0", 1)[0].decode("ascii", "strict")
    if reference_symbol != spec["symbol"]:
        raise ValueError("reference FXT symbol does not match specification")

    def integer(key: str) -> int:
        value = float(spec[key])
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f"{key} must be an integer")
        return int(value)

    def number(key: str) -> float:
        value = float(spec[key])
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        return value

    header[68:196] = ascii_field(spec["broker_server"], 128, "broker_server")
    header[196:208] = ascii_field(tester_symbol, 12, "tester_symbol")
    struct.pack_into("<iiiiii", header, 208, 1, 0, bars,
                     ticks[0]["timestamp"], ticks[-1]["timestamp"], len(ticks))
    struct.pack_into("<d", header, 232, 99.9)
    header[240:252] = ascii_field(spec["account_currency"], 12, "account_currency")
    struct.pack_into("<ii", header, 252, fixed_spread, integer("digits"))
    struct.pack_into("<d", header, 264, number("point"))
    struct.pack_into("<iiii", header, 272,
                     round(number("min_lot") * 100),
                     round(number("max_lot") * 100),
                     round(number("lot_step") * 100), integer("stop_level"))
    struct.pack_into("<d", header, 296, number("contract_size"))
    struct.pack_into("<dd", header, 304, number("tick_value"), number("tick_size"))
    struct.pack_into("<iii", header, 320, integer("profit_calc_mode"),
                     int(number("swap_long") != 0 or number("swap_short") != 0),
                     integer("swap_type"))
    struct.pack_into("<dd", header, 336, number("swap_long"), number("swap_short"))
    struct.pack_into("<i", header, 356, integer("account_leverage"))
    struct.pack_into("<i", header, 364, integer("margin_calc_mode"))
    struct.pack_into("<i", header, 368, integer("account_stopout_level"))
    struct.pack_into("<ddd", header, 376, number("margin_initial"),
                     number("margin_maintenance"), number("margin_hedged"))
    header[408:420] = ascii_field(spec["account_currency"], 12, "margin_currency")
    return header


def write_fxt(destination: Path, header: bytes, ticks: list[dict]) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    bar_minute = -1
    open_price = high = low = 0.0
    volume = 0
    bars = 0
    with destination.open("wb") as stream:
        stream.write(header)
        for tick in ticks:
            minute = tick["timestamp"] - tick["timestamp"] % 60
            bid = tick["bid"]
            if minute != bar_minute:
                bar_minute, open_price, high, low, volume = minute, bid, bid, bid, 1
                bars += 1
            else:
                high, low, volume = max(high, bid), min(low, bid), volume + 1
            stream.write(RECORD.pack(bar_minute, open_price, high, low, bid,
                                     volume, tick["timestamp"], 4))
    expected = HEADER_SIZE + RECORD.size * len(ticks)
    if destination.stat().st_size != expected:
        raise OSError("unexpected FXT file size")
    return bars, expected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an offline MT4 FXT v405 pilot")
    parser.add_argument("targets", nargs="+", help="normalized tick datasets")
    parser.add_argument("--reference", required=True, help="same-symbol Build-1470 FXT template")
    parser.add_argument("--spec", required=True, help="symbol specification CSV")
    parser.add_argument("--output", required=True, help="offline output directory")
    parser.add_argument("--symbol", default=None,
                        help="tester symbol name (e.g. GOLD); defaults to source symbol")
    args = parser.parse_args()
    try:
        paths = discover(args.targets)
        if not paths:
            print("No ticks_*.csv files found.", file=sys.stderr)
            return 2
        spec = read_key_values(Path(args.spec))
        ticks = load_ticks(paths, spec)
        spreads = [tick["spread"] for tick in ticks]
        fixed_spread = round(statistics.median(spreads))
        unique_bars = len({tick["timestamp"] - tick["timestamp"] % 60 for tick in ticks})
        with Path(args.reference).open("rb") as stream:
            template = stream.read(HEADER_SIZE)
        tester_symbol = args.symbol or spec["symbol"]
        header = patch_header(template, spec, ticks, unique_bars, fixed_spread,
                              tester_symbol)
        output = Path(args.output).resolve()
        destination = output / f"{tester_symbol}1_0.fxt"
        bars, size = write_fxt(destination, header, ticks)
        manifest = {
            "format": "FXT", "version": 405, "model": 0,
            "source_symbol": spec["symbol"], "tester_symbol": tester_symbol,
            "period_minutes": 1,
            "terminal_build": integer_or_text(spec.get("terminal_build", "")),
            "ticks": len(ticks), "bars": bars, "bytes": size,
            "fixed_spread_points": fixed_spread,
            "spread_limitation": "Native FXT v405 stores one fixed spread; source per-tick spread remains in CSV.",
        }
        (output / f"{tester_symbol}1_0.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"PASS | ticks={len(ticks)} | bars={bars} | FXT_bytes={size} | fixed_spread={fixed_spread}")
        return 0
    except (OSError, ValueError, csv.Error, struct.error) as exc:
        print(f"FAIL | {exc}", file=sys.stderr)
        return 1


def integer_or_text(value: str):
    try:
        return int(value)
    except ValueError:
        return value


if __name__ == "__main__":
    raise SystemExit(main())
