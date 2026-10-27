import pytest

from brewops.db.connection import connect
from brewops.db.queries import (
    get_drink_types,
    get_stats,
    get_tap_health,
    get_taps,
    insert_brew,
    insert_maintenance,
)
from brewops.db.schema import init_db, reset_db


@pytest.fixture
def conn(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


def test_init_is_idempotent(conn):
    init_db(conn)
    init_db(conn)
    assert len(get_taps(conn)) == 4
    assert len(get_drink_types(conn)) == 6


def test_reference_data_seeded(conn):
    taps = {t["name"]: t for t in get_taps(conn)}
    assert "Hoppy Bertha (3rd floor)" in taps
    assert taps["Old Faithful (2nd floor)"]["has_telemetry"] is False
    drinks = {d["name"] for d in get_drink_types(conn)}
    assert "neipa" in drinks


def test_stats_math(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.5, 6.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 09:00:00", 11.0, 6.5, "csv")
    insert_brew(conn, 2, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_brew(conn, 3, "dipa", "2026-06-02 11:00:00", 13.0, 7.0, "manual")
    conn.commit()

    stats = get_stats(conn)
    assert stats["total_brews"] == 4
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 2
    assert per_drink["sour"] == 1
    assert per_drink["stout"] == 0
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 2}


def test_tap_health(conn):
    insert_brew(conn, 4, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_maintenance(conn, 4, "clean", "2026-06-03 18:00:00", note="quarterly line clean")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health = get_tap_health(conn, 4)
    assert health["brew_count"] == 1
    assert health["last_brew"] == "2026-06-01 08:00:00"
    assert health["last_maintenance"]["type"] == "clean"
    assert health["recent_errors"][0]["error_code"] == "E42"


def test_tap_health_specialty_is_most_poured(conn):
    insert_brew(conn, 1, "sour", "2026-06-01 08:00:00", 14.0, 5.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 09:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 10:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 2, "dipa", "2026-06-01 10:00:00", 13.0, 7.0, "csv")
    conn.commit()

    assert get_tap_health(conn, 1)["specialty"] == {
        "name": "neipa", "label": "NEIPA", "count": 2,
    }


def test_tap_health_specialty_tie_is_deterministic(conn):
    insert_brew(conn, 3, "stout", "2026-06-01 08:00:00", 15.0, 10.0, "manual")
    insert_brew(conn, 3, "dipa", "2026-06-01 09:00:00", 13.0, 7.0, "manual")
    conn.commit()

    assert get_tap_health(conn, 3)["specialty"]["name"] == "dipa"


def test_tap_health_specialty_none_without_pours(conn):
    assert get_tap_health(conn, 4)["specialty"] is None


def test_tap_health_unknown_tap(conn):
    assert get_tap_health(conn, 999) is None


def test_reset_db_clears_events(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    conn.commit()
    reset_db(conn)
    assert get_stats(conn)["total_brews"] == 0
    assert len(get_taps(conn)) == 4


def test_stats_with_date_range(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 09:00:00", 11.0, 6.5, "csv")
    insert_brew(conn, 2, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_brew(conn, 3, "dipa", "2026-06-02 11:00:00", 13.0, 7.0, "manual")
    insert_brew(conn, 1, "stout", "2026-06-03 12:00:00", 15.0, 9.0, "csv")
    conn.commit()

    # Both bounds
    stats = get_stats(conn, date_from="2026-06-01", date_to="2026-06-02")
    assert stats["total_brews"] == 4
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 2
    assert per_drink["sour"] == 1
    assert per_drink["dipa"] == 1
    assert per_drink["stout"] == 0
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 2}


def test_stats_with_only_date_from(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_brew(conn, 1, "dipa", "2026-06-03 12:00:00", 13.0, 7.0, "manual")
    conn.commit()

    stats = get_stats(conn, date_from="2026-06-02")
    assert stats["total_brews"] == 2
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["sour"] == 1
    assert per_drink["dipa"] == 1
    assert per_drink["neipa"] == 0


def test_stats_with_only_date_to(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_brew(conn, 1, "dipa", "2026-06-03 12:00:00", 13.0, 7.0, "manual")
    conn.commit()

    stats = get_stats(conn, date_to="2026-06-02")
    assert stats["total_brews"] == 2
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 1
    assert per_drink["sour"] == 1
    assert per_drink["dipa"] == 0


def test_stats_single_day_range(conn):
    insert_brew(conn, 1, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "neipa", "2026-06-01 09:00:00", 11.0, 6.5, "csv")
    insert_brew(conn, 2, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    conn.commit()

    stats = get_stats(conn, date_from="2026-06-01", date_to="2026-06-01")
    assert stats["total_brews"] == 2
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 2
    assert per_drink["sour"] == 0


def test_stats_inclusive_end_of_day(conn):
    insert_brew(conn, 1, "neipa", "2026-06-02 23:59:59", 12.0, 6.0, "csv")
    insert_brew(conn, 1, "sour", "2026-06-03 00:00:00", 14.0, 5.0, "csv")
    conn.commit()

    stats = get_stats(conn, date_from="2026-06-02", date_to="2026-06-02")
    assert stats["total_brews"] == 1
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["neipa"] == 1
    assert per_drink["sour"] == 0


def test_stats_empty_range_all_drinks_returned(conn):
    insert_brew(conn, 1, "neipa", "2026-05-01 08:00:00", 12.0, 6.0, "csv")
    conn.commit()

    stats = get_stats(conn, date_from="2026-06-01", date_to="2026-06-02")
    assert stats["total_brews"] == 0
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert len(per_drink) == 6
    assert all(count == 0 for count in per_drink.values())
    assert stats["per_day"] == []


def test_tap_health_with_date_range_filters_brew_count_and_specialty(conn):
    insert_brew(conn, 4, "neipa", "2026-05-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 4, "neipa", "2026-06-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 4, "sour", "2026-06-02 10:00:00", 14.0, 5.0, "csv")
    insert_maintenance(conn, 4, "clean", "2026-05-15 18:00:00", note="old clean")
    conn.commit()

    health = get_tap_health(conn, 4, date_from="2026-06-01", date_to="2026-06-02")
    assert health["brew_count"] == 2
    assert health["specialty"]["name"] == "neipa"
    assert health["specialty"]["count"] == 1
    assert health["last_maintenance"]["type"] == "clean"
    assert health["last_maintenance"]["timestamp"] == "2026-05-15 18:00:00"


def test_tap_health_last_brew_unfiltered(conn):
    insert_brew(conn, 4, "neipa", "2026-05-01 08:00:00", 12.0, 6.0, "csv")
    insert_brew(conn, 4, "sour", "2026-06-10 10:00:00", 14.0, 5.0, "csv")
    conn.commit()

    health = get_tap_health(conn, 4, date_from="2026-06-01", date_to="2026-06-05")
    assert health["brew_count"] == 0
    assert health["last_brew"] == "2026-06-10 10:00:00"
