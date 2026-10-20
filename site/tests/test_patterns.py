"""Patterns (spec decisions 3, 4, 8 and 12): the sentences, the pattern panel and the
Patterns page. The page and its links exist only when the stats have patterns.json."""

import json
import re
import shutil
from html.parser import HTMLParser

import pytest

from conftest import html_pages, make_build
from wbot_site import contract, patterns
from wbot_site.build import SAMPLE_DIR, STATIC_DIR

HOW = "/patterns/#how-we-find-patterns"


def read(out, path):
    return (out / path / "index.html").read_text(encoding="utf-8")


def main_of(html):
    return html.split('<main id="main" tabindex="-1">', 1)[1].split("</main>", 1)[0]


def sample_patterns():
    return json.loads((SAMPLE_DIR / "site" / contract.PATTERNS_FILE).read_text(encoding="utf-8"))


@pytest.fixture
def no_patterns_site(tmp_path):
    stats = tmp_path / "stats"
    shutil.copytree(SAMPLE_DIR, stats)
    (stats / "site" / contract.PATTERNS_FILE).unlink()
    (stats / "csv" / contract.PATTERNS_CSV).unlink()
    out = tmp_path / "dist"
    make_build(stats, out)
    return out


# Sentences


def carry_over(**over):
    p = {"id": "carry_over.905.0.E111.all-all", "kind": "carry_over", "route": "905", "direction_id": 0,
         "stops": ["E111"], "preset": None, "rank": 1, "trips": 68, "days": 16, "late": 588, "matched": 510,
         "share": 0.8673, "previous": [{"route": "905", "direction_id": 1, "matched": 510}]}
    return {**p, **over}


def prev(*parts):
    return [{"route": r, "direction_id": d, "matched": m} for r, d, m in parts]


NAMES = patterns.Names(
    routes={"905": "Route 905", "9x": "Route 9X"},
    headsigns={("905", 0): "Sample Transit Center"},
    stops={"E111": "Fictional Blvd & 1st Ave [sb]"},
)


@pytest.mark.parametrize("matched, late, words", [
    (510, 588, "87%"), (196, 588, "33%"), (294, 588, "50%"), (3, 8, "38%"), (657, 673, "98%"), (588, 588, "100%"),
])
def test_shares_read_as_percentages(matched, late, words):
    assert patterns.share_words(carry_over(matched=matched, late=late)) == words


@pytest.mark.parametrize("preset, words", [
    (None, ""),
    ({"daytype": "all", "band": "pm_peak"}, "on PM peaks (3 to 6 pm)"),
    ({"daytype": "weekday", "band": "pm_peak"}, "on weekday PM peaks (3 to 6 pm)"),
    ({"daytype": "weekday", "band": "all"}, "on weekdays"),
    ({"daytype": "saturday", "band": "evening"}, "on Saturday evenings (6 pm on)"),
    ({"daytype": "sunday", "band": "early"}, "on Sunday early mornings (before 6 am)"),
    ({"daytype": "all", "band": "midday"}, "at midday (9 am to 3 pm)"),
])
def test_specific_patterns_name_their_slot(preset, words):
    assert patterns.slot_words(preset) == words


def test_carry_over_sentence():
    assert patterns.sentence(carry_over(), NAMES) == (
        "On Route 905 to Sample Transit Center, 87% of late departures were on trips that left "
        "Fictional Blvd & 1st Ave [sb] late, after the bus arrived from Route 905, going the other way, "
        "too late to leave on time.")
    specific = carry_over(preset={"daytype": "weekday", "band": "pm_peak"}, matched=207, share=0.352)
    assert patterns.sentence(specific, NAMES) == (
        "On Route 905 to Sample Transit Center on weekday PM peaks (3 to 6 pm), 35% of late departures "
        "were on trips that left Fictional Blvd & 1st Ave [sb] late, after the bus arrived from Route 905, "
        "going the other way, too late to leave on time.")


@pytest.mark.parametrize("previous, words", [
    (prev(("9x", 1, 510)), "after the bus arrived from Route 9X too late to leave on time."),
    (prev(("905", 1, 510)), "after the bus arrived from Route 905, going the other way, too late to leave on time."),
    (prev(("905", 0, 510)), "after the bus arrived from an earlier Route 905 trip too late to leave on time."),
    # Mostly: the largest covers under 9 in 10 of the matched late departures.
    (prev(("9x", 1, 400), ("905", 1, 110)), "after the bus arrived (mostly from Route 9X) too late to leave on time."),
    (prev(("905", 1, 450), ("9x", 1, 60)),
     "after the bus arrived (mostly from Route 905, going the other way) too late to leave on time."),
    (prev(("9x", 1, 459)), "after the bus arrived from Route 9X too late to leave on time."),
    (prev(("9x", 1, 458)), "after the bus arrived (mostly from Route 9X) too late to leave on time."),
    ([], "after the bus arrived too late to leave on time."),
])
def test_carry_over_says_where_the_bus_came_from(previous, words):
    text = patterns.sentence(carry_over(previous=previous), NAMES)
    assert text.endswith("left Fictional Blvd & 1st Ave [sb] late, " + words), text


def test_a_previous_route_missing_from_the_names_still_reads():
    text = patterns.sentence(carry_over(previous=prev(("gone", 0, 510))), NAMES)
    assert text.endswith("after the bus arrived too late to leave on time.")


def test_stop_and_previous_route_link_to_their_pages():
    html = str(patterns.sentence_html(carry_over(), NAMES))
    assert ('left <a href="/stops/E111/">Fictional Blvd &amp; 1st Ave <span aria-hidden="true">[sb]</span>'
            '<span class="visually-hidden">southbound</span></a> late') in html
    # The pattern's own route isn't a link; the previous one is.
    assert html.startswith("On Route 905 to Sample Transit Center, 87% of late departures")
    assert 'arrived from <a href="/routes/905/">Route 905</a>, going the other way, too late' in html
    other = str(patterns.sentence_html(carry_over(previous=prev(("9x", 1, 510))), NAMES))
    assert 'arrived from <a href="/routes/9x/">Route 9X</a> too late' in other


def test_panels_link_the_stop_and_previous_route(dev_site):
    html = read(dev_site, "patterns")
    assert re.search(r'<p class="pattern-text">[^\n]*left <a href="/stops/E111/">Fictional Blvd', html)
    assert re.search(r'<p class="pattern-text">[^\n]*arrived from <a href="/routes/901/">Route 901</a> too late', html)
    assert 'arrived (mostly from <a href="/routes/905/">Route 905</a>, going the other way) too late' in html


def test_panels_give_the_percentage_without_a_counts_line(dev_site):
    html = read(dev_site, "patterns")
    assert "pattern-detail" not in html
    assert re.search(r'<p class="pattern-text">On Route 905 to Sample Transit Center, 38% of late departures', html)


def test_a_route_direction_without_a_headsign_still_reads():
    names = patterns.Names(routes={"905": "Route 905"}, headsigns={}, stops={"E111": "Fictional Blvd"})
    assert patterns.sentence(carry_over(), names).startswith("On Route 905, 87% of late departures")


def test_no_template_says_cause_blame_or_fix():
    shares = (196, 250, 294, 350, 420, 510, 570, 588)
    presets = [None] + [{"daytype": dt, "band": b} for dt in contract.MAP_DAYTYPES for b in contract.MAP_BANDS
                        if (dt, b) != ("all", "all")]
    previous = (prev(("9x", 1, 510)), prev(("905", 1, 510)), prev(("905", 0, 510)), prev(("9x", 1, 300)), [])
    checked = 0
    for kind in patterns.TEMPLATES:
        for preset in presets:
            for share in shares:
                for pr in previous:
                    p = carry_over(kind=kind, preset=preset, matched=share, previous=pr)
                    text = patterns.sentence(p, NAMES)
                    assert patterns.banned_words(text) == [], text
                    checked += 1
    assert checked >= len(presets) * len(shares) * len(previous)


@pytest.mark.parametrize("text", [
    "Buses were late because of carry-overs.", "This caused delays.", "The reason is the layover.",
    "Due to traffic", "The driver left late.", "Dispatch held the bus.", "The schedule is too tight.",
    "Operators should leave on time.", "A fix would be longer layovers.", "Why late?",
])
def test_banned_words_are_caught(text):
    assert patterns.banned_words(text)


def test_every_kind_in_patterns_json_has_a_template_or_is_skipped(caplog):
    doc = sample_patterns()
    doc["patterns"].append(carry_over(id="slow_segment.905.0.E111-E133.all-all", kind="slow_segment",
                                      stops=["E111", "E133"]))
    shown = patterns.shown(doc)
    assert all(p["kind"] in patterns.TEMPLATES for p in shown)
    assert len(shown) == len(doc["patterns"]) - 1
    assert "slow_segment" in caplog.text


# The page


def test_patterns_page_is_built_from_patterns_json(dev_site):
    html = read(dev_site, "patterns")
    assert "<title>Patterns in late buses - Was the Bus On Time?</title>" in html
    assert "<h1>Patterns in late buses</h1>" in html
    assert '<h2 id="how-we-find-patterns">How we find patterns</h2>' in html


def test_nav_has_patterns_on_every_page(dev_site):
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        if '<nav class="site-nav"' not in html:
            continue
        nav = html.split('<nav class="site-nav"', 1)[1].split("</nav>", 1)[0]
        assert '<a href="/patterns/">Patterns</a>' in nav, page


def test_no_patterns_json_no_page_no_nav_no_sections(no_patterns_site):
    assert not (no_patterns_site / "patterns").exists()
    for page in html_pages(no_patterns_site):
        html = page.read_text(encoding="utf-8")
        assert 'href="/patterns/' not in html, page
        assert 'class="pattern' not in html, page
    assert "patterns.csv" not in read(no_patterns_site, "data")


def test_every_route_has_an_anchor_in_the_site_order(dev_site):
    html = read(dev_site, "patterns")
    slugs = [r["slug"] for r in json.loads((SAMPLE_DIR / "site" / "system.json").read_text())["routes"]]
    for slug in slugs:
        assert html.count(f'id="route-{slug}"') == 1, slug
    with_patterns = [s for s in slugs if s in {p["route"] for p in sample_patterns()["patterns"]}]
    found = re.findall(r'<section class="pattern-route" id="route-([^"]+)"', html)
    assert found == with_patterns
    others = re.findall(r'<li id="route-([^"]+)"', html)
    assert others == [s for s in slugs if s not in with_patterns]


def test_routes_without_patterns_say_which_kind_of_none(dev_site):
    html = read(dev_site, "patterns")
    assert re.search(r'<li id="route-906">.*Not enough data yet', html)
    assert re.search(r'<li id="route-901">.*No patterns found yet', html)


def test_panels_in_rank_order_with_label_note_and_link(dev_site):
    html = main_of(read(dev_site, "patterns"))
    route = html.split('id="route-905"', 1)[1].split("</section>", 1)[0]
    panels = route.split('<div class="pattern">')[1:]
    assert len(panels) == 3
    for panel in panels:
        assert '<p class="pattern-label">Pattern</p>' in panel
        assert '<p class="pattern-note">A pattern in past trips, not a measured cause.</p>' in panel
        assert f'<a href="{HOW}">How we find patterns</a>' in panel
    assert "38% of late departures" in panels[0] and "on AM peaks (6 to 9 am)" in panels[2]


def test_panel_colors_have_both_themes_and_pass_contrast():
    css = (STATIC_DIR / "site.css").read_text(encoding="utf-8")
    light, dark = css.split("@media (prefers-color-scheme: dark)", 1)

    def tokens(block):
        return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6})", block))

    for theme in (tokens(light), {**tokens(light), **tokens(dark.split("}\n}", 1)[0])}):
        for fg in ("--text", "--muted", "--link", "--pattern-edge"):
            assert contrast(theme[fg], theme["--pattern-bg"]) >= 4.5, (fg, theme[fg])
        assert contrast(theme["--pattern-label-text"], theme["--pattern-edge"]) >= 4.5
    assert "--pattern-bg" in dark and "--pattern-edge" in dark


def contrast(a, b):
    def lum(h):
        def ch(c):
            c = int(c, 16) / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(h[i:i + 2]) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.levels: list[int] = []
        self.landmarks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self.levels.append(int(tag[1]))
        if tag in ("header", "nav", "main", "footer", "aside"):
            self.landmarks.append(tag)


def test_patterns_page_headings_and_landmarks(dev_site):
    outline = Outline()
    outline.feed(read(dev_site, "patterns"))
    assert outline.levels.count(1) == 1 and outline.levels[0] == 1
    assert all(b - a <= 1 for a, b in zip(outline.levels, outline.levels[1:])), outline.levels
    assert outline.landmarks.count("main") == 1 and "header" in outline.landmarks and "footer" in outline.landmarks


def test_patterns_page_prints_the_rules_from_patterns_json(dev_site):
    how = read(dev_site, "patterns").split('id="how-we-find-patterns"', 1)[1]
    for words in ("more than 5 minutes late", "at least 1 in 3", "at least 30 trips", "at least 5 different days",
                  "10 percentage points", "10 minutes or more", "more than 1 minute after", "Rules version 1",
                  "since collection began on Oct 4, 2026", "fewer than 9 in 10"):
        assert words in how, words
    for term in ("Pattern", "Carry-over", "Layover", "Block", "Segment"):
        assert f"<dt>{term}</dt>" in how, term


def test_patterns_page_wording(dev_site):
    text = re.sub(r"<[^>]+>", " ", main_of(read(dev_site, "patterns")))
    for word in ("because", "caused", "reason", "blame", "fault", "operator", "dispatch", "should", "why"):
        assert not re.search(rf"\b{word}\b", text, re.I), word


def test_patterns_page_is_in_the_sitemap_and_indexed(real_looking_stats, tmp_path):
    out = tmp_path / "dist"
    make_build(real_looking_stats, out, "prod")
    assert "<loc>https://wasthebusontime.com/patterns/</loc>" in (out / "sitemap.xml").read_text()
    assert '<meta name="robots"' not in read(out, "patterns")


def test_methodology_points_to_how_we_find_patterns(dev_site, no_patterns_site):
    html = read(dev_site, "methodology")
    assert '<h2 id="patterns">Patterns</h2>' in html
    assert f'href="{HOW}"' in html
    assert 'id="patterns"' not in read(no_patterns_site, "methodology")


def test_data_page_lists_patterns_csv_and_the_per_trip_files(dev_site, no_patterns_site):
    html = read(dev_site, "data")
    assert "<code>patterns.csv</code>" in html
    assert '<a href="/data/patterns.csv">patterns.csv</a>' in html
    for page in (html, read(no_patterns_site, "data")):
        assert "<code>trips/trips_YYYY-MM.csv</code>" in page
        assert "finished month" in page
