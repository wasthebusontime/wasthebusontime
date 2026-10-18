import duckdb
import pytest
from conftest import DAY
from synth import TripPlan

from wbot_pipeline import dateplan, events, static, timeutil
from wbot_pipeline.config import EVENTS_VERSION, MIN_SEEN_AFTER_DEP_S


def run(settings):
    versions = static.list_versions(settings.static_dir)
    return events.process_date(settings, versions, DAY, "test")


def rows(settings, where="TRUE"):
    path = events.paths(settings, DAY).events
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}') WHERE {where} ORDER BY trip_id, stop_sequence")
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def trip_rows(settings):
    path = events.paths(settings, DAY).trips
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY trip_id")
    names = [c[0] for c in cur.description]
    return {r[1]: dict(zip(names, r)) for r in cur.fetchall()}


def test_delays_are_read_from_retained_past_stops(settings, feed):
    feed.plans["901-0-0-v1"] = TripPlan(delays=[-20, 0, 45, 301, 120])
    feed.write()
    assert run(settings) == "ok"
    r = rows(settings, "trip_id = '901-0-0-v1'")
    assert [x["delay_dep_s"] for x in r] == [-20, 0, 45, 301, 120]
    # The last stop has no departure to observe (the vehicle drops at arrival); its arrival is.
    assert [x["observed"] for x in r] == [True, True, True, True, False]
    assert [x["is_first"] for x in r] == [True, False, False, False, False]
    assert [x["is_last"] for x in r] == [False, False, False, False, True]
    assert [x["is_timepoint"] for x in r] == [True, False, True, False, True]
    assert [x["stop_sequence"] for x in r] == [0, 10, 20, 30, 40]
    assert {x["route_slug"] for x in r} == {"901"}
    assert r[0]["stop_code"] == "E201"
    assert r[0]["service_hour"] == 7 and r[0]["day_type"] == "weekday"
    assert r[0]["static_version"] == "test1_aaaaaaaa" and r[0]["events_version"] == EVENTS_VERSION
    # The hold at the third stop: departure is compared with the scheduled departure, not arrival.
    o = timeutil.origin(DAY)
    assert r[2]["sched_dep_ts"] - r[2]["sched_arr_ts"] == 60
    assert r[2]["act_dep_ts"] == o + 7 * 3600 + 9 * 60 + 45
    assert r[4]["observed_arr"] and r[4]["delay_arr_s"] is not None


def test_observed_rule(settings, feed):
    feed.plans["901-0-1-v1"] = TripPlan(vehicle=False)        # never had a vehicle
    feed.plans["901-0-2-v1"] = TripPlan(frozen_ts=True)       # AVL stopped updating before departures
    feed.write()
    run(settings)
    assert not any(x["observed"] for x in rows(settings, "trip_id = '901-0-1-v1'"))
    assert all(x["seen"] for x in rows(settings, "trip_id = '901-0-1-v1'"))
    assert not any(x["observed"] for x in rows(settings, "trip_id = '901-0-2-v1'"))
    assert all(x["observed"] for x in rows(settings, "trip_id = '901-0-3-v1' AND NOT is_last"))


def test_departure_seen_too_briefly_after_leaving_is_not_observed(settings, feed):
    # Left the first stop at 07:00:00; collection stopped at 07:01:00 and resumed after the
    # stop had dropped out of the feed, so its time may not have settled.
    o = timeutil.origin(DAY)
    feed.plans["901-0-0-v1"] = TripPlan(delays=[0, 0, 0, 0, 0])
    feed.gaps = [(o + 7 * 3600 + 61, o + 7 * 3600 + 30 * 60)]
    feed.write()
    run(settings)
    (first,) = rows(settings, "trip_id = '901-0-0-v1' AND is_first")
    assert first["seen_after_dep_s"] < MIN_SEEN_AFTER_DEP_S
    assert first["delay_dep_s"] == 0 and not first["observed"]


def test_long_gap_loses_departures_and_short_gap_loses_none(settings, feed):
    o = timeutil.origin(DAY)
    # Collector down 08:20 to 08:40 (20 min) and 07:20 to 07:30 (10 min).
    feed.gaps = [(o + 8 * 3600 + 20 * 60, o + 8 * 3600 + 40 * 60), (o + 7 * 3600 + 20 * 60, o + 7 * 3600 + 30 * 60)]
    feed.write()
    run(settings)
    lost = rows(settings, f"scheduled AND NOT observed AND sched_dep_ts BETWEEN {o + 7 * 3600} AND {o + 10 * 3600}"
                          " AND NOT is_last")
    # A departure is lost if fewer than MIN_SEEN_AFTER_DEP_S of snapshots came after it before
    # the stop dropped out (here after 08:16:30 to 08:25:15; the last snapshot before the
    # long gap is 08:19:30), or if its trip ended, and so lost its vehicle, before collection
    # resumed. The second case also applies to gaps under 13 minutes; accepted at the 2026-10
    # re-validation.
    gap_list = feed.gaps
    trip_end = {}
    for x in rows(settings, "scheduled"):
        trip_end[x["trip_id"]] = max(trip_end.get(x["trip_id"], 0), x["sched_dep_ts"] + 30)
    last_before = o + 8 * 3600 + 19 * 60 + 30
    for x in lost:
        dep = x["sched_dep_ts"] + 30
        in_long_gap = last_before - MIN_SEEN_AFTER_DEP_S < dep <= o + 8 * 3600 + 25 * 60 + 15
        trip_ended_in_gap = any(a - 30 <= dep and a <= trip_end[x["trip_id"]] < b for a, b in gap_list)
        assert in_long_gap or trip_ended_in_gap, x
    assert any(o + 8 * 3600 + 19 * 60 + 30 <= x["sched_dep_ts"] + 30 <= o + 8 * 3600 + 25 * 60 + 15 for x in lost)
    m = events.read_manifest(settings, DAY)
    lengths = sorted(g["end"] - g["start"] for g in m["gaps"])
    assert lengths == [600, 1200] and {g["cause"] for g in m["gaps"]} == {"collector"}


def test_skipped_no_data_cancelled_added_and_unmatched(settings, feed):
    feed.plans["901-0-0-v1"] = TripPlan(skip={1, 3})
    feed.plans["901-1-0-v1"] = TripPlan(no_data={2})
    feed.plans["901-1-1-v1"] = TripPlan(cancel=True)
    o = timeutil.origin(DAY)
    feed.added = [("EXTRA-1", [(0, "S1", o + 9 * 3600), (10, "S2", o + 9 * 3600 + 240)])]
    feed.write()
    run(settings)
    r = rows(settings, "trip_id = '901-0-0-v1'")
    assert [x["stop_sr"] for x in r] == ["SCHEDULED", "SKIPPED", "SCHEDULED", "SKIPPED", "SCHEDULED"]
    assert rows(settings, "trip_id = '901-1-0-v1' AND stop_sequence = 20")[0]["delay_dep_s"] is None
    t = trip_rows(settings)
    assert t["901-0-0-v1"]["skipped_stops"] == 2
    assert t["901-1-0-v1"]["no_data_stops"] == 1
    assert t["901-1-1-v1"]["trip_sr"] == "CANCELED" and t["901-1-1-v1"]["observed_timepoints"] == 0
    assert t["EXTRA-1"]["trip_sr"] == "ADDED" and not t["EXTRA-1"]["scheduled"]
    assert t["901-0-0-v1"]["eligible_timepoints"] == 2 and t["901-0-0-v1"]["observed_timepoints"] == 2
    extra = rows(settings, "trip_id = 'EXTRA-1'")
    assert len(extra) == 2 and not any(x["scheduled"] for x in extra) and extra[0]["route_slug"] == "901"
    # The Saturday trip isn't scheduled on a Tuesday; the late trip is (it runs after midnight).
    assert "901-0-sat-v1" not in t and t["901-0-late-v1"]["scheduled"]
    assert events.read_manifest(settings, DAY)["counts"]["unmatched_trips"] == 0


def test_loop_route_keys_repeated_stops_by_sequence(settings, feed):
    feed.plans["902-0-0-v1"] = TripPlan(delays=[0, 10, 20, 30, 40])
    feed.write()
    run(settings)
    r = rows(settings, "trip_id = '902-0-0-v1'")
    assert [x["stop_id"] for x in r] == ["S5", "S6", "S7", "S6", "S5"]
    assert [x["delay_dep_s"] for x in r] == [0, 10, 20, 30, 40]
    assert r[2]["stop_code"] == "S7"   # no public code: the stop_id is used


def test_late_pack_and_unreadable_snapshot(settings, feed):
    o = timeutil.origin(DAY)
    hour = o + 7 * 3600
    feed.plans["901-0-0-v1"] = TripPlan(delays=[5, 6, 7, 8, 9])
    feed.corrupt = {hour + 600}
    feed.write(late_hours={hour})
    run(settings)
    m = events.read_manifest(settings, DAY)
    assert any(p["path"].endswith("T14.1.tar.zst") for p in m["packs"])
    assert m["unreadable_snapshots"] == 1
    assert [x["delay_dep_s"] for x in rows(settings, "trip_id = '901-0-0-v1'")] == [5, 6, 7, 8, 9]


def test_hours_read_cover_the_service_span(settings, network):
    versions = static.list_versions(settings.static_dir)
    hours = events.hours_read(settings, versions, DAY)
    run(settings)
    span = events.read_manifest(settings, DAY)["service_span"]
    assert hours[0] <= span[0] - events.READ_BEFORE_S and hours[-1] + 3600 > span[1] + events.READ_AFTER_S
    assert [h % 3600 for h in hours] == [0] * len(hours) and hours == list(range(hours[0], hours[-1] + 1, 3600))
    assert events.hours_read(settings, versions, DAY.replace(day=11)) is None    # a Sunday: no service
    assert events.hours_read(settings, [], DAY) is None


def test_day_lists_the_packs_now_archived(settings, feed):
    versions = static.list_versions(settings.static_dir)
    assert events.day(settings, versions, DAY) == dateplan.Day(DAY, None, "test1_aaaaaaaa", [])
    feed.write()
    run(settings)
    day = events.day(settings, versions, DAY)
    assert day.packs == [{"path": p["path"], "size": p["size"]} for p in day.manifest["packs"]]
    # A late pack turns up (for example restored from backup).
    first = settings.data_dir / day.packs[0]["path"]
    late = first.with_name(first.name.replace(".tar.zst", ".1.tar.zst"))
    late.write_bytes(first.read_bytes())
    assert {"path": late.relative_to(settings.data_dir).as_posix(), "size": late.stat().st_size} in         events.day(settings, versions, DAY).packs


@pytest.mark.parametrize("weekday_ok", [True])
def test_no_service_day(settings, network, weekday_ok):
    versions = static.list_versions(settings.static_dir)
    sunday = DAY.replace(day=11)
    assert events.process_date(settings, versions, sunday, "test") == "no_service"
