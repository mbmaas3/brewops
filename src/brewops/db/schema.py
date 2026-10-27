"""Schema and reference data.

Timestamps are stored as naive local time, 'YYYY-MM-DD HH:MM:SS'.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS taps (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    floor         INTEGER NOT NULL,
    has_telemetry INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS drink_types (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brew_events (
    id         INTEGER PRIMARY KEY,
    tap_id     INTEGER NOT NULL REFERENCES taps(id),
    drink_type TEXT NOT NULL REFERENCES drink_types(name),
    timestamp  TEXT NOT NULL,
    duration_s REAL,
    temp_c     REAL,
    source     TEXT NOT NULL CHECK (source IN ('csv', 'manual'))
);

CREATE TABLE IF NOT EXISTS maintenance_events (
    id         INTEGER PRIMARY KEY,
    tap_id     INTEGER NOT NULL REFERENCES taps(id),
    type       TEXT NOT NULL CHECK (type IN ('clean', 'refill', 'repair', 'error')),
    timestamp  TEXT NOT NULL,
    note       TEXT,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_brew_events_tap ON brew_events(tap_id);
CREATE INDEX IF NOT EXISTS idx_brew_events_timestamp ON brew_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_maintenance_events_tap ON maintenance_events(tap_id);
"""

# The draught taps. Old Faithful predates telemetry; its pours are logged by hand in the UI.
TAPS = [
    (1, "Hoppy Bertha (3rd floor)", 3, True),
    (2, "Tappy McTapface (kitchen)", 1, True),
    (3, "Old Faithful (2nd floor)", 2, False),
    (4, "Hop Rocket (4th floor)", 4, True),
]

# Supported special beers. name is the key used in events and the API.
DRINK_TYPES = [
    ("neipa", "NEIPA"),
    ("dipa", "DIPA"),
    ("stout", "Stout"),
    ("sour", "Sour"),
    ("lambic", "Lambic"),
    ("barleywine", "Barleywine"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and insert reference data. Safe to call repeatedly."""
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO taps (id, name, floor, has_telemetry) VALUES (?, ?, ?, ?)",
        TAPS,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO drink_types (name, label) VALUES (?, ?)",
        DRINK_TYPES,
    )
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drop all data and recreate the schema (used by `uv run seed`)."""
    conn.executescript(
        """
        DROP TABLE IF EXISTS brew_events;
        DROP TABLE IF EXISTS maintenance_events;
        DROP TABLE IF EXISTS drink_types;
        DROP TABLE IF EXISTS taps;
        DROP TABLE IF EXISTS machines;
        """
    )
    init_db(conn)
