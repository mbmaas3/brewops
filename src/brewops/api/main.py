"""FastAPI app: JSON API + static frontend, one process, port 8123."""

import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from brewops.db import queries
from brewops.db.connection import connect
from brewops.db.schema import init_db

HOST = "127.0.0.1"
PORT = 8123

TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with closing(connect()) as conn:
        init_db(conn)
    yield


app = FastAPI(title="BrewOps", lifespan=lifespan)


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def parse_timestamp(value: str) -> str:
    """Accept form ('YYYY-MM-DDTHH:MM') and log ('YYYY-MM-DD HH:MM:SS') styles;
    normalize to the storage format. Rejects future timestamps."""
    for fmt in TIMESTAMP_FORMATS:
        try:
            ts = datetime.strptime(value.strip(), fmt)
            break
        except ValueError:
            continue
    else:
        raise HTTPException(400, f"unparsable timestamp {value!r}")
    if ts > datetime.now():
        raise HTTPException(400, "timestamp is in the future")
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def parse_date_range(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """Validate and normalize date range parameters.
    
    Accepts 'YYYY-MM-DD' strings. Empty strings are treated as None.
    Rejects unparsable dates and date_from > date_to.
    Does NOT reject future dates.
    """
    # Treat empty strings as None
    if date_from == "":
        date_from = None
    if date_to == "":
        date_to = None
    
    # Parse and validate each non-empty value
    parsed_from = None
    if date_from is not None:
        try:
            parsed_from = datetime.strptime(date_from.strip(), "%Y-%m-%d")
            date_from = parsed_from.strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"unparsable date {date_from!r}")
    
    parsed_to = None
    if date_to is not None:
        try:
            parsed_to = datetime.strptime(date_to.strip(), "%Y-%m-%d")
            date_to = parsed_to.strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(400, f"unparsable date {date_to!r}")
    
    # Check that date_from is not after date_to
    if parsed_from is not None and parsed_to is not None and parsed_from > parsed_to:
        raise HTTPException(400, "date_from is after date_to")
    
    return (date_from, date_to)


class BrewIn(BaseModel):
    tap_id: int
    drink_type: str
    timestamp: str
    duration_s: float | None = None
    temp_c: float | None = None


class MaintenanceIn(BaseModel):
    tap_id: int
    type: str
    timestamp: str
    note: str | None = None
    error_code: str | None = None


@app.get("/api/stats")
def stats(date_from: str | None = None, date_to: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    start, end = parse_date_range(date_from, date_to)
    return queries.get_stats(conn, start, end)


@app.get("/api/taps")
def taps(conn: sqlite3.Connection = Depends(get_db)):
    return queries.get_taps(conn)


@app.get("/api/taps/{tap_id}")
def tap_health(tap_id: int, date_from: str | None = None, date_to: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    start, end = parse_date_range(date_from, date_to)
    health = queries.get_tap_health(conn, tap_id, start, end)
    if health is None:
        raise HTTPException(404, f"no tap with id {tap_id}")
    return health


@app.get("/api/drink-types")
def drink_types(conn: sqlite3.Connection = Depends(get_db)):
    return queries.get_drink_types(conn)


@app.post("/api/brews")
def log_brew(brew: BrewIn, conn: sqlite3.Connection = Depends(get_db)):
    if queries.get_tap(conn, brew.tap_id) is None:
        raise HTTPException(400, f"unknown tap_id {brew.tap_id}")
    if not queries.drink_type_exists(conn, brew.drink_type):
        raise HTTPException(400, f"unknown drink_type {brew.drink_type!r}")
    timestamp = parse_timestamp(brew.timestamp)
    brew_id = queries.insert_brew(
        conn, brew.tap_id, brew.drink_type, timestamp,
        brew.duration_s, brew.temp_c, source="manual",
    )
    conn.commit()
    return {"id": brew_id, "status": "logged"}


@app.post("/api/maintenance")
def log_maintenance(event: MaintenanceIn, conn: sqlite3.Connection = Depends(get_db)):
    if queries.get_tap(conn, event.tap_id) is None:
        raise HTTPException(400, f"unknown tap_id {event.tap_id}")
    if event.type not in ("clean", "refill", "repair", "error"):
        raise HTTPException(400, f"unknown maintenance type {event.type!r}")
    timestamp = parse_timestamp(event.timestamp)
    event_id = queries.insert_maintenance(
        conn, event.tap_id, event.type, timestamp, event.note, event.error_code
    )
    conn.commit()
    return {"id": event_id, "status": "logged"}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def run() -> None:
    uvicorn.run(app, host=HOST, port=PORT)
