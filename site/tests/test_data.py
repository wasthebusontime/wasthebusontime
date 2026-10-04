import json
import logging

import pytest

from wbot_site import data
from wbot_site.data import StatsError


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


def test_text_formats():
    assert data.delay_text(0) == "on schedule"
    assert data.delay_text(45) == "45 s late"
    assert data.delay_text(-75) == "1 min 15 s early"
    assert data.delay_text(120) == "2 min late"
    assert data.delay_text(None) == "n/a"
    assert data.hour_text(0) == "12 am"
    assert data.hour_text(13) == "1 pm"
    assert data.hour_text(25) == "1 am (next day)"
    assert data.date_text("2026-10-04") == "Oct 4, 2026"
    assert data.month_text("2026-11") == "Nov 2026"
    assert data.timestamp_text("2026-10-04T04:32:23Z") == "2026-10-04 04:32 UTC"


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


def test_load_json_checks_schema(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"schema": 1, "a": 1}))
    assert data.load_json(path)["a"] == 1
    path.write_text(json.dumps({"schema": 2}))
    with pytest.raises(StatsError, match="schema 2"):
        data.load_json(path)
    path.write_text(json.dumps({"a": 1}))
    with pytest.raises(StatsError, match="schema None"):
        data.load_json(path)
    with pytest.raises(StatsError, match="missing"):
        data.load_json(tmp_path / "nope.json")
