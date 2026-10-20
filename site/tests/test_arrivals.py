"""Arrivals at the end of trips (Patterns ticket 10): on a route's stop tables where trips end,
on stop pages where trips end, and whether buses were in time for their next trip. Measured
numbers, shaded apart from departures, with a note linking the methodology. Optional in the
stats: without them the pages read as before."""

import json
import re
import shutil

import pytest

from conftest import make_build
from test_patterns import contrast, main_of, read
from wbot_site import data
from wbot_site.build import SAMPLE_DIR, STATIC_DIR

NOTE = '<a class="arrivals-note" href="/methodology/#arrivals">How arrivals are measured</a>'


def sample(*parts):
    return json.loads((SAMPLE_DIR / "site" / "/".join(parts)).read_text(encoding="utf-8"))


@pytest.fixture
def old_stats_site(tmp_path):
    """The sample without the arrivals keys, as stats from before ticket 10."""
    stats = tmp_path / "stats"
    shutil.copytree(SAMPLE_DIR, stats)
    site = stats / "site"
    for path in [site / "system.json", *site.glob("routes/*.json"), *site.glob("presets/*/routes/*.json"),
                 *site.glob("presets/*/system.json"), *site.glob("stops/*.json")]:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.pop("end_of_line", None) if "stop" in doc else doc["end_of_line"].pop("next", None)
        for d in doc.get("stops", []):
            for s in d["stops"]:
                s.pop("arrivals", None)
        path.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "dist"
    make_build(stats, out)
    return out


@pytest.mark.parametrize("seconds, words", [
    (502, "8 min 22 sec to spare"), (60, "1 min to spare"), (0, "no time to spare"),
    (-100, "1 min 40 sec after the next trip was due to leave"), (None, "n/a"),
])
def test_spare_words(seconds, words):
    assert data.spare_text(seconds) == words


# Route pages


def tables(html):
    main = main_of(html).split('<section class="scope scope-timepoints">', 1)[1].split("</section>", 1)[0]
    return re.findall(r'<table class="data">\s*<caption>Timepoints to.*?</table>', main, re.S)


def test_route_tables_show_arrivals_on_the_last_stop(dev_site):
    route = sample("routes", "905.json")
    found = tables(read(dev_site, "routes/905"))
    assert len(found) == len(route["stops"])
    for table, direction in zip(found, route["stops"]):
        a = direction["stops"][-1]["arrivals"]
        last = table.rsplit("<tr", 1)[1]
        assert last.startswith(' class="arrivals"')
        assert f"{a['n']:,} arrivals, median {data.delay_text(a['p50'])}" in last
        assert f"{data.percent(a['next']['in_time'], a['next']['n'])} in time for the next trip" in last
        assert f"(median {data.spare_text(a['next']['spare_p50'])})" in last
        assert NOTE in last
        assert "arrivals only" not in last


def test_filtered_route_pages_show_their_arrivals(dev_site):
    a = sample("presets", "weekday-am_peak", "routes", "905.json")["stops"][0]["stops"][-1]["arrivals"]
    (first, *_) = tables(read(dev_site, "routes/905/weekdays/am-peak"))
    assert f"{a['n']:,} arrivals" in first.rsplit("<tr", 1)[1]


def test_end_of_line_paragraph_says_how_many_were_in_time(dev_site):
    eol = sample("routes", "905.json")["end_of_line"]
    html = read(dev_site, "routes/905")
    para = html.split("<h3>End-of-line arrivals</h3>", 1)[1].split("</p>", 1)[0]
    nxt = eol["next"]
    assert (f"Of the {nxt['n']:,} arrivals followed by another trip on the same bus, "
            f"{data.percent(nxt['in_time'], nxt['n'])} were in time for it, with a median of "
            f"{data.spare_text(nxt['spare_p50'])}.") in para


def test_without_arrivals_in_the_stats_route_pages_read_as_before(old_stats_site):
    html = read(old_stats_site, "routes/905")
    assert "Last stop: arrivals only (see End-of-line arrivals below)" in html
    assert 'class="arrivals"' not in html and "in time for" not in html


# Stop pages


def arrivals_section(html):
    m = re.search(r'<div class="arrivals-block">.*?</div>', main_of(html), re.S)
    return m and m.group(0)


def test_stop_page_where_trips_end_has_an_arrivals_table(dev_site):
    eol = sample("stops", "E133.json")["end_of_line"]
    block = arrivals_section(read(dev_site, "stops/E133"))
    assert "<h3>Arrivals at the end of trips</h3>" in block
    assert "<caption>Arrivals here, by route</caption>" in block
    rows = re.findall(r'<tr><th scope="row"><a href="/routes/([^/]+)/">', block)
    assert rows == [r["slug"] for r in eol["by_route"]]
    r = eol["by_route"][0]
    assert f"<td class=\"num\">{r['n']:,}</td>" in block
    assert data.percent(r["next"]["in_time"], r["next"]["n"]) in block
    assert NOTE in block


def test_arrivals_come_after_the_departures_table(dev_site):
    main = main_of(read(dev_site, "stops/E133"))
    assert main.index("<caption>By route</caption>") < main.index('class="arrivals-block"')


def test_stop_without_trips_ending_has_no_arrivals(dev_site, old_stats_site):
    assert arrivals_section(read(dev_site, "stops/E120")) is None
    assert arrivals_section(read(old_stats_site, "stops/E133")) is None


def test_few_arrivals_say_not_enough_data(tmp_path):
    stats = tmp_path / "stats"
    shutil.copytree(SAMPLE_DIR, stats)
    path = stats / "site" / "stops" / "E133.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    for r in doc["end_of_line"]["by_route"]:
        r["next"]["n"] = r["next"]["in_time"] = 3
    path.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "dist"
    make_build(stats, out)
    block = arrivals_section(read(out, "stops/E133"))
    assert "Not enough data" in block


# Shading and the methodology


def test_arrivals_shading_has_both_themes_and_passes_contrast():
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)
    def tokens(block):
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", block))

    assert "--arrivals-bg" in light and "--arrivals-bg" in dark.split("}\n}", 1)[0]
    for theme in (tokens(light), {**tokens(light), **tokens(dark.split("}\n}", 1)[0])}):
        for fg in ("text", "muted", "link"):
            assert contrast(theme[fg], theme["arrivals-bg"]) >= 4.5, (fg, theme[fg], theme["arrivals-bg"])
    assert re.search(r"tr\.arrivals\s*(>\s*\S+\s*)?\{[^}]*var\(--arrivals-bg\)", css)
    assert re.search(r"\.arrivals-block\s*\{[^}]*var\(--arrivals-bg\)", css)


def test_methodology_explains_arrivals_and_the_next_trip(dev_site):
    html = read(dev_site, "methodology")
    section = html.split('<h3 id="arrivals">', 1)[1].split("<h2", 1)[0]
    assert "in time" in section and "to spare" in section and "carry-over" in section
