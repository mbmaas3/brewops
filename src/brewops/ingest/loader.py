"""CSV ingestion: parse, validate, load.

File conventions for an inbox folder:

- ``brews_*.csv``        columns: tap_id, drink, timestamp, duration_s, temp_c
- ``maintenance_*.csv``  columns: tap_id, type, timestamp, note, error_code
- ``manual_*.csv``       brew columns; loaded with source='manual'
                         (exports of the paper log kept next to Old Faithful's tap)

Timestamps are naive local time, 'YYYY-MM-DD HH:MM:SS'. Rows that fail
validation are skipped and reported; the rest of the file still loads.
"""

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
MAINTENANCE_TYPES = {"clean", "refill", "repair", "error"}

BREW_COLUMNS = ["tap_id", "drink", "timestamp", "duration_s", "temp_c"]
MAINTENANCE_COLUMNS = ["tap_id", "type", "timestamp", "note", "error_code"]


@dataclass
class IngestReport:
    files: int = 0
    brews_loaded: int = 0
    maintenance_loaded: int = 0
    rejected: list[tuple[str, int, str]] = field(default_factory=list)  # (file, line, reason)
    skipped_files: list[str] = field(default_factory=list)


def _parse_timestamp(value: str, now: datetime) -> tuple[str | None, str | None]:
    try:
        ts = datetime.strptime(value.strip(), TIMESTAMP_FORMAT)
    except ValueError:
        return None, f"unparsable timestamp {value!r} (expected YYYY-MM-DD HH:MM:SS)"
    if ts > now:
        return None, f"timestamp {value!r} is in the future"
    return ts.strftime(TIMESTAMP_FORMAT), None


def _known_taps(conn: sqlite3.Connection) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM taps")}


def _known_drinks(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM drink_types")}


def ingest_file(conn: sqlite3.Connection, path: Path, report: IngestReport, now: datetime) -> None:
    name = path.name.lower()
    if name.startswith("brews"):
        kind, source = "brew", "csv"
    elif name.startswith("manual"):
        kind, source = "brew", "manual"
    elif name.startswith("maintenance"):
        kind, source = "maintenance", None
    else:
        report.skipped_files.append(path.name)
        return

    taps = _known_taps(conn)
    drinks = _known_drinks(conn)
    report.files += 1

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # header is line 1
            reason = _load_row(conn, kind, source, row, taps, drinks, now)
            if reason is None:
                if kind == "brew":
                    report.brews_loaded += 1
                else:
                    report.maintenance_loaded += 1
            else:
                report.rejected.append((path.name, line_no, reason))
    conn.commit()


def _load_row(
    conn: sqlite3.Connection,
    kind: str,
    source: str | None,
    row: dict,
    taps: set[int],
    drinks: set[str],
    now: datetime,
) -> str | None:
    """Validate and insert one row. Returns a rejection reason, or None on success."""
    try:
        tap_id = int((row.get("tap_id") or "").strip())
    except ValueError:
        return f"bad tap_id {row.get('tap_id')!r}"
    if tap_id not in taps:
        return f"unknown tap_id {tap_id}"

    timestamp, reason = _parse_timestamp(row.get("timestamp") or "", now)
    if reason:
        return reason

    if kind == "brew":
        drink = (row.get("drink") or "").strip()
        if drink not in drinks:
            return f"unknown drink {drink!r}"
        try:
            duration_s = float((row.get("duration_s") or "").strip())
            temp_c = float((row.get("temp_c") or "").strip())
        except ValueError:
            return f"bad numeric fields duration_s={row.get('duration_s')!r} temp_c={row.get('temp_c')!r}"
        if duration_s <= 0:
            return f"non-positive duration_s {duration_s}"
        conn.execute(
            """
            INSERT INTO brew_events (tap_id, drink_type, timestamp, duration_s, temp_c, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tap_id, drink, timestamp, duration_s, temp_c, source),
        )
    else:
        mtype = (row.get("type") or "").strip()
        if mtype not in MAINTENANCE_TYPES:
            return f"unknown maintenance type {mtype!r}"
        conn.execute(
            """
            INSERT INTO maintenance_events (tap_id, type, timestamp, note, error_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (tap_id, mtype, timestamp, (row.get("note") or "").strip() or None,
             (row.get("error_code") or "").strip() or None),
        )
    return None


def ingest_path(conn: sqlite3.Connection, path: Path, now: datetime | None = None) -> IngestReport:
    """Ingest a CSV file, or every CSV in a folder (sorted, for determinism)."""
    now = now or datetime.now()
    report = IngestReport()
    files = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    for f in files:
        ingest_file(conn, f, report, now)
    return report
