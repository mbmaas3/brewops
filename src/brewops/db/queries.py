"""Read and write queries. All timestamps are naive local time strings."""

import sqlite3
from datetime import datetime, timedelta
from typing import Any


def _range_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """Turn inclusive 'YYYY-MM-DD' bounds into a half-open [start, end) timestamp range.
    
    Returns (start_timestamp, end_timestamp) where:
    - start = date_from + " 00:00:00", or None if date_from is None
    - end = midnight of the day AFTER date_to, or None if date_to is None
    """
    start = None
    if date_from is not None:
        start = date_from + " 00:00:00"
    
    end = None
    if date_to is not None:
        date_obj = datetime.strptime(date_to, "%Y-%m-%d")
        next_day = date_obj + timedelta(days=1)
        end = next_day.strftime("%Y-%m-%d 00:00:00")
    
    return (start, end)


def _range_clause(start: str | None, end: str | None, column: str = "timestamp") -> tuple[str, list[str]]:
    """Build a SQL fragment and params for timestamp range filtering.
    
    Returns (sql_fragment, params_list) where the fragment can be joined with AND/WHERE.
    """
    conditions = []
    params = []
    
    if start is not None:
        conditions.append(f"{column} >= ?")
        params.append(start)
    
    if end is not None:
        conditions.append(f"{column} < ?")
        params.append(end)
    
    sql = " AND ".join(conditions)
    return (sql, params)


def get_taps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, floor, has_telemetry FROM taps ORDER BY id")
    return [dict(r) | {"has_telemetry": bool(r["has_telemetry"])} for r in rows]


def get_tap(conn: sqlite3.Connection, tap_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, floor, has_telemetry FROM taps WHERE id = ?", (tap_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row) | {"has_telemetry": bool(row["has_telemetry"])}


def get_drink_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, label FROM drink_types ORDER BY id")
    return [dict(r) for r in rows]


def drink_type_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM drink_types WHERE name = ?", (name,)).fetchone()
    return row is not None


def insert_brew(
    conn: sqlite3.Connection,
    tap_id: int,
    drink_type: str,
    timestamp: str,
    duration_s: float,
    temp_c: float,
    source: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO brew_events (tap_id, drink_type, timestamp, duration_s, temp_c, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (tap_id, drink_type, timestamp, duration_s, temp_c, source),
    )
    return cur.lastrowid


def insert_maintenance(
    conn: sqlite3.Connection,
    tap_id: int,
    type: str,
    timestamp: str,
    note: str | None = None,
    error_code: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO maintenance_events (tap_id, type, timestamp, note, error_code)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tap_id, type, timestamp, note, error_code),
    )
    return cur.lastrowid


def get_stats(conn: sqlite3.Connection, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    """Dashboard numbers: totals, per-drink, per-day.
    
    Optional date_from and date_to as 'YYYY-MM-DD' strings (both inclusive).
    """
    start, end = _range_bounds(date_from, date_to)
    range_sql, range_params = _range_clause(start, end, "timestamp")
    
    # Total brews
    if range_sql:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM brew_events WHERE {range_sql}",
            range_params
        ).fetchone()["n"]
    else:
        total = conn.execute("SELECT COUNT(*) AS n FROM brew_events").fetchone()["n"]
    
    # Per-drink: range goes in JOIN condition, not WHERE, to preserve zero-count drinks
    join_condition = "be.drink_type = dt.name"
    if range_sql:
        join_condition += f" AND {range_sql}"
    
    per_drink = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT dt.name, dt.label, COUNT(be.id) AS count
            FROM drink_types dt
            LEFT JOIN brew_events be ON {join_condition}
            GROUP BY dt.id
            ORDER BY dt.id
            """,
            range_params
        )
    ]
    
    # Per-day
    if range_sql:
        per_day = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT DATE(timestamp) AS day, COUNT(*) AS count
                FROM brew_events
                WHERE {range_sql}
                GROUP BY DATE(timestamp)
                ORDER BY day
                """,
                range_params
            )
        ]
    else:
        per_day = [
            dict(r)
            for r in conn.execute(
                """
                SELECT DATE(timestamp) AS day, COUNT(*) AS count
                FROM brew_events
                GROUP BY DATE(timestamp)
                ORDER BY day
                """
            )
        ]
    
    return {"total_brews": total, "per_drink": per_drink, "per_day": per_day}


def get_tap_health(conn: sqlite3.Connection, tap_id: int, date_from: str | None = None, date_to: str | None = None) -> dict[str, Any] | None:
    """Tap card: pour activity plus maintenance history.
    
    Optional date_from and date_to as 'YYYY-MM-DD' strings filter brew_count and specialty only.
    last_brew, last_maintenance, and recent_errors are unfiltered.
    """
    tap = get_tap(conn, tap_id)
    if tap is None:
        return None
    
    start, end = _range_bounds(date_from, date_to)
    range_sql, range_params = _range_clause(start, end, "timestamp")
    
    # Brew count with range filter
    if range_sql:
        brews = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM brew_events WHERE tap_id = ? AND {range_sql}
            """,
            [tap_id] + range_params
        ).fetchone()
        brew_count = brews["count"]
    else:
        brews = conn.execute(
            "SELECT COUNT(*) AS count FROM brew_events WHERE tap_id = ?",
            (tap_id,)
        ).fetchone()
        brew_count = brews["count"]
    
    # Last brew: unfiltered
    last_brew_row = conn.execute(
        "SELECT MAX(timestamp) AS last_brew FROM brew_events WHERE tap_id = ?",
        (tap_id,)
    ).fetchone()
    last_brew = last_brew_row["last_brew"] if last_brew_row else None
    
    # Last maintenance: unfiltered
    last_maintenance = conn.execute(
        """
        SELECT type, timestamp, note, error_code
        FROM maintenance_events
        WHERE tap_id = ? AND type != 'error'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (tap_id,),
    ).fetchone()
    
    # Recent errors: unfiltered
    recent_errors = [
        dict(r)
        for r in conn.execute(
            """
            SELECT timestamp, error_code, note
            FROM maintenance_events
            WHERE tap_id = ? AND type = 'error'
            ORDER BY timestamp DESC LIMIT 5
            """,
            (tap_id,),
        )
    ]
    
    # Specialty with range filter
    if range_sql:
        specialty = conn.execute(
            f"""
            SELECT be.drink_type AS name, COALESCE(dt.label, be.drink_type) AS label, COUNT(*) AS count
            FROM brew_events be
            LEFT JOIN drink_types dt ON dt.name = be.drink_type
            WHERE be.tap_id = ? AND {range_sql}
            GROUP BY be.drink_type
            ORDER BY count DESC, label ASC
            LIMIT 1
            """,
            [tap_id] + range_params
        ).fetchone()
    else:
        specialty = conn.execute(
            """
            SELECT be.drink_type AS name, COALESCE(dt.label, be.drink_type) AS label, COUNT(*) AS count
            FROM brew_events be
            LEFT JOIN drink_types dt ON dt.name = be.drink_type
            WHERE be.tap_id = ?
            GROUP BY be.drink_type
            ORDER BY count DESC, label ASC
            LIMIT 1
            """,
            (tap_id,),
        ).fetchone()
    
    return tap | {
        "brew_count": brew_count,
        "last_brew": last_brew,
        "last_maintenance": dict(last_maintenance) if last_maintenance else None,
        "recent_errors": recent_errors,
        "specialty": dict(specialty) if specialty else None,
    }
