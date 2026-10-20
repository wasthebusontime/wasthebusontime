"""The Stage's questions, on small in-memory stop event and trip tables."""

from datetime import date, timedelta

import duckdb
import pytest

from wbot_pipeline import timeutil
from wbot_pipeline.config import load_settings
from wbot_pipeline.perf import reference
from wbot_pipeline.stage import Period, Stage

DAY = date(2026, 10, 6)   # a Tuesday
DAY2 = DAY + timedelta(days=1)

EV = dict(service_date="DATE", trip_id="VARCHAR", route_slug="VARCHAR", direction_id="INTEGER", stop_code="VARCHAR",
          stop_sequence="INTEGER", day_type="VARCHAR", service_hour="INTEGER", is_first="BOOLEAN", is_last="BOOLEAN",
          is_timepoint="BOOLEAN", delay_dep_s="INTEGER", delay_arr_s="INTEGER", observed="BOOLEAN",
          observed_arr="BOOLEAN", scheduled="BOOLEAN", stop_sr="VARCHAR", trip_sr="VARCHAR", method="VARCHAR",
          sched_dep_ts="BIGINT")
TR = dict(service_date="DATE", trip_id="VARCHAR", route_id="VARCHAR", route_slug="VARCHAR", direction_id="INTEGER",
          scheduled="BOOLEAN", trip_sr="VARCHAR",
          eligible_timepoints="INTEGER", observed_timepoints="INTEGER", skipped_stops="INTEGER",
          start_ts="BIGINT", end_ts="BIGINT", prev_trip_id="VARCHAR", carry_over="BOOLEAN")


def trip(trip_id, route, stops, delays, *, direction=0, day=DAY, hour=8, timepoints=None, trip_sr="SCHEDULED",
         observed=True, stop_sr=None, scheduled=True, eol_method="block"):
    """Stop event rows and the trip row for one trip. delays: departure delay at each stop,
    and the arrival delay at the last one. timepoints: stop indexes (default all). stop_sr: {index: status}."""
    timepoints = set(range(len(stops))) if timepoints is None else timepoints
    stop_sr = stop_sr or {}
    last = len(stops) - 1
    start = int((day - date(2026, 1, 1)).total_seconds()) + hour * 3600
    events = []
    for i, (code, delay) in enumerate(zip(stops, delays)):
        events.append(dict(
            service_date=day, trip_id=trip_id, route_slug=route, direction_id=direction, stop_code=code,
            stop_sequence=i + 1, day_type="weekday" if day.weekday() < 5 else ("saturday" if day.weekday() == 5 else "sunday"),
            service_hour=hour, is_first=i == 0, is_last=i == last, is_timepoint=i in timepoints,
            delay_dep_s=None if i == last else delay, delay_arr_s=delay if i == last else None,
            observed=observed, observed_arr=observed, scheduled=scheduled,
            stop_sr=stop_sr.get(i, "SCHEDULED"), trip_sr=trip_sr, method=eol_method if i == last else "tu",
            sched_dep_ts=None if i == last else start + 60 * i))
    timed = [e for e in events if e["is_timepoint"] and not e["is_last"]]
    row = dict(service_date=day, trip_id=trip_id, route_id="R" + route, route_slug=route, direction_id=direction,
               scheduled=scheduled, trip_sr=trip_sr,
               eligible_timepoints=len(timed),
               observed_timepoints=sum(1 for e in timed if e["observed"] and e["stop_sr"] == "SCHEDULED"),
               skipped_stops=sum(1 for e in events if e["stop_sr"] == "SKIPPED"), start_ts=start, end_ts=start + 1800,
               prev_trip_id=None, carry_over=None)
    return events, row


AL = dict(alert_id="VARCHAR", effect="VARCHAR", routes="VARCHAR[]", periods="STRUCT(start BIGINT, \"end\" BIGINT)[]",
          header="VARCHAR", description="VARCHAR", first_seen="BIGINT", last_seen="BIGINT")


def alert(alert_id, routes, first_seen, last_seen, periods=(), effect="DETOUR", header="", description=""):
    return dict(alert_id=alert_id, effect=effect, routes=routes, periods=[{"start": a, "end": b} for a, b in periods],
                header=header, description=description, first_seen=first_seen, last_seen=last_seen)


def make_stage(tmp_path, *trips, days=(DAY, DAY2), lost_s=0, alerts=(), extra_routes=None, **settings):
    con = duckdb.connect()
    for name, cols, rows in (("ev", EV, [e for evs, _ in trips for e in evs]), ("tr", TR, [t for _, t in trips]),
                             ("al", AL, list(alerts))):
        con.execute(f"CREATE TABLE {name} ({', '.join(f'{k} {v}' for k, v in cols.items())})")
        if rows:
            con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in cols)})",
                            [[r[k] for k in cols] for r in rows])
    s = load_settings({}, data_dir=tmp_path / "data", derived_dir=tmp_path / "derived", first_service_date=days[0],
                      **settings)
    manifests = [{"service_date": d.isoformat(), "service_s": 36000, "lost_service_s": lost_s if i == 0 else 0}
                 for i, d in enumerate(days)]
    return Stage(s, Period(days[0], days[-1], manifests), con, extra_routes=extra_routes or {})


A = ["E1", "E2", "E3", "E4"]


def test_perf_blocks_match_a_recount(tmp_path):
    trips = [trip("t1", "901", A, [-20, 0, 45, 99]), trip("t2", "901", A, [400, 10, 70, -5], timepoints={0, 3}),
             trip("t3", "902", A, [-90, 30, 30, 0], day=DAY2)]
    stage = make_stage(tmp_path, *trips)
    blocks = stage.perf_blocks(None, daily=True)
    assert blocks[("all_stops", None)]["summary"] == reference([-20, 0, 45, 400, 10, 70, -90, 30, 30])
    assert blocks[("timepoints", None)]["summary"] == reference([-20, 0, 45, 400, -90, 30, 30])
    assert stage.perf([], terminal=True, hist=True)[()] == reference([-20, 400, -90])
    daily = blocks[("timepoints", None)]["daily"]
    assert [d["date"] for d in daily] == [DAY.isoformat(), DAY2.isoformat()]
    assert [d["n"] for d in daily] == [4, 3] and "p50" not in daily[0] and "hist" not in daily[0]
    assert blocks[("timepoints", None)]["monthly"][0]["partial"] is True
    assert set(stage.perf_blocks("route", daily=False)) == {(s, r) for s in ("timepoints", "all_stops") for r in ("901", "902")}


def test_only_departures_that_count_are_counted(tmp_path):
    trips = [trip("ok", "901", A, [0, 0, 0, 0]),
             trip("skip", "901", A, [0, 0, 0, 0], stop_sr={1: "SKIPPED", 2: "NO_DATA"}),
             trip("cancel", "901", A, [0, 0, 0, 0], trip_sr="CANCELED"),
             trip("unseen", "901", A, [0, 0, 0, 0], observed=False),
             trip("added", "901", A, [0, 0, 0, 0], trip_sr="ADDED", scheduled=False)]
    assert make_stage(tmp_path, *trips).perf(["scope"])[("all_stops",)]["n"] == 3 + 1


def test_window_boundaries_by_direction(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", ["E1", "E2", "E3", "E4", "E5"], [-61, -60, -1, 300, 0], direction=1))
    p = stage.perf(["scope", "route", "direction_id"])[("all_stops", "901", 1)]
    assert (p["early"], p["on_time"], p["late"]) == (1, 3, 0)
    assert (p["early_alt"], p["on_time_alt"], p["late_alt"]) == (3, 1, 0)


def test_end_of_line_early_is_any_time_before_the_schedule(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, -1]), trip("t2", "901", A, [0, 0, 0, 0]),
                       trip("t3", "901", A, [0, 0, 0, -300]))
    assert stage.end_of_line([])[()] == {"n": 3, "early": 2, "p10": -300, "p50": -1, "p90": 0,
                                         "n_block": 3, "n_gps": 0, "late": 0, "next_n": 0, "next_in_time": 0,
                                         "spare_p50": None}


def test_end_of_line_late_is_the_site_window(tmp_path):
    # Late: more than 5 minutes after the scheduled arrival, as for departures.
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, 301]), trip("t2", "901", A, [0, 0, 0, 300]),
                       trip("t3", "901", A, [0, 0, 0, -400]))
    assert stage.end_of_line([])[()]["late"] == 1


def test_end_of_line_counts_its_sources(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, 30]), trip("t2", "901", A, [0, 0, 0, 0], eol_method="gps"),
                       trip("t3", "902", A, [0, 0, 0, 0], eol_method="gps"))
    eol = stage.end_of_line(["route"])
    assert (eol[("901",)]["n_block"], eol[("901",)]["n_gps"]) == (1, 1)
    assert (eol[("902",)]["n_block"], eol[("902",)]["n_gps"]) == (0, 1)


def followed_by(prev, nxt, carry_over):
    """nxt (a trip()) as the next trip on prev's block, with its carry-over value."""
    return nxt[0], {**nxt[1], "prev_trip_id": prev[1]["trip_id"], "carry_over": carry_over}


def test_end_of_line_by_route_direction_and_stop(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, 30]),
                       trip("t2", "901", list(reversed(A)), [0, 0, 0, -60], direction=1),
                       trip("t3", "902", ["E9", "E4"], [0, 120]))
    eol = stage.end_of_line(["route", "direction_id", "stop"])
    assert {k: v["n"] for k, v in eol.items()} == {("901", 0, "E4"): 1, ("901", 1, "E1"): 1, ("902", 0, "E4"): 1}
    by_stop = stage.end_of_line(["stop", "route"])
    assert (by_stop[("E4", "901")]["p50"], by_stop[("E4", "902")]["p50"]) == (30, 120)
    assert stage.end_of_line(["stop"])[("E4",)]["n"] == 2


def test_end_of_line_in_time_for_the_next_trip(tmp_path):
    # Each trip is scheduled to end 30 minutes after it starts; the next ones start an hour after.
    t1, t2, t3 = trip("t1", "901", A, [0, 0, 0, 30]), trip("t2", "901", A, [0, 0, 0, 1900]),         trip("t3", "901", A, [0, 0, 0, 0])
    n1 = followed_by(t1, trip("n1", "901", A, [0, 0, 0, 0], hour=9), False)
    n2 = followed_by(t2, trip("n2", "901", A, [0, 0, 0, 0], hour=9), True)
    # Carry-over unknown: the arrival counts, but not toward the next trip.
    n3 = followed_by(t3, trip("n3", "901", A, [0, 0, 0, 0], hour=9), None)
    stage = make_stage(tmp_path, t1, t2, t3, n1, n2, n3)
    p = stage.end_of_line(["route"])[("901",)]
    assert p["n"] == 6
    assert (p["next_n"], p["next_in_time"]) == (2, 1)
    # Time to spare: the next trip's scheduled departure minus the arrival (1770 s and -100 s);
    # the nearest-rank median of two is the lower one.
    assert p["spare_p50"] == -100


def test_end_of_line_counts_each_arrival_once_with_the_earliest_next_trip(tmp_path):
    t1 = trip("t1", "901", A, [0, 0, 0, 30])
    stage = make_stage(tmp_path, t1, followed_by(t1, trip("n1", "901", A, [0, 0, 0, 0], hour=9), False),
                       followed_by(t1, trip("n2", "901", A, [0, 0, 0, 0], hour=10), True))
    p = stage.end_of_line(["stop", "route"])[("E4", "901")]
    assert (p["n"], p["next_n"], p["next_in_time"], p["spare_p50"]) == (3, 1, 1, 1770)


def test_end_of_line_next_trip_follows_the_filter(tmp_path):
    t1 = trip("t1", "901", A, [0, 0, 0, 30])
    stage = make_stage(tmp_path, t1, followed_by(t1, trip("n1", "901", A, [0, 0, 0, 0], hour=9), False))
    assert stage.only("weekday", "am_peak").end_of_line(["route"])[("901",)]["next_n"] == 1
    assert ("901",) not in stage.only("all", "pm_peak").end_of_line(["route"])


def test_trip_facts(tmp_path):
    trips = [trip("full", "901", A, [0, 0, 0, 0]),
             trip("half", "901", A, [0, 0, 0, 0], stop_sr={1: "NO_DATA", 2: "SKIPPED"}),
             trip("cancel", "901", A, [0, 0, 0, 0], trip_sr="CANCELED"),
             trip("unseen", "902", A, [0, 0, 0, 0], observed=False),
             trip("added", "902", A, [0, 0, 0, 0], trip_sr="ADDED", scheduled=False)]
    facts = make_stage(tmp_path, *trips).trip_facts(None)[None]
    assert facts["trips"] == {"scheduled": 4, "observed": 1, "not_observed": 2, "cancelled": 1, "added": 1,
                              "skipped_stops": 1}
    assert facts["completeness"]["departures_scheduled"] == 9 and facts["completeness"]["departures_observed"] == 4
    # "half" has 1 of 3 timepoints observed: it counts at a lower coverage threshold.
    lenient = make_stage(tmp_path, *trips, trip_coverage_min=0.3).trip_facts("route")
    assert lenient["901"]["trips"]["observed"] == 2 and lenient["902"]["trips"]["observed"] == 0


def test_quality_days_cover_every_processed_date(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, 0], day=DAY2), lost_s=3600)
    first, second = stage.quality_days()
    assert (first["date"], first["trips_scheduled"], first["uptime_service_hours"]) == (DAY.isoformat(), 0, 0.9)
    assert (second["date"], second["trips_observed"], second["uptime_service_hours"]) == (DAY2.isoformat(), 1, 1.0)


def test_the_network_as_scheduled(tmp_path):
    trips = [trip("a", "901", ["E1", "E2", "E5"], [0, 0, 0], timepoints={0, 2}),
             trip("b", "902", ["E5", "E6", "S7"], [0, 0, 0], timepoints={0}),
             trip("c", "903", ["E9"], [0], scheduled=False)]
    stage = make_stage(tmp_path, *trips)
    assert sorted(stage.served_routes()) == ["901", "902"]
    serving, timed = stage.stop_routes()
    assert serving["E5"] == {"901", "902"} and "E9" not in serving
    # E5 ends 901, so it is a timed stop only for 902.
    assert timed == {"E1": {"901"}, "E5": {"902"}}
    assert stage.route_stop_order()[("901", 0)] == [("E1", True), ("E2", False), ("E5", False)]
    assert stage.route_spans()["901"] == [(trips[0][1]["start_ts"], trips[0][1]["end_ts"])]


def test_map_cells_add_up(tmp_path):
    trips = [trip("a", "901", A, [0, 400, 0, 0], hour=7), trip("b", "901", A, [-90, 0, 0, 0], hour=20),
             trip("c", "901", A, [0, 0, 0, 0], day=date(2026, 10, 10))]
    cells = make_stage(tmp_path, *trips, days=(DAY, date(2026, 10, 10))).map_cells()
    assert cells[("all", "all", "all", "all_stops", "E1")] == [3, 1, 2, 0, 2]
    assert cells[("2026-10", "weekday", "am_peak", "all_stops", "E2")] == [1, 0, 0, 1, 0]
    parts = [cells.get(("all", dt, "all", "all_stops", "E1"), [0] * 5) for dt in ("weekday", "saturday", "sunday")]
    assert [sum(p[k] for p in parts) for k in range(5)] == cells[("all", "all", "all", "all_stops", "E1")]


SAT = date(2026, 10, 10)


def test_only_narrows_to_a_day_type_and_band(tmp_path):
    trips = [trip("am", "901", A, [10, 20, 30, 40], hour=7), trip("pm", "901", A, [400, 400, 400, 0], hour=16),
             trip("sat", "901", A, [-90, 0, 0, 0], hour=7, day=SAT)]
    stage = make_stage(tmp_path, *trips, days=(DAY, SAT))
    narrowed = stage.only("weekday", "am_peak")
    block = narrowed.perf_blocks(None, daily=True)[("all_stops", None)]
    assert block["summary"] == reference([10, 20, 30])
    # By hour follows the day type only, by day type the band only.
    assert [(h["hour"], h["n"]) for h in block["by_hour"]] == [(7, 3), (16, 3)]
    assert {dt: v["n"] for dt, v in block["by_daytype"].items()} == {"weekday": 3, "saturday": 3, "sunday": 0}
    # Each day lists only weekdays: Tuesday to Friday.
    assert [d["date"] for d in block["daily"]] == [(DAY + timedelta(days=i)).isoformat() for i in range(4)]
    assert narrowed.end_of_line([])[()]["n"] == 1 and stage.end_of_line([])[()]["n"] == 3
    assert narrowed.perf([], terminal=True)[()]["n"] == 1
    assert stage.only("all", "am_peak").perf(["scope"])[("all_stops",)]["n"] == 6
    assert stage.perf(["scope"])[("all_stops",)]["n"] == 9         # the Stage itself is unchanged
    assert stage.only("sunday", "all").perf_blocks(None, daily=True) == {}
    with pytest.raises(ValueError):
        stage.only("weekday", "late_night")


def test_scheduled_departures_by_day_type_and_band(tmp_path):
    trips = [trip("am", "901", A, [0, 0, 0, 0], hour=7, timepoints={0, 3}),
             trip("unseen", "901", A, [0, 0, 0, 0], hour=16, observed=False),
             trip("cancel", "901", A, [0, 0, 0, 0], hour=16, trip_sr="CANCELED"),
             trip("added", "901", A, [0, 0, 0, 0], hour=16, trip_sr="ADDED", scheduled=False),
             trip("sat", "902", A, [0, 0, 0, 0], hour=7, day=SAT)]
    stage = make_stage(tmp_path, *trips, days=(DAY, SAT))
    # Every stop but the last, of scheduled trips that weren't cancelled, observed or not.
    assert stage.scheduled_departures("route") == {"901": {"timepoints": 4, "all_stops": 6},
                                                   "902": {"timepoints": 3, "all_stops": 3}}
    assert stage.scheduled_departures(None)[None]["timepoints"] == stage.trip_facts(None)[None]["completeness"][
        "departures_scheduled"] == 7
    assert stage.only("weekday", "pm_peak").scheduled_departures("route") == {"901": {"timepoints": 3, "all_stops": 3}}
    assert stage.only("saturday", "pm_peak").scheduled_departures(None) == {}


def test_detours_flag_trips_on_their_routes_while_active(tmp_path):
    t1 = trip("t1", "901", A, [0, 0, 0, 0], hour=8)
    t2 = trip("t2", "901", A, [0, 0, 0, 0], hour=12)
    t3 = trip("t3", "902", A, [0, 0, 0, 0], hour=8, direction=1)
    t4 = trip("t4", "903", A, [0, 0, 0, 0], hour=8, day=DAY2)
    start = t1[1]["start_ts"]
    alerts = [
        # Posted 07:50 to 10:00 but taken down after the 08:20 snapshot: flags t1 only.
        alert("a1", ["R901"], start - 3600, start + 1200, periods=[(start - 600, start + 7200)],
              header="Route 901 on detour", description="Stops E2 and E3 closed."),
        # Tagged 902 only; the hand-kept file adds 903 (whose trip runs the next day).
        alert("a2", ["R902"], start - 3600, t4[1]["end_ts"]),
        # Not a detour.
        alert("a3", ["R901"], start - 3600, t2[1]["end_ts"], effect="SIGNIFICANT_DELAYS"),
    ]
    stage = make_stage(tmp_path, t1, t2, t3, t4, alerts=alerts, extra_routes={"a2": ["R903"]})
    found = {(d["alert_id"], d["route"]): d for d in stage.detours()}
    assert set(found) == {("a1", "901"), ("a2", "902"), ("a2", "903")}
    a1 = found[("a1", "901")]
    assert a1["trips"] == 1 and a1["header"] == "Route 901 on detour" and a1["description"] == "Stops E2 and E3 closed."
    assert (a1["start"], a1["end"]) == (timeutil.iso_utc(start - 600), timeutil.iso_utc(start + 1200 + 300))
    assert stage.detour_trips(["date", "route", "direction_id"]) == {
        (DAY.isoformat(), "901", 0): 1, (DAY.isoformat(), "902", 1): 1, (DAY2.isoformat(), "903", 0): 1}


def test_no_alerts_no_detours(tmp_path):
    stage = make_stage(tmp_path, trip("t1", "901", A, [0, 0, 0, 0]))
    assert stage.detours() == [] and stage.detour_trips(["date", "route", "direction_id"]) == {}
