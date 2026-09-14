#!/usr/bin/env python3
"""Build broker-isolated, daily tick datasets from MT4 Tick Lab sessions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

TICK_REQUIRED = {
    "schema_version", "session_id", "sequence", "broker_time",
    "received_utc", "monotonic_us", "bid", "ask", "last",
    "volume", "spread_points",
}
OUTPUT_FIELDS = [
    "broker_company", "broker_server", "terminal_id", "symbol",
    "schema_version", "session_id", "sequence", "broker_time",
    "received_utc", "monotonic_us", "bid", "ask", "last", "volume",
    "spread_points", "raw_tick_bid", "raw_tick_ask", "market_bid",
    "market_ask", "quote_source",
]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())[:80]
    return cleaned or "unknown"


def discover(targets: list[str], pattern: str) -> list[Path]:
    files: set[Path] = set()
    for raw in targets:
        path = Path(raw)
        if path.is_file() and path.match(pattern):
            files.add(path.resolve())
        elif path.is_dir():
            files.update(item.resolve() for item in path.rglob(pattern))
    return sorted(files)


def read_metadata(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows or rows[0] != ["key", "value"]:
        raise ValueError(f"invalid metadata header: {path}")
    data = {row[0]: row[1] for row in rows[1:] if len(row) >= 2}
    required = {"session_id", "broker_company", "broker_server", "terminal_id", "symbol"}
    missing = sorted(required - data.keys())
    if missing:
        raise ValueError(f"metadata missing {', '.join(missing)}: {path}")
    return data


def metadata_index(paths: list[Path]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for path in paths:
        data = read_metadata(path)
        session = data["session_id"]
        identity = tuple(data.get(key, "") for key in (
            "broker_company", "broker_server", "terminal_id", "symbol"
        ))
        if session in index:
            previous = tuple(index[session].get(key, "") for key in (
                "broker_company", "broker_server", "terminal_id", "symbol"
            ))
            if identity != previous:
                raise ValueError(f"conflicting metadata for session {session}")
        else:
            index[session] = data
    return index


def canonical_row(row: dict[str, str], meta: dict[str, str]) -> dict[str, str]:
    schema = row["schema_version"]
    if schema not in {"1", "2"}:
        raise ValueError(f"unsupported schema_version {schema}")
    received = row["received_utc"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", received):
        raise ValueError(f"invalid received_utc {received}")
    int(row["sequence"])
    int(row["monotonic_us"])
    for key in ("bid", "ask", "last", "spread_points"):
        float(row[key])
    int(row["volume"])

    result = {
        "broker_company": meta["broker_company"],
        "broker_server": meta["broker_server"],
        "terminal_id": meta["terminal_id"],
        "symbol": meta["symbol"],
    }
    result.update({key: row[key] for key in TICK_REQUIRED})
    if schema == "2":
        for key in ("raw_tick_bid", "raw_tick_ask", "market_bid", "market_ask", "quote_source"):
            if key not in row:
                raise ValueError(f"schema v2 missing {key}")
            result[key] = row[key]
    else:
        result.update({
            "raw_tick_bid": row["bid"], "raw_tick_ask": row["ask"],
            "market_bid": "", "market_ask": "", "quote_source": "mql_tick_v1",
        })
    return result


def payload_hash(row: dict[str, str]) -> str:
    payload = "\x1f".join(row[field] for field in OUTPUT_FIELDS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def initialize_db(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE ticks (
            broker_company TEXT, broker_server TEXT, terminal_id TEXT, symbol TEXT,
            schema_version TEXT, session_id TEXT, sequence INTEGER, broker_time TEXT,
            received_utc TEXT, monotonic_us INTEGER, bid TEXT, ask TEXT, last TEXT,
            volume TEXT, spread_points TEXT, raw_tick_bid TEXT, raw_tick_ask TEXT,
            market_bid TEXT, market_ask TEXT, quote_source TEXT, payload_hash TEXT,
            PRIMARY KEY (broker_company, broker_server, terminal_id, symbol, session_id, sequence)
        )
    """)


def ingest(connection: sqlite3.Connection, tick_paths: list[Path], metadata: dict[str, dict[str, str]]) -> Counter:
    stats = Counter()
    placeholders = ",".join("?" for _ in range(len(OUTPUT_FIELDS) + 1))
    insert_sql = f"INSERT INTO ticks VALUES ({placeholders})"
    for path in tick_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            missing = sorted(TICK_REQUIRED - set(reader.fieldnames or []))
            if missing:
                raise ValueError(f"tick file missing {', '.join(missing)}: {path}")
            for line, source_row in enumerate(reader, start=2):
                session = source_row.get("session_id", "")
                if session not in metadata:
                    raise ValueError(f"no metadata for session {session}: {path}:{line}")
                try:
                    row = canonical_row(source_row, metadata[session])
                except ValueError as exc:
                    raise ValueError(f"{path}:{line}: {exc}") from exc
                digest = payload_hash(row)
                values = [row[field] for field in OUTPUT_FIELDS] + [digest]
                try:
                    connection.execute(insert_sql, values)
                    stats["inserted"] += 1
                except sqlite3.IntegrityError:
                    key = [row[field] for field in (
                        "broker_company", "broker_server", "terminal_id", "symbol", "session_id"
                    )] + [int(row["sequence"])]
                    existing = connection.execute(
                        "SELECT payload_hash FROM ticks WHERE broker_company=? AND broker_server=? "
                        "AND terminal_id=? AND symbol=? AND session_id=? AND sequence=?", key
                    ).fetchone()
                    if not existing or existing[0] != digest:
                        raise ValueError(f"conflicting duplicate: {path}:{line}")
                    stats["exact_duplicates"] += 1
        stats["files"] += 1
    connection.commit()
    return stats


def reject_overlapping_sessions(connection: sqlite3.Connection) -> None:
    rows = connection.execute("""
        SELECT broker_company, broker_server, terminal_id, symbol, session_id,
               MIN(received_utc), MAX(received_utc)
        FROM ticks GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,6
    """).fetchall()
    previous: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for company, server, terminal, symbol, session, start, end in rows:
        group = (company, server, terminal, symbol)
        if group in previous:
            previous_end, previous_session = previous[group]
            if start < previous_end:
                raise ValueError(
                    f"overlapping sessions {previous_session} and {session} "
                    f"for {company}/{server}/{terminal}/{symbol}"
                )
            if end > previous_end:
                previous[group] = (end, session)
        else:
            previous[group] = (end, session)


def export(connection: sqlite3.Connection, output: Path) -> list[dict]:
    groups = connection.execute("""
        SELECT broker_company, broker_server, terminal_id, symbol,
               substr(received_utc, 1, 10) AS utc_day, COUNT(*)
        FROM ticks GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5
    """).fetchall()
    manifest: list[dict] = []
    query = """
        SELECT broker_company, broker_server, terminal_id, symbol, schema_version,
               session_id, sequence, broker_time, received_utc, monotonic_us, bid, ask,
               last, volume, spread_points, raw_tick_bid, raw_tick_ask, market_bid,
               market_ask, quote_source
        FROM ticks WHERE broker_company=? AND broker_server=? AND terminal_id=?
          AND symbol=? AND substr(received_utc, 1, 10)=?
        ORDER BY received_utc, monotonic_us, session_id, sequence
    """
    for company, server, terminal, symbol, day, count in groups:
        folder = output / safe_name(company) / safe_name(server) / safe_name(terminal) / safe_name(symbol)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"ticks_{day.replace('-', '')}.csv"
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(OUTPUT_FIELDS)
            writer.writerows(connection.execute(query, (company, server, terminal, symbol, day)))
        manifest.append({
            "broker_company": company, "broker_server": server,
            "terminal_id": terminal, "symbol": symbol, "utc_day": day,
            "rows": count, "file": str(destination.relative_to(output)),
        })
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build isolated daily MT4 tick datasets")
    parser.add_argument("targets", nargs="+", help="raw session files/directories")
    parser.add_argument("--output", required=True, help="output dataset directory")
    args = parser.parse_args()
    output = Path(args.output).resolve()

    metadata_paths = discover(args.targets, "metadata_*.csv")
    tick_paths = discover(args.targets, "ticks_*.csv")
    if not metadata_paths or not tick_paths:
        print("Both metadata_*.csv and ticks_*.csv files are required.", file=sys.stderr)
        return 2

    temporary = tempfile.NamedTemporaryFile(prefix="mt4_tick_lab_", suffix=".sqlite", delete=False)
    temporary.close()
    database = Path(temporary.name)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database)
        initialize_db(connection)
        stats = ingest(connection, tick_paths, metadata_index(metadata_paths))
        reject_overlapping_sessions(connection)
        manifest = export(connection, output)
        summary = {"status": "PASS", **stats, "datasets": manifest}
        (output / "manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"PASS | files={stats['files']} | rows={stats['inserted']} | duplicates={stats['exact_duplicates']} | datasets={len(manifest)}")
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
