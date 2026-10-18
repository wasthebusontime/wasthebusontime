"""End-of-line arrivals: from the next trip in the block, else from GPS, else not recorded."""

import duckdb
import pytest
from conftest import DAY
from synth import Feed, Trip, TripPlan, write_gtfs

from wbot_pipeline import events, rules, static, timeutil

LINE = ["S1", "S2", "S3", "S4", "S5"]   # 4 minutes apart: a trip takes 16 minutes


def t(trip_id, direction, start_min, block):
    stops = LINE if direction == 0 else LINE[::-1]
    return Trip(trip_id, "R901", direction, "WK", start_min, stops, {0, 2, 4}, {}, "", f"SH901-{direction}",
                block_id=block)


TRIPS = [
    t("a1", 0, 7 * 60, "B1"), t("b1", 1, 7 * 60 + 20, "B1"),        # 4 min layover
    t("a2", 0, 8 * 60, "B2"), t("b2", 1, 8 * 60 + 30, "B2"),        # 14 min layover
    t("a3", 0, 9 * 60, "B3"), t("b3", 1, 9 * 60 + 20, "B3"),        # another bus on b3
    t("a4", 0, 10 * 60, "B4"), t("b4", 1, 10 * 60 + 20, "B4"),      # b4 never tracked
    t("a5", 0, 11 * 60, "B5"),                                      # no GPS
    t("a6", 0, 12 * 60, "B6"),                                      # GPS gap at the end
]


@pytest.fixture
def last_stops(settings):
    write_gtfs(settings.static_dir, "test1_aaaaaaaa", TRIPS)
    feed = Feed(settings.data_dir, DAY, TRIPS)
    feed.last_stop_in_feed = False
    feed.plans["b3"] = TripPlan(vehicle_id="VX3")
    feed.plans["b4"] = TripPlan(vehicle=False)
    feed.plans["a5"] = TripPlan(gps=False)
    o = timeutil.origin(DAY)
    feed.vp_gaps = [(o + 12 * 3600 + 13 * 60, o + 12 * 3600 + 17 * 60)]
    feed.write()
    events.process_date(settings, static.list_versions(settings.static_dir), DAY, "test")
    path = events.paths(settings, DAY).events.as_posix()
    cur = duckdb.connect().execute(f"SELECT * FROM read_parquet('{path}')")
    names = [c[0] for c in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    simulated = {tr.trip_id: feed.actual(tr) for tr in TRIPS}
    counted = duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{path}') WHERE {rules.END_OF_LINE}").fetchone()[0]
    return ({r["trip_id"]: r for r in rows if r["is_last"]}, {r["trip_id"]: r for r in rows if r["is_first"]}, simulated,
            counted)


def test_next_trip_in_the_block_gives_the_arrival(last_stops):
    last, first, _, _ = last_stops
    a1 = last["a1"]
    assert a1["method"] == "block" and a1["observed_arr"]
    assert a1["act_arr_ts"] == first["b1"]["act_arr_ts"]
    assert a1["delay_arr_s"] == a1["act_arr_ts"] - a1["sched_arr_ts"]


@pytest.mark.parametrize("trip_id", ["b1", "a2", "a3", "a4", "b2", "b3"])
def test_gps_when_the_block_cant_be_used(last_stops, trip_id):
    # b1, b2, b3: last of their block; a2: 14 min layover; a3: another bus ran b3; a4: b4 untracked.
    last, _, simulated, _ = last_stops
    row = last[trip_id]
    assert row["method"] == "gps" and row["observed_arr"]
    # The GPS arrival is when the bus is within 40 m: a little before it stops (30 s reports).
    assert -60 <= row["act_arr_ts"] - simulated[trip_id][-1][2] <= 15


@pytest.mark.parametrize("trip_id", ["b4", "a5", "a6"])
def test_otherwise_not_recorded(last_stops, trip_id):
    # b4: never tracked; a5: no GPS; a6: GPS reports stop before it arrives.
    last, _, _, _ = last_stops
    row = last[trip_id]
    assert not row["observed_arr"] and row["act_arr_ts"] is None and row["method"] == "tu"


def test_found_arrivals_count_in_the_numbers(last_stops):
    last, _, _, counted = last_stops
    assert counted == sum(r["observed_arr"] for r in last.values()) == 7
