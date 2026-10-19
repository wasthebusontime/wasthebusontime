import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from wbot_site import charts, windows

SNAPSHOTS = Path(__file__).parent / "snapshots"
MIN = 30
W = windows.from_meta({"headline": [-60, 300], "alt": [0, 300]})


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

# Each day under a day type filter: only that day type's days (here Sundays, one too small).
SUNDAYS = [
    {"date": "2026-10-04", **perf(40, 1, 35, 4)},
    {"date": "2026-10-11", **perf(42, 2, 30, 10)},
    {"date": "2026-10-18", **perf(6, 0, 6, 0)},
    {"date": "2026-10-25", **perf(38, 0, 36, 2)},
    {"date": "2026-11-01", **perf(41, 3, 33, 5)},
]
WEEKDAY_DATES = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
                 "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
WEEKDAYS = [{"date": d, **perf(60, 2, 50 - i % 3 * 4, 8 + i % 3 * 4)} for i, d in enumerate(WEEKDAY_DATES)]

CASES = {
    "headline": lambda: charts.headline_chart(SUMMARY, "Route 901", MIN, W),
    "daytype": lambda: charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN),
    "histogram": lambda: charts.histogram_chart(SUMMARY, "Route 901", MIN, W),
    "hour": lambda: charts.hour_chart(BY_HOUR, "Route 901", MIN),
    "daily": lambda: charts.daily_chart(DAILY, "Route 901", MIN),
    # Under a filter: the chosen day type highlighted, the chosen band shaded, and Each day
    # for one day type.
    "daytype-chosen": lambda: charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN, daytype="saturday"),
    "hour-band": lambda: charts.hour_chart(BY_HOUR, "Route 901", MIN, band="am_peak"),
    "daily-sunday": lambda: charts.daily_chart(SUNDAYS, "Route 901", MIN, daytype="sunday"),
    "daily-weekday": lambda: charts.daily_chart(WEEKDAYS, "Route 901", MIN, daytype="weekday"),
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
    assert charts.headline_chart(SUMMARY, "Route 901", MIN, W).summary == (
        "Route 901: 85% on time, 6% early, 9% late, 200 departures (1 min early to 5 min late)."
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
    assert charts.headline_chart(tiny, "Stop E999", MIN, W) is None
    assert charts.histogram_chart(tiny, "Stop E999", MIN, W) is None
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


def test_histogram_follows_the_window():
    """The bracket and colors come from the window, not from fixed bucket numbers."""
    def bracket_and_kinds(w):
        svg = charts.histogram_chart(SUMMARY, "Route 901", MIN, w).svg
        path = re.search(r'<path d="M(\S+) \S+ V\S+ H(\S+) ', svg).groups()
        kinds = re.findall(r'class="(early|on-time|late)"', svg)
        return tuple(float(x) for x in path), kinds
    (x0, x1), kinds = bracket_and_kinds(W)
    (y0, y1), wide = bracket_and_kinds(windows.from_meta({"headline": [-120, 420], "alt": [0, 300]}))
    step = (x1 - x0) / 6
    assert abs((x0 - y0) - step) < 0.2 and abs((y1 - x1) - 2 * step) < 0.2
    assert wide.count("on-time") > kinds.count("on-time")


def test_headline_chart_shows_the_alt_window_only_when_asked():
    from dataclasses import replace
    hidden = charts.headline_chart(SUMMARY, "Route 901", MIN, W)
    shown = charts.headline_chart(SUMMARY, "Route 901", MIN, replace(W, show_alt=True))
    assert [r[0] for r in hidden.rows] == ["1 min early to 5 min late"]
    assert [r[0] for r in shown.rows] == ["1 min early to 5 min late", "0 to 5 min late (Intercity Transit's own definition)"]
    assert "Intercity Transit" not in hidden.svg and "0 to 5 min late (Intercity Transit)" in shown.svg


def days(start, shares, n=100):
    """Daily rows from a start date, one on-time share per day (None: too few departures)."""
    first = date.fromisoformat(start)
    return [{"date": (first + timedelta(days=i)).isoformat(),
             **(perf(5, 0, 5, 0) if s is None else perf(n, 0, round(n * s), n - round(n * s)))}
            for i, s in enumerate(shares)]


def test_daily_axis_zooms_to_the_data():
    # The lowest day, rounded down to 10%, with at least 20 points of range.
    assert charts._floor(days("2026-10-05", [0.9, 0.75, 0.88])) == 70
    assert charts._floor(days("2026-10-05", [0.97, 0.99])) == 80
    assert charts._floor(days("2026-10-05", [0.9, 0.3])) == 30
    svg = charts.daily_chart(days("2026-10-05", [0.9, 0.75, 0.88]), "Route 901", MIN).svg
    assert ">70%<" in svg and ">100%<" in svg and ">60%<" not in svg


def test_daily_average_is_weighted_by_departures():
    rows = [{"date": "2026-10-05", **perf(300, 0, 270, 30)}, {"date": "2026-10-06", **perf(30, 0, 15, 15)}]
    chart = charts.daily_chart(rows, "Route 901", MIN)
    # 285 of 330 departures, not the 70% mean of the two days.
    assert "average 86%" in chart.summary and ">Average 86%<" in chart.svg


def test_daily_labels_mondays_without_collisions():
    rows = days("2026-08-03", [0.9] * 90)  # a Monday; 13 Mondays in all
    mondays = [i for i, r in enumerate(rows) if date.fromisoformat(r["date"]).weekday() == 0]
    for layout in (charts.WIDE, charts.NARROW):
        step = layout.plot / 89
        labels = charts._date_labels(layout, rows, step)
        spans = []
        for i, anchor in labels:
            w = len(charts._short_date(rows[i])) * (6.4 if layout.narrow else 5.6)
            x = layout.left + i * step
            spans.append({"start": (x, x + w), "end": (x - w, x), "middle": (x - w / 2, x + w / 2)}[anchor])
        assert all(a[1] < b[0] for a, b in zip(spans, spans[1:])), layout
        labeled_mondays = [i for i, _ in labels if i in mondays]
        if layout.narrow:
            assert 3 <= len(labeled_mondays) < len(mondays)
        else:
            assert labeled_mondays == mondays
    # Short ranges label every Monday and both ends.
    labels = charts._date_labels(charts.NARROW, rows[:13], charts.NARROW.plot / 12)
    assert [i for i, _ in labels] == [0, 7, 12]


def test_daily_marks_weekends_and_names_weekdays():
    chart = charts.daily_chart(DAILY, "Route 901", MIN)
    assert 'class="saturday"><title>Sat, Oct 31, 2026</title>' in chart.svg
    assert 'class="sunday"><title>Sun, Nov 1, 2026</title>' in chart.svg
    assert chart.weekend_key and chart.rows[0][0] == "Wed, Nov 4, 2026"
    weekdays_only = charts.daily_chart(days("2026-10-05", [0.9] * 5), "Route 901", MIN)
    assert not weekdays_only.weekend_key and 'class="saturday"' not in weekdays_only.svg


def test_hour_chart_shades_the_chosen_band():
    chart = charts.hour_chart(BY_HOUR, "Route 901", MIN, band="am_peak")
    # Hours 6, 7 and 8 of the five bars: from the first bar's left edge to the third's right.
    step = charts.WIDE.plot / len(BY_HOUR)
    shade = re.search(r'<rect x="([\d.]+)" y="0.0" width="([\d.]+)" height="174.0" class="chosen">', chart.svg)
    assert shade and float(shade.group(1)) == charts.WIDE.left
    assert abs(float(shade.group(2)) - 3 * step) < 0.1
    assert chart.svg.index('class="chosen"') < chart.svg.index('class="grid"')  # behind everything
    assert chart.summary.endswith("Shaded: AM peak (6 to 9 am), the time of day chosen.")
    assert chart.chosen_key == "AM peak (6 to 9 am), the time of day chosen"
    # A band with no hours in the chart shades nothing; no band, no key.
    assert 'class="chosen"' not in charts.hour_chart(BY_HOUR, "Route 901", MIN, band="early").svg
    assert charts.hour_chart(BY_HOUR, "Route 901", MIN).chosen_key == ""


def test_daytype_chart_highlights_the_chosen_day_type():
    chart = charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN, daytype="saturday")
    for svg, lines in ((chart.svg, 1), (chart.svg_narrow, 2)):  # narrow: label and numbers on two lines
        assert svg.count('class="chosen"') == 1 and "<title>Saturday (chosen)</title>" in svg
        bold = re.findall(r'<text font-weight="bold"[^>]*>(.*?)</text>', svg)
        assert len(bold) == lines and bold[0].startswith("Saturday:")
    assert "saturday 90% (the day type chosen)" in chart.summary
    assert chart.chosen_key == "Saturday, the day type chosen"
    assert 'class="chosen"' not in charts.daytype_chart(BY_DAYTYPE, "Route 901", MIN).svg


def test_daily_chart_for_one_day_type():
    chart = charts.daily_chart(SUNDAYS, "Route 901", MIN, daytype="sunday")
    # Evenly spaced, no weekend shading or week lines, and the average over the days shown.
    xs = [float(x) for x in re.findall(r'<line x1="([\d.]+)" y1="\d+" x2="[\d.]+" y2="\d+" class="axis"/>', chart.svg)]
    gaps = {round(b - a, 1) for a, b in zip(xs, xs[1:])}
    assert len(xs) == 5 and len(gaps) == 1
    assert 'class="saturday"' not in chart.svg and 'class="sunday"' not in chart.svg and not chart.weekend_key
    assert not re.search(r'<line x1="([\d.]+)" y1="10" x2="\1"', chart.svg)  # no week lines
    usable = [d for d in SUNDAYS if d["n"] >= MIN]
    assert f"average {percent_of(usable)}" in chart.summary
    assert chart.summary.startswith("Route 901, on time each Sunday over the last 5 Sundays:")
    # Every Sunday's date is labeled where it fits.
    for d in ("Oct 4", "Oct 11", "Oct 18", "Oct 25", "Nov 1"):
        assert f">{d}</text>" in chart.svg, d
    weekdays = charts.daily_chart(WEEKDAYS, "Route 901", MIN, daytype="weekday")
    assert weekdays.summary.startswith("Route 901, on time each weekday over the last 10 weekdays:")
    assert not weekdays.weekend_key


def percent_of(rows: list[dict]) -> str:
    from wbot_site.data import percent
    return percent(sum(d["on_time"] for d in rows), sum(d["n"] for d in rows))


def test_saturday_and_sunday_labels_thin_evenly_from_the_last_day():
    rows = [{"date": (date(2026, 1, 4) + timedelta(weeks=i)).isoformat(), **perf(50, 0, 45, 5)} for i in range(40)]
    for layout in (charts.WIDE, charts.NARROW):
        step = layout.plot / 39
        labels = charts._even_labels(layout, rows, step)
        idx = [i for i, _ in labels]
        assert idx[-1] == 39 and len({b - a for a, b in zip(idx, idx[1:])}) == 1
        assert labels[-1][1] == "end"
