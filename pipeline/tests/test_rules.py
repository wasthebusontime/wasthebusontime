"""The methodology rules, on small in-memory tables."""

import duckdb
import pytest

from wbot_pipeline import rules, timeutil

GOOD = dict(observed=True, scheduled=True, stop_sr="SCHEDULED", trip_sr="SCHEDULED", is_last=False,
            is_timepoint=True, delay_dep_s=30, observed_arr=True, delay_arr_s=10)
TYPES = dict(observed="BOOLEAN", scheduled="BOOLEAN", stop_sr="VARCHAR", trip_sr="VARCHAR", is_last="BOOLEAN",
             is_timepoint="BOOLEAN", delay_dep_s="INTEGER", observed_arr="BOOLEAN", delay_arr_s="INTEGER",
             eligible_timepoints="INTEGER", observed_timepoints="INTEGER")


def holds(rule: str, **cols) -> bool:
    """Whether `rule` is true for one row with GOOD's columns, changed by `cols`."""
    row = {**GOOD, **cols}
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({', '.join(f'{k} {TYPES[k]}' for k in row)})")
    con.execute(f"INSERT INTO t VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    return con.execute(f"SELECT coalesce({rule}, false) FROM t").fetchone()[0]


def test_departure_counts_only_observed_scheduled_departures():
    assert holds(rules.DEPARTURE)
    assert holds(rules.DEPARTURE, delay_dep_s=-900)
    for change in (dict(observed=False), dict(scheduled=False), dict(stop_sr="SKIPPED"), dict(stop_sr="NO_DATA"),
                   dict(trip_sr="CANCELED"), dict(trip_sr="ADDED"), dict(trip_sr=None), dict(is_last=True),
                   dict(delay_dep_s=None)):
        assert not holds(rules.DEPARTURE, **change), change


def test_end_of_line_is_the_last_stop_arrival():
    assert holds(rules.END_OF_LINE, is_last=True, observed=False, delay_dep_s=None)
    for change in (dict(is_last=False), dict(observed_arr=False), dict(stop_sr="SKIPPED"),
                   dict(trip_sr="CANCELED"), dict(scheduled=False), dict(delay_arr_s=None)):
        assert not holds(rules.END_OF_LINE, **{"is_last": True, **change}), change


def test_timed_stop_is_a_timepoint_but_not_the_last_stop():
    assert holds(rules.TIMED_STOP)
    assert not holds(rules.TIMED_STOP, is_last=True)
    assert not holds(rules.TIMED_STOP, is_timepoint=False)


@pytest.mark.parametrize("hour", range(0, 48))
def test_band_matches_the_python_band(hour):
    con = duckdb.connect()
    assert con.execute(f"SELECT {rules.band('h')} FROM (VALUES ({hour})) v(h)").fetchone()[0] == timeutil.band(hour)


TRIP = dict(scheduled=True, trip_sr="SCHEDULED", eligible_timepoints=4, observed_timepoints=2)


def test_trip_counted_excludes_cancelled_and_unscheduled_trips():
    assert holds(rules.TRIP_COUNTED, **TRIP)
    assert holds(rules.TRIP_COUNTED, **{**TRIP, "trip_sr": None})
    assert not holds(rules.TRIP_COUNTED, **{**TRIP, "trip_sr": "CANCELED"})
    assert not holds(rules.TRIP_COUNTED, **{**TRIP, "scheduled": False})


def test_trip_observed_at_and_below_the_coverage_threshold():
    rule = rules.trip_observed(0.5)
    assert holds(rule, **TRIP)
    assert not holds(rule, **{**TRIP, "observed_timepoints": 1})
    assert not holds(rule, **{**TRIP, "eligible_timepoints": 0, "observed_timepoints": 0})
    assert not holds(rule, **{**TRIP, "trip_sr": "CANCELED"})
    assert holds(rules.trip_observed(1.0), **{**TRIP, "observed_timepoints": 4})


def test_register_adds_the_helper_macros():
    con = duckdb.connect()
    rules.register(con)
    assert con.execute("SELECT gtfs_s('25:10:05'), route_slug('  62A Express '), stop_slug('E 1/2')").fetchone() \
        == (90605, "62a-express", "E-1-2")


def where_it_falls(delay: int) -> set[str]:
    con = duckdb.connect()
    names = list(rules.window_counts("d"))
    row = con.execute(f"SELECT {', '.join(rules.window_counts('d').values())} FROM (VALUES ({delay})) v(d)").fetchone()
    return {name for name, hit in zip(names, row) if hit}


@pytest.mark.parametrize("delay, expected", [
    (-61, {"early", "early_alt"}),
    (-60, {"on_time", "early_alt"}),
    (-1, {"on_time", "early_alt"}),
    (0, {"on_time", "on_time_alt"}),
    (300, {"on_time", "on_time_alt"}),
    (301, {"late", "late_alt"}),
])
def test_window_counts_at_the_window_edges(delay, expected):
    assert where_it_falls(delay) == expected


def test_window_counts_follow_the_configured_windows(monkeypatch):
    monkeypatch.setitem(rules.WINDOWS, "", (-120, 420))
    assert where_it_falls(-120) >= {"on_time"} and where_it_falls(-121) >= {"early"}
    assert where_it_falls(420) >= {"on_time"} and where_it_falls(421) >= {"late"}


def test_before_schedule_is_any_early_time():
    con = duckdb.connect()
    assert [r[0] for r in con.execute(f"SELECT {rules.before_schedule('d')} FROM (VALUES (-1), (0), (1)) v(d)").fetchall()] \
        == [True, False, False]
