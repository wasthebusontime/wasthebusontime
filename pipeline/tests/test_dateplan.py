from datetime import date, timedelta

import pytest

from wbot_pipeline import dateplan
from wbot_pipeline.config import EVENTS_VERSION
from wbot_pipeline.dateplan import Day, Entry

D = date(2026, 10, 6)
PACKS = [{"path": "archive/tripupdates/2026/10/06/tripupdates-2026-10-06T14.tar.zst", "size": 100}]
HOURS = [1_791_300_000 + 3600 * i for i in range(3)]


def manifest(**changes) -> dict:
    m = {"events_version": EVENTS_VERSION, "methodology_version": "1", "static": {"stem": "v1"},
         "status": "ok", "packs": [{**p, "sha256": "x"} for p in PACKS]}
    return {**m, **changes}


def day(d=D, **changes) -> Day:
    fields = {"date": d, "manifest": manifest(), "static_stem": "v1", "packs": PACKS, **changes}
    return Day(**fields)


def reason(**changes):
    return dateplan.stale_reason(day(**changes), "1")


def test_current_date_needs_nothing():
    assert reason() is None


def test_new_date():
    assert reason(manifest=None) == "new"


@pytest.mark.parametrize("changes", [
    {"manifest": manifest(events_version=EVENTS_VERSION - 1)},
    {"manifest": manifest(methodology_version="0")},
    {"static_stem": "v2"},          # a newer static version applies now
    {"static_stem": None},          # none does any more
])
def test_old_version(changes):
    assert reason(**changes) == "old_version"


@pytest.mark.parametrize("status", ["no_static", "no_service"])
def test_dates_without_static_or_service_are_tried_again(status):
    assert reason(manifest=manifest(status=status)) == status


def test_packs_changed():
    late = {"path": PACKS[0]["path"].replace(".tar.zst", ".1.tar.zst"), "size": 50}
    assert reason(packs=[*PACKS, late]) == "packs_changed"
    assert reason(packs=[{**PACKS[0], "size": 101}]) == "packs_changed"
    assert reason(packs=[]) == "packs_changed"


def test_recent_dates_are_redone():
    days = [day(D + timedelta(days=i)) for i in range(5)]
    days[1] = day(D + timedelta(days=1), manifest=None)
    todo = dateplan.dates(days, trailing_days=2, methodology_version="1")
    assert todo == [(D + timedelta(days=1), "new"), (D + timedelta(days=3), "recent"), (D + timedelta(days=4), "recent")]
    assert dateplan.dates(days, trailing_days=0, methodology_version="1") == [(D + timedelta(days=1), "new")]


def test_rebuild_redoes_every_date():
    days = [day(D + timedelta(days=i)) for i in range(3)]
    assert dateplan.dates(days, trailing_days=1, methodology_version="1", rebuild=True) == \
        [(D + timedelta(days=i), "rebuild") for i in range(3)]


def test_waits_until_the_last_hour_is_packed():
    ready_at = HOURS[-1] + 3600 + dateplan.READY_AFTER_S
    assert dateplan.entry(D, "new", HOURS, set(), ready_at - 1) == Entry(D, False, "hours_missing")
    assert dateplan.entry(D, "new", HOURS, set(), ready_at) == Entry(D, True, "new")


def test_waits_while_an_hour_is_still_in_the_spool():
    late = HOURS[-1] + 86400
    assert dateplan.entry(D, "recent", HOURS, {HOURS[1]}, late) == Entry(D, False, "hours_missing")
    assert dateplan.entry(D, "recent", HOURS, {HOURS[0] - 3600}, late) == Entry(D, True, "recent")


def test_date_that_reads_no_hours_never_waits():
    # No static version or no service: processing only records that.
    assert dateplan.entry(D, "requested", None, set(), 0) == Entry(D, True, "requested")
