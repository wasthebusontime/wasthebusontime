from datetime import UTC, date, datetime

from wbot_pipeline import timeutil


def utc(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def test_origin_is_midnight_on_ordinary_days():
    assert timeutil.origin(date(2026, 10, 6)) == utc(2026, 10, 6, 7)    # PDT
    assert timeutil.origin(date(2026, 12, 1)) == utc(2026, 12, 1, 8)    # PST


def test_origin_on_dst_change_days_is_noon_minus_12_hours():
    # Fall back on 2026-11-01: noon is PST (UTC-8), so the origin is 23:00 local the day before.
    o = timeutil.origin(date(2026, 11, 1))
    assert o == utc(2026, 11, 1, 20) - 12 * 3600
    # A trip scheduled at 08:00:00 leaves at 08:00 PST.
    assert o + timeutil.gtfs_seconds("08:00:00") == utc(2026, 11, 1, 16)
    # Spring forward on 2027-03-14: noon is PDT (UTC-7).
    o = timeutil.origin(date(2027, 3, 14))
    assert o + timeutil.gtfs_seconds("08:00:00") == utc(2027, 3, 14, 15)


def test_times_past_midnight_stay_on_the_service_date():
    o = timeutil.origin(date(2026, 10, 6))
    assert o + timeutil.gtfs_seconds("25:10:00") == utc(2026, 10, 7, 8, 10)
    assert timeutil.gtfs_seconds("27:59:59") // 3600 == 27
    assert timeutil.gtfs_seconds("") is None


def test_day_type_and_band():
    assert timeutil.daytype(date(2026, 10, 6)) == "weekday"
    assert timeutil.daytype(date(2026, 10, 10)) == "saturday"
    assert timeutil.daytype(date(2026, 10, 11)) == "sunday"
    assert [timeutil.band(h) for h in (5, 6, 9, 15, 18, 25)] == \
        ["early", "am_peak", "midday", "pm_peak", "evening", "evening"]


def test_snapshot_time_from_collector_file_name():
    assert timeutil.snapshot_time("tripupdates-20261004T032400Z.pb") == utc(2026, 10, 4, 3, 24)
    assert timeutil.snapshot_time("other.pb") is None
