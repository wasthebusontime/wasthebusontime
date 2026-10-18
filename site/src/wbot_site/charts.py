"""Inline SVG charts, drawn at build time.

Each helper returns a Chart: the SVG, a one-sentence summary and the same numbers as
a table. Colors come from CSS classes (early, on-time, late), so the palette lives in
site.css. Meaning never depends on color alone: rows and bars carry text labels, and
every mark has a <title> with its exact value. Output is deterministic text, so the
tests snapshot it.

Every chart is drawn twice: WIDE for screens with room, NARROW (laid out for phones,
where it shows at about 1:1 so the text stays readable). CSS shows one of the two.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape

from wbot_site import agency
from wbot_site.contract import DAYTYPES
from wbot_site.data import (
    DAYTYPE_LABELS, date_text, delay_text, enough, hour_text, number, percent, split, weekday_date_text,
)
from wbot_site.windows import Windows

PARTS = (("early", "early"), ("on_time", "on-time"), ("late", "late"))
PART_LABELS = {"early": "early", "on_time": "on time", "late": "late"}


@dataclass(frozen=True)
class Layout:
    width: int
    left: int
    right: int
    narrow: bool

    @property
    def plot(self) -> int:
        return self.width - self.left - self.right


WIDE = Layout(width=640, left=40, right=10, narrow=False)
NARROW = Layout(width=320, left=32, right=6, narrow=True)


@dataclass
class Chart:
    svg: str
    summary: str
    headers: list[str]
    rows: list[list[str]]
    legend: bool = False  # colored early / on time / late, so the page shows a key
    svg_narrow: str = ""
    weekend_key: bool = False  # shaded Saturdays and Sundays, so the page shows a key


# The drawing is hidden from screen readers: the summary sentence under it and the table
# carry the same numbers, so each number is heard once.
def _svg(L: Layout, drawn: tuple[list[str], int]) -> str:
    body, height = drawn
    return "\n".join([
        f'<svg viewBox="0 0 {L.width} {height}" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">',
        *body,
        "</svg>",
    ])


def _both(draw) -> tuple[str, str]:
    """draw(layout) -> (body, height). Returns the wide and the narrow SVG."""
    return _svg(WIDE, draw(WIDE)), _svg(NARROW, draw(NARROW))


def _text(x: float, y: float, s: str, anchor: str = "start", cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<text x="{x:g}" y="{y:g}" text-anchor="{anchor}"{c}>{escape(s)}</text>'


def _rect(x: float, y: float, w: float, h: float, cls: str, title: str) -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" class="{cls}"><title>{escape(title)}</title></rect>'


def _pct(perf: dict, part: str = "on_time", window: str = "headline", digits: int = 0) -> str:
    early, on_time, late = split(perf, window)
    return percent({"early": early, "on_time": on_time, "late": late}[part], perf["n"], digits)


def _count_cells(perf: dict, window: str = "headline") -> list[str]:
    early, on_time, late = split(perf, window)
    n = perf["n"]
    return [number(n)] + [f"{number(c)} ({percent(c, n, 1)})" for c in (early, on_time, late)]


# Stacked early / on time / late bars


def _stacked(L: Layout, rows: list[tuple[str, dict | None, str]], min_sample: int) -> tuple[list[str], int]:
    """rows: (label, perf, window). Each row is a text line over a 100% bar; on narrow
    screens the label and the numbers take a line each."""
    body, y = [], 0
    for label, perf, window in rows:
        y += 16
        if not enough(perf, min_sample):
            n = perf["n"] if perf else 0
            if L.narrow:
                body.append(_text(0, y, f"{label}:"))
                y += 16
                body.append(_text(0, y, f"not enough data ({number(n)} departures)"))
            else:
                body.append(_text(0, y, f"{label}: not enough data ({number(n)} departures)"))
            y += 12
            continue
        parts = dict(zip(("early", "on_time", "late"), split(perf, window)))
        line = ", ".join(f"{_pct(perf, p, window)} {PART_LABELS[p]}" for p in ("on_time", "early", "late"))
        if L.narrow:
            body.append(_text(0, y, f"{label}:"))
            y += 16
            body.append(_text(0, y, line))
        else:
            body.append(_text(0, y, f"{label}: {line}"))
        y += 6
        x = 0.0
        for part, cls in PARTS:
            w = L.width * parts[part] / perf["n"]
            if w > 0:
                # A 2px gap between segments keeps them apart without relying on color.
                body.append(_rect(x, y, max(w - 2, 0.5), 20, cls, f"{label}: {number(parts[part])} {PART_LABELS[part]} ({_pct(perf, part, window, 1)})"))
            x += w
        y += 28
    return body, y


def headline_chart(perf: dict, subject: str, min_sample: int, windows: Windows) -> Chart | None:
    """Early / on time / late under the headline window, and the alt window if it is shown."""
    if not enough(perf, min_sample):
        return None
    span, alt = windows.headline.span, windows.alt.span
    rows_in = [(span, perf, "headline")]
    rows = [[span, *_count_cells(perf)]]
    if windows.show_alt:
        rows_in.append((f"{alt} ({agency.NAME})", perf, "alt"))
        rows.append([f"{alt} ({agency.NAME}'s own definition)", *_count_cells(perf, "alt")])
    summary = (
        f"{subject}: {_pct(perf)} on time, {_pct(perf, 'early')} early, {_pct(perf, 'late')} late, "
        f"{number(perf['n'])} departures ({span})."
    )
    wide, narrow = _both(lambda L: _stacked(L, rows_in, min_sample))
    return Chart(wide, summary, ["On-time window", "Departures", "Early", "On time", "Late"], rows, legend=True, svg_narrow=narrow)


def daytype_chart(by_daytype: dict, subject: str, min_sample: int) -> Chart | None:
    present = [(DAYTYPE_LABELS[d], by_daytype.get(d)) for d in DAYTYPES if by_daytype.get(d) and by_daytype[d]["n"] > 0]
    if not any(enough(p, min_sample) for _, p in present):
        return None
    rows_in = [(label, p, "headline") for label, p in present]
    parts = [f"{label.lower()} {_pct(p)}" if enough(p, min_sample) else f"{label.lower()} not enough data" for label, p in present]
    summary = f"{subject}, on time by day type: {', '.join(parts)}."
    rows = [[label, *_count_cells(p)] for label, p in present]
    wide, narrow = _both(lambda L: _stacked(L, rows_in, min_sample))
    return Chart(wide, summary, ["Day type", "Departures", "Early", "On time", "Late"], rows, legend=True, svg_narrow=narrow)


# Delay histogram


def _histogram(L: Layout, hist: dict, values: list[int], kinds: list[str], labels: list[str]) -> tuple[list[str], int]:
    start, counts = hist["start_min"], hist["counts"]
    top, plot_h, base = 26, 120, 146
    step = L.plot / len(values)
    peak = max(values) or 1
    body = [f'<line x1="{L.left}" y1="{base}" x2="{L.width - L.right}" y2="{base}" class="axis"/>']
    for i, (v, kind, label) in enumerate(zip(values, kinds, labels)):
        h = plot_h * v / peak
        if v:
            body.append(_rect(L.left + i * step + 1, base - h, step - 2, h, kind, f"{label}: {number(v)} departure{'' if v == 1 else 's'}"))
    # Bracket over the on-time buckets.
    on_time = [i for i, k in enumerate(kinds) if k == "on-time"]
    x0 = L.left + on_time[0] * step
    x1 = L.left + (on_time[-1] + 1) * step
    body.append(f'<path d="M{x0:.1f} {top + 6} V{top} H{x1:.1f} V{top + 6}" class="bracket"/>')
    body.append(_text((x0 + x1) / 2, top - 4, "On time", "middle"))
    for m in range(start, start + len(counts) + 1, 5):
        x = L.left + (1 + m - start) * step
        body.append(_text(x, base + 14, f"{m:+d}" if m else "0", "middle", "small"))
    body.append(_text(L.left + L.plot / 2, base + 30, "Minutes late (negative is early)", "middle", "small"))
    return body, base + 36


def _bucket(m: int) -> str:
    """A 1-minute bucket in words: "7 to 8 min early", "0 to 1 min late"."""
    return f"{-m - 1} to {-m} min early" if m < 0 else f"{m} to {m + 1} min late"


def histogram_chart(perf: dict, subject: str, min_sample: int, windows: Windows) -> Chart | None:
    """1-minute delay buckets, with the bucket under and over the range at the ends."""
    hist = perf.get("hist")
    if not hist or not enough(perf, min_sample):
        return None
    start, counts = hist["start_min"], hist["counts"]
    values = [hist["under"], *counts, hist["over"]]
    labels = [f"more than {-start} min early"] + [_bucket(m) for m in range(start, start + len(counts))]
    labels.append(f"{start + len(counts)} min or more late")
    kinds = ["early"] + [windows.headline.kind(m) for m in range(start, start + len(counts))] + ["late"]
    summary = (
        f"{subject}: median departure {delay_text(perf['p50'])}; 80% of departures were between "
        f"{delay_text(perf['p10'])} and {delay_text(perf['p90'])}."
    )
    rows = [[label, number(v)] for label, v in zip(labels, values)]
    wide, narrow = _both(lambda L: _histogram(L, hist, values, kinds, labels))
    return Chart(wide, summary, ["Delay", "Departures"], rows, legend=True, svg_narrow=narrow)


# On time by hour


def _axis_y(L: Layout, body: list[str], top: int, plot_h: int, ticks: tuple[int, ...] = (0, 50, 100)) -> None:
    for pct in ticks:
        y = top + plot_h * (100 - pct) / (100 - ticks[0])
        body.append(f'<line x1="{L.left}" y1="{y:g}" x2="{L.width - L.right}" y2="{y:g}" class="grid"/>')
        body.append(_text(L.left - 6, y + 4, f"{pct}%", "end", "small"))


def _hours(L: Layout, by_hour: list[dict], min_sample: int) -> tuple[list[str], int]:
    top, plot_h = 16, 140
    base = top + plot_h
    step = L.plot / len(by_hour)
    body: list[str] = []
    _axis_y(L, body, top, plot_h)
    for i, h in enumerate(by_hour):
        cx = L.left + i * step + step / 2
        if enough(h, min_sample):
            share = h["on_time"] / h["n"]
            bh = plot_h * share
            body.append(_rect(cx - step / 2 + 2, base - bh, step - 4, bh, "on-time",
                              f"{hour_text(h['hour'])}: {_pct(h, digits=1)} on time, {number(h['n'])} departures"))
            # Values over the bars only where they fit; the titles and the table have them all.
            if len(by_hour) <= 24 and not L.narrow:
                body.append(_text(cx, base - bh - 3, _pct(h).rstrip("%"), "middle", "small"))
        if h["hour"] % 3 == 0:
            body.append(_text(cx, base + 14, str(h["hour"]), "middle", "small"))
    caption = "Hour of the day (24 and later: after midnight)" if L.narrow else "Hour of the service day (24 and later: after midnight)"
    body.append(_text(L.left + L.plot / 2, base + 30, caption, "middle", "small"))
    return body, base + 36


def hour_chart(by_hour: list[dict], subject: str, min_sample: int) -> Chart | None:
    usable = [h for h in by_hour if enough(h, min_sample)]
    if not usable:
        return None
    best = max(usable, key=lambda h: (h["on_time"] / h["n"], -h["hour"]))
    worst = min(usable, key=lambda h: (h["on_time"] / h["n"], h["hour"]))
    summary = (
        f"{subject}, on time by hour: lowest {_pct(worst)} at {hour_text(worst['hour'])}, "
        f"highest {_pct(best)} at {hour_text(best['hour'])}."
    )
    rows = [[hour_text(h["hour"]), *_count_cells(h)] if enough(h, min_sample)
            else [hour_text(h["hour"]), number(h["n"]), "Not enough data", "", ""] for h in by_hour]
    wide, narrow = _both(lambda L: _hours(L, by_hour, min_sample))
    return Chart(wide, summary, ["Hour", "Departures", "Early", "On time", "Late"], rows, svg_narrow=narrow)


# Daily trend


def _share(d: dict) -> float:
    return d["on_time"] / d["n"]


def _weekday(d: dict) -> int:
    return datetime.fromisoformat(d["date"]).weekday()


def _average(usable: list[dict]) -> str:
    """On time over all the days shown, weighted by departures."""
    return percent(sum(d["on_time"] for d in usable), sum(d["n"] for d in usable))


def _floor(usable: list[dict]) -> int:
    """Bottom of the y-axis in percent: the lowest day or the average, rounded down to a
    multiple of 10, and never above 80, so the axis always spans at least 20 points."""
    average = sum(d["on_time"] for d in usable) / sum(d["n"] for d in usable)
    low = min(min(_share(d) for d in usable), average) * 100
    return min(int(low // 10) * 10, 80)


def _short_date(d: dict) -> str:
    return date_text(d["date"]).rsplit(",", 1)[0]


def _date_labels(L: Layout, daily: list[dict], step: float) -> list[tuple[int, str]]:
    """(index, anchor) of the dates to label: Mondays, thinned so their labels never touch,
    then the first and last day where they fit beside them."""
    gap = 8
    last = len(daily) - 1

    def span(i: int, anchor: str) -> tuple[float, float]:
        # A rough width of the small label (10px wide, 11.5px narrow), in drawing units.
        w = len(_short_date(daily[i])) * (6.4 if L.narrow else 5.6)
        x = L.left + i * step
        return {"start": (x, x + w), "end": (x - w, x), "middle": (x - w / 2, x + w / 2)}[anchor]

    def clear(i: int, anchor: str, labels: list[tuple[int, str]]) -> bool:
        lo, hi = span(i, anchor)
        return all(hi + gap <= span(j, a)[0] or span(j, a)[1] + gap <= lo for j, a in labels)

    mondays = [i for i, d in enumerate(daily) if _weekday(d) == 0]
    every = 1
    while 7 * every * step < 6 * (6.4 if L.narrow else 5.6) + gap:
        every += 1
    labels = [(i, "middle") for i in mondays[::every]]
    for i, anchor in ((0, "start"), (last, "end")):
        others = [(j, a) for j, a in labels if j != i]
        if len(others) < len(labels) or clear(i, anchor, others):
            labels = [*others, (i, anchor)]
    return sorted(labels)


def _days(L: Layout, daily: list[dict], min_sample: int) -> tuple[list[str], int]:
    usable = [d for d in daily if enough(d, min_sample)]
    top, plot_h = 10, 180 if L.narrow else 140
    base = top + plot_h
    lo = _floor(usable)
    step = L.plot / max(len(daily) - 1, 1)
    right = L.left + L.plot
    x = lambda i: L.left + i * step
    y = lambda share: top + plot_h * (100 - share * 100) / (100 - lo)
    body: list[str] = []

    # Saturdays and Sundays as shaded columns behind everything, one day wide.
    for i, d in enumerate(daily):
        kind = {5: "saturday", 6: "sunday"}.get(_weekday(d))
        if kind:
            x0, x1 = max(x(i) - step / 2, L.left), min(x(i) + step / 2, right)
            body.append(_rect(x0, top, x1 - x0, plot_h, kind, weekday_date_text(d["date"])))
    _axis_y(L, body, top, plot_h, tuple(range(lo, 101, 10)))
    # A line at the start of each week, and a tick for each day where they have room.
    for i, d in enumerate(daily):
        if _weekday(d) == 0:
            body.append(f'<line x1="{x(i):.1f}" y1="{top}" x2="{x(i):.1f}" y2="{base}" class="grid"/>')
        if step >= 6:
            body.append(f'<line x1="{x(i):.1f}" y1="{base}" x2="{x(i):.1f}" y2="{base + 4}" class="axis"/>')

    # The average of the days shown. Its label goes on top of the data line, later.
    average = sum(d["on_time"] for d in usable) / sum(d["n"] for d in usable)
    ya = y(average)
    body.append(f'<line x1="{L.left}" y1="{ya:.1f}" x2="{right}" y2="{ya:.1f}" class="average"/>')

    # Days without enough data break the line rather than being bridged.
    segment: list[tuple[float, float]] = []
    segments: list[list[tuple[float, float]]] = []
    for i, d in enumerate(daily):
        if enough(d, min_sample):
            segment.append((x(i), y(_share(d))))
        elif segment:
            segments.append(segment)
            segment = []
    if segment:
        segments.append(segment)
    for seg in segments:
        if len(seg) > 1:
            body.append(f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in seg)}" class="line"/>')
    # A dot for every day when they have room, otherwise only for days standing alone.
    for seg in segments:
        if step >= 10 or len(seg) == 1:
            body.extend(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" class="dot"/>' for px, py in seg)
    # Above the left end of the average line (below it when near the top), haloed in CSS.
    body.append(_text(L.left + 4, round(ya - 5 if ya - 5 >= top + 12 else ya + 15, 1), f"Average {_average(usable)}", "start", "average-label"))
    for i, d in enumerate(daily):
        if enough(d, min_sample):
            body.append(f'<circle cx="{x(i):.1f}" cy="{y(_share(d)):.1f}" r="6" class="hit"><title>'
                        f'{escape(weekday_date_text(d["date"]))}: {_pct(d, digits=1)} on time, {number(d["n"])} departures</title></circle>')

    for i, anchor in _date_labels(L, daily, step):
        body.append(_text(x(i), base + 16, _short_date(daily[i]), anchor, "small"))
    return body, base + 24


def daily_chart(daily: list[dict], subject: str, min_sample: int) -> Chart | None:
    usable = [d for d in daily if enough(d, min_sample)]
    if len(usable) < 2:
        return None
    low = min(usable, key=lambda d: (_share(d), d["date"]))
    high = max(usable, key=lambda d: (_share(d), d["date"]))
    summary = (
        f"{subject}, on time each day over the last {len(daily)} days: average {_average(usable)}, "
        f"lowest {_pct(low)} on {date_text(low['date'])}, highest {_pct(high)} on {date_text(high['date'])}, "
        f"latest {_pct(usable[-1])} on {date_text(usable[-1]['date'])}."
    )
    rows = [[weekday_date_text(d["date"]), *_count_cells(d)] if enough(d, min_sample)
            else [weekday_date_text(d["date"]), number(d["n"]), "Not enough data", "", ""] for d in reversed(daily)]
    wide, narrow = _both(lambda L: _days(L, daily, min_sample))
    return Chart(wide, summary, ["Date", "Departures", "Early", "On time", "Late"], rows,
                 svg_narrow=narrow, weekend_key=any(_weekday(d) >= 5 for d in daily))
