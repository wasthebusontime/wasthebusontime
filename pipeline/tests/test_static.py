from datetime import date

import duckdb
from synth import default_trips, write_gtfs

from wbot_pipeline import static


def test_version_in_effect_is_the_newest_fetched_before_the_day_ended(tmp_path):
    trips = default_trips()
    write_gtfs(tmp_path, "v1_11111111", trips, feed_version="v1", start="20261001", end="20261231",
               fetched_at="2026-10-01T10:00:00Z")
    write_gtfs(tmp_path, "v2_22222222", trips, feed_version="v2", start="20261015", end="20270115",
               fetched_at="2026-10-20T10:00:00Z")
    versions = static.list_versions(tmp_path)
    pick = lambda d: static.choose(versions, d).feed_version
    assert pick(date(2026, 10, 10)) == "v1"         # v2 doesn't cover it
    assert pick(date(2026, 10, 16)) == "v1"         # v2 covers it but wasn't fetched yet
    assert pick(date(2026, 10, 20)) == "v2"         # fetched that morning
    assert pick(date(2027, 1, 10)) == "v2"
    assert static.choose(versions, date(2027, 2, 1)) is None
    assert static.most_current(versions, date(2026, 10, 4), date(2026, 10, 25)).feed_version == "v2"


def test_first_days_use_the_earliest_fetched_covering_version(tmp_path):
    # Collection began after the period started: nothing was fetched before 10-02 ended.
    write_gtfs(tmp_path, "v1_11111111", default_trips(), fetched_at="2026-10-04T03:23:33.116Z")
    versions = static.list_versions(tmp_path)
    assert static.choose(versions, date(2026, 10, 2)).stem == "v1_11111111"


def test_cache_and_active_services(tmp_path):
    zip_path = write_gtfs(tmp_path / "static", "v1_11111111", default_trips(),
                          calendar_dates=[("WK", "20261007", "2"), ("SAT", "20261007", "1")], with_shapes=False)
    (version,) = static.list_versions(zip_path.parent)
    cache = static.ensure_cache(tmp_path / "derived", version)
    assert static.ensure_cache(tmp_path / "derived", version) == cache   # second call reuses it
    con = duckdb.connect()
    static.register(con, cache)
    assert static.active_services(con, date(2026, 10, 6)) == ["WK"]
    assert static.active_services(con, date(2026, 10, 7)) == ["SAT"]     # swapped by calendar_dates
    assert static.active_services(con, date(2026, 10, 10)) == ["SAT"]
    assert static.active_services(con, date(2026, 10, 11)) == []
    # Byte order mark stripped, quoted names kept, missing optional files are empty tables.
    assert con.execute("SELECT stop_name FROM stops WHERE stop_id = 'S2'").fetchone() == ("Example St & 1st Ave",)
    assert con.execute("SELECT count(*) FROM shapes").fetchone() == (0,)
    assert con.execute("SELECT stop_code FROM stops WHERE stop_id = 'S7'").fetchone() == (None,)


def test_cache_version_follows_the_columns():
    base = static.cache_version()
    added = {**static.COLUMNS, "trips": [*static.COLUMNS["trips"], "wheelchair_accessible"]}
    renamed = {**static.COLUMNS, "stops": ["stop_id", "stop_code", "stop_desc", "stop_lat", "stop_lon"]}
    reordered = {**static.COLUMNS, "routes": static.COLUMNS["routes"][::-1]}
    new_table = {**static.COLUMNS, "frequencies": ["trip_id", "start_time"]}
    versions = [static.cache_version(c) for c in (added, renamed, reordered, new_table)]
    assert len({base, *versions}) == 5
    assert static.cache_version(fmt=static.CACHE_FORMAT + 1) != base
    # Table order doesn't change it.
    assert static.cache_version(dict(reversed(static.COLUMNS.items()))) == base


def test_cache_version_is_a_stable_digest():
    # A fixed digest, not hash(): the same across runs and Python versions.
    assert static.cache_version({"routes": ["route_id"]}, 1) == "10a0e96f231932ae"


def test_cache_with_other_columns_is_rebuilt(tmp_path, monkeypatch):
    zip_path = write_gtfs(tmp_path / "static", "v1_11111111", default_trips())
    (version,) = static.list_versions(zip_path.parent)
    cache = static.ensure_cache(tmp_path / "derived", version)
    columns = {**static.COLUMNS, "trips": [*static.COLUMNS["trips"], "wheelchair_accessible"]}
    monkeypatch.setattr(static, "COLUMNS", columns)
    monkeypatch.setattr(static, "CACHE_VERSION", static.cache_version(columns))
    static.ensure_cache(tmp_path / "derived", version)
    con = duckdb.connect()
    static.register(con, cache)
    assert con.execute("SELECT count(wheelchair_accessible) FROM trips").fetchone() == (0,)
