"""End to end on simulated data: the pipeline's output must satisfy the site's own loader
and build, and the files must be laid out as the site expects. The numbers themselves are
tested through the Stage (test_stage.py)."""

import csv
import json
from datetime import date, timedelta

import pytest
from conftest import DAY, after
from synth import Feed, SimAlert, TripPlan, default_trips, write_gtfs

from wbot_pipeline import pipeline, sitefiles, timeutil
from wbot_pipeline.config import load_settings
from wbot_site import data as site_data
from wbot_site.build import build as site_build
from wbot_site.contract import ContractError

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
    o2 = timeutil.origin(DAY2)
    f2.alerts = [SimAlert("d1", ["R901"], (o2 + 7 * 3600, o2 + 8 * 3600), header="Route 901 on detour",
                          description="Example St is closed between 1st and 3rd Ave.")]
    f2.write()
    result = pipeline.run(settings, now=after(DAY2), do_publish=False, generated_at=GENERATED)
    return settings, result


def load(world, *parts):
    settings, _ = world
    return json.loads(settings.out_dir.joinpath("site", *parts).read_text(encoding="utf-8"))


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
    assert meta["windows"] == {"headline": [-60, 300], "alt": [0, 300]}
    assert len(meta["inputs_sha256"]) == 64


def test_system_file(world):
    system = load(world, "system.json")
    assert set(system["scopes"]) == {"timepoints", "all_stops"}
    assert [r["slug"] for r in system["routes"]] == ["901", "902"]
    assert system["end_of_line"]["n"] > 0 and system["trips"]["cancelled"] == 1


def test_notices_and_quality(world):
    system = load(world, "system.json")
    codes = [n["code"] for n in system["notices"]]
    assert codes[0] == "provisional" and "data_loss" in codes and "methodology_change" in codes
    quality = load(world, "quality.json")
    assert [d["date"] for d in quality["days"]] == [DAY.isoformat(), DAY2.isoformat()]
    (loss,) = quality["data_loss"]
    assert loss["cause"] == "collector" and loss["start"].endswith("Z")
    assert quality["days"][0]["uptime_service_hours"] < 1.0 == quality["days"][1]["uptime_service_hours"]
    route_codes = [n["code"] for n in load(world, "routes", "901.json")["notices"]]
    assert route_codes == ["provisional", "data_loss", "detour"]


def test_detours(world):
    settings, _ = world
    (notice,) = [n for n in load(world, "routes", "901.json")["notices"] if n["code"] == "detour"]
    assert notice["header"] == "Route 901 on detour" and notice["description"].startswith("Example St")
    assert notice["trips"] > 0 and notice["start"].startswith(DAY2.isoformat())
    assert not [n for n in load(world, "routes", "902.json")["notices"] if n["code"] == "detour"]
    stops_901 = {s["code"] for s in load(world, "stops.json")["stops"] if "901" in s["routes"]}
    for s in load(world, "stops.json")["stops"]:
        codes = [(n["code"], n.get("route")) for n in load(world, "stops", f"{s['code']}.json")["notices"]]
        assert codes == ([("detour", "901")] if s["code"] in stops_901 else [])
    (row,) = load(world, "quality.json")["detours"]
    assert (row["route"], row["trips"]) == ("901", notice["trips"])
    with open(settings.out_dir / "csv" / "detours.csv", encoding="utf-8") as f:
        (csv_row,) = list(csv.DictReader(f))
    assert (csv_row["alert_id"], csv_row["route"], int(csv_row["trips"])) == ("d1", "901", notice["trips"])
    with open(settings.out_dir / "csv" / "routes_daily.csv", encoding="utf-8") as f:
        daily = [r for r in csv.DictReader(f) if r["scope"] == "timepoints"]
    assert sum(int(r["detour_trips"]) for r in daily if r["date"] == DAY2.isoformat()) == notice["trips"]
    assert all(r["detour_trips"] == "0" for r in daily if r["date"] == DAY.isoformat() or r["route"] != "901")


def test_route_and_stop_pages(world):
    route = load(world, "routes", "902.json")
    (loop,) = route["stops"]
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


def test_map_presets_and_route_lines(world):
    index = load(world, "stops.json")["stops"]
    total = load(world, "map", "all", "all-all.json")
    assert len(total["all_stops"]) == len(total["timepoints"]) == len(index)
    assert all(len(cell) == 5 for cell in total["all_stops"])
    geo = load(world, "routes.geojson")
    keys = {(f["properties"]["slug"], f["properties"]["direction_id"]) for f in geo["features"]}
    assert keys == {("901", 0), ("901", 1), ("902", 0)}
    line = next(f for f in geo["features"] if f["properties"]["slug"] == "901")["geometry"]["coordinates"]
    assert line[0] == [-122.9, 47.04] and len(line) == 2     # straight line: inner points simplified away


def test_page_presets(world):
    settings, _ = world
    presets = settings.out_dir / "site" / "presets"
    files = sorted(p.relative_to(presets).as_posix() for p in presets.rglob("*.json"))
    # Weekday trips at 7 and 8 am and one after midnight (evening); none on other day types or bands.
    # Route 902 has no evening trip, so its evening presets have no file.
    assert files == [f"{name}/{f}" for name, routes in (
        ("all-am_peak", ["901", "902"]), ("all-evening", ["901"]), ("weekday-all", ["901", "902"]),
        ("weekday-am_peak", ["901", "902"]), ("weekday-evening", ["901"]))
        for f in [*(f"routes/{r}.json" for r in routes), "system.json"]]
    evening = load(world, "presets", "weekday-evening", "system.json")
    assert evening["preset"] == {"daytype": "weekday", "band": "evening"}
    assert [(r["slug"], r["scheduled"]["all_stops"]) for r in evening["routes"]] == [("901", 8)]   # 4 stops, 2 days
    route = load(world, "presets", "weekday-am_peak", "routes", "901.json")
    codes = [[s["code"] for s in d["stops"]] for d in route["stops"]]
    assert codes == [[s["code"] for s in d["stops"]] for d in load(world, "routes", "901.json")["stops"]]
    assert "completeness" not in route and "notices" not in route


def test_page_presets_add_up_to_the_route_file(world):
    base = load(world, "routes", "901.json")

    def total(names, key):
        return sum(key(load(world, "presets", name, "routes", "901.json")) for name in names)

    def n(doc):
        return doc["scopes"]["all_stops"]["summary"]["n"]

    def scheduled(doc, scope="all_stops"):
        return doc["scheduled"][scope]

    assert total(["all-am_peak", "all-evening"], n) == n(base)
    assert total(["all-am_peak", "all-evening"], lambda d: scheduled(d, "timepoints")) == \
        base["completeness"]["departures_scheduled"]
    weekday = load(world, "presets", "weekday-all", "routes", "901.json")
    assert total(["weekday-am_peak", "weekday-evening"], scheduled) == scheduled(weekday)


def test_csv_columns(world):
    settings, _ = world
    csv_dir = settings.out_dir / "csv"
    names = sorted(p.name for p in csv_dir.glob("*.csv"))
    assert names == sorted(f"{n}.csv" for n in (
        "system_daily", "routes_daily", "routes_monthly", "routes_hourly_monthly", "stops_monthly",
        "terminal_monthly", "end_of_line_monthly", "quality_daily", "data_loss", "detours", "patterns"))
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


def tree(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_output_is_deterministic(world, tmp_path):
    settings, _ = world
    before = tree(settings.out_dir)
    sitefiles.build(settings, settings.out_dir, pipeline_version=load(world, "meta.json")["pipeline_version"],
                    generated_at=GENERATED)
    assert tree(settings.out_dir) == before


def test_output_that_breaks_the_contract_leaves_the_last_good_output(world, monkeypatch):
    settings, _ = world
    before = tree(settings.out_dir)

    def broken(stats_dir):
        raise ContractError("system.json: missing scopes")

    monkeypatch.setattr(sitefiles.contract, "validate", broken)
    with pytest.raises(ContractError):
        sitefiles.build(settings, settings.out_dir, pipeline_version="x", generated_at="2099-01-01T00:00:00Z")
    assert tree(settings.out_dir) == before


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
