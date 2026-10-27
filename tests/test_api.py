import pytest

from brewops.api.main import app
from brewops.db.connection import connect
from brewops.db.queries import insert_brew, insert_maintenance
from brewops.db.schema import init_db

from asgi_client import request


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "api-test.db"
    monkeypatch.setenv("BREWOPS_DB", str(path))
    conn = connect(path)
    init_db(conn)
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.5, 6.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 09:00:00", 11.0, 6.5, "csv")
    insert_brew(conn, 2, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_maintenance(conn, 1, "clean", "2026-06-03 18:00:00", note="quarterly")
    conn.commit()
    yield conn
    conn.close()


def test_stats(db):
    r = request(app, "GET", "/api/stats")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 3
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 2
    assert {d["day"]: d["count"] for d in stats["per_day"]} == {
        "2026-06-01": 2,
        "2026-06-02": 1,
    }


def test_taps_list_and_health(db):
    r = request(app, "GET", "/api/taps")
    assert r.status == 200
    assert len(r.json()) == 4

    r = request(app, "GET", "/api/taps/1")
    assert r.status == 200
    health = r.json()
    assert health["brew_count"] == 2
    assert health["last_maintenance"]["type"] == "clean"
    assert health["specialty"] == {"name": "neipa", "label": "NEIPA", "count": 2}

    assert request(app, "GET", "/api/taps/4").json()["specialty"] is None


def test_tap_health_404(db):
    r = request(app, "GET", "/api/taps/999")
    assert r.status == 404


def test_drink_types(db):
    r = request(app, "GET", "/api/drink-types")
    assert r.status == 200
    assert {d["name"] for d in r.json()} >= {"neipa", "sour", "stout"}


def test_post_brew_ok_and_visible_in_stats(db):
    r = request(app, "POST", "/api/brews", {
        "tap_id": 3,
        "drink_type": "dipa",
        "timestamp": "2026-06-05 14:30:00",
    })
    assert r.status == 200, r.text
    assert r.json()["status"] == "logged"

    row = db.execute("SELECT source, duration_s FROM brew_events WHERE tap_id = 3").fetchone()
    assert row["source"] == "manual"
    assert row["duration_s"] is None

    stats = request(app, "GET", "/api/stats").json()
    assert stats["total_brews"] == 4


def test_post_brew_accepts_html_form_timestamp(db):
    r = request(app, "POST", "/api/brews", {
        "tap_id": 1,
        "drink_type": "neipa",
        "timestamp": "2026-06-05T14:30",
    })
    assert r.status == 200, r.text
    row = db.execute(
        "SELECT timestamp FROM brew_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["timestamp"] == "2026-06-05 14:30:00"


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"tap_id": 99, "drink_type": "neipa", "timestamp": "2026-06-05 14:30:00"}, "unknown tap"),
        ({"tap_id": 1, "drink_type": "unicorn_frappe", "timestamp": "2026-06-05 14:30:00"}, "unknown drink"),
        ({"tap_id": 1, "drink_type": "neipa", "timestamp": "2099-01-01 00:00:00"}, "future"),
        ({"tap_id": 1, "drink_type": "neipa", "timestamp": "yesterday-ish"}, "unparsable"),
    ],
)
def test_post_brew_validation(db, payload, fragment):
    r = request(app, "POST", "/api/brews", payload)
    assert r.status == 400
    assert fragment in r.json()["detail"]


def test_post_maintenance(db):
    r = request(app, "POST", "/api/maintenance", {
        "tap_id": 2,
        "type": "clean",
        "timestamp": "2026-06-06 09:00:00",
        "note": "smelled funny",
    })
    assert r.status == 200
    r = request(app, "POST", "/api/maintenance", {
        "tap_id": 2,
        "type": "exploded",
        "timestamp": "2026-06-06 09:00:00",
    })
    assert r.status == 400


def test_stats_with_date_range(db):
    r = request(app, "GET", "/api/stats?date_from=2026-06-01&date_to=2026-06-02")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 3
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 2
    assert per_drink["sour"] == 1


def test_stats_single_day_range(db):
    r = request(app, "GET", "/api/stats?date_from=2026-06-02&date_to=2026-06-02")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 1
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-02": 1}


def test_stats_future_range_returns_zeros(db):
    r = request(app, "GET", "/api/stats?date_from=2030-01-01")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 0
    assert len(stats["per_drink"]) == 6
    assert all(d["count"] == 0 for d in stats["per_drink"])
    assert stats["per_day"] == []


def test_stats_inverted_date_range_returns_400(db):
    r = request(app, "GET", "/api/stats?date_from=2026-06-02&date_to=2026-06-01")
    assert r.status == 400
    assert "after" in r.json()["detail"]


def test_stats_malformed_date_returns_400(db):
    r = request(app, "GET", "/api/stats?date_from=not-a-date")
    assert r.status == 400
    assert "unparsable" in r.json()["detail"]


def test_stats_empty_date_params_ignored(db):
    r1 = request(app, "GET", "/api/stats")
    r2 = request(app, "GET", "/api/stats?date_from=&date_to=")
    assert r1.json() == r2.json()


def test_tap_health_with_date_range(db):
    r = request(app, "GET", "/api/taps/1?date_from=2026-06-01&date_to=2026-06-02")
    assert r.status == 200
    health = r.json()
    assert health["brew_count"] == 2
    assert health["specialty"]["name"] == "neipa"
