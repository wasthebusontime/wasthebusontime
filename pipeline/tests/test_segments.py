"""Segments (GLOSSARY.md): per trip, consecutive timepoints A to B, the last stop counting as an
end, and the delay gained between them: the arrival delay at B minus the departure delay at A."""

import duckdb
import pytest
from conftest import DAY
from synth import Feed, Trip, TripPlan, write_gtfs

from wbot_pipeline import events, static, timeutil
from wbot_pipeline.config import EVENTS_VERSION

LINE = ["S1", "S2", "S3", "S4", "S5"]   # 4 minutes apart: a trip takes 16 minutes


def t(trip_id, start_min, block="", timepoints=(0, 2, 4), hold_at=None, stops=LINE, route="R901"):
    return Trip(trip_id, route, 0, "WK", start_min, stops, set(timepoints), hold_at or {}, "", "SH901-0",
                block_id=block)


TRIPS = [
    t("a1", 7 * 60, "B1"), Trip("n1", "R901", 1, "WK", 7 * 60 + 20, LINE[::-1], {0, 2, 4}, {}, "", "SH901-1",
                                block_id="B1"),                  # n1 gives a1's last-stop arrival
    t("g1", 8 * 60),                                             # last-stop arrival from GPS
    t("u1", 9 * 60),                                             # no data at the middle timepoint
    t("u2", 10 * 60),                                            # middle timepoint skipped
    t("u3", 11 * 60),                                            # never tracked
    t("u4", 12 * 60),                                            # cancelled
    t("u5", 13 * 60),                                            # no GPS: last stop not recorded
    t("h1", 14 * 60, hold_at={2: 2}),                            # 2 min hold at the middle timepoint
    t("e1", 15 * 60, timepoints=(0, 2)),                         # last stop is not a timepoint
    t("m1", 16 * 60, timepoints=(0, 1, 2, 4)),                   # consecutive timepoints
]


@pytest.fixture
def segs(settings):
    write_gtfs(settings.static_dir, "test1_aaaaaaaa", TRIPS)
    feed = Feed(settings.data_dir, DAY, TRIPS)
    feed.last_stop_in_feed = False
    feed.plans["a1"] = TripPlan(delays=[0, 30, 90, 120, 200])
    feed.plans["g1"] = TripPlan(delays=[60, 60, 150, 200, 240])
    feed.plans["u1"] = TripPlan(no_data={2})
    feed.plans["u2"] = TripPlan(skip={2})
    feed.plans["u3"] = TripPlan(vehicle=False)
    feed.plans["u4"] = TripPlan(cancel=True)
    feed.plans["u5"] = TripPlan(gps=False)
    feed.plans["h1"] = TripPlan(delays=[0, 0, 200, 100, 100])
    feed.plans["m1"] = TripPlan(delays=[0, 40, 100, 100, 100])
    feed.write()
    events.process_date(settings, static.list_versions(settings.static_dir), DAY, "test")
    p = events.paths(settings, DAY)
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{p.segments.as_posix()}') ORDER BY trip_id, segment")
    names = [c[0] for c in cur.description]
    out = {}
    for r in cur.fetchall():
        row = dict(zip(names, r))
        out.setdefault(row["trip_id"], []).append(row)
    cur = con.execute(f"SELECT * FROM read_parquet('{p.events.as_posix()}')")
    names = [c[0] for c in cur.description]
    ev = {(r["trip_id"], r["stop_code"]): r for r in (dict(zip(names, x)) for x in cur.fetchall())}
    return out, ev


def pairs(rows):
    return [(r["from_stop_code"], r["to_stop_code"]) for r in rows]


def test_consecutive_timepoints_pair_up(segs):
    s, _ = segs
    assert pairs(s["a1"]) == [("E201", "E203"), ("E203", "E205")]
    assert [r["segment"] for r in s["a1"]] == [0, 1]
    assert [r["stops_between"] for r in s["a1"]] == [1, 1]
    assert [r["to_last"] for r in s["a1"]] == [False, True]
    assert pairs(s["m1"]) == [("E201", "E202"), ("E202", "E203"), ("E203", "E205")]
    assert [r["stops_between"] for r in s["m1"]] == [0, 0, 1]
    r = s["a1"][0]
    assert (r["route_slug"], r["direction_id"], r["from_stop_id"], r["to_stop_id"]) == ("901", 0, "S1", "S3")
    assert (r["from_seq"], r["to_seq"]) == (0, 20)
    assert r["day_type"] == "weekday" and r["service_hour"] == 7
    assert r["events_version"] == EVENTS_VERSION
    # The other direction's trip is its own segments.
    assert pairs(s["n1"]) == [("E205", "E203"), ("E203", "E201")] and s["n1"][0]["direction_id"] == 1


def test_delay_gained_is_arrival_at_b_minus_departure_at_a(segs):
    s, ev = segs
    r = s["a1"][0]
    # The bus left S1 on time and reached S3 70 s late (the simulator arrives 20 s before it leaves).
    assert (r["from_delay_dep_s"], r["to_delay_arr_s"], r["gain_s"]) == (0, 70, 70)
    assert r["to_method"] == "tu"
    assert r["from_delay_dep_s"] == ev[("a1", "E201")]["delay_dep_s"]
    assert r["to_delay_arr_s"] == ev[("a1", "E203")]["delay_arr_s"]
    o = timeutil.origin(DAY)
    assert (r["sched_dep_ts"], r["sched_arr_ts"], r["sched_run_s"]) == (o + 7 * 3600, o + 7 * 3600 + 8 * 60, 480)
    # Equivalently: the time it took, less the time scheduled.
    assert r["gain_s"] == (ev[("a1", "E203")]["act_arr_ts"] - ev[("a1", "E201")]["act_dep_ts"]) - r["sched_run_s"]
    g = s["g1"][0]
    assert (g["from_delay_dep_s"], g["to_delay_arr_s"], g["gain_s"]) == (60, 130, 70)


def test_last_stop_from_the_block(segs):
    s, ev = segs
    r = s["a1"][1]
    last = ev[("a1", "E205")]
    assert last["method"] == "block" and r["to_method"] == "block"
    assert r["from_delay_dep_s"] == 90
    assert r["to_delay_arr_s"] == last["delay_arr_s"]
    assert r["gain_s"] == last["delay_arr_s"] - 90


def test_last_stop_from_gps(segs):
    s, ev = segs
    r = s["g1"][1]
    last = ev[("g1", "E205")]
    assert last["method"] == "gps" and r["to_method"] == "gps"
    assert r["gain_s"] == last["delay_arr_s"] - 150


def test_last_stop_counts_as_an_end_when_not_a_timepoint(segs):
    s, _ = segs
    assert pairs(s["e1"]) == [("E201", "E203"), ("E203", "E205")]
    assert s["e1"][1]["to_last"] and s["e1"][1]["gain_s"] is not None


@pytest.mark.parametrize("trip_id", ["u1", "u2"])
def test_unobserved_middle_end(segs, trip_id):
    # No data or skipped at S3: neither segment touching it has a delay gained.
    s, _ = segs
    rows = s[trip_id]
    assert pairs(rows) == [("E201", "E203"), ("E203", "E205")]
    assert rows[0]["from_delay_dep_s"] is not None and rows[0]["to_delay_arr_s"] is None
    assert rows[1]["from_delay_dep_s"] is None
    assert all(r["gain_s"] is None for r in rows)


@pytest.mark.parametrize("trip_id", ["u3", "u4"])
def test_untracked_or_cancelled_trip(segs, trip_id):
    s, _ = segs
    assert len(s[trip_id]) == 2
    assert all(r["gain_s"] is None and r["from_delay_dep_s"] is None and r["to_delay_arr_s"] is None
               for r in s[trip_id])


def test_last_stop_not_recorded(segs):
    s, _ = segs
    a, b = s["u5"]
    assert a["gain_s"] == -20                # 30 s late throughout, arriving 20 s before leaving
    assert b["from_delay_dep_s"] == 30 and b["to_delay_arr_s"] is None and b["gain_s"] is None
    assert b["to_method"] is None


def test_a_hold_at_a_timepoint_is_not_running_time(segs):
    # h1 is scheduled to wait 2 minutes at S3. Arriving at S3 is measured against the scheduled
    # arrival, leaving against the scheduled departure, so the wait is in neither segment.
    s, ev = segs
    a, b = s["h1"]
    o = timeutil.origin(DAY)
    assert a["sched_run_s"] == 480 and b["sched_run_s"] == 480
    assert b["sched_dep_ts"] == o + 14 * 3600 + 10 * 60
    assert (a["from_delay_dep_s"], a["to_delay_arr_s"], a["gain_s"]) == (0, 180, 180)
    assert b["from_delay_dep_s"] == 200


def test_every_trip_has_rows(segs):
    s, _ = segs
    assert set(s) == {tr.trip_id for tr in TRIPS}
