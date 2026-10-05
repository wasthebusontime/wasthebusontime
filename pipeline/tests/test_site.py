"""End to end on simulated data: the pipeline's output must satisfy the site's own loader
and build, and its numbers must match a plain-Python recount of the stop events."""

import csv
import json
from datetime import date, timedelta

import duckdb
import pytest
from conftest import DAY, after
from synth import Feed, TripPlan, default_trips, write_gtfs

from wbot_pipeline import pipeline, sitefiles, timeutil
from wbot_pipeline.config import load_settings
from wbot_pipeline.perf import reference
from wbot_site import data as site_data
from wbot_site.build import build as site_build

DAY2 = DAY + timedelta(days=1)
GENERATED = "2026-10-08T12:00:00Z"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("world")
    settings = load_settings({}, data_dir=root / "data", derived_dir=root / "derived", first_service_date=DAY,
                             methodology_changes=(("2026-10-07", "2"),))
    trips = default_trips()
    write_gtfs(settings.static_dir, "test1_aaaaaaaa", trips)
    f1 = Feed(settings.data_dir, DAY, trips)
    f1.plans["901-0-0-v1"] = TripPlan(delays=[-20, 0, 45, 301, 120])
    f1.plans["901-1-0-v1"] = TripPlan(delays=[-61, -60, -1, 300, 30])
    f1.plans["901-1-1-v1"] = TripPlan(cancel=True)
    f1.plans["902-0-1-v1"] = TripPlan(skip={1})
    o = timeutil.origin(DAY)
    f1.gaps = [(o + 8 * 3600 + 20 * 60, o + 8 * 3600 + 40 * 60)]
    f1.write()
    f2 = Feed(settings.data_dir, DAY2, trips)
    f2.plans["901-0-2-v1"] = TripPlan(vehicle=False)
    f2.plans["901-0-3-v1"] = TripPlan(delays=[700, 800, 900, 1000, 1100])
    f2.write()
    result = pipeline.run(settings, now=after(DAY2), do_publish=False, generated_at=GENERATED)
    return settings, result


def load(world, *parts):
    settings, _ = world
    return json.loads(settings.out_dir.joinpath("site", *parts).read_text(encoding="utf-8"))


def eligible(settings, where="TRUE"):
    con = duckdb.connect()
    glob = (settings.derived_dir / "events" / "*" / "*.parquet").as_posix()
    return [r[0] for r in con.execute(f"""
        SELECT delay_dep_s FROM read_parquet('{glob}')
        WHERE observed AND scheduled AND stop_sr = 'SCHEDULED' AND trip_sr = 'SCHEDULED' AND NOT is_last
              AND delay_dep_s IS NOT NULL AND {where}""").fetchall()]


def test_run_processes_both_days(world):
    _, result = world
    assert result.processed == {DAY.isoformat(): "ok", DAY2.isoformat(): "ok"}
    assert result.wrote_site and result.published == "disabled"


def test_site_loader_and_build_accept_the_output(world, tmp_path):
    settings, _ = world
    stats = site_data.load_stats(settings.out_dir)      # checks schema, windows, every route, stop and preset
    assert not stats.synthetic
    assert stats.map_periods() == ["all", "2026-10"]
    out = tmp_path / "dist"
    b = site_build(settings.out_dir, out, "prod", code_commit="abc", stats_commit="def")
    assert (out / "routes" / "901" / "index.html").exists()
    assert (out / "stops" / "E201" / "index.html").exists()
    assert (out / "stops" / "S7" / "index.html").exists()
    assert (out / "data" / "system_daily.csv").exists()
    assert len(b.pages) > 10


def test_meta(world):
    meta = load(world, "meta.json")
    assert meta["synthetic"] is False and meta["schema"] == 1
    assert meta["data_through"] == DAY2.isoformat() and meta["collection_start"] == DAY.isoformat()
    assert meta["generated_at"] == GENERATED
    assert meta["static_versions"] == ["test1"] and meta["min_sample"] == 30
    assert meta["windows"] == {"headline": [0, 300], "alt": [-60, 300]}
    assert len(meta["inputs_sha256"]) == 64


def test_system_numbers_match_a_recount(world):
    settings, _ = world
    system = load(world, "system.json")
    assert system["scopes"]["all_stops"]["summary"] == reference(eligible(settings))
    assert system["scopes"]["timepoints"]["summary"] == reference(eligible(settings, "is_timepoint"))
    assert system["terminal"] == reference(eligible(settings, "is_first"))
    s = system["scopes"]["all_stops"]["summary"]
    assert s["early"] + s["on_time"] + s["late"] == s["n"] == s["early_alt"] + s["on_time_alt"] + s["late_alt"]
    daily = system["scopes"]["timepoints"]["daily"]
    assert [d["date"] for d in daily] == [DAY.isoformat(), DAY2.isoformat()]
    assert "p50" not in daily[0] and "hist" not in daily[0]
    assert system["scopes"]["timepoints"]["monthly"][0]["partial"] is True
    assert [r["slug"] for r in system["routes"]] == ["901", "902"]
    assert system["end_of_line"]["n"] > 0


def test_window_boundaries(world):
    route = load(world, "routes", "901.json")
    # 901-1-0 on day 1: -61, -60, -1, 300 at its non-last stops (plus every other trip at +30).
    by_dir = {d["direction_id"]: d for d in route["scopes"]["all_stops"]["by_direction"]}
    assert by_dir[1]["early"] == 3 and by_dir[1]["early_alt"] == 1


def test_trip_facts_and_notices(world):
    system = load(world, "system.json")
    t = system["trips"]
    assert t["cancelled"] == 1 and t["skipped_stops"] == 1 and t["added"] == 0
    assert t["scheduled"] == t["observed"] + t["not_observed"] + t["cancelled"]
    assert t["not_observed"] >= 1    # the trip without a vehicle
    codes = [n["code"] for n in system["notices"]]
    assert codes[0] == "provisional" and "data_loss" in codes and "methodology_change" in codes
    quality = load(world, "quality.json")
    assert [d["date"] for d in quality["days"]] == [DAY.isoformat(), DAY2.isoformat()]
    (loss,) = quality["data_loss"]
    assert loss["cause"] == "collector" and loss["start"].endswith("Z")
    assert quality["days"][0]["uptime_service_hours"] < 1.0 == quality["days"][1]["uptime_service_hours"]
    route_codes = [n["code"] for n in load(world, "routes", "901.json")["notices"]]
    assert route_codes == ["provisional", "data_loss"]


def test_route_stops_in_order_and_stop_pages(world):
    route = load(world, "routes", "902.json")
    (loop,) = route["stops"]
    assert [s["code"] for s in loop["stops"]][:1] == ["E205"]
    assert {s["code"] for s in loop["stops"]} == {"E205", "E206", "S7"}
    index = load(world, "stops.json")["stops"]
    assert [s["name"] for s in index] == sorted(s["name"] for s in index)
    tc = next(s for s in index if s["code"] == "E201")
    assert tc["routes"] == ["901"] and tc["lat"] == 47.04 and tc["lon"] == -122.9
    stop = load(world, "stops", "E202.json")
    assert stop["stop"]["timepoint_somewhere"] is False and stop["scopes"]["timepoints"] is None
    assert stop["stop"]["timepoint_routes"] == []
    assert load(world, "stops", "E201.json")["stop"]["timepoint_routes"] == ["901"]
    # E205 is a timepoint on both routes (901 ends there, the 902 loop starts there).
    assert load(world, "stops", "E205.json")["stop"]["timepoint_routes"] == ["901", "902"]
    assert "daily" not in stop["scopes"]["all_stops"]
    assert [r["slug"] for r in stop["scopes"]["all_stops"]["by_route"]] == ["901"]


def test_map_presets_add_up(world):
    index = load(world, "stops.json")["stops"]
    total = load(world, "map", "all", "all-all.json")
    parts = [load(world, "map", "all", f"{dt}-all.json") for dt in ("weekday", "saturday", "sunday")]
    for i in range(len(index)):
        assert total["all_stops"][i] == [sum(p["all_stops"][i][k] for p in parts) for k in range(5)]
    geo = load(world, "routes.geojson")
    keys = {(f["properties"]["slug"], f["properties"]["direction_id"]) for f in geo["features"]}
    assert keys == {("901", 0), ("901", 1), ("902", 0)}
    line = next(f for f in geo["features"] if f["properties"]["slug"] == "901")["geometry"]["coordinates"]
    assert line[0] == [-122.9, 47.04] and len(line) == 2     # straight line: inner points simplified away


def test_csv_columns(world):
    settings, _ = world
    csv_dir = settings.out_dir / "csv"
    names = sorted(p.name for p in csv_dir.glob("*.csv"))
    assert names == sorted(f"{n}.csv" for n in (
        "system_daily", "routes_daily", "routes_monthly", "routes_hourly_monthly", "stops_monthly",
        "terminal_monthly", "end_of_line_monthly", "quality_daily", "data_loss"))
    with open(csv_dir / "system_daily.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0])[:9] == ["date", "scope", "n", "early", "on_time", "late", "early_alt", "on_time_alt",
                                 "late_alt"]
    assert list(rows[0])[9] == "under" and list(rows[0])[10] == "m_10" and list(rows[0])[-1] == "over"
    system = load(world, "system.json")
    day1 = next(r for r in rows if r["date"] == DAY.isoformat() and r["scope"] == "timepoints")
    assert int(day1["n"]) == system["scopes"]["timepoints"]["daily"][0]["n"]
    with open(csv_dir / "stops_monthly.csv", encoding="utf-8") as f:
        assert next(csv.reader(f))[:4] == ["month", "stop_code", "stop_name", "route"]
    assert (settings.derived_dir / "agg" / "routes_daily.parquet").exists()


def test_output_is_deterministic(world, tmp_path):
    settings, _ = world
    before = {p.relative_to(settings.out_dir): p.read_bytes() for p in settings.out_dir.rglob("*") if p.is_file()}
    sitefiles.build(settings, settings.out_dir, pipeline_version=load(world, "meta.json")["pipeline_version"],
                    generated_at=GENERATED)
    after_ = {p.relative_to(settings.out_dir): p.read_bytes() for p in settings.out_dir.rglob("*") if p.is_file()}
    assert before == after_


def test_thresholds_are_settings(world, tmp_path):
    settings, _ = world
    strict = load_settings({}, data_dir=settings.data_dir, derived_dir=settings.derived_dir,
                           first_service_date=DAY, trip_coverage_min=1.0, min_sample=10, provisional=False)
    out = tmp_path / "out"
    sitefiles.build(strict, out, pipeline_version="x", generated_at=GENERATED)
    meta = json.loads((out / "site" / "meta.json").read_text())
    system = json.loads((out / "site" / "system.json").read_text())
    assert meta["min_sample"] == 10
    assert system["notices"][0]["code"] != "provisional"
    assert system["trips"]["observed"] <= load(world, "system.json")["trips"]["observed"]


def test_unused_date_is_not_published(world):
    assert date(2026, 10, 5).isoformat() not in json.dumps(load(world, "quality.json"))


def test_csv_headers_match_the_site_sample(world):
    """The sample the site is built and tested on uses exactly the pipeline's columns."""
    from wbot_site.build import SAMPLE_DIR
    settings, _ = world
    ours = {p.name: p.read_text(encoding="utf-8").partition("\n")[0] for p in (settings.out_dir / "csv").glob("*.csv")}
    sample = {p.name: p.read_text(encoding="utf-8").partition("\n")[0] for p in (SAMPLE_DIR / "csv").glob("*.csv")}
    assert ours == sample
