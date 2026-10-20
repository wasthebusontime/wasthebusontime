"""Per trip: its block, the previous trip on the block, the layover and carry-over (GLOSSARY.md)."""

import csv
from datetime import date

import duckdb
import pytest
from conftest import DAY
from synth import Feed, Trip, TripPlan, write_gtfs
from wbot_site import contract

from wbot_pipeline import events, opendata, sitefiles, static
from wbot_pipeline.config import CARRY_OVER_MARGIN_S
from wbot_pipeline.stage import Period

LINE = ["S1", "S2", "S3", "S4", "S5"]   # 4 minutes apart: a trip takes 16 minutes


def t(trip_id, direction, start_min, block):
    stops = LINE if direction == 0 else LINE[::-1]
    return Trip(trip_id, "R901", direction, "WK", start_min, stops, {0, 2, 4}, {}, "", f"SH901-{direction}",
                block_id=block)


TRIPS = [
    t("p1", 0, 7 * 60, "B1"), t("n1", 1, 7 * 60 + 20, "B1"),         # in time for its departure
    t("p2", 0, 8 * 60, "B2"), t("n2", 1, 8 * 60 + 20, "B2"),         # carry-over
    t("p3", 0, 9 * 60, "B3"), t("n3", 1, 9 * 60 + 30, "B3"),         # 14 min layover
    t("p4", 0, 10 * 60, "B4"), t("n4", 1, 10 * 60 + 20, "B4"),       # another bus on n4
    t("p5", 0, 11 * 60, "B5"), t("n5", 1, 11 * 60 + 20, "B5"),       # n5 never tracked
    t("p6", 0, 12 * 60, "B6"), t("n6", 1, 12 * 60 + 20, "B6"),       # no data at n6's first stop
    t("q1", 0, 13 * 60, "B7"), t("q2", 1, 13 * 60 + 20, "B7"), t("q3", 0, 13 * 60 + 40, "B7"),
    t("x1", 0, 14 * 60, ""),                                         # no block
    # 12 min layovers, judged from the previous trip's GPS arrival at the shared stop.
    t("p7", 0, 15 * 60, "B8"), t("n7", 1, 15 * 60 + 28, "B8"),       # arrived 160 s after n7 was due
    t("p8", 0, 16 * 60, "B9"), t("n8", 1, 16 * 60 + 28, "B9"),       # just after: inside the margin
    t("p9", 0, 17 * 60, "B10"), t("n9", 1, 17 * 60 + 28, "B10"),     # another bus on n9
]


@pytest.fixture
def trips(settings):
    write_gtfs(settings.static_dir, "test1_aaaaaaaa", TRIPS)
    feed = Feed(settings.data_dir, DAY, TRIPS)
    feed.last_stop_in_feed = False
    feed.plans["n1"] = TripPlan(delays=[0, 30, 30, 30, 30])   # reaches its first stop 20 s early
    feed.plans["n4"] = TripPlan(vehicle_id="VX4")
    feed.plans["n5"] = TripPlan(vehicle=False)
    feed.plans["n6"] = TripPlan(no_data={0})
    # p7 runs 900 s late: it reaches the shared stop 16 + 15 min - 20 s after its start, 160 s
    # after n7's scheduled departure. p8 runs 800 s late: 60 s after, which GPS puts a little
    # earlier. The next trips leave late too, so their buses don't report them before the
    # previous trip ends.
    feed.plans["p7"] = TripPlan(delays=[900] * 5)
    feed.plans["n7"] = TripPlan(delays=[900] * 5)
    feed.plans["p8"] = TripPlan(delays=[800] * 5)
    feed.plans["n8"] = TripPlan(delays=[850] * 5)
    feed.plans["p9"] = TripPlan(delays=[900] * 5)
    feed.plans["n9"] = TripPlan(delays=[900] * 5, vehicle_id="VX9")
    feed.write()
    events.process_date(settings, static.list_versions(settings.static_dir), DAY, "test")
    path = events.paths(settings, DAY).trips.as_posix()
    cur = duckdb.connect().execute(f"SELECT * FROM read_parquet('{path}')")
    names = [c[0] for c in cur.description]
    return {r["trip_id"]: r for r in (dict(zip(names, row)) for row in cur.fetchall())}


def test_pair_in_time_for_its_departure(trips):
    n1 = trips["n1"]
    assert (n1["block_id"], n1["prev_trip_id"], n1["sched_layover_s"]) == ("B1", "p1", 4 * 60)
    # p1's end-of-line arrival is n1's first-stop arrival, 20 s before n1 leaves at 7:20.
    assert n1["prev_end_delay_s"] == trips["p1"]["end_delay_s"] == 4 * 60 - 20
    assert n1["start_delay_s"] == 0
    assert n1["carry_over"] is False


def test_carry_over(trips):
    # The bus reached n2's first stop 10 s after n2 was due to leave.
    n2 = trips["n2"]
    assert (n2["prev_trip_id"], n2["prev_end_delay_s"], n2["start_delay_s"]) == ("p2", 4 * 60 + 10, 30)
    assert n2["carry_over"] is True


def test_chains_along_the_block(trips):
    assert [trips[x]["prev_trip_id"] for x in ("q1", "q2", "q3")] == [None, "q1", "q2"]
    assert trips["q3"]["carry_over"] is True


@pytest.mark.parametrize("trip_id", ["n4", "n5", "n6"])
def test_unknown_when_the_pair_fails_the_rules_or_the_arrival_is_missing(trips, trip_id):
    # n4: another bus; n5: never tracked; n6: no arrival at its first stop.
    row = trips[trip_id]
    assert row["prev_trip_id"] == "p" + trip_id[1] and row["carry_over"] is None
    assert row["sched_layover_s"] == 4 * 60


def test_long_layover_from_the_previous_trips_gps_arrival(trips):
    # p3 ended on time, 14 minutes before n3 was due: not a carry-over.
    assert (trips["n3"]["sched_layover_s"], trips["n3"]["carry_over"]) == (14 * 60, False)
    # p7 reached the stop 160 s after n7 was due (GPS, good to about 15 s): a carry-over.
    assert (trips["n7"]["sched_layover_s"], trips["n7"]["carry_over"]) == (12 * 60, True)
    assert trips["n7"]["prev_end_delay_s"] == pytest.approx(12 * 60 + 160, abs=30)


def test_long_layover_inside_the_margin_is_not_a_carry_over(trips):
    # p8 reached the stop after n8 was due, but within CARRY_OVER_MARGIN_S of it.
    assert 0 < trips["n8"]["prev_end_delay_s"] - trips["n8"]["sched_layover_s"] <= CARRY_OVER_MARGIN_S
    assert trips["n8"]["carry_over"] is False


def test_long_layover_needs_the_same_bus(trips):
    assert trips["n9"]["prev_end_delay_s"] is not None and trips["n9"]["carry_over"] is None


@pytest.mark.parametrize("trip_id", ["p1", "q1"])
def test_first_trip_on_its_block_is_not_a_carry_over(trips, trip_id):
    row = trips[trip_id]
    assert (row["prev_trip_id"], row["sched_layover_s"], row["prev_end_delay_s"], row["carry_over"]) ==         (None, None, None, False)


def test_unknown_without_a_block(trips):
    row = trips["x1"]
    assert (row["prev_trip_id"], row["sched_layover_s"], row["prev_end_delay_s"], row["carry_over"]) ==         (None, None, None, None)


def test_block_from_the_schedule(trips):
    assert (trips["q1"]["block_id"], trips["q3"]["block_id"], trips["x1"]["block_id"]) == ("B7", "B7", None)


# The per-trip download.

def test_finished_months():
    oct4 = date(2026, 10, 4)
    assert opendata.finished_months(oct4, date(2026, 11, 2), 3) == []
    assert opendata.finished_months(oct4, date(2026, 11, 3), 3) == ["2026-10"]
    assert opendata.finished_months(oct4, date(2026, 10, 30), 0) == []
    assert opendata.finished_months(oct4, date(2026, 10, 31), 0) == ["2026-10"]
    assert opendata.finished_months(oct4, date(2027, 1, 5), 3) == ["2026-10", "2026-11", "2026-12"]


def write_trips(settings, end, out):
    period = Period(DAY, end, [events.read_manifest(settings, DAY)])
    db = settings.derived_dir / "test.duckdb"
    stage = sitefiles.prepare(settings, period, db)
    try:
        return opendata.write_trips(stage, out)
    finally:
        stage.close()
        db.unlink()


def test_download_waits_for_the_month_to_finish(settings, trips, tmp_path):
    # Oct 31 is still in the trailing window until the data runs through Nov 3.
    assert write_trips(settings, date(2026, 11, 2), tmp_path / "trips") == []
    assert not (tmp_path / "trips").exists()


def test_download_has_a_row_per_scheduled_trip(settings, trips, tmp_path):
    assert write_trips(settings, date(2026, 11, 3), tmp_path / "trips") == ["2026-10"]
    path = tmp_path / "trips" / "trips_2026-10.csv"
    contract.validate_trips(tmp_path / "trips")
    with open(path, newline="", encoding="utf-8") as f:
        rows = {r["trip_id"]: r for r in csv.DictReader(f)}
    assert set(rows) == set(trips)
    assert rows["n1"] == {"date": "2026-10-06", "route": "901", "direction_id": "1", "trip_id": "n1",
                          "sched_start": "07:20:00", "start_delay_s": "0", "end_delay_s": rows["n1"]["end_delay_s"],
                          "block_id": "B1", "prev_trip_id": "p1", "sched_layover_s": "240",
                          "prev_end_delay_s": "220", "carry_over": "no"}
    assert (rows["n2"]["carry_over"], rows["n3"]["carry_over"], rows["n4"]["carry_over"], rows["x1"]["block_id"]) ==         ("yes", "no", "", "")
    # Rewritten byte for byte by the next night's run, so the stats repo sees no change.
    before = path.read_bytes()
    write_trips(settings, date(2026, 11, 4), tmp_path / "trips")
    assert path.read_bytes() == before
