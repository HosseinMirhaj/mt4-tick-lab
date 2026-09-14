#!/usr/bin/env python3
"""Aggregate normalized MT4 Tick Lab datasets into broker-time M1 bars."""

from __future__ import annotations

import argparse
import csv
import math
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

REQUIRED = {
    "broker_company", "broker_server", "terminal_id", "symbol",
    "session_id", "sequence", "broker_time", "received_utc",
    "monotonic_us", "bid", "ask", "volume", "spread_points",
}
OUTPUT_FIELDS = [
    "broker_company", "broker_server", "terminal_id", "symbol", "timeframe",
    "broker_time", "open", "high", "low", "close", "tick_volume",
    "real_volume", "spread_open", "spread_min", "spread_max", "spread_avg",
    "spread_close", "first_received_utc", "last_received_utc",
]
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}):\d{2}$")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())[:80]
    return cleaned or "unknown"


def discover(targets: list[str]) -> list[Path]:
    files: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_file() and path.name.startswith("ticks_") and path.suffix.lower() == ".csv":
            files.add(path.resolve())
        elif path.is_dir():
            files.update(item.resolve() for item in path.rglob("ticks_*.csv"))
    return sorted(files)


def decimals(value: str) -> int:
    text = value.strip().lower()
    if "e" in text:
        return 10
    return len(text.partition(".")[2])


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE bars (
            broker_company TEXT, broker_server TEXT, terminal_id TEXT, symbol TEXT,
            broker_minute TEXT, open REAL, high REAL, low REAL, close REAL,
            tick_volume INTEGER, real_volume INTEGER, spread_open REAL,
            spread_min REAL, spread_max REAL, spread_sum REAL, spread_close REAL,
            first_received_utc TEXT, last_received_utc TEXT,
            first_order TEXT, last_order TEXT, digits INTEGER,
            PRIMARY KEY (broker_company, broker_server, terminal_id, symbol, broker_minute)
        )
    """)


def ingest(connection: sqlite3.Connection, paths: list[Path]) -> tuple[int, int]:
    tick_count = 0
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(REQUIRED - set(reader.fieldnames or []))
            if missing:
                raise ValueError(f"missing {', '.join(missing)}: {path}")
            for line, row in enumerate(reader, start=2):
                try:
                    match = TIME_RE.fullmatch(row["broker_time"])
                    if not match:
                        raise ValueError("invalid broker_time")
                    minute = match.group(1) + ":00"
                    bid = float(row["bid"])
                    ask = float(row["ask"])
                    spread = float(row["spread_points"])
                    volume = int(row["volume"])
                    monotonic = int(row["monotonic_us"])
                    sequence = int(row["sequence"])
                    if not all(math.isfinite(value) for value in (bid, ask, spread)):
                        raise ValueError("non-finite quote")
                    if bid <= 0 or ask < bid or spread < 0 or volume < 0:
                        raise ValueError("invalid quote/volume")
                    order = f"{row['received_utc']}|{monotonic:020d}|{row['session_id']}|{sequence:020d}"
                    identity = (
                        row["broker_company"], row["broker_server"],
                        row["terminal_id"], row["symbol"], minute,
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{path}:{line}: {exc}") from exc

                current = connection.execute(
                    "SELECT open,high,low,close,tick_volume,real_volume,spread_open,"
                    "spread_min,spread_max,spread_sum,spread_close,first_received_utc,"
                    "last_received_utc,first_order,last_order,digits FROM bars "
                    "WHERE broker_company=? AND broker_server=? AND terminal_id=? "
                    "AND symbol=? AND broker_minute=?", identity,
                ).fetchone()
                price_digits = max(decimals(row["bid"]), decimals(row["ask"]))
                if current is None:
                    connection.execute(
                        "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        identity + (
                            bid, bid, bid, bid, 1, volume, spread, spread, spread,
                            spread, spread, row["received_utc"], row["received_utc"],
                            order, order, price_digits,
                        ),
                    )
                else:
                    (open_price, high, low, close, ticks, real_volume, spread_open,
                     spread_min, spread_max, spread_sum, spread_close, first_utc,
                     last_utc, first_order, last_order, stored_digits) = current
                    if order < first_order:
                        open_price, spread_open, first_utc, first_order = (
                            bid, spread, row["received_utc"], order
                        )
                    if order > last_order:
                        close, spread_close, last_utc, last_order = (
                            bid, spread, row["received_utc"], order
                        )
                    connection.execute(
                        "UPDATE bars SET open=?,high=?,low=?,close=?,tick_volume=?,"
                        "real_volume=?,spread_open=?,spread_min=?,spread_max=?,"
                        "spread_sum=?,spread_close=?,first_received_utc=?,"
                        "last_received_utc=?,first_order=?,last_order=?,digits=? "
                        "WHERE broker_company=? AND broker_server=? AND terminal_id=? "
                        "AND symbol=? AND broker_minute=?",
                        (
                            open_price, max(high, bid), min(low, bid), close, ticks + 1,
                            real_volume + volume, spread_open, min(spread_min, spread),
                            max(spread_max, spread), spread_sum + spread, spread_close,
                            first_utc, last_utc, first_order, last_order,
                            max(stored_digits, price_digits),
                        ) + identity,
                    )
                tick_count += 1
    connection.commit()
    bars = connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    return tick_count, bars


def export(connection: sqlite3.Connection, output: Path) -> int:
    groups = connection.execute("""
        SELECT broker_company,broker_server,terminal_id,symbol,
               substr(broker_minute,1,10),COUNT(*)
        FROM bars GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5
    """).fetchall()
    for company, server, terminal, symbol, day, _ in groups:
        folder = output / safe_name(company) / safe_name(server) / safe_name(terminal) / safe_name(symbol)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"bars_M1_{day.replace('-', '')}.csv"
        rows = connection.execute("""
            SELECT broker_company,broker_server,terminal_id,symbol,broker_minute,
                   open,high,low,close,tick_volume,real_volume,spread_open,
                   spread_min,spread_max,spread_sum,spread_close,
                   first_received_utc,last_received_utc,digits
            FROM bars WHERE broker_company=? AND broker_server=? AND terminal_id=?
              AND symbol=? AND substr(broker_minute,1,10)=? ORDER BY broker_minute
        """, (company, server, terminal, symbol, day))
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                (*identity, minute, open_price, high, low, close, tick_volume,
                 real_volume, spread_open, spread_min, spread_max, spread_sum,
                 spread_close, first_utc, last_utc, digits) = row
                fmt = f".{{}}f".format(digits)
                writer.writerow(dict(zip(OUTPUT_FIELDS, [
                    *identity, "M1", minute,
                    format(open_price, fmt), format(high, fmt),
                    format(low, fmt), format(close, fmt), tick_volume, real_volume,
                    f"{spread_open:.2f}", f"{spread_min:.2f}",
                    f"{spread_max:.2f}", f"{spread_sum / tick_volume:.2f}",
                    f"{spread_close:.2f}", first_utc, last_utc,
                ])))
    return len(groups)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate MT4 ticks into broker-time M1 bars")
    parser.add_argument("targets", nargs="+", help="normalized tick datasets")
    parser.add_argument("--output", required=True, help="output bars directory")
    args = parser.parse_args()
    paths = discover(args.targets)
    if not paths:
        print("No ticks_*.csv files found.", file=sys.stderr)
        return 2

    temp = tempfile.NamedTemporaryFile(prefix="mt4_bars_", suffix=".sqlite", delete=False)
    temp.close()
    database = Path(temp.name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        initialize(connection)
        ticks, bars = ingest(connection, paths)
        files = export(connection, Path(args.output).resolve())
        print(f"PASS | ticks={ticks} | M1_bars={bars} | files={files}")
        return 0
    except (OSError, ValueError, csv.Error, sqlite3.Error) as exc:
        print(f"FAIL | {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
        database.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
