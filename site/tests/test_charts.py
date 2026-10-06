import os
import re
from pathlib import Path

import pytest

from wbot_site import charts

SNAPSHOTS = Path(__file__).parent / "snapshots"
MIN = 30


def perf(n, early, on_time, late, early_alt=None, on_time_alt=None, **extra):
    early_alt = early // 3 if early_alt is None else early_alt
    on_time_alt = on_time + early - early_alt if on_time_alt is None else on_time_alt
    return {"n": n, "early": early, "on_time": on_time, "late": late,
            "early_alt": early_alt, "on_time_alt": on_time_alt, "late_alt": late, **extra}


SUMMARY = perf(
    200, 12, 170, 18, p10=-20, p50=75, p90=290,
    hist={"start_min": -10, "under": 1, "over": 7,
          "counts": [0, 0, 0, 0, 0, 0, 0, 1, 3, 7, 40, 45, 35, 25, 15, 8, 5, 3, 2, 1, 1, 1] + [0] * 8},
)
BY_HOUR = [
    {"hour": 6, **perf(40, 2, 36, 2)},
    {"hour": 7, **perf(80, 4, 60, 16)},
    {"hour": 8, **perf(12, 1, 10, 1)},
    {"hour": 9, **perf(60, 3, 54, 3)},
    {"hour": 24, **perf(31, 0, 31, 0)},
]
BY_DAYTYPE = {"weekday": perf(150, 9, 125, 16), "saturday": perf(40, 2, 36, 2), "sunday": perf(10, 1, 9, 0)}
DAILY = [
    {"date": "2026-10-30", **perf(50, 2, 45, 3)},
    {"date": "2026-10-31", **perf(40, 2, 30, 8)},
    {"date": "2026-11-01", **perf(5, 0, 5, 0)},
    {"date": "2026-11-02", **perf(60, 6, 50, 4)},
    {"date": "2026-11-03", **perf(55, 1, 50, 4)},
    {"date": "2026-11-04", **perf(58, 2, 52, 4)},
]

CASES = {
    "headline": lambda: charts.headline_chart(SUMMARY, "Route 901", MIN),
    "daytype": lambda: charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN),
    "histogram": lambda: charts.histogram_chart(SUMMARY, "Route 901", MIN),
    "hour": lambda: charts.hour_chart(BY_HOUR, "Route 901", MIN),
    "daily": lambda: charts.daily_chart(DAILY, "Route 901", MIN),
}


def as_text(chart: charts.Chart) -> str:
    table = "\n".join(" | ".join(row) for row in [chart.headers, *chart.rows])
    return f"{chart.svg}\n\n{chart.summary}\n\n{table}\n"


@pytest.mark.parametrize("name", sorted(CASES))
def test_chart_snapshot(name):
    """Set UPDATE_SNAPSHOTS=1 to rewrite the snapshots after an intended change."""
    text = as_text(CASES[name]())
    path = SNAPSHOTS / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(CASES))
def test_narrow_chart_snapshot(name):
    """The phone layout of each chart. UPDATE_SNAPSHOTS=1 rewrites these too."""
    text = CASES[name]().svg_narrow + "\n"
    path = SNAPSHOTS / f"{name}-narrow.txt"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def test_narrow_charts_fit_their_width():
    # Every mark, point and label is placed inside the narrow drawing.
    for make in CASES.values():
        svg = make().svg_narrow
        assert svg.startswith(f'<svg viewBox="0 0 {charts.NARROW.width} ')
        for x in re.findall(r'x="([-0-9.]+)"', svg):
            assert 0 <= float(x) <= charts.NARROW.width, (svg[:60], x)


def test_charts_are_deterministic():
    for make in CASES.values():
        assert as_text(make()) == as_text(make())


def test_summaries_carry_the_numbers():
    assert charts.headline_chart(SUMMARY, "Route 901", MIN).summary == (
        "Route 901: 85% on time, 6% early, 9% late, 200 departures (Intercity Transit's definition, 0 to 5 min late)."
    )
    assert "sunday not enough data" in charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN).summary


def test_every_mark_has_a_title():
    for make in CASES.values():
        svg = make().svg
        rects = re.findall(r"<rect [^>]*>(.*?)</rect>", svg)
        assert len(rects) == svg.count("<rect") and "<title>" in svg
        assert all(r.startswith("<title>") for r in rects)
        # Screen readers get the summary and the table instead of the drawing.
        assert 'aria-hidden="true"' in svg and 'role="img"' not in svg


def test_small_samples_draw_no_chart():
    tiny = perf(10, 1, 8, 1, p10=0, p50=10, p90=30, hist=SUMMARY["hist"])
    assert charts.headline_chart(tiny, "Stop E999", MIN) is None
    assert charts.histogram_chart(tiny, "Stop E999", MIN) is None
    assert charts.hour_chart([{"hour": 6, **tiny}], "Stop E999", MIN) is None
    assert charts.daily_chart(DAILY[:1], "Stop E999", MIN) is None
    assert charts.daytype_chart({"weekday": tiny}, "Stop E999", MIN) is None


def test_hour_chart_marks_small_hours_in_table():
    rows = charts.hour_chart(BY_HOUR, "Route 901", MIN).rows
    assert rows[2][:3] == ["8 am", "12", "Not enough data"]
    assert rows[-1][0] == "12 am (next day)"


def test_daily_chart_breaks_line_at_small_days():
    svg = charts.daily_chart(DAILY, "Route 901", MIN).svg
    assert svg.count("<polyline") == 2
