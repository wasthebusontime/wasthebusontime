"""Carry-over patterns: the rules (patterns.py) on hand-made cells, and the measure (Stage)."""

from datetime import date, timedelta

import duckdb
import pytest
from test_stage import AL, EV, TR, trip
from wbot_site import contract

from wbot_pipeline import patterns
from wbot_pipeline.config import load_settings
from wbot_pipeline.patterns import Cell
from wbot_pipeline.stage import Period, Stage

MONDAY = date(2026, 10, 5)


def days(n, daytype="weekday"):
    """n dates of a day type, from MONDAY on."""
    weekdays = {"weekday": range(5), "saturday": (5,), "sunday": (6,)}[daytype]
    out, d = [], MONDAY
    while len(out) < n:
        if d.weekday() in weekdays:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def cells(n_days, trips, matched_trips, *, route="13", direction=0, stop="A", daytype="weekday", band="midday",
          late_per_trip=2):
    """One cell per day: `trips` trips with late_per_trip late departures each, of which
    matched_trips left late right after a carry-over."""
    return [Cell(d, route, direction, stop, daytype, band, trips * late_per_trip, matched_trips * late_per_trip, trips)
            for d in days(n_days, daytype)]


def found(cs, routes=("13",)):
    return patterns.carry_over(cs, list(routes))


def test_general_pattern_at_the_minimum():
    # 30 trips with a late departure, 1 in 3 of their late departures after a carry-over, 10 days.
    ps, too_little = found(cells(10, 3, 1))
    (p,) = ps
    assert (p.route, p.direction_id, p.stops, p.preset) == ("13", 0, ["A"], None)
    assert (p.trips, p.days, p.late, p.matched, p.rank) == (30, 10, 60, 20, 1)
    assert p.share == pytest.approx(1 / 3) and too_little == []
    assert p.doc() == {"id": "carry_over.13.0.A.all-all", "kind": "carry_over", "route": "13", "direction_id": 0,
                       "stops": ["A"], "preset": None, "rank": 1, "trips": 30, "days": 10, "late": 60,
                       "matched": 20, "share": 0.3333}


def test_too_few_trips_is_too_little_data():
    ps, too_little = found(cells(29, 1, 1))
    assert ps == [] and too_little == ["13"]


def test_a_route_without_trips_is_too_little_data():
    ps, too_little = found(cells(10, 3, 3), routes=("13", "14"))
    assert [p.route for p in ps] == ["13"] and too_little == ["14"]


def test_share_below_the_threshold_is_no_pattern():
    # 19 of 60 late departures: just under 1 in 3. Enough data, so not "too little".
    cs = cells(10, 3, 1)
    cs[0] = cs[0]._replace(matched=1)
    ps, too_little = found(cs)
    assert ps == [] and too_little == []


def test_matched_on_too_few_days_is_no_pattern():
    # Every matched trip on 4 days: share 0.4, but too few days.
    cs = cells(4, 3, 3) + cells(10, 3, 0)[4:]
    ps, too_little = found(cs)
    assert sum(c.matched for c in cs) / sum(c.late for c in cs) == pytest.approx(0.4)
    assert ps == [] and too_little == []


def test_a_group_is_route_direction_and_first_stop():
    # Each group alone is under the minimum; together they would make it.
    cs = cells(10, 2, 1, stop="A") + cells(10, 2, 1, stop="B") + cells(10, 2, 1, direction=1)
    ps, too_little = found(cs)
    assert ps == [] and too_little == ["13"]


def test_specific_slot_needs_the_margin_over_general():
    # Weekday PM peaks: every late trip after a carry-over; midday: none; Saturday PM peaks: none.
    cs = cells(10, 3, 0) + cells(10, 3, 3, band="pm_peak") + cells(5, 2, 0, daytype="saturday", band="pm_peak")
    ps, _ = found(cs)
    # General 60/140 = 0.43. Weekday all day 60/120 = 0.5 is under 0.43 + 0.10. All days PM
    # peak 60/80 = 0.75 qualifies; weekday PM peak 60/60 = 1.0 is 10 points above it too, and
    # ranks first of the two (the same matched count, a higher share).
    assert [(p.preset, p.matched, p.late, p.rank) for p in ps] == [
        (None, 60, 140, 1), ({"daytype": "weekday", "band": "pm_peak"}, 60, 60, 2),
        ({"daytype": "all", "band": "pm_peak"}, 60, 80, 3)]


def test_broader_slot_kept_over_a_nested_one():
    # Saturday PM peaks now also all after a carry-over: all days PM peak is 1.0, and weekday
    # PM peak (1.0) isn't 10 points above it, so only the broader slot shows.
    cs = cells(10, 3, 0) + cells(10, 3, 3, band="pm_peak") + cells(5, 2, 2, daytype="saturday", band="pm_peak")
    ps, _ = found(cs)
    assert [p.preset for p in ps] == [None, {"daytype": "all", "band": "pm_peak"}]


def test_specific_slot_meets_the_minimum_itself():
    # Weekday PM peaks are strong but only 20 trips (general: 40/140, no pattern).
    cs = cells(10, 5, 0) + cells(10, 2, 2, band="pm_peak")
    ps, _ = found(cs)
    assert ps == []


def test_specific_pattern_without_a_general_one():
    # General 60/240 = 0.25: no general pattern, but the PM peak slot is one.
    cs = cells(10, 9, 0) + cells(10, 3, 3, band="pm_peak")
    ps, _ = found(cs)
    assert [(p.preset, p.rank) for p in ps] == [({"daytype": "all", "band": "pm_peak"}, 1)]


def test_ranking_general_first_then_matched_then_share():
    cs = (cells(10, 3, 1, direction=0)                               # general, matched 20, share 1/3
          + cells(10, 3, 2, direction=1)                             # general, matched 40, share 2/3
          + cells(10, 6, 2, stop="B")                                # general, matched 40, share 1/3
          + cells(10, 9, 0, stop="C") + cells(10, 3, 3, stop="C", band="pm_peak"))   # specific, matched 60
    ps, _ = found(cs)
    assert [(p.direction_id, p.stops[0], p.preset is None, p.rank) for p in ps] == [
        (1, "A", True, 1), (0, "B", True, 2), (0, "A", True, 3), (0, "C", False, 4)]


def test_patterns_follow_the_route_order_given():
    cs = cells(10, 3, 3, route="14") + cells(10, 3, 3, route="13")
    ps, _ = found(cs, routes=("14", "13"))
    assert [p.route for p in ps] == ["14", "13"]


def test_counts_per_filter():
    cs = cells(10, 3, 0) + cells(10, 3, 3, band="pm_peak") + cells(5, 2, 2, daytype="saturday", band="pm_peak")
    ps, _ = found(cs)
    counts = contract.pattern_counts([p.doc() for p in ps])
    assert counts["all-all"] == 2 and counts["weekday-pm_peak"] == 2 and counts["all-pm_peak"] == 2
    assert counts["weekday-midday"] == 1 and counts["weekday-all"] == 1 and counts["sunday-early"] == 1


def test_document():
    cs = cells(10, 3, 3) + cells(10, 3, 0, route="14")
    period = Period(MONDAY, MONDAY + timedelta(days=13))
    doc = patterns.document(*found(cs, routes=("13", "14", "15")), period)
    assert doc["schema"] == 1 and doc["rules_version"] == 1
    assert doc["period"] == {"key": "all", "start": "2026-10-05", "end": "2026-10-18"}
    assert doc["thresholds"]["carry_over"] == {"late_after_s": 300, "min_trips": 30, "min_days": 5,
                                               "min_share": 0.3333, "specific_margin": 0.1,
                                               "long_layover_s": 600, "long_layover_margin_s": 60}
    assert [p["id"] for p in doc["patterns"]] == ["carry_over.13.0.A.all-all"]
    assert doc["routes"] == {"13": contract.pattern_counts(doc["patterns"])}
    assert doc["stops"] == {"A": contract.pattern_counts(doc["patterns"])}
    assert doc["too_little_data"] == ["15"]


def test_csv_rows():
    cs = cells(10, 3, 0) + cells(10, 3, 3, band="pm_peak")
    rows = patterns.csv_rows(found(cs)[0])
    assert rows == [["carry_over.13.0.A.all-all", "carry_over", "13", 0, "A", "all", "all", 1, 60, 10, 120, 60, 0.5],
                    ["carry_over.13.0.A.all-pm_peak", "carry_over", "13", 0, "A", "all", "pm_peak", 2, 30, 10, 60, 60,
                     1.0]]


# The measure, from stop events and trips (Stage.carry_over_cells).

def stage_with(trips):
    """trips: (events, trip row, carry_over, start_delay_s) per trip."""
    con = duckdb.connect()
    tr_cols = {**TR, "carry_over": "BOOLEAN", "start_delay_s": "INTEGER"}
    ev = [e for events, *_ in trips for e in events]
    tr = [{**row, "carry_over": carry_over, "start_delay_s": start} for _, row, carry_over, start in trips]
    for name, cols, rows in (("ev", EV, ev), ("tr", tr_cols, tr), ("al", AL, [])):
        con.execute(f"CREATE TABLE {name} ({', '.join(f'{k} {v}' for k, v in cols.items())})")
        if rows:
            con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in cols)})",
                            [[r[k] for k in cols] for r in rows])
    settings = load_settings({}, data_dir="x", derived_dir="x", first_service_date=MONDAY)
    return Stage(settings, Period(MONDAY, MONDAY), con, extra_routes={})


def test_measure_counts_late_departures_at_all_stops():
    day = date(2026, 10, 6)
    stops = ["A", "S2", "S3", "S4"]
    s = stage_with([
        # Left late after a carry-over: 2 late departures (the last stop is an arrival, not counted).
        (*trip("c1", "13", stops, [400, 500, 200, 600], day=day, hour=16), True, 400),
        # Late, no carry-over: 2 late departures.
        (*trip("n1", "13", stops, [350, 100, 320, 0], day=day, hour=16), False, 350),
        # A carry-over but left on time: 1 late departure, not matched.
        (*trip("c2", "13", stops, [100, 400, 0, 0], day=day, hour=16), True, 100),
        # Carry-over unknown, or the first-stop departure not counted: left out.
        (*trip("u1", "13", stops, [400, 400, 400, 0], day=day, hour=16), None, 400),
        (*trip("u2", "13", stops, [None, 400, 400, 0], day=day, hour=16), True, None),
        # No late departure: not a trip with a late departure.
        (*trip("o1", "13", stops, [0, 0, 0, 0], day=day, hour=16), False, 0),
    ])
    assert s.carry_over_cells() == [Cell("2026-10-06", "13", 0, "A", "weekday", "pm_peak", 5, 2, 3)]
