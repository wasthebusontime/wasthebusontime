import logging

from wbot_site import data


def test_percent_rounds_half_up():
    assert data.percent(1, 8) == "13%"  # 12.5
    assert data.percent(1, 8, 1) == "12.5%"
    assert data.percent(2, 3, 1) == "66.7%"
    assert data.percent(1, 40) == "3%"  # 2.5 rounds up, not to even
    assert data.percent(5, 5) == "100%"
    assert data.percent(0, 0) == "n/a"


def test_fraction():
    assert data.fraction(0.62) == "62%"
    assert data.fraction(0.9975, 1) == "99.8%"
    assert data.fraction(0.125) == "13%"


def test_minimum_sample():
    assert data.enough({"n": 30}, 30)
    assert not data.enough({"n": 29}, 30)
    assert not data.enough(None, 30)


def test_direction_codes_are_read_as_words():
    html = str(data.directions_html("Example St & 1st Ave [sb]"))
    assert html == ('Example St &amp; 1st Ave <span aria-hidden="true">[sb]</span>'
                    '<span class="visually-hidden">southbound</span>')
    assert str(data.directions_html("<b>[xb]</b>")) == "&lt;b&gt;[xb]&lt;/b&gt;"


def test_text_formats():
    assert data.delay_text(0) == "on schedule"
    assert data.delay_text(45) == "45 sec late"
    assert data.delay_text(-75) == "1 min 15 sec early"
    assert data.delay_text(120) == "2 min late"
    assert data.delay_text(None) == "n/a"
    assert data.hour_text(0) == "12 am"
    assert data.hour_text(13) == "1 pm"
    assert data.hour_text(25) == "1 am (next day)"
    assert data.date_text("2026-10-04") == "Oct 4, 2026"
    assert data.month_text("2026-11") == "Nov 2026"
    # Pacific time, daylight and standard, and across midnight UTC.
    assert data.timestamp_text("2026-10-16T13:17:30Z") == "Oct 16, 2026, 6:17 am"
    assert data.timestamp_text("2026-11-02T20:05:00Z") == "Nov 2, 2026, 12:05 pm"
    assert data.timestamp_text("2026-10-04T04:32:23Z") == "Oct 3, 2026, 9:32 pm"


def test_period_text():
    meta = {"period": {"key": "all", "start": "2026-10-04", "end": "2026-11-02"}}
    assert data.period_text(meta) == "since collection began on Oct 4, 2026"
    meta["period"]["key"] = "last28"
    assert data.period_text(meta) == "Oct 4, 2026 to Nov 2, 2026"


def test_unknown_notice_codes_are_skipped_with_a_warning(caplog):
    notices = [{"code": "provisional"}, {"code": "brand_new_code", "x": 1}]
    with caplog.at_level(logging.WARNING, logger="wbot_site"):
        assert data.known_notices(notices, "system.json") == [{"code": "provisional"}]
    assert "brand_new_code" in caplog.text


def test_alert_text_splits_into_headline_and_the_rest():
    # Intercity Transit puts the whole message in the alert's header.
    header = "Route 24 is on detour.\n\nThe following stops are closed:\nStop #894\n"
    assert data.alert_headline(header) == "Route 24 is on detour."
    assert data.alert_details(header, "") == "The following stops are closed:\nStop #894"
    assert data.alert_details("One line only", "Buses use 4th Ave.") == "Buses use 4th Ave."
    assert data.alert_details("Headline\nMore", "Description") == "More\n\nDescription"
    assert data.alert_details("One line only", "") == ""
